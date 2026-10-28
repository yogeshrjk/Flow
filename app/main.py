"""FastAPI entry: serves UI + WS realtime + REST. Free-first, zero required keys."""
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.config import settings, RESPONSE_MODES, normalize_response_mode
from app.conversation.engine import engine
from app.conversation.prompts import LANGUAGES, LANGUAGE_STT_CODES
from app.coaching.roleplay import SCENARIOS, today_challenge
from app.coaching.memory import load_profile, save_profile
from app.providers.tts.fish import FishProvider
from app.providers.tts.fish_library import (
    LANGUAGES as FISH_LIBRARY_LANGUAGES,
    FishLibraryError,
    fish_voice_library,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")

from contextlib import asynccontextmanager
from app.identity import issue_identity, read_identity
from app.coaching.memory import validate_id


@asynccontextmanager
async def lifespan(app):
    yield
    tasks = list(engine.tasks.values()) + list(engine.reading_tasks.values()) + list(engine.greeting_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for session in list(engine.sessions.values()):
        await engine.close_live(session)
    engine.tasks.clear()
    engine.reading_tasks.clear()
    engine.greeting_tasks.clear()
    engine.sessions.clear()
    from app.providers.llm import groq, gemini
    from app.providers.stt import groq_whisper
    from app.providers.tts import fish, gemini as gemini_tts
    for module in (groq, gemini, groq_whisper, fish, gemini_tts):
        close = getattr(module, "_close_client", None)
        if close:
            await close()
    client = getattr(fish_voice_library, "_client", None)
    if client and not client.is_closed:
        await client.aclose()


app = FastAPI(title="Flow", lifespan=lifespan)
from collections import deque
_request_windows = {}


def permit_request(key, limit=60):
    now = time.monotonic()
    if len(_request_windows) > 4096:
        for old_key, times in list(_request_windows.items()):
            if not times or now - times[-1] > 60:
                _request_windows.pop(old_key, None)
        if len(_request_windows) > 4096 and key not in _request_windows:
            return False
    times = _request_windows.setdefault(key, deque())
    while times and now - times[0] >= 60:
        times.popleft()
    if len(times) >= limit:
        return False
    times.append(now)
    return True


def same_origin(origin, host):
    from urllib.parse import urlsplit
    if not origin:
        return True
    return origin in settings.allowed_origins.split(",") or urlsplit(origin).netloc == host


def owned_session(sid, uid):
    try:
        validate_id(sid)
        # Namespace browser-provided IDs; two browsers can safely use /api/turn.
        import hashlib
        internal = hashlib.sha256((uid + ":" + sid).encode()).hexdigest()[:32]
        return engine.get_or_create(internal, user_id=uid)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


async def cancel_turn(s):
    pending = [mapping.get(s.id) for mapping in (engine.tasks, engine.reading_tasks, engine.greeting_tasks)]
    pending = [t for t in pending if t and not t.done() and t is not asyncio.current_task()]
    if not pending:
        return  # Keep a healthy completed Live connection warm; never send an empty user turn.
    engine.cancel(s.id)
    if pending:
        try:
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=3)
        except TimeoutError:
            pass


@app.middleware("http")
async def browser_identity(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request.headers.get("origin"), request.headers.get("host")):
        return JSONResponse({"error": "Origin rejected"}, status_code=403)
    if request.url.path.startswith("/api/") and (request.method == "POST" or request.url.path.startswith(("/api/tts", "/api/voice-library"))):
        if not permit_request((request.client.host if request.client else "unknown", "http")):
            return JSONResponse({"error": "Practice requests are arriving too quickly; pause briefly."}, status_code=429, headers={"Retry-After": "60"})
    cookie = request.cookies.get("flow_learner", "")
    uid = read_identity(cookie)
    fresh = uid is None
    if fresh:
        cookie = issue_identity()
        uid = read_identity(cookie)
    request.state.learner = uid
    response = await call_next(request)
    if fresh:
        response.set_cookie("flow_learner", cookie, httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=31536000)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_no_cache_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
async def index():
    return FileResponse(
        str(STATIC / "index.html"),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/health")
async def health():
    return {
        "ok": True,
        "llm": getattr(engine.llm, "name", "?"),
        "llm_models": getattr(engine.llm, "models", [getattr(engine.llm, "name", "?")]),
        "has_gemini": settings.has_gemini,
        "has_fish": settings.has_fish,
        "stt": "browser (free)",
        "vad": "client-energy (local, free)",
        "time": time.time(),
    }


@app.get("/api/config")
async def api_config():
    """Client config with available voice assistant engine modes."""
    return {
        "tts_provider": settings.tts_provider,
        "cost_note": "Use free-tier provider accounts. Account quotas apply; Flow never substitutes browser speech.",
        "response_modes": [
            {"id": "gemini_live", "label": "Gemini Live Preview",
             "desc": "Real-time native voice assistant powered by Gemini Live with 16 celestial voices.",
             "available": engine.mode_available("gemini_live"), "error": "" if engine.mode_available("gemini_live") else engine.mode_error("gemini_live")},
            {"id": "groq_fish", "label": "Groq + Fish Audio",
             "desc": "Streaming Groq responses with Fish Audio voice.",
             "available": engine.mode_available("groq_fish"), "error": "" if engine.mode_available("groq_fish") else engine.mode_error("groq_fish")},
            {"id": "gemini_fish", "label": "Gemini + Fish Audio",
             "desc": "Natural voice conversation with Gemini & Fish Audio.",
             "available": engine.mode_available("gemini_fish"), "error": "" if engine.mode_available("gemini_fish") else engine.mode_error("gemini_fish")},
        ],
        "default_response_mode": settings.default_response_mode,
        "tts": settings.tts_provider,
        "languages": [
            {"id": lid, "label": disp, "stt": LANGUAGE_STT_CODES.get(lid, "en-IN")}
            for lid, disp in LANGUAGES.items()
        ],
        "fish_voices": FISH_VOICES,
        "fish_library_languages": [
            {"id": code, "label": label} for code, label in FISH_LIBRARY_LANGUAGES.items()
        ],
        "default_fish_voice": DEFAULT_FISH_VOICE,
        "gemini_voices": GEMINI_VOICES,
        "default_gemini_voice": DEFAULT_GEMINI_VOICE,
        "fish_key_set": settings.has_fish,
        "gemini_key_set": settings.has_gemini,
        "stt": "browser (free)",
        "server_stt": settings.has_groq,
        "modes": ["free", "reading", "training", "practice", "roleplay", "listening"],
        "scenarios": [{"id": k, **v} for k, v in SCENARIOS.items()],
    }


@app.get("/api/voice-library")
async def voice_library(language: str, page: int = 1, title: str = ""):
    """Return one cached page of public Fish Audio models for a language."""
    try:
        return await fish_voice_library.list_public_models(language, page, title)
    except FishLibraryError as exc:
        headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
        return JSONResponse({"error": exc.message}, status_code=exc.status_code, headers=headers)


@app.get("/api/voice-library/validate")
async def validate_library_voice(voice_id: str):
    """Verify that a selected library voice is still public before using it."""
    try:
        if await fish_voice_library.is_public_model(voice_id):
            return {"public": True}
        return JSONResponse({"error": "Voice is not public or is unavailable."}, status_code=404)
    except FishLibraryError as exc:
        headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
        return JSONResponse({"error": exc.message}, status_code=exc.status_code, headers=headers)


@app.get("/api/challenge/today")
async def challenge():
    return today_challenge()


@app.get("/api/profile")
async def profile(request: Request):
    return load_profile(settings.data_dir, request.state.learner)


class TTSReq(BaseModel):
    text: str = Field(min_length=1, max_length=800)
    voice: str = ""  # fish reference_id or gemini voice name
    engine: str = ""  # "fish" | "gemini" | ""


from app.voices import FISH_VOICES, DEFAULT_FISH_VOICE, GEMINI_VOICES, DEFAULT_GEMINI_VOICE


async def fish_voice_validation_error(voice_id: str):
    if voice_id in {voice["id"] for voice in GEMINI_VOICES}:
        return JSONResponse({"error": "Choose a Fish Audio voice for the selected Fish response mode."}, status_code=400)
    if not voice_id or voice_id in {voice["id"] for voice in FISH_VOICES}:
        return None
    try:
        if await fish_voice_library.is_public_model(voice_id):
            return None
    except FishLibraryError as exc:
        headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
        return JSONResponse({"error": exc.message}, status_code=exc.status_code, headers=headers)
    return JSONResponse({"error": "unknown or non-public voice"}, status_code=400)


@app.post("/api/tts")
async def tts(req: TTSReq):
    """Voice synthesis: routes to Gemini Live native voice or Fish Audio."""
    from fastapi.responses import Response
    from app.conversation.engine import clean_for_speech, sanitize_error
    raw_text = (req.text or "").strip()[:800]
    if not raw_text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    text = clean_for_speech(raw_text)
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    vid = (req.voice or "").strip() or None
    is_gemini_voice = (req.engine == "gemini") or (not req.engine and vid in {v["id"] for v in GEMINI_VOICES})

    if is_gemini_voice:
        from app.providers.tts.gemini import GeminiTTSProvider
        g_voice = vid if vid in {v["id"] for v in GEMINI_VOICES} else DEFAULT_GEMINI_VOICE
        log.info(f"[TTS] request started engine=gemini voice={g_voice} chars={len(text)}")
        audio, err = await GeminiTTSProvider().synthesize(text, voice_name=g_voice)
        if audio:
            return Response(content=audio, media_type="audio/wav")
        log.warning(f"[ERROR] stage=TTS engine=gemini error={sanitize_error(err)}")
        return JSONResponse({"error": err or "gemini live audio failed"}, status_code=402)

    voice_error = await fish_voice_validation_error(vid or "")
    if voice_error:
        return voice_error
    from app.providers.tts.fish import FishProvider
    log.info(f"[TTS] request started engine=fish voice={vid} chars={len(text)}")
    audio, err = await FishProvider().synthesize(text, voice_id=vid)
    if audio:
        return Response(content=audio, media_type="audio/mpeg")
    log.warning(f"[ERROR] stage=TTS engine=fish error={sanitize_error(err)}")
    return JSONResponse({"error": err or "fish failed"}, status_code=402)


@app.get("/api/tts/stream")
async def tts_stream(text: str = "", voice: str = "", engine: str = ""):
    """Progressive voice streaming."""
    from fastapi.responses import StreamingResponse
    from app.conversation.engine import clean_for_speech, sanitize_error
    raw_txt = (text or "").strip()[:800]
    if not raw_txt:
        return JSONResponse({"error": "empty text"}, status_code=400)
    txt = clean_for_speech(raw_txt)
    if not txt:
        return JSONResponse({"error": "empty text"}, status_code=400)
    vid = (voice or "").strip() or None
    is_gemini_voice = (engine == "gemini") or (not engine and vid in {v["id"] for v in GEMINI_VOICES})

    if is_gemini_voice:
        return await tts(TTSReq(text=txt, voice=vid or "", engine="gemini"))

    voice_error = await fish_voice_validation_error(vid or "")
    if voice_error:
        return voice_error
    log.info(f"[TTS] stream request started engine=fish voice={vid} chars={len(txt)}")
    it, err = await FishProvider().open_stream(txt, voice_id=vid)
    if it is None:
        log.warning(f"[ERROR] stage=TTS_STREAM error={sanitize_error(err)}")
        return JSONResponse({"error": err or "fish failed"}, status_code=402)
    return StreamingResponse(it, media_type="audio/mpeg", headers={
        "Cache-Control": "no-store",
        "Accept-Ranges": "none",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/tts/test")
async def tts_test(voice: str = "", engine: str = ""):
    """One-click voice test for either Gemini Live voice or Fish Audio voice."""
    vid = (voice or "").strip()
    is_gemini = (engine == "gemini") or (not engine and vid in {v["id"] for v in GEMINI_VOICES})
    if is_gemini:
        return await tts(TTSReq(text="Hello! This is a Gemini Live voice test. How do I sound?", voice=vid, engine="gemini"))
    voice_error = await fish_voice_validation_error(vid or "")
    if voice_error:
        return voice_error
    return await tts(TTSReq(text="Hey! This is a Fish Audio voice test. How do I sound?", voice=vid, engine="fish"))


@app.post("/api/stt")
async def stt_transcribe(request: Request):
    """Transcribe one recorded utterance (raw audio bytes in the body).

    Lets mobile hold ONE mic stream open for the whole session: the client
    records on its held getUserMedia stream and POSTs each utterance here
    instead of relying on Web Speech, whose platform-owned stream auto-ends
    on silence/utterance boundaries. Optional `X-Language` header (`en`/`hi`)
    only picks the prompt sample as a script hint — the spoken language is
    always auto-detected from the audio.
    """
    from app.providers.stt.groq_whisper import MAX_AUDIO_BYTES, GroqWhisperSTT
    ctype = (request.headers.get("content-type") or "").lower()
    if "audio/" not in ctype and "video/webm" not in ctype:
        return JSONResponse({"error": "send raw audio bytes with an audio Content-Type"}, status_code=415)
    provider = GroqWhisperSTT()
    if not provider.available:
        return JSONResponse({"error": "server transcription unavailable"}, status_code=503)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_AUDIO_BYTES:
            return JSONResponse({"error": "audio too large"}, status_code=413)
        data.extend(chunk)
    data = bytes(data)
    if not data or len(data) < 1024:
        return JSONResponse({"error": "empty audio"}, status_code=400)
    if len(data) > MAX_AUDIO_BYTES:
        return JSONResponse({"error": "audio too large"}, status_code=413)
    lang = (request.headers.get("x-language") or "").strip().lower()[:16]
    lang = lang if lang in LANGUAGES else None
    fname = (request.headers.get("x-filename") or "").strip()[-40:] or "utterance.webm"
    fname = "".join(c for c in fname if c.isalnum() or c in (".", "-", "_")) or "utterance.webm"
    # forward the real container type: Groq sniffs content AND the part type,
    # so a hardcoded webm here rejects Safari mp4 recordings.
    ftype = ctype.split(";")[0].strip() or "audio/webm"
    # Always auto-detect the spoken language: forcing `en` on Hindi speech
    # mangles it ("kal" -> "kil") and a Hindi prompt on English speech hijacks
    # the output — both verified live. X-Language only picks the prompt
    # sample (a script hint for short/ambiguous utterances); detection itself
    # follows the audio and wins on clear speech either way.
    log.info(f"[STT] transcribe request bytes={len(data)} hint={lang or 'auto'}")
    sid = (request.headers.get("x-session") or "").strip()[:32]
    raw_level = request.headers.get("x-level")
    try:
        # Absent header (older clients) means "unknown" — skip the energy rule;
        # an explicit low reading means the mic captured no real speech.
        peak = float(raw_level.strip()) if raw_level is not None else None
    except (ValueError, AttributeError):
        peak = None
    if sid:
        sid = owned_session(sid, request.state.learner).id
    prompt, context = _stt_prompt(lang, sid)
    text, err, detected = await provider.transcribe(data, filename=fname, language=None, content_type=ftype, prompt=prompt)
    if err:
        log.warning(f"[STT] transcribe failed: {err[:120]}")
        return JSONResponse({"error": err}, status_code=502)
    if _looks_hallucinated(text, context, peak):
        log.warning(f"[STT] hallucinated transcript dropped peak={peak}: {text[:80]}")
        return JSONResponse({"error": "No speech detected"}, status_code=502)
    if (request.headers.get("x-barged") or "").strip() == "1" and _is_echo_of_assistant(text, sid):
        log.warning(f"[STT] speaker-echo transcript dropped: {text[:80]}")
        return JSONResponse({"error": "No speech detected"}, status_code=502)
    from app.conversation.intent import clean_speech_artifacts, detect_language_switch_request, detect_language_from_content
    text = clean_speech_artifacts(text, context=context)
    req_switch = detect_language_switch_request(text)
    if req_switch:
        detected = req_switch
    elif not detected or detected == "english":
        content_lang = detect_language_from_content(text)
        if content_lang:
            detected = content_lang
    log.info(f"[STT] transcript ok detected={detected} hint={lang or 'auto'}")
    if sid and detected and sid in engine.sessions:
        try:
            sess = engine.sessions[sid]
            sess.last_spoken_lang = detected
            if req_switch or (detected and detected != "english"):
                sess.language = detected
        except Exception:
            pass
    return {"text": text, "language": detected}


# Classic Whisper-on-noise signatures: looped words, caption-site junk,
# prompt parroted back on silence, or hallucinations on background audio / whistle / snoring.
_STT_JUNK_PHRASES = (
    "thanks for watching", "thank you for watching", "subscribe",
    "transcription by", "amara.org", "http://", "https://", "www.",
    "what is it about that", "so what is it about that", "what is that about",
    "thanks for listening", "thank you for listening",
    "like and subscribe", "please subscribe",
)

_QUIET_NOISE_PHRASES = (
    "thank you", "thanks", "thank you very much", "thank you so much",
    "you", "bye", "bye-bye", "goodbye", "the end", "see you next time",
)


def _word_overlap(a: str, b: str) -> float:
    aw = [w for w in a.lower().split() if w]
    if not aw:
        return 0.0
    bw = set(b.lower().split())
    hit = sum(1 for w in aw if w in bw)
    return hit / len(aw)


def _looks_hallucinated(text: str, context: str, peak: float | None) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    import re
    no_brackets = re.sub(r"\[.*?\]|\(.*?\)", "", cleaned).strip()
    if not no_brackets:
        return True
    words = no_brackets.split()
    if not words:
        return True
    low = no_brackets.lower().strip().rstrip(".!?,")
    if any(p in low for p in _STT_JUNK_PHRASES):
        return True
    if low in _QUIET_NOISE_PHRASES:
        if peak is not None and peak < 0.035:
            return True
    if len(words) >= 4 and len({w.lower() for w in words}) / len(words) < 0.4:
        return True
    # Background voice / faint ambient speech rejection:
    # Direct near-field user speech directed at the microphone has peak >= 0.028.
    # Distant background voices or ambient room chatter have peak < 0.024.
    if peak is not None and peak < 0.024:
        return True
    # Short utterances (<= 3 words) with low peak (< 0.028) are background snippets
    if peak is not None and peak < 0.028 and len(words) <= 3:
        return True
    return False


def _is_echo_of_assistant(text: str, sid: str) -> bool:
    """True when a barge-time transcript just repeats what the AI just said:
    speaker echo leaking past cancellation, not the user speaking."""
    words = (text or "").split()
    if len(words) < 4 or not sid:
        return False
    try:
        from app.conversation.engine import engine as _eng

        turns = _eng.sessions.get(sid, None)
        history = (turns.turns if turns else [])
        last_ai = ""
        for t in reversed(history):
            if t.get("role") == "assistant" and (t.get("text") or "").strip():
                last_ai = t["text"]
                break
        if not last_ai:
            return False
        return _word_overlap(text, last_ai) >= 0.8
    except Exception:
        return False


# Whisper prompt: a short sample in the user's PREFERRED language (their
# setting) plus recent turns. The sample is load-bearing for short
# utterances ("haan" with no sample came back "Huh?"), and detection always
# follows the audio, so a preferred-language sample never blocks another
# language — it only steers ambiguous shorts toward the preferred script.
# Capped short — long prompts get truncated server-side anyway.
_STT_SAMPLES = {
    "english": "Hey, how are you doing today? I'm doing well, thanks for asking.",
    "hindi": "नमस्ते, आप कैसे हैं? मैं बिल्कुल ठीक हूँ, धन्यवाद।",
    "spanish": "Hola, ¿cómo estás hoy? Estoy muy bien, gracias por preguntar.",
    "french": "Bonjour, comment vas-tu aujourd'hui ? Je vais très bien, merci.",
    "german": "Hallo, wie geht es dir heute? Mir geht es gut, danke.",
    "portuguese": "Olá, como você está hoje? Estou bem, obrigado por perguntar.",
    "italian": "Ciao, come stai oggi? Sto bene, grazie.",
    "dutch": "Hallo, hoe gaat het vandaag? Met mij gaat het goed, bedankt.",
    "russian": "Привет, как дела сегодня? У меня всё хорошо, спасибо.",
    "japanese": "こんにちは、今日はお元気ですか？元気です、ありがとう。",
    "korean": "안녕하세요, 오늘 어떻게 지내세요? 잘 지내고 있어요, 감사합니다.",
    "chinese": "你好，今天怎么样？我很好，谢谢。",
    "arabic": "مرحبا، كيف حالك اليوم؟ أنا بخير، شكرا لسؤالك.",
    "tamil": "வணக்கம், இன்று எப்படி இருக்கிறீர்கள்? நான் நன்றாக இருக்கிறேன், நன்றி.",
    "telugu": "నమస్కారం, ఈరోజు ఎలా ఉన్నారు? నేను బాగున్నాను, ధన్యవాదాలు.",
    "bengali": "নমস্কার, আজ কেমন আছেন? আমি ভালো আছি, ধন্যবাদ।",
    "marathi": "नमस्कार, आज कसे आहात? मी ठीक आहे, धन्यवाद.",
    "urdu": "ہیلو، آج آپ کیسے ہیں؟ میں ٹھیک ہوں، پوچھنے کا شکریہ.",
}


def _stt_prompt(lang: str | None, sid: str) -> tuple[str, str]:
    """Return (whisper prompt, session context). Context is also returned
    separately so the hallucination filter can spot prompt-parroting.
    Samples steer short/ambiguous utterances; when the user recently spoke a
    different language than their preferred one, that language's sample rides
    along too — switchers stay covered without any extra round trip."""
    context = ""
    last_spoken = ""
    if sid:
        try:
            from app.conversation.engine import engine as _eng

            sess = _eng.sessions.get(sid, None)
            recent = (sess.turns if sess else [])[-3:]
            context = " / ".join(
                str((t.get("text") or "")).strip()[:100] for t in recent
                if (t.get("text") or "").strip()
            ).strip()
            if sess and getattr(sess, "last_spoken_lang", ""):
                last_spoken = sess.last_spoken_lang
        except Exception:
            context = ""
    parts = []
    if context:
        parts.append(context)
    if lang in _STT_SAMPLES:
        parts.append(_STT_SAMPLES[lang])
    if last_spoken and last_spoken != lang and last_spoken in _STT_SAMPLES:
        parts.append(_STT_SAMPLES[last_spoken])
    return " ".join(parts)[:300], context


class TurnReq(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    speech_duration_s: float | None = Field(default=None, ge=0, le=180, allow_inf_nan=False)
    mode: str = "free"
    scenario: str = "casual"
    correction: str = "balanced"
    level: str = "auto"
    language: str = "english"
    voice_id: str = ""
    response_mode: str = ""
    history: list[dict] = Field(default_factory=list, max_length=24)
    t0: float | None = None
    turn_id: str = ""
    req_id: str = ""
    reply_lang: str = ""  # detected spoken language for THIS turn; reply follows it

    @field_validator("history")
    @classmethod
    def safe_history(cls, history):
        if any(h.get("role") not in ("user", "assistant") or not isinstance(h.get("content"), str) or len(h["content"]) > 4000 for h in history):
            raise ValueError("History must contain bounded user/assistant text only")
        return history


@app.post("/api/session/{sid}/turn")
@app.post("/api/turn")
async def api_turn(req: TurnReq, request: Request, sid: str = "default"):
    """Server-Sent Events (SSE) streaming turn endpoint for serverless/HTTP environments."""
    from fastapi.responses import StreamingResponse
    from app.conversation.engine import sanitize_error
    s = owned_session(sid, request.state.learner)
    await cancel_turn(s)
    if req.mode in ("free", "reading", "training", "practice", "correction", "pronunciation", "roleplay", "vocab", "interview", "challenge", "listening"):
        s.mode = req.mode
    if req.scenario:
        s.scenario = req.scenario
    if req.correction in ("passive", "balanced", "active"):
        s.correction = req.correction
    if req.level:
        s.level_setting = req.level
    if req.language in LANGUAGES:
        s.language = req.language
    if req.voice_id:
        valid_voice = req.voice_id in {v["id"] for v in GEMINI_VOICES}
        voice_error = None if valid_voice else await fish_voice_validation_error(req.voice_id)
        if voice_error:
            return voice_error
        s.voice_id = req.voice_id
    if req.response_mode:
        if req.response_mode not in (*RESPONSE_MODES, "fast", "quality", "live"):
            raise HTTPException(400, "Choose Gemini Live Preview, Groq + Fish Audio, or Gemini + Fish Audio")
        s.response_mode = normalize_response_mode(req.response_mode)
    s.history = req.history[-16:]

    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    stream_active = True

    async def sse_send(msg: dict):
        if stream_active:
            await queue.put(f"data: {json.dumps(msg)}\n\n")

    t0 = time.time()
    turn_id = req.turn_id or str(int(t0 * 1000))
    req_id = req.req_id or turn_id

    async def event_generator():
        nonlocal stream_active
        turn_task = asyncio.create_task(engine.handle_user_turn(
            s, req.text, sse_send, t0,
            t_speech_end_ms=req.t0, turn_id=turn_id, req_id=req_id,
            reply_lang=req.reply_lang, speech_duration_s=req.speech_duration_s,
        ))
        engine.tasks[s.id] = turn_task
        try:
            while not turn_task.done() or not queue.empty():
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield chunk
                except asyncio.TimeoutError:
                    continue
            await turn_task
            while not queue.empty():
                yield queue.get_nowait()
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            turn_task.cancel()
            raise
        except Exception as e:
            err_msg = sanitize_error(str(e))
            yield f"data: {json.dumps({'type': 'error', 'scope': 'turn', 'stage': 'HTTP_STREAM', 'message': err_msg, 'req_id': req_id, 'turn': turn_id})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            stream_active = False
            if not turn_task.done():
                turn_task.cancel()
            await asyncio.gather(turn_task, return_exceptions=True)
            if engine.tasks.get(s.id) is turn_task:
                engine.tasks.pop(s.id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.post("/api/session/{sid}/end")
async def end_session(sid: str, request: Request):
    s = owned_session(sid, request.state.learner)
    await cancel_turn(s)
    summary = engine.end_session(s)
    return summary


@app.websocket("/ws/session/{sid}")
async def ws_session(ws: WebSocket, sid: str, lang: str = ""):
    uid = read_identity(ws.cookies.get("flow_learner", ""))
    if not uid or not same_origin(ws.headers.get("origin"), ws.headers.get("host")):
        await ws.close(code=4403)
        return
    try:
        s = owned_session(sid, uid)
    except HTTPException:
        await ws.close(code=4400)
        return
    previous = getattr(s, "_socket", None)
    if previous:
        await previous.close(code=4000)
    await ws.accept()
    s._socket = ws
    s._connected = True
    lang = (lang or "").lower()
    if lang in LANGUAGES:
        s.language = lang
    log.info(f"[WS] open sid={s.id}")
    await ws.send_json({"type": "ready", "session_id": s.id, "mode": s.mode,
                        "correction": s.correction, "level": s.level,
                        "response_mode": s.response_mode or settings.default_response_mode})
    # No welcome message: the session starts silent when the user presses
    # start (mic). The user always speaks first.

    send_lock = asyncio.Lock()
    async def send(obj: dict):
        async with send_lock:
            await ws.send_json(obj)

    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > 65536:
                await ws.close(code=1009)
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if not isinstance(msg, dict):
                await send({"type": "error", "message": "Expected a JSON object"})
                continue
            s.last_active = time.monotonic()
            mtype = msg.get("type")
            try:
                if mtype == "user_transcript":
                    if not permit_request((uid, "turn")):
                        await send({"type": "error", "message": "Too many turns; pause briefly before trying again."})
                        continue
                    t0 = time.time()
                    # cancel any in-flight reply (barge-in safety)
                    await cancel_turn(s)
                    parsed = TurnReq(text=msg.get("text", ""), speech_duration_s=msg.get("speech_duration_s"))
                    # t0/turn_id come from the client so "speech_end → first audio" is real
                    turn_id = str(msg.get("turn_id", ""))
                    req_id = str(msg.get("req_id") or turn_id or s.id)
                    task = asyncio.create_task(engine.handle_user_turn(
                        s, parsed.text, send, t0,
                        t_speech_end_ms=msg.get("t0"), turn_id=turn_id, req_id=req_id,
                        reply_lang=str(msg.get("reply_lang", "")), speech_duration_s=parsed.speech_duration_s))
                    engine.tasks[s.id] = task
                    task.add_done_callback(lambda t: engine.tasks.pop(s.id, None) if engine.tasks.get(s.id) is t else None)
                    # never await here: the receive loop must keep reading, otherwise a
                    # barge_in (or the next turn) can't cancel this turn mid-reply
                    task.add_done_callback(lambda t: t.cancelled() or
                                           (t.exception() and log.warning(f"[TURN req_id={req_id}] task error: {t.exception()}")))
                elif mtype == "partial":
                    # useful for interruption prediction / latency logging only
                    log.info(f"[STT] partial sid={s.id} {str(msg.get('text',''))[:80]!r}")
                elif mtype == "barge_in":
                    log.info(f"[AUDIO] barge-in sid={s.id} — cancelling TTS+LLM")
                    await cancel_turn(s)
                    await engine.close_live(s)
                    await send({"type": "status", "state": "INTERRUPTED"})
                    await send({"type": "status", "state": "LISTENING"})
                elif mtype == "set_mode":
                    mode = str(msg.get("mode", "free"))
                    if mode in ("free", "reading", "training", "practice", "correction", "pronunciation", "roleplay", "vocab", "interview", "challenge", "listening"):
                        previous_mode = s.mode
                        if previous_mode != mode:
                            await cancel_turn(s)
                            await engine.close_live(s)
                        s.mode = mode
                        if msg.get("scenario"):
                            # A generic settings value (usually "casual")
                            # accompanies reconnect/UI syncs. Do not replace
                            # the scenario the user already selected in an
                            # active roleplay with that unrelated value.
                            if not (mode == "roleplay" and previous_mode == "roleplay" and s.roleplay_started):
                                s.scenario = msg["scenario"]
                        if mode == "challenge":
                            s.challenge = today_challenge()
                        if mode == "reading":
                            if not (s.reading and (s.reading.active or s.reading.state == "GENERATING_PASSAGE")):
                                task = asyncio.create_task(engine.start_reading(s, send))
                                engine.reading_tasks[s.id] = task
                        else:
                            if s.reading and s.reading.active:
                                engine.reading_off(s)
                                await send({"type": "reading_state", "state": "off"})
                            # Dynamic AI greeting for conversation, training, and roleplay
                            greeted = getattr(s, "_greeted_modes", None)
                            if greeted is None:
                                s._greeted_modes = set()
                                greeted = s._greeted_modes
                            if previous_mode != mode or mode not in greeted or msg.get("force_greet"):
                                greeted.add(mode)
                                task = asyncio.create_task(engine.start_greeting(s, send))
                                engine.greeting_tasks[s.id] = task
                        await send({"type": "mode", "mode": s.mode, "scenario": s.scenario})
                        if mode == "training":
                            from app.coaching.training import get_active_lesson, get_learning_path
                            prof = load_profile(settings.data_dir, s.user_id)
                            les = get_active_lesson(prof)
                            await send({"type": "training_update", "lesson": les,
                                        "mastered": prof.get("training", {}).get("mastered_topics", []),
                                        "practice_needed": prof.get("training", {}).get("practice_needed", []),
                                        "learning_path": get_learning_path(prof)})
                        elif mode == "roleplay":
                            # The client syncs set_mode on reconnect and UI
                            # refreshes. Preserve a selected roleplay rather
                            # than silently resetting it on either of those.
                            if previous_mode != "roleplay" or msg.get("reset_roleplay"):
                                s.roleplay_started = False
                                s.roleplay_scenario_title = "Choose a Scenario"
                                s.roleplay_role = ""
                            await send({"type": "roleplay_update", "title": s.roleplay_scenario_title or "Choose a Scenario", "role": s.roleplay_role})
                elif mtype == "set_correction":
                    if msg.get("value") in ("passive", "balanced", "active"):
                        s.correction = msg["value"]
                        await send({"type": "correction", "value": s.correction})
                elif mtype == "set_level":
                    s.level_setting = str(msg.get("value", "auto"))
                    await send({"type": "level", "value": s.level_setting})
                elif mtype == "set_language":
                    lang = str(msg.get("value", "english")).lower()
                    if lang in LANGUAGES:
                        s.language = lang
                        await send({"type": "language", "value": s.language})
                elif mtype == "set_response_mode":
                    mode = str(msg.get("value", "")).lower()
                    if mode in (*RESPONSE_MODES, "fast", "quality", "live"):
                        mode = normalize_response_mode(mode)
                        if s.response_mode != mode:
                            await cancel_turn(s)
                            await engine.close_live(s)
                        s.response_mode = mode
                        await send({"type": "response_mode", "value": mode, "available": engine.mode_available(mode), "message": "" if engine.mode_available(mode) else engine.mode_error(mode)})
                    else:
                        await send({"type": "error", "message": "Choose one of the three supported response modes."})
                elif mtype == "set_voice":
                    vid = str(msg.get("value", ""))
                    from app.voices import FISH_VOICES, GEMINI_VOICES, persona_for
                    valid_ids = {v["id"] for v in FISH_VOICES} | {v["id"] for v in GEMINI_VOICES}
                    valid = False
                    try:
                        valid = bool(vid) and (vid in valid_ids or await fish_voice_library.is_public_model(vid))
                    except Exception as exc:
                        log.warning(f"[WS] voice validation error: {exc}")
                        valid = False
                    if valid:
                        if s.voice_id and s.voice_id != vid:
                            await cancel_turn(s)
                            await engine.close_live(s)
                        s.voice_id = vid
                        pname, pgender = persona_for(vid)
                        await send({"type": "voice", "value": vid, "persona": pname})
                    else:
                        s.voice_id = DEFAULT_FISH_VOICE
                        pname, pgender = persona_for(s.voice_id)
                        await send({"type": "voice", "value": s.voice_id, "persona": pname})
                elif mtype == "sync_history":
                    turns = msg.get("turns") or []
                    if not isinstance(turns, list) or len(turns) > 40:
                        raise ValueError("Too much history")
                    mode = msg.get("mode") or s.mode
                    if isinstance(turns, list) and turns:
                        engine.sync_past_history(s, turns, mode=mode)
                elif mtype == "clear_history":
                    mode = msg.get("mode") or s.mode
                    if mode and hasattr(s, "mode_histories") and mode in s.mode_histories:
                        s.mode_histories[mode].clear()
                        s.mode_turns[mode].clear()
                        s.mode_summaries[mode] = ""
                    else:
                        s.history.clear()
                        s.turns.clear()
                        s.past_chat_summary = ""
                    try:
                        profile = load_profile(settings.data_dir, s.user_id)
                        if "mode_summaries" in profile and isinstance(profile["mode_summaries"], dict):
                            if mode:
                                profile["mode_summaries"][mode] = ""
                            else:
                                profile["mode_summaries"].clear()
                        profile["last_chat_summary"] = ""
                        save_profile(settings.data_dir, profile)
                    except Exception as e:
                        log.warning(f"[MEMORY] failed to clear profile summaries: {e}")
                elif mtype == "end":
                    summary = engine.end_session(s)
                    await send({"type": "summary", **summary})
                elif mtype == "reading_start":
                    if not (s.reading and (s.reading.active or s.reading.state == "GENERATING_PASSAGE")):
                        task = asyncio.create_task(engine.start_reading(s, send))
                        engine.reading_tasks[s.id] = task
                        task.add_done_callback(lambda t: t.cancelled() or
                                               (t.exception() and log.warning(f"[READING] start error: {t.exception()}")))
                elif mtype == "reading_voice":
                    await engine.handle_reading_voice(s, send)
                elif mtype == "reading_submit":
                    pause_raw = msg.get("pause_total_s")
                    await engine.handle_reading_submit(
                        s, str(msg.get("text", "")), send,
                        round=int(msg.get("round") or 1),
                        gen=int(msg.get("gen") or 0),
                        word_count=int(msg.get("word_count") or 0),
                        duration_s=(float(msg["duration_s"]) if msg.get("duration_s") is not None else None),
                        elapsed_s=(float(msg["elapsed_s"]) if msg.get("elapsed_s") is not None else None),
                        pause_total_s=(float(pause_raw) if pause_raw is not None else None),
                        long_pause_count=int(msg.get("long_pause_count") or 0),
                        pauses=msg.get("pauses") or [],
                    )
                elif mtype == "reading_stop":
                    engine.reading_off(s)
                    await send({"type": "reading_state", "state": "off"})
                elif mtype == "reading_round_ok":
                    ack = engine.reading_acks.get(s.id)
                    if ack:
                        ack.set()
                elif mtype == "ping":
                    await send({"type": "pong"})
            except Exception as me:
                log.error(f"[WS] message handling error mtype={mtype}: {me}", exc_info=True)
    except (WebSocketDisconnect, RuntimeError):
        log.info(f"[WS] close sid={s.id}")
    finally:
        if getattr(s, "_socket", None) is ws:
            s._connected = False
            s._socket = None
            await cancel_turn(s)
            await engine.close_live(s)


@app.get("/api/learning/plan")
async def learning_plan(request: Request):
    from app.coaching.learning import daily_plan
    return daily_plan(load_profile(settings.data_dir, request.state.learner))


class AssessmentReq(BaseModel):
    text: str = Field(min_length=10, max_length=2000)


@app.post("/api/learning/assess")
async def learning_assess(req: AssessmentReq, request: Request):
    from app.coaching.learning import assess
    prof = load_profile(settings.data_dir, request.state.learner)
    result = assess(prof, req.text)
    save_profile(settings.data_dir, prof)
    return result


class ReviewReq(BaseModel):
    card_id: str = Field(max_length=80)
    correct: bool


@app.post("/api/learning/review")
async def learning_review(req: ReviewReq, request: Request):
    from app.coaching.learning import review
    prof = load_profile(settings.data_dir, request.state.learner)
    try:
        result = review(prof, req.card_id, req.correct)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    save_profile(settings.data_dir, prof)
    return result


@app.get("/api/learning/listening")
async def listening_get(request: Request):
    from app.coaching.learning import listening_exercise
    prof = load_profile(settings.data_dir, request.state.learner)
    exercise = listening_exercise(prof)
    save_profile(settings.data_dir, prof)
    return exercise


class ListeningAnswerReq(BaseModel):
    exercise_id: str = Field(max_length=40)
    answer: int = Field(ge=0, le=2, strict=True)


@app.post("/api/learning/listening/answer")
async def listening_answer(req: ListeningAnswerReq, request: Request):
    from app.coaching.learning import grade_listening
    prof = load_profile(settings.data_dir, request.state.learner)
    try:
        result = grade_listening(prof, req.exercise_id, req.answer)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    save_profile(settings.data_dir, prof)
    return result
