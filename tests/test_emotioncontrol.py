"""Fish Audio emotion control: catalogue fidelity, cue-safe chunking, TTS/UI split.

The tables below are an independent copy of the official S2 reference, so an
accidental edit to `app/conversation/emotioncontrol.py` fails loudly instead of
silently changing what the voice engine hears.

Source: https://docs.fish.audio/developer-guide/core-features/emotions
"""
import asyncio
import re

from app.conversation import emotioncontrol as ec
from app.conversation.engine import (
    FIRST_SENT_MIN,
    Engine,
    SentenceCoalescer,
    Session,
    chunk_sentences,
    clean_for_speech,
    first_chunk_split,
    strip_emotion_tags,
)
from tests.mock_provider import MockProvider


# --- official reference tables (verbatim) ----------------------------------
BASIC_EMOTIONS = [
    ("[happy]", "Cheerful, upbeat tone", "Good news, greetings"),
    ("[sad]", "Melancholic, downcast", "Sympathy, bad news"),
    ("[angry]", "Frustrated, aggressive", "Complaints, warnings"),
    ("[excited]", "Energetic, enthusiastic", "Announcements, celebrations"),
    ("[calm]", "Peaceful, relaxed", "Instructions, meditation"),
    ("[nervous]", "Anxious, uncertain", "Disclaimers, apologies"),
    ("[confident]", "Assertive, self-assured", "Presentations, sales"),
    ("[surprised]", "Shocked, amazed", "Reactions, discoveries"),
    ("[satisfied]", "Content, pleased", "Confirmations, reviews"),
    ("[delighted]", "Very pleased, joyful", "Celebrations, compliments"),
    ("[scared]", "Frightened, fearful", "Warnings, horror stories"),
    ("[worried]", "Concerned, troubled", "Concerns, questions"),
    ("[upset]", "Disturbed, distressed", "Complaints, problems"),
    ("[frustrated]", "Annoyed, exasperated", "Technical issues, delays"),
    ("[depressed]", "Very sad, hopeless", "Serious topics"),
    ("[empathetic]", "Understanding, caring", "Support, counseling"),
    ("[embarrassed]", "Ashamed, awkward", "Apologies, mistakes"),
    ("[disgusted]", "Repelled, revolted", "Negative reviews"),
    ("[moved]", "Emotionally touched", "Heartfelt moments"),
    ("[proud]", "Accomplished, satisfied", "Achievements, praise"),
    ("[relaxed]", "At ease, casual", "Casual conversation"),
    ("[grateful]", "Thankful, appreciative", "Thanks, appreciation"),
    ("[curious]", "Inquisitive, interested", "Questions, exploration"),
    ("[sarcastic]", "Ironic, mocking", "Humor, criticism"),
]

ADVANCED_EMOTIONS = [
    ("[disdainful]", "Contemptuous, scornful", "Criticism, rejection"),
    ("[unhappy]", "Discontent, dissatisfied", "Complaints, feedback"),
    ("[anxious]", "Very worried, uneasy", "Urgent matters"),
    ("[hysterical]", "Uncontrollably emotional", "Extreme reactions"),
    ("[indifferent]", "Uncaring, neutral", "Neutral responses"),
    ("[uncertain]", "Doubtful, unsure", "Speculation, questions"),
    ("[doubtful]", "Skeptical, questioning", "Disbelief, questioning"),
    ("[confused]", "Puzzled, perplexed", "Clarification requests"),
    ("[disappointed]", "Let down, dissatisfied", "Unmet expectations"),
    ("[regretful]", "Sorry, remorseful", "Apologies, mistakes"),
    ("[guilty]", "Culpable, responsible", "Confessions, apologies"),
    ("[ashamed]", "Deeply embarrassed", "Serious mistakes"),
    ("[jealous]", "Envious, resentful", "Comparisons"),
    ("[envious]", "Wanting what others have", "Admiration with desire"),
    ("[hopeful]", "Optimistic about future", "Future plans"),
    ("[optimistic]", "Positive outlook", "Encouragement"),
    ("[pessimistic]", "Negative outlook", "Warnings, doubts"),
    ("[nostalgic]", "Longing for the past", "Memories, stories"),
    ("[lonely]", "Isolated, alone", "Emotional content"),
    ("[bored]", "Uninterested, weary", "Disinterest"),
    ("[contemptuous]", "Showing contempt", "Strong criticism"),
    ("[sympathetic]", "Showing sympathy", "Condolences"),
    ("[compassionate]", "Showing deep care", "Support, help"),
    ("[determined]", "Resolved, decided", "Goals, commitments"),
    ("[resigned]", "Accepting defeat", "Giving up, acceptance"),
]

TONE_CONTROLS = [
    ("[in a hurry tone]", "Rushed, urgent", "Time-sensitive information"),
    ("[shouting]", "Loud, calling out", "Getting attention"),
    ("[screaming]", "Very loud, panicked", "Emergencies, fear"),
    ("[whispering]", "Very soft, secretive", "Secrets, quiet scenes"),
    ("[soft tone]", "Gentle, quiet", "Comfort, lullabies"),
    ("[emphasis]", "Stress a word/phrase", "Highlighting key words"),
]

AUDIO_EFFECTS = [
    ("[laughing]", "Full laughter", "Ha, ha, ha"),
    ("[chuckling]", "Light laugh", "Heh, heh"),
    ("[sobbing]", "Crying heavily", "Optional text"),
    ("[crying loudly]", "Intense crying", "Optional text"),
    ("[sighing]", "Exhale of relief/frustration", "sigh"),
    ("[groaning]", "Sound of frustration", "ugh"),
    ("[panting]", "Out of breath", "huff, puff"),
    ("[gasping]", "Sharp intake of breath", "gasp"),
    ("[yawning]", "Tired sound", "yawn"),
    ("[snoring]", "Sleep sound", "zzz"),
    ("[clear throat]", "Throat-clearing sound", "ahem"),
]

SPECIAL_EFFECTS = [
    ("[audience laughing]", "Crowd laughing sound"),
    ("[background laughter]", "Ambient laughter"),
    ("[crowd laughing]", "Large group laughing"),
    ("[break]", "Brief pause in speech"),
    ("[long-break]", "Extended pause in speech"),
]

FILLERS = ["um", "umm", "uh", "uhh", "hmm", "hmmm", "well",
           "let me think", "you know", "actually", "I mean", "ugh"]


# --- catalogue --------------------------------------------------------------
def test_every_basic_emotion_exists():
    assert len(ec.EMOTIONS) == 24
    assert {e["tag"] for e in ec.EMOTIONS.values()} == {t for t, _, _ in BASIC_EMOTIONS}


def test_every_advanced_emotion_exists():
    assert len(ec.ADVANCED_EMOTIONS) == 25
    assert {e["tag"] for e in ec.ADVANCED_EMOTIONS.values()} == {t for t, _, _ in ADVANCED_EMOTIONS}


def test_all_six_tone_controls_exist():
    assert len(ec.TONE_CONTROLS) == 6
    assert {e["tag"] for e in ec.TONE_CONTROLS.values()} == {t for t, _, _ in TONE_CONTROLS}


def test_all_eleven_audio_effects_exist():
    assert len(ec.AUDIO_EFFECTS) == 11
    assert {e["tag"] for e in ec.AUDIO_EFFECTS.values()} == {t for t, _, _ in AUDIO_EFFECTS}


def test_all_five_special_effects_exist():
    assert len(ec.SPECIAL_EFFECTS) == 5
    assert {e["tag"] for e in ec.SPECIAL_EFFECTS.values()} == {t for t, _ in SPECIAL_EFFECTS}
    assert ec.PAUSE_TAGS == ("[break]", "[long-break]")


def test_official_descriptions_and_contexts_are_verbatim():
    for group, official, has_context in (
        (ec.EMOTIONS, BASIC_EMOTIONS, True),
        (ec.ADVANCED_EMOTIONS, ADVANCED_EMOTIONS, True),
        (ec.TONE_CONTROLS, TONE_CONTROLS, True),
    ):
        by_tag = {e["tag"]: e for e in group.values()}
        for tag, description, context in official:
            entry = by_tag[tag]
            assert entry["description"] == description, tag
            if has_context:
                assert entry["example_context"] == context, tag


def test_audio_effect_suggested_text_is_separate_from_description():
    for tag, description, suggested in AUDIO_EFFECTS:
        entry = next(e for e in ec.AUDIO_EFFECTS.values() if e["tag"] == tag)
        assert entry["description"] == description
        assert entry["suggested_text"] == suggested
        assert suggested not in entry["description"]


def test_special_effect_descriptions_and_unavailable_contexts():
    by_tag = {e["tag"]: e for e in ec.SPECIAL_EFFECTS.values()}
    for tag, description in SPECIAL_EFFECTS:
        assert by_tag[tag]["description"] == description
        assert by_tag[tag]["example_context"] == ec.UNAVAILABLE


def test_every_entry_has_the_consistent_field_set():
    for key, entry in ec.ALL_CUES.items():
        assert entry["category"], key
        assert entry["priority"] in (1, 2, 3), key
        assert entry["tag"].startswith("[") and entry["tag"].endswith("]"), key


def test_spacing_inside_official_tags_is_preserved():
    assert ec.TONE_CONTROLS["hurried"]["tag"] == "[in a hurry tone]"
    assert ec.TONE_CONTROLS["soft"]["tag"] == "[soft tone]"
    assert ec.AUDIO_EFFECTS["clear_throat"]["tag"] == "[clear throat]"
    assert ec.SPECIAL_EFFECTS["short_pause"]["tag"] == "[break]"
    assert ec.SPECIAL_EFFECTS["long_pause"]["tag"] == "[long-break]"
    assert ec.OFFICIAL_TAGS == frozenset(t for t, *_ in BASIC_EMOTIONS + ADVANCED_EMOTIONS
                                          + TONE_CONTROLS + AUDIO_EFFECTS + SPECIAL_EFFECTS)


def test_fillers_are_marked_unofficial():
    assert set(ec.CONVERSATIONAL_FILLERS) >= {"um", "umm", "uh", "hmm", "ugh"}
    for entry in ec.CONVERSATIONAL_FILLERS.values():
        assert entry["official"] is False
        assert entry["text"] in FILLERS
        assert entry["category"] == "conversational_filler"


# --- prompt generation ------------------------------------------------------
def test_prompt_includes_every_category_and_official_text():
    prompt = ec.build_emotion_control_prompt()
    for entry in ec.ALL_CUES.values():
        assert entry["tag"] in prompt, entry["tag"]
        assert entry["description"] in prompt, entry["description"]
    for entry in list(ec.EMOTIONS.values()) + list(ec.ADVANCED_EMOTIONS.values()):
        assert entry["example_context"] in prompt
    for entry in ec.TONE_CONTROLS.values():
        assert entry["example_context"] in prompt
    for entry in ec.AUDIO_EFFECTS.values():
        if entry["suggested_text"] != "Optional text":
            assert entry["suggested_text"] in prompt


def test_prompt_covers_every_required_section():
    prompt = ec.build_emotion_control_prompt().lower()
    for needle in ("emotions", "tone & delivery", "voice effects", "special effects",
                   "pauses", "fillers", "restraint", "streaming safety", "examples",
                   "how to place cues"):
        assert needle in prompt


def test_prompt_teaches_pauses_and_fillers():
    prompt = ec.build_emotion_control_prompt()
    assert "[break]" in prompt and "[long-break]" in prompt
    assert "brief pause" in prompt.lower() and "extended pause" in prompt.lower()
    assert "rhetorical question" in prompt
    assert "hesitation" in prompt
    # fillers are ordinary text, never cues
    assert "ordinary words, not cues" in prompt
    assert "never bracketed" in prompt
    for filler in ("um", "umm", "uh", "uhh", "hmm", "hmmm", "well",
                   "let me think", "you know", "actually", "I mean", "ugh"):
        assert filler in prompt
    assert "[groaning]" in prompt and "the word ugh is ordinary text" in prompt.lower()


def test_prompt_examples_match_the_catalogue():
    prompt = ec.build_emotion_control_prompt()
    for example in (
        "[relaxed] Yeah, that makes sense. Hmm, let me think about the best way to explain it.",
        "There is one important thing to remember. [break] The database must remain consistent.",
        "I understand why that matters. [long-break] Now, let's look at the trade-offs.",
        "Hmm, I think we should check the logs before changing anything.",
        "[empathetic] That sounds really frustrating. [break] Let's work through it together.",
        "[excited] That's fantastic news! You worked hard for this.",
        "[frustrated] Ugh, that error is annoying. Let's identify what's causing it.",
        "[uncertain] I'm not entirely sure yet. [break] Let me explain what we know so far.",
        "[laughing] Ha, ha, ha! That's a good one.",
    ):
        assert example in prompt
        for cue in ec.extract_cues(example):
            assert cue in ec.OFFICIAL_TAGS


def test_prompt_is_cached_and_stable():
    assert ec.build_emotion_control_prompt() is ec.build_emotion_control_prompt()


def test_prompt_is_merged_into_the_system_prompt():
    from app.conversation.prompts import build_system

    system = build_system(mode="free", level="B1", level_setting="auto",
                          correction="balanced", persona_name="Flow")
    assert ec.build_emotion_control_prompt() in system
    # the legacy "at most ONCE per turn" rule must not contradict the catalogue
    assert "at most ONCE per turn" not in system
    # no duplicated voice-direction blocks
    assert system.count("FISH AUDIO VOICE CUES") == 1


def test_native_voice_tier_gets_no_fish_cues():
    from app.conversation.prompts import NATIVE_VOICE_GUIDANCE, build_system

    native = build_system("free", "B1", "auto", "balanced", voice_cues=False)
    assert "[break]" not in native
    assert "[empathetic]" not in native
    assert NATIVE_VOICE_GUIDANCE in native


def test_every_mode_still_builds_a_valid_system_prompt():
    from app.conversation.prompts import MODE_ADDENDA, build_system

    for mode in MODE_ADDENDA:
        system = build_system(mode=mode, level="B1", level_setting="auto",
                              correction="balanced", scenario="job_interview",
                              challenge="talk about your day", persona_name="Flow")
        assert system.startswith("You're Flow")
        assert "FISH AUDIO VOICE CUES" in system


# --- pauses -----------------------------------------------------------------
def test_pause_tags_survive_tts_cleaning():
    assert "[break]" in clean_for_speech("One thing matters. [break] The database stays consistent.")
    assert "[long-break]" in clean_for_speech("It matters. [long-break] Now the trade-offs.")
    assert clean_for_speech("[break] Hi [long-break] there") == "[break] Hi [long-break] there"


def test_pause_tags_are_stripped_from_display_only():
    assert strip_emotion_tags("[break] Hi [long-break] there") == "Hi there"
    assert "[break]" not in strip_emotion_tags("[sad] It broke. [long-break] We fix it.")
    # a truncated stream must not leave half a cue on screen either
    assert strip_emotion_tags("Good news! [bre") == "Good news!"


def test_pause_tags_are_not_split_across_chunks():
    sents, rest = chunk_sentences("[break] Hello there. [long-break] How are you?")
    assert sents == ["[break] Hello there.", "[long-break] How are you?"]
    assert rest == ""


def test_ordinary_punctuation_still_chunks():
    sents, rest = chunk_sentences("Hello there. How are you? Good")
    assert sents == ["Hello there.", "How are you?"]
    assert rest == "Good"


# --- fillers ----------------------------------------------------------------
def test_fillers_survive_tts_cleaning():
    assert clean_for_speech("Umm, let me think about that.") == "Umm, let me think about that."
    assert clean_for_speech("Hmm. Ugh. Uh. Well.") == "Hmm. Ugh. Uh. Well."
    assert clean_for_speech("Ugh, that sounds frustrating.") == "Ugh, that sounds frustrating."
    assert clean_for_speech("Hmm, [break] let me think.") == "Hmm, [break] let me think."


def test_fillers_are_not_tags():
    assert "ugh" not in ec.OFFICIAL_TAGS
    assert "[ugh]" not in ec.OFFICIAL_TAGS
    assert "[groaning]" in ec.OFFICIAL_TAGS
    # display text keeps fillers, strips cues
    assert strip_emotion_tags("[frustrated] Ugh, that is annoying.") == "Ugh, that is annoying."


# --- streaming --------------------------------------------------------------
def _stream(tokens):
    """Mirror `handle_user_turn`'s emit path and collect the TTS chunks."""
    buf = ""
    coalescer = SentenceCoalescer()
    emitted = []
    first_chunk_done = False
    for tok in tokens:
        buf += tok
        if not first_chunk_done:
            candidate = f"{coalescer.held} {buf}".strip() if coalescer.held else buf
            split = first_chunk_split(candidate)
            if split:
                head, rest = split
                coalescer.held = ""
                buf = rest
                spoken = clean_for_speech(head)
                if spoken:
                    emitted.append(spoken)
                    first_chunk_done = True
        sentences, buf = chunk_sentences(buf)
        for sent in sentences:
            if not first_chunk_done:
                lead = f"{coalescer.held} {sent}".strip() if coalescer.held else sent
                if len(lead) >= FIRST_SENT_MIN:
                    coalescer.held = ""
                    spoken = clean_for_speech(lead)
                    if spoken:
                        emitted.append(spoken)
                        first_chunk_done = True
                    continue
                else:
                    coalescer.held = lead
                    continue
            for chunk in coalescer.feed(sent):
                spoken = clean_for_speech(chunk)
                if spoken:
                    emitted.append(spoken)
                    first_chunk_done = True
    for chunk in coalescer.flush(buf):
        spoken = clean_for_speech(chunk)
        if spoken:
            emitted.append(spoken)
    return emitted


def _balanced(chunk):
    return chunk.count("[") == chunk.count("]")


def test_complete_cue_survives_streaming_whole():
    text = "[empathetic] That sounds really frustrating. [break] Let's work through it together."
    chunks = _stream(re.findall(r"\S+\s*", text))
    assert all(_balanced(c) for c in chunks)
    joined = " ".join(chunks)
    assert "[empathetic]" in joined and "[break]" in joined


def test_cue_split_across_token_boundaries_is_buffered():
    tokens = ["[empa", "thetic] That", " sounds really frustrating.", " [bre", "ak] Let's",
              " work through it together."]
    chunks = _stream(tokens)
    assert all(_balanced(c) for c in chunks)
    joined = " ".join(chunks)
    assert "[empathetic]" in joined
    assert "[break]" in joined
    # nothing half-formed leaked out: every chunk's cues are complete
    for chunk in chunks:
        for cue in ec.extract_cues(chunk):
            assert cue in ec.OFFICIAL_TAGS


def test_incomplete_trailing_cue_is_never_emitted():
    tokens = ["[excited] That's fantastic news! You worked hard", " for this. [lau"]
    chunks = _stream(tokens)
    joined = " ".join(chunks)
    assert all(_balanced(c) for c in chunks)
    assert "[lau" not in joined
    assert "You worked hard for this." in joined


def test_chunker_never_splits_a_cue_containing_a_comma():
    cue = "[speak in an extremely urgent, hushed tone]"
    head, rest = first_chunk_split(f"{cue} Listen to me. Then we run.")
    assert head == f"{cue} Listen to me."
    assert rest == "Then we run."


def test_coalescer_still_merges_short_leads_with_cues():
    c = SentenceCoalescer()
    assert c.feed("[sad] Okay.") == []
    out = c.feed("I understand why that matters to you.")
    assert out == ["[sad] Okay. I understand why that matters to you."]


def test_clean_for_speech_regressions():
    assert clean_for_speech("**bold** and *it*") == "bold and it"
    assert clean_for_speech("# Heading\n1. item") == "Heading item"
    assert clean_for_speech("Hi 👋 there") == "Hi there"
    assert clean_for_speech("[break]") == "[break]"


# --- engine wiring ----------------------------------------------------------
class _CueProvider(MockProvider):
    def __init__(self, text):
        super().__init__()
        self._text = text

    async def stream(self, messages, max_tokens=180):
        for tok in re.findall(r"\S+\s*", self._text):
            yield tok


class _Sink:
    def __init__(self):
        self.msgs = []

    async def __call__(self, msg):
        self.msgs.append(msg)


def test_turn_keeps_cues_for_tts_and_strips_them_from_display():
    eng = Engine()
    reply = ("[empathetic] That sounds really frustrating. [break] "
             "Let's work through it together.")
    eng.llm_for = lambda s: _CueProvider(reply)
    session = Session(id="test-emotion-turn", mode="free")
    sink = _Sink()

    asyncio.run(eng.handle_user_turn(session, "today was a long day at work", sink, 0.0))

    tts = [m["text"] for m in sink.msgs if m.get("type") == "tts_sentence"]
    assert tts, "no TTS chunks emitted"
    spoken = " ".join(tts)
    assert "[empathetic]" in spoken
    assert "[break]" in spoken
    assert all(_balanced(c) for c in tts)

    done = [m for m in sink.msgs if m.get("type") == "llm_done"][0]
    assert "[empathetic]" not in done["display_text"]
    assert "[break]" not in done["display_text"]
    assert "frustrating" in done["display_text"]
    # the stored turn keeps the raw cues for the next LLM call
    assert "[empathetic]" in session.history[-1]["content"]


def test_streaming_event_contract_is_unchanged():
    eng = Engine()
    eng.llm_for = lambda s: _CueProvider("[calm] Sure, let's take it slowly.")
    session = Session(id="test-emotion-events", mode="free")
    sink = _Sink()

    asyncio.run(eng.handle_user_turn(session, "today was a long day at work", sink, 0.0))

    kinds = [m.get("type") for m in sink.msgs]
    assert "status" in kinds and "llm_token" in kinds
    assert "tts_sentence" in kinds and "llm_done" in kinds
    for msg in sink.msgs:
        if msg.get("type") == "tts_sentence":
            assert set(msg) <= {"type", "text", "turn", "req_id", "first", "lat"}
            assert isinstance(msg["text"], str) and msg["text"]
        if msg.get("type") == "llm_done":
            assert "display_text" in msg and "text" in msg
