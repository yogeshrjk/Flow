"""Exercise real provider adapters and API routing with network transports stubbed."""
import asyncio
import base64
import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from app.config import settings, Settings
from app.conversation.engine import Engine, Session
from app.main import app, cancel_turn
from app.providers.llm.groq import GroqProvider
from app.providers.llm.gemini import GeminiChainProvider
from app.providers.llm import groq, gemini
from app.providers.tts import fish
from app.voices import DEFAULT_FISH_VOICE

MP3 = b"\xff\xfb\x90\x00" + b"voice-data" * 600
SSE = (b'data: {"choices":[{"delta":{"content":"That sounds great. "}}]}\n\n'
       b'data: {"choices":[{"delta":{"content":"What did you enjoy about your day?"}}]}\n\n'
       b'data: [DONE]\n\n')


def test_exact_three_modes_and_live_default_despite_legacy_environment(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "browser")
    monkeypatch.setenv("LLM_ALLOW_OLLAMA", "true")
    config = Settings(groq_api_key="", gemini_api_key="", llm_default_response_mode="gemini_live")
    assert config.default_response_mode == "gemini_live" and config.tts_provider == "fish"
    data = TestClient(app).get("/api/config").json()
    assert [m["id"] for m in data["response_modes"]] == ["gemini_live", "groq_fish", "gemini_fish"]
    assert "demo" not in data


@pytest.mark.parametrize("mode,provider,module,key", [
    ("groq_fish", GroqProvider, groq, "groq_api_key"),
    ("gemini_fish", GeminiChainProvider, gemini, "gemini_api_key"),
])
def test_actual_text_adapter_to_fish_stream_pipeline(monkeypatch, mode, provider, module, key):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append((str(request.url), body, dict(request.headers)))
        if str(request.url).endswith("/chat/completions"):
            return httpx.Response(200, content=SSE)
        assert request.headers["model"] == "s2.1-pro-free"
        assert body["reference_id"] == DEFAULT_FISH_VOICE
        return httpx.Response(200, content=MP3, headers={"Content-Type":"audio/mpeg"})
    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(module, "_get_client", lambda: transport)
    monkeypatch.setattr(fish, "_get_client", lambda: transport)
    monkeypatch.setattr(fish, "_get_stream_client", lambda: transport)
    monkeypatch.setattr(settings, key, "test-credential")
    monkeypatch.setattr(settings, "fish_api_key", "test-fish")
    engine = Engine()
    engine.providers[mode] = provider(api_key="test-credential")
    session = Session(id="mode-test", response_mode=mode)
    events = []
    async def send(event): events.append(event)
    async def run():
        await engine.handle_user_turn(session, "My day was good and I spent time with my family.", send, time.time(), turn_id="pipeline")
        assert next(i for i,e in enumerate(events) if e["type"] == "tts_sentence") < next(i for i,e in enumerate(events) if e["type"] == "llm_done")
        from app.main import tts_stream, tts, TTSReq
        first = next(e["text"] for e in events if e["type"] == "tts_sentence")
        response = await tts_stream(text=first, voice=DEFAULT_FISH_VOICE, engine="fish")
        received = b"".join([part async for part in response.body_iterator])
        assert response.media_type == "audio/mpeg" and received == MP3
        post = await tts(TTSReq(text="A second phrase.",voice=DEFAULT_FISH_VOICE,engine="fish"))
        assert post.media_type == "audio/mpeg" and post.body == MP3
        await transport.aclose()
    asyncio.run(run())
    assert len([r for r in requests if r[0].endswith("/chat/completions")]) == 1
    assert len([r for r in requests if r[0].endswith("/v1/tts")]) == 2


def test_missing_fish_key_is_explicit_and_never_mocked(monkeypatch):
    monkeypatch.setattr(settings,"fish_api_key","")
    engine = Engine()
    events=[]
    async def send(event): events.append(event)
    asyncio.run(engine.handle_user_turn(Session(response_mode="groq_fish"),"Hello",send,time.time(),turn_id="missing"))
    assert any(e["type"]=="error" and "FISH_API_KEY" in e["message"] for e in events)
    assert not any(e["type"] in ("llm_done","tts_sentence") for e in events)


def test_idle_cancel_does_not_interrupt_a_healthy_live_connection():
    session=Session(id="idle")
    class Healthy:
        calls=0
        async def interrupt(self): self.calls+=1
    live=Healthy(); session._live_session=live
    from app.conversation.engine import engine
    engine.sessions[session.id]=session
    asyncio.run(cancel_turn(session))
    assert live.calls==0 and session._live_session is live


def test_explicit_fish_engine_never_routes_a_stale_gemini_voice_to_native(monkeypatch):
    called=[]
    from app.providers.tts.gemini import GeminiTTSProvider
    async def unexpected(*args,**kwargs): called.append(1);return b"wrong",None
    monkeypatch.setattr(GeminiTTSProvider,"synthesize",unexpected)
    result=TestClient(app).post("/api/tts",json={"text":"Hello","voice":"Orbit","engine":"fish"})
    assert result.status_code>=400 and not called


def test_native_voice_clip_uses_live_model_and_cleans_up(monkeypatch):
    from app.providers.tts import gemini as tts_module
    calls=[]
    class Live:
        def __init__(self,**kwargs): calls.append(kwargs)
        async def connect(self,**kwargs): pass
        async def send_user_text(self,text,on_audio=None):
            await on_audio(base64.b64encode(b"\x00\x00"*120).decode(),"audio/pcm;rate=24000")
        async def close(self): calls.append("closed")
    monkeypatch.setattr(tts_module,"GeminiLiveSession",Live)
    audio,error=asyncio.run(tts_module.GeminiTTSProvider(api_key="test").synthesize("Hello",voice_name="Orbit"))
    assert error is None and audio.startswith(b"RIFF")
    assert calls[0]["voice_name"]=="Orbit" and calls[-1]=="closed"


def test_gemma_fallback_does_not_receive_unsupported_thinking_config(monkeypatch):
    bodies=[]
    def handler(request):
        bodies.append(json.loads(request.content));return httpx.Response(200,content=SSE)
    client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gemini,"_get_client",lambda:client)
    async def run():
        await GeminiChainProvider(models=["gemma-4-31b-it"],api_key="test").complete([{"role":"user","content":"Hello"}])
        await client.aclose()
    asyncio.run(run())
    assert "extra_body" not in bodies[0]


def test_actual_live_protocol_streams_pcm_and_reuses_warm_socket(monkeypatch):
    from app.providers.llm import gemini_live
    monkeypatch.setattr(settings, 'gemini_api_key', 'test-live-key')
    connections = []
    class Socket:
        def __init__(self):
            self.messages = []
            self.pending = asyncio.Queue()
            self.closed = False
        async def send(self, message):
            payload = json.loads(message)
            self.messages.append(payload)
            if 'setup' in payload:
                self.pending.put_nowait(json.dumps({'setupComplete': {}}))
            elif payload.get('clientContent', {}).get('turns'):
                self.pending.put_nowait(json.dumps({'serverContent': {
                    'outputTranscription': {'text': 'That sounds interesting. Tell me more.'},
                    'modelTurn': {'parts': [{'inlineData': {'data': base64.b64encode(b'\x00\x00' * 120).decode(), 'mimeType': 'audio/pcm;rate=24000'}}]},
                    'turnComplete': True}}))
        async def recv(self): return await self.pending.get()
        async def close(self): self.closed = True
    async def connect(*args, **kwargs):
        socket = Socket(); connections.append(socket); return socket
    monkeypatch.setattr(gemini_live.websockets, 'connect', connect)
    instance = Engine()
    session = Session(id='native-protocol', response_mode='gemini_live')
    events = []
    async def send(event): events.append(event)
    async def run():
        await instance.handle_user_turn(session, 'I spent today practicing with my family.', send, time.time(), turn_id='live-one')
        await instance.handle_user_turn(session, 'We practiced again during the evening.', send, time.time(), turn_id='live-two')
        assert len(connections) == 1
        messages = connections[0].messages
        assert len([m for m in messages if m.get('clientContent', {}).get('turns')]) == 2
        assert not any(m.get('clientContent', {}).get('turns') == [] for m in messages)
        assert [m['turn'] for m in events if m['type'] == 'live_audio_chunk'] == ['live-one', 'live-two']
        assert not any(m['type'] == 'tts_sentence' for m in events)
        await instance.close_live(session)
        assert connections[0].closed
    asyncio.run(run())
