"""Fish Audio S2.1 Pro emotion & delivery catalogue — the single source of truth.

Official documentation (S2 bracket syntax, which is what `s2.1-pro-free` speaks):
    https://docs.fish.audio/developer-guide/core-features/emotions

Every ``description``, ``example_context`` and ``suggested_text`` below is copied
verbatim from that page (the S2 tables, not the deprecated S1 parenthesised
mirror). Internal fields — ``category``, ``priority``, ``usage`` — are ours and
never alter the official wording.

Nothing else in the codebase should restate these tags: prompts, cleaning and
tests all read from here, so the catalogue can only drift in one place.
"""

from functools import lru_cache

DOCS_URL = "https://docs.fish.audio/developer-guide/core-features/emotions"

# Marker for a field the official table does not provide. Never invent text.
UNAVAILABLE = "unavailable in the official documentation"

# Practical selection priorities (ours, not Fish Audio's). Lower = safer/more
# useful in ordinary conversation, so it earns a place in the prompt budget
# first and the model is told to reach for it first.
PRIORITY_CORE = 1        # everyday, low-risk colouring
PRIORITY_SITUATIONAL = 2  # only when the context clearly calls for it
PRIORITY_RARE = 3         # intense / unusual states — very sparingly


EMOTIONS = {
    "happy": {
        "tag": "[happy]",
        "description": "Cheerful, upbeat tone",
        "example_context": "Good news, greetings",
        "category": "basic_emotion",
        "priority": PRIORITY_CORE,
    },
    "sad": {
        "tag": "[sad]",
        "description": "Melancholic, downcast",
        "example_context": "Sympathy, bad news",
        "category": "basic_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "angry": {
        "tag": "[angry]",
        "description": "Frustrated, aggressive",
        "example_context": "Complaints, warnings",
        "category": "basic_emotion",
        "priority": PRIORITY_RARE,
    },
    "excited": {
        "tag": "[excited]",
        "description": "Energetic, enthusiastic",
        "example_context": "Announcements, celebrations",
        "category": "basic_emotion",
        "priority": PRIORITY_CORE,
    },
    "calm": {
        "tag": "[calm]",
        "description": "Peaceful, relaxed",
        "example_context": "Instructions, meditation",
        "category": "basic_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "nervous": {
        "tag": "[nervous]",
        "description": "Anxious, uncertain",
        "example_context": "Disclaimers, apologies",
        "category": "basic_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "confident": {
        "tag": "[confident]",
        "description": "Assertive, self-assured",
        "example_context": "Presentations, sales",
        "category": "basic_emotion",
        "priority": PRIORITY_CORE,
    },
    "surprised": {
        "tag": "[surprised]",
        "description": "Shocked, amazed",
        "example_context": "Reactions, discoveries",
        "category": "basic_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "satisfied": {
        "tag": "[satisfied]",
        "description": "Content, pleased",
        "example_context": "Confirmations, reviews",
        "category": "basic_emotion",
        "priority": PRIORITY_CORE,
    },
    "delighted": {
        "tag": "[delighted]",
        "description": "Very pleased, joyful",
        "example_context": "Celebrations, compliments",
        "category": "basic_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "scared": {
        "tag": "[scared]",
        "description": "Frightened, fearful",
        "example_context": "Warnings, horror stories",
        "category": "basic_emotion",
        "priority": PRIORITY_RARE,
    },
    "worried": {
        "tag": "[worried]",
        "description": "Concerned, troubled",
        "example_context": "Concerns, questions",
        "category": "basic_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "upset": {
        "tag": "[upset]",
        "description": "Disturbed, distressed",
        "example_context": "Complaints, problems",
        "category": "basic_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "frustrated": {
        "tag": "[frustrated]",
        "description": "Annoyed, exasperated",
        "example_context": "Technical issues, delays",
        "category": "basic_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "depressed": {
        "tag": "[depressed]",
        "description": "Very sad, hopeless",
        "example_context": "Serious topics",
        "category": "basic_emotion",
        "priority": PRIORITY_RARE,
    },
    "empathetic": {
        "tag": "[empathetic]",
        "description": "Understanding, caring",
        "example_context": "Support, counseling",
        "category": "basic_emotion",
        "priority": PRIORITY_CORE,
    },
    "embarrassed": {
        "tag": "[embarrassed]",
        "description": "Ashamed, awkward",
        "example_context": "Apologies, mistakes",
        "category": "basic_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "disgusted": {
        "tag": "[disgusted]",
        "description": "Repelled, revolted",
        "example_context": "Negative reviews",
        "category": "basic_emotion",
        "priority": PRIORITY_RARE,
    },
    "moved": {
        "tag": "[moved]",
        "description": "Emotionally touched",
        "example_context": "Heartfelt moments",
        "category": "basic_emotion",
        "priority": PRIORITY_RARE,
    },
    "proud": {
        "tag": "[proud]",
        "description": "Accomplished, satisfied",
        "example_context": "Achievements, praise",
        "category": "basic_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "relaxed": {
        "tag": "[relaxed]",
        "description": "At ease, casual",
        "example_context": "Casual conversation",
        "category": "basic_emotion",
        "priority": PRIORITY_CORE,
    },
    "grateful": {
        "tag": "[grateful]",
        "description": "Thankful, appreciative",
        "example_context": "Thanks, appreciation",
        "category": "basic_emotion",
        "priority": PRIORITY_CORE,
    },
    "curious": {
        "tag": "[curious]",
        "description": "Inquisitive, interested",
        "example_context": "Questions, exploration",
        "category": "basic_emotion",
        "priority": PRIORITY_CORE,
    },
    "sarcastic": {
        "tag": "[sarcastic]",
        "description": "Ironic, mocking",
        "example_context": "Humor, criticism",
        "category": "basic_emotion",
        "priority": PRIORITY_RARE,
    },
}

ADVANCED_EMOTIONS = {
    "disdainful": {
        "tag": "[disdainful]",
        "description": "Contemptuous, scornful",
        "example_context": "Criticism, rejection",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "unhappy": {
        "tag": "[unhappy]",
        "description": "Discontent, dissatisfied",
        "example_context": "Complaints, feedback",
        "category": "advanced_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "anxious": {
        "tag": "[anxious]",
        "description": "Very worried, uneasy",
        "example_context": "Urgent matters",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "hysterical": {
        "tag": "[hysterical]",
        "description": "Uncontrollably emotional",
        "example_context": "Extreme reactions",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "indifferent": {
        "tag": "[indifferent]",
        "description": "Uncaring, neutral",
        "example_context": "Neutral responses",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "uncertain": {
        "tag": "[uncertain]",
        "description": "Doubtful, unsure",
        "example_context": "Speculation, questions",
        "category": "advanced_emotion",
        "priority": PRIORITY_CORE,
    },
    "doubtful": {
        "tag": "[doubtful]",
        "description": "Skeptical, questioning",
        "example_context": "Disbelief, questioning",
        "category": "advanced_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "confused": {
        "tag": "[confused]",
        "description": "Puzzled, perplexed",
        "example_context": "Clarification requests",
        "category": "advanced_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "disappointed": {
        "tag": "[disappointed]",
        "description": "Let down, dissatisfied",
        "example_context": "Unmet expectations",
        "category": "advanced_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "regretful": {
        "tag": "[regretful]",
        "description": "Sorry, remorseful",
        "example_context": "Apologies, mistakes",
        "category": "advanced_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "guilty": {
        "tag": "[guilty]",
        "description": "Culpable, responsible",
        "example_context": "Confessions, apologies",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "ashamed": {
        "tag": "[ashamed]",
        "description": "Deeply embarrassed",
        "example_context": "Serious mistakes",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "jealous": {
        "tag": "[jealous]",
        "description": "Envious, resentful",
        "example_context": "Comparisons",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "envious": {
        "tag": "[envious]",
        "description": "Wanting what others have",
        "example_context": "Admiration with desire",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "hopeful": {
        "tag": "[hopeful]",
        "description": "Optimistic about future",
        "example_context": "Future plans",
        "category": "advanced_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "optimistic": {
        "tag": "[optimistic]",
        "description": "Positive outlook",
        "example_context": "Encouragement",
        "category": "advanced_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "pessimistic": {
        "tag": "[pessimistic]",
        "description": "Negative outlook",
        "example_context": "Warnings, doubts",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "nostalgic": {
        "tag": "[nostalgic]",
        "description": "Longing for the past",
        "example_context": "Memories, stories",
        "category": "advanced_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "lonely": {
        "tag": "[lonely]",
        "description": "Isolated, alone",
        "example_context": "Emotional content",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "bored": {
        "tag": "[bored]",
        "description": "Uninterested, weary",
        "example_context": "Disinterest",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "contemptuous": {
        "tag": "[contemptuous]",
        "description": "Showing contempt",
        "example_context": "Strong criticism",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
    "sympathetic": {
        "tag": "[sympathetic]",
        "description": "Showing sympathy",
        "example_context": "Condolences",
        "category": "advanced_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "compassionate": {
        "tag": "[compassionate]",
        "description": "Showing deep care",
        "example_context": "Support, help",
        "category": "advanced_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "determined": {
        "tag": "[determined]",
        "description": "Resolved, decided",
        "example_context": "Goals, commitments",
        "category": "advanced_emotion",
        "priority": PRIORITY_SITUATIONAL,
    },
    "resigned": {
        "tag": "[resigned]",
        "description": "Accepting defeat",
        "example_context": "Giving up, acceptance",
        "category": "advanced_emotion",
        "priority": PRIORITY_RARE,
    },
}

TONE_CONTROLS = {
    "hurried": {
        "tag": "[in a hurry tone]",
        "description": "Rushed, urgent",
        "example_context": "Time-sensitive information",
        "category": "tone_control",
        "priority": PRIORITY_SITUATIONAL,
    },
    "shouting": {
        "tag": "[shouting]",
        "description": "Loud, calling out",
        "example_context": "Getting attention",
        "category": "tone_control",
        "priority": PRIORITY_RARE,
    },
    "screaming": {
        "tag": "[screaming]",
        "description": "Very loud, panicked",
        "example_context": "Emergencies, fear",
        "category": "tone_control",
        "priority": PRIORITY_RARE,
    },
    "whispering": {
        "tag": "[whispering]",
        "description": "Very soft, secretive",
        "example_context": "Secrets, quiet scenes",
        "category": "tone_control",
        "priority": PRIORITY_RARE,
    },
    "soft": {
        "tag": "[soft tone]",
        "description": "Gentle, quiet",
        "example_context": "Comfort, lullabies",
        "category": "tone_control",
        "priority": PRIORITY_SITUATIONAL,
    },
    "emphasis": {
        "tag": "[emphasis]",
        "description": "Stress a word/phrase",
        "example_context": "Highlighting key words",
        "category": "tone_control",
        "priority": PRIORITY_CORE,
    },
}

AUDIO_EFFECTS = {
    "laughing": {
        "tag": "[laughing]",
        "description": "Full laughter",
        "suggested_text": "Ha, ha, ha",
        "category": "audio_effect",
        "priority": PRIORITY_SITUATIONAL,
    },
    "chuckling": {
        "tag": "[chuckling]",
        "description": "Light laugh",
        "suggested_text": "Heh, heh",
        "category": "audio_effect",
        "priority": PRIORITY_CORE,
    },
    "sobbing": {
        "tag": "[sobbing]",
        "description": "Crying heavily",
        "suggested_text": "Optional text",
        "category": "audio_effect",
        "priority": PRIORITY_RARE,
    },
    "crying_loudly": {
        "tag": "[crying loudly]",
        "description": "Intense crying",
        "suggested_text": "Optional text",
        "category": "audio_effect",
        "priority": PRIORITY_RARE,
    },
    "sighing": {
        "tag": "[sighing]",
        "description": "Exhale of relief/frustration",
        "suggested_text": "sigh",
        "category": "audio_effect",
        "priority": PRIORITY_CORE,
    },
    "groaning": {
        "tag": "[groaning]",
        "description": "Sound of frustration",
        "suggested_text": "ugh",
        "category": "audio_effect",
        "priority": PRIORITY_RARE,
    },
    "panting": {
        "tag": "[panting]",
        "description": "Out of breath",
        "suggested_text": "huff, puff",
        "category": "audio_effect",
        "priority": PRIORITY_RARE,
    },
    "gasping": {
        "tag": "[gasping]",
        "description": "Sharp intake of breath",
        "suggested_text": "gasp",
        "category": "audio_effect",
        "priority": PRIORITY_RARE,
    },
    "yawning": {
        "tag": "[yawning]",
        "description": "Tired sound",
        "suggested_text": "yawn",
        "category": "audio_effect",
        "priority": PRIORITY_RARE,
    },
    "snoring": {
        "tag": "[snoring]",
        "description": "Sleep sound",
        "suggested_text": "zzz",
        "category": "audio_effect",
        "priority": PRIORITY_RARE,
    },
    "clear_throat": {
        "tag": "[clear throat]",
        "description": "Throat-clearing sound",
        "suggested_text": "ahem",
        "category": "audio_effect",
        "priority": PRIORITY_SITUATIONAL,
    },
}

SPECIAL_EFFECTS = {
    "audience_laughing": {
        "tag": "[audience laughing]",
        "description": "Crowd laughing sound",
        "example_context": UNAVAILABLE,
        "category": "special_effect",
        "priority": PRIORITY_RARE,
    },
    "background_laughter": {
        "tag": "[background laughter]",
        "description": "Ambient laughter",
        "example_context": UNAVAILABLE,
        "category": "special_effect",
        "priority": PRIORITY_RARE,
    },
    "crowd_laughing": {
        "tag": "[crowd laughing]",
        "description": "Large group laughing",
        "example_context": UNAVAILABLE,
        "category": "special_effect",
        "priority": PRIORITY_RARE,
    },
    "short_pause": {
        "tag": "[break]",
        "description": "Brief pause in speech",
        "example_context": UNAVAILABLE,
        "category": "special_effect",
        "priority": PRIORITY_CORE,
    },
    "long_pause": {
        "tag": "[long-break]",
        "description": "Extended pause in speech",
        "example_context": UNAVAILABLE,
        "category": "special_effect",
        "priority": PRIORITY_CORE,
    },
}

# ---------------------------------------------------------------------------
# Conversational fillers — ordinary spoken words, NOT Fish Audio cues.
# The official docs only cover bracketed cues, so every field here is ours and
# explicitly marked unofficial. They stay in the TTS text exactly as written.
# ---------------------------------------------------------------------------
CONVERSATIONAL_FILLERS = {
    "um": {
        "text": "um",
        "description": "Hesitation while gathering a thought",
        "example_context": "Um, the important thing to remember is that...",
        "category": "conversational_filler",
        "priority": PRIORITY_CORE,
        "official": False,
    },
    "umm": {
        "text": "umm",
        "description": "Slightly longer hesitation",
        "example_context": "Umm, give me a second to think.",
        "category": "conversational_filler",
        "priority": PRIORITY_CORE,
        "official": False,
    },
    "uh": {
        "text": "uh",
        "description": "Mild uncertainty or stalling",
        "example_context": "Uh, I'm not completely sure about that.",
        "category": "conversational_filler",
        "priority": PRIORITY_CORE,
        "official": False,
    },
    "uhh": {
        "text": "uhh",
        "description": "Longer form of mild uncertainty",
        "example_context": "Uhh, let me check that again.",
        "category": "conversational_filler",
        "priority": PRIORITY_CORE,
        "official": False,
    },
    "hmm": {
        "text": "hmm",
        "description": "Thinking sound before a considered answer",
        "example_context": "Hmm, let me think about that for a second.",
        "category": "conversational_filler",
        "priority": PRIORITY_CORE,
        "official": False,
    },
    "hmmm": {
        "text": "hmmm",
        "description": "Longer thinking sound",
        "example_context": "Hmmm, there are two ways to look at this.",
        "category": "conversational_filler",
        "priority": PRIORITY_CORE,
        "official": False,
    },
    "well": {
        "text": "well",
        "description": "Soft lead-in before an opinion or a shift",
        "example_context": "Well, I think there are two ways to approach this.",
        "category": "conversational_filler",
        "priority": PRIORITY_CORE,
        "official": False,
    },
    "let_me_think": {
        "text": "let me think",
        "description": "Explicit thinking beat",
        "example_context": "Hmm, let me think about that for a second.",
        "category": "conversational_filler",
        "priority": PRIORITY_CORE,
        "official": False,
    },
    "you_know": {
        "text": "you know",
        "description": "Casual emphasis in relaxed conversation",
        "example_context": "It's one of those days, you know?",
        "category": "conversational_filler",
        "priority": PRIORITY_SITUATIONAL,
        "official": False,
    },
    "actually": {
        "text": "actually",
        "description": "Correction or an added point",
        "example_context": "Actually, there's one more thing worth considering.",
        "category": "conversational_filler",
        "priority": PRIORITY_CORE,
        "official": False,
    },
    "i_mean": {
        "text": "I mean",
        "description": "Self-correction or clarification",
        "example_context": "I mean, the simpler option is probably better.",
        "category": "conversational_filler",
        "priority": PRIORITY_SITUATIONAL,
        "official": False,
    },
    "ugh": {
        "text": "ugh",
        "description": "Mild frustration as a plain word",
        "example_context": "Ugh, that sounds frustrating.",
        "category": "conversational_filler",
        "priority": PRIORITY_SITUATIONAL,
        "official": False,
    },
}

PAUSE_TAGS = ("[break]", "[long-break]")

# Merged, read-only view of every official cue.
ALL_CUES = {
    **EMOTIONS,
    **ADVANCED_EMOTIONS,
    **TONE_CONTROLS,
    **AUDIO_EFFECTS,
    **SPECIAL_EFFECTS,
}

OFFICIAL_TAGS = frozenset(entry["tag"] for entry in ALL_CUES.values())
# Longest first so prefix matching never eats a shorter tag's opener.
OFFICIAL_TAGS_BY_LENGTH = tuple(sorted(OFFICIAL_TAGS, key=len, reverse=True))


# ---------------------------------------------------------------------------
# Cue-safe text scanning (used by the chunkers and the TTS cleaner)
# ---------------------------------------------------------------------------
def mask_cues(text: str) -> tuple[list[bool], int]:
    """Per-character safety map for a streamed fragment.

    ``outside[i]`` is True when index ``i`` is not inside a ``[cue]`` (the
    brackets themselves count as inside, so no splitter can ever cut a cue in
    half). ``open_at`` is the index of an unterminated trailing ``[`` — -1 when
    every cue in the text is closed.

    Streaming needs this because an LLM emits "[exci" and "ted]" as separate
    tokens; chunking must not treat the fragment as speakable text.
    """
    outside = [True] * len(text)
    i = 0
    while i < len(text):
        if text[i] != "[":
            i += 1
            continue
        close = text.find("]", i + 1)
        if close == -1:
            for j in range(i, len(text)):
                outside[j] = False
            return outside, i
        for j in range(i, close + 1):
            outside[j] = False
        i = close + 1
    return outside, -1


def trim_incomplete_cue(text: str) -> str:
    """Drop an unterminated trailing cue ("... news! [bre" -> "... news!").

    A truncated LLM stream must never hand Fish Audio half a tag: the engine
    would read the fragment as literal text or reject the request.
    """
    if "[" not in text:
        return text
    _, open_at = mask_cues(text)
    if open_at < 0:
        return text
    return text[:open_at].rstrip()


def extract_cues(text: str) -> list[str]:
    """Every complete ``[cue]`` found in the text, in order (tests/debugging)."""
    out: list[str] = []
    i = 0
    while True:
        start = text.find("[", i)
        if start < 0:
            return out
        end = text.find("]", start + 1)
        if end < 0:
            return out
        out.append(text[start:end + 1])
        i = end + 1


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
def _entry_line(entry: dict) -> str:
    """`[tag] Official description (Official example context)` — verbatim text,
    context omitted when the official table does not provide one."""
    line = f"{entry['tag']} {entry['description']}"
    context = entry.get("example_context")
    if context and context != UNAVAILABLE:
        line += f" ({context})"
    return line


def _group_line(entries: dict, priority: int) -> str:
    picked = [e for e in entries.values() if e.get("priority") == priority]
    return "; ".join(_entry_line(e) for e in picked)


def _effect_line(entry: dict) -> str:
    line = f"{entry['tag']} {entry['description']}"
    suggested = entry.get("suggested_text", "")
    if suggested and suggested != "Optional text":
        line += f' (say: "{suggested}")'
    elif suggested == "Optional text":
        line += " (spoken text optional)"
    return line


@lru_cache(maxsize=1)
def build_emotion_control_prompt() -> str:
    """Compact, complete voice-direction brief built from the catalogue.

    Cached: the string is identical for every turn, so building it once keeps
    the hot path free of work and the system prompt stable (provider-side
    prompt caching stays effective).
    """
    emotions = {**EMOTIONS, **ADVANCED_EMOTIONS}
    lines = [
        "FISH AUDIO VOICE CUES — optional, context-driven, never shown on screen",
        "You speak through Fish Audio S2.1 Pro, which performs the bracketed cues below as real emotion, breath, pauses and human sounds. Cues are heard, never displayed. Plain, well-paced speech is the default: add a cue only where the tone of your words genuinely changes, and remember a cue colours the words that follow it.",
        "",
        "HOW TO PLACE CUES",
        "- A sentence's emotion cue goes at the START of that sentence: [empathetic] That sounds really frustrating.",
        "- Delivery and sound cues go right next to the words they affect: That is [emphasis] really important.",
        "- One primary emotion per sentence, at most 3 cues on a sentence, never two conflicting emotions.",
        "- Use only the cue names in this list, always in [square brackets], always in English even when you reply in another language.",
        "- Never split, translate or reword a cue, never nest brackets, never put a cue inside a word, never leave one unclosed.",
        "",
        "EMOTIONS — common, low-risk",
        _group_line(emotions, PRIORITY_CORE) + ".",
        "EMOTIONS — situational, only when the context clearly calls for it",
        _group_line(emotions, PRIORITY_SITUATIONAL) + ".",
        "EMOTIONS — intense, use very sparingly",
        _group_line(emotions, PRIORITY_RARE) + ".",
        "",
        "TONE & DELIVERY (place next to the affected words)",
        _group_line(TONE_CONTROLS, PRIORITY_CORE) + "; "
        + _group_line(TONE_CONTROLS, PRIORITY_SITUATIONAL) + "; "
        + _group_line(TONE_CONTROLS, PRIORITY_RARE) + ".",
        "Put [emphasis] directly before the word you want stressed: That is [emphasis] really important.",
        "",
        "VOICE EFFECTS (human sounds; add the suggested text after the cue where one is given)",
        "; ".join(_effect_line(e) for e in AUDIO_EFFECTS.values()) + ".",
        "A cue alone does not guarantee the sound — keep the short suggested word or breath after it, e.g. [laughing] Ha, ha, ha! or [sighing] sigh.",
        "",
        "SPECIAL EFFECTS (atmosphere — rarely right in a one-to-one conversation)",
        _group_line(SPECIAL_EFFECTS, PRIORITY_RARE) + ".",
        "",
        "PAUSES — the most natural cue you have",
        "[break] Brief pause in speech. [long-break] Extended pause in speech.",
        "Use [break] for a short human beat: between two related but distinct thoughts, before an important point, after a rhetorical question, during a natural hesitation, or when the emotion shifts.",
        "Use [long-break] when the silence itself carries meaning: between separate thoughts, before a realisation lands, or wherever a longer pause is natural in spoken conversation.",
        "Pause rules: at most one pause in a short reply, never two in a row, never after every sentence, never inside a word or a cue. Punctuation alone is weak when you actually want silence, so prefer an explicit marker: There is one important thing to remember. [break] The database must remain consistent.",
        "",
        "FILLERS — ordinary words, not cues",
        "These are plain spoken words, never bracketed: um, umm, uh, uhh, hmm, hmmm, well, let me think, you know, actually, I mean, ugh.",
        "Write them as normal text, keep them exactly as written, and they are spoken as normal text. They make you sound human when you are thinking through something hard, mildly unsure, starting a new thought, correcting yourself, acknowledging a messy situation, or just chatting casually.",
        "Filler rules: not every reply; never open every answer with umm or hmm; never chain them (umm, umm, umm); none in formal, urgent or highly sensitive answers; never fake uncertainty about something you know; use them more in casual chat than in structured explanations; one meaningful hesitation beats several; if a pause says it better, use [break] instead of filler words.",
        "[groaning] is a sound-effect cue; the word ugh is ordinary text. Do not confuse them.",
        "",
        "RESTRAINT",
        "- Most sentences need no cue at all, and a cue-free reply is often the best one. Never put a cue on every sentence, and never decorate a two-word reply.",
        "- Do not assume a question is [curious] or a confirmation is [happy]; choose from the meaning.",
        "- Change emotion only when the conversation genuinely changes; never act out a mood the words do not carry.",
        "- Clear, natural spoken English comes first; cues are seasoning, not content.",
        "",
        "STREAMING SAFETY",
        "Your words stream to the voice engine sentence by sentence, so every cue must be complete inside its sentence: never split one across sentences, never leave one open at the end of your reply, and never use . ! ? inside a cue.",
        "",
        "EXAMPLES (illustrations, not scripts)",
        "[relaxed] Yeah, that makes sense. Hmm, let me think about the best way to explain it.",
        "There is one important thing to remember. [break] The database must remain consistent.",
        "I understand why that matters. [long-break] Now, let's look at the trade-offs.",
        "Hmm, I think we should check the logs before changing anything.",
        "[empathetic] That sounds really frustrating. [break] Let's work through it together.",
        "[excited] That's fantastic news! You worked hard for this.",
        "[frustrated] Ugh, that error is annoying. Let's identify what's causing it.",
        "[uncertain] I'm not entirely sure yet. [break] Let me explain what we know so far.",
        "[laughing] Ha, ha, ha! That's a good one.",
    ]
    return "\n".join(lines)
