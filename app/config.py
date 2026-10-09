"""Central config. Everything free-first. No secrets in code."""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _default_data_dir() -> str:
    if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "/tmp/data"
    env_dir = os.environ.get("DATA_DIR", "./data").strip()
    return env_dir or "./data"


@dataclass
class Settings:
    # --- Fast tier: Groq (OpenAI-compatible chat completions, very low TTFT) ---
    # Never surfaced in the UI: users see "Fast Response" / "Overall Good" only.
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    groq_base_url: str = field(default_factory=lambda: _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))
    # llama-3.1-8b-instant = lowest TTFT conversational model; comma list = mini chain
    groq_model: str = field(default_factory=lambda: _env("GROQ_MODEL", "llama-3.1-8b-instant"))
    # only sent to reasoning-capable models (gpt-oss/qwen) — instant models 400 on it
    groq_reasoning_effort: str = field(default_factory=lambda: _env("GROQ_REASONING_EFFORT", "low"))

    # --- Quality tier: Gemini chain ---
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    # GEMINI_MODEL = optional single-model override for the chain head
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"))
    llm_primary: str = field(default_factory=lambda: _env("LLM_PRIMARY", "gemini-3.5-flash-lite"))
    llm_secondary: str = field(default_factory=lambda: _env("LLM_SECONDARY", "gemini-3.1-flash-lite"))
    llm_fallback: str = field(default_factory=lambda: _env("LLM_FALLBACK", "gemma-4-31b-it"))
    llm_thinking_level: str = field(default_factory=lambda: _env("LLM_THINKING_LEVEL", "minimal"))
    ollama_base_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434/v1"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "llama3.1:8b"))
    allow_ollama: bool = field(default_factory=lambda: _env("LLM_ALLOW_OLLAMA", "false").lower() == "true")

    fish_api_key: str = field(default_factory=lambda: _env("FISH_API_KEY"))
    # FISH_REFERENCE_ID is accepted as an alias (spec-style naming)
    fish_voice_id: str = field(default_factory=lambda: _env("FISH_VOICE_ID") or _env("FISH_REFERENCE_ID"))
    fish_model: str = field(default_factory=lambda: _env("FISH_MODEL", "s2.1-pro-free"))
    # voice latency knobs (docs.fish.audio): balanced = lowest time-to-first-audio,
    # smaller chunk_length starts audio sooner, 64kbps mp3 = fewer bytes first
    fish_latency: str = field(default_factory=lambda: _env("FISH_LATENCY", "balanced"))
    fish_chunk_length: int = field(default_factory=lambda: int(_env("FISH_CHUNK_LENGTH", "100") or "100"))
    fish_mp3_bitrate: int = field(default_factory=lambda: int(_env("FISH_MP3_BITRATE", "64") or "64"))

    # near-zero pause before answering (it was 350ms of pure added latency)
    reply_pause_ms: int = field(default_factory=lambda: int(_env("REPLY_PAUSE_MS", "80") or "80"))

    stt_provider: str = field(default_factory=lambda: _env("STT_PROVIDER", "browser"))
    deepgram_key: str = field(default_factory=lambda: _env("DEEPGRAM_API_KEY"))

    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8000") or "8000"))
    data_dir: str = field(default_factory=_default_data_dir)
    default_mode: str = field(default_factory=lambda: _env("DEFAULT_MODE", "free"))
    default_correction: str = field(default_factory=lambda: _env("DEFAULT_CORRECTION", "balanced"))
    default_level: str = field(default_factory=lambda: _env("DEFAULT_LEVEL", "auto"))

    llm_default_response_mode: str = field(default_factory=lambda: _env("LLM_DEFAULT_RESPONSE_MODE", "fast"))

    @property
    def groq_models(self) -> list[str]:
        out = [m.strip() for m in (self.groq_model or "").split(",") if m.strip()]
        return out or ["llama-3.1-8b-instant"]

    @property
    def llm_chain(self) -> list[str]:
        # Never include Gemini 2.x (retired). Enforce.
        chain = [self.gemini_model, self.llm_primary, self.llm_secondary, self.llm_fallback]
        out = [m for m in chain if m and "2.0" not in m and "2.5" not in m and m.startswith(("gemini-", "gemma-"))]
        # de-dup preserve order
        seen = set()
        dedup = []
        for m in out:
            if m not in seen:
                seen.add(m)
                dedup.append(m)
        return dedup or ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemma-4-31b-it"]

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def default_response_mode(self) -> str:
        """Default is Groq+Fish; fall back to Gemini tiers when Groq has no key."""
        want = (self.llm_default_response_mode or "groq_fish").lower()
        if want in ("fast", "speed", "groq", "groq_fish"):
            want = "groq_fish"
        elif want in ("quality", "gemini", "gemini_fish"):
            want = "gemini_fish"
        elif want in ("live", "gemini_live", "gemini-3.8-live"):
            want = "gemini_live"
        else:
            want = "groq_fish"

        if want == "groq_fish" and not self.has_groq:
            return "gemini_fish" if self.has_gemini else "gemini_live"
        if want in ("gemini_fish", "gemini_live") and not self.has_gemini:
            return "groq_fish" if self.has_groq else want
        return want

    @property
    def has_fish(self) -> bool:
        return bool(self.fish_api_key)


settings = Settings()
