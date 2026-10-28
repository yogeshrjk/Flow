"""Gemini voice clips use the configured Live model/voice, never speculative REST TTS models."""
import asyncio
import base64
import io
import wave
from app.config import settings
from app.providers.tts.base import TTSProvider
from app.providers.llm.gemini_live import GeminiLiveSession


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000, num_channels: int = 1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(num_channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_data)
    return output.getvalue()


class GeminiTTSProvider(TTSProvider):
    name = "gemini-live-tts"

    def __init__(self, api_key=None, voice_name=None):
        self.api_key = api_key if api_key is not None else settings.gemini_api_key
        self.voice_name = voice_name or "Orbit"

    @property
    def available(self):
        return bool(self.api_key)

    async def synthesize(self, text, voice_name=None):
        if not self.api_key:
            return None, "GEMINI_API_KEY is missing"
        session = GeminiLiveSession(api_key=self.api_key, voice_name=voice_name or self.voice_name)
        chunks = []
        async def on_audio(data, mime):
            chunks.append(base64.b64decode(data))
        try:
            async with asyncio.timeout(30):
                await session.connect(system_instruction="Read the user's text aloud exactly as written. Do not add introductions, explanations, or answers. Use your natural voice.")
                await session.send_user_text(text, on_audio=on_audio)
            if not chunks:
                return None, "Gemini Live returned no audio"
            return pcm_to_wav(b"".join(chunks)), None
        except Exception as exc:
            return None, "Gemini Live voice failed (" + type(exc).__name__ + "). Check the server key and provider availability."
        finally:
            try:
                await asyncio.wait_for(session.close(), timeout=3)
            except Exception:
                pass
