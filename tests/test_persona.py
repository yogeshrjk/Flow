"""Voice-driven persona tests: name follows voice, Sarah/Natasha female, rest male."""
import sys
sys.path.insert(0, ".")
from app.voices import FISH_VOICES, persona_for
from app.conversation.prompts import build_system
from fastapi.testclient import TestClient
from app.main import app

SARAH = "933563129e564b19a115bedd57b7406a"
NATASHA = "e80db686476f4ccda758da35cacfb993"
FLOW = "802e3bc2b27e49c2995d23ef70e6ac89"


def test_sarah_natasha_female_rest_male():
    assert persona_for(SARAH) == ("Sarah", "female")
    assert persona_for(NATASHA) == ("Natasha", "female")
    female_names = {"Sarah", "Natasha", "Olivia", "Cutie", "Priya"}
    for v in FISH_VOICES:
        name, gender = persona_for(v["id"])
        if name in female_names:
            assert gender == "female"
        else:
            assert gender == "male", name


def test_unknown_voice_falls_back_to_flow():
    assert persona_for("nope") == ("Flow", "male")
    assert persona_for("") == ("Flow", "male")
    assert persona_for(None) == ("Flow", "male")


def test_prompt_uses_voice_name_and_gender():
    s = build_system("free", "B1", "auto", "balanced", persona_name="Sarah", persona_gender="female")
    assert "Sarah" in s and "woman" in s and "Priya" not in s and "Noor" not in s
    m = build_system("free", "B1", "auto", "balanced", persona_name="Modi", persona_gender="male")
    assert "Modi" in m and "man" in m


def test_ws_set_voice_persona():
    c = TestClient(app)
    with c.websocket_connect("/ws/session/personatest") as ws:
        ws.receive_json()  # ready (no greeting — user speaks first)
        ws.send_json({"type": "set_voice", "value": SARAH})
        for _ in range(10):
            m = ws.receive_json()
            if m["type"] == "voice":
                assert m["value"] == SARAH and m["persona"] == "Sarah"
                break
        else:
            raise AssertionError("no voice ack")
