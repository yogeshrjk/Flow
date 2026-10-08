# English Speaking Partner — Realtime AI Spoken-English Coach

Voice-first, **free-tier-first** realtime conversation partner. Default mode: **Free Conversation + Passive Coaching** — natural talk first, corrections later, never breaking flow.

## 100% free to run

| Piece | Default (free, unlimited-ish) | Optional paid/free-key upgrade |
|---|---|---|
| AI Response | Two tiers behind friendly UI labels: **Fast Response** (`GROQ_API_KEY`, Groq streaming, lowest time-to-first-token) and **Overall Good** (`GEMINI_API_KEY`, free Gemini chain `gemini-3.5-flash-lite → gemini-3.1-flash-lite → gemma-4-31b-it`, never Gemini 2.x). No keys at all → `mock-free` demo | `LLM_ALLOW_OLLAMA=true` + `ollama serve` for 100% local |
| STT | Browser Web Speech API (free, no key) | `STT_PROVIDER=deepgram` + key |
| TTS | Fish Audio S2.1 Pro **Free** (`model: s2.1-pro-free` header, single voice `802e3bc2…`). No fallback: Fish errors surface as-is |
| VAD | Browser energy VAD (local, free) | Silero upgrade path |

No credit card needed. App **runs with zero keys** in demo mode.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env
# optional: add GROQ_API_KEY=... (Fast Response) and/or GEMINI_API_KEY=... (Overall Good),
# plus FISH_API_KEY=... for the voice. Keys stay server-side; the UI never shows them.
uvicorn app.main:app --host 0.0.0.0 --port 8000
# open http://localhost:8000 → Start conversation → allow mic → talk
```

With keys the tiers auto-activate (check `/health`); the in-app picker says only **Fast Response** / **Overall Good**. Without keys, offline mock keeps the full pipeline testable free.

## Use

- **Mic button** → always-listening starts (Web Speech `en-IN` + local VAD). Just speak anytime; talking over the AI interrupts it instantly (350ms sustained-speech gate ignores coughs/fans).
- Center shows the live line only, over a full-width aurora wave; full history lives in the **left drawer** (top-left icon); **gear** bottom-right holds mode/scenario/correction/level, voice test, challenge, summary.
- Switch **Mode** (free/practice/correction/pronunciation/roleplay/vocab/interview/challenge), **Correction** (passive/balanced/active), **Level** (auto…advanced).
- **End + summary** → short spoken feedback + detailed card (duration, words, fluency, main improvement, useful phrase, pronunciation focus, next goal).

## Pipeline

```
mic → local VAD → browser STT en-IN → commit on speech END (interim, no wait for the final)
→ WS → streaming LLM (Fast Response = Groq | Overall Good = Gemini)
→ sentence chunker + SentenceCoalescer (merges <45-char sentences)
→ first phrase: /api/tts/stream (Fish s2.1-pro-free, chunked) → <audio> plays the FIRST frames
→ later phrases: /api/tts + prefetch queue (no gaps between sentences)
```

Barge-in aborts in-flight TTS (AbortController), drops the queue and clears the
streaming element, so stale audio can never resume. Every turn prints a measured
`[VOICE LATENCY]` block (STT + network, LLM TTFT, first phrase, TTS first audio,
playback buffer, TOTAL speech-end → first AI audio) in the UI log + server console.

Logs: `[STT] [LLM] [TTS] [VAD] [AUDIO] [TURN]` in server console + UI log box.

## Tests

```bash
pytest -q
python scripts/smoke_test.py
python scripts/latency_probe.py --tiers   # per-tier speech_end → first audio, Fish stream TTFB
```

Covers: correction policy, chunker, level smoothing, §43 acceptance transcript, WS
greeting + barge-in + summary + response-mode switch, provider tiers and hidden
naming, Fish streaming + error paths, the `[VOICE LATENCY]` formatter, failure fallbacks.

## Notes

- Gemini temps not customized for 3.5-flash-lite (per docs, ignored); latency via `thinking_level=minimal`.
- Pronunciation never pretends TTS scores — uses transcript vs target + Indic confusion map (TH/V-W/R-L/S-Z/P-F/stress).
- Profiles in `data/profiles/*.json` track mistakes/level/confidence/minutes-spoken (hero metric). No sensitive data stored.
