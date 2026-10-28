"""Conversation engine: turn-taking, streaming, barge-in, coaching wiring."""
import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field

from app.config import settings, normalize_response_mode
from app.conversation.emotioncontrol import mask_cues, trim_incomplete_cue
from app.conversation.prompts import build_system
from app.coaching import correction as corr
from app.coaching.level import estimate_level
from app.coaching.confidence import confidence_score
from app.coaching.memory import load_profile, save_profile, update_after_turn, record_session
from app.coaching.summary import build_summary
from app.coaching.roleplay import SCENARIOS, today_challenge
from app.coaching import reading as reading_mod
from app.conversation.intent import (
    UserIntent,
    clean_speech_artifacts,
    classify_user_intent,
    detect_language_switch_request,
    detect_language_from_content,
    has_trailing_hesitation,
    is_audio_check,
    is_casual_chat_request,
)
from app.latency import format_block
from app.search import needs_search, pick_filler, search_facts
from app.providers.llm.base import LLMProvider
from app.providers.llm.gemini import GeminiChainProvider
from app.providers.llm.groq import GroqProvider
from app.providers.llm.base import UnavailableProvider

# Response tiers behind the friendly UI labels. Internal names never reach the UI.
#   "fast"    -> Groq      (lowest time-to-first-token, short conversational turns)
#   "quality" -> Gemini    (better coaching quality, still streaming)
TIERS = ("groq_fish", "gemini_fish", "gemini_live", "fast", "quality", "live")

log = logging.getLogger("engine")

# Upper bound for one Live turn (connect + full stream). A hung Bidi socket
# must never wedge a turn forever: expiry surfaces as error + fallback, and
# the next turn reconnects fresh.
TURN_TIMEOUT_S = 60.0

SENT_END = re.compile(r"(.+?[.!?]+)(?=\s|$)")

# First-chunk fast path: the voice starts on the opening thought without waiting
# for the full response. Tiny leads (< 28 chars like "Perfect!", "Right on.")
# are merged with the sentence that follows so TTS prosody is natural and does not
# starve the audio queue with an awkward dead-air pause.
FIRST_SENT_MIN = 18
FIRST_CLAUSE_MIN = 24
FIRST_BOUNDARY = re.compile(r"[.!?]+(?=\s|$)|[,;:](?=\s)")


def first_chunk_split(text: str) -> tuple[str, str] | None:
    """Earliest speakable opening chunk. Returns (chunk, rest) or None.
    Avoids chopping off tiny leads ('Perfect!', 'Right on.', 'Good!') alone,
    which causes TTS starvation and unnatural pauses.

    Split points inside a [fish cue] are ignored: the cue is voice direction,
    so cutting "[long-" from "break]" would ship broken markup to Fish Audio."""
    text = text or ""
    outside, _ = mask_cues(text)
    for m in FIRST_BOUNDARY.finditer(text):
        if not outside[m.start()]:
            continue
        head = text[: m.end()].strip()
        if not head:
            continue
        ch = m.group()[0]
        if ch in ".!?" and len(head) >= FIRST_SENT_MIN:
            return head, text[m.end():].strip()
        if ch in ",;:" and len(head) >= FIRST_CLAUSE_MIN:
            return head, text[m.end():].strip()
    return None


def chunk_sentences(buffer: str) -> tuple[list[str], str]:
    """Split complete sentences; merge tiny interjections so TTS doesn't sound choppy.
    Never splits inside a [fish cue]: an emotion/pause tag belongs to the words
    around it, so it must reach Fish Audio whole."""
    out: list[str] = []
    rest = buffer
    while True:
        outside, _ = mask_cues(rest)
        idx = -1
        for i, ch in enumerate(rest):
            if ch in ".!?":
                if not outside[i]:
                    continue
                # skip abbreviations / decimals: letter-dot-letter or digit-dot-digit
                prev = rest[i - 1] if i > 0 else " "
                nxt = rest[i + 1] if i + 1 < len(rest) else " "
                if prev.isalnum() and nxt.isalnum() and ch == ".":
                    continue
                out.append(rest[: i + 1].strip())
                rest = rest[i + 1 :].strip()
                idx = i
                break
        if idx == -1:
            break
        if len(out) > 8:
            break
    # merge a tiny lead ("Hey!", "Nice.", "Oh.", "Right.") with what follows so voice flows
    if out and len(out[0]) < 12:
        if len(out) > 1:
            out[0] = f"{out[0]} {out.pop(1)}"
        elif rest:
            out[0] = (out[0] + " " + rest.split(".")[0]).strip() if "." in rest else (out[0] + " " + rest)
            rest = "" if "." not in rest else ".".join(rest.split(".")[1:]).strip()
    # Only flush complete sentences. Never split mid-sentence on commas —
    # comma-split chunks each become a separate TTS request with a pause,
    # which sounds slow and robotic after every comma.
    # Long buffers flush whole at llm_done; threshold raised to avoid chopping.
    if not out and len(rest) > 280:
        for sep in [" and ", " but ", " so ", " because "]:
            if sep in rest:
                parts = rest.split(sep, 1)
                out = [parts[0]]
                rest = sep.strip() + " " + parts[1]
                break
    return out, rest


def clean_for_speech(text: str) -> str:
    """Strip markdown/lists/emoji so TTS sounds spoken, not read.
    Emotion tags like [chuckle], [happy], [emphasis] and the pause markers
    [break]/[long-break] are KEPT — Fish Audio interprets them as voice
    direction markers. Only a truncated, unterminated cue is dropped, so a
    partial tag can never reach the TTS request."""
    import re as _re
    t = _re.sub(r"```.*?```", " ", text, flags=_re.S)
    t = _re.sub(r"^#{1,6}\s+", "", t, flags=_re.M)
    t = _re.sub(r"^\s*[-*•\d]+\s*[.)]\s+", "", t, flags=_re.M)
    t = _re.sub(r"\*\*(.*?)\*\*", r"\1", t)
    t = _re.sub(r"\*+", "", t)  # stray marker when a **pair** straddles a chunk split
    t = _re.sub(r"__(.*?)__", r"\1", t)
    t = _re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = "".join(c for c in t if not (0x1F300 <= ord(c) <= 0x1FAFF or ord(c) in (0x200D, 0xFE0F)))
    t = trim_incomplete_cue(t)
    t = _re.sub(r"\s+", " ", t).strip()
    return t


def strip_emotion_tags(text: str) -> str:
    """Remove Fish Audio emotion/delivery/pause cues for on-screen display.
    Ordinary fillers ("hmm", "ugh", "well") are plain words, not cues, so they
    stay in the display text. Keeps **emphasis** markers (handled separately by
    the client's emphasize() function for visual highlighting). Also strips any
    stray or unmatched brackets so no raw '[' or ']' can leak onto the screen."""
    import re as _re
    t = trim_incomplete_cue(text or "")
    t = _re.sub(r"\[[^\]]*\]", "", t)
    t = _re.sub(r"[\[\]]", "", t)
    t = _re.sub(r"\s+", " ", t).strip()
    return t


def sanitize_error(err_str: str) -> str:
    """Sanitize error messages to ensure no API keys or Bearer tokens are logged or returned."""
    import re as _re
    s = str(err_str or "")
    s = _re.sub(r"Bearer\s+[A-Za-z0-9_\-\.]+", "Bearer [REDACTED]", s, flags=_re.IGNORECASE)
    s = _re.sub(r"(key|token|secret|authorization)=['\"][^'\"]+['\"]", r"\1=[REDACTED]", s, flags=_re.IGNORECASE)
    return s[:300]


def memory_hint_for(profile: dict, s: "Session") -> str:
    bits = []
    g = profile.get("grammar", {}) or {}
    top = sorted(g.items(), key=lambda kv: kv[1], reverse=True)[:2]
    if top and top[0][1] >= 2:
        bits.append(f"repeated slips: {', '.join(k.replace('_',' ') for k,_ in top)}")
    topics = (profile.get("topics", []) or [])[-3:]
    if topics:
        bits.append(f"they care about: {', '.join(topics)}")
    m = s.mode if s.mode in ("free", "reading", "training", "roleplay", "listening") else "free"
    mode_sum = s.mode_summaries.get(m, "")
    has_history = bool(s.mode_histories.get(m) or s.turns)
    if mode_sum and has_history:
        bits.append(f"previous conversations in this {m} mode: user discussed '{mode_sum}' — feel free to reference this naturally (e.g. 'Last time we talked about...', 'Continuing from our last discussion...') or continue where you left off")
    elif getattr(s, "past_chat_summary", "") and has_history:
        bits.append(f"previous conversations: user mentioned '{s.past_chat_summary}' — feel free to reference this naturally (e.g. 'I remember you mentioned...', 'Last time we discussed...') or ask if they'd like to continue where you left off")
    if s.hindi_used:
        bits.append("sometimes slips into Hindi — support, don't lecture")
    last_ai = next((t.get("text","") for t in reversed(s.turns) if t.get("role")=="assistant"), "")
    if last_ai:
        bits.append(f"previous turn you said: {last_ai[:100]!r} — do not repeat its greeting, opener, praise, or question")
    return "; ".join(bits) if bits else ""


def detect_hindi(text: str) -> bool:
    return bool(re.search(r"[\u0900-\u097F]", text)) or bool(
        re.search(r"\b(mujhe|tumhe|aapko|thoda|bahut|kya|kaise|accha|nahi|haan)\b", text.lower())
    )


class SentenceCoalescer:
    """Merge a short sentence with the one that follows so they become ONE TTS
    request. Two requests = network gap mid-thought ("Short and sweet! ... [pause]
    ... Did you..."), one request = natural continuous prosody.

    Short sentences are held (not delayed by timers — the next tokens arrive
    within milliseconds during streaming) and flushed at stream end.
    """

    HOLD_UNDER = 35  # sentences shorter than this wait for a partner
    MAX_HOLD = 110  # never hold more than this; flush instead

    def __init__(self):
        self.held = ""

    def feed(self, sentence: str) -> list[str]:
        s = (sentence or "").strip()
        if not s:
            return []
        if len(s) < self.HOLD_UNDER:
            self.held = f"{self.held} {s}".strip() if self.held else s
            if len(self.held) >= self.MAX_HOLD:
                out, self.held = self.held, ""
                return [out]
            return []
        if self.held:
            s = f"{self.held} {s}"
            self.held = ""
        return [s]

    def flush(self, tail: str = "") -> list[str]:
        t = f"{self.held} {(tail or '').strip()}".strip()
        self.held = ""
        return [t] if t else []


@dataclass
class Session:
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_active: float = field(default_factory=time.monotonic)
    speech_seconds: float = 0.0
    turn_speech_seconds: float | None = None
    ended_summary: dict | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    user_id: str = "default"
    mode: str = "free"
    correction: str = "balanced"
    level_setting: str = "auto"
    level: str = "B1"
    language: str = "english"
    last_spoken_lang: str = ""  # detected per utterance; reply follows it, falls back to language
    voice_id: str = ""  # selected Fish voice; persona follows it
    scenario: str = "casual"
    challenge: dict = field(default_factory=dict)
    mistake_counts: dict = field(default_factory=dict)
    hindi_used: bool = False
    start_t: float = field(default_factory=time.time)
    words_spoken: int = 0
    response_mode: str = ""  # "fast" | "quality"; "" = server default
    cancelled: bool = False
    reading: "reading_mod.ReadingSession | None" = None
    roleplay_scenario_title: str = ""
    roleplay_role: str = ""
    roleplay_started: bool = False
    mode_histories: dict[str, list[dict]] = field(default_factory=lambda: {
        "free": [], "reading": [], "training": [], "roleplay": []
    })
    mode_turns: dict[str, list[dict]] = field(default_factory=lambda: {
        "free": [], "reading": [], "training": [], "roleplay": []
    })
    mode_summaries: dict[str, str] = field(default_factory=lambda: {
        "free": "", "reading": "", "training": "", "roleplay": ""
    })

    @property
    def history(self) -> list[dict]:
        m = self.mode if self.mode in ("free", "reading", "training", "roleplay", "listening") else "free"
        return self.mode_histories.setdefault(m, [])

    @history.setter
    def history(self, val: list[dict]) -> None:
        m = self.mode if self.mode in ("free", "reading", "training", "roleplay", "listening") else "free"
        self.mode_histories[m] = val

    @property
    def turns(self) -> list[dict]:
        m = self.mode if self.mode in ("free", "reading", "training", "roleplay", "listening") else "free"
        return self.mode_turns.setdefault(m, [])

    @turns.setter
    def turns(self, val: list[dict]) -> None:
        m = self.mode if self.mode in ("free", "reading", "training", "roleplay", "listening") else "free"
        self.mode_turns[m] = val

    @property
    def past_chat_summary(self) -> str:
        m = self.mode if self.mode in ("free", "reading", "training", "roleplay", "listening") else "free"
        return self.mode_summaries.get(m, "")

    @past_chat_summary.setter
    def past_chat_summary(self, val: str) -> None:
        m = self.mode if self.mode in ("free", "reading", "training", "roleplay", "listening") else "free"
        self.mode_summaries[m] = val


class Engine:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.reading_tasks: dict[str, asyncio.Task] = {}
        self.reading_acks: dict[str, asyncio.Event] = {}
        self.greeting_tasks: dict[str, asyncio.Task] = {}
        # one provider instance per response tier, built once (keep-alive clients)
        self.providers: dict[str, LLMProvider] = {}
        if settings.has_groq:
            gp = GroqProvider()
            self.providers["groq_fish"] = gp
            self.providers["fast"] = gp
            log.info(f"[LLM] Groq+Fish tier ready {gp.models}")
        else:
            log.info("[LLM] Groq tier unavailable (no GROQ_API_KEY)")
        if settings.has_gemini:
            gcp = GeminiChainProvider()
            self.providers["gemini_fish"] = gcp
            self.providers["quality"] = gcp
            log.info(f"[LLM] Gemini+Fish tier ready {gcp.models}")

            glive = GeminiChainProvider()
            self.providers["gemini_live"] = glive
            self.providers["live"] = glive
            log.info(f"[LLM] Gemini Live tier ready {glive.models}")
        else:
            log.info("[LLM] Gemini tiers unavailable (no GEMINI_API_KEY)")
        self.fallback_chains = {}
        self.unavailable = UnavailableProvider()
        self.llm = self.llm_for(None)

    def sync_past_history(self, s: Session, turns: list[dict], mode: str = "") -> None:
        """Digest past conversation turns from local storage and update session memory for the mode."""
        if not turns:
            return
        m = mode or s.mode or "free"
        if m not in ("free", "reading", "training", "roleplay", "listening", "practice"):
            return
        turns = [t for t in turns[-40:] if isinstance(t, dict) and isinstance(t.get("content", t.get("text", "")), str)]
        hist = s.mode_histories.setdefault(m, [])
        mturns = s.mode_turns.setdefault(m, [])
        if not hist:
            for t in turns[-12:]:
                c = (t.get("content") or t.get("text") or "").strip()
                r = t.get("role") or ("user" if t.get("who") == "You" else "assistant")
                if c and r in ("user", "assistant"):
                    c = c[:2000]
                    hist.append({"role": r, "content": c})
                    mturns.append({"role": r, "text": c, "t": time.time()})
        user_lines = []
        for t in turns:
            c = (t.get("content") or t.get("text") or "").strip()
            role = t.get("role") or t.get("who") or ""
            if role.lower() in ("user", "you") and len(c.split()) >= 3:
                user_lines.append(c)
        if user_lines:
            summary = "; ".join(user_lines[-4:])[:300]
            s.mode_summaries[m] = summary
            profile = load_profile(settings.data_dir, s.user_id)
            if "mode_summaries" not in profile:
                profile["mode_summaries"] = {}
            profile["mode_summaries"][m] = summary
            profile["last_chat_summary"] = summary
            save_profile(settings.data_dir, profile)
            log.info(f"[MEMORY] synced past chat summary for {s.id} mode={m}: {summary[:100]!r}")

    def llm_for(self, s: "Session | None") -> LLMProvider:
        mode = normalize_response_mode(getattr(s, "response_mode", "") or settings.default_response_mode)
        return self.providers.get(mode, self.unavailable)

    def mode_available(self, mode: str) -> bool:
        mode = normalize_response_mode(mode)
        return bool(self.providers.get(mode)) and (mode == "gemini_live" or settings.has_fish)

    def mode_error(self, mode: str) -> str:
        mode = normalize_response_mode(mode)
        missing = []
        if not self.providers.get(mode):
            missing.append("GROQ_API_KEY" if mode == "groq_fish" else "GEMINI_API_KEY")
        if mode != "gemini_live" and not settings.has_fish:
            missing.append("FISH_API_KEY")
        return "Add " + " and ".join(missing) + " in the server .env, then restart Flow."

    def get_or_create(self, sid: str | None, user_id: str = "") -> Session:
        from app.coaching.memory import validate_id
        if sid:
            validate_id(sid)
        now = time.monotonic()
        for old_id, old in list(self.sessions.items()):
            if now - old.last_active > settings.session_ttl_s and old_id not in self.tasks and not getattr(old, "_connected", False):
                self.sessions.pop(old_id, None)
        if len(self.sessions) >= settings.max_sessions and sid not in self.sessions:
            raise RuntimeError("Server session capacity reached; retry later")
        if sid and sid in self.sessions:
            existing = self.sessions[sid]
            if user_id and existing.user_id != user_id:
                raise PermissionError("Session belongs to a different browser")
            existing.last_active = now
            return existing
        s = Session(id=sid or uuid.uuid4().hex, user_id=user_id or sid or uuid.uuid4().hex,
                    mode=settings.default_mode, correction=settings.default_correction,
                    level_setting=settings.default_level,
                    response_mode=settings.default_response_mode)
        profile = load_profile(settings.data_dir, s.user_id)
        s.level = profile.get("level", "B1")
        self.sessions[s.id] = s
        return s

    def llm_messages(self, s: Session, scenario_text: str = "", challenge_text: str = "", extra_nudge: str = "",
                       language: str = "") -> list[dict]:
        from app.voices import persona_for
        profile = load_profile(settings.data_dir, s.user_id)
        mem = memory_hint_for(profile, s)
        pname, pgender = persona_for(s.voice_id or None)
        sys = build_system(s.mode, s.level, s.level_setting, s.correction,
                           scenario=scenario_text or s.scenario, challenge=challenge_text,
                           memory_hint=mem, language=language or s.language,
                           persona_name=pname, persona_gender=pgender)
        from app.coaching.learning import daily_plan
        plan = daily_plan(profile)
        sys += ("\nEnglish learning goal: help the learner answer in complete sentences. "
                "Invite a short example, naturally recast one recurring error, and periodically revisit it. "
                "Use clear English at the learner level; offer native-language support when requested. "
                "Never claim to measure pronunciation from transcript alone or promise fluency by a deadline. "
                f"Current practice focus: {plan['focus']}. Assessment: {plan['assessment']['status']}.")
        if s.mode == "listening":
            sys += "\nListening coaching: discuss the listening exercise, explain expressions, then invite a short shadowing attempt. Never invent comprehension scores or claim to hear an accent from text."
        if s.response_mode not in ("gemini_live", "live"):
            from app.conversation.emotioncontrol import build_emotion_control_prompt
            sys = sys.replace(build_emotion_control_prompt(), "Voice delivery: use natural conversational pacing. Optional sparse Fish cues: [calm], [happy], [empathetic], [curious], [break]. Never overact or tag every sentence. Use plain spoken words for fillers.")
        # single system message: fold correction nudge in (two system msgs confuse some models)
        if extra_nudge:
            sys += "\nThis turn: " + extra_nudge
        # never repeat yourself: include last assistant line explicitly
        msgs = [{"role": "system", "content": sys}]
        # keep last 16 turns for context (free-tier token safety)
        msgs += s.history[-16:]
        return msgs

    async def greeting(self, s: Session) -> str:
        """AI-decided welcome line (never hardcoded). Falls back to neutral hello."""
        try:
            from app.conversation.prompts import LANGUAGES
            from app.voices import persona_for
            from app.coaching.roleplay import SCENARIOS
            profile = load_profile(settings.data_dir, s.user_id)
            mem = memory_hint_for(profile, s)
            pname, pgender = persona_for(s.voice_id or None)
            sys = build_system(s.mode, s.level, s.level_setting, s.correction,
                               scenario=s.scenario, challenge="", memory_hint=mem,
                               language=s.language,
                               persona_name=pname, persona_gender=pgender)
            lang_name = LANGUAGES.get(s.language, "English")
            m = s.mode
            mode_hist = s.mode_histories.get(m, [])
            prior_summary = s.mode_summaries.get(m, "")
            has_prior = bool(prior_summary and len(mode_hist) >= 2)
            if s.mode == "training":
                from app.coaching.training import get_active_lesson, is_lesson_mastered
                lesson = get_active_lesson(profile)
                mastered = is_lesson_mastered(profile, lesson["id"])
                if has_prior:
                    user_prompt = (
                        f"You are {pname}, the AI English Trainer. Welcome the learner back warmly in {lang_name} to their English Training session. "
                        f"We are continuing our topic: {lesson['title']} ({lesson['concept']}). Acknowledge where we left off, "
                        f"give another natural example or practice prompt: \"{lesson['exercise_prompt']}\". Under 45 words, natural spoken speech."
                    )
                else:
                    user_prompt = (
                        f"You are {pname}, the AI English Trainer. Welcome the learner warmly in {lang_name} to their English Training session. "
                        f"Introduce our current topic: {lesson['title']} ({lesson['concept']}). Give the example: \"{lesson['example']}\", "
                        f"and invite them to try: \"{lesson['exercise_prompt']}\". Under 45 words, natural spoken speech."
                    )
            elif s.mode == "roleplay":
                if s.roleplay_started and s.roleplay_scenario_title:
                    user_prompt = (
                        f"You are {pname}. Welcome the user back warmly in {lang_name} to Roleplay mode. "
                        f"Pick up where we left off in our scenario: {s.roleplay_scenario_title} (your role: {s.roleplay_role}). Under 35 words, natural spoken speech."
                    )
                elif has_prior:
                    user_prompt = (
                        f"You are {pname}. Welcome the user back warmly in {lang_name} to Roleplay mode. "
                        f"Acknowledge our previous roleplay discussions ('{prior_summary}'), and ask if they would like to continue where we left off or talk through a new topic. Under 35 words."
                    )
                else:
                    user_prompt = (
                        f"You are {pname}. Welcome the user warmly in {lang_name} to Roleplay mode. "
                        "Invite them to talk and decide together on a scenario or situation they would like to practice today "
                        "(e.g., job interview, ordering at a cafe, catching up with an old friend, or any custom scenario they have in mind). "
                        "Ask what situation they'd like to try. Under 35 words, natural spoken speech."
                    )
            elif s.mode == "practice" and s.scenario in SCENARIOS:
                sc_info = SCENARIOS[s.scenario]
                sc_title = sc_info.get("title", s.scenario)
                user_prompt = f"Start our {sc_title} English practice in {lang_name}. Introduce yourself briefly as {pname} in character ({sc_info.get('role', 'interviewer')}) and ask your opening question for {sc_title}. 1-2 natural sentences."
            else:
                if has_prior:
                    user_prompt = (
                        f"Say a brief warm welcome-back in {lang_name} as {pname}. "
                        f"Reference where you left off last time naturally ('{prior_summary}') and ask how things are going with that or how their day has been. One or two sentences, natural spoken speech."
                    )
                else:
                    user_prompt = f"Say a brief warm hello in {lang_name} to start our spoken practice — introduce yourself as {pname} and ask one friendly question to start chatting. One or two sentences, natural spoken speech."
            msgs = [
                {"role": "system", "content": sys},
                {"role": "user", "content": user_prompt},
            ]
            out = (await asyncio.wait_for(self.llm_for(s).complete(msgs, max_tokens=100), timeout=15)).strip()
            if out:
                act = getattr(self.llm_for(s), "active_model", getattr(self.llm_for(s), "name", "?"))
                log.info(f"[LLM] greeting model={act} text={out!r}")
                return out
        except Exception as e:
            log.warning(f"[LLM] greeting failed, fallback: {e}")
        from app.coaching.roleplay import SCENARIOS
        if s.mode == "training":
            from app.coaching.training import get_active_lesson
            profile = load_profile(settings.data_dir, s.user_id)
            lesson = get_active_lesson(profile)
            return f"Welcome to English Training! Today we are working on {lesson['title']}. For example: \"{lesson['example']}\". {lesson['exercise_prompt']}"
        if s.mode == "roleplay":
            return "Welcome to Roleplay! What scenario would you like to practice? We could do meeting an old friend at the market, ordering at a cafe, a job interview, or any situation you like!"
        if s.mode == "practice" and s.scenario in SCENARIOS:
            return SCENARIOS[s.scenario].get("opener", f"Hello! Ready for our {s.scenario.replace('_', ' ')} practice?")
        pname, _ = persona_for(s.voice_id or None)
        return f"Hey! I'm {pname}. How's your day going?"

    async def start_greeting(self, s: Session, send) -> None:
        """AI greeting for conversation, training, or roleplay."""
        t = self.greeting_tasks.pop(s.id, None)
        if t and t is not asyncio.current_task() and not t.done():
            t.cancel()
        s.cancelled = False
        try:
            text = await self.greeting(s)
            if not text or s.cancelled:
                return
            log.info(f"[GREETING] mode={s.mode} text={text[:60]!r}")
            s.history.append({"role": "assistant", "content": text})
            s.turns.append({"role": "assistant", "text": text, "t": time.time()})

            turn_tag = f"greet-{s.mode}"
            first = True
            coalescer = SentenceCoalescer()
            for sent in self._split_speech(text):
                if s.cancelled:
                    return
                for chunk in coalescer.feed(sent):
                    spoken = clean_for_speech(chunk)
                    if spoken:
                        await send({"type": "tts_sentence", "text": spoken,
                                    "turn": turn_tag, "req_id": turn_tag,
                                    "first": first})
                        first = False
            for chunk in coalescer.flush():
                spoken = clean_for_speech(chunk)
                if spoken:
                    await send({"type": "tts_sentence", "text": spoken,
                                "turn": turn_tag, "req_id": turn_tag,
                                "first": first})

            await send({
                "type": "llm_done",
                "text": text,
                "display_text": strip_emotion_tags(text),
                "greeting": True,
                "turn": turn_tag,
                "req_id": turn_tag,
            })
        except asyncio.CancelledError:
            log.info(f"[GREETING] cancelled sid={s.id}")
            raise
        except Exception as e:
            log.warning(f"[GREETING] failed sid={s.id}: {e}")

    # --- English Reading Practice (dedicated interaction state) ------------
    @classmethod
    def _extract_passage_and_intro(cls, text: str, level: str = "B1") -> tuple[str, str]:
        """Extract AI coach spoken intro and passage from model output.
        If the model provided both INTRO and PASSAGE blocks, separates them.
        Falls back to generating an intro if only a passage was returned."""
        import re as _re
        raw = (text or "").strip()
        intro = ""
        passage = ""

        # Match INTRO: ... and PASSAGE: ...
        m_intro = _re.search(
            r"(?:INTRO|INTRODUCTION|COACH(?:\s+INTRO)?)\s*:\s*(.*?)(?=(?:PASSAGE|TEXT|STORY)\s*:|$)",
            raw, _re.DOTALL | _re.IGNORECASE
        )
        m_passage = _re.search(
            r"(?:PASSAGE|TEXT|STORY)\s*:\s*(.*)",
            raw, _re.DOTALL | _re.IGNORECASE
        )

        if m_intro and m_passage:
            intro = m_intro.group(1).strip()
            passage = m_passage.group(1).strip()
        elif m_passage:
            passage = m_passage.group(1).strip()
            pre = raw[:m_passage.start()].strip()
            if pre and len(pre.split()) <= 40:
                intro = pre
        elif m_intro:
            intro = m_intro.group(1).strip()
            passage = raw[m_intro.end():].strip()
        else:
            paras = [p.strip() for p in _re.split(r"\n\s*\n", raw) if p.strip()]
            if len(paras) >= 2 and len(paras[0].split()) <= 35 and any(w in paras[0].lower() for w in ("read", "practice", "passage", "story", "today", "ready")):
                intro = paras[0]
                passage = " ".join(paras[1:])
            else:
                passage = raw

        cleaned_passage = cls._clean_passage(passage)
        if intro:
            intro = _re.sub(r"[*_#`]", "", intro).strip()
            intro = _re.sub(r'^\s*["\']\s*|\s*["\']\s*$', '', intro)
            intro = _re.sub(r"\s+", " ", intro).strip()

        return cleaned_passage, intro

    @staticmethod
    def _clean_passage(text: str) -> str:
        """Strip markers so a reported passage is readable and not repeated
        with asterisks; keep sentence punctuation intact."""
        import re as _re
        t = _re.sub(r"```.*?```", " ", text or "", flags=_re.S)
        t = _re.sub(r"^#{1,6}\s+", "", t, flags=_re.M)
        t = _re.sub(r"^\s*[-*•\d]+\s*[.)]\s+", "", t, flags=_re.M)
        t = _re.sub(r"\*\*(.*?)\*\*", r"\1", t)
        t = _re.sub(r"\*+", "", t)
        t = _re.sub(r"\[([^\]]*)\]", r"\1", t)
        t = _re.sub(r"[^\S\n]+", " ", t).strip()
        # drop a leading "Title:"-style line if the model added one
        if "\n" in t:
            first, rest = t.split("\n", 1)
            first = first.strip()
            if first and len(first) < 40 and not first.rstrip().endswith((".", "!", "?")):
                t = rest.strip()
        return _re.sub(r"\s+", " ", t).strip()

    @staticmethod
    def _split_speech(text: str) -> list[str]:
        """Split TTS-ready feedback into speakable sentences (reuses the
        conversation chunker so punctuation/abbreviations stay safe)."""
        out: list[str] = []
        buf = (text or "").strip()
        guard = 0
        while buf and guard < 12:
            guard += 1
            sents, rest = chunk_sentences(buf)
            if sents:
                out.extend(sents)
            elif rest == buf:
                break
            buf = rest.strip()
        if buf:
            out.append(buf)
        return out

    async def speak_reading(self, s: Session, send, text: str, turn_tag: str) -> None:
        """Speak reading feedback/intro through the normal Fish TTS pipeline
        (tts_sentence messages). Unique turn tag per speech group so a
        barge-in dead-turn flag can never stick to the next group."""
        first = True
        coalescer = SentenceCoalescer()
        for sent in self._split_speech(text):
            if s.cancelled:
                return
            for chunk in coalescer.feed(sent):
                spoken = clean_for_speech(chunk)
                if spoken:
                    await send({"type": "tts_sentence", "text": spoken,
                                "turn": turn_tag, "req_id": turn_tag,
                                "first": first})
                    first = False
        for chunk in coalescer.flush():
            spoken = clean_for_speech(chunk)
            if spoken:
                await send({"type": "tts_sentence", "text": spoken,
                            "turn": turn_tag, "req_id": turn_tag,
                            "first": first})

    async def start_reading(self, s: Session, send) -> None:
        """GENERATING_PASSAGE → WAITING_FOR_READING: create/regenerate a
        passage (level-adjusted, one topic), then hand it to the client.
        Generation is bounded so a hung provider can never wedge the session."""
        # a previous round's analysis must never overlap the next passage
        t = self.reading_tasks.pop(s.id, None)
        # A caller registers the task before it begins so later starts can
        # cancel it. Once this coroutine is running, that registry entry is
        # *this task* — cancelling it here made every WebSocket mode-start
        # cancel itself before a passage could be sent.
        if t and t is not asyncio.current_task() and not t.done():
            t.cancel()
        s.cancelled = False
        ack = self.reading_acks.pop(s.id, None)
        if ack:
            ack.set()
        old = s.reading if isinstance(s.reading, reading_mod.ReadingSession) else None
        if old and old.state == "GENERATING_PASSAGE":
            log.info(f"[READING] start_reading called while already generating passage sid={s.id} — ignoring duplicate")
            return
        rs = reading_mod.ReadingSession()
        rs.gen = (old.gen + 1) if old else 1
        rs.transcripts = list(old.transcripts) if old else []
        s.reading = rs
        s.cancelled = False
        old_passage = old.passage if old and old.attempted else ""
        await send({"type": "reading_state", "state": "GENERATING_PASSAGE", "gen": rs.gen})
        exclude = [old_passage] if old_passage else []
        passage = ""
        intro = ""
        try:
            msgs = reading_mod.passage_messages(s.level, exclude=exclude)
            raw = (await asyncio.wait_for(
                self.llm_for(s).complete(msgs, max_tokens=380), timeout=25.0)).strip()
            passage, intro = self._extract_passage_and_intro(raw, s.level)
        except Exception as e:
            log.info(f"[READING] passage generation failed, fallback: {sanitize_error(str(e))}")
        if not passage:
            passage = reading_mod.fallback_passages_for(s.level, exclude=exclude)
            passage = self._clean_passage(passage)
        if not passage:
            passage = reading_mod.fallback_passages_for(s.level)
        if not intro:
            intro = reading_mod.intro_for(passage, s.level)
        rs.passage = passage
        rs.attempted = True
        rs.state = "WAITING_FOR_READING"
        rs.round = 1
        log.info(f"[READING] passage ready gen={rs.gen} words={len(passage.split())}")
        await self.speak_reading(s, send, intro, turn_tag=f"r{rs.gen}-intro")
        await send({"type": "reading_passage", "passage": passage, "round": 1,
                    "instructions": reading_mod.READING_INSTRUCTION, "gen": rs.gen,
                    "intro": intro, "state": rs.state})
        await send({"type": "reading_state", "state": rs.state, "gen": rs.gen, "round": 1})

    def reading_off(self, s: Session) -> bool:
        """Exit Reading Practice: cancel any analysis task, clear state."""
        t = self.reading_tasks.pop(s.id, None)
        if t and not t.done():
            t.cancel()
        ack = self.reading_acks.pop(s.id, None)
        if ack:
            ack.set()
        s.reading = None
        log.info(f"[READING] stopped sid={s.id}")
        return True

    async def _wait_reading_ack(self, s: Session, timeout: float = 15.0) -> None:
        """Yield until the client confirms the current speech group finished
        playing (or timeout) — the next round only starts after the feedback
        was actually delivered, not when the WS messages were merely sent."""
        ev = self.reading_acks.get(s.id)
        if ev is None:
            ev = asyncio.Event()
            self.reading_acks[s.id] = ev
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.info("[READING] round ack timeout — proceeding anyway")
        finally:
            if self.reading_acks.get(s.id) is ev:
                self.reading_acks.pop(s.id, None)

    async def handle_reading_voice(self, s: Session, send) -> None:
        """Client reports the first voice of a round → RECORDING_READING.
        Duplicate prevention: only WAITING may transition here."""
        rs = s.reading
        if rs and rs.state == "WAITING_FOR_READING":
            rs.state = "RECORDING_READING"
            await send({"type": "reading_state", "state": rs.state, "gen": rs.gen,
                        "round": rs.round})

    async def handle_reading_submit(self, s: Session, text: str, send, round: int = 1,
                                    gen: int = 0, duration_s: float | None = None,
                                    elapsed_s: float | None = None,
                                    pause_total_s: float | None = None,
                                    long_pause_count: int | None = None,
                                    word_count: int = 0,
                                    pauses: list | None = None) -> None:
        """FINALIZING_TRANSCRIPT → ANALYZING_READING → PROVIDING_FEEDBACK →
        next round / ROUND_COMPLETE. Anything stale or duplicated is dropped
        by gen + round + state checks; at most one analysis task per session."""
        rs = s.reading
        if not rs or not rs.active:
            await send({"type": "reading_error", "message": "Reading practice is not active."})
            return
        if gen and gen != rs.gen:
            log.info(f"[READING] stale submit dropped gen={gen} current={rs.gen}")
            return
        if rs.state not in ("RECORDING_READING", "WAITING_FOR_READING", "FINALIZING_TRANSCRIPT"):
            log.info(f"[READING] submit ignored in state={rs.state} round={round} current={rs.round}")
            return
        if round and round != rs.round:
            log.info(f"[READING] submit for round {round} ignored — current round {rs.round}")
            return
        running = self.reading_tasks.get(s.id)
        if running and not running.done():
            log.info("[READING] analysis already running — duplicate submit dropped")
            return
        text = clean_for_speech(text or "").strip()
        if not text or len(text.split()) < 2:
            # Never silently discard: ask for the reading again, same round.
            await send({"type": "reading_state", "state": "WAITING_FOR_READING",
                        "gen": rs.gen, "round": rs.round, "note": "nothing heard"})
            await send({"type": "reading_error",
                        "message": "I couldn't hear your reading — start again from the beginning.",
                        "retryable": True})
            return
        p_wc = len(rs.passage.split())
        s_wc = len(text.split())
        if s_wc < max(4, int(p_wc * 0.35)):
            # User spoke only a few words - do not conclude on few words!
            rs.state = "WAITING_FOR_READING"
            await send({"type": "reading_state", "state": rs.state,
                        "gen": rs.gen, "round": rs.round, "note": "Please finish the paragraph"})
            remind_msg = "You only read a few words. Please read the full paragraph from start to finish."
            await self.speak_reading(s, send, remind_msg, turn_tag=f"r{rs.gen}-remind")
            await send({"type": "reading_error",
                        "message": remind_msg,
                        "retryable": True})
            return
        rs.state = "FINALIZING_TRANSCRIPT"
        await send({"type": "reading_state", "state": rs.state, "gen": rs.gen,
                    "round": rs.round})
        rs.state = "ANALYZING_READING"
        await send({"type": "reading_state", "state": rs.state, "gen": rs.gen,
                    "round": rs.round})

        task = asyncio.create_task(self._analyze_reading(
            s, text, duration_s, elapsed_s, pause_total_s, long_pause_count, pauses, send))
        self.reading_tasks[s.id] = task
        task.add_done_callback(lambda t: self.reading_tasks.pop(s.id, t) if self.reading_tasks.get(s.id) is t else None)

    async def _analyze_reading(self, s: Session, text: str, duration_s, elapsed_s, pause_total_s,
                               long_pause_count, pauses, send) -> None:
        rs = s.reading
        if not rs or not rs.active:
            return
        round_no = rs.round
        gen = rs.gen

        def stale() -> bool:
            # a new passage / stop replaced the reading object between awaits
            return s.reading is not rs

        try:
            diff = reading_mod.diff_passage(rs.passage, text)
            pause_analysis = reading_mod.analyze_pauses(rs.passage, text, pauses or [], diff)
            stats = reading_mod.summarize_stats(
                diff, duration_s, pause_total_s=pause_total_s, long_pause_count=long_pause_count,
                elapsed_s=elapsed_s, pauses=pauses, pause_analysis=pause_analysis
            )
            is_accurate = (diff.accuracy >= 0.85 and len(diff.missing) <= 3)
            from app.coaching.learning import record_practice
            profile = load_profile(settings.data_dir, s.user_id)
            record_practice(profile, "reading", text, seconds=duration_s)
            update_after_turn(profile, [], len(text.split()), duration_s or 0, 0.5)
            save_profile(settings.data_dir, profile)
            rs.transcripts.append({"round": round_no, "text": text, "stats": stats, "is_accurate": is_accurate})
            feedback = ""
            try:
                msgs = reading_mod.feedback_messages(
                    diff, rs.passage, text, round_no, duration_s, pause_total_s,
                    long_pause_count, level=s.level,
                    history=[f"attempt {i+1}: acc {t['stats'].get('word_accuracy')}"
                             for i, t in enumerate(rs.transcripts[:-1])],
                    elapsed_s=elapsed_s, pause_analysis=pause_analysis,
                    is_accurate=is_accurate)
                feedback = (await asyncio.wait_for(
                    self.llm_for(s).complete(msgs, max_tokens=280), timeout=30.0)).strip()
                if stale():
                    return
            except Exception as e:
                log.warning(f"[READING] feedback LLM failed ({sanitize_error(str(e))}) — deterministic fallback")
            if not feedback:
                feedback = self._fallback_feedback(diff, round_no, stats=stats, is_accurate=is_accurate)
            rs.state = "PROVIDING_FEEDBACK"
            await send({"type": "reading_state", "state": rs.state, "gen": gen,
                        "round": round_no, "is_accurate": is_accurate})

            if is_accurate or round_no >= 2:
                full_speech = feedback.strip()
                if "continue reading" not in full_speech.lower():
                    full_speech = full_speech + " Would you like to continue reading?"
                await self.speak_reading(s, send, full_speech, turn_tag=f"r{gen}-fb{round_no}")
                await send({"type": "reading_feedback", "text": full_speech,
                            "display_text": strip_emotion_tags(full_speech),
                            "round": round_no, "gen": gen, "stats": stats,
                            "is_accurate": is_accurate, "completed": True})
                if s.cancelled or stale():
                    return
                await self._wait_reading_ack(s)
                if stale():
                    return
                rs.state = "AWAITING_CONTINUE"
                await send({"type": "reading_state", "state": "AWAITING_CONTINUE",
                            "gen": gen, "round": round_no, "completed": True})
                await send({"type": "reading_continue_prompt",
                            "question": "Would you like to continue reading?",
                            "gen": gen})
            else:
                await self.speak_reading(s, send, feedback, turn_tag=f"r{gen}-fb{round_no}")
                await send({"type": "reading_feedback", "text": feedback,
                            "display_text": strip_emotion_tags(feedback),
                            "round": round_no, "gen": gen, "stats": stats,
                            "is_accurate": False, "completed": False})
                if s.cancelled or stale():
                    return
                await self._wait_reading_ack(s)
                if stale():
                    return
                rs.round = round_no + 1
                rs.state = "WAITING_FOR_READING"
                await send({"type": "reading_retry", "passage": rs.passage,
                            "instructions": reading_mod.RETRY_INSTRUCTION,
                            "gen": gen, "round": rs.round})
                await send({"type": "reading_state", "state": rs.state, "gen": gen,
                            "round": rs.round, "note": "Ready — please read the passage again"})
        except asyncio.CancelledError:
            log.info("[READING] analysis cancelled (mode change / exit)")
            raise
        except Exception as e:
            log.error(f"[READING] analysis error: {sanitize_error(str(e))}")
            try:
                rs.state = "WAITING_FOR_READING"
            except Exception:
                pass
            await send({"type": "reading_error", "message": "Analysis failed — read the passage again.",
                        "retryable": True})
            await send({"type": "reading_state", "state": rs.state, "gen": rs.gen,
                        "round": rs.round})

    @staticmethod
    def _fallback_feedback(diff, round_no: int = 1, stats: dict | None = None, is_accurate: bool = False) -> str:
        """Deterministic, evidence-only feedback when the LLM is unavailable."""
        if is_accurate:
            return "Excellent reading! You pronounced the words clearly and read with great flow."
        bits = ["Good effort!"]
        if diff.missing:
            bits.append("You left out words like " + ", ".join(f"'{w}'" for w in diff.missing[:3]) + ".")
        if diff.replaced:
            bits.append("You said " + ", ".join(f"'{h}' for '{p}'" for p, h in diff.replaced[:2]) + ".")
        if diff.repeated:
            bits.append("The word " + ", ".join(f"'{w}'" for w in diff.repeated[:2]) + " doubled.")
        if not (diff.missing or diff.replaced or diff.repeated):
            bits.append("The words matched the passage nicely.")
        bits.append("Please read the passage again so we can practice it until it is clear and accurate.")
        return " ".join(bits)

    async def _reading_final_summary(self, s: Session, rs, send, gen: int) -> None:
        summary = ""
        try:
            msgs = reading_mod.summary_messages(rs.passage, rs.transcripts, level=s.level)
            summary = (await asyncio.wait_for(
                self.llm_for(s).complete(msgs, max_tokens=200), timeout=25.0)).strip()
        except Exception as e:
            log.warning(f"[READING] summary LLM failed ({sanitize_error(str(e))}) — deterministic fallback")
        if s.reading is not rs:
            return
        if not summary:
            lines = []
            best = max(rs.transcripts, key=lambda t: t["stats"].get("word_accuracy", 0)) if rs.transcripts else None
            if best:
                lines.append(f"Your clearest reading was round {best['round']} with about "
                             f"{int(best['stats'].get('word_accuracy', 0) * 100)} percent word accuracy.")
            lines.append("Keep practising: 1) speak each word completely, 2) keep an even "
                         "pace, 3) read something new aloud every day.")
            summary = " ".join(lines)
        await self.speak_reading(s, send, summary, turn_tag=f"r{gen}-final")
        await send({"type": "reading_summary", "text": summary,
                    "display_text": strip_emotion_tags(summary), "gen": gen})

    async def handle_gemini_live_turn(self, s: Session, text: str, send, t_turn0: float,
                                       t_speech_end_ms: float | None = None, turn_id: str = "",
                                       req_id: str = "", reply_lang: str = ""):
        """Stream turn using Google's official Gemini Live BidiGenerateContent WebSocket."""
        req_id = req_id or turn_id or s.id
        log.info(f"[CHAT req_id={req_id}] chat handler entered (gemini_live) sid={s.id} text={text[:80]!r}")
        s.cancelled = False
        await send({"type": "status", "state": "THINKING", "req_id": req_id})
        t_llm0 = time.time()

        last_assistant_text = ""
        for h in reversed(s.history or []):
            if h.get("role") == "assistant" and (h.get("content") or "").strip():
                last_assistant_text = (h.get("content") or "").strip()
                break
        cleaned_text = clean_speech_artifacts(text, context=last_assistant_text)
        req_lang = detect_language_switch_request(cleaned_text)
        if req_lang:
            log.info(f"[LANG] explicit language switch requested to {req_lang} (gemini_live)")
            s.language = req_lang
            s.last_spoken_lang = req_lang
            reply_lang = req_lang
            await send({"type": "language", "value": req_lang})
            if s.mode != "free":
                s.mode = "free"
                s.scenario = "casual"
                await send({"type": "mode", "mode": "free", "scenario": "casual"})
            if getattr(s, "_live_session", None):
                await self.close_live(s)
                s._live_session = None
        else:
            content_lang = detect_language_from_content(cleaned_text)
            if content_lang and content_lang != "english":
                log.info(f"[LANG] content language detected as {content_lang} (gemini_live)")
                s.language = content_lang
                s.last_spoken_lang = content_lang
                reply_lang = content_lang
                await send({"type": "language", "value": content_lang})
                if getattr(s, "_live_session", None):
                    await self.close_live(s)
                    s._live_session = None

        intent = classify_user_intent(cleaned_text, current_mode=s.mode)
        if intent == UserIntent.CASUAL_CHAT_REQUEST and s.mode != "free":
            log.info(f"[MODE] user requested casual chat: switching from {s.mode} to free (gemini_live)")
            s.mode = "free"
            s.scenario = "casual"
            await send({"type": "mode", "mode": "free", "scenario": "casual"})
            if getattr(s, "_live_session", None):
                await self.close_live(s)
                s._live_session = None
        elif s.mode == "roleplay":
            from app.coaching.roleplay import detect_roleplay_scenario, is_roleplay_change_request, is_roleplay_confirmation
            if is_roleplay_change_request(cleaned_text):
                s.roleplay_started = False
                s.scenario = "roleplay_select"
                s.roleplay_scenario_title = "Choose a Scenario"
                s.roleplay_role = ""
                s._roleplay_pending_topic = None
                await send({"type": "roleplay_update", "title": "Choose a Scenario", "role": ""})
                if getattr(s, "_live_session", None):
                    await self.close_live(s)
                    s._live_session = None
            elif not s.roleplay_started:
                detected = detect_roleplay_scenario(cleaned_text)
                is_confirm = is_roleplay_confirmation(cleaned_text)
                has_explicit_roles = bool(re.search(r"\b(?:act\s+as|pretend|i\s+am|i'm.+you\s+are|you\s+be|you're)\b", cleaned_text.lower()))
                pending = getattr(s, "_roleplay_pending_topic", None)
                if (detected and (is_confirm or has_explicit_roles or pending)) or (is_confirm and pending):
                    chosen = detected or pending or SCENARIOS["market_friend"]
                    s.roleplay_started = True
                    s.scenario = chosen.get("key", "market_friend")
                    s.roleplay_scenario_title = chosen.get("title", "Roleplay")
                    s.roleplay_role = chosen.get("role", "an old friend")
                    s._roleplay_pending_topic = None
                    await send({"type": "roleplay_update", "title": s.roleplay_scenario_title, "role": s.roleplay_role})
                    if getattr(s, "_live_session", None):
                        await self.close_live(s)
                        s._live_session = None
                elif detected:
                    s._roleplay_pending_topic = detected

        profile = load_profile(settings.data_dir, s.user_id)
        mistakes = corr.detect_mistakes(cleaned_text, s.mistake_counts)
        for m in mistakes:
            s.mistake_counts[m.kind] = s.mistake_counts.get(m.kind, 0) + 1
        to_correct = corr.should_correct(mistakes, s.correction)
        conf = confidence_score(cleaned_text)
        if detect_hindi(cleaned_text):
            s.hindi_used = True
        s.words_spoken += len(cleaned_text.split())
        from app.coaching.learning import record_practice
        seconds = s.turn_speech_seconds
        seconds = max(0, min(180, seconds)) if isinstance(seconds, (int, float)) else None
        s.speech_seconds += seconds or 0
        update_after_turn(profile, mistakes, len(cleaned_text.split()), seconds or 0, conf)
        record_practice(profile, s.mode, cleaned_text, mistakes, seconds)
        s.level = profile.get("level", s.level)
        save_profile(settings.data_dir, profile)

        if s.history and s.history[-1].get("role") == "user":
            s.history[-1]["content"] = cleaned_text
        else:
            s.history.append({"role": "user", "content": cleaned_text})

        if s.turns and s.turns[-1].get("role") == "user":
            s.turns[-1]["text"] = cleaned_text
            s.turns[-1]["t"] = time.time()
        else:
            s.turns.append({"role": "user", "text": cleaned_text, "t": time.time()})

        # Fact check first (live path): center-text notice only — the native
        # voice owns the audio here, so no TTS filler (it would overlap).
        # Never touches history/transcript. Facts ride along inside the text
        # handed to the bidi session, not the stored user turn.
        live_facts = ""
        search_query = needs_search(text) if s.mode == "free" else ""
        if search_query:
            filler = pick_filler()
            log.info(f"[SEARCH] live fact check for {search_query!r}")
            await send({"type": "notice", "text": filler})
            try:
                live_facts = await asyncio.wait_for(search_facts(search_query), timeout=1.5)
            except Exception as e:
                log.info(f"[SEARCH] live failed, answering from model: {e}")
                live_facts = ""
            if s.cancelled:
                await send({"type": "llm_cancelled"})
                return
        live_text = text + ("\n\n" + live_facts if live_facts else "")
        if s.mode == "training":
            from app.coaching.training import (get_active_lesson, is_topic_change_request,
                find_matching_lesson, advance_to_next_lesson, update_training_progress,
                attempt_matches_lesson, build_trainer_prompt, is_lesson_mastered)
            lesson = get_active_lesson(profile)
            changed = intent not in (UserIntent.META_CONFUSED_OR_HELP, UserIntent.AUDIO_CHECK) and is_topic_change_request(cleaned_text)
            if changed:
                lesson = advance_to_next_lesson(profile, find_matching_lesson(cleaned_text))
                nudge = build_trainer_prompt(lesson, profile, stage="INTRO", topic_changed=True)
            elif intent in (UserIntent.META_READY, UserIntent.AUDIO_CHECK):
                nudge = build_trainer_prompt(lesson, profile, stage="INTRO")
            elif intent == UserIntent.META_CONFUSED_OR_HELP:
                nudge = build_trainer_prompt(lesson, profile, stage="HELP", user_response=cleaned_text)
            else:
                update_training_progress(profile, lesson["id"], not mistakes and attempt_matches_lesson(lesson, cleaned_text), mistakes)
                nudge = build_trainer_prompt(lesson, profile, stage="PRACTICE", user_response=cleaned_text,
                    detected_mistakes=mistakes, is_mastered=is_lesson_mastered(profile, lesson["id"]))
            save_profile(settings.data_dir, profile)
            await send({"type": "training_update", "lesson": lesson,
                "mastered": profile.get("training", {}).get("mastered_topics", [])})
            live_text += "\n\nCoach guidance for this turn (do not read these instructions aloud):\n" + nudge


        live_sess = getattr(s, "_live_session", None)
        transcribe_sess = getattr(s, "_transcribe_live_session", None)
        from app.voices import GEMINI_VOICES, DEFAULT_GEMINI_VOICE
        gemini_ids = {v["id"] for v in GEMINI_VOICES}
        voice_name = s.voice_id if s.voice_id in gemini_ids else DEFAULT_GEMINI_VOICE

        def _is_conn_drop(e: Exception) -> bool:
            # Google-side socket death: 1011 keepalive timeouts, resets, halves.
            # Retrying on a FRESH socket is safe only when nothing reached the
            # client yet — otherwise the user would hear everything twice.
            try:
                from websockets.exceptions import ConnectionClosed
                if isinstance(e, ConnectionClosed):
                    return True
            except Exception:
                pass
            s_ = str(e).lower()
            return any(k in s_ for k in ("1011", "keepalive", "ping timeout",
                                         "connection reset", "broken pipe",
                                         "network is unreachable", "connectionclosed",
                                         "going away", "no close frame"))

        first_token = False
        live_parts: list[str] = []  # every token actually streamed toward the client
        live_audio_n = 0

        async def on_token(tok):
            nonlocal first_token
            live_parts.append(tok)
            if s.cancelled:
                return
            if not first_token:
                first_token = True
                log.info(f"[LLM req_id={req_id}] LLM response first token received (gemini_live)")
                await send({"type": "tts_start_hint", "req_id": req_id, "turn": turn_id})
                await send({"type": "status", "state": "SPEAKING", "req_id": req_id})
            await send({"type": "llm_token", "token": tok, "req_id": req_id, "turn": turn_id})
            await send({"type": "live_caption", "text": "".join(live_parts), "req_id": req_id, "turn": turn_id})

        async def on_pcm_audio(pcm_b64, mime):
            nonlocal first_token, live_audio_n
            live_audio_n += 1
            if live_audio_n == 1:
                log.info(f"[AUDIO req_id={req_id}] live audio stream started")
            if s.cancelled:
                return
            if transcribe_sess and transcribe_sess.setup_complete:
                await transcribe_sess.send_audio_chunk(pcm_b64, rate=24000)
            await send({
                "type": "live_audio_chunk",
                "data": pcm_b64,
                "mime": mime,
                "turn": turn_id,
                "req_id": req_id
            })

        # setup + streaming share ONE guarded region: any failure anywhere
        # surfaces as a visible error + spoken fallback — never silence.
        # Dropped sockets get ONE silent reconnect+retry (only when the client
        # saw nothing, so nothing can double-play).
        attempt = 0
        try:
            while True:
                attempt += 1
                if attempt > 1:
                    first_token = False
                    live_parts.clear()
                    live_audio_n = 0
                active_lang = reply_lang or s.language or "english"
                if not live_sess or not live_sess.setup_complete or getattr(live_sess, "voice_name", "") != voice_name or getattr(live_sess, "language", "") != active_lang:
                    if live_sess:
                        await self.close_live(s)
                        live_sess = None
                        transcribe_sess = None
                    from app.providers.llm.gemini_live import GeminiLiveSession, GeminiTranscribeLiveSession
                    from app.voices import persona_for
                    mem = memory_hint_for(profile, s)
                    pname, pgender = persona_for(s.voice_id or None)
                    sys_inst = build_system(s.mode, s.level, s.level_setting, s.correction,
                                            scenario=s.scenario, challenge="", memory_hint=mem,
                                            language=active_lang, persona_name=pname, persona_gender=pgender,
                                            voice_cues=False)
                    live_sess = GeminiLiveSession(voice_name=voice_name)
                    live_sess.language = active_lang
                    log.info(f"[LLM req_id={req_id}] LLM request started (Gemini Live Bidi) voice={voice_name} lang={active_lang}")
                    await live_sess.connect(system_instruction=sys_inst)
                    s._live_session = live_sess

                transcribe_sess = None  # native outputTranscription supplies captions

                latest_caption = ""
                transcribe_parts: list[str] = []

                async def transcribe_reader():
                    nonlocal latest_caption
                    if not transcribe_sess:
                        return
                    try:
                        while True:
                            cap = await transcribe_sess.recv_transcription(timeout=2.0)
                            if s.cancelled:
                                break
                            if cap and cap.strip() and cap.strip() != latest_caption:
                                latest_caption = cap.strip()
                                transcribe_parts.append(latest_caption)
                                await send({
                                    "type": "live_caption",
                                    "text": latest_caption,
                                    "turn": turn_id,
                                    "req_id": req_id
                                })
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        log.debug(f"[GEMINI TRANSCRIBE LIVE] reader ended: {e}")

                reader_task = asyncio.create_task(transcribe_reader())

                try:
                    full_reply = await asyncio.wait_for(
                        live_sess.send_user_text(live_text, on_text=on_token, on_audio=on_pcm_audio),
                        timeout=TURN_TIMEOUT_S,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if attempt == 1 and _is_conn_drop(e) and not live_parts and live_audio_n == 0:
                        log.info(f"[GEMINI LIVE] socket dropped with zero output ({e}) — reconnecting once")
                        await self.close_live(s)
                        live_sess = None
                        transcribe_sess = None
                        continue
                    raise
                finally:
                    if transcribe_sess and transcribe_sess.setup_complete:
                        await transcribe_sess.send_turn_complete()
                        await asyncio.sleep(0.35)
                    if not reader_task.done():
                        reader_task.cancel()
                        try:
                            await reader_task
                        except asyncio.CancelledError:
                            pass

                if not full_reply and transcribe_parts:
                    full_reply = transcribe_parts[-1]
                elif not full_reply and live_parts:
                    full_reply = "".join(live_parts)
                if not transcribe_parts and full_reply:
                    await send({
                        "type": "live_caption",
                        "text": full_reply,
                        "turn": turn_id,
                        "req_id": req_id
                    })
                if s.cancelled:
                    await send({"type": "llm_cancelled", "req_id": req_id, "turn": turn_id})
                    return
                s.history.append({"role": "assistant", "content": full_reply})
                s.turns.append({"role": "assistant", "text": full_reply, "t": time.time()})
                log.info(f"[LLM req_id={req_id}] LLM response received tokens={len(live_parts)} audio_chunks={live_audio_n}")
                fixed_sentence = corr.apply_fixes_to_sentence(cleaned_text, mistakes)
                mistake_payload = [
                    {"kind": m.kind, "original": m.pattern, "replacement": getattr(m, "replacement", ""), "hint": m.correction_hint}
                    for m in mistakes
                ]
                await send({
                    "type": "llm_done",
                    "text": full_reply,
                    "display_text": strip_emotion_tags(full_reply),
                    "live": True,
                    "correction": [{"kind": m.kind, "hint": m.correction_hint} for m in to_correct],
                    "mistakes": mistake_payload,
                    "fixed_text": fixed_sentence,
                    "level": s.level,
                    "confidence": conf,
                    "req_id": req_id,
                    "turn": turn_id
                })
                await send({"type": "status", "state": "LISTENING", "req_id": req_id})
                return  # success — leave the attempt loop, never run the turn twice
        except asyncio.CancelledError:
            # Interrupted mid-turn (real user barge-in OR a stray mic-noise trigger).
            # A Live turn streams for many seconds, so an interrupt almost always
            # lands mid-sentence. The interrupted socket may be poisoned (Google
            # still finishing the old turn), so drop it: the next turn connects
            # fresh instead of hanging on a dead stream.
            await self.close_live(s)
            # Never leave it silent: deliver whatever already
            # reached the client as a partial reply, or a graceful fallback if the
            # interrupt landed during setup before any output existed.
            partial = "".join(live_parts).strip()
            if partial:
                log.info(f"[GEMINI LIVE] turn interrupted after {len(live_parts)} tokens / {live_audio_n} audio chunks — delivering partial")
                s.history.append({"role": "assistant", "content": partial})
                s.turns.append({"role": "assistant", "text": partial, "t": time.time()})
                await send({
                    "type": "llm_done",
                    "text": partial,
                    "display_text": strip_emotion_tags(partial),
                    "live": True,
                    "partial": True,
                    "correction": [{"kind": m.kind, "hint": m.correction_hint} for m in to_correct],
                    "level": s.level,
                    "confidence": conf,
                    "req_id": req_id,
                    "turn": turn_id
                })
            else:
                log.info("[GEMINI LIVE] turn interrupted during setup with zero output — cancelled silently")
                await send({"type": "llm_cancelled", "req_id": req_id, "turn": turn_id})
            await send({"type": "status", "state": "LISTENING", "req_id": req_id})
            raise
        except Exception as e:
            err_msg = sanitize_error(str(e))
            log.error(f"[ERROR req_id={req_id}] stage=GEMINI_LIVE error={err_msg}")
            await send({"type": "error", "scope": "gemini_live", "stage": "GEMINI_LIVE", "message": err_msg, "req_id": req_id, "turn": turn_id})
            partial = "".join(live_parts).strip()
            if partial:
                # Died with words already out (e.g. turn timeout): keep them.
                log.info(f"[GEMINI LIVE] failed after {len(live_parts)} tokens / {live_audio_n} audio chunks — delivering partial")
                s.history.append({"role": "assistant", "content": partial})
                s.turns.append({"role": "assistant", "text": partial, "t": time.time()})
                await send({
                    "type": "llm_done",
                    "text": partial,
                    "display_text": strip_emotion_tags(partial),
                    "live": True,
                    "partial": True,
                    "correction": [{"kind": m.kind, "hint": m.correction_hint} for m in to_correct],
                    "level": s.level,
                    "confidence": conf,
                    "req_id": req_id,
                    "turn": turn_id
                })
            else:
                # No second answer after any native audio has reached the client.
                fallback = self.providers.get("groq_fish") or self.providers.get("gemini_fish")
                fb = "Voice service unavailable. Check the selected provider keys and try again."
                if live_audio_n == 0 and fallback:
                    try:
                        fb = await asyncio.wait_for(fallback.complete(self.llm_messages(s, language=reply_lang), max_tokens=180), timeout=15)
                        await send({"type": "tts_sentence", "text": clean_for_speech(fb), "first": True, "turn": turn_id, "req_id": req_id})
                        s.history.append({"role": "assistant", "content": fb})
                        s.turns.append({"role": "assistant", "text": fb, "t": time.time()})
                    except Exception:
                        pass
                elif live_audio_n:
                    fb = "Audio response ended; captions are unavailable."
                await send({"type": "llm_done", "text": fb, "display_text": strip_emotion_tags(fb), "correction": [], "level": s.level, "confidence": conf, "req_id": req_id, "turn": turn_id})
            await send({"type": "status", "state": "LISTENING", "req_id": req_id})

    async def handle_user_turn(self, s: Session, text: str, send, t_turn0: float,
                               t_speech_end_ms=None, turn_id="", req_id="", reply_lang="",
                               speech_duration_s=None):
        turn_id = turn_id or uuid.uuid4().hex
        req_id = req_id or turn_id
        async with s.turn_lock:
            s.last_active = time.monotonic()
            s.cancelled = False
            s.ended_summary = None
            s.turn_speech_seconds = speech_duration_s
            if not self.mode_available(s.response_mode or settings.default_response_mode):
                await send({"type": "error", "scope": "configuration", "message": self.mode_error(s.response_mode), "turn": turn_id, "req_id": req_id})
                await send({"type": "status", "state": "LISTENING", "turn": turn_id, "req_id": req_id})
                return
            async def tagged_send(event):
                await send({"turn": turn_id, "req_id": req_id, **event})
            try:
                async with asyncio.timeout(max(1, settings.turn_timeout_s)):
                    await self._handle_user_turn_impl(s, text, tagged_send, t_turn0,
                        t_speech_end_ms, turn_id, req_id, reply_lang)
            except TimeoutError:
                await tagged_send({"type": "error", "scope": "turn", "message": "Response timed out. Your practice is saved; try another turn."})
                await tagged_send({"type": "status", "state": "LISTENING"})
            finally:
                for mode in s.mode_histories:
                    s.mode_histories[mode] = s.mode_histories[mode][-24:]
                    s.mode_turns[mode] = s.mode_turns.get(mode, [])[-200:]

    async def _handle_user_turn_impl(self, s: Session, text: str, send, t_turn0: float,
                               t_speech_end_ms: float | None = None, turn_id: str = "",
                               req_id: str = "", reply_lang: str = ""):
        """Stream LLM reply token-by-token via send() callback. Supports barge-in cancel.
        reply_lang: detected spoken language for THIS turn — the reply follows
        it, falling back to the session (preferred) language."""
        text = (text or "").strip()
        if not text:
            await send({"type": "status", "state": "LISTENING", "note": "empty transcript ignored", "req_id": req_id})
            return
        # Reading Practice owns the turn while the user reads or the analysis
        # runs: no conversational reply may overlap the reading (client guards
        # sends; this is the server-side safety net).
        rs = s.reading
        if rs and rs.active:
            log.info(f"[TURN] suppressed during reading state={rs.state} sid={s.id}")
            await send({"type": "status", "state": "LISTENING", "req_id": req_id,
                        "note": "listening to your reading"})
            return
        last_assistant_text = ""
        for h in reversed(s.history or []):
            if h.get("role") == "assistant" and (h.get("content") or "").strip():
                last_assistant_text = (h.get("content") or "").strip()
                break
        cleaned_text = clean_speech_artifacts(text, context=last_assistant_text)
        from app.conversation.prompts import LANGUAGES
        reply_lang = (reply_lang or "").lower()
        turn_lang = reply_lang if reply_lang in LANGUAGES else (s.language or "english")

        req_lang = detect_language_switch_request(cleaned_text)
        if req_lang:
            log.info(f"[LANG] explicit language switch requested to {req_lang} text={cleaned_text!r}")
            turn_lang = req_lang
            s.language = req_lang
            s.last_spoken_lang = req_lang
            await send({"type": "language", "value": req_lang})
            if s.mode != "free":
                s.mode = "free"
                s.scenario = "casual"
                await send({"type": "mode", "mode": "free", "scenario": "casual"})
        else:
            content_lang = detect_language_from_content(cleaned_text)
            if content_lang and content_lang != "english":
                log.info(f"[LANG] content language detected as {content_lang} text={cleaned_text!r}")
                turn_lang = content_lang
                s.language = content_lang
                s.last_spoken_lang = content_lang
                await send({"type": "language", "value": content_lang})

        if turn_lang != (s.language or "english"):
            log.info(f"[LANG] reply follows spoken language: {turn_lang} (preferred {s.language})")

        want_mode = (getattr(s, "response_mode", "") or "").lower()
        if want_mode in ("gemini_live", "live") and settings.has_gemini and self.mode_available("gemini_live") :
            return await self.handle_gemini_live_turn(s, text, send, t_turn0, t_speech_end_ms, turn_id, req_id=req_id,
                                                      reply_lang=turn_lang)

        req_id = req_id or turn_id or s.id
        log.info(f"[CHAT req_id={req_id}] chat handler entered sid={s.id} mode={want_mode} text={text[:80]!r}")
        log.info(f"[TURN] user sid={s.id} chars={len(text)} text={text[:120]!r}")
        log.info(f"[STT] final chars={len(text)}")
        s.cancelled = False
        await send({"type": "status", "state": "THINKING", "req_id": req_id, "turn": turn_id})
        # monotonic clock: wall-clock jumps must never show up as latency
        t_llm0 = time.perf_counter()

        intent = classify_user_intent(cleaned_text, current_mode=s.mode)
        if intent == UserIntent.CASUAL_CHAT_REQUEST and s.mode != "free":
            log.info(f"[MODE] user requested casual chat: switching from {s.mode} to free")
            s.mode = "free"
            s.scenario = "casual"
            await send({"type": "mode", "mode": "free", "scenario": "casual"})

        profile = load_profile(settings.data_dir, s.user_id)
        mistakes = corr.detect_mistakes(cleaned_text, s.mistake_counts)
        for m in mistakes:
            s.mistake_counts[m.kind] = s.mistake_counts.get(m.kind, 0) + 1
        to_correct = corr.should_correct(mistakes, s.correction)
        conf = confidence_score(cleaned_text)
        if detect_hindi(cleaned_text):
            s.hindi_used = True
        s.words_spoken += len(cleaned_text.split())
        from app.coaching.learning import record_practice
        seconds = s.turn_speech_seconds
        seconds = max(0, min(180, seconds)) if isinstance(seconds, (int, float)) else None
        s.speech_seconds += seconds or 0
        update_after_turn(profile, mistakes, len(cleaned_text.split()), seconds or 0, conf)
        record_practice(profile, s.mode, cleaned_text, mistakes, seconds)
        s.level = profile.get("level", s.level)
        save_profile(settings.data_dir, profile)

        if s.history and s.history[-1].get("role") == "user":
            s.history[-1]["content"] = cleaned_text
        else:
            s.history.append({"role": "user", "content": cleaned_text})

        if s.turns and s.turns[-1].get("role") == "user":
            s.turns[-1]["text"] = cleaned_text
            s.turns[-1]["t"] = time.time()
        else:
            s.turns.append({"role": "user", "text": cleaned_text, "t": time.time()})

        if s.mode == "training":
            from app.coaching.training import (
                get_active_lesson, advance_to_next_lesson, update_training_progress,
                build_trainer_prompt, is_topic_change_request, find_matching_lesson,
                is_lesson_mastered
            )
            lesson = get_active_lesson(profile)
            topic_changed = False
            if intent not in (UserIntent.META_CONFUSED_OR_HELP, UserIntent.AUDIO_CHECK) and is_topic_change_request(cleaned_text):
                requested_lid = find_matching_lesson(cleaned_text)
                lesson = advance_to_next_lesson(profile, requested_lid)
                save_profile(settings.data_dir, profile)
                topic_changed = True
                log.info(f"[TRAINING] user explicitly requested topic change → switched to {lesson['title']}")

            wpm = len(cleaned_text.split()) * 60 / seconds if seconds and seconds >= 1 else None

            if topic_changed:
                trainer_nudge = build_trainer_prompt(lesson, profile, stage="INTRO", topic_changed=True)
            elif intent == UserIntent.AUDIO_CHECK:
                trainer_nudge = (
                    f"The user is checking microphone audio ('{cleaned_text}'). Confirm clearly that you hear "
                    f"them well, and ask if they are ready for the practice on {lesson['title']}."
                )
            elif intent == UserIntent.META_READY:
                trainer_nudge = build_trainer_prompt(lesson, profile, stage="INTRO")
            elif intent == UserIntent.META_CONFUSED_OR_HELP:
                trainer_nudge = build_trainer_prompt(
                    lesson, profile, stage="HELP", user_response=cleaned_text
                )
            else:
                major_mistakes = [m for m in mistakes if getattr(m, "category", "") in ("B", "C")]
                from app.coaching.training import attempt_matches_lesson
                passed = (not mistakes and attempt_matches_lesson(lesson, cleaned_text))
                update_training_progress(profile, lesson["id"], passed, mistakes)
                save_profile(settings.data_dir, profile)
                mastered = is_lesson_mastered(profile, lesson["id"])
                trainer_nudge = build_trainer_prompt(
                    lesson, profile, stage="PRACTICE", user_response=cleaned_text,
                    speaking_stats={"words_per_minute": wpm},
                    detected_mistakes=mistakes,
                    is_mastered=mastered
                )
            messages = self.llm_messages(s, extra_nudge=trainer_nudge, language=turn_lang)
        elif s.mode == "roleplay":
            from app.coaching.roleplay import detect_roleplay_scenario, is_roleplay_change_request, is_roleplay_confirmation
            if is_roleplay_change_request(cleaned_text):
                s.roleplay_started = False
                s.scenario = "roleplay_select"
                s.roleplay_scenario_title = "Choose a Scenario"
                s.roleplay_role = ""
                s._roleplay_pending_topic = None
                await send({"type": "roleplay_update", "title": "Choose a Scenario", "role": ""})
                roleplay_nudge = (
                    "The user wants to change or reset the roleplay topic. "
                    "Warmly ask what scenario or situation they would like to practice next, "
                    "and talk with them to decide together. Under 30 words."
                )
            elif not s.roleplay_started:
                detected = detect_roleplay_scenario(cleaned_text)
                is_confirm = is_roleplay_confirmation(cleaned_text)
                has_explicit_roles = bool(re.search(r"\b(?:act\s+as|pretend|i\s+am|i'm.+you\s+are|you\s+be|you're)\b", cleaned_text.lower()))
                pending = getattr(s, "_roleplay_pending_topic", None)

                if (detected and (is_confirm or has_explicit_roles or pending)) or (is_confirm and pending):
                    chosen = detected or pending or SCENARIOS["market_friend"]
                    s.roleplay_started = True
                    s.scenario = chosen.get("key", "market_friend")
                    s.roleplay_scenario_title = chosen.get("title", "Roleplay")
                    s.roleplay_role = chosen.get("role", "an old friend")
                    s._roleplay_pending_topic = None
                    await send({"type": "roleplay_update", "title": s.roleplay_scenario_title, "role": s.roleplay_role})
                    roleplay_nudge = (
                        f"START TWO-PERSON CONVERSATION IN CHARACTER!\n"
                        f"Scenario: {s.roleplay_scenario_title}.\n"
                        f"Your Role: You are {s.roleplay_role}.\n"
                        "Briefly confirm the agreed scenario/role in 1 short phrase (e.g. 'Awesome, let\'s begin!'), "
                        "then immediately jump into your character's opening line in 1st person dialogue! 1-3 natural spoken sentences."
                    )
                elif detected:
                    s._roleplay_pending_topic = detected
                    roleplay_nudge = (
                        f"ROLEPLAY TOPIC DISCUSSION & DECISION:\n"
                        f"The user suggested the topic: {detected.get('title')}.\n"
                        f"Talk with the user to decide the roleplay details together! "
                        f"Enthusiastically acknowledge their choice, propose the roles (e.g., 'I can be {detected.get('role')} and you can be the candidate/customer/friend'), "
                        "and ask if that sounds good or if they are ready to begin. Under 35 words."
                    )
                else:
                    roleplay_nudge = (
                        "ROLEPLAY TOPIC DISCUSSION & DECISION:\n"
                        "Talk warmly with the user to explore and decide together on a roleplay topic. "
                        "Suggest 2-3 engaging real-life situations (e.g. meeting an old friend at the market, ordering at a cafe, a job interview, or a travel scenario), "
                        "or ask what situation they'd love to practice today. Under 35 words."
                    )
            else:
                roleplay_nudge = (
                    f"ONGOING TWO-PERSON ROLEPLAY: {s.roleplay_scenario_title}.\n"
                    f"Role: You are {s.roleplay_role}.\n"
                    "CRITICAL RULE: Stay 100% in character in this two-person conversation. "
                    "Respond authentically to what they said, react with emotion, and keep the dialogue flowing naturally (1-3 sentences). "
                    "Never break character."
                )
            messages = self.llm_messages(s, extra_nudge=roleplay_nudge, language=turn_lang)
        else:
            challenge_text = (s.challenge or {}).get("prompt", "") if s.mode == "challenge" else ""
            if req_lang:
                target_display = LANGUAGES.get(req_lang, req_lang.capitalize())
                extra_nudge = (
                    f"LANGUAGE SWITCH: The user specifically said '{cleaned_text}'. "
                    f"You MUST immediately switch and reply 100% in {target_display}. "
                    f"Greet them warmly and continue conversing naturally in {target_display} with native pronunciation and phrasing."
                )
            elif intent == UserIntent.AUDIO_CHECK:
                extra_nudge = (
                    f"The user is checking audio connectivity ('{cleaned_text}'). Confirm warmly and clearly "
                    "that you can hear them well, and ask what's on their mind."
                )
            elif intent == UserIntent.CASUAL_CHAT_REQUEST:
                extra_nudge = (
                    "The user asked for casual chat. Acknowledge warmly and invite them to chat casually "
                    "about whatever they like. No lessons, exercises, or grammar drills."
                )
            elif has_trailing_hesitation(cleaned_text):
                extra_nudge = (
                    "The user paused or trailed off slightly. Respond naturally to the thought they expressed "
                    "without pointing out disfluencies or inventing words they did not say."
                )
            else:
                extra_nudge = corr.correction_instruction(to_correct, mode=s.mode)
            messages = self.llm_messages(s, challenge_text=challenge_text, extra_nudge=extra_nudge, language=turn_lang)

        llm = self.llm_for(s)
        want_label = (s.response_mode or settings.default_response_mode or "fast")
        log.info(f"[LLM req_id={req_id}] LLM request started provider={getattr(llm, 'name', '?')} model={getattr(llm, 'active_model', getattr(llm, 'name', '?'))}")
        full: list[str] = []
        buf = ""
        coalescer = SentenceCoalescer()
        first_token_logged = False
        first_chunk_done = False
        t_first_token = 0.0
        t_first_chunk = 0.0
        # [VOICE LATENCY] stages knowable on the server
        lat: dict = {"turn": turn_id}
        if t_speech_end_ms:
            lat["stt_ms"] = max(0.0, t_turn0 * 1000 - float(t_speech_end_ms))

        def _mark_chunk() -> None:
            """Time from the first LLM token to the first speakable phrase."""
            if first_token_logged:
                lat["chunk_ms"] = max(0.0, (time.perf_counter() - t_llm0 - t_first_token) * 1000)

        async def speak_chunk(speech_text: str, first: bool) -> None:
            """Send a speakable phrase. The first one of a turn carries the
            server latency numbers and tells the client to stream-play it.
            `turn` tags every phrase so the client can drop audio from a turn
            that was interrupted while these messages were in flight."""
            log.info(f"[TTS req_id={req_id}] TTS request started text={speech_text[:50]!r} (first={first})")
            payload: dict = {"type": "tts_sentence", "text": speech_text, "turn": turn_id, "req_id": req_id}
            if first:
                payload["first"] = True
                payload["lat"] = {k: (int(round(v)) if isinstance(v, (int, float)) else v)
                                  for k, v in lat.items() if v is not None}
            await send(payload)

        try:
            # Fact check first: factual questions get a free DDG + Wikipedia
            # lookup while the filler ("Let me check...") is already speaking.
            # The filler is SPOKEN (tts_sentence) and shown in center text, but
            # never touches history/transcript — those come only from llm_done.
            search_query = needs_search(text) if s.mode == "free" else ""
            if search_query:
                filler = pick_filler()
                log.info(f"[SEARCH] fact check for {search_query!r} (saying {filler!r} first)")
                filler_spoken = clean_for_speech(filler)
                if filler_spoken:
                    await speak_chunk(filler_spoken, True)
                    # filler took the turn's first/stream-play slot so the real
                    # first chunk below must not re-send first=True (no overlap)
                    first_chunk_done = True
                try:
                    facts = await asyncio.wait_for(search_facts(search_query), timeout=1.5)
                except Exception as e:
                    log.info(f"[SEARCH] failed, answering from model: {e}")
                    facts = ""
                if s.cancelled:
                    await send({"type": "llm_cancelled", "req_id": req_id, "turn": turn_id})
                    return
                if facts:
                    messages = messages + [{"role": "system", "content": facts}]
            # brief human beat before answering — if configured (0ms default for instant replies)
            if settings.reply_pause_ms > 0:
                await asyncio.sleep(settings.reply_pause_ms / 1000)
            if s.cancelled:
                await send({"type": "llm_cancelled", "req_id": req_id, "turn": turn_id})
                return
            async for tok in llm.stream(messages, max_tokens=180):
                if s.cancelled:
                    log.info(f"[TURN req_id={req_id}] cancelled by barge-in")
                    await send({"type": "llm_cancelled", "req_id": req_id, "turn": turn_id})
                    return
                if not first_token_logged:
                    t_first_token = time.perf_counter() - t_llm0
                    lat["llm_ttft_ms"] = t_first_token * 1000
                    log.info(f"[LLM req_id={req_id}] LLM response first token received t={t_first_token:.2f}s "
                             f"model={getattr(llm,'active_model',getattr(llm,'name','?'))}")
                    first_token_logged = True
                    await send({"type": "tts_start_hint", "req_id": req_id, "turn": turn_id})
                full.append(tok)
                buf += tok
                await send({"type": "llm_token", "token": tok, "req_id": req_id, "turn": turn_id})
                # fast path: emit the opening clause the moment it is speakable
                if not first_chunk_done:
                    candidate = f"{coalescer.held} {buf}".strip() if coalescer.held else buf
                    split = first_chunk_split(candidate)
                    if split:
                        head, rest = split
                        coalescer.held = ""
                        buf = rest
                        spoken = clean_for_speech(head)
                        if spoken:
                            _mark_chunk()
                            await speak_chunk(spoken, True)
                            first_chunk_done = True
                            t_first_chunk = time.perf_counter() - t_llm0
                            log.info(f"[TTS req_id={req_id}] first chunk fast-path chars={len(spoken)} after={t_first_chunk:.2f}s")
                sentences, buf = chunk_sentences(buf)
                for sent in sentences:
                    if s.cancelled:
                        return
                    if not first_chunk_done:
                        lead = f"{coalescer.held} {sent}".strip() if coalescer.held else sent
                        if len(lead) >= FIRST_SENT_MIN:
                            coalescer.held = ""
                            spoken = clean_for_speech(lead)
                            if spoken:
                                _mark_chunk()
                                await speak_chunk(spoken, True)
                                first_chunk_done = True
                                t_first_chunk = time.perf_counter() - t_llm0
                                log.info(f"[TTS req_id={req_id}] first sentence chars={len(spoken)} after={t_first_chunk:.2f}s")
                            continue
                        else:
                            coalescer.held = lead
                            continue
                    for chunk in coalescer.feed(sent):
                        spoken = clean_for_speech(chunk)
                        if spoken:
                            if not first_chunk_done:
                                _mark_chunk()
                            await speak_chunk(spoken, not first_chunk_done)
                            first_chunk_done = True
                    log.info(f"[TTS req_id={req_id}] sentence queued chars={len(sent)}")
            for chunk in coalescer.flush(buf):
                spoken = clean_for_speech(chunk)
                if spoken:
                    await speak_chunk(spoken, not first_chunk_done)
                    first_chunk_done = True
            if first_chunk_done:
                # server half of the report; the client appends TTS + playback + TOTAL
                log.info("[VOICE LATENCY server-side]\n" + format_block(
                    stt_ms=lat.get("stt_ms"), llm_ttft_ms=lat.get("llm_ttft_ms"),
                    chunk_ms=lat.get("chunk_ms"), mode=want_label))
            reply = "".join(full).strip()
            # If the reply (after stripping emotion cues and brackets) has no conversational words, replace it:
            actual_words = strip_emotion_tags(reply)
            if len(actual_words) < 4 or not any(c.isalnum() for c in actual_words):
                log.warning(f"[LLM req_id={req_id}] reply has no conversational content ({len(reply)} chars: {reply!r}), generating natural continuation")
                if s.mode == "free":
                    reply = "I'm listening! Tell me more about what's on your mind."
                elif s.mode == "training":
                    reply = "Let's keep going! Give it another try."
                elif s.mode == "roleplay":
                    reply = "Go on, I'm listening."
                else:
                    reply = "I hear you! What's on your mind?"
                spoken = clean_for_speech(reply)
                if spoken:
                    await speak_chunk(spoken, not first_chunk_done)
                    first_chunk_done = True
            clean_display = strip_emotion_tags(reply)
            if not clean_display or not any(c.isalnum() for c in clean_display):
                clean_display = re.sub(r"[\[\]]", "", reply).strip() or "I'm listening!"
            s.history.append({"role": "assistant", "content": reply})
            s.turns.append({"role": "assistant", "text": reply, "t": time.time()})
            log.info(f"[LLM req_id={req_id}] LLM response received chars={len(reply)}")
            log.info(f"[TURN req_id={req_id}] done turn_s={time.time()-t_turn0:.2f}s reply_chars={len(reply)}")
            fixed_sentence = corr.apply_fixes_to_sentence(cleaned_text, mistakes)
            mistake_payload = [
                {"kind": m.kind, "original": m.pattern, "replacement": getattr(m, "replacement", ""), "hint": m.correction_hint}
                for m in mistakes
            ]
            await send({"type": "llm_done", "text": reply,
                        "display_text": clean_display,
                        "correction": [ {"kind": m.kind, "hint": m.correction_hint} for m in to_correct ],
                        "mistakes": mistake_payload,
                        "fixed_text": fixed_sentence,
                        "level": s.level, "confidence": conf,
                        "req_id": req_id,
                        "turn": turn_id})
            if s.mode == "training":
                try:
                    from app.coaching.training import get_active_lesson, get_learning_path
                    cur_prof = load_profile(settings.data_dir, s.user_id)
                    next_les = get_active_lesson(cur_prof)
                    await send({
                        "type": "training_update",
                        "lesson": next_les,
                        "mastered": cur_prof.get("training", {}).get("mastered_topics", []),
                        "practice_needed": cur_prof.get("training", {}).get("practice_needed", []),
                        "learning_path": get_learning_path(cur_prof),
                        "turn": turn_id,
                    })
                except Exception:
                    pass
            await send({"type": "status", "state": "LISTENING", "req_id": req_id, "turn": turn_id})
        except asyncio.CancelledError:
            log.info(f"[TURN req_id={req_id}] task cancelled")
            try:
                await send({"type": "llm_cancelled", "req_id": req_id, "turn": turn_id})
            except Exception:
                pass
            raise
        except Exception as e:
            err_msg = sanitize_error(str(e))
            log.error(f"[ERROR req_id={req_id}] stage=LLM error={err_msg} — attempting provider fallback")
            if full:
                for chunk in coalescer.flush(buf):
                    spoken = clean_for_speech(chunk)
                    if spoken:
                        await speak_chunk(spoken, not first_chunk_done)
                        first_chunk_done = True
                partial = "".join(full).strip()
                s.history.append({"role": "assistant", "content": partial})
                s.turns.append({"role": "assistant", "text": partial, "t": time.time()})
                await send({"type": "llm_done", "text": partial, "display_text": strip_emotion_tags(partial),
                            "partial": True, "correction": [], "level": s.level, "confidence": conf,
                            "req_id": req_id, "turn": turn_id})
                await send({"type": "status", "state": "LISTENING", "req_id": req_id, "turn": turn_id})
                return

            await send({"type": "error", "scope": "llm", "stage": "LLM", "message": err_msg, "req_id": req_id, "turn": turn_id})
            fb = "The AI service is unavailable. Your practice has been saved. Try another turn shortly or choose another configured response mode."
            await send({"type": "llm_done", "text": fb, "display_text": fb, "correction": [], "level": s.level, "confidence": conf, "req_id": req_id, "turn": turn_id})
            await send({"type": "status", "state": "LISTENING", "req_id": req_id, "turn": turn_id})

    async def close_live(self, s: Session) -> None:
        """Release the Gemini Live Bidi socket and Transcribe Live socket.
        Called on WS disconnect and before replacing the session."""
        live_sess = getattr(s, "_live_session", None)
        s._live_session = None
        if live_sess:
            try:
                await live_sess.close()
            except Exception:
                pass

        transcribe_sess = getattr(s, "_transcribe_live_session", None)
        s._transcribe_live_session = None
        if transcribe_sess:
            try:
                await transcribe_sess.close()
            except Exception:
                pass

    def cancel(self, sid: str):
        s = self.sessions.get(sid)
        if s:
            s.cancelled = True
            live = getattr(s, "_live_session", None)
            if live:
                async def halt_old_socket():
                    try:
                        await asyncio.wait_for(live.interrupt(), timeout=2)
                    except Exception:
                        pass
                asyncio.create_task(halt_old_socket())
        t = self.tasks.pop(sid, None)
        if t and not t.done():
            t.cancel()
        gt = self.greeting_tasks.pop(sid, None)
        if gt and not gt.done():
            gt.cancel()
        rt = self.reading_tasks.pop(sid, None)
        if rt and not rt.done():
            rt.cancel()

    def end_session(self, s: Session) -> dict:
        if s.ended_summary is not None:
            return s.ended_summary
        self.cancel(s.id)
        if hasattr(s, "_greeted_modes"):
            s._greeted_modes.clear()
        dur = time.time() - s.start_t
        profile = load_profile(settings.data_dir, s.user_id)
        record_session(profile)
        save_profile(settings.data_dir, profile)
        sess = {"turns": s.turns, "mistake_counts": s.mistake_counts,
                "level": s.level, "hindi_used": s.hindi_used, "duration_s": dur, "speaking_seconds": s.speech_seconds}
        summary = build_summary(sess, profile)
        # persist session
        try:
            from pathlib import Path
            import json
            p = Path(settings.data_dir) / "sessions" / f"{s.id}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({**sess, "summary": summary}, indent=2, ensure_ascii=False))
        except Exception:
            pass
        s.ended_summary = summary
        return summary


engine = Engine()
