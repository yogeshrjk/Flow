"""TTS abstraction. Fish Audio or Gemini Live native audio."""
from abc import ABC, abstractmethod


class TTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def synthesize(self, text: str) -> bytes | None:
        """Return mp3/wav bytes or an explicit error. Never substitute a system voice."""
        raise NotImplementedError
