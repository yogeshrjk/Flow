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
    s.response_mode = "nonsense"  # unknown value -> default tier
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

