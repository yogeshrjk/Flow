"""FastAPI entry: serves UI + WS realtime + REST. Free-first, zero required keys."""
import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import settings
from app.conversation.engine import engine
from app.conversation.prompts import LANGUAGES, LANGUAGE_STT_CODES
from app.coaching.roleplay import SCENARIOS, today_challenge
from app.coaching.memory import load_profile
from app.providers.tts.fish import FishProvider
from app.providers.tts.fish_library import (
    LANGUAGES as FISH_LIBRARY_LANGUAGES,
    FishLibraryError,
    fish_voice_library,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Flow")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC / "index.html"))


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
        "response_modes": [
            {"id": "groq_fish", "label": "Groq + Fish Audio",
             "desc": "Ultra-fast voice response (~400ms TTFT) with Groq & Fish Audio.",
             "available": engine.mode_available("groq_fish")},
            {"id": "gemini_fish", "label": "Gemini + Fish Audio",
             "desc": "Natural voice conversation with Gemini & Fish Audio.",
             "available": engine.mode_available("gemini_fish")},
            {"id": "gemini_live", "label": "Gemini Live Preview",
             "desc": "Gemini Live voice assistant using gemini-3.8-live model.",
             "available": engine.mode_available("gemini_live")},
        ],
        "default_response_mode": settings.default_response_mode,
        "tts": "fish-s2.1-pro-free",
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
        "modes": ["free", "practice"],
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
async def profile(user_id: str = "default"):
    return load_profile(settings.data_dir, user_id)


class TTSReq(BaseModel):
    text: str
    voice: str = ""  # fish reference_id or gemini voice name
    engine: str = ""  # "fish" | "gemini" | ""


from app.voices import FISH_VOICES, DEFAULT_FISH_VOICE, GEMINI_VOICES, DEFAULT_GEMINI_VOICE


async def fish_voice_validation_error(voice_id: str):
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
    is_gemini_voice = (req.engine == "gemini") or (vid in {v["id"] for v in GEMINI_VOICES})

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
    is_gemini_voice = (engine == "gemini") or (vid in {v["id"] for v in GEMINI_VOICES})

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
    is_gemini = (engine == "gemini") or (vid in {v["id"] for v in GEMINI_VOICES})
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
    data = await request.body()
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
    prompt, context = _stt_prompt(lang, sid)
    text, err, detected = await provider.transcribe(data, filename=fname, language=None, content_type=ftype, prompt=prompt)
    if err:
        log.warning(f"[STT] transcribe failed: {err[:120]}")
        return JSONResponse({"error": err}, status_code=502)
    if _looks_hallucinated(text, context, peak):
        log.warning(f"[STT] hallucinated transcript dropped peak={peak}: {text[:80]}")
        return JSONResponse({"error": "I didn't catch that — say it once more?"}, status_code=502)
    if (request.headers.get("x-barged") or "").strip() == "1" and _is_echo_of_assistant(text, sid):
        log.warning(f"[STT] speaker-echo transcript dropped: {text[:80]}")
        return JSONResponse({"error": "I didn't catch that — say it once more?"}, status_code=502)
    log.info(f"[STT] transcript ok detected={detected} hint={lang or 'auto'}")
    if sid and detected and sid in engine.sessions:
        try:
            engine.sessions[sid].last_spoken_lang = detected
        except Exception:
            pass
    return {"text": text, "language": detected}


# Classic Whisper-on-noise signatures: looped words, caption-site junk, or the
# prompt parroted back on silence. Real short replies ("haan", "yes") never
# match: the repetition rule needs 4+ words and the parrot rule 3+.
_STT_JUNK_PHRASES = (
    "thanks for watching", "thank you for watching", "subscribe",
    "transcription by", "amara.org", "http://", "https://", "www.",
)


def _word_overlap(a: str, b: str) -> float:
    aw = [w for w in a.lower().split() if w]
    if not aw:
        return 0.0
    bw = set(b.lower().split())
    hit = sum(1 for w in aw if w in bw)
    return hit / len(aw)


def _looks_hallucinated(text: str, context: str, peak: float | None) -> bool:
    words = (text or "").split()
    if not words:
        return True
    low = text.lower()
    if any(p in low for p in _STT_JUNK_PHRASES):
        return True
    if len(words) >= 4 and len({w.lower() for w in words}) / len(words) < 0.4:
        return True
    if context and len(words) >= 3 and _word_overlap(text, context) >= 0.9:
        return True
    if peak is not None and peak < 0.010 and len(words) <= 2:
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
    if lang in _STT_SAMPLES:
        parts.append(_STT_SAMPLES[lang])
    if last_spoken and last_spoken != lang and last_spoken in _STT_SAMPLES:
        parts.append(_STT_SAMPLES[last_spoken])
    if context:
        parts.append(context)
    return " ".join(parts)[:300], context


class TurnReq(BaseModel):
    text: str
    mode: str = "free"
    scenario: str = "casual"
    correction: str = "balanced"
    level: str = "auto"
    language: str = "english"
    voice_id: str = ""
    response_mode: str = ""
    history: list[dict] = []
    t0: float | None = None
    turn_id: str = ""
    req_id: str = ""
    reply_lang: str = ""  # detected spoken language for THIS turn; reply follows it


@app.post("/api/session/{sid}/turn")
@app.post("/api/turn")
async def api_turn(req: TurnReq, sid: str = "default"):
    """Server-Sent Events (SSE) streaming turn endpoint for serverless/HTTP environments."""
    from fastapi.responses import StreamingResponse
    from app.conversation.engine import sanitize_error
    s = engine.get_or_create(sid)
    if req.mode in ("free", "practice", "correction", "pronunciation", "roleplay", "vocab", "interview", "challenge"):
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
        s.voice_id = req.voice_id
    if req.response_mode:
        s.response_mode = req.response_mode
    if req.history:
        s.history = [h for h in req.history if isinstance(h, dict) and "role" in h and "content" in h][-16:]

    queue: asyncio.Queue = asyncio.Queue()

    async def sse_send(msg: dict):
        await queue.put(f"data: {json.dumps(msg)}\n\n")

    t0 = time.time()
    turn_id = req.turn_id or str(int(t0 * 1000))
    req_id = req.req_id or turn_id

    async def event_generator():
        turn_task = asyncio.create_task(engine.handle_user_turn(
            s, req.text, sse_send, t0,
            t_speech_end_ms=req.t0, turn_id=turn_id, req_id=req_id,
            reply_lang=req.reply_lang,
        ))
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
            engine.cancel(s.id)
            yield f"data: {json.dumps({'type': 'llm_cancelled', 'req_id': req_id, 'turn': turn_id})}\n\n"
            yield "data: [DONE]\n\n"
            raise
        except Exception as e:
            err_msg = sanitize_error(str(e))
            yield f"data: {json.dumps({'type': 'error', 'scope': 'turn', 'stage': 'HTTP_STREAM', 'message': err_msg, 'req_id': req_id, 'turn': turn_id})}\n\n"
            yield "data: [DONE]\n\n"

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
async def end_session(sid: str):
    s = engine.sessions.get(sid)
    if not s:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    summary = engine.end_session(s)
    return summary


@app.websocket("/ws/session/{sid}")
async def ws_session(ws: WebSocket, sid: str, lang: str = ""):
    await ws.accept()
    s = engine.get_or_create(sid)
    lang = (lang or "").lower()
    if lang in LANGUAGES:
        s.language = lang
    log.info(f"[WS] open sid={s.id}")
    await ws.send_json({"type": "ready", "session_id": s.id, "mode": s.mode,
                        "correction": s.correction, "level": s.level,
                        "response_mode": s.response_mode or settings.default_response_mode})
    # No welcome message: the session starts silent when the user presses
    # start (mic). The user always speaks first.

    async def send(obj: dict):
        try:
            await ws.send_json(obj)
        except Exception:
            pass

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            try:
                if mtype == "user_transcript":
                    t0 = time.time()
                    # cancel any in-flight reply (barge-in safety)
                    engine.cancel(s.id)
                    # t0/turn_id come from the client so "speech_end → first audio" is real
                    turn_id = str(msg.get("turn_id", ""))
                    req_id = str(msg.get("req_id") or turn_id or s.id)
                    task = asyncio.create_task(engine.handle_user_turn(
                        s, msg.get("text", ""), send, t0,
                        t_speech_end_ms=msg.get("t0"), turn_id=turn_id, req_id=req_id,
                        reply_lang=str(msg.get("reply_lang", ""))))
                    engine.tasks[s.id] = task
                    # never await here: the receive loop must keep reading, otherwise a
                    # barge_in (or the next turn) can't cancel this turn mid-reply
                    task.add_done_callback(lambda t: t.cancelled() or
                                           (t.exception() and log.warning(f"[TURN req_id={req_id}] task error: {t.exception()}")))
                elif mtype == "partial":
                    # useful for interruption prediction / latency logging only
                    log.info(f"[STT] partial sid={s.id} {str(msg.get('text',''))[:80]!r}")
                elif mtype == "barge_in":
                    log.info(f"[AUDIO] barge-in sid={s.id} — cancelling TTS+LLM")
                    engine.cancel(s.id)
                    await send({"type": "status", "state": "INTERRUPTED"})
                    await send({"type": "status", "state": "LISTENING"})
                elif mtype == "set_mode":
                    mode = str(msg.get("mode", "free"))
                    if mode in ("free", "practice", "correction", "pronunciation", "roleplay", "vocab", "interview", "challenge"):
                        s.mode = mode
                        if msg.get("scenario"):
                            s.scenario = msg["scenario"]
                        if mode == "challenge":
                            s.challenge = today_challenge()
                        await send({"type": "mode", "mode": s.mode, "scenario": s.scenario})
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
                    if mode in ("groq_fish", "gemini_fish", "gemini_live", "fast", "quality", "live"):
                        s.response_mode = mode
                        await send({"type": "response_mode", "value": mode})
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
                        s.voice_id = vid
                        pname, pgender = persona_for(vid)
                        await send({"type": "voice", "value": vid, "persona": pname})
                    else:
                        s.voice_id = DEFAULT_FISH_VOICE
                        pname, pgender = persona_for(s.voice_id)
                        await send({"type": "voice", "value": s.voice_id, "persona": pname})
                elif mtype == "sync_history":
                    turns = msg.get("turns") or []
                    if isinstance(turns, list) and turns:
                        engine.sync_past_history(s, turns)
                elif mtype == "clear_history":
                    s.history.clear()
                    s.turns.clear()
                    s.past_chat_summary = ""
                elif mtype == "end":
                    summary = engine.end_session(s)
                    await send({"type": "summary", **summary})
                elif mtype == "ping":
                    await send({"type": "pong"})
            except Exception as me:
                log.error(f"[WS] message handling error mtype={mtype}: {me}", exc_info=True)
    except WebSocketDisconnect:
        log.info(f"[WS] close sid={s.id}")
        engine.cancel(s.id)
        # release the Live Bidi socket so dead sessions don't pile up
        # Google-side and starve fresh setups of session slots
        try:
            await engine.close_live(s)
        except Exception:
            pass
