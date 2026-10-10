"""Server-side speech-to-text over the Groq transcription endpoint.

Lets mobile browsers hold ONE microphone stream open for the whole session:
the client records utterances with MediaRecorder on its held getUserMedia
stream and POSTs each utterance here. Web Speech's platform-owned stream
(auto-ends on silence/utterance on mobile) is then out of the loop.

Wire shape is OpenAI-compatible multipart:

    POST {base}/audio/transcriptions   Authorization: Bearer <key>
    file=<audio> model=whisper-large-v3-turbo [language=xx] response_format=json

The provider name is intentionally never sent to the browser UI.
"""
import logging

import httpx

from app.config import settings
from app.providers.stt.base import STTProvider

log = logging.getLogger("stt")

MODEL = "whisper-large-v3-turbo"
MAX_AUDIO_BYTES = 8 * 1024 * 1024

# Whisper names a detected language in plain English ("spanish", "hindi",
# "chinese", ...). Our language ids are the same lowercase names, so a
# generic display-name lookup maps detection to reply language for EVERY
# language — no per-language special cases anywhere in this path.
_EXTRA_WHISPER_NAMES = {"mandarin": "chinese"}

# module-level keep-alive client, same pattern as the LLM/TTS providers
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16, keepalive_expiry=120.0),
        )
    return _client


async def _close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


class GroqWhisperSTT(STTProvider):
    name = "groq-whisper"  # internal only — never rendered in the UI

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or settings.groq_base_url).rstrip("/") + "/"
        self.api_key = api_key if api_key is not None else settings.groq_api_key

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def transcribe(
        self, audio: bytes, filename: str = "utterance.webm", language: str | None = None,
        content_type: str = "audio/webm", prompt: str | None = None,
    ) -> tuple[str, str | None, str | None]:
        """Return (text, error, detected_language_id). `detected` is our
        language id ("hindi", "spanish", ...) or None when unknown, so the
        reply can follow whatever language the user just spoke."""
        if not self.api_key:
            return "", "server transcription unavailable", None
        if not audio or len(audio) < 1024:
            return "", "empty audio", None
        if len(audio) > MAX_AUDIO_BYTES:
            return "", "audio too large", None
        url = self.base_url + "audio/transcriptions"
        # verbose_json carries per-segment no_speech_prob: Whisper's own
        # confidence that a segment holds no speech. High values on every
        # segment mean fluent-sounding output generated from noise — the
        # hallucination class word-rules cannot see.
        data = {"model": MODEL, "response_format": "verbose_json", "temperature": "0"}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt[:300]
        try:
            resp = await _get_client().post(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=data,
                files={"file": (filename, audio, content_type or "audio/webm")},
            )
        except Exception as e:
            log.warning(f"[STT] transcription request failed: {type(e).__name__}")
            return "", "could not reach transcription service", None
        if resp.status_code != 200:
            body = (resp.text or "")[:400]
            log.warning(f"[STT] transcription error {resp.status_code}: {body[:200]}")
            if resp.status_code in (401, 403):
                return "", "transcription key rejected", None
            if resp.status_code == 429:
                return "", "transcription rate-limited — try again", None
            return "", "transcription rejected: " + _short_upstream_error(body), None
        try:
            obj = resp.json()
        except Exception:
            return "", "transcription returned an unreadable reply", None
        segs = obj.get("segments") or []
        if segs:
            text = " ".join((s.get("text") or "").strip() for s in segs).strip()
            try:
                probs = [float(s.get("no_speech_prob", 0.0)) for s in segs]
                avg_nsp = sum(probs) / len(probs)
            except (ValueError, TypeError):
                avg_nsp = 0.0
            if avg_nsp > 0.6:
                log.warning(f"[STT] no-speech segments (prob={avg_nsp:.2f}): {text[:80]}")
                return "", "I didn't catch that — say it once more?", None
        else:
            text = (obj.get("text") or "").strip()
        if not text:
            return "", "heard nothing intelligible", None
        return text, None, _detect_id(obj.get("language"))


def _detect_id(name: str | None) -> str | None:
    """Map Whisper's detected language name to our language id."""
    if not name:
        return None
    key = str(name).strip().lower()
    if key in _EXTRA_WHISPER_NAMES:
        return _EXTRA_WHISPER_NAMES[key]
    try:
        from app.conversation.prompts import LANGUAGES

        for lang_id, display in LANGUAGES.items():
            if display.lower() == key or lang_id == key:
                return lang_id
    except Exception:
        pass
    return None


def _short_upstream_error(body: str) -> str:
    """Distill Groq's {"error": {"message": ...}} into one short client-safe line."""
    try:
        import json as _json

        obj = _json.loads(body)
        msg = ((obj.get("error") or {}).get("message") if isinstance(obj.get("error"), dict) else obj.get("error")) or ""
        msg = " ".join(str(msg).split())
        if "must be one of the following types" in msg:
            return "audio format not accepted — try again"
        if msg:
            return msg[:140]
    except Exception:
        pass
    return "bad request"
