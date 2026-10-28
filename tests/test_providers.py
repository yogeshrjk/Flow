"""Provider tiers, request shapes, Fish streaming, error sanitization, and diagnostic logging."""
import asyncio
import json

import httpx
import pytest

from app.conversation.engine import Engine, Session, sanitize_error
from app.latency import format_block
from app.providers.llm import groq as groq_mod
from app.providers.llm.groq import GroqProvider
from app.providers.tts import fish as fish_mod

SSE_OK = (
    b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
    b'data: [DONE]\n\n'
)
MP3 = b"\xff\xfb\x90\x00" + b"a" * 5000


def test_groq_streams_openai_shape_without_reasoning_flag(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, content=SSE_OK, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(groq_mod, "_get_client", lambda: client)
    p = GroqProvider(models=["llama-3.1-8b-instant"], api_key="test-key")

    async def run() -> str:
        out = await p.complete([{"role": "user", "content": "hi"}])
        await client.aclose()
        return out

    assert asyncio.run(run()) == "Hello world"
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"].startswith("Bearer ")
    assert seen["body"]["stream"] is True
    assert seen["body"]["model"] == "llama-3.1-8b-instant"
    assert "reasoning_effort" not in seen["body"]


def test_groq_reasoning_model_gets_low_effort(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, content=SSE_OK)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(groq_mod, "_get_client", lambda: client)
    p = GroqProvider(models=["openai/gpt-oss-20b"], api_key="test-key")

    async def run() -> None:
        [tok async for tok in p.stream([{"role": "user", "content": "hi"}])]
        await client.aclose()

    asyncio.run(run())
    assert seen["body"]["reasoning_effort"] == "low"


def test_groq_without_key_is_a_clear_error():
    p = GroqProvider(api_key="")

    async def run() -> str:
        try:
            [t async for t in p.stream([{"role": "user", "content": "hi"}])]
        except RuntimeError as e:
            return str(e)
        return ""

    assert "GROQ_API_KEY" in asyncio.run(run())


def test_groq_retries_next_model_on_retryable(monkeypatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content.decode())["model"]
        calls.append(model)
        if model == "bad-model":
            return httpx.Response(429, content=b"rate limited")
        return httpx.Response(200, content=SSE_OK)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(groq_mod, "_get_client", lambda: client)
    p = GroqProvider(models=["bad-model", "llama-3.1-8b-instant"], api_key="test-key")

    async def run() -> str:
        out = await p.complete([{"role": "user", "content": "hi"}])
        await client.aclose()
        return out

    assert asyncio.run(run()) == "Hello world"
    assert calls == ["bad-model", "llama-3.1-8b-instant"]


def test_engine_serves_the_chosen_tier_and_never_breaks():
    e = Engine()
    groq_fish, gemini_fish, gemini_live = object(), object(), object()
    e.providers = {
        "groq_fish": groq_fish,
        "gemini_fish": gemini_fish,
        "gemini_live": gemini_live,
        "fast": groq_fish,
        "quality": gemini_fish,
    }
    s = Session()
    s.response_mode = "groq_fish"
    assert e.llm_for(s) is groq_fish
    s.response_mode = "gemini_fish"
    assert e.llm_for(s) is gemini_fish
    s.response_mode = "gemini_live"
    assert e.llm_for(s) is gemini_live
    s.response_mode = ""  # empty value -> default tier
    assert e.llm_for(s) is groq_fish  # explicit test default
    s.response_mode = "nonsense"  # invalid legacy preference -> Live default
    assert e.llm_for(s) is gemini_live
    e.providers = {"gemini_fish": gemini_fish}  # selected Live tier has no key
    assert e.llm_for(s) is e.unavailable
    e.providers = {}
    assert e.llm_for(s) is e.unavailable
    assert e.mode_available("groq_fish") is False


def test_fish_stream_yields_audio_progressively(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(fish_mod, "_get_stream_client", lambda: client)
    p = fish_mod.FishProvider(api_key="k")

    async def run() -> tuple[bytes, str | None]:
        it, err = await p.open_stream("Hello there, how are you?")
        if it is None:
            return b"", err
        body = b"".join([c async for c in it])
        await client.aclose()
        return body, err

    body, err = asyncio.run(run())
    assert err is None
    assert body == MP3


def test_fish_stream_surfaces_json_200_as_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"message":"no credit"}')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(fish_mod, "_get_stream_client", lambda: client)
    p = fish_mod.FishProvider(api_key="k")

    async def run() -> tuple[object, str | None]:
        it, err = await p.open_stream("hi")
        await client.aclose()
        return it, err

    it, err = asyncio.run(run())
    assert it is None and err and "non-audio" in err


def test_fish_stream_reports_402(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, content=b"out of credit")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(fish_mod, "_get_stream_client", lambda: client)
    p = fish_mod.FishProvider(api_key="k")

    async def run() -> str | None:
        it, err = await p.open_stream("hi")
        await client.aclose()
        return err

    assert "402" in (asyncio.run(run()) or "")


def test_sanitize_error():
    assert "REDACTED" in sanitize_error("Error with Bearer sk-1234567890abcdef1234567890")
    assert "sk-1234567890" not in sanitize_error("Error with Bearer sk-1234567890abcdef1234567890")
    assert "REDACTED" in sanitize_error("Failed auth key='super-secret-key-value'")


def test_handle_user_turn_with_req_id():
    sent_msgs = []

    async def mock_send(msg: dict):
        sent_msgs.append(msg)

    engine = Engine()
    s = Session(id="test_turn_sid")
    s.response_mode = "fast"

    async def run():
        await engine.handle_user_turn(s, "Hello, how are you?", mock_send, 0.0, turn_id="42", req_id="req_42_12345")

    asyncio.run(run())
    types = [m.get("type") for m in sent_msgs]
    assert "status" in types
    assert "llm_done" in types
    done_msg = next(m for m in sent_msgs if m.get("type") == "llm_done")
    assert done_msg.get("req_id") == "req_42_12345"
    assert done_msg.get("turn") == "42"
    assert done_msg.get("text")


def test_vercel_sse_turn_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app

    c = TestClient(app)
    payload = {
        "text": "Testing Vercel SSE streaming.",
        "mode": "free",
        "turn_id": "v1",
        "req_id": "req_v1"
    }
    with c.stream("POST", "/api/session/v_sid/turn", json=payload) as resp:
        assert resp.status_code == 200
        events = [line for line in resp.iter_lines() if line.startswith("data:")]
        assert len(events) >= 2
        assert any("llm_done" in e for e in events)
        assert "[DONE]" in events[-1]


def test_gemini_live_default_and_all_voices():
    """Verify Gemini Live Preview is default and all 16 voices are exposed."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import settings
    from app.voices import GEMINI_VOICES, DEFAULT_GEMINI_VOICE, persona_for

    from app.config import Settings
    assert Settings(gemini_api_key="test-key", groq_api_key="").default_response_mode == "gemini_live"
    assert Settings(gemini_api_key="", groq_api_key="", llm_default_response_mode="gemini_live").default_response_mode == "gemini_live"
    assert DEFAULT_GEMINI_VOICE == "Orbit"

    expected_app_voices = [
        "Orbit", "Pegasus", "Capella", "Dipper", "Orion",
        "Ursa", "Vega", "Eclipse", "Flare", "Glow"
    ]
    expected_studio_voices = [
        "Puck", "Charon", "Kore", "Aoede", "Fenrir", "Enceladus"
    ]
    all_expected = set(expected_app_voices + expected_studio_voices)
    actual_ids = {v["id"] for v in GEMINI_VOICES}
    assert all_expected == actual_ids
    assert len(GEMINI_VOICES) == 16

    # Test persona mapping
    assert persona_for(None) == ("Orbit", "male")
    assert persona_for("Orbit") == ("Orbit", "male")
    assert persona_for("Capella") == ("Capella", "female")
    assert persona_for("Ursa") == ("Ursa", "female")
    assert persona_for("Flare") == ("Flare", "female")
    assert persona_for("Charon") == ("Charon", "male")

    # API config test
    c = TestClient(app)
    cfg = c.get("/api/config").json()
    assert cfg.get("default_response_mode") == settings.default_response_mode
    assert cfg.get("default_gemini_voice") == "Orbit"
    resp_modes = cfg.get("response_modes", [])
    assert {m["id"] for m in resp_modes} == {"gemini_live", "groq_fish", "gemini_fish"}
    assert len(cfg.get("gemini_voices", [])) == 16


def test_gemini_transcribe_live_session():
    """Verify GeminiTranscribeLiveSession sends correct setup payload and parses real-time transcriptions."""
    from app.providers.llm.gemini_live import GeminiTranscribeLiveSession

    sess = GeminiTranscribeLiveSession(api_key="test-api-key")
    assert "gemini-3.5-transcribe-live" in sess.model

    sent_payloads = []

    class MockWS:
        def __init__(self):
            self.closed = False

        async def send(self, data):
            sent_payloads.append(json.loads(data))

        async def recv(self):
            return json.dumps({"setupComplete": {}})

        async def close(self):
            self.closed = True

    mock_ws = MockWS()

    async def run():
        import websockets
        orig_connect = websockets.connect

        async def mock_connect(url, **kwargs):
            return mock_ws

        websockets.connect = mock_connect
        try:
            await sess.connect()
            assert sess.setup_complete is True
            assert len(sent_payloads) == 1
            setup = sent_payloads[0]["setup"]
            assert setup["model"] == "models/gemini-3.5-transcribe-live"
            assert setup["generationConfig"]["responseModalities"] == ["TEXT"]

            # Test send audio chunk
            await sess.send_audio_chunk("AQIDBA==", rate=24000)
            assert len(sent_payloads) == 2
            media = sent_payloads[1]["realtimeInput"]["mediaChunks"][0]
            assert media["mimeType"] == "audio/pcm;rate=24000"
            assert media["data"] == "AQIDBA=="

            # Test recv interim transcription
            async def mock_recv_chunk():
                return json.dumps({
                    "serverContent": {
                        "interimInputTranscription": {
                            "text": "Hello world"
                        }
                    }
                })
            mock_ws.recv = mock_recv_chunk
            txt = await sess.recv_transcription(timeout=1.0)
            assert txt == "Hello world"

            await sess.close()
            assert mock_ws.closed is True
        finally:
            websockets.connect = orig_connect

    asyncio.run(run())

