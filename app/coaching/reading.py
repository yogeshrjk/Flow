"""English Reading Practice: passage generation, word-level diff, and
evidence-bounded feedback prompts.

Design rules baked in here:
- Feedback may only claim what the evidence supports. The transcript is the
  only dependable evidence server-side, so word-level metrics (missing /
  extra / repeated / replaced words, WPM, pause stats) are computed here and
  handed to the LLM as FACTS. Pronunciation at the sound level needs audio
  evidence we do not extract, so the prompt forbids inventing it; only
  transcript-level word substitutions may be described as "heard differently".
"""

import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Passage difficulty tuning: ~100-150 words per the feature spec.
PASSAGE_MAX_WORDS = 150
PASSAGE_MIN_METRICS_WORDS = 3

READING_INSTRUCTION = "Read the passage aloud clearly at your own pace."
RETRY_INSTRUCTION = "Please read the passage again, focusing on speaking clearly and accurately."

READING_SYSTEM = """You are Flow's English reading coach in a live voice session.
A student Reads an English PASSAGE aloud; their SPOKEN TRANSCRIPT (from speech
recognition) is compared against it. Analyze reading and give SHORT, SIMPLE,
useful spoken feedback. Respond warmly, like a supportive human coach — no
headings syntax, no numbered lists, no markdown. Simple English, ok to add one
short Hindi clarification in brackets when a beginner needs it. NEVER narrate
your thinking."""

ROUND_INSTRUCTIONS = {
    1: READING_INSTRUCTION,
    2: RETRY_INSTRUCTION,
    3: RETRY_INSTRUCTION,
}

# Zero-cost offline fallbacks so Reading Practice still works without keys
# (mock tier) or when the LLM is down. Level-bucketed, 100-150 words each.
FALLBACK_PASSAGES = {
    "beginner": (
        "My name is Maya, and I live in a small flat with my sister. Every morning I wake up at "
        "seven o'clock and wash my face. Then I drink a cup of warm milk and eat two slices of bread. "
        "My sister likes tea, so she makes tea for both of us. I walk to the bus stop with her, and "
        "we talk about our plans for the day. She works at a bank, and I study at a college near my "
        "house. When I come back in the evening, I watch a little TV and help my sister cook dinner. "
        "We eat together at eight and go to bed early. Life is quite simple, but I am happy with it."
    ),
    "default": (
        "Every day before the sun rises, Arun takes his bicycle out and rides along the old river road. "
        "He knows every shop on the way: the small tea stall where he buys one biscuit, and the tailor "
        "who opens his shop at six and waves at him. The morning air is cool, and the road is empty, so "
        "Arun rides slowly and thinks about the day ahead. He works in a school, and he must reach there "
        "by half past eight. On the way, he meets his friend Babu, who sells fruit from a cart. Babu "
        "always gives him a ripe banana. Arun thanks him and rides on, feeling ready and calm. By the "
        "time the city begins to grow loud, he has already done something fine for himself today."
    ),
    "upper": (
        "When Priya decided to learn swimming at thirty-two, her friends were surprised. She had always "
        "been afraid of water after a small accident in her childhood, and she had quietly built her "
        "life around that fear. But last summer, standing by the pool and watching her nephew laugh and "
        "splash, she realized that the fear had taken too many mornings from her already. The first "
        "lesson was terrible: her legs sank, her breath disappeared, and the instructor shouted the "
        "same correction five times. Yet she returned the next day, and the next. Slowly her body began "
        "to trust the water. By autumn she could cross the pool without stopping, and although she was "
        "still not graceful, she was free. Some victories are silent, she says, but they change you."
    ),
}


@dataclass
class ReadingSession:
    """Server-side reading round state machine. One exists per session while
    Reading Practice is active; cleared on exit. `gen` increments on every
    (re)start so stale client submissions can never produce duplicate analysis."""
    state: str = "GENERATING_PASSAGE"
    passage: str = ""
    topic: str = ""
    round: int = 1
    gen: int = 0
    attempted: bool = False  # a passage was already requested for this gen
    transcripts: list = field(default_factory=list)  # [{round, text, stats}]
    started_at: float = field(default_factory=time.time)
    error: str = ""

    @property
    def active(self) -> bool:
        return bool(self.passage)


ACTIVE_RECORDING_STATES = ("WAITING_FOR_READING", "RECORDING_READING", "FINALIZING_TRANSCRIPT")


def _words(text: str) -> list[str]:
    """Word tokens: letters/digits kept, apostrophes kept, everything else
    dropped. Case is normalized by the caller."""
    t = re.sub(r"[^\w'\s]", " ", text or "")
    return [w for w in re.sub(r"\s+", " ", t).lower().split() if any(c.isalnum() for c in w)]


def passage_word_count(passage: str) -> int:
    return len(passage.split())


def _normalize_word(w: str) -> str:
    return w.lower().replace("'", "")


@dataclass
class WordDiff:
    missing: list
    extra: list
    repeated: list  # [word] spoken consecutively twice+
    replaced: list  # [(passage_word, heard_word)] aligned substitutions
    spoken_wc: int
    passage_wc: int
    accuracy: float  # fraction of passage words present in transcript (0..1)

    def is_empty(self) -> bool:
        return not (self.missing or self.extra or self.repeated or self.replaced)


def diff_passage(passage: str, transcript: str) -> WordDiff:
    """Evidence the coach may rely on: an alignment of passage words against
    the recognized transcript. Missing = in passage, absent where aligned.
    Extra/repeated = spoken without a passage source. Replaced = aligned
    pair where the recognized word differs (could be misread OR merely
    mistyped by the recognizer — the feedback prompt says which is safe)."""
    p = [_normalize_word(w) for w in _words(passage)]
    s = [_normalize_word(w) for w in _words(transcript)]
    ms = SequenceMatcher(None, p, s)
    missing, extra, repeated, replaced = [], [], [], []
    prev_last = None
    for tag, i1, i2, j1, j2 in ms.get_opcodes():
        if tag == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                pw = p[i1 + k] if i1 + k < i2 else None
                sw = s[j1 + k] if j1 + k < j2 else None
                if pw and sw and pw != sw:
                    replaced.append((pw, sw))
                elif pw and not sw:
                    missing.append(pw)
                elif sw and not pw:
                    extra.append(sw)
        elif tag == "delete":
            missing.extend(p[i1:i2])
        elif tag == "insert":
            extra.extend(s[j1:j2])
        elif tag == "equal":
            pass
    # repeated-word detection: consecutive repeats AND words spoken more times than in passage
    seen_in_transcript = set()
    prev_w = None
    for w in s:
        if len(w) >= 1:
            if w == prev_w and w not in repeated:
                repeated.append(w)
            elif w in seen_in_transcript and s.count(w) > p.count(w) and w not in repeated:
                repeated.append(w)
        seen_in_transcript.add(w)
        prev_w = w
    # accuracy: transcript words that align to the passage
    equal = sum(i2 - i1 for tag, i1, i2, j1, j2 in ms.get_opcodes() if tag == "equal")
    accuracy = round(equal / len(p), 3) if p else 0.0
    return WordDiff(missing, extra, repeated, replaced, len(s), len(p), accuracy)


def analyze_pauses(passage: str, transcript: str, pauses: list, diff: WordDiff) -> dict:
    """Analyze measured pauses against the expected passage and word diff.
    
    Classifies pauses into:
    - sentence_boundary: pause occurring at terminal punctuation (., ?, !)
    - clause_boundary: pause occurring at minor punctuation (,, ;, :, --)
    - mid_sentence: pause occurring inside a clause/sentence without punctuation
    
    Identifies unnecessary pauses vs natural pauses:
    - Natural sentence pauses (0.4s - 1.8s) are normal and NOT penalized.
    - Long sentence pauses (> 1.8s) indicate losing place between sentences.
    - Natural clause pauses (0.4s - 1.2s) are normal phrasing.
    - Mid-sentence pauses before difficult/mispronounced/replaced words are classified as hesitations.
    - Mid-sentence pauses >= 1.0s are classified as unnecessary pauses.
    - Mid-sentence pauses < 1.0s are reported as possible hesitations rather than confirmed errors.
    """
    if not pauses:
        return {
            "total_pauses": 0,
            "significant_pauses_count": 0,
            "sentence_boundary_pauses_count": 0,
            "clause_boundary_pauses_count": 0,
            "mid_sentence_pauses_count": 0,
            "hesitation_points_count": 0,
            "significant_pauses": [],
            "hesitation_points": [],
            "pause_suggestions": [],
            "all_pauses": [],
        }

    raw_tokens = passage.split()
    passage_tokens = []
    for tok in raw_tokens:
        clean = re.sub(r"[^\w'-]", "", tok).lower()
        punct = re.sub(r"[\w'-]", "", tok)
        if clean:
            passage_tokens.append((clean, punct))

    replaced_spoken = {s.lower() for _, s in diff.replaced if s}
    replaced_passage = {p.lower() for p, _ in diff.replaced if p}
    all_replaced = replaced_spoken | replaced_passage
    repeated_words = {w.lower() for w in diff.repeated}
    missing_words = {w.lower() for w in diff.missing}

    analyzed = []
    sentence_pauses = 0
    clause_pauses = 0
    mid_sentence_pauses = 0
    hesitations = []

    for p in pauses:
        dur = float(p.get("duration_s") or 0.0)
        after_w = str(p.get("after_word") or "").strip().lower()
        before_w = str(p.get("before_word") or "").strip().lower()
        if dur < 0.35:
            continue

        boundary = "mid_sentence"
        matching_punct = ""
        for clean, punct in passage_tokens:
            if clean == after_w:
                matching_punct = punct
                break

        if any(c in matching_punct for c in (".", "?", "!")):
            boundary = "sentence"
        elif any(c in matching_punct for c in (",", ";", ":", "-")):
            boundary = "clause"

        is_natural = False
        classification = "unnecessary_mid_sentence_pause"
        desc = ""

        if boundary == "sentence":
            sentence_pauses += 1
            if dur <= 1.8:
                is_natural = True
                classification = "natural_sentence_pause"
                desc = f"Natural pause after sentence ({dur}s)"
            else:
                classification = "long_sentence_pause"
                desc = f"Long pause after '{after_w}' ({dur}s)"
                hesitations.append(f"long {dur}s pause between sentences after '{after_w}'")
        elif boundary == "clause":
            clause_pauses += 1
            if dur <= 1.2:
                is_natural = True
                classification = "natural_clause_pause"
                desc = f"Natural breath pause at comma ({dur}s)"
            else:
                classification = "long_clause_pause"
                desc = f"Extended pause after '{after_w}' ({dur}s)"
                hesitations.append(f"extended {dur}s pause after '{after_w}'")
        else:
            mid_sentence_pauses += 1
            if before_w and (before_w in all_replaced or before_w in repeated_words or len(before_w) >= 7):
                classification = "hesitation_before_word"
                desc = f"Hesitation before '{before_w}' ({dur}s)"
                hesitations.append(f"{dur}s hesitation before '{before_w}'")
            elif dur >= 1.0:
                classification = "unnecessary_mid_sentence_pause"
                target_desc = f"between '{after_w}' and '{before_w}'" if after_w and before_w else f"after '{after_w}'"
                desc = f"Unnecessary pause {target_desc} ({dur}s)"
                hesitations.append(f"{dur}s pause {target_desc}")
            else:
                classification = "possible_hesitation"
                target_desc = f"between '{after_w}' and '{before_w}'" if after_w and before_w else f"after '{after_w}'"
                desc = f"Brief pause {target_desc} ({dur}s)"

        analyzed.append({
            "duration_s": dur,
            "after_word": after_w,
            "before_word": before_w,
            "boundary": boundary,
            "classification": classification,
            "is_natural": is_natural,
            "description": desc,
        })

    suggestions = []
    if mid_sentence_pauses >= 2:
        suggestions.append("Try to link words together smoothly inside each sentence rather than pausing.")
    elif hesitations:
        first_hes = hesitations[0]
        suggestions.append(f"Practice reading phrases smoothly to reduce hesitations like the {first_hes}.")
    elif sentence_pauses > 0:
        suggestions.append("Great job pausing naturally at sentence boundaries!")
    elif len(raw_tokens) > 25 and sentence_pauses == 0:
        suggestions.append("Take a brief, natural breath pause when you reach a period at the end of a sentence.")

    significant = [a for a in analyzed if a["duration_s"] >= 0.6]

    return {
        "total_pauses": len(analyzed),
        "significant_pauses_count": len(significant),
        "sentence_boundary_pauses_count": sentence_pauses,
        "clause_boundary_pauses_count": clause_pauses,
        "mid_sentence_pauses_count": mid_sentence_pauses,
        "hesitation_points_count": len(hesitations),
        "significant_pauses": significant,
        "hesitation_points": hesitations[:5],
        "pause_suggestions": suggestions[:2],
        "all_pauses": analyzed,
    }


def summarize_stats(diff: WordDiff, duration_s: float | None,
                    pause_total_s: float | None = None,
                    long_pause_count: int | None = None,
                    elapsed_s: float | None = None,
                    pauses: list | None = None,
                    pause_analysis: dict | None = None) -> dict:
    """Measured numbers only. WPM requires both duration and a word count;
    pause data requires the client timer to have measured it — None means
    the coach must not claim it."""
    d = {
        "missing": diff.missing[:20],
        "extra": diff.extra[:20],
        "repeated": diff.repeated[:10],
        "replaced": diff.replaced[:20],
        "spoken_word_count": diff.spoken_wc,
        "passage_word_count": diff.passage_wc,
        "word_accuracy": diff.accuracy,
    }
    wpm: float | None = None
    if duration_s and duration_s > 0 and diff.spoken_wc >= PASSAGE_MIN_METRICS_WORDS:
        wpm = round(diff.spoken_wc / (duration_s / 60.0), 1)

    speaking_dur = None if not duration_s else round(duration_s, 1)
    elapsed_dur = None if not elapsed_s else round(elapsed_s, 1)
    d["duration_s"] = speaking_dur
    d["speaking_duration_s"] = speaking_dur
    d["elapsed_duration_s"] = elapsed_dur
    d["words_per_minute"] = wpm

    pa = pause_analysis or {}
    total_pause_dur = round(pause_total_s, 1) if pause_total_s is not None else None
    d["pause_total_s"] = total_pause_dur
    if pause_total_s is not None or pause_analysis:
        d["long_pauses"] = pa.get("significant_pauses_count", long_pause_count or 0)
        d["total_pauses"] = pa.get("total_pauses", 0 if pause_total_s is None else 1)
        d["significant_pauses_count"] = d["long_pauses"]
    else:
        d["long_pauses"] = None
        d["total_pauses"] = None
        d["significant_pauses_count"] = None
    d["sentence_boundary_pauses_count"] = pa.get("sentence_boundary_pauses_count", 0)
    d["clause_boundary_pauses_count"] = pa.get("clause_boundary_pauses_count", 0)
    d["mid_sentence_pauses_count"] = pa.get("mid_sentence_pauses_count", 0)
    d["hesitation_points_count"] = pa.get("hesitation_points_count", 0)
    d["significant_pauses"] = pa.get("significant_pauses", [])
    d["hesitation_points"] = pa.get("hesitation_points", [])
    d["pause_suggestions"] = pa.get("pause_suggestions", [])
    return d


def feedback_messages(diff: WordDiff, passage: str, transcript: str, round_no: int = 1,
                      duration_s: float | None = None, pause_total_s: float | None = None,
                      long_pause_count: int | None = None, level: str = "B1",
                      history: list | None = None,
                      elapsed_s: float | None = None,
                      pause_analysis: dict | None = None,
                      is_accurate: bool = False) -> list[dict]:
    stats = summarize_stats(diff, duration_s, pause_total_s, long_pause_count,
                            elapsed_s=elapsed_s, pause_analysis=pause_analysis)
    has_speed = stats["words_per_minute"] is not None
    has_pause = stats["pause_total_s"] is not None
    rules = [
        "EVIDENCE RULES — obey strictly:",
        "- The transcript is speech-recognition output; treat TINY recognizer quirks (maybe 'a' vs 'the') cautiously.",
        "- MISSING/EXTRA/REPLACED words listed under MEASURED are real transcript evidence: you may report them.",
        f"- Speaking speed: {'given, report it briefly' if has_speed else 'NOT measured — do NOT mention speed at all'}.",
        f"- Pauses: {'given, you may describe them briefly' if has_pause else 'NOT measured — do NOT mention pauses'}.",
        "- Sound-level pronunciation quality has NO audio evidence here. You may only point at REPLACED words as 'heard differently'. NEVER invent pronunciation scores, fake mispronunciations, or percentage grades.",
        "- Never claim to have heard audio. Never give grades or scores.",
        "- DO NOT mention round numbers (do NOT say 'round 1' or 'round 2').",
    ]
    if is_accurate:
        instruction = (
            "The student read the passage near accurately and clearly! "
            "Praise their reading warmly and highlight their good flow and clarity in 1-2 simple sentences (under 35 words)."
        )
    else:
        instruction = (
            "The student's reading was not near perfect yet. "
            "Gently mention the main missing or replaced words if any, and warmly ask them to read the passage again until it is clear and accurate (under 45 words)."
        )
    user = (
        f"PASSAGE TO READ ALOUD:\n{passage}\n\n"
        f"STUDENT'S SPOKEN TRANSCRIPT:\n{transcript or '(nothing recognizable was heard)'}\n\n"
        f"MEASURED (computed from this transcript and timing): {stats}\n\n"
        f"Student level: about {level}.\n"
        + "\n".join(rules)
        + f"\n\n{instruction}"
    )
    return [
        {"role": "system", "content": READING_SYSTEM},
        {"role": "user", "content": user},
    ]


def summary_messages(passage: str, rounds: list, level: str = "B1") -> list[dict]:
    rnd = []
    for r in rounds:
        rnd.append(f"ROUND {r.get('round')}: transcript={r.get('text', '')[:600]} "
                   f"stats={r.get('stats')}")
    user = (
        f"PASSAGE read three times:\n{passage}\n\n{chr(10).join(rnd)}\n\n"
        "Write the CLOSING SUMMARY of this reading session: 1) the main improvements "
        "across the three rounds (mention round numbers), 2) THREE priority areas to keep "
        "practising, each in a few simple words. Plain flowing sentences, no headings, no "
        "lists. Simple English, supportive, under 80 words."
    )
    return [
        {"role": "system", "content": READING_SYSTEM},
        {"role": "user", "content": user},
    ]


COACH_INTROS = [
    "Welcome to reading practice! Here is a short passage for you — whenever you're ready, take your time and read it aloud.",
    "Let's do some reading practice together. Take a look at this story, and start reading aloud at your own pace.",
    "I've got a nice reading practice passage ready for you today. Take a comfortable breath and read it aloud whenever you feel ready.",
    "Here is today's reading practice passage. Go ahead and read it aloud when you're ready, focusing on speaking clearly.",
    "Let's practice your spoken flow with this short reading practice passage. Whenever you're ready, begin reading aloud.",
]


def passage_messages(level: str, topic_hint: str = "", exclude: list | None = None) -> list[dict]:
    rule = (
        f"You are the AI English coach preparing a reading practice session for a student (level {level}).\n"
        "1. First, decide how you want to interact with the student to start this session. "
        "Write a brief, warm, natural spoken coach intro (1-2 sentences) introducing the passage topic "
        "and inviting them to read it aloud at their own pace.\n"
        f"2. Then write ONE short English passage for a student (level {level}) to read aloud, "
        f"{PASSAGE_MAX_WORDS // 2}-{PASSAGE_MAX_WORDS} words, exactly 3-6 simple sentences. "
        "Topic: " + (topic_hint or "daily life, routines, travel, family, work, or small habits") + ". "
        "Use common everyday words and natural sentences; no difficult vocabulary, no names "
        "that are hard to read, no dialogue quotes, no lists, no title.\n\n"
        "Format your output as:\n"
        "INTRO: <your natural spoken intro here>\n"
        "PASSAGE: <the passage text here>"
    )
    excl = "\n".join("Do not reuse: " + e[:80] for e in (exclude or [])[-3:]) if exclude else ""
    return [
        {"role": "system", "content": READING_SYSTEM},
        {"role": "user", "content": rule + (("\n" + excl) if excl else "")},
    ]


def intro_for(passage: str, level: str) -> str:
    """Diverse fallback coach intros for reading practice when LLM intro is unavailable."""
    import hashlib
    h = int(hashlib.md5((passage or "").encode("utf-8")).hexdigest(), 16)
    return COACH_INTROS[h % len(COACH_INTROS)]


def fallback_passages_for(level: str, exclude: list | None = None) -> str:
    ex = [e[:40] for e in (exclude or [])]
    for key in ("beginner", "default", "upper"):
        cand = FALLBACK_PASSAGES[key]
        if not any(cand.startswith(x) for x in ex):
            return cand
    return FALLBACK_PASSAGES["default"]
