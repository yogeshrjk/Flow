"""Provider tiers, hidden naming, Fish streaming and the latency block.

Nothing here needs real keys: httpx.MockTransport stands in for the network so
the request shape (model, stream flag, headers) and the streaming/error paths
are verified deterministically.
"""
import asyncio
import json

import httpx

from app.conversation.engine import Engine, Session
from app.latency import format_block
from app.providers.llm import groq as groq_mod
from app.providers.llm.groq import GroqProvider
from app.providers.tts import fish as fish_mod

SSE_OK = (
    b'data: {"choices":[{"delta":{"content":"Nice"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":" one"}}]}\n\n'
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

    assert asyncio.run(run()) == "Nice one"
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"].startswith("Bearer ")
    assert seen["body"]["stream"] is True
    assert seen["body"]["model"] == "llama-3.1-8b-instant"
    # llama-3.1 400s on reasoning_effort — it must only go to reasoning families
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

    assert asyncio.run(run()) == "Nice one"
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
    s.response_mode = "nonsense"  # unknown value → default tier, never a crash
    assert e.llm_for(s) is groq_fish
    e.providers = {"gemini_fish": gemini_fish}  # chosen tier has no key
    assert e.llm_for(s) is gemini_fish
    e.providers = {}
    assert e.llm_for(s) is e.mock
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
    assert body == MP3  # every byte arrives, none lost between chunks


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


def test_latency_block_has_every_stage_and_a_total():
    block = format_block(stt_ms=42, llm_ttft_ms=180.4, chunk_ms=61, tts_ms=690, buffer_ms=35, total_ms=1008)
    for label in ("[VOICE LATENCY]", "STT + network:", "LLM TTFT:", "First phrase:",
                  "TTS first audio:", "Playback buffer:", "TOTAL"):
        assert label in block
    assert "180ms" in block and "1008ms" in block
    assert "n/a" in format_block(llm_ttft_ms=None)


def test_public_config_response_modes():
    """/api/config exposes the 3 response modes for voice assistant selection."""
    from fastapi.testclient import TestClient
    from app.main import app

    cfg = TestClient(app).get("/api/config").json()
    modes = [m["id"] for m in cfg["response_modes"]]
    assert modes == ["groq_fish", "gemini_fish", "gemini_live"]
    assert cfg["response_modes"][0]["label"] == "Groq + Fish Audio"
    assert cfg["response_modes"][1]["label"] == "Gemini + Fish Audio"
    assert cfg["response_modes"][2]["label"] == "Gemini Live Preview"
    assert "available" in cfg["response_modes"][0]
    assert cfg["default_response_mode"] in ("groq_fish", "gemini_fish", "gemini_live", "fast", "quality")
