"""Prompt guardrails: the model must never narrate its reasoning aloud and
must keep replies short (the live center line shows 1-2 sentences max)."""
from app.conversation.prompts import build_system


def _system(**kw):
    args = dict(mode="free", level="B1", level_setting="auto",
                correction="balanced", persona_name="Flow", persona_gender="male")
    args.update(kw)
    return build_system(**args)


def test_no_inner_monologue_rule():
    s = _system()
    assert "NEVER narrate your thinking" in s
    assert "The user is asking" in s  # the exact leak pattern is named


def test_hard_length_cap():
    s = _system()
    assert "~40 words" in s
    assert "NEVER exceed 2 short sentences" in s


def test_practice_mode_enforces_selected_scenario():
    s = _system(mode="practice", scenario="job_interview")
    assert "Job Interview" in s
    assert "hiring manager / job interviewer" in s
    assert "WARMUP & COMFORT FIRST" in s
    assert "SPOKEN ENGLISH COACHING" in s
    assert "STRICT SCENARIO FOCUS" in s
    assert "Job Interview" in s

    s_rest = _system(mode="practice", scenario="restaurant")
    assert "Restaurant" in s_rest
    assert "restaurant waiter / server" in s_rest
    assert "Restaurant" in s_rest
