"""Language option tests: prompt addenda, session default, WS switching."""
import sys
sys.path.insert(0, ".")
from app.conversation.prompts import build_system, LANGUAGES
from app.conversation.engine import Engine
from fastapi.testclient import TestClient
from app.main import app


def test_languages_known():
    assert set(LANGUAGES) == {"english", "hindi", "hinglish"}


def test_hindi_prompt():
    s = build_system("free", "B1", "auto", "balanced", language="hindi")
    assert "Hindi" in s and "Devanagari" in s


def test_hinglish_prompt():
    s = build_system("free", "B1", "auto", "balanced", language="hinglish")
    assert "Hinglish" in s


def test_english_prompt_has_no_addendum():
    s = build_system("free", "B1", "auto", "balanced", language="english")
    assert "HINGLISH" not in s and "HINDI" not in s


def test_bad_language_falls_back():
    s = build_system("free", "B1", "auto", "balanced", language="klingon")
    assert "HINGLISH" not in s and "HINDI" not in s


def test_session_default_english():
    e = Engine()
    s = e.get_or_create(None)
    assert s.language == "english"


def test_ws_set_language():
    c = TestClient(app)
    with c.websocket_connect("/ws/session/langtest") as ws:
        ws.receive_json()  # ready (no greeting — user speaks first)
        ws.send_json({"type": "set_language", "value": "hindi"})
        for _ in range(10):
            m = ws.receive_json()
            if m["type"] == "language":
                assert m["value"] == "hindi"
                break
        else:
            raise AssertionError("no language ack")
        ws.send_json({"type": "set_language", "value": "klingon"})
        # invalid ignored: no crash, next ping works
        ws.send_json({"type": "ping"})
        for _ in range(10):
            m = ws.receive_json()
            if m["type"] == "pong":
                break
        else:
            raise AssertionError("no pong after invalid language")
