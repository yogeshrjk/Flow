"""Gemini free-tier chain via OpenAI-compatible endpoint + httpx SSE.
Chain: gemini-3.5-flash-lite -> gemini-3.1-flash-lite -> gemma-4-31b-it
thinking_level minimal/low for realtime latency. No Gemini 2.x ever."""
import asyncio
import json
import logging
import time
from typing import AsyncGenerator

import httpx

from app.config import settings
from app.providers.llm.base import LLMProvider

log = logging.getLogger("llm")

# keep-alive client: TLS/HTTP2 handshake per turn was pure added latency
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


class GeminiChainProvider(LLMProvider):
    name = "gemini-chain"

    def __init__(self, models: list[str] | None = None, base_url: str | None = None, api_key: str | None = None):
        self.models = [m for m in (models or settings.llm_chain) if "2.0" not in m and "2.5" not in m]
        self.base_url = (base_url or settings.llm_base_url).rstrip("/") + "/"
        self.api_key = api_key if api_key is not None else settings.gemini_api_key
        self.active_model = self.models[0] if self.models else "gemini-3.5-flash-lite"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def _stream_one(self, model: str, messages: list[dict], max_tokens: int) -> AsyncGenerator[str, None]:
        url = self.base_url + "chat/completions"
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
        }
        # thinking level for Gemini 3.x latency control (best-effort; ignored if unsupported)
        try:
            lvl = (settings.llm_thinking_level or "minimal").lower()
            if lvl not in ("minimal", "low", "medium", "high"):
                lvl = "minimal"
            if model.startswith("gemini-3"):
                body["extra_body"] = {"google": {"thinking_config": {"thinking_level": lvl}}}
        except Exception:
            pass
        t0 = time.time()
        first = False
        client = _get_client()
        async with client.stream("POST", url, headers=self._headers(), json=body) as resp:
            if resp.status_code in (429, 500, 502, 503, 529):
                raise RuntimeError(f"retryable:{resp.status_code}")
            if resp.status_code != 200:
                txt = (await resp.aread()).decode("utf-8", "ignore")[:500]
                raise RuntimeError(f"gemini {resp.status_code}: {txt}")
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    delta = obj["choices"][0].get("delta", {}).get("content", "")
                    if delta:
                        if not first:
                            log.info(f"[LLM] first-token model={model} t={time.time()-t0:.2f}s")
                            first = True
                        yield delta
                except Exception:
                    continue

    async def stream(self, messages: list[dict], max_tokens: int = 250) -> AsyncGenerator[str, None]:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is missing")
        last_err: Exception | None = None
        for i, model in enumerate(self.models):
            t_attempt = time.perf_counter()
            got_any = False
            try:
                self.active_model = model
                async for tok in self._stream_one(model, messages, max_tokens):
                    got_any = True
                    yield tok
                if got_any:
                    return
                raise RuntimeError("Provider returned no tokens")
            except Exception as e:
                if got_any:
                    raise  # Preserve partial output; no second answer may be appended.
                last_err = e
                msg = str(e)
                retryable = msg.startswith("retryable") or "429" in msg or "5" in msg[:12]
                log.warning(f"[LLM] model={model} failed ({msg[:200]}) "
                            f"after={(time.perf_counter() - t_attempt) * 1000:.0f}ms, "
                            f"fallback={i+1 < len(self.models)}")
                if i + 1 < len(self.models):
                    # Different model = different request; waiting bought nothing
                    # and cost 400ms of first-token latency per rate-limited hop.
                    continue
                raise last_err if last_err else RuntimeError("llm failed")
