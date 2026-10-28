"""STT / VAD abstractions. Browser recognition availability and network use depend on the browser.
Server keeps passthrough + hooks for Deepgram/local later."""
from abc import ABC, abstractmethod


class STTProvider(ABC):
    name = "base"


class BrowserPassthroughSTT(STTProvider):
    name = "browser"


class VADProvider(ABC):
    name = "base"


class ClientVAD(VADProvider):
    name = "client-energy"
