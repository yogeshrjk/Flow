"""TTS abstraction. Primary Fish S2.1 Pro Free; fallback browser (free unlimited)."""
from abc import ABC, abstractmethod


class TTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def synthesize(self, text: str) -> bytes | None:
        """Return mp3/wav bytes, or None if caller should use browser TTS."""
        raise NotImplementedError
