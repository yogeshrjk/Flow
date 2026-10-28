"""English Reading Practice: passage diff, metrics, prompt evidence rules,

and the engine interaction state machine (hermetic — MockProvider only)."""

import asyncio

import pytest

from app.coaching import reading as reading_mod

from app.conversation.engine import Session, Engine

from tests.mock_provider import MockProvider

PASSAGE = ("My family loves to drink tea every evening. We sit together "

           "and tell stories about our day. Sometimes my sister tells "

           "funny stories and everyone laughs loudly. After that we "

           "help each other clean the room. Those evenings always make "

           "me feel happy and warm.")

FULL_READ = ("my family loves to drink tea every evening we sit together "

             "and tell stories about our day sometimes my sister tells "

             "funny stories and everyone laughs loudly after that we "

             "help each other clean the room those evenings always make "

             "me feel happy and warm")

PARTIAL = ("my family loves to drink tea every evening we sit together "

           "tell stories about our day sometimes my sister tells "

           "funny stories and drink everyone laughs loudly after that "

           "we help each other clean the room those evenings make me "

           "feel happy and calm")

class Sink:

    """Collect all outbound WS payloads for assertions."""

    def __init__(self):

        self.msgs = []

    async def __call__(self, m):

        self.msgs.append(dict(m))

    def types(self):

        return [m["type"] for m in self.msgs]

    def has(self, mtype):

        return any(m["type"] == mtype for m in self.msgs)

    def count(self, mtype):

        return sum(1 for m in self.msgs if m["type"] == mtype)

    def nth(self, mtype, n=1):

        got = [m for m in self.msgs if m["type"] == mtype]

        return got[n - 1] if len(got) >= n else None

# --- pure unit tests ---------------------------------------------------------

def test_diff_perfect():

    d = reading_mod.diff_passage(PASSAGE, FULL_READ)

    assert d.is_empty()

    assert d.accuracy == pytest.approx(1.0)

    assert d.spoken_wc == d.passage_wc

def test_diff_detects_missing_extra_repeated():

    d = reading_mod.diff_passage(PASSAGE, PARTIAL)

    assert not d.is_empty()

    missing = [w.lower() for w in d.missing]

    assert "always" in missing and "and" in missing

    assert "drink" in [w.lower() for w in d.extra]

    replaced = [(p.lower(), h.lower()) for p, h in d.replaced]

    assert any(p == "warm" for p, _ in replaced)

    assert 0.8 < d.accuracy < 0.98

def test_diff_detects_repeated_word():

    d = reading_mod.diff_passage(PASSAGE, PARTIAL + " tell")

    assert "tell" in [w.lower() for w in d.repeated]

    # a word repeated later in a NEW position is not reported again as doubled

    d2 = reading_mod.diff_passage("a b c d e f g", "a b b b c d e f g")

    assert d2.repeated == ["b"] or all(w == "b" for w in d2.repeated)

def test_diff_word_count():

    d = reading_mod.diff_passage(PASSAGE, FULL_READ)

    assert d.passage_wc == len(PASSAGE.split())

    assert d.spoken_wc == len(FULL_READ.split())

def test_summarize_stats_no_timing_gives_no_wpm():

    """Without a duration the coach must NOT invent speed or pause claims."""

    d = reading_mod.diff_passage(PASSAGE, PARTIAL)

    s = reading_mod.summarize_stats(d, None, None, None)

    assert s["words_per_minute"] is None

    assert s["pause_total_s"] is None

    assert s["long_pauses"] is None

    assert "missing" in s and "replaced" in s

def test_summarize_stats_with_timing():

    d = reading_mod.diff_passage(PASSAGE, FULL_READ)

    s = reading_mod.summarize_stats(d, 30.0, 6.0, 1)

    assert 0 <= s["words_per_minute"] <= 180

    assert s["pause_total_s"] == 6.0

    assert s["long_pauses"] == 1

def test_feedback_rules_no_timing():

    """Speed/pause must be forbidden in the prompt when nothing was measured."""

    d = reading_mod.diff_passage(PASSAGE, PARTIAL)

    msgs = reading_mod.feedback_messages(d, PASSAGE, PARTIAL, 1)

    blob = " ".join(m["content"] for m in msgs)

    assert "do NOT mention speed" in blob

    assert "do NOT mention pauses" in blob

    user = msgs[-1]["content"]

    assert PASSAGE in user          # the passage is in the user turn

    assert PARTIAL in user          # the transcript is in the user turn

def test_feedback_rules_with_timing():

    d = reading_mod.diff_passage(PASSAGE, PARTIAL)

    msgs = reading_mod.feedback_messages(d, PASSAGE, PARTIAL, 2, 25.0, 4.0, 1)

    blob = " ".join(m["content"] for m in msgs)

    assert "report it" in blob and "may describe them briefly" in blob

    assert "NEVER invent pronunciation scores" in blob

    assert "never give grades" in blob.lower()

def test_feedback_messages_measured_block_lists_diffs():

    d = reading_mod.diff_passage(PASSAGE, PARTIAL)

    msgs = reading_mod.feedback_messages(d, PASSAGE, PARTIAL, 1, 25.0, 4.0, 1)

    blob = msgs[-1]["content"]

    assert "MEASURED" in blob

    assert "always" in blob   # missing word appears as evidence

    assert "warm" in blob

def test_summary_messages_structure():

    rounds = [

        {"round": 1, "text": PARTIAL, "stats": {"word_accuracy": 0.9}},

        {"round": 2, "text": FULL_READ, "stats": {"word_accuracy": 1.0}},

        {"round": 3, "text": FULL_READ, "stats": {"word_accuracy": 1.0}},

    ]

    msgs = reading_mod.summary_messages(PASSAGE, rounds)

    blob = " ".join(m["content"] for m in msgs)

    assert "THREE priority areas" in blob

    assert "ROUND 1" in blob and "ROUND 3" in blob

def test_passage_messages_exclusion():

    msgs = reading_mod.passage_messages("B1", exclude=["Do not reuse something else."])

    blob = " ".join(m["content"] for m in msgs)

    assert "Do not reuse" in blob

    assert "passage" in blob.lower()

    assert "150" in blob  # PASSAGE_MAX_WORDS is the cap

def test_intro_is_short_and_neutral():

    intro = reading_mod.intro_for(PASSAGE, "B1")

    assert len(intro.split()) < 40

    assert "read" in intro.lower()

def test_reading_system_and_rules_forbid_fabrication():

    assert "reading coach" in reading_mod.READING_SYSTEM.lower()

    d = reading_mod.diff_passage(PASSAGE, PARTIAL)

    fb_blob = " ".join(m["content"] for m in

                       reading_mod.feedback_messages(d, PASSAGE, PARTIAL, 1, None, None, None))

    assert "NEVER invent pronunciation" in fb_blob

    assert "grades" in fb_blob.lower()

def test_fallback_passages_exclude_repeats():

    a = reading_mod.fallback_passages_for("B1")

    b = reading_mod.fallback_passages_for("B1", exclude=[a])

    c = reading_mod.fallback_passages_for("B1", exclude=[a, b])

    assert a != b and b != c

def test_round_instructions():

    assert reading_mod.READING_INSTRUCTION

    assert reading_mod.RETRY_INSTRUCTION

    assert "round" not in reading_mod.READING_INSTRUCTION.lower()

    assert "round" not in reading_mod.RETRY_INSTRUCTION.lower()

    for i in (1, 2, 3):

        assert reading_mod.ROUND_INSTRUCTIONS[i], i

def test_clean_passage_strips_markdown_and_title():

    raw = "**Morning Walks**\n\nEvery morning Ravi drinks tea with his family."

    out = Engine._clean_passage(raw)

    assert "*" not in out

    assert "Morning Walks" not in out

    assert "Ravi" in out

def test_split_speech():

    parts = Engine._split_speech(

        "Good reading. You left out two words: 'always', 'and'. Next time read slowly.")

    assert len(parts) >= 2

    assert all(p.strip() for p in parts)

def test_reading_session_lifecycle():

    rs = reading_mod.ReadingSession()

    assert not rs.active and rs.state == "GENERATING_PASSAGE"

    rs.passage = PASSAGE

    assert rs.active and rs.round == 1

# --- engine state machine (hermetic: MockProvider everywhere) ----------------

class PassageMock(MockProvider):

    """MockProvider that answers passage-generation requests with a real-size

    passage (Gemini-shaped), everything else conversationally."""

    PASSAGE_TEXT = ("Every weekend my family takes a long walk along the canal near our "

                    "house. My father talks about the birds, and my sister counts the "

                    "small boats passing under the old stone bridge. Sometimes we buy "

                    "sweet corn from a man who cycles with a small basket of peanuts. "

                    "Last Sunday the sky was grey, and the wind pushed the water around "

                    "in small waves. We walked faster than usual, laughing when the cold "

                    "air touched our faces with tiny spots of rain.")

    KEY = "passage for a student"

    async def stream(self, messages: list, max_tokens: int = 250):

        users = [m.get("content", "") for m in messages if m.get("role") == "user"]

        last = users[-1] if users else ""

        if self.KEY in last:

            yield self.PASSAGE_TEXT

            return

        async for tok in super().stream(messages, max_tokens=max_tokens):

            yield tok

@pytest.fixture()

def eng():

    e = Engine()

    e.llm_for = lambda s: PassageMock()

    return e

def _states_of(sink):

    return [m.get("state") for m in sink.msgs if m["type"] == "reading_state"]

async def _ack_when(eng, sid, sink, mtype, count=1, tries=600):

    """Simulate the client acking after the given speech group arrives — the

    engine's round handoff only continues once reading_round_ok is set."""

    for _ in range(tries):

        ev = eng.reading_acks.get(sid)

        if ev is not None and sink.count(mtype) >= count:

            ev.set()

            return True

        await asyncio.sleep(0.005)

    return False

async def _settle(eng, sid, s, sink, retry_count, want_state):

    for _ in range(900):

        t = eng.reading_tasks.get(sid)

        if (t is None and sink.count("reading_retry") >= retry_count and s.reading and s.reading.state == want_state):

            return True

        await asyncio.sleep(0.005)

    return False

def test_start_reading_message_sequence(eng):

    s, sink = Session(id="t_seq"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

    asyncio.run(asyncio.wait_for(go(), timeout=5))

    assert _states_of(sink) == ["GENERATING_PASSAGE", "WAITING_FOR_READING"]

    pmsg = sink.nth("reading_passage")

    assert pmsg and len(pmsg["passage"].split()) >= 30  # ~100-150 words target

    assert pmsg["instructions"] == reading_mod.READING_INSTRUCTION

    tts = [m for m in sink.msgs if m["type"] == "tts_sentence"]

    assert tts and tts[0].get("turn", "").startswith("r1-intro")

    assert tts[0].get("first") is True

    tts_all = " ".join(m["text"].lower() for m in tts)

    assert "reading practice" in tts_all

    assert s.reading.state == "WAITING_FOR_READING" and s.reading.gen == 1


def test_registered_reading_start_task_does_not_cancel_itself(eng):
    """WebSocket mode changes register the generation task before it runs.
    That registry entry must not make start_reading cancel its own coroutine.
    """
    s, sink = Session(id="t_registered_start"), Sink()

    async def go():
        task = asyncio.create_task(eng.start_reading(s, sink))
        eng.reading_tasks[s.id] = task
        await asyncio.wait_for(task, timeout=3)

    asyncio.run(asyncio.wait_for(go(), timeout=5))

    assert sink.has("reading_passage")
    assert s.reading and s.reading.state == "WAITING_FOR_READING"

def test_reading_voice_only_from_waiting(eng):

    s, sink = Session(id="t_voice"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        st = _states_of(sink)

        assert st.count("RECORDING_READING") == 0

        await eng.handle_reading_voice(s, sink)

        assert _states_of(sink).count("RECORDING_READING") == 1

        await eng.handle_reading_voice(s, sink)

        assert _states_of(sink).count("RECORDING_READING") == 1

    asyncio.run(asyncio.wait_for(go(), timeout=5))

def test_reading_retry_until_accurate_and_continue_prompt(eng):

    s, sink = Session(id="t_flow"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        assert s.reading.state == "WAITING_FOR_READING"

        # 1. First attempt: Inaccurate read (modify passage so accuracy drops and missing > 3)
        p_words = s.reading.passage.split()
        inacc_words = p_words[:25] + ["different", "words", "here"] + p_words[35:]
        inacc_read = " ".join(inacc_words)

        await eng.handle_reading_voice(s, sink)

        sub1 = asyncio.create_task(eng.handle_reading_submit(
            s, inacc_read, sink, round=1, gen=s.reading.gen,
            duration_s=30, pause_total_s=3, long_pause_count=0,
            word_count=len(inacc_words)))

        assert await _ack_when(eng, s.id, sink, "reading_feedback", count=1)

        await asyncio.wait_for(sub1, timeout=5)

        # Should ask user to read again!
        assert await _settle(eng, s.id, s, sink, 1, "WAITING_FOR_READING")

        assert sink.count("reading_retry") == 1

        fb1 = sink.nth("reading_feedback", 1)

        assert fb1["is_accurate"] is False and fb1["completed"] is False

        # 2. Second attempt: Accurate read (reads exact passage)
        await eng.handle_reading_voice(s, sink)

        sub2 = asyncio.create_task(eng.handle_reading_submit(
            s, s.reading.passage, sink, round=2, gen=s.reading.gen,
            duration_s=25, pause_total_s=2, long_pause_count=0,
            word_count=len(p_words)))

        assert await _ack_when(eng, s.id, sink, "reading_feedback", count=2)

        await asyncio.wait_for(sub2, timeout=5)

        # Should complete and prompt: Would you like to continue reading?
        assert await _settle_complete(eng, s.id, s, sink)

        assert sink.count("reading_continue_prompt") == 1

        fb2 = sink.nth("reading_feedback", 2)

        assert fb2["is_accurate"] is True and fb2["completed"] is True

        assert "continue reading" in fb2["text"].lower()

        assert s.reading.state == "AWAITING_CONTINUE"

    asyncio.run(asyncio.wait_for(go(), timeout=30))

async def _settle_complete(eng, sid, s, sink):

    for _ in range(900):

        t = eng.reading_tasks.get(sid)

        if t is None and sink.count("reading_continue_prompt") >= 1 and s.reading.state == "AWAITING_CONTINUE":

            return True

        await asyncio.sleep(0.005)

    return False

def test_reject_premature_few_words(eng):

    s, sink = Session(id="t_few"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        await eng.handle_reading_voice(s, sink)

        # Only spoke 3 words

        await eng.handle_reading_submit(

            s, "My family loves", sink, round=1, gen=s.reading.gen, word_count=3)

        assert sink.has("reading_error")

        err = sink.nth("reading_error")

        assert "few words" in err["message"].lower()

        assert s.reading.state == "WAITING_FOR_READING"

    asyncio.run(asyncio.wait_for(go(), timeout=5))

def test_late_ack_fire_timeout():

    e = Engine()

    e.llm_for = lambda s: PassageMock()

    orig = e._wait_reading_ack

    async def patched(sess, timeout=15.0):

        await orig(sess, timeout=0.2)

    e._wait_reading_ack = patched

    s, sink = Session(id="t_ack"), Sink()

    async def go():

        await asyncio.wait_for(e.start_reading(s, sink), timeout=3)

        await e.handle_reading_voice(s, sink)

        sub = asyncio.create_task(e.handle_reading_submit(

            s, PARTIAL, sink, round=1, gen=s.reading.gen, word_count=len(PARTIAL.split())))

        # NO ack is ever set — the patched short timeout must release it

        await asyncio.wait_for(sub, timeout=10)

        for _ in range(900):

            t = e.reading_tasks.get(s.id)

            if (t is None and sink.count("reading_retry") >= 1 and s.reading.state == "WAITING_FOR_READING"):

                break

            await asyncio.sleep(0.005)

        assert sink.count("reading_retry") == 1

    asyncio.run(asyncio.wait_for(go(), timeout=20))

def test_duplicate_submit_dropped(eng):

    s, sink = Session(id="t_dup"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        await eng.handle_reading_voice(s, sink)

        # occupy the analysis slot with a fake running task

        eng.reading_tasks[s.id] = asyncio.create_task(asyncio.sleep(10))

        await eng.handle_reading_submit(s, FULL_READ, sink, round=1, gen=s.reading.gen)

        assert not eng.reading_tasks[s.id].done()

        eng.reading_tasks[s.id].cancel()

    asyncio.run(asyncio.wait_for(go(), timeout=5))

    assert not sink.has("reading_feedback")

def test_stale_gen_submit_dropped(eng):

    s, sink = Session(id="t_stale"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        await eng.handle_reading_voice(s, sink)

        await eng.handle_reading_submit(s, FULL_READ, sink, round=1, gen=s.reading.gen + 5)

    asyncio.run(asyncio.wait_for(go(), timeout=5))

    assert not sink.has("reading_feedback")

def test_wrong_round_submit_dropped(eng):

    s, sink = Session(id="t_roundx"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        await eng.handle_reading_voice(s, sink)

        await eng.handle_reading_submit(s, FULL_READ, sink, round=2, gen=s.reading.gen)

    asyncio.run(asyncio.wait_for(go(), timeout=5))

    assert not sink.has("reading_feedback")

def test_empty_submit_retryable(eng):

    s, sink = Session(id="t_empty"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        await eng.handle_reading_voice(s, sink)

        await eng.handle_reading_submit(s, "", sink, round=1, gen=s.reading.gen)

    asyncio.run(asyncio.wait_for(go(), timeout=5))

    err = sink.nth("reading_error")

    assert err and "start again from the beginning" in err["message"]

    assert err["retryable"] is True

    note_state = [m for m in sink.msgs if m["type"] == "reading_state"

                  and m.get("note") == "nothing heard"]

    assert note_state and note_state[-1]["state"] == "WAITING_FOR_READING"

    assert s.reading.round == 1

def test_submit_without_reading_active_rejected(eng):

    s, sink = Session(id="t_off"), Sink()

    async def go():

        await eng.handle_reading_submit(s, FULL_READ, sink, round=1, gen=1)

    asyncio.run(asyncio.wait_for(go(), timeout=5))

    assert sink.has("reading_error")

def test_reading_off_clears_state(eng):

    s, sink = Session(id="t_stop"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        assert s.reading and s.reading.active

        eng.reading_off(s)

        assert s.reading is None

        assert not eng.reading_tasks.get(s.id)

    asyncio.run(asyncio.wait_for(go(), timeout=5))

def test_start_again_carries_transcripts_and_bumps_gen(eng):

    s, sink = Session(id="t_regen"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        first_gen = s.reading.gen

        s.reading.transcripts.append({"round": 1, "text": "x", "stats": {}})

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        assert s.reading.gen == first_gen + 1

        assert len(s.reading.transcripts) == 1

        assert s.reading.round == 1

    asyncio.run(asyncio.wait_for(go(), timeout=8))

def test_llm_failure_uses_fallback_passage(mock_llm_eng=None):

    e = Engine()

    class Boom(MockProvider):

        async def stream(self, messages, max_tokens=250):

            raise RuntimeError("LLM out of quota")

            yield ""

    e.llm_for = lambda s: Boom()

    s, sink = Session(id="t_nofb"), Sink()

    async def go():

        await asyncio.wait_for(e.start_reading(s, sink), timeout=28)

    asyncio.run(asyncio.wait_for(go(), timeout=32))

    p = sink.nth("reading_passage")

    assert p and p["passage"] in (reading_mod.FALLBACK_PASSAGES["beginner"],

                                  reading_mod.FALLBACK_PASSAGES["default"],

                                  reading_mod.FALLBACK_PASSAGES["upper"])

    assert _states_of(sink)[-1] == "WAITING_FOR_READING"

def test_llm_feedback_failure_uses_fallback_feedback():

    e = Engine()

    class Boom(MockProvider):

        async def stream(self, messages, max_tokens=250):

            raise RuntimeError("LLM out of quota")

            yield ""

    s, sink = Session(id="t_nofb2"), Sink()

    # MockProvider for passage generation, Boom for the feedback call.

    calls = {"n": 0}

    def llm_picker(sess):

        calls["n"] += 1

        if calls["n"] <= 1:

            return MockProvider()   # passage generation

        return Boom()               # every later call (feedback/summary)

    e.llm_for = llm_picker

    async def go():

        await asyncio.wait_for(e.start_reading(s, sink), timeout=3)

        await e.handle_reading_voice(s, sink)

        sub = asyncio.create_task(e.handle_reading_submit(

            s, PARTIAL, sink, round=1, gen=s.reading.gen))

        assert await _ack_when(e, s.id, sink, "reading_feedback")

        await asyncio.wait_for(sub, timeout=8)

    asyncio.run(asyncio.wait_for(go(), timeout=16))

    fb = sink.nth("reading_feedback")

    assert fb and "read the passage again" in fb["text"].lower()

def test_analysis_cancelled_by_reading_off(eng):

    s, sink = Session(id="t_cancel"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        await eng.handle_reading_voice(s, sink)

        sub = asyncio.create_task(eng.handle_reading_submit(

            s, FULL_READ, sink, round=1, gen=s.reading.gen))

        await asyncio.sleep(0.05)

        t = eng.reading_tasks.get(s.id)

        assert t is not None

        eng.reading_off(s)   # cancels the analysis task and clears the mode

        with pytest.raises(asyncio.CancelledError):

            await t

        assert not sink.has("reading_feedback")

    asyncio.run(asyncio.wait_for(go(), timeout=10))

def test_conversational_turn_suppressed_during_active_reading(eng):

    s, sink = Session(id="t_suppress"), Sink()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

        assert s.reading and s.reading.active

        # A normal chat turn must be suppressed

        await eng.handle_user_turn(s, "Hello, how are you?", sink, 0.0)

        assert not sink.has("tts_sentence") or all(m.get("turn", "").startswith("r1") for m in sink.msgs if m["type"] == "tts_sentence")

        assert not sink.has("correction")

        assert sink.has("status")

        st = sink.nth("status")

        assert st and st.get("note") == "listening to your reading"

    asyncio.run(asyncio.wait_for(go(), timeout=10))

# --- Pause analysis and VAD pause measurement tests --------------------------

def test_pause_analysis_continuous_speech_natural_gaps():

    """Sub-350ms gaps between words/syllables are natural articulation gaps, not pauses."""

    d = reading_mod.diff_passage(PASSAGE, FULL_READ)

    micro_gaps = [

        {"duration_s": 0.12, "after_word": "family", "before_word": "loves"},

        {"duration_s": 0.18, "after_word": "drink", "before_word": "tea"},

        {"duration_s": 0.22, "after_word": "tea", "before_word": "every"},

    ]

    res = reading_mod.analyze_pauses(PASSAGE, FULL_READ, micro_gaps, d)

    assert res["total_pauses"] == 0

    assert res["significant_pauses_count"] == 0

    assert res["mid_sentence_pauses_count"] == 0

    assert len(res["hesitation_points"]) == 0

def test_pause_analysis_long_pause_in_middle_of_sentence():

    """A 1.6s pause inside a sentence without punctuation is an unnecessary pause / hesitation."""

    d = reading_mod.diff_passage(PASSAGE, FULL_READ)

    pauses = [

        {"duration_s": 1.6, "after_word": "tea", "before_word": "every"}

    ]

    res = reading_mod.analyze_pauses(PASSAGE, FULL_READ, pauses, d)

    assert res["total_pauses"] == 1

    assert res["significant_pauses_count"] == 1

    assert res["mid_sentence_pauses_count"] == 1

    assert res["sentence_boundary_pauses_count"] == 0

    assert len(res["hesitation_points"]) == 1

    assert "tea" in res["hesitation_points"][0] and "1.6" in res["hesitation_points"][0]

    p0 = res["significant_pauses"][0]

    assert p0["boundary"] == "mid_sentence"

    assert p0["classification"] == "unnecessary_mid_sentence_pause"

    assert p0["is_natural"] is False

def test_pause_analysis_pauses_between_sentences():

    """A 1.1s pause at a sentence period is natural phrasing and must not be penalized."""

    d = reading_mod.diff_passage(PASSAGE, FULL_READ)

    pauses = [

        {"duration_s": 1.1, "after_word": "evening", "before_word": "we"}

    ]

    res = reading_mod.analyze_pauses(PASSAGE, FULL_READ, pauses, d)

    assert res["total_pauses"] == 1

    assert res["sentence_boundary_pauses_count"] == 1

    assert res["mid_sentence_pauses_count"] == 0

    assert len(res["hesitation_points"]) == 0  # NOT penalized

    p0 = res["significant_pauses"][0]

    assert p0["boundary"] == "sentence"

    assert p0["classification"] == "natural_sentence_pause"

    assert p0["is_natural"] is True

def test_pause_analysis_hesitation_before_difficult_or_substituted_word():

    """A pause preceding a substituted or complex word is identified as hesitation before that word."""

    d = reading_mod.diff_passage(PASSAGE, PARTIAL)

    pauses = [

        {"duration_s": 1.4, "after_word": "happy", "before_word": "calm"}

    ]

    res = reading_mod.analyze_pauses(PASSAGE, PARTIAL, pauses, d)

    assert res["mid_sentence_pauses_count"] == 1

    assert len(res["hesitation_points"]) == 1

    assert "hesitation before 'calm'" in res["hesitation_points"][0]

def test_summarize_stats_distinguishes_speaking_time_from_elapsed():

    """WPM must be calculated over active speaking time, excluding pauses and app delays."""

    d = reading_mod.diff_passage(PASSAGE, FULL_READ)

    pauses = [

        {"duration_s": 1.2, "after_word": "evening", "before_word": "we"}

    ]

    pa = reading_mod.analyze_pauses(PASSAGE, FULL_READ, pauses, d)

    stats = reading_mod.summarize_stats(

        d, duration_s=25.0, pause_total_s=5.0, long_pause_count=1,

        elapsed_s=30.0, pauses=pauses, pause_analysis=pa

    )

    assert stats["speaking_duration_s"] == 25.0

    assert stats["elapsed_duration_s"] == 30.0

    assert stats["pause_total_s"] == 5.0

    assert stats["sentence_boundary_pauses_count"] == 1

    # 45 words in 25.0s speaking time -> 45 / (25 / 60) = 108.0 WPM

    assert stats["words_per_minute"] == pytest.approx(108.0, abs=0.5)

def test_vad_noise_adaptation_simulation():

    """Simulate EnergyVAD's adaptive noise floor and gate computation."""

    threshold = 0.008

    snr_gate = 2.0

    noise_floor = 0.004

    gate = max(threshold, noise_floor * snr_gate)

    assert gate == 0.008

    # Ambient noise rises to 0.006 (e.g. AC hum)

    for _ in range(50):

        noise_floor += (0.006 - noise_floor) * 0.002

    gate = max(threshold, noise_floor * snr_gate)

    assert gate >= 0.008

    # Signal below gate is treated as silence

    rms = 0.007

    is_speech = rms > gate

    assert not is_speech

def test_vad_pause_detection_and_no_duplicate_speech_end():

    """Simulate EnergyVAD's pause tracker:

    1. Speech starts -> hasSpoken = True

    2. Voice drops below gate -> lastVoiceEnd recorded

    3. Silence > 380ms followed by voice resume -> fires onPause exactly once

    4. End of turn (850ms silence) fires onSpeechEnd only once

    """

    events = []

    pauses = []

    def onSpeechStart(): events.append("speech_start")

    def onSpeechEnd(): events.append("speech_end")

    def onPause(ev): pauses.append(dict(ev))

    speaking = False

    hasSpoken = False

    lastVoiceEnd = 0

    lastSpeech = 0

    minPauseMs = 380

    endOfTurnMs = 850

    # 1. Voice starts at t=1000

    now = 1260

    lastSpeech = now

    speaking = True

    hasSpoken = True

    onSpeechStart()

    # 2. Voice drops at t=2000

    now = 2000

    lastSpeech = now

    now = 2020

    lastVoiceEnd = lastSpeech

    # 3. Silence continues to t=2900 (900ms silence > endOfTurnMs)

    now = 2900

    if speaking and now - lastSpeech > endOfTurnMs:

        speaking = False

        onSpeechEnd()

    assert events == ["speech_start", "speech_end"]

    # Frame at 3100 is still silent: onSpeechEnd must NOT fire again!

    now = 3100

    if speaking and now - lastSpeech > endOfTurnMs:

        speaking = False

        onSpeechEnd()

    assert events.count("speech_end") == 1

    # 4. Voice resumes at t=3200

    now = 3200

    if hasSpoken and lastVoiceEnd > 0:

        pauseDur = now - lastVoiceEnd

        if pauseDur >= minPauseMs:

            onPause({"start": lastVoiceEnd, "end": now, "duration": pauseDur})

        lastVoiceEnd = 0

    assert len(pauses) == 1

    assert pauses[0]["duration"] == 1200

    assert pauses[0]["start"] == 2000

    assert pauses[0]["end"] == 3200

def test_vad_short_syllable_gaps_not_counted_as_pauses():

    """Gaps < 380ms (e.g. 180ms stop consonants / syllable closures) do not emit pauses."""

    pauses = []

    def onPause(ev): pauses.append(dict(ev))

    hasSpoken = True

    lastVoiceEnd = 2000

    now = 2180  # 180ms gap

    minPauseMs = 380

    pauseDur = now - lastVoiceEnd

    if pauseDur >= minPauseMs:

        onPause({"duration": pauseDur})

    lastVoiceEnd = 0

    assert len(pauses) == 0

def test_extract_passage_and_intro_with_labels(eng):

    raw = (

        "INTRO: Hey there! Let's do some reading practice with a story about a morning walk. "

        "Take your time and read it aloud whenever you are ready.\n\n"

        "PASSAGE:\n"

        "Every morning Arun takes his bicycle and rides along the river road. "

        "The air is cool, and the quiet path feels peaceful."

    )

    passage, intro = eng._extract_passage_and_intro(raw, "B1")

    assert "morning walk" in intro

    assert "take your time" in intro.lower()

    assert passage.startswith("Every morning Arun takes")

    assert "INTRO:" not in passage

    assert "PASSAGE:" not in passage

def test_ai_decided_intro_spoken_by_engine(eng):

    class CustomIntroMock(MockProvider):

        KEY = "passage for a student"

        async def stream(self, messages: list, max_tokens: int = 250):

            users = [m.get("content", "") for m in messages if m.get("role") == "user"]

            last = users[-1] if users else ""

            if self.KEY in last:

                yield (

                    "INTRO: Welcome to today's reading practice! I have chosen a pleasant story for you. Read it out loud whenever you feel ready.\n"

                    "PASSAGE: Deep in the forest, an old stone cottage stood quietly under tall pine trees. "

                    "Birds sang from the branches, and a gentle stream flowed nearby."

                )

                return

            async for tok in super().stream(messages, max_tokens=max_tokens):

                yield tok

    s, sink = Session(id="t_ai_intro"), Sink()

    eng.llm_for = lambda sess: CustomIntroMock()

    async def go():

        await asyncio.wait_for(eng.start_reading(s, sink), timeout=3)

    asyncio.run(asyncio.wait_for(go(), timeout=5))

    tts = [m for m in sink.msgs if m["type"] == "tts_sentence"]

    assert tts and tts[0].get("turn", "").startswith("r1-intro")

    tts_all = " ".join(m["text"].lower() for m in tts)

    assert "pleasant story" in tts_all

    assert "cottage" not in tts_all  # passage is not in the spoken intro

    pmsg = sink.nth("reading_passage")

    assert pmsg and "deep in the forest" in pmsg["passage"].lower()

    assert "INTRO:" not in pmsg["passage"]

def test_intro_for_provides_diverse_natural_intros():

    p1 = "Maya wakes up early every morning to drink tea with her sister."

    p2 = "The old lighthouse on the rocky cliff flashed its bright white beam across the sea."

    intro1 = reading_mod.intro_for(p1, "B1")

    intro2 = reading_mod.intro_for(p2, "B1")

    assert len(intro1) > 10 and len(intro2) > 10

    assert "read" in intro1.lower() and "read" in intro2.lower()

    assert set(reading_mod.COACH_INTROS) >= {intro1, intro2}

