"""Pre-output failover with a cooldown. Never joins answers from two models."""
import time
from app.providers.llm.base import LLMProvider


class ResilientProvider(LLMProvider):
    def __init__(self, providers):
        self.providers = providers
        self.unavailable_until = {}
        self.name = "free-fallback-chain"

    async def stream(self, messages, max_tokens=250):
        error = None
        for provider in self.providers:
            if time.monotonic() < self.unavailable_until.get(id(provider), 0):
                continue
            emitted = False
            try:
                async for token in provider.stream(messages, max_tokens=max_tokens):
                    emitted = True
                    yield token
                if not emitted:
                    raise RuntimeError("Provider returned no text")
                return
            except Exception as exc:
                if emitted:
                    raise
                error = exc
                self.unavailable_until[id(provider)] = time.monotonic() + 30
        raise RuntimeError("AI services unavailable; try again shortly or choose another configured response mode") from error
