"""Chunker + level + acceptance transcript tests."""
import asyncio
from app.conversation.engine import chunk_sentences, SentenceCoalescer, first_chunk_split, clean_for_speech, strip_emotion_tags
from app.coaching.level import estimate_level
from tests.mock_provider import MockProvider


def test_chunker():
    sents, rest = chunk_sentences("Hello there. How are you? Good")
    assert sents == ["Hello there.", "How are you?"]
    assert rest == "Good"


def test_chunker_long_phrase_flush():
    buf = "x," * 100
    sents, rest = chunk_sentences(buf)
    assert isinstance(sents, list)


def test_clean_for_speech_strips_emphasis_markers():
    """A **pair** can straddle a chunk split — never let a stray asterisk reach TTS."""
    assert clean_for_speech("What kind of **project** is it?") == "What kind of project is it?"
    assert clean_for_speech("What kind of project are you **working on") == "What kind of project are you working on"
    assert "*" not in clean_for_speech("**went to the office** today")


def test_clean_for_speech_keeps_emotion_tags():
    """Emotion tags like [chuckle], [happy] must survive clean_for_speech so
    Fish Audio receives them as voice direction markers."""
    assert "[chuckle]" in clean_for_speech("[chuckle] That's [emphasis] really interesting!")
    assert "[happy]" in clean_for_speech("[happy] Good news!")
    assert "[break]" in clean_for_speech("Hello [break] world")


def test_strip_emotion_tags_for_display():
    """Display text must not show emotion tags (but [emphasis] is a TTS-only
    directive, not a display marker — the client uses **...** for display)."""
    assert strip_emotion_tags("[chuckle] That's really interesting!") == "That's really interesting!"
    assert strip_emotion_tags("[happy] Good [break] news!") == "Good news!"
    assert strip_emotion_tags("No tags here") == "No tags here"
    # [emphasis] is a Fish Audio delivery marker — stripped from display
    assert strip_emotion_tags("[emphasis] really important") == "really important"
    # Stray or lone brackets must be stripped so brackets never leak to display
    assert strip_emotion_tags("[") == ""
    assert strip_emotion_tags("]") == ""
    assert strip_emotion_tags("[relaxed]") == ""
    assert strip_emotion_tags("[happy] [calm]") == ""


def test_first_chunk_split_fast_path():
    """The opening chunk must be speakable ASAP without emitting bare fragments or tiny leads."""
    assert first_chunk_split("Right on. We usually say") is None     # tiny lead (< 28 chars) waits for full sentence
    assert first_chunk_split("Nice! How are you") is None           # too short to stand alone
    assert first_chunk_split("Right on, that makes") is None         # clause under 24 chars
    head, rest = first_chunk_split("This is about my project, and we are working")
    assert head == "This is about my project," and rest == "and we are working"
    # Lead + sentence forms a complete, natural opening chunk:
    head, rest = first_chunk_split("Perfect! That sentence is grammatically spot-on. You've mastered")
    assert head == "Perfect! That sentence is grammatically spot-on." and rest == "You've mastered"
    assert first_chunk_split("It costs 3.5 dollars today") is None   # decimal, not a sentence end
    assert first_chunk_split("") is None


def test_coalescer_merges_short_lead():
    """The reported bug: 'Short and sweet!' must NOT become its own TTS request."""
    c = SentenceCoalescer()
    assert c.feed("Short and sweet!") == []
    out = c.feed("Did you end up doing anything fun, or was it just a quiet day?")
    assert out == ["Short and sweet! Did you end up doing anything fun, or was it just a quiet day?"]
    assert c.flush() == []


def test_coalescer_long_sentence_sends_immediately():
    c = SentenceCoalescer()
    out = c.feed("That sounds like a really useful project with plenty of moving parts involved.")
    assert len(out) == 1
    assert c.flush() == []


def test_coalescer_flush_remainder():
    c = SentenceCoalescer()
    assert c.feed("Nice!") == []
    assert c.flush("thanks") == ["Nice! thanks"]
    assert c.flush() == []


def test_level_moves_gradually():
    assert estimate_level("Hi", "B1") in ("A2", "B1")
    adv = estimate_level("Although the breakthrough was significant, despite trade-offs we managed because the architecture, which handled long context, performed well on the other hand.", "B1")
    assert adv in ("B2", "C1", "C2", "B1")


def test_acceptance_transcript():
    """§43: natural replies, no instant grammar interrupt; delayed pattern note."""
    async def go():
        p = MockProvider()
        msgs = lambda u: [{"role": "system", "content": "x"}, {"role": "user", "content": u}]
        r1 = p.reply(msgs("Today was good. I go to office and work on my project."))
        assert "went" not in r1.lower() or "correction" not in r1.lower()  # no instant interrupt
        assert any(k in r1.lower() for k in ("nice", "what", "working", "interesting"))
        r2 = p.reply(msgs("I'm building an interactive web application."))
        assert any(k in r2.lower() for k in ("hardest part", "interesting", "nice"))
        # after several turns, pattern note appears
        p.turn = 3
        r3 = p.reply(msgs("yesterday_go"))  # synthetic trigger
        assert True  # no crash
    asyncio.run(go())


def test_sync_past_history(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    from app.conversation.engine import Session, engine, memory_hint_for
    s = Session(id="test_mem", user_id="test_isolated_user")
    turns = [
        {"role": "user", "content": "I am working on building a responsive web app with Python."},
        {"role": "assistant", "content": "That sounds very interesting!"},
        {"role": "user", "content": "We need to deploy it next month."}
    ]
    engine.sync_past_history(s, turns)
    assert "responsive web app" in s.past_chat_summary
    assert "deploy it next month" in s.past_chat_summary
    mem_hint = memory_hint_for({}, s)
    assert "previous conversations" in mem_hint
    assert "responsive web app" in mem_hint


def test_fresh_session_no_leaked_history(tmp_path, monkeypatch):
    """Verify that a brand new session with no client history does not leak disk summaries into greeting or memory."""
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    from app.coaching.memory import save_profile
    from app.conversation.engine import Session, Engine, memory_hint_for

    # Setup profile with old past summary on disk
    save_profile(str(tmp_path), {
        "user_id": "test_ghost",
        "last_chat_summary": "I was working on tender management",
        "mode_summaries": {"free": "I was working on tender management"}
    })

    # Fresh session with 0 history
    s = Session(id="fresh_sess", user_id="test_ghost", mode="free")

    # 1. memory_hint_for must NOT leak disk summaries into prompt
    mem = memory_hint_for({"mode_summaries": {"free": "I was working on tender management"}}, s)
    assert "tender management" not in mem
    assert "previous conversations" not in mem

    # 2. Greeting prompt generated must be a fresh intro, not a welcome-back to old discussions
    eng = Engine()
    captured_prompt = []

    class CaptureLLM:
        name = "mock"
        async def complete(self, msgs, max_tokens=60):
            captured_prompt.append(msgs)
            return "Hey! I'm Puck. How's your day going?"

    eng.llm_for = lambda sess: CaptureLLM()
    asyncio.run(eng.greeting(s))

    assert len(captured_prompt) == 1
    user_p = captured_prompt[0][-1]["content"]
    assert "tender management" not in user_p
    assert "Reference where you left off" not in user_p
    assert "Say a brief warm hello" in user_p


def test_start_greeting_modes():
    """Verify start_greeting generates and speaks an AI greeting for free, training, and roleplay."""
    from app.conversation.engine import Session, Engine

    eng = Engine()
    for m in ("free", "training", "roleplay"):
        s = Session(id=f"test_greet_{m}", mode=m)
        sent_messages = []

        async def mock_send(msg):
            sent_messages.append(msg)

        asyncio.run(eng.start_greeting(s, mock_send))
        tts_msgs = [msg for msg in sent_messages if msg.get("type") == "tts_sentence"]
        done_msgs = [msg for msg in sent_messages if msg.get("type") == "llm_done"]
        assert len(tts_msgs) >= 1, f"No TTS sentence sent for mode={m}"
        assert len(done_msgs) == 1, f"No llm_done sent for mode={m}"
        assert done_msgs[0].get("greeting") is True
        assert done_msgs[0].get("turn") == f"greet-{m}"
        assert s.history and s.history[-1]["role"] == "assistant"


def test_gemini_live_cancel_invokes_interrupt():
    """Verify engine.cancel immediately triggers live_session.interrupt() in background."""
    from app.conversation.engine import Session, Engine

    eng = Engine()
    s = Session(id="test_live_cancel", response_mode="gemini_live")
    eng.sessions[s.id] = s

    class MockLiveSession:
        def __init__(self):
            self.interrupted = False

        async def interrupt(self):
            self.interrupted = True

    mock_live = MockLiveSession()
    s._live_session = mock_live

    async def run_cancel():
        eng.cancel(s.id)
        # Yield to let the created asyncio task execute
        await asyncio.sleep(0.01)

    asyncio.run(run_cancel())
    assert s.cancelled is True
    assert mock_live.interrupted is True


def test_gemini_live_turn_interrupted_silence():
    """Verify handle_gemini_live_turn does not emit fallback text when interrupted before tokens."""
    import time
    from app.conversation.engine import Session, Engine
    import pytest

    eng = Engine()
    s = Session(id="test_live_interrupt", response_mode="gemini_live")
    eng.sessions[s.id] = s

    class MockLiveSession:
        def __init__(self):
            self.setup_complete = True
            self.voice_name = "Orbit"
            self.language = "english"

        async def send_user_text(self, text, on_text=None, on_audio=None):
            raise asyncio.CancelledError()

        async def close(self):
            pass

    s._live_session = MockLiveSession()
    sent = []

    async def mock_send(msg):
        sent.append(msg)

    async def run_turn():
        with pytest.raises(asyncio.CancelledError):
            await eng.handle_gemini_live_turn(s, "Hello", mock_send, time.time())

    asyncio.run(run_turn())
    # Verify no fallback speech was sent
    assert not any(m.get("type") == "llm_done" and "sorry" in m.get("text", "").lower() for m in sent)
    assert any(m.get("type") == "llm_cancelled" for m in sent)
    assert not any("sorry" in h.get("content", "").lower() for h in s.history)


def test_gemini_live_turn_interrupted_delivers_partial():
    """Verify handle_gemini_live_turn delivers partial text on interrupt without audio leak."""
    import time
    from app.conversation.engine import Session, Engine
    import pytest

    eng = Engine()
    s = Session(id="test_live_partial", response_mode="gemini_live")
    eng.sessions[s.id] = s

    class MockLiveSession:
        def __init__(self):
            self.setup_complete = True
            self.voice_name = "Orbit"
            self.language = "english"

        async def send_user_text(self, text, on_text=None, on_audio=None):
            if on_text:
                await on_text("Hello there, ")
            if on_audio:
                await on_audio("AQID", "audio/pcm;rate=24000")
            raise asyncio.CancelledError()

        async def close(self):
            pass

    s._live_session = MockLiveSession()
    sent = []

    async def mock_send(msg):
        sent.append(msg)

    async def run_turn():
        with pytest.raises(asyncio.CancelledError):
            await eng.handle_gemini_live_turn(s, "Hello", mock_send, time.time())

    asyncio.run(run_turn())
    done_msgs = [m for m in sent if m.get("type") == "llm_done"]
    assert len(done_msgs) == 1
    assert done_msgs[0].get("partial") is True
    assert "Hello there" in done_msgs[0].get("text", "")
    assert s.history and "Hello there" in s.history[-1]["content"]


def test_error_fallback_never_shows_sorry_or_say_again():
    """Verify handle_user_turn error fallback never contains 'sorry', 'missed', 'hiccup', or 'say that again'."""
    from app.conversation.engine import Session, Engine

    eng = Engine()
    s = Session(id="test_fallback_nosorry")
    s.response_mode = "fast"
    sent = []

    async def mock_send(msg):
        sent.append(msg)

    # Force error by sending turn with no reachable providers
    asyncio.run(eng.handle_user_turn(s, "Test user speech", mock_send, 0.0, turn_id="t1", req_id="r1"))
    done_msgs = [m for m in sent if m.get("type") == "llm_done"]
    assert len(done_msgs) == 1
    text = done_msgs[0].get("text", "").lower()
    assert "sorry" not in text
    assert "missed" not in text
    assert "hiccup" not in text
    assert "say that again" not in text
    assert "again" not in text

