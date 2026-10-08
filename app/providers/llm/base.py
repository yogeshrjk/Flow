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
