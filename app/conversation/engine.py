"""Conversation engine: turn-taking, streaming, barge-in, coaching wiring."""
import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field

from app.config import settings
from app.conversation.prompts import build_system
from app.coaching import correction as corr
from app.coaching.level import estimate_level
from app.coaching.confidence import confidence_score
from app.coaching.memory import load_profile, save_profile, update_after_turn, record_session
from app.coaching.summary import build_summary
from app.coaching.roleplay import SCENARIOS, today_challenge
from app.latency import format_block
from app.search import needs_search, pick_filler, search_facts
from app.providers.llm.base import LLMProvider
from app.providers.llm.gemini import GeminiChainProvider
from app.providers.llm.groq import GroqProvider
from app.providers.llm.mock import MockProvider

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

# First-chunk fast path: the voice should start on the opening clause, not after
# a whole sentence (measured: waiting for sentence+coalescer cost ~1-1.5s of
# time-to-first-audio). A sentence end is safe from 8 chars ("Right on."),
# a clause boundary only from 24 chars so we never emit a bare fragment.
FIRST_SENT_MIN = 8
FIRST_CLAUSE_MIN = 24
FIRST_BOUNDARY = re.compile(r"[.!?]+(?=\s|$)|[,;:](?=\s)")


def first_chunk_split(text: str) -> tuple[str, str] | None:
    """Earliest speakable opening chunk. Returns (chunk, rest) or None."""
    for m in FIRST_BOUNDARY.finditer(text or ""):
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
    """Split complete sentences; merge tiny interjections so TTS doesn't sound choppy."""
    out: list[str] = []
    rest = buffer
    while True:
        idx = -1
        for i, ch in enumerate(rest):
            if ch in ".!?":
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
    # merge a tiny lead ("Hey!", "Nice.", "Oh.") with what follows so voice flows
    if out and len(out[0]) < 12 and rest:
        out[0] = (out[0] + " " + rest.split(".")[0]).strip() if "." in rest else (out[0] + " " + rest)
        rest = "" if "." not in rest else ".".join(rest.split(".")[1:]).strip()
        if rest and not rest[0].isupper():
            pass
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
    Emotion tags like [chuckle], [happy], [emphasis] are KEPT — Fish Audio
    interprets them as voice direction markers."""
    import re as _re
    t = _re.sub(r"```.*?```", " ", text, flags=_re.S)
    t = _re.sub(r"^#{1,6}\s+", "", t, flags=_re.M)
    t = _re.sub(r"^\s*[-*•\d]+\s*[.)]\s+", "", t, flags=_re.M)
    t = _re.sub(r"\*\*(.*?)\*\*", r"\1", t)
    t = _re.sub(r"\*+", "", t)  # stray marker when a **pair** straddles a chunk split
    t = _re.sub(r"__(.*?)__", r"\1", t)
    t = _re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = "".join(c for c in t if not (0x1F300 <= ord(c) <= 0x1FAFF or ord(c) in (0x200D, 0xFE0F)))
    t = _re.sub(r"\s+", " ", t).strip()
    return t


def strip_emotion_tags(text: str) -> str:
    """Remove Fish Audio emotion/delivery tags for on-screen display.
    Keeps **emphasis** markers (handled separately by the client's
    emphasize() function for visual highlighting)."""
    import re as _re
    t = _re.sub(r"\[[^\]]*\]", "", text)
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
    if getattr(s, "past_chat_summary", ""):
        bits.append(f"previous conversations: user mentioned '{s.past_chat_summary}' — feel free to reference this naturally (e.g. 'I remember you mentioned...', 'Last time we discussed...') or ask if they'd like to continue where you left off")
    elif profile.get("last_chat_summary"):
        bits.append(f"previous conversations: user mentioned '{profile['last_chat_summary']}' — feel free to reference this naturally")
    if s.hindi_used:
        bits.append("sometimes slips into Hindi — support, don't lecture")
    last_ai = next((t.get("text","") for t in reversed(s.turns) if t.get("role")=="assistant"), "")
    if last_ai:
        bits.append(f"you just said: {last_ai[:100]!r} — don't repeat its opener/question")
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

    HOLD_UNDER = 45  # sentences shorter than this wait for a partner
    MAX_HOLD = 120  # never hold more than this; flush instead

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
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    user_id: str = "default"
    mode: str = "free"
    correction: str = "balanced"
    level_setting: str = "auto"
    level: str = "B1"
    language: str = "english"
    voice_id: str = ""  # selected Fish voice; persona follows it
    scenario: str = "casual"
    challenge: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    turns: list[dict] = field(default_factory=list)
    mistake_counts: dict = field(default_factory=dict)
    hindi_used: bool = False
    start_t: float = field(default_factory=time.time)
    words_spoken: int = 0
    response_mode: str = ""  # "fast" | "quality"; "" = server default
    cancelled: bool = False
    past_chat_summary: str = ""


class Engine:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.tasks: dict[str, asyncio.Task] = {}
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

            glive = GeminiChainProvider(models=["gemini-3.8-live", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"])
            self.providers["gemini_live"] = glive
            self.providers["live"] = glive
            log.info(f"[LLM] Gemini Live tier ready {glive.models}")
        else:
            log.info("[LLM] Gemini tiers unavailable (no GEMINI_API_KEY)")
        self.mock = MockProvider()
        if not self.providers:
            log.info("[LLM] no provider keys — zero-cost demo mode")
        self.llm = self.llm_for(None)

    def sync_past_history(self, s: Session, turns: list[dict]) -> None:
        """Digest past conversation turns from local storage and update session memory."""
        if not turns:
            return
        user_lines = []
        for t in turns:
            c = (t.get("content") or t.get("text") or "").strip()
            role = t.get("role") or t.get("who") or ""
            if role.lower() in ("user", "you") and len(c.split()) >= 3:
                user_lines.append(c)
        if user_lines:
            summary = "; ".join(user_lines[-4:])[:300]
            s.past_chat_summary = summary
            profile = load_profile(settings.data_dir, s.user_id)
            profile["last_chat_summary"] = summary
            save_profile(settings.data_dir, profile)
            log.info(f"[MEMORY] synced past chat summary for {s.id}: {summary[:100]!r}")

    def llm_for(self, s: "Session | None") -> LLMProvider:
        """Pick the provider for a session's chosen response mode, never failing.
        If the chosen tier has no key we use an available fallback."""
        raw = (getattr(s, "response_mode", "") or settings.default_response_mode or "groq_fish").lower()
        if raw in ("fast", "groq"):
            want = "groq_fish"
        elif raw in ("quality", "gemini"):
            want = "gemini_fish"
        elif raw in ("live", "gemini_live", "gemini-3.8-live"):
            want = "gemini_live"
        elif raw in self.providers:
            want = raw
        else:
            want = "groq_fish"

        p = self.providers.get(want)
        if p is not None:
            return p
        for fallback in ("groq_fish", "gemini_fish", "gemini_live", "fast", "quality"):
            if self.providers.get(fallback):
                log.info(f"[LLM] mode={want} unavailable → serving from {fallback}")
                return self.providers[fallback]
        return self.mock

    def mode_available(self, mode: str) -> bool:
        if mode in ("fast", "groq"):
            mode = "groq_fish"
        elif mode in ("quality", "gemini"):
            mode = "gemini_fish"
        elif mode in ("live",):
            mode = "gemini_live"
        return bool(self.providers.get(mode))

    def get_or_create(self, sid: str | None, user_id: str = "default") -> Session:
        if sid and sid in self.sessions:
            return self.sessions[sid]
        s = Session(id=sid or uuid.uuid4().hex[:8], user_id=user_id,
                    mode=settings.default_mode, correction=settings.default_correction,
                    level_setting=settings.default_level)
        self.sessions[s.id] = s
        return s

    def llm_messages(self, s: Session, scenario_text: str = "", challenge_text: str = "", extra_nudge: str = "") -> list[dict]:
        from app.voices import persona_for
        profile = load_profile(settings.data_dir, s.user_id)
        mem = memory_hint_for(profile, s)
        pname, pgender = persona_for(s.voice_id or None)
        sys = build_system(s.mode, s.level, s.level_setting, s.correction,
                           scenario=scenario_text or s.scenario, challenge=challenge_text,
                           memory_hint=mem, language=s.language,
                           persona_name=pname, persona_gender=pgender)
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
            if s.mode == "practice" and s.scenario in SCENARIOS:
                sc_info = SCENARIOS[s.scenario]
                sc_title = sc_info.get("title", s.scenario)
                user_prompt = f"Start our {sc_title} English practice in {lang_name}. Introduce yourself briefly as {pname} in character ({sc_info.get('role', 'interviewer')}) and ask your opening question for {sc_title}. 1-2 natural sentences."
            else:
                user_prompt = f"Say a brief warm hello in {lang_name} to start our spoken practice — introduce yourself as {pname} and ask one friendly question to start chatting. One or two sentences, natural spoken speech."
            msgs = [
                {"role": "system", "content": sys},
                {"role": "user", "content": user_prompt},
            ]
            out = (await self.llm_for(s).complete(msgs, max_tokens=60)).strip()
            if out:
                act = getattr(self.llm_for(s), "active_model", getattr(self.llm_for(s), "name", "?"))
                log.info(f"[LLM] greeting model={act} text={out!r}")
                return out
        except Exception as e:
            log.warning(f"[LLM] greeting failed, fallback: {e}")
        from app.coaching.roleplay import SCENARIOS
        if s.mode == "practice" and s.scenario in SCENARIOS:
            return SCENARIOS[s.scenario].get("opener", f"Hello! Ready for our {s.scenario.replace('_', ' ')} practice?")
        pname, _ = persona_for(s.voice_id or None)
        return f"Hey! I'm {pname}. How's your day going?"

    async def handle_gemini_live_turn(self, s: Session, text: str, send, t_turn0: float,
                                       t_speech_end_ms: float | None = None, turn_id: str = "",
                                       req_id: str = ""):
        """Stream turn using Google's official Gemini Live BidiGenerateContent WebSocket."""
        req_id = req_id or turn_id or s.id
        log.info(f"[CHAT req_id={req_id}] chat handler entered (gemini_live) sid={s.id} text={text[:80]!r}")
        s.cancelled = False
        await send({"type": "status", "state": "THINKING", "req_id": req_id})
        t_llm0 = time.time()

        profile = load_profile(settings.data_dir, s.user_id)
        mistakes = corr.detect_mistakes(text, s.mistake_counts)
        for m in mistakes:
            s.mistake_counts[m.kind] = s.mistake_counts.get(m.kind, 0) + 1
        to_correct = corr.should_correct(mistakes, s.correction)
        s.level = estimate_level(text, s.level)
        conf = confidence_score(text)
        if detect_hindi(text):
            s.hindi_used = True
        s.words_spoken += len(text.split())
        update_after_turn(profile, mistakes, len(text.split()), 20.0, conf)
        save_profile(settings.data_dir, profile)

        s.history.append({"role": "user", "content": text})
        s.turns.append({"role": "user", "text": text, "t": time.time()})

        # Fact check first (live path): center-text notice only — the native
        # voice owns the audio here, so no TTS filler (it would overlap).
        # Never touches history/transcript. Facts ride along inside the text
        # handed to the bidi session, not the stored user turn.
        live_facts = ""
        search_query = needs_search(text)
        if search_query:
            filler = pick_filler()
            log.info(f"[SEARCH] live fact check for {search_query!r}")
            await send({"type": "notice", "text": filler})
            try:
                live_facts = await search_facts(search_query)
            except Exception as e:
                log.info(f"[SEARCH] live failed, answering from model: {e}")
                live_facts = ""
            if s.cancelled:
                await send({"type": "llm_cancelled"})
                return
        live_text = text + ("\n\n" + live_facts if live_facts else "")

        live_sess = getattr(s, "_live_session", None)
        voice_name = s.voice_id if s.voice_id in ("Puck", "Aoede", "Charon", "Kore", "Fenrir") else "Puck"

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

        async def on_pcm_audio(pcm_b64, mime):
            nonlocal first_token, live_audio_n
            live_audio_n += 1
            if live_audio_n == 1:
                log.info(f"[AUDIO req_id={req_id}] live audio stream started")
            if s.cancelled:
                return
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
                if not live_sess or not live_sess.setup_complete or getattr(live_sess, "voice_name", "") != voice_name:
                    if live_sess:
                        await self.close_live(s)
                        live_sess = None
                    from app.providers.llm.gemini_live import GeminiLiveSession
                    from app.voices import persona_for
                    mem = memory_hint_for(profile, s)
                    pname, pgender = persona_for(s.voice_id or None)
                    sys_inst = build_system(s.mode, s.level, s.level_setting, s.correction,
                                            scenario=s.scenario, challenge="", memory_hint=mem,
                                            language=s.language, persona_name=pname, persona_gender=pgender)
                    live_sess = GeminiLiveSession(voice_name=voice_name)
                    log.info(f"[LLM req_id={req_id}] LLM request started (Gemini Live Bidi) voice={voice_name}")
                    await live_sess.connect(system_instruction=sys_inst)
                    s._live_session = live_sess

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
                        continue
                    raise
                if not full_reply and live_parts:
                    full_reply = "".join(live_parts)
                if s.cancelled:
                    await send({"type": "llm_cancelled", "req_id": req_id, "turn": turn_id})
                    return
                s.history.append({"role": "assistant", "content": full_reply})
                s.turns.append({"role": "assistant", "text": full_reply, "t": time.time()})
                log.info(f"[LLM req_id={req_id}] LLM response received tokens={len(live_parts)} audio_chunks={live_audio_n}")
                await send({
                    "type": "llm_done",
                    "text": full_reply,
                    "display_text": strip_emotion_tags(full_reply),
                    "live": True,
                    "correction": [{"kind": m.kind, "hint": m.correction_hint} for m in to_correct],
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
                log.info("[GEMINI LIVE] turn interrupted during setup with zero output — graceful fallback")
                fb = "Sorry, I missed that — say that again?"
                s.history.append({"role": "assistant", "content": fb})
                s.turns.append({"role": "assistant", "text": fb, "t": time.time()})
                await send({"type": "llm_done", "text": fb, "live": True,
                            "partial": True, "correction": [], "level": s.level,
                            "confidence": conf, "req_id": req_id, "turn": turn_id})
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
                fb = "Sorry, I had a little hiccup. Could you say that again?"
                await send({"type": "llm_done", "text": fb, "display_text": fb, "correction": [], "level": s.level, "confidence": conf, "req_id": req_id, "turn": turn_id})
            await send({"type": "status", "state": "LISTENING", "req_id": req_id})

    async def handle_user_turn(self, s: Session, text: str, send, t_turn0: float,
                               t_speech_end_ms: float | None = None, turn_id: str = "",
                               req_id: str = ""):
        """Stream LLM reply token-by-token via send() callback. Supports barge-in cancel."""
        text = (text or "").strip()
        if not text:
            await send({"type": "status", "state": "LISTENING", "note": "empty transcript ignored", "req_id": req_id})
            return

        want_mode = (getattr(s, "response_mode", "") or settings.default_response_mode or "").lower()
        if want_mode in ("gemini_live", "live"):
            return await self.handle_gemini_live_turn(s, text, send, t_turn0, t_speech_end_ms, turn_id, req_id=req_id)

        req_id = req_id or turn_id or s.id
        log.info(f"[CHAT req_id={req_id}] chat handler entered sid={s.id} mode={want_mode} text={text[:80]!r}")
        log.info(f"[TURN] user sid={s.id} chars={len(text)} text={text[:120]!r}")
        log.info(f"[STT] final chars={len(text)}")
        s.cancelled = False
        await send({"type": "status", "state": "THINKING", "req_id": req_id, "turn": turn_id})
        t_llm0 = time.time()

        profile = load_profile(settings.data_dir, s.user_id)
        mistakes = corr.detect_mistakes(text, s.mistake_counts)
        for m in mistakes:
            s.mistake_counts[m.kind] = s.mistake_counts.get(m.kind, 0) + 1
        to_correct = corr.should_correct(mistakes, s.correction)
        s.level = estimate_level(text, s.level)
        conf = confidence_score(text)
        if detect_hindi(text):
            s.hindi_used = True
        s.words_spoken += len(text.split())
        update_after_turn(profile, mistakes, len(text.split()), 20.0, conf)
        save_profile(settings.data_dir, profile)

        s.history.append({"role": "user", "content": text})
        s.turns.append({"role": "user", "text": text, "t": time.time()})

        challenge_text = (s.challenge or {}).get("prompt", "") if s.mode == "challenge" else ""
        ci = corr.correction_instruction(to_correct)
        messages = self.llm_messages(s, challenge_text=challenge_text, extra_nudge=ci)

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
                lat["chunk_ms"] = max(0.0, (time.time() - t_llm0 - t_first_token) * 1000)

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
            search_query = needs_search(text)
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
                    facts = await search_facts(search_query)
                except Exception as e:
                    log.info(f"[SEARCH] failed, answering from model: {e}")
                    facts = ""
                if s.cancelled:
                    await send({"type": "llm_cancelled", "req_id": req_id, "turn": turn_id})
                    return
                if facts:
                    messages = messages + [{"role": "system", "content": facts}]
            # brief human beat before answering — not the old 350ms wall
            await asyncio.sleep(max(0, settings.reply_pause_ms) / 1000)
            if s.cancelled:
                await send({"type": "llm_cancelled", "req_id": req_id, "turn": turn_id})
                return
            await send({"type": "status", "state": "SPEAKING", "req_id": req_id, "turn": turn_id})
            async for tok in llm.stream(messages, max_tokens=180):
                if s.cancelled:
                    log.info(f"[TURN req_id={req_id}] cancelled by barge-in")
                    await send({"type": "llm_cancelled", "req_id": req_id, "turn": turn_id})
                    return
                if not first_token_logged:
                    t_first_token = time.time() - t_llm0
                    lat["llm_ttft_ms"] = t_first_token * 1000
                    log.info(f"[LLM req_id={req_id}] LLM response first token received t={t_first_token:.2f}s "
                             f"model={getattr(llm,'active_model',getattr(llm,'name','?'))}")
                    first_token_logged = True
                    await send({"type": "tts_start_hint", "req_id": req_id, "turn": turn_id})
                full.append(tok)
                buf += tok
                await send({"type": "llm_token", "token": tok, "req_id": req_id, "turn": turn_id})
                # fast path: emit the opening clause the moment it is speakable
                if not first_chunk_done and not coalescer.held:
                    split = first_chunk_split(buf)
                    if split:
                        head, buf = split
                        spoken = clean_for_speech(head)
                        if spoken:
                            _mark_chunk()
                            await speak_chunk(spoken, True)
                            first_chunk_done = True
                            t_first_chunk = time.time() - t_llm0
                            log.info(f"[TTS req_id={req_id}] first chunk fast-path chars={len(spoken)} after={t_first_chunk:.2f}s")
                sentences, buf = chunk_sentences(buf)
                for sent in sentences:
                    if s.cancelled:
                        return
                    if not first_chunk_done and len(sent.strip()) >= FIRST_SENT_MIN:
                        # opening sentence: speak it at once, no coalescer hold
                        spoken = clean_for_speech(sent)
                        if spoken:
                            _mark_chunk()
                            await speak_chunk(spoken, True)
                            first_chunk_done = True
                            t_first_chunk = time.time() - t_llm0
                            log.info(f"[TTS req_id={req_id}] first sentence chars={len(spoken)} after={t_first_chunk:.2f}s")
                        continue
                    for chunk in coalescer.feed(sent):
                        spoken = clean_for_speech(chunk)
                        if spoken:
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
            s.history.append({"role": "assistant", "content": reply})
            s.turns.append({"role": "assistant", "text": reply, "t": time.time()})
            log.info(f"[LLM req_id={req_id}] LLM response received chars={len(reply)}")
            log.info(f"[TURN req_id={req_id}] done turn_s={time.time()-t_turn0:.2f}s reply_chars={len(reply)}")
            await send({"type": "llm_done", "text": reply,
                        "display_text": strip_emotion_tags(reply),
                        "correction": [ {"kind": m.kind, "hint": m.correction_hint} for m in to_correct ],
                        "level": s.level, "confidence": conf,
                        "req_id": req_id,
                        "turn": turn_id})
            await send({"type": "status", "state": "LISTENING", "req_id": req_id, "turn": turn_id})
        except asyncio.CancelledError:
            log.info(f"[TURN req_id={req_id}] task cancelled")
            await send({"type": "llm_cancelled", "req_id": req_id, "turn": turn_id})
            raise
        except Exception as e:
            err_msg = sanitize_error(str(e))
            log.error(f"[ERROR req_id={req_id}] stage=LLM error={err_msg}")
            await send({"type": "error", "scope": "llm", "stage": "LLM", "message": err_msg, "req_id": req_id, "turn": turn_id})
            # graceful spoken fallback (still conversational)
            fb = "Sorry, I had a little hiccup. Can you say that again?"
            await send({"type": "llm_done", "text": fb, "display_text": fb, "correction": [], "level": s.level, "confidence": conf, "req_id": req_id, "turn": turn_id})
            await send({"type": "status", "state": "LISTENING", "req_id": req_id, "turn": turn_id})

    async def close_live(self, s: Session) -> None:
        """Release the Gemini Live Bidi socket (frees the Google-side session
        slot). Called on WS disconnect and before replacing the session."""
        live_sess = getattr(s, "_live_session", None)
        s._live_session = None
        if live_sess:
            try:
                await live_sess.close()
            except Exception:
                pass

    def cancel(self, sid: str):
        s = self.sessions.get(sid)
        if s:
            s.cancelled = True
        t = self.tasks.pop(sid, None)
        if t and not t.done():
            t.cancel()

    def end_session(self, s: Session) -> dict:
        dur = time.time() - s.start_t
        profile = load_profile(settings.data_dir, s.user_id)
        record_session(profile)
        profile["minutes_spoken"] = round(profile.get("minutes_spoken", 0) + dur / 60, 2)
        save_profile(settings.data_dir, profile)
        sess = {"turns": s.turns, "mistake_counts": s.mistake_counts,
                "level": s.level, "hindi_used": s.hindi_used, "duration_s": dur}
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
        return summary


engine = Engine()
