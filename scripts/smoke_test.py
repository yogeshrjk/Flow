"""Deterministic zero-key smoke test; no import-time network/provider calls."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    import tempfile
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app
    from app.conversation.engine import engine
    from tests.mock_provider import MockProvider
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as directory:
        settings.data_dir = directory
        with patch.object(engine, "providers", {"groq_fish": MockProvider()}), patch.object(settings, "fish_api_key", "test-fish"), TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/api/config").json()["tts_provider"]
            with client.websocket_connect("/ws/session/smoke") as ws:
                assert ws.receive_json()["type"] == "ready"
                ws.send_json({"type": "set_response_mode", "value": "groq_fish"})
                assert ws.receive_json()["value"] == "groq_fish"
                ws.send_json({"type": "user_transcript", "text": "I practiced English with my family today.", "turn_id": "smoke"})
                done = False
                while True:
                    event = ws.receive_json()
                    done = done or event["type"] == "llm_done"
                    if done and event["type"] == "status" and event.get("state") == "LISTENING": break
                ws.send_json({"type": "barge_in"})
                assert ws.receive_json()["state"] == "INTERRUPTED"
            exercise = client.get("/api/learning/listening").json()
            assert client.post("/api/learning/listening/answer", json={"exercise_id": exercise["id"], "answer": 0}).status_code == 200
            assert len(client.get("/api/learning/plan").json()["tasks"]) == 5
            assert client.post("/api/session/smoke/end").status_code == 200
    print("PASS: config, WS turn, interruption, cleanup, listening, daily plan and summary")


if __name__ == "__main__":
    main()
