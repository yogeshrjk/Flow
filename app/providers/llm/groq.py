"""Groq streaming provider — the "Fast Response" tier.

OpenAI-compatible chat completions (SSE), same wire shape as the Gemini
OpenAI-compat endpoint, so this mirrors `GeminiChainProvider`:

    POST {base}/chat/completions   Authorization: Bearer <key>
    {"model": ..., "messages": [...], "stream": true, "max_tokens": N}

Purpose: lowest possible time-to-first-token for a realtime voice coach.
Default model is `llama-3.1-8b-instant` (no reasoning overhead). A comma
separated GROQ_MODEL becomes a tiny failover chain.

The provider name is intentionally never sent to the browser UI.
"""
import asyncio
import json
import logging
import time
from typing import AsyncGenerator

import httpx

from app.config import settings
from app.providers.llm.base import LLMProvider

log = logging.getLogger("llm")

# module-level keep-alive client: a fresh TLS handshake per turn costs ~0.2-0.35s
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


def _wants_reasoning_effort(model: str) -> bool:
    """Only reasoning-capable families accept reasoning_effort; llama/qwen 4xx or waste tokens on it."""
    m = (model or "").lower()
    return ("gpt-oss" in m) or ("reasoning" in m)


class GroqProvider(LLMProvider):
    name = "groq"  # internal only — never rendered in the UI

    def __init__(self, models: list[str] | None = None, base_url: str | None = None, api_key: str | None = None):
        self.models = [m for m in (models or settings.groq_models) if m]
        self.base_url = (base_url or settings.groq_base_url).rstrip("/") + "/"
        self.api_key = api_key if api_key is not None else settings.groq_api_key
        self.active_model = self.models[0] if self.models else "llama-3.1-8b-instant"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _body(self, model: str, messages: list[dict], max_tokens: int) -> dict:
        body: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": 0.7,
            "top_p": 0.9,
        }
        if _wants_reasoning_effort(model):
            lvl = (settings.groq_reasoning_effort or "low").lower()
            body["reasoning_effort"] = lvl if lvl in ("low", "medium", "high") else "low"
        return body

    async def _stream_one(self, model: str, messages: list[dict], max_tokens: int) -> AsyncGenerator[str, None]:
        url = self.base_url + "chat/completions"
        t0 = time.time()
        first = False
        client = _get_client()
        async with client.stream("POST", url, headers=self._headers(), json=self._body(model, messages, max_tokens)) as resp:
            if resp.status_code in (429, 500, 502, 503, 529):
                raise RuntimeError(f"retryable:{resp.status_code}")
            if resp.status_code != 200:
                txt = (await resp.aread()).decode("utf-8", "ignore")[:400]
                raise RuntimeError(f"groq {resp.status_code}: {txt}")
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    choice = (obj.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    # reasoning models may emit `reasoning`; never speak that
                    text = delta.get("content", "") or ""
                    if text:
                        if not first:
                            log.info(f"[LLM] first-token model={model} t={time.time()-t0:.2f}s (fast tier)")
                            first = True
                        yield text
                except Exception:
                    continue

    async def stream(self, messages: list[dict], max_tokens: int = 250) -> AsyncGenerator[str, None]:
        if not self.api_key:
            raise RuntimeError("no GROQ_API_KEY — fast tier unavailable")
        last_err: Exception | None = None
        for i, model in enumerate(self.models):
            try:
                self.active_model = model
                got_any = False
                async for tok in self._stream_one(model, messages, max_tokens):
                    got_any = True
                    yield tok
                if not got_any:
                    raise RuntimeError(f"groq model={model} yielded 0 tokens")
                return
            except Exception as e:
                last_err = e
                msg = str(e)
                log.warning(f"[LLM] groq model={model} failed ({msg[:160]}), fallback={i + 1 < len(self.models)}")
                if i + 1 < len(self.models):
                    await asyncio.sleep(0.25)
                    continue
                raise last_err

    async def complete(self, messages: list[dict], max_tokens: int = 250) -> str:
        parts: list[str] = []
        async for tok in self.stream(messages, max_tokens=max_tokens):
            parts.append(tok)
        return "".join(parts)
