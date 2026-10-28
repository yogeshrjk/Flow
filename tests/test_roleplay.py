"""Tests for Roleplay Mode: scenario detection, topic selection, and 2-person dialogue."""
import asyncio
import pytest
from app.coaching.roleplay import (
    SCENARIOS,
    detect_roleplay_scenario,
    is_roleplay_change_request,
)
from app.conversation.engine import Engine, Session


def test_detect_market_friend_scenario():
    """Verify detection of the user's explicit example: meeting an old friend at the market."""
    text1 = "I met a old friend on the market and AI will act as my friend"
    detected1 = detect_roleplay_scenario(text1)
    assert detected1 is not None
    assert detected1["key"] == "market_friend"
    assert detected1["title"] == "Old Friend at the Market"
    assert "friend" in detected1["role"].lower()
    assert "market" in detected1["desc"].lower()

    text2 = "let's do meeting an old friend at the market"
    detected2 = detect_roleplay_scenario(text2)
    assert detected2 is not None
    assert detected2["key"] == "market_friend"

    text3 = "option 1"
    detected3 = detect_roleplay_scenario(text3)
    assert detected3 is not None
    assert detected3["key"] == "market_friend"


def test_detect_cafe_and_interview_scenarios():
    """Verify detection of cafe and job interview scenarios and numbered options."""
    detected_cafe = detect_roleplay_scenario("can we practice ordering at a cafe?")
    assert detected_cafe is not None
    assert detected_cafe["key"] == "cafe"

    detected_interview = detect_roleplay_scenario("let's do a job interview")
    assert detected_interview is not None
    assert detected_interview["key"] == "job_interview"

    detected_opt2 = detect_roleplay_scenario("number two")
    assert detected_opt2 is not None
    assert detected_opt2["key"] == "cafe"

    detected_opt3 = detect_roleplay_scenario("third one")
    assert detected_opt3 is not None
    assert detected_opt3["key"] == "job_interview"


def test_detect_custom_scenario():
    """Verify custom scenario extraction."""
    detected_custom = detect_roleplay_scenario("act as my college professor")
    assert detected_custom is not None
    assert detected_custom["key"] == "custom"
    assert "college professor" in detected_custom["role"].lower()


def test_roleplay_unselected_returns_none():
    """General speech should not trigger a false scenario selection."""
    assert detect_roleplay_scenario("hello") is None
    assert detect_roleplay_scenario("can you hear me?") is None
    assert detect_roleplay_scenario("what can we do?") is None


def test_is_roleplay_change_request():
    """Verify recognition of scenario reset / change intent."""
    assert is_roleplay_change_request("can we change the scenario?") is True
    assert is_roleplay_change_request("switch to another roleplay") is True
    assert is_roleplay_change_request("let's choose a new topic") is True
    assert is_roleplay_change_request("hey what did you buy at the market?") is False


def test_roleplay_greeting():
    """Roleplay greeting presents scenario options."""
    eng = Engine()
    s = Session(mode="roleplay")
    greeting = asyncio.run(eng.greeting(s))
    assert greeting and len(greeting.split()) >= 4
    # Greeting mentions roleplay / scenarios
    g_lower = greeting.lower()
    assert any(w in g_lower for w in ("roleplay", "market", "friend", "cafe", "interview", "scenario"))


def test_roleplay_turn_flow():
    """Trace end-to-end roleplay turn flow:
    1. Initial turn before scenario chosen -> lists options
    2. User specifies scenario -> activates roleplay, starts in-character
    3. Subsequent turn -> stays in-character
    4. User requests reset -> returns to topic selection
    """
    eng = Engine()
    s = Session(mode="roleplay")

    messages_sent = []
    async def fake_send(msg):
        messages_sent.append(msg)

    # Step 1: User says hello without choosing scenario
    asyncio.run(eng.handle_user_turn(s, "hello", fake_send, t_turn0=100.0))
    assert s.roleplay_started is False

    # Step 2: User selects market friend scenario
    user_selection = "I met a old friend on the market and AI will act as my friend"
    messages_sent.clear()
    asyncio.run(eng.handle_user_turn(s, user_selection, fake_send, t_turn0=105.0))
    assert s.roleplay_started is True
    assert s.scenario == "market_friend"
    assert "Old Friend" in s.roleplay_scenario_title
    # Check that roleplay_update was sent to client
    updates = [m for m in messages_sent if m.get("type") == "roleplay_update"]
    assert len(updates) >= 1
    assert "Old Friend" in updates[0].get("title", "")

    # Step 3: Ongoing conversation in character
    messages_sent.clear()
    asyncio.run(eng.handle_user_turn(s, "Oh hey! I'm shopping for some fresh mangoes today.", fake_send, t_turn0=110.0))
    assert s.roleplay_started is True

    # Step 4: User asks to change scenario
    messages_sent.clear()
    asyncio.run(eng.handle_user_turn(s, "Can we change the scenario?", fake_send, t_turn0=115.0))
    assert s.roleplay_started is False
    reset_updates = [m for m in messages_sent if m.get("type") == "roleplay_update"]
    assert len(reset_updates) >= 1
    assert reset_updates[0].get("title") == "Choose a Scenario"


def test_roleplay_topic_discussion_and_confirmation():
    """Verify that proposing a topic first triggers a discussion with the user,
    and roleplay only starts in-character once confirmed."""
    from app.conversation.engine import Engine, Session
    eng = Engine()
    s = Session(mode="roleplay")

    messages_sent = []
    async def fake_send(msg):
        messages_sent.append(msg)

    # 1. User proposes a topic without explicit confirmation/roles
    asyncio.run(eng.handle_user_turn(s, "I'd like to do a job interview", fake_send, t_turn0=100.0))
    # Not started yet: discussing topic & roles with user first
    assert s.roleplay_started is False
    assert getattr(s, "_roleplay_pending_topic", None) is not None
    assert s._roleplay_pending_topic.get("key") == "job_interview"

    # 2. User confirms and agrees to start
    messages_sent.clear()
    asyncio.run(eng.handle_user_turn(s, "Yes, that sounds good, let's start!", fake_send, t_turn0=105.0))
    # Now roleplay starts in-character
    assert s.roleplay_started is True
    assert s.scenario == "job_interview"
    assert "Job Interview" in s.roleplay_scenario_title
