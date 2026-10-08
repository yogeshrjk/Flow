"""Correction policy tests (§4)."""
from app.coaching.correction import detect_mistakes, should_correct


def test_didnt_went_detected():
    ms = detect_mistakes("I didn't went there")
    assert any(m.kind == "didnt_went" for m in ms)


def test_passive_ignores_minor():
    ms = detect_mistakes("I went office yesterday")
    out = should_correct(ms, "passive")
    # minor alone -> may be empty or single; never more than 1
    assert len(out) <= 1


def test_balanced_corrects_past_tense():
    ms = detect_mistakes("Yesterday I go to the market")
    out = should_correct(ms, "balanced")
    assert len(out) == 1


def test_repeated_promoted():
    ms = detect_mistakes("I didn't went there", {"didnt_went": 3})
    assert ms[0].category == "E"
    assert len(should_correct(ms, "passive")) == 1


def test_active_allows_two():
    ms = detect_mistakes("I didn't went and I do a decision")
    assert len(should_correct(ms, "active")) <= 2
