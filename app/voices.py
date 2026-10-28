"""Single source of truth for Fish voices: id, UI label, persona gender.
Sarah and Natasha are female; everything else is male.
Persona name always equals the selected voice's name."""

FISH_VOICES = [
    {"id": "802e3bc2b27e49c2995d23ef70e6ac89", "label": "Flow", "gender": "male"},
    {"id": "8b734220ba20480aa95b5e6329730003", "label": "Modi", "gender": "male"},
    {"id": "010da99c66f44d319159c8588315f22c", "label": "Trump", "gender": "male"},
    {"id": "06134e9a49cb4a36997b75402c8a0621", "label": "Osho", "gender": "male"},
    {"id": "5c440edc2182445eaf7e2da219a24c03", "label": "Rahul", "gender": "male"},
    {"id": "7e67b3e4d059464c8bdd9e9b3fde6f9e",
        "label": "Nobita", "gender": "male"},
    {"id": "e75da8399ae344dcbfca55839b464a2a",
        "label": "Sinchan", "gender": "male"},
    {"id": "933563129e564b19a115bedd57b7406a",
        "label": "Sarah", "gender": "female"},
    {"id": "e80db686476f4ccda758da35cacfb993",
        "label": "Natasha", "gender": "female"},
    {"id": "59e9dc1cb20c452584788a2690c80970",
        "label": "Olivia", "gender": "female"},
    {"id": "98655a12fa944e26b274c535e5e03842",
        "label": "Cutie", "gender": "female"},
    {"id": "4c00e9ff458246c1855535d9fc637d04",
        "label": "Priya", "gender": "female"},
    {"id": "8988e6f626114e78bfc4482e0f46b4be", "label": "Aman", "gender": "male"},
]

DEFAULT_FISH_VOICE = FISH_VOICES[0]["id"]

GEMINI_VOICES = [
    # Gemini Live App Voices
    {
        "id": "Orbit",
        "label": "Orbit (Energetic, engaging, clear)",
        "gender": "male",
        "group": "Gemini Live App Voices",
        "description": "Energetic, engaging, and clear; typically serves as the standard default voice for real-time conversation.",
    },
    {
        "id": "Pegasus",
        "label": "Pegasus (Warm, resonant, friendly)",
        "gender": "male",
        "group": "Gemini Live App Voices",
        "description": "Warm, resonant, and friendly with a steady conversational rhythm.",
    },
    {
        "id": "Capella",
        "label": "Capella (Smooth, clear, articulate)",
        "gender": "female",
        "group": "Gemini Live App Voices",
        "description": "Smooth, clear, and highly articulate.",
    },
    {
        "id": "Dipper",
        "label": "Dipper (Deeper, calm, grounded)",
        "gender": "male",
        "group": "Gemini Live App Voices",
        "description": "Deeper, calm, and grounded with a relaxed pacing.",
    },
    {
        "id": "Orion",
        "label": "Orion (Bright, expressive, dynamic)",
        "gender": "male",
        "group": "Gemini Live App Voices",
        "description": "Bright, expressive, and dynamic.",
    },
    {
        "id": "Ursa",
        "label": "Ursa (Conversational, comforting, natural)",
        "gender": "female",
        "group": "Gemini Live App Voices",
        "description": "Conversational, comforting, and natural.",
    },
    {
        "id": "Vega",
        "label": "Vega (Crisp, upbeat, modern)",
        "gender": "female",
        "group": "Gemini Live App Voices",
        "description": "Crisp, upbeat, and modern.",
    },
    {
        "id": "Eclipse",
        "label": "Eclipse (Focused, precise, measured)",
        "gender": "male",
        "group": "Gemini Live App Voices",
        "description": "Focused, precise, and measured.",
    },
    {
        "id": "Flare",
        "label": "Flare (Vibrant, expressive, enthusiastic)",
        "gender": "female",
        "group": "Gemini Live App Voices",
        "description": "Vibrant, expressive, and enthusiastic; optimized for fast-paced interactions.",
    },
    {
        "id": "Glow",
        "label": "Glow (Bright, friendly, smooth)",
        "gender": "female",
        "group": "Gemini Live App Voices",
        "description": "Bright, friendly, and smooth.",
    },
    # Gemini API / Studio Prebuilt Voices
    {
        "id": "Puck",
        "label": "Puck (Energetic and casual)",
        "gender": "male",
        "group": "Gemini API / Studio Prebuilt Voices",
        "description": "Energetic and casual, ideal for everyday conversational prompts.",
    },
    {
        "id": "Charon",
        "label": "Charon (Warm and deep)",
        "gender": "male",
        "group": "Gemini API / Studio Prebuilt Voices",
        "description": "Warm and deep, giving an authoritative yet friendly presentation.",
    },
    {
        "id": "Kore",
        "label": "Kore (Grounded and clear)",
        "gender": "female",
        "group": "Gemini API / Studio Prebuilt Voices",
        "description": "Grounded and clear, commonly used for technical or detailed reading.",
    },
    {
        "id": "Aoede",
        "label": "Aoede (Soft and melodic)",
        "gender": "female",
        "group": "Gemini API / Studio Prebuilt Voices",
        "description": "Soft and melodic, built for expressive and creative storytelling.",
    },
    {
        "id": "Fenrir",
        "label": "Fenrir (Strong and bold)",
        "gender": "male",
        "group": "Gemini API / Studio Prebuilt Voices",
        "description": "Strong and bold, carrying a distinct presence suitable for commands and gaming contexts.",
    },
    {
        "id": "Enceladus",
        "label": "Enceladus (Smooth and relaxed)",
        "gender": "male",
        "group": "Gemini API / Studio Prebuilt Voices",
        "description": "Smooth and relaxed, optimal for long-form listening or background audio.",
    },
]

DEFAULT_GEMINI_VOICE = "Orbit"


def persona_for(voice_id: str | None) -> tuple[str, str]:
    """Return (name, gender) for a voice id. Unknown/empty → default voice."""
    for v in GEMINI_VOICES:
        if v["id"] == voice_id:
            return v["id"], v["gender"]
    for v in FISH_VOICES:
        if v["id"] == voice_id:
            return v["label"], v["gender"]
    return GEMINI_VOICES[0]["id"], GEMINI_VOICES[0]["gender"]
