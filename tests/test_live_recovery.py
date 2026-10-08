"""Gemini Live recovery tests (no network — scripted fake Bidi sessions).
Guards: post-interrupt reuse, setup-failure fallback, 1011 reconnect-retry,
hung-turn timeout. Groq/Fish paths untouched."""
import asyncio
import sys
sys.path.insert(0, ".")

import pytest

from app.conversation import engine as engmod
from app.conversation.engine import engine


class FakeLive:
    """Scripted stand-in for GeminiLiveSession (already set up)."""
    def __init__(self, voice_name="Puck", script=None):
        self.voice_name = voice_name
        self.setup_complete = True
        self.closed = False
        self.script = script or []  # list of actions: ("tokens", [...]) / ("raise", exc) / ("hang",)
        self.sends = 0

    async def connect(self, system_instruction=""):
        self.setup_complete = True

    async def close(self):
        self.closed = True
        self.setup_complete = False

    async def send_user_text(self, text, on_text=None, on_audio=None):
        self.sends += 1
        out = []
        for act in self.script:
            if act[0] == "tokens":
                for tok in act[1]:
                    out.append(tok)
                    if on_text:
                        r = on_text(tok)
                        if asyncio.iscoroutine(r):
                            await r
            elif act[0] == "raise":
                raise act[1]
            elif act[0] == "hang":
                await asyncio.sleep(3600)
        return "".join(out)


def run_turn(sid, text, monkeypatch, fake, timeout=60.0):
    s = engine.get_or_create(sid)
    s.response_mode = "gemini_live"
    s._live_session = fake
    sent = []

    async def send(o):
        sent.append(o)

    async def go():
        await engine.handle_gemini_live_turn(s, text, send, 0.0, None, "t1")

    monkeypatch.setattr(engmod, "TURN_TIMEOUT_S", timeout)
    asyncio.run(go())
    return s, sent


def test_murder_delivers_partial_and_next_turn_reconnects(monkeypatch):
    # hang AFTER two tokens with a short timeout: timeout expiry must deliver
    # the partial, and the NEXT turn on the same session must work fully.
    s, sent = run_turn("live-m1", "hello?", monkeypatch,
                       FakeLive(script=[("tokens", ["Hi", " there!"]), ("hang",)]), timeout=2.0)
    done = [o for o in sent if o["type"] == "llm_done"]
    assert done and done[0].get("partial") and "Hi there!" in done[0]["text"]
    s._live_session = FakeLive(script=[("tokens", ["All", " good."])])
    sent2 = []

    async def send2(o):
        sent2.append(o)

    asyncio.run(engine.handle_gemini_live_turn(s, "again?", send2, 0.0, None, "t2"))
    done2 = [o for o in sent2 if o["type"] == "llm_done"]
    assert done2 and done2[0]["text"] == "All good." and not done2[0].get("partial")


def test_setup_failure_is_visible_not_silent(monkeypatch):
    import app.providers.llm.gemini_live as glmod

    class BadConnect(FakeLive):
        async def connect(self, system_instruction=""):
            raise RuntimeError("simulated 429 rate limit")

    s = engine.get_or_create("live-m2")
    s.response_mode = "gemini_live"
    if hasattr(s, "_live_session"):
        delattr(s, "_live_session")
    sent = []

    async def send(o):
        sent.append(o)

    real = glmod.GeminiLiveSession
    glmod.GeminiLiveSession = lambda voice_name="Puck": BadConnect(voice_name)
    try:
        asyncio.run(engine.handle_gemini_live_turn(s, "hi?", send, 0.0, None, "t1"))
    finally:
        glmod.GeminiLiveSession = real
    types = [o["type"] for o in sent]
    assert "error" in types and "llm_done" in types, types


def test_1011_reconnects_once_with_no_duplication(monkeypatch):
    import app.providers.llm.gemini_live as glmod

    calls = {"n": 0}

    class Flaky(FakeLive):
        async def send_user_text(self, text, on_text=None, on_audio=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("sent 1011 (internal error) keepalive ping timeout")
            if on_text:
                r = on_text("recovered!")
                if asyncio.iscoroutine(r):
                    await r
            return "recovered!"

    real = glmod.GeminiLiveSession
    made = {}

    def factory(voice_name="Puck"):
        f = Flaky(voice_name)
        made["last"] = f
        return f

    glmod.GeminiLiveSession = factory
    try:
        s = engine.get_or_create("live-m3")
        s.response_mode = "gemini_live"
        if hasattr(s, "_live_session"):
            delattr(s, "_live_session")
        sent = []

        async def send(o):
            sent.append(o)

        asyncio.run(engine.handle_gemini_live_turn(s, "hi?", send, 0.0, None, "t1"))
    finally:
        glmod.GeminiLiveSession = real
    done = [o for o in sent if o["type"] == "llm_done"]
    assert calls["n"] == 2, calls
    assert done and done[0]["text"] == "recovered!" and not done[0].get("partial")


def test_cancelled_turn_frees_socket_for_next_turn(monkeypatch):
    s = engine.get_or_create("live-m4")
    s.response_mode = "gemini_live"
    fake = FakeLive(script=[("tokens", ["partial", " words"])])
    s._live_session = fake
    sent = []

    async def send(o):
        sent.append(o)

    async def go():
        task = asyncio.ensure_future(
            engine.handle_gemini_live_turn(s, "hi?", send, 0.0, None, "t1"))
        # monkeypatch FakeLive to hang AFTER 2 tokens so cancel lands mid-stream
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # make the fake hang after tokens
    orig = fake.send_user_text

    async def hanging(text, on_text=None, on_audio=None):
        await orig(text, on_text=on_text, on_audio=on_audio)
        await asyncio.sleep(3600)

    fake.send_user_text = hanging
    asyncio.run(go())
    done = [o for o in sent if o["type"] == "llm_done"]
    assert done and done[0].get("partial") and "partial words" in done[0]["text"]
    assert fake.closed, "interrupted socket must be released"
    assert getattr(s, "_live_session", None) is None, "next turn must reconnect fresh"
