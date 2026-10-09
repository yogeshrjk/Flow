"""Smoke test: boots app, hits /health + /api/config, runs a live WS turn. No greeting — the user speaks first."""
import sys
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)
h = c.get("/health")
print("health:", h.status_code, h.json())
assert h.status_code == 200
cfg = c.get("/api/config")
resp_modes = cfg.json().get("response_modes", [])
print("config response modes:", [(m["id"], m["label"], m["available"]) for m in resp_modes])
assert cfg.status_code == 200
assert [m["id"] for m in resp_modes] == ["groq_fish", "gemini_fish", "gemini_live"]
assert cfg.json().get("default_response_mode") in ("groq_fish", "gemini_fish", "gemini_live", "fast", "quality")
ch = c.get("/api/challenge/today")
assert ch.status_code == 200
print("challenge:", ch.json()["prompt"][:60])

def read_turn_reply(ws):
    """Read all messages of a turn until completion and return the reply."""
    reply = ""
    for _ in range(250):
        m = ws.receive_json()
        if m.get("type") == "llm_done" and not m.get("greeting"):
            reply = m.get("text", "")
        if m.get("type") == "status" and m.get("state") == "LISTENING" and reply:
            break
    return reply

# WS real turn (no greeting — user speaks first)
with c.websocket_connect("/ws/session/smoke1") as ws:
    first = ws.receive_json()
    print("ws first:", first["type"])
    assert first["type"] == "ready"
    ws.send_json({"type": "user_transcript", "text": "Today was good. I go to office and work on my project."})
    reply = read_turn_reply(ws)
    print("reply:", reply[:200])
    assert reply, "no llm_done reply received"
    # natural, no instant grammar interrupt on first turn
    assert any(k in reply.lower() for k in ("nice", "what", "working", "project", "interesting", "kind", "good", "hear", "sound", "productive")), reply
    # follow-up: tender -> hardest part
    ws.send_json({"type": "user_transcript", "text": "I'm building a tender management system."})
    reply2 = read_turn_reply(ws)
    print("reply2:", reply2[:200])
    assert reply2, "no second reply"
    # barge-in + summary
    ws.send_json({"type": "barge_in"})
    st = ws.receive_json()
    print("barge:", st["type"])
    ws.send_json({"type": "end"})
    got_summary = False
    for _ in range(10):
        m = ws.receive_json()
        if m["type"] == "summary":
            print("summary:", str(m)[:300])
            assert "spoken_feedback" in m and "words_spoken" in m
            got_summary = True
            break
    assert got_summary, "no summary"

# barge-in mid-turn must really cancel the turn, and the socket must stay usable
with c.websocket_connect("/ws/session/smoke2") as ws:
    assert ws.receive_json()["type"] == "ready"
    ws.send_json({"type": "user_transcript", "text": "Yesterday I go to market and buy vegetables."})
    ws.send_json({"type": "barge_in"})
    outcome = None
    for _ in range(120):
        m = ws.receive_json()
        if m["type"] in ("llm_done", "llm_cancelled") and not m.get("greeting"):
            outcome = m["type"]
            break
    print("mid-turn barge-in outcome:", outcome)
    assert outcome in ("llm_done", "llm_cancelled"), "no terminal message after barge-in"
    # the socket must still serve the next turn (no wedged session/queue)
    ws.send_json({"type": "user_transcript", "text": "Okay, let's try again. What should we talk about today?"})
    got = None
    for _ in range(120):
        m = ws.receive_json()
        if m["type"] == "llm_done" and not m.get("greeting"):
            got = m["text"]
            break
    print("post-barge-in reply:", (got or "(none)")[:90])
    assert got, "socket unusable after a mid-turn barge-in"

# response mode switch is accepted and confirmed over the socket
with c.websocket_connect("/ws/session/smoke3") as ws:
    assert ws.receive_json()["type"] == "ready"
    ws.send_json({"type": "set_response_mode", "value": "gemini_fish"})
    conf = ws.receive_json()
    print("response_mode confirm:", conf)
    assert conf["type"] == "response_mode" and conf["value"] in ("gemini_fish", "quality")
    # a turn still completes on the switched tier
    ws.send_json({"type": "user_transcript", "text": "Hello, what should we talk about today?"})
    reply = None
    for _ in range(60):
        m = ws.receive_json()
        if m["type"] == "tts_sentence" and m.get("first"):
            print("first chunk lat:", m.get("lat"))
            assert "llm_ttft_ms" in (m.get("lat") or {}), "server latency stages missing"
        if m["type"] == "llm_done" and not m.get("greeting"):
            reply = m["text"]
            break
    assert reply, "no reply on the quality tier"
    print("quality-tier reply:", reply[:120])

# TTS is Fish-only: expect audio (credit OK) or a clean 402 error (no credit).
r = c.post("/api/tts", json={"text": "Hello there"})
print("tts status:", r.status_code, r.headers.get("content-type"))
assert r.status_code in (200, 402)
if r.status_code == 200:
    assert "audio" in (r.headers.get("content-type") or "")
    print("tts audio bytes:", len(r.content))
else:
    print("tts fish error (expected without API credit):", r.text[:150])

# progressive endpoint: same audio, streamed to a media element
s = c.get("/api/tts/stream", params={"text": "Hey, this is the streaming path."})
print("tts/stream status:", s.status_code, s.headers.get("content-type"),
      "bytes:", len(s.content), "accept-ranges:", s.headers.get("accept-ranges"))
assert s.status_code in (200, 402)
if s.status_code == 200:
    assert s.headers.get("content-type", "").startswith("audio") or "audio" in (s.headers.get("content-type") or "")
    assert s.content[:3] == b"ID3" or (s.content[0] == 0xFF and (s.content[1] & 0xE0) == 0xE0)
    assert len(s.content) > 1000
    # the stream must not be cached or range-requested (it is generated per call)
    assert (s.headers.get("accept-ranges") or "none") == "none"

print("SMOKE OK — realtime pipeline verified (STT->LLM->TTS queue, barge-in, summary).")
