"""Voice latency instrumentation — one place that formats the report block.

The number that matters is TOTAL: the moment the user stops speaking → the
moment they actually HEAR the AI. Every stage is measured, never guessed, so
the real bottleneck is visible instead of assumed (it was never Fish alone).

Server side knows: STT+network, LLM TTFT, time to first speakable phrase.
Client side adds: TTS fetch/synth, playback buffer, TOTAL.
"""
from __future__ import annotations


def _row(label: str, ms: float | int | None) -> str:
    val = "  n/a" if ms is None else f"{int(round(ms))}ms"
    return f"{label:<20}{val:>8}"


def format_block(
    stt_ms: float | None = None,
    llm_ttft_ms: float | None = None,
    chunk_ms: float | None = None,
    tts_ms: float | None = None,
    buffer_ms: float | None = None,
    total_ms: float | None = None,
    mode: str = "",
) -> str:
    head = "[VOICE LATENCY]" + (f" {mode}" if mode else "")
    rows = [
        _row("STT + network:", stt_ms),
        _row("LLM TTFT:", llm_ttft_ms),
        _row("First phrase:", chunk_ms),
        _row("TTS first audio:", tts_ms),
        _row("Playback buffer:", buffer_ms),
        "-" * 28,
        _row("TOTAL", total_ms) + "  (speech end → first AI audio)",
    ]
    return "\n".join([head, *rows])


def block_data(**kw) -> dict:
    """Same numbers as a dict — attached to the WS payload for the client block."""
    out: dict = {}
    for k in ("stt_ms", "llm_ttft_ms", "chunk_ms", "tts_ms", "buffer_ms", "total_ms"):
        v = kw.get(k)
        if v is not None:
            out[k] = int(round(v))
    return out
