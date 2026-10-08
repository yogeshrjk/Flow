"""Gemini Live BidiGenerateContent WebSocket provider.
Connects to Google's official Gemini Live WebSocket API for true native audio streaming.
Model: models/gemini-3.8-live.
"""
import asyncio
import base64
import json
import logging
import ssl
import websockets
from app.config import settings

log = logging.getLogger("gemini_live")

LIVE_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"


class GeminiLiveSession:
    def __init__(self, api_key: str | None = None, voice_name: str = "Puck", model: str = "models/gemini-3.8-live"):
        self.api_key = api_key or settings.gemini_api_key
        self.voice_name = voice_name or "Puck"
        self.model = model if model.startswith("models/") else f"models/{model}"
        self.ws = None
        self.setup_complete = False
        self._lock = asyncio.Lock()

    async def connect(self, system_instruction: str = ""):
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY missing for Gemini Live.")
        url = f"{LIVE_WS_URL}?key={self.api_key}"
        # Pin certifi's CA bundle explicitly (same as httpx does). Rationale:
        # a bare ssl.create_default_context() honors DYLD_LIBRARY_PATH, and a
        # stale libssl picked up that way silently loses the system trust store
        # → CERTIFICATE_VERIFY_FAILED only for this socket, while httpx-based
        # providers keep working. Explicit cafile makes Live immune to that.
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        # bound the handshake: a stalled connect must fail loudly instead of
        # wedging the turn forever (the caller surfaces it, never silent)
        self.ws = await asyncio.wait_for(
            websockets.connect(url, ssl=ssl_ctx, ping_interval=20, ping_timeout=20),
            timeout=15.0,
        )
        setup_payload = {
            "setup": {
                "model": self.model,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": self.voice_name
                            }
                        }
                    }
                }
            }
        }
        if system_instruction:
            setup_payload["setup"]["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }
        await self.ws.send(json.dumps(setup_payload))
        # Wait for setupComplete
        raw_init = await asyncio.wait_for(self.ws.recv(), timeout=10.0)
        init_data = json.loads(raw_init)
        if "setupComplete" in init_data:
            self.setup_complete = True
            log.info(f"[GEMINI LIVE] setupComplete voice={self.voice_name} model={self.model}")
        else:
            log.warning(f"[GEMINI LIVE] unexpected setup response: {raw_init}")

    async def interrupt(self):
        """Send official Gemini Live interrupt payload to stop server-side generation."""
        if self.ws and self.setup_complete:
            try:
                await self.ws.send(json.dumps({
                    "clientContent": {
                        "turns": [],
                        "turnComplete": True
                    }
                }))
            except Exception:
                pass

    async def send_user_text(self, text: str, on_text=None, on_audio=None):
        """Send user text and stream live text/audio chunks from Gemini Live."""
        if not self.ws or not self.setup_complete:
            raise RuntimeError("Gemini Live session not connected.")
        turn = {
            "clientContent": {
                "turns": [
                    {"role": "user", "parts": [{"text": text}]}
                ],
                "turnComplete": True
            }
        }
        await self.ws.send(json.dumps(turn))

        full_text_parts = []
        try:
            while True:
                raw_msg = await self.ws.recv()
                data = json.loads(raw_msg)
                sc = data.get("serverContent", {})

                if sc.get("interrupted"):
                    break

                # 1. Output audio transcription (real-time text tokens)
                if "outputTranscription" in sc:
                    t_token = sc["outputTranscription"].get("text", "")
                    if t_token:
                        full_text_parts.append(t_token)
                        if on_text:
                            res = on_text(t_token)
                            if asyncio.iscoroutine(res):
                                await res

                # 2. Live streaming PCM audio chunk (24000Hz 16-bit PCM)
                model_turn = sc.get("modelTurn", {})
                for p in model_turn.get("parts", []):
                    if "inlineData" in p:
                        pcm_b64 = p["inlineData"].get("data", "")
                        mime = p["inlineData"].get("mimeType", "audio/pcm;rate=24000")
                        if pcm_b64 and on_audio:
                            res = on_audio(pcm_b64, mime)
                            if asyncio.iscoroutine(res):
                                await res

                if sc.get("turnComplete"):
                    break
        except asyncio.CancelledError:
            await self.interrupt()
            raise

        return "".join(full_text_parts)

    async def close(self):
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
            self.setup_complete = False
