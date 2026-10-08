"""Gemini native TTS provider — used for Gemini Live voice output."""
import base64
import io
import logging
import wave
import httpx
from app.config import settings
from app.providers.tts.base import TTSProvider

log = logging.getLogger("tts")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=8.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16, keepalive_expiry=60.0),
        )
    return _client


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000, num_channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


class GeminiTTSProvider(TTSProvider):
    name = "gemini-live-tts"
    models = [
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-lite-tts",
        "gemini-2.5-flash-preview-tts",
        "gemini-3.1-flash-tts-preview",
    ]

    def __init__(self, api_key: str | None = None, voice_name: str | None = None):
        self.api_key = api_key if api_key is not None else settings.gemini_api_key
        self.voice_name = voice_name or "Puck"

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def synthesize(self, text: str, voice_name: str | None = None) -> tuple[bytes | None, str | None]:
        if not self.api_key:
            return None, "Gemini API key missing — set GEMINI_API_KEY"
        if not text.strip():
            return None, "empty text"
        vname = voice_name or self.voice_name or "Puck"
        payload = {
            "contents": [{"parts": [{"text": text[:800]}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": vname
                        }
                    }
                },
            },
        }
        client = _get_client()
        last_err = None
        for model in self.models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
            try:
                r = await client.post(url, json=payload)
                if r.status_code == 200:
                    data = r.json()
                    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    for p in parts:
                        if "inlineData" in p:
                            mime = p["inlineData"].get("mimeType", "")
                            raw = base64.b64decode(p["inlineData"].get("data", ""))
                            if "pcm" in mime.lower() or "l16" in mime.lower():
                                wav = pcm_to_wav(raw, 24000, 1)
                                return wav, None
                            return raw, None
                elif r.status_code in (404, 429, 500, 503):
                    last_err = f"Gemini TTS {model} returned {r.status_code}"
                    continue
                else:
                    return None, f"Gemini TTS error: {r.text[:120]}"
            except Exception as e:
                last_err = str(e)
                continue
        # Seamless fallback to Fish Audio if Google rate-limits preview TTS (429)
        if settings.has_fish:
            log.warning(f"[TTS] Gemini TTS unavailable ({last_err}), falling back to Fish Audio")
            from app.providers.tts.fish import FishProvider
            return await FishProvider().synthesize(text)
        return None, last_err or "Gemini TTS synthesis failed"
