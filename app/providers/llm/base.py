"""LLM provider abstraction. Free-first: Gemini chain. No Gemini 2.x."""
from abc import ABC, abstractmethod
from typing import AsyncIterator, AsyncGenerator


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def stream(self, messages: list[dict], max_tokens: int = 250) -> AsyncGenerator[str, None]:
        raise NotImplementedError
        yield ""  # make it async generator

    async def complete(self, messages: list[dict], max_tokens: int = 250) -> str:
        parts = []
        async for tok in self.stream(messages, max_tokens=max_tokens):
            parts.append(tok)
        return "".join(parts)


class UnavailableProvider(LLMProvider):
    """Missing configuration is an error, never a scripted AI response."""
    name = "unconfigured"

    async def stream(self, messages, max_tokens=250):
        raise RuntimeError("The selected AI provider is not configured. Add its server API key.")
        yield ""
