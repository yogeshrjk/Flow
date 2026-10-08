"""Fish Audio S2.1 Pro — the only TTS engine. No fallbacks.
If Fish errors (e.g. 402 no API credit), the error is surfaced, not hidden."""
import logging
import time
import httpx
from app.config import settings
from app.providers.tts.base import TTSProvider

log = logging.getLogger("tts")
FISH_URL = "https://api.fish.audio/v1/tts"

# One keep-alive client for the whole process: a fresh TLS handshake per
# sentence cost ~0.35s of first-audio latency (measured).
_client: httpx.AsyncClient | None = None
# separate client for progressive streaming: Fish can pause between chunks on
# long text, so the read timeout is generous (the POST path stays fail-fast)
_stream_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=8.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16, keepalive_expiry=60.0),
        )
    return _client


def _get_stream_client() -> httpx.AsyncClient:
    global _stream_client
    if _stream_client is None or _stream_client.is_closed:
        _stream_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=8.0, read=45.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16, keepalive_expiry=60.0),
        )
    return _stream_client


async def _close_client() -> None:
    global _client, _stream_client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
    if _stream_client is not None and not _stream_client.is_closed:
        await _stream_client.aclose()
    _stream_client = None


def _looks_like_mp3(data: bytes) -> bool:
    if not data or len(data) < 4:
        return False
    if data[:3] == b"ID3":
        return True
    # MPEG frame sync: 0xFF + top 3 bits set
    return data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


class FishProvider(TTSProvider):
    name = "fish-s2.1-pro"

    def __init__(self, api_key: str | None = None, voice_id: str | None = None):
        self.api_key = api_key if api_key is not None else settings.fish_api_key
        self.voice_id = voice_id if voice_id is not None else settings.fish_voice_id

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def short_error(self, status: int, body: str) -> str:
        if status == 402:
            return "402: Fish API credit is empty — top up at fish.audio/app/developers"
        if status == 401:
            return "401: Fish API key rejected — check FISH_API_KEY"
        return f"{status}: {body[:120]}"

    def _payload(self, text: str, vid: str | None) -> dict:
        return {
            "text": text[:800],
            "reference_id": vid,
            "format": "mp3",
            # voice latency knobs (docs.fish.audio): balanced = lowest
            # time-to-first-audio; small chunks start generating sooner;
            # 64kbps mp3 halves the bytes the browser has to receive first.
            "latency": settings.fish_latency,
            "chunk_length": settings.fish_chunk_length,
            "mp3_bitrate": settings.fish_mp3_bitrate,
        }

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "model": settings.fish_model or "s2.1-pro-free",
        }

    async def open_stream(self, text: str, voice_id: str | None = None):
        """Progressive synthesis: returns (byte_iterator, None) or (None, error).

        Fish answers `Transfer-Encoding: chunked` and emits mp3 frames while it
        synthesises (measured: first byte 0.70s vs 2.85s for the whole body), so
        a media element pointed at a proxy of this can start speaking long before
        the sentence is finished. The first chunk is peeked here so a JSON error
        disguised as 200 still surfaces as a clean error before any bytes ship.
        """
        vid = voice_id or self.voice_id
        if not self.api_key:
            return None, "Fish API key missing — set FISH_API_KEY"
        if not text.strip():
            return None, "empty text"
        t0 = time.time()
        try:
            client = _get_stream_client()
            req = client.build_request("POST", FISH_URL, headers=self._headers(), json=self._payload(text, vid))
            resp = await client.send(req, stream=True)
        except Exception as e:
            log.warning(f"[TTS] fish stream failed {e}")
            return None, str(e)[:150]
        if resp.status_code != 200:
            body = (await resp.aread()).decode("utf-8", "ignore")
            await resp.aclose()
            err = self.short_error(resp.status_code, body)
            log.warning(f"[TTS] fish stream failed: {err}")
            return None, err
        # chunk_size=None → yield each decoded chunk the moment it arrives.
        # Anything bigger BUFFERS: at 64kbps (8KB/s) a 16KB read means waiting
        # ~2s of audio before the browser sees a single frame (measured bug).
        aiter = resp.aiter_bytes(None)
        head = b""
        try:
            while len(head) < 4:  # need just one mp3 frame header for the sniff
                head += await aiter.__anext__()
        except StopAsyncIteration:
            pass
        except Exception as e:
            await resp.aclose()
            return None, str(e)[:150]
        if not head:
            await resp.aclose()
            return None, "Fish returned no audio"
        if not _looks_like_mp3(head):
            await resp.aclose()
            return None, f"Fish returned non-audio data: {head[:120]!r}"
        log.info(f"[TTS] fish stream open chars={len(text)} ttfb={time.time()-t0:.2f}s bytes={len(head)}")

        async def gen():
            try:
                yield head
                async for chunk in aiter:
                    yield chunk
            finally:
                # client disconnect (barge-in) lands here → upstream cancelled
                try:
                    await resp.aclose()
                except Exception:
                    pass

        return gen(), None

    async def synthesize(self, text: str, voice_id: str | None = None) -> tuple[bytes | None, str | None]:
        """Returns (audio, error). Mirrors the verified working request:
        model goes in the `model:` header (s2.1-pro-free = free tier)."""
        vid = voice_id or self.voice_id
        if not self.api_key:
            return None, "Fish API key missing — set FISH_API_KEY"
        if not text.strip():
            return None, "empty text"
        t0 = time.time()
        payload = self._payload(text, vid)
        headers = self._headers()
        try:
            # fail fast: a hung request must never stall the voice queue
            client = _get_client()
            r = await client.post(FISH_URL, headers=headers, json=payload)
            if r.status_code != 200:
                err = self.short_error(r.status_code, r.text or "")
                log.warning(f"[TTS] fish failed: {err}")
                return None, err
            body = r.content or b""
            # Fish sometimes answers 200 with a JSON error — never serve that as audio.
            if not _looks_like_mp3(body):
                err = f"Fish returned non-audio data: {body[:120]!r}"
                log.warning(f"[TTS] {err}")
                return None, err
            log.info(f"[TTS] fish ok chars={len(text)} t={time.time()-t0:.2f}s bytes={len(body)}")
            return body, None
        except Exception as e:
            log.warning(f"[TTS] fish failed {e}")
            return None, str(e)[:150]
