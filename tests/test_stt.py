"""Server-side transcription (utterance audio -> text) for always-open mobile capture."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.stt import groq_whisper as gw_mod
from app.providers.stt.groq_whisper import GroqWhisperSTT

AUDIO = b"\x1a\x45\xdf\xa3" + b"a" * 5000


def _ok_client(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["ctype"] = request.headers.get("content-type")
        return httpx.Response(200, json={"text": "hello there"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gw_mod, "_get_client", lambda: client)
    return client, seen


def test_whisper_posts_multipart_transcription_shape(monkeypatch):
    client, seen = _ok_client(monkeypatch)
    p = GroqWhisperSTT(base_url="https://api.groq.com/openai/v1", api_key="test-key")
    text, err, _det = asyncio.run(p.transcribe(AUDIO, filename="u.webm", language="en"))
    asyncio.run(client.aclose())
    assert err is None
    assert text == "hello there"
    assert seen["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert seen["auth"].startswith("Bearer ")
    assert "multipart/form-data" in seen["ctype"]


def test_whisper_without_key_is_a_clear_error():
    p = GroqWhisperSTT(api_key="")
    text, err, _det = asyncio.run(p.transcribe(AUDIO))
    assert text == "" and "unavailable" in err


def test_whisper_rejects_empty_and_oversize(monkeypatch):
    p = GroqWhisperSTT(api_key="test-key")
    text, err, _det = asyncio.run(p.transcribe(b"tiny"))
    assert text == "" and err
    text, err, _det = asyncio.run(p.transcribe(b"x" * (gw_mod.MAX_AUDIO_BYTES + 1)))
    assert text == "" and "large" in err


def test_whisper_surfaces_upstream_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "busy"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gw_mod, "_get_client", lambda: client)
    p = GroqWhisperSTT(api_key="test-key")
    text, err, _det = asyncio.run(p.transcribe(AUDIO))
    asyncio.run(client.aclose())
    assert text == "" and "rate-limited" in err


def test_whisper_forwards_real_container_type_and_detail(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ctype"] = request.headers.get("content-type")
        return httpx.Response(400, json={"error": {"message": "file must be one of the following types: [flac mp3]", "type": "invalid"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gw_mod, "_get_client", lambda: client)
    p = GroqWhisperSTT(api_key="test-key")
    text, err, _det = asyncio.run(p.transcribe(AUDIO, filename="u.m4a", content_type="audio/mp4"))
    asyncio.run(client.aclose())
    assert "multipart/form-data" in seen["ctype"]
    assert text == "" and "format not accepted" in err


def _client():
    return TestClient(app, raise_server_exceptions=False)


def test_stt_endpoint_transcribes_audio(monkeypatch):
    client, _ = _ok_client(monkeypatch)
    c = _client()
    # force availability regardless of env keys
    monkeypatch.setattr(gw_mod.GroqWhisperSTT, "available", property(lambda self: True))
    r = c.post("/api/stt", content=AUDIO, headers={"Content-Type": "audio/webm", "X-Language": "en"})
    asyncio.run(client.aclose())
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "hello there"


def test_stt_endpoint_never_forces_upstream_language(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "नमस्ते"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gw_mod, "_get_client", lambda: client)
    c = _client()
    monkeypatch.setattr(gw_mod.GroqWhisperSTT, "available", property(lambda self: True))
    # Hindi speech arriving under the English hint must still auto-detect:
    # no forced `language` field may reach the transcriber.
    r = c.post("/api/stt", content=AUDIO, headers={"Content-Type": "audio/webm", "X-Language": "en"})
    asyncio.run(client.aclose())
    assert r.status_code == 200, r.text
    assert b'name="language"' not in seen["body"]


def test_stt_endpoint_rejects_non_audio():
    c = _client()
    r = c.post("/api/stt", content=b"{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 415


def test_stt_endpoint_rejects_empty_audio(monkeypatch):
    c = _client()
    monkeypatch.setattr(gw_mod.GroqWhisperSTT, "available", property(lambda self: True))
    r = c.post("/api/stt", content=b"\x00" * 10, headers={"Content-Type": "audio/webm"})
    assert r.status_code == 400


def test_stt_endpoint_503_without_key(monkeypatch):
    c = _client()
    monkeypatch.setattr(gw_mod.GroqWhisperSTT, "available", property(lambda self: False))
    r = c.post("/api/stt", content=AUDIO, headers={"Content-Type": "audio/webm"})
    assert r.status_code == 503


def test_public_config_exposes_only_availability_flag():
    c = _client()
    cfg = c.get("/api/config").json()
    assert cfg["server_stt"] in (True, False)
    # this change must not add new provider leaks (notably the STT backend)
    assert "whisper" not in str(cfg).lower()


def test_whisper_sends_prompt_when_given(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "hi"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gw_mod, "_get_client", lambda: client)
    p = GroqWhisperSTT(api_key="test-key")
    text, err, _det = asyncio.run(p.transcribe(AUDIO, prompt="नमस्ते context"))
    asyncio.run(client.aclose())
    assert err is None and text == "hi"
    assert b"prompt" in seen["body"]


def test_whisper_leaves_language_to_auto_detect(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "hi"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gw_mod, "_get_client", lambda: client)
    p = GroqWhisperSTT(api_key="test-key")
    # even when the caller names a language, no `language` field is forced:
    # forcing `en` on Hindi speech mangles it, verified live.
    text, err, _det = asyncio.run(p.transcribe(AUDIO, language=None, prompt="sample"))
    asyncio.run(client.aclose())
    assert err is None
    assert b'name="language"' not in seen["body"]


def test_stt_prompt_uses_preferred_sample():
    from app.main import _stt_prompt

    prompt, context = _stt_prompt("hindi", "")
    assert "नमस्ते" in prompt and context == ""
    prompt_es, _ = _stt_prompt("spanish", "")
    assert "¿cómo estás" in prompt_es
    prompt_none, _ = _stt_prompt(None, "")
    assert prompt_none == ""  # no hint, no session: pure auto-detect
    prompt_unknown, _ = _stt_prompt("klingon", "")
    assert prompt_unknown == ""
    assert len(prompt) <= 300


def test_stt_prompt_includes_session_context():
    from app.conversation.engine import engine as _eng
    from app.main import _stt_prompt

    s = _eng.get_or_create("stt-prompt-test-sid")
    s.turns.append({"role": "user", "text": "ordering coffee at Central Perk", "t": 0})
    try:
        prompt, context = _stt_prompt("english", "stt-prompt-test-sid")
        assert "Central Perk" in prompt and "Central Perk" in context
    finally:
        _eng.sessions.pop("stt-prompt-test-sid", None)


def test_stt_prompt_covers_last_spoken_language():
    from app.conversation.engine import engine as _eng
    from app.main import _stt_prompt

    s = _eng.get_or_create("stt-switch-test-sid")
    s.last_spoken_lang = "spanish"
    try:
        # preferred english + recently-spoken spanish: both samples ride along
        # so switchers stay covered without an extra round trip
        prompt, _ = _stt_prompt("english", "stt-switch-test-sid")
        assert "how are you doing today" in prompt
        assert "¿cómo estás" in prompt
        # same language twice: sample appears once
        s.last_spoken_lang = "english"
        prompt2, _ = _stt_prompt("english", "stt-switch-test-sid")
        assert prompt2.count("how are you doing today") == 1
    finally:
        _eng.sessions.pop("stt-switch-test-sid", None)


def test_hallucination_filter():
    from app.main import _looks_hallucinated

    assert _looks_hallucinated("", "", 0.05) is True
    assert _looks_hallucinated("hello hello hello hello", "", 0.05) is True
    assert _looks_hallucinated("Thanks for watching my video", "", 0.05) is True
    assert _looks_hallucinated("ordering coffee at Central Perk", "ordering coffee at Central Perk", 0.05) is True
    assert _looks_hallucinated("ok", "ok sure", 0.004) is True  # quiet + tiny
    assert _looks_hallucinated("yes", "", None) is False  # no energy reading: not condemned
    # real replies pass through
    assert _looks_hallucinated("haan, chalo coffee peete hain", "something else entirely", 0.04) is False
    assert _looks_hallucinated("yes", "", 0.03) is False
    assert _looks_hallucinated("नमस्ते, आप कैसे हैं", "", 0.05) is False


def test_stt_endpoint_drops_hallucinated_transcript(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "hello hello hello hello"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gw_mod, "_get_client", lambda: client)
    c = _client()
    monkeypatch.setattr(gw_mod.GroqWhisperSTT, "available", property(lambda self: True))
    r = c.post("/api/stt", content=AUDIO, headers={"Content-Type": "audio/webm", "X-Level": "0.04"})
    asyncio.run(client.aclose())
    assert r.status_code == 502
    assert "catch" in r.json()["error"]


def _verbose_client(monkeypatch, segments):
    def handler(request: httpx.Request) -> httpx.Response:
        full = " ".join(s["text"] for s in segments)
        return httpx.Response(200, json={"text": full, "segments": segments})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gw_mod, "_get_client", lambda: client)
    return client


def test_whisper_joins_verbose_segments(monkeypatch):
    client = _verbose_client(monkeypatch, [
        {"text": "hello there", "no_speech_prob": 0.02},
        {"text": "how are you", "no_speech_prob": 0.10},
    ])
    p = GroqWhisperSTT(api_key="test-key")
    text, err, _det = asyncio.run(p.transcribe(AUDIO))
    asyncio.run(client.aclose())
    assert err is None
    assert text == "hello there how are you"


def test_whisper_drops_high_no_speech_probability(monkeypatch):
    client = _verbose_client(monkeypatch, [
        {"text": "yeah so anyway the thing is", "no_speech_prob": 0.85},
        {"text": "right, exactly", "no_speech_prob": 0.78},
    ])
    p = GroqWhisperSTT(api_key="test-key")
    text, err, _det = asyncio.run(p.transcribe(AUDIO))
    asyncio.run(client.aclose())
    assert text == "" and "catch" in err


def test_stt_endpoint_drops_speaker_echo(monkeypatch):
    from app.conversation.engine import engine as _eng

    s = _eng.get_or_create("stt-echo-test-sid")
    s.turns.append({"role": "assistant", "text": "so tell me about your weekend plans in detail", "t": 0})
    try:
        client = _verbose_client(monkeypatch, [
            {"text": "so tell me about your weekend plans in detail", "no_speech_prob": 0.05},
        ])
        c = _client()
        monkeypatch.setattr(gw_mod.GroqWhisperSTT, "available", property(lambda self: True))
        r = c.post("/api/stt", content=AUDIO, headers={
            "Content-Type": "audio/webm", "X-Level": "0.05",
            "X-Session": "stt-echo-test-sid", "X-Barged": "1",
        })
        asyncio.run(client.aclose())
        assert r.status_code == 502, r.text
        assert "catch" in r.json()["error"]
    finally:
        _eng.sessions.pop("stt-echo-test-sid", None)


def test_stt_endpoint_accepts_distinct_barge(monkeypatch):
    from app.conversation.engine import engine as _eng

    s = _eng.get_or_create("stt-barge-test-sid")
    s.turns.append({"role": "assistant", "text": "so tell me about your weekend plans in detail", "t": 0})
    try:
        client = _verbose_client(monkeypatch, [
            {"text": "haan, weekend pe hum Goa ja rahe hain", "no_speech_prob": 0.04},
        ])
        c = _client()
        monkeypatch.setattr(gw_mod.GroqWhisperSTT, "available", property(lambda self: True))
        r = c.post("/api/stt", content=AUDIO, headers={
            "Content-Type": "audio/webm", "X-Level": "0.05",
            "X-Session": "stt-barge-test-sid", "X-Barged": "1",
        })
        asyncio.run(client.aclose())
        assert r.status_code == 200, r.text
        assert "Goa" in r.json()["text"]
    finally:
        _eng.sessions.pop("stt-barge-test-sid", None)


def test_stt_endpoint_accepts_session_header(monkeypatch):
    client, _ = _ok_client(monkeypatch)
    c = _client()
    monkeypatch.setattr(gw_mod.GroqWhisperSTT, "available", property(lambda self: True))
    r = c.post("/api/stt", content=AUDIO, headers={
        "Content-Type": "audio/webm", "X-Language": "hindi", "X-Session": "nope-unknown-sid",
    })
    asyncio.run(client.aclose())
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "hello there"


def test_whisper_detected_language_mapping(monkeypatch):
    from app.providers.stt.groq_whisper import _detect_id

    assert _detect_id("Spanish") == "spanish"
    assert _detect_id("hindi") == "hindi"
    assert _detect_id("Chinese") == "chinese"
    assert _detect_id("Mandarin") == "chinese"
    assert _detect_id("Klingon") is None
    assert _detect_id("") is None
    assert _detect_id(None) is None


def test_whisper_returns_detected_language(monkeypatch):
    client = _verbose_client(monkeypatch, [
        {"text": "hola buenas tardes", "no_speech_prob": 0.03},
    ])
    # _verbose_client builds no language field; patch handler via fresh mock
    asyncio.run(client.aclose())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "text": "hola buenas tardes",
            "language": "spanish",
            "segments": [{"text": "hola buenas tardes", "no_speech_prob": 0.03}],
        })

    client2 = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gw_mod, "_get_client", lambda: client2)
    p = GroqWhisperSTT(api_key="test-key")
    text, err, detected = asyncio.run(p.transcribe(AUDIO))
    asyncio.run(client2.aclose())
    assert err is None and text == "hola buenas tardes"
    assert detected == "spanish"


def test_stt_endpoint_returns_detected_language(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "text": "नमस्ते", "language": "hindi",
            "segments": [{"text": "नमस्ते", "no_speech_prob": 0.02}],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gw_mod, "_get_client", lambda: client)
    c = _client()
    monkeypatch.setattr(gw_mod.GroqWhisperSTT, "available", property(lambda self: True))
    r = c.post("/api/stt", content=AUDIO, headers={
        "Content-Type": "audio/webm", "X-Language": "english", "X-Level": "0.04",
    })
    asyncio.run(client.aclose())
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "नमस्ते", "language": "hindi"}


def test_public_config_languages_no_hinglish():
    c = _client()
    cfg = c.get("/api/config").json()
    ids = [l["id"] for l in cfg["languages"]]
    assert "hinglish" not in ids
    for want in ("english", "hindi", "spanish", "french", "tamil", "urdu"):
        assert want in ids, want
    for entry in cfg["languages"]:
        assert entry["stt"]  # every language needs a fallback STT code


def test_turn_req_accepts_reply_lang():
    from app.main import TurnReq

    req = TurnReq(text="hola", reply_lang="spanish")
    assert req.reply_lang == "spanish"
    assert TurnReq(text="hi").reply_lang == ""
