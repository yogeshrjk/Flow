"""Comprehensive tests for natural conversation behavior:
- Natural conversation & tone: no canned praise, no forced interrogation
- Context awareness & user intent: 'Can we start?', 'I don't know', 'Can we talk about the practice?'
- Casual chat intent: 'I want to do some casual chat only' switches mode to free without grading
- Audio check intent: 'hello can you hear me', 'say something'
- Adaptive English tutoring: subtle embedding in free mode without unsolicited drills
- Speech recognition handling: repeated words, leading fillers, trailing cutoffs
- Conversation continuity: preserving history and context
"""
import asyncio
import pytest
from app.conversation.engine import Engine, Session
from app.conversation.intent import (
    UserIntent,
    clean_speech_artifacts,
    classify_user_intent,
    has_trailing_hesitation,
    is_audio_check,
    is_casual_chat_request,
    is_meta_ready,
    is_meta_help,
)
from app.conversation.prompts import build_system
from app.coaching import correction as corr
from app.coaching import training as tr_mod
from tests.mock_provider import MockProvider


class CaptureSink:
    def __init__(self):
        self.msgs = []

    async def __call__(self, msg: dict):
        self.msgs.append(msg)


def test_speech_artifact_cleaning_repeated_words():
    """Repeated stutter words from STT must be deduplicated."""
    assert clean_speech_artifacts("hello hello can you hear me") == "hello can you hear me"
    assert clean_speech_artifacts("I I want to practice") == "I want to practice"
    assert clean_speech_artifacts("the the book is good") == "the book is good"
    assert clean_speech_artifacts("uh hello there") == "hello there"
    assert clean_speech_artifacts("um I think so") == "I think so"


def test_contextual_homophone_alignment():
    """Homophones are corrected relative to conversation context (e.g. Serial -> cereal when context is morning food)."""
    from app.conversation.intent import align_contextual_homophones

    ai_context = "Okay, think of another thing you eat or drink in the morning and make a simple sentence. Try 'I sometimes eat cereal.' Give it a go."
    assert align_contextual_homophones("What is Serial?", ai_context) == "What is cereal?"
    assert align_contextual_homophones("I like serial with milk", "What do you have for breakfast?") == "I like cereal with milk"
    assert align_contextual_homophones("I eight bread", "Did you eat breakfast?") == "I ate bread"
    assert clean_speech_artifacts("What is Serial?", context=ai_context) == "What is cereal?"


def test_speech_trailing_hesitation_detection():
    """Detect when a user trails off mid-sentence with filler phrases."""
    assert has_trailing_hesitation("no I don't have any hobby I am just") is True
    assert has_trailing_hesitation("I wanted to say you know") is True
    assert has_trailing_hesitation("I enjoy playing cricket on weekends") is False


def test_intent_classification():
    """User intents are correctly categorized."""
    # Audio check
    assert classify_user_intent("hello can you hear me") == UserIntent.AUDIO_CHECK
    assert classify_user_intent("can you hear me?") == UserIntent.AUDIO_CHECK
    assert classify_user_intent("say something") == UserIntent.AUDIO_CHECK
    assert is_audio_check("hey say something") is True

    # Casual chat request
    assert classify_user_intent("I want to do some casual chat only", "training") == UserIntent.CASUAL_CHAT_REQUEST
    assert classify_user_intent("can we just chat?", "training") == UserIntent.CASUAL_CHAT_REQUEST
    assert classify_user_intent("let's just talk", "training") == UserIntent.CASUAL_CHAT_REQUEST
    assert is_casual_chat_request("switch to casual chat") is True

    # Meta ready in training mode
    assert classify_user_intent("Can we start?", "training") == UserIntent.META_READY
    assert classify_user_intent("I'm ready", "training") == UserIntent.META_READY
    assert is_meta_ready("ready to start") is True

    # Meta help in training mode
    assert classify_user_intent("I don't know", "training") == UserIntent.META_CONFUSED_OR_HELP
    assert classify_user_intent("what do you mean?", "training") == UserIntent.META_CONFUSED_OR_HELP
    assert classify_user_intent("can we talk about the practice?", "training") == UserIntent.META_CONFUSED_OR_HELP
    assert is_meta_help("give me an example") is True

    # Actual exercise attempt
    assert classify_user_intent("I drink coffee every morning", "training") == UserIntent.EXERCISE_ATTEMPT


def test_casual_chat_intent_switches_mode_from_training_without_grading():
    """When user requests casual chat during training, engine switches mode to free,
    does NOT evaluate as an exercise attempt, and does NOT advance lesson passes."""
    eng = Engine()
    eng.llm_for = lambda sess: MockProvider()
    s = Session(id="test_casual_switch", mode="training")
    sink = CaptureSink()

    asyncio.run(eng.handle_user_turn(s, "I want to do some casual chat only", sink, 0.0))

    # Engine must have switched session mode to free
    assert s.mode == "free"
    # Mode switch notification sent to client
    mode_msgs = [m for m in sink.msgs if m.get("type") == "mode"]
    assert len(mode_msgs) >= 1
    assert mode_msgs[0].get("mode") == "free"


def test_meta_ready_in_training_does_not_advance_progress():
    """Saying 'Can we start?' in training mode must NOT be graded as an exercise attempt
    or advance mastery progress."""
    eng = Engine()
    captured_messages = []

    class CapturingProvider(MockProvider):
        async def stream(self, messages, max_tokens=180):
            captured_messages.extend(messages)
            yield "Let's begin! We are practicing Subject-Verb-Object order."

    eng.llm_for = lambda sess: CapturingProvider()
    s = Session(id="test_meta_ready", user_id="test_user_ready_fresh", mode="training")
    sink = CaptureSink()

    from app.coaching.memory import load_profile
    from app.config import settings
    prof_before = load_profile(settings.data_dir, s.user_id)
    passes_before = prof_before.get("training", {}).get("consecutive_passes", {}).copy()

    asyncio.run(eng.handle_user_turn(s, "Can we start?", sink, 0.0))

    # Profile consecutive passes must NOT be incremented or changed
    prof_after = load_profile(settings.data_dir, s.user_id)
    passes_after = prof_after.get("training", {}).get("consecutive_passes", {})
    assert passes_after == passes_before

    # System prompt / nudge must instruct INTRO, not PRACTICE evaluation
    sys_content = "\n".join(m.get("content", "") for m in captured_messages if m.get("role") == "system")
    assert "Introduce this lesson" in sys_content or "INTRO" in sys_content


def test_meta_help_in_training_does_not_fail_exercise():
    """Saying 'I don't know' or 'What do you mean?' in training mode provides supportive
    help and does NOT register as a failed exercise or penalize progress."""
    eng = Engine()
    captured_messages = []

    class CapturingProvider(MockProvider):
        async def stream(self, messages, max_tokens=180):
            captured_messages.extend(messages)
            yield "No problem at all! Here is a simple example to help you out."

    eng.llm_for = lambda sess: CapturingProvider()
    s = Session(id="test_meta_help", user_id="test_user_help_fresh", mode="training")
    sink = CaptureSink()

    from app.coaching.memory import load_profile
    from app.config import settings
    prof_before = load_profile(settings.data_dir, s.user_id)
    passes_before = prof_before.get("training", {}).get("consecutive_passes", {}).copy()

    asyncio.run(eng.handle_user_turn(s, "I don't know, what should I say?", sink, 0.0))

    prof_after = load_profile(settings.data_dir, s.user_id)
    passes_after = prof_after.get("training", {}).get("consecutive_passes", {})
    # Must not reset or corrupt consecutive passes
    assert passes_after == passes_before

    # Must provide help guidance
    sys_content = "\n".join(m.get("content", "") for m in captured_messages if m.get("role") == "system")
    assert "uncertainty or asked for help" in sys_content or "HELP" in sys_content


def test_audio_check_intent_handled_without_grammar_grading():
    """Saying 'hello can you hear me' or 'say something' gets direct audio confirmation
    without treating it as a conversational grammar test."""
    eng = Engine()
    captured_messages = []

    class CapturingProvider(MockProvider):
        async def stream(self, messages, max_tokens=180):
            captured_messages.extend(messages)
            yield "Yes, I hear you clearly! What's on your mind?"

    eng.llm_for = lambda sess: CapturingProvider()
    s = Session(id="test_audio_check", mode="free")
    sink = CaptureSink()

    asyncio.run(eng.handle_user_turn(s, "hello can you hear me", sink, 0.0))

    sys_content = "\n".join(m.get("content", "") for m in captured_messages if m.get("role") == "system")
    assert "checking audio connectivity" in sys_content


def test_adaptive_correction_instruction_in_free_mode():
    """In free mode, grammar corrections are subtly modeled rather than given as lectures."""
    mistakes = [corr.Mistake("yesterday_go", "B", "yesterday i go", "Yesterday needs past tense: went")]

    # Free mode correction instruction
    ci_free = corr.correction_instruction(mistakes, mode="free")
    assert "seamlessly model" in ci_free
    assert "without lecturing" in ci_free
    assert "acknowledge→correct→why" not in ci_free

    # When no mistakes in free mode
    ci_none = corr.correction_instruction([], mode="free")
    assert "No grammar correction needed" in ci_none
    assert "with a follow-up question" not in ci_none  # No forced question!


def test_prompts_ban_canned_praise_and_forced_interrogation():
    """System prompts explicitly instruct against repetitive praise and forced interrogation."""
    sys_prompt = build_system(mode="free", level="B1", level_setting="auto",
                              correction="balanced", persona_name="Flow")

    # Anti-canned praise instruction
    assert "NEVER start with repetitive canned praise" in sys_prompt
    assert "Great, let's dive right in!" in sys_prompt
    assert "Sounds perfect!" in sys_prompt
    assert "NEVER parrot or repeat back the user's sentence" in sys_prompt

    # Anti-forced interrogation instruction
    assert "You do NOT have to ask a question on every turn" in sys_prompt
    assert "End with AT MOST one relevant question" in sys_prompt

    # Speech disfluency guidance
    assert "Handle speech recognition artifacts" in sys_prompt


def test_conversation_continuity_and_history():
    """History preserves natural conversation turns without stutter duplicates."""
    eng = Engine()
    eng.llm_for = lambda sess: MockProvider()
    s = Session(id="test_continuity", mode="free")
    sink = CaptureSink()

    asyncio.run(eng.handle_user_turn(s, "hello hello how are you today", sink, 0.0))

    # Stored turn must have cleaned transcript without stutter duplicates
    assert s.history[-2]["content"] == "hello how are you today"
    assert len(s.turns) == 2


def test_grammar_replacement_fixes():
    """Verify that detect_mistakes populates replacement fixes and apply_fixes_to_sentence corrects sentences."""
    mistakes1 = corr.detect_mistakes("I didn't went yesterday")
    assert len(mistakes1) >= 1
    assert mistakes1[0].pattern.lower() == "didn't went"
    assert mistakes1[0].replacement.lower() == "didn't go"
    fixed1 = corr.apply_fixes_to_sentence("I didn't went yesterday", mistakes1)
    assert fixed1 == "I didn't go yesterday"

    mistakes2 = corr.detect_mistakes("yesterday i go to market and he don't listen music")
    fixed2 = corr.apply_fixes_to_sentence("yesterday i go to market and he don't listen music", mistakes2)
    assert "yesterday I went" in fixed2
    assert "doesn't" in fixed2
    assert "listen to music" in fixed2


def test_llm_done_emits_mistakes_and_fixed_text():
    """Engine emits mistakes list and fixed_text in llm_done payload."""
    eng = Engine()
    eng.llm_for = lambda sess: MockProvider()
    s = Session(id="test_fixes_payload", mode="free")
    sink = CaptureSink()

    asyncio.run(eng.handle_user_turn(s, "I didn't went to the office yesterday", sink, 0.0))
    dones = [m for m in sink.msgs if m.get("type") == "llm_done"]
    assert len(dones) == 1
    done_msg = dones[0]
    assert "mistakes" in done_msg
    assert "fixed_text" in done_msg
    assert "didn't go" in done_msg["fixed_text"]
    assert any(m.get("original") == "didn't went" for m in done_msg["mistakes"])


def test_clean_speech_artifacts_homophone_slips():
    """Verify acoustic confusion of unvoiced stop /t/ with /d/ ('add' vs 'ate') is corrected contextually."""
    assert clean_speech_artifacts("I add breakfast this morning") == "I ate breakfast this morning"
    assert clean_speech_artifacts("we add lunch together") == "we ate lunch together"
    assert clean_speech_artifacts("they add dinner at 8") == "they ate dinner at 8"
    assert clean_speech_artifacts("I add an apple") == "I ate an apple"
    assert clean_speech_artifacts("I already add") == "I already ate"
    # Ordinary uses of 'add' should remain untouched
    assert clean_speech_artifacts("please add this item") == "please add this item"
    assert clean_speech_artifacts("add two numbers together") == "add two numbers together"


def test_detect_language_switch_request():
    from app.conversation.intent import detect_language_switch_request

    assert detect_language_switch_request("talk in hindi") == "hindi"
    assert detect_language_switch_request("talk in german") == "german"
    assert detect_language_switch_request("can we speak in spanish?") == "spanish"
    assert detect_language_switch_request("can you speak in French please?") == "french"
    assert detect_language_switch_request("switch to russian") == "russian"
    assert detect_language_switch_request("hindi me bolo") == "hindi"
    assert detect_language_switch_request("sprich bitte deutsch") == "german"
    assert detect_language_switch_request("let's talk in english") == "english"
    assert detect_language_switch_request("what is the weather today?") is None


def test_detect_language_from_content():
    from app.conversation.intent import detect_language_from_content

    assert detect_language_from_content("नमस्ते, आप कैसे हैं?") == "hindi"
    assert detect_language_from_content("Guten Tag, wie geht es dir heute?") == "german"
    assert detect_language_from_content("Bonjour, comment allez-vous aujourd'hui?") == "french"
    assert detect_language_from_content("Hola, ¿cómo estás hoy? Muy bien") == "spanish"
    assert detect_language_from_content("Привет! Как дела?") == "russian"
    assert detect_language_from_content("مرحبا كيف حالك اليوم") == "arabic"
    assert detect_language_from_content("こんにちは、お元気ですか？") == "japanese"
    assert detect_language_from_content("Hello, how are you doing today?") is None


def test_engine_handles_language_switch_turn_hindi():
    eng = Engine()
    eng.llm_for = lambda sess: MockProvider()
    s = Session(id="test_switch_hindi", mode="training")
    sink = CaptureSink()

    asyncio.run(eng.handle_user_turn(s, "talk in hindi", sink, 0.0))
    # Session language must update to hindi
    assert s.language == "hindi"
    assert s.last_spoken_lang == "hindi"
    # Training mode should have shifted to free
    assert s.mode == "free"
    # A language update must have been emitted to the client
    lang_msgs = [m for m in sink.msgs if m.get("type") == "language"]
    assert len(lang_msgs) >= 1
    assert lang_msgs[0]["value"] == "hindi"


def test_engine_handles_language_switch_turn_german():
    eng = Engine()
    eng.llm_for = lambda sess: MockProvider()
    s = Session(id="test_switch_german", mode="free")
    sink = CaptureSink()

    asyncio.run(eng.handle_user_turn(s, "Can we talk in German?", sink, 0.0))
    assert s.language == "german"
    assert s.last_spoken_lang == "german"
    lang_msgs = [m for m in sink.msgs if m.get("type") == "language"]
    assert len(lang_msgs) >= 1
    assert lang_msgs[0]["value"] == "german"
