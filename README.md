# Flow — AI English voice practice

Five learning modules (Conversation, Reading, Training, Roleplay and Listening),
with exactly three AI response modes:

| Response mode | Voice | Required server keys |
|---|---|---|
| **Gemini Live Preview — default** | Gemini's native streaming voice | `GEMINI_API_KEY` |
| **Groq + Fish Audio** | Selected Fish Audio voice | `GROQ_API_KEY` + `FISH_API_KEY` |
| **Gemini + Fish Audio** | Selected Fish Audio voice | `GEMINI_API_KEY` + `FISH_API_KEY` |

Local AI, Demo Coach and browser speech synthesis are removed. Fish errors appear
as errors; the application never substitutes an operating-system voice. Listening
uses the selected Fish/native Gemini voice, including speed controls. Existing
learner profiles and daily practice features remain available.

## Start

Python 3.11+ and a modern browser are required. Microphone capture requires HTTPS
or localhost.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill the keys for the modes you want to use.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open http://localhost:8000. Add all three keys to enable all three modes. Restart
Flow after changing keys. Keys stay on the server. Missing keys are reported in
Settings and when a turn is attempted; no fake coach is started.

The browser's saved Local/Demo choice is migrated to Gemini Live Preview. A saved
`TTS_PROVIDER=browser` from the previous version is ignored. There is no local
inference configuration. Browser **recognition** remains a microphone-input
fallback; it is separate from voice output and may require internet access.

## Voice and latency fixes

- Both Fish modes send the selected Fish voice ID and `model: s2.1-pro-free` header.
  An explicit Fish request cannot route to a stale Gemini voice ID.
- The opening phrase plays progressively from `/api/tts/stream`; later phrases
  prefetch while it plays. The opener is never synthesized twice.
- A failed stream may retry once through the same provider and selected voice
  before any sound starts. Partially played phrases are never replayed.
- Mode changes cancel old audio, and repeated mode/voice synchronization no longer
  kills a valid reply. Stale audio and cancellation events stay fenced.
- Normal completed Live turns reuse the connection. They no longer send an empty
  user turn when preparing the next response. Interruptions discard the old stream.
- A redundant 120ms recorder wait is removed; natural-pause VAD remains. The Fish
  prompt is shorter and a coherent opening can stream earlier. Optional factual
  search is capped at 1.5 seconds and skipped during lessons.
- Gemini voice previews and reading clips use the configured Live model/voice,
  rather than probing unrelated REST TTS model names. Normal Live conversation
  streams PCM immediately; standalone preview/reading clips buffer a WAV response.
- HTTP/SSE fallback retains audio/caption callbacks. Fish failures are visible in
  the active view. No fallback can turn an API error into robotic browser speech.

Use the UI's `[VOICE LATENCY]` logs for speech-end, STT/network, first token,
first phrase and playback stages. Actual audible latency depends on model load,
network and device; automated transport timing is not an audible measurement.

With speakers, transcript echo checks reduce self-interruption. With headphones,
enable “interrupt on confirmed speech” for faster held-microphone interruption.
Manual Interrupt stops audio immediately. Validate on your target devices.

## Learning and storage

Settings → Daily plan and progress contains placement samples, daily tasks,
recurring-error review and progress history. Placement is a provisional transcript
heuristic, not a certified CEFR result. Reading transcript differences do not prove
pronunciation errors. Vocabulary counts observed words; review completion is
self-reported. Six starter Listening exercises should be expanded for long programs.

Use one persistent process and a durable `DATA_DIR`. Atomic JSON profiles and
signed browser cookies provide anonymous profile isolation, not account login.
Add authentication, HTTPS, backups and load testing before public hosting. A
multi-worker deployment needs shared transactional storage/coordination. The
retained Vercel manifest does not provide durable storage or full WebSocket parity.

```bash
docker build -t flow .
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -v flow-data:/data flow
```

## Free provider boundaries

Use free, unbilled provider accounts if API payments are unacceptable. Flow cannot
inspect account billing or guarantee provider quotas. It never buys credits,
upgrades subscriptions or switches to a paid Fish model. Check current limits:
[Groq](https://console.groq.com/docs/rate-limits),
[Gemini](https://ai.google.dev/gemini-api/docs/pricing),
[Fish Audio](https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits).

## Checks

```bash
pip install -r requirements-dev.txt
python -m pytest -q
node --test scripts/test_audio.cjs
python scripts/smoke_test.py
```

Optional DOM check: `npm ci`, start `python -m tests.fixture_server`, then
`npm run test:dom`. The fixture server is test-only, stores temporary data, uses
explicit test doubles and must never be used as production startup. No npm build
is needed to serve Flow. See [AUDIT_AND_TESTING.md](AUDIT_AND_TESTING.md).
