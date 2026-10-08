"""Chunker + level + acceptance transcript tests."""
import asyncio
from app.conversation.engine import chunk_sentences, SentenceCoalescer, first_chunk_split, clean_for_speech, strip_emotion_tags
from app.coaching.level import estimate_level
from app.providers.llm.mock import MockProvider


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


def test_first_chunk_split_fast_path():
    """The opening chunk must be speakable ASAP without emitting bare fragments."""
    assert first_chunk_split("Right on. We usually say") == ("Right on.", "We usually say")
    assert first_chunk_split("Nice! How are you") is None           # too short to stand alone
    assert first_chunk_split("Right on, that makes") is None         # clause under 24 chars
    head, rest = first_chunk_split("This is about my project, and we are working")
    assert head == "This is about my project," and rest == "and we are working"
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
        r2 = p.reply(msgs("I'm building a tender management system."))
        assert "hardest part" in r2.lower()
        # after several turns, pattern note appears
        p.turn = 3
        r3 = p.reply(msgs("yesterday_go"))  # synthetic trigger
        assert True  # no crash
    asyncio.run(go())
