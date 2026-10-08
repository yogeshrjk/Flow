"""Single source of truth for Fish voices: id, UI label, persona gender.
Sarah and Natasha are female; everything else is male.
Persona name always equals the selected voice's name."""

FISH_VOICES = [
    {"id": "802e3bc2b27e49c2995d23ef70e6ac89", "label": "Flow", "gender": "male"},
    {"id": "8b734220ba20480aa95b5e6329730003", "label": "Modi", "gender": "male"},
    {"id": "010da99c66f44d319159c8588315f22c", "label": "Trump", "gender": "male"},
    {"id": "06134e9a49cb4a36997b75402c8a0621", "label": "Osho", "gender": "male"},
    {"id": "5c440edc2182445eaf7e2da219a24c03", "label": "Pragyesh", "gender": "male"},
    {"id": "7e67b3e4d059464c8bdd9e9b3fde6f9e", "label": "Nobita", "gender": "male"},
    {"id": "e75da8399ae344dcbfca55839b464a2a", "label": "Sinchan", "gender": "male"},
    {"id": "933563129e564b19a115bedd57b7406a", "label": "Sarah", "gender": "female"},
    {"id": "e80db686476f4ccda758da35cacfb993", "label": "Natasha", "gender": "female"},
    {"id": "59e9dc1cb20c452584788a2690c80970", "label": "Olivia", "gender": "female"},
    {"id": "98655a12fa944e26b274c535e5e03842", "label": "Cutie", "gender": "female"},
    {"id": "4c00e9ff458246c1855535d9fc637d04", "label": "Priya", "gender": "female"},
    {"id": "8988e6f626114e78bfc4482e0f46b4be", "label": "Aman", "gender": "male"},
]

DEFAULT_FISH_VOICE = FISH_VOICES[0]["id"]

GEMINI_VOICES = [
    {"id": "Puck", "label": "Puck (Gemini Live)", "gender": "male"},
    {"id": "Aoede", "label": "Aoede (Gemini Live)", "gender": "female"},
    {"id": "Charon", "label": "Charon (Gemini Live Deep)", "gender": "male"},
    {"id": "Kore", "label": "Kore (Gemini Live Calm)", "gender": "female"},
    {"id": "Fenrir", "label": "Fenrir (Gemini Live Resonant)", "gender": "male"},
]

DEFAULT_GEMINI_VOICE = "Puck"


def persona_for(voice_id: str | None) -> tuple[str, str]:
    """Return (name, gender) for a voice id. Unknown/empty → default voice."""
    for v in FISH_VOICES:
        if v["id"] == voice_id:
            return v["label"], v["gender"]
    for v in GEMINI_VOICES:
        if v["id"] == voice_id:
            return v["id"], v["gender"]
    return FISH_VOICES[0]["label"], FISH_VOICES[0]["gender"]
