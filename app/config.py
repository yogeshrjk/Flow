"""Central config. Everything free-first. No secrets in code."""
import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _default_data_dir() -> str:
    if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "/tmp/data"
    env_dir = os.environ.get("DATA_DIR", "./data").strip()
    return env_dir or "./data"


RESPONSE_MODES = ("gemini_live", "groq_fish", "gemini_fish")


def normalize_response_mode(mode: str) -> str:
    aliases = {"fast": "groq_fish", "speed": "groq_fish", "groq": "groq_fish",
               "quality": "gemini_fish", "gemini": "gemini_fish", "live": "gemini_live"}
    value = (mode or "gemini_live").lower()
    value = aliases.get(value, value)
    return value if value in RESPONSE_MODES else "gemini_live"


@dataclass
class Settings:
    # --- Fast tier: Groq (OpenAI-compatible chat completions, very low TTFT) ---
    # Never surfaced in the UI: users see "Fast Response" / "Overall Good" only.
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    groq_base_url: str = field(default_factory=lambda: _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))
    # Comma-separated free-tier model chain; model eligibility can change.
    groq_model: str = field(default_factory=lambda: _env("GROQ_MODEL", "openai/gpt-oss-20b,openai/gpt-oss-120b"))
    # only sent to reasoning-capable models (gpt-oss/qwen) — instant models 400 on it
    groq_reasoning_effort: str = field(default_factory=lambda: _env("GROQ_REASONING_EFFORT", "low"))

    # --- Quality tier: Gemini chain ---
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    # GEMINI_MODEL = optional single-model override for the chain head
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL"))
    gemini_live_model: str = field(default_factory=lambda: _env("GEMINI_LIVE_MODEL", "gemini-3.8-live"))
    gemini_transcribe_live_model: str = field(default_factory=lambda: _env("GEMINI_TRANSCRIBE_LIVE_MODEL", "gemini-3.5-transcribe-live"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"))
    llm_primary: str = field(default_factory=lambda: _env("LLM_PRIMARY", "gemini-3.5-flash-lite"))
    llm_secondary: str = field(default_factory=lambda: _env("LLM_SECONDARY", "gemini-3.1-flash-lite"))
    llm_fallback: str = field(default_factory=lambda: _env("LLM_FALLBACK", "gemma-4-31b-it"))
    llm_thinking_level: str = field(default_factory=lambda: _env("LLM_THINKING_LEVEL", "minimal"))

    fish_api_key: str = field(default_factory=lambda: _env("FISH_API_KEY"))
    # FISH_REFERENCE_ID is accepted as an alias (spec-style naming)
    fish_voice_id: str = field(default_factory=lambda: _env("FISH_VOICE_ID") or _env("FISH_REFERENCE_ID"))
    fish_model: str = field(default_factory=lambda: _env("FISH_MODEL", "s2.1-pro-free"))
    # voice latency knobs (docs.fish.audio): balanced = lowest time-to-first-audio,
    # smaller chunk_length starts audio sooner, 64kbps mp3 = fewer bytes first
    fish_latency: str = field(default_factory=lambda: _env("FISH_LATENCY", "balanced"))
    fish_chunk_length: int = field(default_factory=lambda: int(_env("FISH_CHUNK_LENGTH", "100") or "100"))
    fish_mp3_bitrate: int = field(default_factory=lambda: int(_env("FISH_MP3_BITRATE", "128") or "128"))

    # zero artificial delay before answering for lowest latency and natural conversation flow
    reply_pause_ms: int = field(default_factory=lambda: int(_env("REPLY_PAUSE_MS", "0") or "0"))

    # Fixed routing: legacy TTS_PROVIDER=browser is intentionally ignored.
    tts_provider: str = "fish"
    turn_timeout_s: float = field(default_factory=lambda: float(_env("TURN_TIMEOUT_S", "45")))
    allowed_origins: str = field(default_factory=lambda: _env("ALLOWED_ORIGINS"))
    max_sessions: int = field(default_factory=lambda: int(_env("MAX_SESSIONS", "200")))
    session_ttl_s: int = field(default_factory=lambda: int(_env("SESSION_TTL_S", "3600")))

    stt_provider: str = field(default_factory=lambda: _env("STT_PROVIDER", "browser"))
    deepgram_key: str = field(default_factory=lambda: _env("DEEPGRAM_API_KEY"))

    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8000") or "8000"))
    data_dir: str = field(default_factory=_default_data_dir)
    default_mode: str = field(default_factory=lambda: _env("DEFAULT_MODE", "free"))
    default_correction: str = field(default_factory=lambda: _env("DEFAULT_CORRECTION", "balanced"))
    default_level: str = field(default_factory=lambda: _env("DEFAULT_LEVEL", "auto"))

    llm_default_response_mode: str = field(default_factory=lambda: _env("LLM_DEFAULT_RESPONSE_MODE", "gemini_live"))

    @property
    def groq_models(self) -> list[str]:
        out = [m.strip() for m in (self.groq_model or "").split(",") if m.strip()]
        return out or ["openai/gpt-oss-20b"]

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
        """Live is the default even before credentials are configured."""
        return normalize_response_mode(self.llm_default_response_mode)

    @property
    def has_fish(self) -> bool:
        return bool(self.fish_api_key)


settings = Settings()
