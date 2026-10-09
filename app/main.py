"""FastAPI entry: serves UI + WS realtime + REST. Free-first, zero required keys."""
import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import settings
from app.conversation.engine import engine
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
            {"id": "english", "label": "English", "stt": "en-IN"},
            {"id": "hindi", "label": "Hindi", "stt": "hi-IN"},
            {"id": "hinglish", "label": "Hinglish", "stt": "en-IN"},
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
    if lang in ("english", "hindi", "hinglish"):
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
            if mtype == "user_transcript":
                t0 = time.time()
                # cancel any in-flight reply (barge-in safety)
                engine.cancel(s.id)
                # t0/turn_id come from the client so "speech_end → first audio" is real
                turn_id = str(msg.get("turn_id", ""))
                req_id = str(msg.get("req_id") or turn_id or s.id)
                task = asyncio.create_task(engine.handle_user_turn(
                    s, msg.get("text", ""), send, t0,
                    t_speech_end_ms=msg.get("t0"), turn_id=turn_id, req_id=req_id))
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
                if lang in ("english", "hindi", "hinglish"):
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
                try:
                    valid = vid in valid_ids or await fish_voice_library.is_public_model(vid)
                except FishLibraryError as exc:
                    await send({"type": "voice_error", "error": exc.message, "value": vid})
                    continue
                if valid:
                    s.voice_id = vid
                    pname, pgender = persona_for(vid)
                    await send({"type": "voice", "value": vid, "persona": pname})
                else:
                    await send({"type": "voice_error", "error": "Voice is not public or is unavailable.", "value": vid})
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
    except WebSocketDisconnect:
        log.info(f"[WS] close sid={s.id}")
        engine.cancel(s.id)
        # release the Live Bidi socket so dead sessions don't pile up
        # Google-side and starve fresh setups of session slots
        try:
            await engine.close_live(s)
        except Exception:
            pass
