# Voice regression correction — 28 October 2026

This version corrects the previous delivery's unwanted local/demo modes and
browser speech output. The source remains based on upstream commit `aea1a31`.
Nothing was pushed to GitHub.

## What changed

- Exactly three response choices: Gemini Live Preview (default), Groq + Fish Audio,
  Gemini + Fish Audio. Removed runtime local AI, demo generation and optional local
  recognition dependencies. Test doubles exist only under tests/.
- Removed every speech-synthesis/device-voice path in app/. Fish failures no longer
  create robotic browser output, including in Listening and voice tests.
- Fixed selected-voice routing, legacy-setting migration, startup synchronization,
  idempotent mode/voice updates and audio callbacks on HTTP fallback.
- Restored selected Gemini Live as native voice in Training and Listening turns.
  Training lesson/progress instructions remain attached to native turns.
- Gemini voice clips use the same configured Live model instead of sequential
  speculative REST-model requests. They return WAV; regular Live turns stream PCM.
- Reduced avoidable waits: no empty turns sent on normal completed Live reuse,
  no duplicate Fish opener, shorter Fish prompt, earlier coherent first phrase,
  no redundant 120ms recorder delay, bounded/lesson-free optional web lookup,
  and a 12-second playback-start timeout.
- Fixed fallback-model parameter handling, upstream cancellation cleanup, stale
  Gemini voice rejection on Fish requests and SOCKS proxy dependency support.
- Retained previous profile isolation, atomic storage, queue ownership, daily plans,
  learning modules and bounded input/session processing.

## Verification

Final validation: **225 Python tests passed**, **8 Node audio tests passed**, offline smoke and DOM smoke passed, plus compilation/syntax/diff checks. External provider connections were simulated. Validation covers:

- Python regression suite, including both real Groq/Gemini HTTP streaming adapters
  connected to the real Fish adapter/API handlers with `httpx.MockTransport` at
  the network boundary. Tests check selected voice ID, free-model header, MP3 bytes,
  early sentence events and no switch to a native/system voice.
- Real Gemini Live protocol adapter with a simulated WebSocket: setup, tagged
  PCM/captions, two turns on one connection, no empty turns, cleanup.
- Exact three modes, Live default without configured keys, ignored old browser/local
  settings, explicit missing-Fish error and native preview/reading WAV cleanup.
- Node audio tests: opener deduplication, old/new generation race, no replay of
  partial speech, same-provider retry, mute, resource cleanup and no system speech.
- DOM smoke: old Local preference migrates to Live, exactly three choices appear,
  Groq/Fish and Gemini/Fish produce Fish request URLs, typed streaming replies work,
  listening/plan controls work and stale cancellation cannot stop a newer reply.
- Offline transport smoke, Python compilation, JavaScript syntax and diff checks.

All external requests in automated provider tests are simulated. These tests do
not establish live provider availability, voice quality or real audible latency.
No real credentials or human microphone were available in this environment; no
live endpoint speed or “perfect” audio claim is made. Test your actual free-tier
keys, target browser/headset, speaker echo, rate limits and network recovery.

The Python environment emits one Starlette/httpx TestClient deprecation warning;
it does not fail tests. Full browser rendering, long mobile sessions, public
hosting/load testing and Docker build remain unverified. Single-process JSON
storage and anonymous cookies require the deployment precautions in README.md.
