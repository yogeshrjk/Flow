"""Tests for Personalised AI English Trainer:
- 10 learning areas and curriculum structure
- Personalised learning path and progression
- Multi-exercise mastery estimation (no single-turn assumption)
- Mistake tracking and practice_needed adaptation
- Trainer prompt building with grammar and acoustic speaking metrics
- Persistence across sessions
- Mode switching (free, reading, training) without state destruction
"""
import asyncio
import tempfile
import pytest
from app.coaching import training as tr_mod
from app.coaching import correction as corr
from app.coaching.memory import load_profile, save_profile
from app.conversation.engine import Engine, Session
from tests.mock_provider import MockProvider


REQUIRED_AREAS = [
    "basic_structure",
    "verb_tenses",
    "modal_verbs",
    "questions_answers",
    "articles_prepositions",
    "common_errors",
    "vocabulary_formation",
    "everyday_conversations",
    "workplace_communication",
    "fluency_connected_speech",
]


def test_training_curriculum_coverage():
    """All 10 required learning areas must be present with structured lessons."""
    area_ids = [a["id"] for a in tr_mod.LEARNING_AREAS]
    for req in REQUIRED_AREAS:
        assert req in area_ids, f"Missing required learning area: {req}"

    for area in tr_mod.LEARNING_AREAS:
        assert len(area["lessons"]) >= 2
        for les in area["lessons"]:
            assert les["id"]
            assert les["title"]
            assert les["level"] in ("A1", "A2", "B1", "B2", "C1", "C2")
            assert les["concept"]
            assert les["example"]
            assert les["exercise_prompt"]


def test_training_learning_path_progression():
    """Learning path generates ordered lessons with correct statuses."""
    profile = {"user_id": "test_user", "training": tr_mod.get_default_training_profile("test_user", "B1")}
    path = tr_mod.get_learning_path(profile)
    assert len(path) >= 20
    assert path[0]["status"] == "up_next"

    # Select next lesson should return the first unmastered lesson
    next_les = tr_mod.select_next_lesson(profile)
    assert next_les["id"] == "svo_order"

    # Mark first lesson needing practice -> it should be prioritized for review
    profile["training"]["practice_needed"] = ["past_simple_actions"]
    next_les = tr_mod.select_next_lesson(profile)
    assert next_les["id"] == "past_simple_actions"


def test_training_mastery_requires_multiple_attempts():
    """A single correct answer must NOT be treated as proof of mastery.
    Requires at least 2 consecutive successful exercises to mark as mastered."""
    prof = {"user_id": "u1", "level": "B1", "training": tr_mod.get_default_training_profile("u1", "B1")}

    # Attempt 1: passed
    tr_mod.update_training_progress(prof, "svo_order", passed=True)
    assert "svo_order" not in prof["training"]["mastered_topics"]
    assert prof["training"]["consecutive_passes"]["svo_order"] == 1

    # Attempt 2: failed -> resets consecutive passes and flags for practice
    tr_mod.update_training_progress(prof, "svo_order", passed=False)
    assert "svo_order" not in prof["training"]["mastered_topics"]
    assert prof["training"]["consecutive_passes"]["svo_order"] == 0
    assert "svo_order" in prof["training"]["practice_needed"]

    # Attempt 3: passed again
    tr_mod.update_training_progress(prof, "svo_order", passed=True)
    assert "svo_order" not in prof["training"]["mastered_topics"]
    assert prof["training"]["consecutive_passes"]["svo_order"] == 1

    # Attempt 4: passed consecutively -> now mastered and removed from practice_needed!
    tr_mod.update_training_progress(prof, "svo_order", passed=True)
    assert "svo_order" in prof["training"]["mastered_topics"]
    assert "svo_order" not in prof["training"]["practice_needed"]


def test_training_prompt_generation_with_metrics_and_mistakes():
    """Prompt must include concept, example, exercise, and acoustic speaking stats."""
    lesson = tr_mod.LESSON_MAP["past_simple_actions"]
    prof = {"training": tr_mod.get_default_training_profile()}

    # 1. Intro stage prompt
    intro_prompt = tr_mod.build_trainer_prompt(lesson, prof, stage="INTRO")
    assert "Past Simple" in intro_prompt
    assert lesson["example"] in intro_prompt
    assert lesson["exercise_prompt"] in intro_prompt

    # 2. Practice evaluation prompt with detected mistakes and speaking rate
    mistakes = [corr.Mistake("yesterday_go", "B", "yesterday i go", "Yesterday needs past tense: went")]
    speaking_stats = {"words_per_minute": 112.5, "mid_sentence_pauses_count": 2}
    eval_prompt = tr_mod.build_trainer_prompt(
        lesson, prof, stage="PRACTICE", user_response="yesterday i go to office",
        speaking_stats=speaking_stats, detected_mistakes=mistakes
    )
    assert "yesterday i go to office" in eval_prompt
    assert "112.5 WPM" in eval_prompt
    assert "2 mid-sentence hesitation" in eval_prompt
    assert "yesterday i go" in eval_prompt


def test_training_persistence_across_sessions():
    """Learning path and training progress persist in JSON profile across loads."""
    with tempfile.TemporaryDirectory() as td:
        p = load_profile(td, "trainer_user")
        assert "training" in p
        assert p["training"]["current_level"] == "B1"

        # Update and save
        tr_mod.update_training_progress(p, "svo_order", passed=True)
        tr_mod.update_training_progress(p, "svo_order", passed=True)
        save_profile(td, p)

        # Reload from disk
        p2 = load_profile(td, "trainer_user")
        assert "svo_order" in p2["training"]["mastered_topics"]
        assert len(p2["training"]["exercise_history"]) == 2


def test_mode_switching_preserves_conversation_and_state():
    """Switching modes maintains separate conversation history per module and preserves state."""
    s = Session(id="test_switch", mode="free")
    s.history.append({"role": "user", "content": "Hello in free!"})
    s.history.append({"role": "assistant", "content": "Hi there in free!"})
    assert len(s.history) == 2

    # Switch to training -> training history is distinct and isolated
    s.mode = "training"
    assert len(s.history) == 0
    s.history.append({"role": "user", "content": "Exercise in training"})
    assert len(s.history) == 1

    # Switch to reading -> reading history is distinct and isolated
    s.mode = "reading"
    assert len(s.history) == 0

    # Switch back to free -> free history is preserved
    s.mode = "free"
    assert len(s.history) == 2
    assert s.history[0]["content"] == "Hello in free!"

    # Switch back to training -> training history is preserved
    s.mode = "training"
    assert len(s.history) == 1
    assert s.history[0]["content"] == "Exercise in training"


def test_expanded_grammar_rules_detection():
    """Detect common errors across modal verbs, questions, prepositions, and SVO agreement."""
    # Modal error: could went
    m1 = corr.detect_mistakes("i could went there yesterday")
    assert any(m.kind == "could_went" for m in m1)

    # Modal error: should to
    m2 = corr.detect_mistakes("you should to practice daily")
    assert any(m.kind == "should_to" for m in m2)

    # Question inversion error: why you are
    m3 = corr.detect_mistakes("why you are laughing at me")
    assert any(m.kind == "why_you_are" for m in m3)

    # Preposition error: listen music
    m4 = corr.detect_mistakes("i love to listen music")
    assert any(m.kind == "listen_music" for m in m4)

    # Subject-verb agreement: he don't
    m5 = corr.detect_mistakes("he don't know the answer")
    assert any(m.kind == "he_dont" for m in m5)


def test_engine_handles_training_mode_turn():
    """Engine greeting and user turn in training mode emit training prompts."""
    eng = Engine()
    eng.llm_for = lambda sess: MockProvider()
    s = Session(id="t_trainer_eng", mode="training")

    # Greeting in training mode
    greeting = asyncio.run(eng.greeting(s))
    assert greeting and len(greeting.split()) >= 3

    # Turn in training mode
    class Sink:
        def __init__(self): self.msgs = []
        async def __call__(self, m): self.msgs.append(m)

    sink = Sink()
    asyncio.run(eng.handle_user_turn(s, "Every morning I drink black tea.", sink, 0.0))
    types = [m.get("type") for m in sink.msgs]
    assert "llm_done" in types
    assert "training_update" in types
    t_up = next(m for m in sink.msgs if m.get("type") == "training_update")
    assert "lesson" in t_up
    assert "learning_path" in t_up
