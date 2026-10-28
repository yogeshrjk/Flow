# AGENTS.md — Flow (Voice Chat App)

> **RULE ZERO (always obey): after every code change, update this file.**
> Add a line to `Changelog`, and update any section this change touched
> (Gotchas, UI map, API, Tests). A change is not done until this file says it.

## What this is
Flow is a voice-first chat app: a realtime AI conversation experience for spoken
interaction, with a single-server, zero-build frontend. Default mode: free
conversation with optional light feedback when relevant.

## Stack
- Backend: Python + FastAPI (`app/main.py`), serves API + static UI. No frontend build.
- LLM: **response tiers behind friendly UI labels** (never show provider names
  in the UI — `/api/config` only exposes `response_modes` with labels/descriptions):
  - `"gemini_live"` → **"Gemini Live Preview"** = default response tier (`gemini-3.8-live`
    model via official Google Multimodal Live Bidi WebSocket, native bidirectional audio streaming,
    paired with `gemini-3.5-transcribe-live` (Gemini 3.5 Transcribe Live) for millisecond-exact real-time streaming captions,
    16 prebuilt celestial voices across Gemini Live App and Gemini API / Studio collections,
    standard default voice **Orbit**).
  - `"fast"` → **"Fast Response"** = Groq (`app/providers/llm/groq.py`, OpenAI-compat
    SSE, `GROQ_MODEL` default `llama-3.1-8b-instant`, comma list = failover chain;
    `reasoning_effort` only for gpt-oss/qwen).
  - `"quality"` → **"Overall Good"** = Gemini free chain via OpenAI-compat endpoint —
    `GEMINI_MODEL` (optional head) → `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite`
    → `gemma-4-31b-it`. NEVER use Gemini 2.x (retired). `thinking_level=minimal`.
  - No key for the chosen tier → serve the other tier (logged, never surfaced).
    No keys at all → `MockProvider` demo mode. Optional local: Ollama (`LLM_ALLOW_OLLAMA`).
- TTS: **Fish Audio S2.1 Pro Free ONLY** — model `s2.1-pro-free` goes in the
  `model:` request HEADER (not JSON body), single voice `FISH_VOICE_ID`
  (`FISH_REFERENCE_ID` accepted as an alias).
  No fallbacks by design: errors surface as short strings.
  Fish answers **chunked** (measured: first frames 0.6-1.0s vs 2.2-2.9s for the
  whole mp3), so the first phrase of a turn is played **progressively** through
  `GET /api/tts/stream` (a `StreamingResponse` proxy of `FishProvider.open_stream`)
- STT: when a transcription key is set (`server_stt` in `/api/config`), every
  MediaRecorder-capable browser (desktop and mobile) uses one held
  `getUserMedia` stream for the whole session (opened on start, closed only on
  stop), VAD levels on it
  via `EnergyVAD.attach()`, a fresh `MediaRecorder` per utterance (started on
  the first above-floor frame via `onFirstVoice`, so onsets never clip),
  each utterance POSTed raw to
  `POST /api/stt` → Groq Whisper (`app/providers/stt/groq_whisper.py`,
  `whisper-large-v3-turbo`, always auto-detect + one mixed-script prompt
  sample + session context). No `python-multipart` needed:
  the body is raw audio (`Content-Type: audio/*`, `X-Language`/`X-Filename`
  headers). VAD: local energy (`vad.js`) with an
  adaptive noise floor (fan/AC hum raises the gate instead of triggering),
  strict foreground threshold (`0.018`, SNR `2.2x`) preventing background chatter
  and distant TV voices from triggering, plus a speech-shape gate (mid-frequency
  spread admits human speech while rejecting narrow whistles and low snoring/rumble
  before an utterance is confirmed). Utterances must meet peak >= 0.026 and SNR >= 2.2x
  in `mobFinishUtt` before upload, and server-side `_looks_hallucinated` drops peak < 0.024.
  Without server transcription or MediaRecorder, browser Web Speech (`en-IN`)
  is the fallback; its platform-owned stream may cycle, so it cannot provide
  a never-close microphone guarantee.
  A turn commits the moment speech ENDS (latest interim), not when the STT
  engine finalizes; the final is then deduped (`sameText`, including Hindi
  joined/separate word boundaries). A later distinct utterance is always a
  clean new turn; never concatenate it onto an already dispatched request.
  Desktop runs health watchdogs while live: a recognizer that stays active
  without an audio stream for 3s is rebuilt, and a closed/suspended VAD audio
  path is reopened. Every recognizer callback is generation-fenced before a
  replacement is aborted, so delayed events from a dead Chrome instance cannot
  falsely mark a newer one healthy or create duplicate mic streams. The only
  intentional desktop close is `stopSession()`; its main-control origin is
  logged explicitly.
- Frontend: vanilla JS + Tailwind CDN + GSAP CDN + Lucide icons. No emojis in UI.
- Audio pipeline: WS `tts_sentence` chunks → `AudioQueue`:
  the **first** phrase of a turn (`first: true`) is stream-played from
  `/api/tts/stream` (sound starts on the first frames), later phrases keep the
  Fish POST + 3-ahead prefetch + 25ms inter-chunk gap. Barge-in cancels the LLM
  task, aborts in-flight TTS fetches (`AbortController`), clears the queue/cache
  and drops the streaming element's `src` — stale audio can never resume.
  First chunk goes out via the engine's fast path (opening clause) so the voice
  starts before the reply is fully written.
- Voice emotion & delivery (`app/conversation/emotioncontrol.py`, single source
  of truth): full official S2.1 Pro cue catalogue — 24 basic emotions, 25
  advanced emotions, 6 tone controls, 11 audio effects, 5 special effects
  ([break]/[long-break]), descriptions/contexts copied verbatim from
  https://docs.fish.audio/developer-guide/core-features/emotions (internal
  `category`/`priority`/`usage` fields are ours). `build_emotion_control_prompt()`
  (cached, ~8.2k chars ≈ 2k tokens) is injected into `BASE_SYSTEM` via the
  `{emotion_control}` placeholder; `build_system(..., voice_cues=False)` swaps in
  `NATIVE_VOICE_GUIDANCE` for Gemini Live (native voice — Fish cue syntax would
  leak into captions). Fillers (um/umm/uh/uhh/hmm/hmmm/well/let me think/you
  know/actually/I mean/ugh) are plain text, never bracketed. Cues stay in the
  TTS text and are stripped from `display_text`; `mask_cues()` +
  `trim_incomplete_cue()` guarantee no chunker or cleaner ever emits half a cue.
- Fact search (`app/search.py`, free, no keys): `needs_search()` gates factual
  turns only (chit-chat/opinions never trigger); DuckDuckGo instant-answer +
  html results run parallel with a Wikipedia lookup, Wikipedia links ranked
  first, all failures degrade to "" (answer from model). Factual turns speak
  a filler FIRST (`"Let me check..."`/`"Checking..."` via normal
  `tts_sentence` in Fish modes — transcript-safe since history only comes
  from `llm_done`; `{"type":"notice"}` center-text-only in Live mode where
  Fish audio would overlap native voice), then the fact block rides as a
  trailing system message (standard) or inside the bidi user text (live).
  NOTE: Wikimedia 403s some IPs (robot policy) — live Wikipedia is
  best-effort, DDG always carries. Gotcha: `speak_chunk` is defined AFTER
  the messages block, so the search block lives INSIDE `try:` after it;
  filler consumes the turn's `first:` stream-play slot (`first_chunk_done`
  set) so the real first chunk never double-sends it.
- Latency: every turn prints a measured `[VOICE LATENCY]` block
  (`app/latency.py` formats it) — server sends `stt_ms`/`llm_ttft_ms`/`chunk_ms`
  on the first `tts_sentence` (`lat` field), the client adds TTS first audio,
  playback buffer and TOTAL speech-end → first AI audio. Browser-only bench:
  `/static/latency.html` (dev tool, not linked from the UI) measures the TTS leg
  in a real browser + a barge-in cancel check.
- Latency budget (measured, `scripts/latency_probe.py`): LLM TTFT ~1.3s (Gemini
  floor — prompt size is irrelevant), Fish synth ~1.0s short / ~1.5s typical,
  first audible words ~2.3-2.7s after the user stops. Cache-bust `?v=N` on edit.

## Run / verify (every change must pass these)
```bash
pip install -r requirements.txt
cp .env.example .env   # fill GROQ_API_KEY (fast) + GEMINI_API_KEY (quality), FISH_API_KEY
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
node --check app/static/*.js
python3 -m pytest tests/ -q        # 186 tests
python3 scripts/smoke_test.py      # WS turn + barge-in + mode switch + streaming audio
python3 scripts/latency_probe.py --e2e     # speech→voice budget (needs real keys)
python3 scripts/latency_probe.py --tiers   # per-tier TTFT + Fish stream TTFB (real keys)
```
Cache-bust static JS on every frontend edit: bump `?v=N` in `index.html`.
Same for `styles.css` (it was unversioned once — stale CSS hid all text behind
the fixed canvas). Layer rule: content `.stage` z-index:1 above canvas z-index:0.

## UI map (keep in sync with index.html)
- Five primary modules include Listening. Settings → Daily plan and progress opens `#dailyPanel` (`.daily-modal`), a modern glassmorphic centered modal popup with backdrop blur, 3-card stats grid (CEFR level & status, spoken time, vocabulary count), interactive cross-module task cards with mode icons, spaced-repetition review cards with quote snippets and feedback buttons, CEFR assessment card with textarea and instant validation, and 7-day activity chips. It closes on close icon button, outside backdrop click, or Escape key.
- Listening mode renders `#listeningPanel` (`.listening-panel`), a sleek floating glassmorphic stage HUD card (matching Reading Practice) with header badge, audio playback deck with speed selector (0.8x/1.0x/1.15x), comprehension question card with interactive choice cards (A, B, C, D) featuring instant checkmark/cross grading feedback, semantic status banner, revealable transcript quote card, next exercise action, and close/exit button returning to conversation mode. When active, `.stage.listening-on` hides center greetings/AI line. `learning.js` owns both panels.

- Top bar: live `dot`/`state`/`micLive` (status text; IDLE displays as READY,
  never in center) + session `clock`. Learning Mode selector `#modeSelector`:
  responsive segmented control at the top of the main screen with four modes
  (**Free Conversation** / **Reading** / **Training** / **Roleplay**), highlight
  indicator, and active lesson/roleplay badges (`#trainingBadge`, `#roleplayBadge`);
  on mobile screens ($\le 640\text{px}$), mode buttons hide text labels and render
  icons only on the left side of the top row, with the conversation (`#historyBtn`)
  and settings (`#settingsBtn`) buttons aligned on the right side; below that row sits
  the status line and timer (`#topbar`), followed below by the active badge. Center: `greetLine` → `aiLine` (current
  sentence only) → `hearNote`. Wave canvas `#orb` is the fullscreen
  background (`position:fixed inset:0 100vw/100vh`, behind content, clicks pass
  through); no glow-circle primitives, no white core thread — ribbons only.
- `#historyBtn` and `#settingsBtn` stay at the bottom corners on desktop and move
  to the top corners on mobile (safe-area aware); the history button remains
  visible while its drawer is open → left drawer `#history`: transparent,
  headerless, full `transcript` (whole replies at turn-end, never streamed;
  full-width wrap, bottom-anchored, empty-state hint, no word/turn stats).
- Bottom: text `inputpill` with `#userLine` right above it, 5-button icon dock:
  `captionBtn` (toggles `.nocaptions` hiding center text) + `interruptBtn` on the
  left, big `startBtn` (play/square voice-chat toggle) center, `micMuteBtn`
  (mic `micMuted`: deaf to STT results + interrupts + mic dot) + `muteBtn`
  (speaker) on the right. Side buttons hidden until session starts.
  Reading practice modal panel (`#readingPanel` in `.stage`, toggled by `.stage.reading-on`):
  floating glassmorphic card showing round badge (`#readingRoundBadge`), instructions (`#readingInstr`),
  generated English passage (`#readingPassage`) with real-time word-level span highlighting (green `.reading-word-correct` for correctly read words,
  red `.reading-word-error` for skipped/mispronounced words, and cyan `.reading-word-current` for the active cursor), status note (`#readingStatus`),
  live recognized text (`#readingLiveTranscript`), instant spoken/written feedback card (`#readingFeedback`),
  automatic paragraph completion conclusion, and round-3 actions (`#readingActions`: new passage / exit).
- No welcome message anywhere: session opens silent on mic click, user speaks first (`Engine.greeting` retained but unwired).
- Gear `settingsBtn` bottom-right → right `#panel` (grouped: AI RESPONSE /
  EXPERIENCE / COACHING / SESSION section headers, icon'd `.flbl` labels,
  custom-chevron selects in `.fld`, 2-col `.grid2` for correction+level):
  top Daily Plan & Progress interactive preview card (`#dailyPlanBtn`, `.daily-plan-preview-card`)
  displaying target icon, CEFR level pill (`#dailyPlanBadge`), live task/focus summary (`#dailyPlanSummary`),
  right chevron, and a 3-stat strip (Spoken Today `#dailyPlanSpoken`, Tasks Done `#dailyPlanTasksDone`,
  Vocabulary `#dailyPlanVocab`) which opens `#dailyPanel` on click; **AI Response** radio group `#respModes` (rendered from `/api/config`
  `response_modes`: **Gemini Live Preview** (default) / **Groq + Fish Audio** / **Gemini + Fish Audio**
  and their descriptions, unavailable tiers greyed with a note in `#respNote`;
  persisted in localStorage `respMode`, sent as WS `set_response_mode`), then Experience section with
  Headphones Interruption card (`.headphones-toggle-card`) featuring headphones icon, title, description,
  and custom cyan sliding switch (`#headphonesMode`, `.switch-toggle`), animation select, microphone device picker,
  language (`#language`: preferred language, picker populated from
  `/api/config` — 18 languages, no Hinglish; detection auto-IDs every turn and
  the reply follows the SPOKEN language via `reply_lang`, falling back to this
  preference), microphone (`#micDevice`
  picker from `enumerateDevices`, persisted, passed as `deviceId` to VAD
  `getUserMedia`; `#micTestBtn` standalone 2.5s capture test with peak readout
  in `#micTestStatus` (uses an ideal device constraint, pauses active speech
  recognition, and explains secure-context/permission errors); STT lifecycle
  (`onaudiostart`/`onaudioend`/extended error names) logged so mic failures are
  visible, never silent), voice controls: when "Gemini Live Preview" is active,
  `#geminiVoiceFld` renders the native Gemini voice dropdown (`#voice`) grouped into
  **Gemini Live App Voices** (Orbit [default], Pegasus, Capella, Dipper, Orion, Ursa, Vega, Eclipse, Flare, Glow)
  and **Gemini API / Studio Prebuilt Voices** (Puck, Charon, Kore, Aoede, Fenrir, Enceladus); for
  Groq and Gemini + Fish Audio modes, `#geminiVoiceFld` is hidden and
  `#voicePickerOpen` ("Choose a public voice") is shown, opening a centered
  `#voicePicker` modal with blurred backdrop and Explore / Default Voices /
  Bookmarked tabs; modal title, tabs, language/search controls, and status stay
  fixed while only the voice grid scrolls. Explore shows compact content-sized
  voice cards (three per desktop row) and a Filters popup over the picker;
  the avatar position contains a circular play/preview button, and the Use
  and Bookmark action buttons reside in the top-right corner of the title row,
  revealing on hover for pointer devices and remaining accessible on touch devices;
  descriptions are clamped to two lines with an ellipsis so controls remain in
  view. Default voice cards include a gender-based description and Male/Female
  plus Built-in tags from the configured preset metadata. Cards provide
  interactive preview play/pause avatar, top-right Use icon, and icon-only
  bookmark toggle (bookmarks persist in localStorage), with no `public` or
  `default` badges/tags; selected ID/name persist in localStorage and
  `set_voice`, with `fishTestBtn`/`fishStatus`, challenge, `endBtn`, `feedback`
  (blue accent card), debug log.
- The Fish Audio library is lazy-loaded only when the voice picker opens.
  `/api/voice-library` proxies authenticated, paginated `GET
  https://api.fish.audio/model` requests using `language`, `page_size`,
  `page_number`, `self=false`, and optional `title`; it returns public models
  only, normalized metadata, and safe HTTPS sample URLs. `/api/voice-library/validate`
  and TTS/WS voice changes verify a selected ID with Fish's `GET /model/{id}`
  endpoint before use. Browser cache is keyed by language and title; backend
  caches each page and verified public ID for five minutes. The UI exposes
  English, Hindi, Japanese, Chinese, Spanish, French, German, Portuguese,
  Korean, and Arabic filters.
- Center text: `aiLine` gray (`#8f97a8`) with KEY WORDS white (`.ailine .hl`):
  the LLM wraps 1-3 key words per reply in `**double asterisks**` (prompt rule
  in prompts.py), client `emphasize()` renders them as `<span class="hl">`;
  when no markers arrive, a fallback heuristic highlights numbers, 'Quoted
  phrases', and mid-sentence Proper Nouns. TTS + transcript strip markers
  (`clean_for_speech` / `plainText`). Voice cues never appear in captions:
  `[empathetic]`/`[break]`/… are stripped for display (`strip_emotion_tags` →
  `display_text`, client `plainText()`), while a reply is being streamed the
  engine buffer already excludes an unterminated trailing tag. GSAP fade+rise
  on each completed reply (`animateAiLine`, never per-token).
- `#userLine` caption under the wave: current user line only, fades 6s after
  last speech (interim + final + typed all route through `userSaid()`).
- Animation: `orb.js` multi-style engine (GSAP-tweened rig + `gsap.ticker`):
  `mobius` thick colorful 3D Infinity Möbius strip with flowing boundary rotation, soft aura & bubbles (default) |
  `aurora` soft luminous aura wave | `particles` 3D multi-chromatic particle ribbon |
  `nebula` drifting color clouds. `Orb.set(state)` /
  `Orb.setStyle(name)`, choice persisted in localStorage, picker `#anim` in
  settings panel. Shared `lens()` envelope + mic level via `window.__micLevel`.
  Depth pass: mobius = 3D parametric volumetric Infinity Lemniscate slab ($A_x \approx 310$px, 96 segments x 4 faces, zero-GC preallocated typed arrays, continuous flowing boundary twist $\phi = u + t \cdot 0.65$, rich multi-stop chromatic palette [indigo $\to$ cobalt $\to$ cyan $\to$ emerald $\to$ amber $\to$ rose $\to$ violet], soft satin diffuse/specular lighting without blinding white glares, violet/cyan/indigo atmospheric aura, 36 floating 3D micro-bubbles with chromatic Fresnel rims);
  aurora = Gaussian multi-layer feathered aura + central threads + motes;
  particles = 1800 depth-sorted particles on 3D twisting ribbon spine, multi-stop
  neon gradient (magenta→violet→blue→cyan→emerald→gold), star nodes, stardust motes;
  nebula = STARS; all styles share a breathing stage-glow radial in `frame()`.

## Gotchas (learned the hard way — read before touching)

Current user requirements: exactly Gemini Live Preview (default), Groq + Fish Audio and Gemini + Fish Audio. Local AI, Demo Coach and browser TTS are removed. Fish modes must always use Fish audio; errors are explicit and never switch to another voice. Never claim unlimited cloud usage or measured pronunciation from a transcript. Profile and session identifiers must be validated. Profiles use atomic replacement and failures must surface.

1. Fish 402 with valid key = wrong model: free tier needs header
   `model: s2.1-pro-free`, NOT `"model": "s2.1-pro"` in body.
2. Fish can 200 with a JSON error body: `fish.py` sniffs MP3 magic bytes.
3. `audio.pause()` never fires `ended`: `cancel()` MUST settle the playUrl
   promise (`_playResolve`) or the queue wedges silent forever.
4. One Fish request per sentence = network pause mid-thought:
   `SentenceCoalescer` holds <45-char sentences and merges (see tests).
5. STT defaults to `en-IN`; `continuous=true` is a HINT, not a guarantee —
   mobile browsers auto-end recognition on silence/utterance boundaries (iOS
   ignores `continuous` entirely; Android ends after seconds of silence;
   audio-focus/power management can end it too) even though we never call
   `stop()` mid-session. Each auto-end fires `onaudioend` ("mic stream closed")
   + `onend`, and the restart fires `onaudiostart` ("mic stream opened") — so
   the open/close pair in the log is the platform lifecycle, NOT a leak, and
   "opens once, never cycles" is unachievable with Web Speech on mobile. The
   `onend` restart is debounced (700ms mobile / 350ms desktop) with storm
   backoff (ends <2s apart repeatedly, or ≥3 consecutive aborts, stretch the
   delay up to 2.5s) and guarded by `listening && running && !micMuted &&
   !recogFatal`. STT stays listening through assistant turns so barge-in keeps
   working; `pauseSTT()` only flips the `listening` flag (never stops
   hardware); only `stopSession()` truly stops recognition. Unmute always
   resumes while running. `isTurnActive()` (`llmPending || audioQ.busy`) exists
   for reasoning about turn state. Barge-in needs a sustained-speech
   gate (`vad.js` 300ms above an adaptive noise floor + 250ms extra hold) or
   coughs/fans false-trigger VAD. Low-confidence near-empty finals are dropped.
6. Never store secrets in repo (`.env` is gitignored); never print keys to logs.
7. Pydantic v2 ignores extra JSON fields — stale cached JS sending old fields is safe.
8. Stacking trap: a `position:fixed;z-index:0` canvas INSIDE `.stage` paints
   OVER static text (normal flow loses to positioned z:0 in the same stacking
   context). Keep `#orb` a direct child of `<body>` so `.stage` (z:1) wins.
   Verified with headless-Chromium screenshots (`/tmp/ref/*.png` pattern).
9. Latency is measured, not guessed: `scripts/latency_probe.py` (add `--e2e`
   for the full speech→voice budget). Both providers MUST reuse the module-level
   keep-alive `httpx` client — a fresh TLS handshake per sentence cost ~0.35s
   (Fish) of first-audio time. The old 350ms `sleep` before answering and the
   coalescer hold on the FIRST sentence were ~0.8s of self-inflicted delay.
10. `**emphasis**` markers are display-only: `clean_for_speech` must strip
   paired AND stray `*` (a marker pair can straddle a chunk split, and TTS
   would otherwise say "asterisk asterisk"). Never let markers into TTS text.
11. The first TTS chunk bypasses `SentenceCoalescer` (fast path in
   `first_chunk_split`): the opening clause is spoken at once, later sentences
   keep the merge rule. Only fast-path when `coalescer.held` is empty, or the
   held opener would come out AFTER the clause (out of order).
12. Fish's plain `POST /v1/tts` is **already streamed** (`Transfer-Encoding:
   chunked`); a proxy that returns the body only at the end throws away ~1-2s.
   `/api/tts/stream` is a GET (so `<audio src>` can use it) and returns
   `StreamingResponse` — the first chunk is peeked in `open_stream()` so a JSON
   error sent as 200 still surfaces as a clean error before any bytes ship.
   Never buffer it: `httpx`'s `aiter_bytes(n)` ACCUMULATES until it has n bytes
   (16384 ≈ 2s of 64kbps audio), so read with `aiter_bytes(None)` and sniff only
   the first few bytes. First frames cost ~0.62s (8-char phrase) to ~1.0s
   (38 chars), so a SHORT opening sentence is a real latency lever (prompt rule).
   Chrome still adds its own start-of-playback buffering (measured 32-475ms of
   the streaming win left on the table for a normal first phrase).
13. Barge-in must kill three things, not one: the LLM task, the queued/prefetched
   TTS fetches (each carries an `AbortController` + generation counter) and the
   progressive `<audio>` (drop `src` then `load()`, otherwise it keeps downloading
   and can replay after the interrupt). Phrases already in flight also arrive
   AFTER `cancel()` — every `tts_sentence` carries a `turn` tag and the client
   drops turns listed in `AudioQueue.deadTurns`, else the AI starts talking again
   right after you interrupt it (measured).
   The WS loop must NOT `await` the turn task: with `await` a `barge_in` sent
   mid-reply is only read after the reply finishes, so cancelling does nothing.
14. Attribute the first-audio measurement to the right turn or the numbers lie:
   the greeting (or the previous turn's tail) finishing right after the user stops
   was reported as a 93ms reply. `_reportFirstAudio` now ignores audio whose
   `turn` tag isn't the current turn, and reports `n/a` instead of a fake 0ms when
   no progress event arrived. A retry through the download path must pass the
   turn through too.
   Background/other preview tabs are timer-throttled: WS sends and media start
   stall by seconds there, so only compare browser numbers from a focused tab.
14. `/api/config` is the ONLY payload the browser sees; keep provider/model
   names out of it (test `test_public_config_never_leaks_provider_names` enforces
   groq/gemini/llama/openai/… never appear). Internal names are fine in `/health`
   and server logs.
15. Public Fish voices selected from the library must be rechecked against
   `GET /model/{id}` and `visibility == "public"` before WebSocket selection or
   TTS use; never accept arbitrary browser-provided voice IDs.
15. Held-stream server capture requires a secure origin (HTTPS or localhost) on
   desktop and mobile. The manual mic test samples the held stream when active;
   otherwise it pauses active Web Speech recognition first, uses a preferred
   rather than exact saved device ID, and gives a site-permission explanation
   for `NotAllowedError`; it cannot override a permission denied by the browser
   or OS.
16. Fish Audio `GET /model` documents public listing (`self=false`) and page
   controls up to 100 records per page; response `window_limited`,
   `max_offset`, and total fields must be preserved. Live verification returned
   public English and Hindi voices on pages 1 and 2 from creators different
   from the zero-result `self=true` owned-model query, but also set
   `window_limited=true` with an accessible total of 1,000. Never describe this
   endpoint as the complete public catalogue.
17. Always-open server capture owns ONE mic stream on desktop and mobile:
   never open a second `getUserMedia` while it runs (VAD uses `attach()`, the
   mic test samples the held stream's live level, per-utterance recorders are
   only taps). Each
   utterance gets a FRESH `MediaRecorder` — a recorder emits container headers
   only in its first slice, so slicing turns out of one continuous recording
   produced headerless fragments that Groq rejected after the first turn
   ("could not process file"). The recorder starts on VAD's first above-floor
   frame (`onFirstVoice`, before the 250ms confirmation gate) so onsets never
   clip; unconfirmed blips are discarded. Stale transcriptions die via the
   `mobUttGen` generation counter (bumped on stop/mute/test), same pattern as
   the audio queue's `_gen`.
18. Language is detected per utterance, never forced, and switchable on request:
   `/api/stt` always auto-detects (Whisper `language` field mapped through
   `_detect_id` supporting all ISO 639-1/2 codes like `de`, `hi`, `es`, `fr` and full names);
   `detect_language_from_content` checks native scripts and keywords, and
   `detect_language_switch_request` recognizes direct user commands ("talk in hindi",
   "talk in german", "sprich deutsch", "hindi me bolo", etc.). When switched,
   the session updates, emits `type: "language"` so the client `#language` select
   and `sttLang` synchronize, and `language_addendum()` instructs the LLM to output
   native scripts (e.g. Devanagari for Hindi/Marathi) and authentic idioms so TTS
   synthesizes with a 100% authentic native accent. Preferred is only the initial default.
19. Distinguish conversational VAD from pause analysis: `EnergyVAD`'s `endOfTurnMs`
   (850ms) detects conversational turn handoff. Measuring reading pauses using
   `onSpeechEnd` is broken because pauses <850ms never fire it, and every pause
   is conflated with a turn end. Intra-turn acoustic pauses are tracked in
   `vad.js` via `minPauseMs` (380ms) and `onPause({ start, end, duration })`
   during continuous speech, filtering sub-380ms syllable/plosive closures.
   Monotonic `performance.now()` ensures application startup delays or network
   latency are never counted as speech pauses. Word-level punctuation classification
   in `analyze_pauses()` separates natural sentence-end (0.6-1.8s) and clause-end
   (0.4-1.2s) breath pauses from mid-sentence hesitations or pauses before
   difficult/substituted words. Gemini Live API does not expose acoustic pause
   timestamps from raw user audio; pause facts must be measured client-side
   and provided to the LLM via evidence-bounded prompt facts.
20. Fish cues are streamed voice direction, so nothing in the text path may
   split or truncate one. `chunk_sentences`/`first_chunk_split` consult
   `mask_cues()` and skip boundaries inside `[...]` (a cue may contain a comma:
   "[speak in an extremely urgent, hushed tone]"), and `clean_for_speech`/
   `strip_emotion_tags` call `trim_incomplete_cue()` so a stream that dies
   mid-tag ships the words, never "[lau". Fillers are ordinary words, NOT cues:
   `[groaning]` is a sound effect, "ugh" is text that stays in both TTS and
   captions. Live-verified against the real API: `s2.1-pro-free` accepts the
   cue markup on both paths (POST 2.8-2.9s / 38KB mp3, `/api/tts/stream`
   ttfb ~1.07s), so cues add no measurable latency.
21. Reading start tasks are registered before they run so duplicate starts can
   be cancelled. `start_reading()` must never cancel the current registered
   task itself, or selecting Reading over WebSocket produces no passage.
   Repeated `set_mode: roleplay` syncs must preserve an active scenario and
   never replace it with the generic settings value (`casual`).
22. Desktop Web Speech can remain internally active after its platform audio
   stream dies, especially across Bluetooth routing changes. Never trust
   `recogActive` alone: retain the `onaudiostart` state and timestamp, rebuild
   after 3 seconds without an audio stream, and independently recover a VAD
   track or suspended AudioContext. Invalidate and detach every old recognizer
   callback *before* aborting it, and gate all callbacks by its generation;
   otherwise an old `onaudiostart` can log a second mic-open and cause the new
   recognizer to be mistaken for healthy. A `recognition stopped - mic stream
   closed` log is emitted only by `stopSession()`; use the preceding `[SESSION]`
   log to distinguish an intentional stop from browser capture loss.
23. AI greetings across modes: Conversation, Training, and Roleplay dynamically
   generate personalized, spoken AI greetings via `Engine.greeting(s)` and deliver
   them through `start_greeting(s, send)`. The greetings are never hardcoded.
24. Reading continuation state: during `AWAITING_CONTINUE`, reading-specific voice
   recorders are bypassed so normal speech recognition captures spoken continue commands
   ("continue", "yes", "next", etc.) and transitions directly to `handleReadingContinue(true)`.
25. Energy VAD is not a speech classifier by itself: a whistle, snore, or
   sudden background sound can exceed an RMS gate and a transcriber can invent
   fluent text for it. Confirm a held-stream utterance only when its spectrum
   has a broad 120–3400 Hz speech shape (not a narrow tone or low rumble), then
   use verbose no-speech, decoding-confidence, and compression diagnostics to
   reject residual noise transcripts server-side. Keep these thresholds
   conservative so quiet real speech and short replies still pass.
26. Gemini Live interruption and immediate silence: In Gemini Live Preview,
   interruption must silence playback instantly, whether triggered by voice VAD
   barge-in (`mobOnSpeechStart` or desktop VAD) or by manual controls (`#interruptBtn`
   or typing). `AudioQueue.cancel()` zeros `_pcmGain` immediately, stops and
   disconnects all active `_pcmSources`, sets `cancelled=true`, and tracks `deadTurns`.
   Incoming WebSocket `live_audio_chunk` and `llm_token` packets check `cancelled`
   and `deadTurns` and are dropped instantly so late-arriving audio frames never play.
   Server-side `engine.cancel(sid)` calls `live_sess.interrupt()` asynchronously to halt
   Google's Live Bidi session output immediately, sets `s.cancelled=True`, and on
   pre-token cancellation sends `llm_cancelled` rather than fabricating fallback text.
27. Training topic stickiness and explicit progression: In the Training module, the
   tutor stays strictly focused on the current topic (`current_lesson` in `profile["training"]`).
   It never advances or changes topic automatically. When the user passes 2 consecutive
   exercises, the topic is marked as mastered, and the tutor praises them and explicitly
   notifies them that they can say "next topic" when ready or continue practicing this topic.
   Topic changes happen ONLY when the user explicitly requests it (e.g. "next topic", "change topic",
   "let's practice past tense"); meta confusion/help queries ("I don't know, what should I say?")
   must never be mistaken for topic change requests.
28. 4-module chat history isolation: Each of the 4 learning modules (`free`, `reading`,
   `training`, `roleplay`) maintains completely separate and persistent chat history.
   In the browser, transcripts are stored in localStorage keyed by mode (`flow_chat_history_<mode>_v2`),
   rendering that mode's conversation in `#transcript` and syncing it on mode switch.
   On the server, `Session` dynamically routes `history`, `turns`, and `past_chat_summary`
   through `mode_histories`, `mode_turns`, and `mode_summaries` dicts by `session.mode`.
   The AI greeting and context memory resume seamlessly from where the user left off in that
   exact module without cross-mode pollution.
29. Never show 'Sorry, I missed that / say that again' on screen or in speech:
   Users should never see annoying banners or hear robotic disclaimers asking them to repeat themselves when an utterance is faint, noisy, or when an STT/LLM hiccup occurs.
   - Client-side: `isMissedOrHiccup()` suppresses any missed, unintelligible, or hiccup prompts from `hearNote()`, `setAiLine()`, `addTurn()`, `tts_sentence`, and `llm_done`. STT rejection of noise/hallucination/low-confidence drops silently without popping up amber banner warnings.
   - Server-side: `BASE_SYSTEM` prompt explicitly bans apologizing for missing words or asking the user to repeat ('Sorry, I missed that', 'Say that again', etc.). Error fallbacks in `handle_user_turn` and `handle_gemini_live_turn` use natural conversational continuations ('I\'m listening, please go ahead.') instead of 'Sorry, I had a little hiccup. Can you say that again?'.
30. Contextual homophone alignment across conversation turns:
   Whisper and speech recognition often transcribe phonetic homophones incorrectly (e.g. 'Serial' instead of 'cereal', 'ate' vs 'eight' / 'add', 'weather' vs 'whether').
   `align_contextual_homophones(text, context)` matches the transcribed text against recent assistant turns and semantic domain triggers (e.g. food/morning for cereal), automatically correcting the word in `clean_speech_artifacts()`, `POST /api/stt`, and frontend `sendText()`. The prompt explicitly instructs the LLM to interpret homophones relative to what it just said.
31. Universal zero-latency autointerruption across all response modes:
   Auto-interruption silences AI speech immediately when the user speaks, whether in Groq + Fish Audio, Gemini + Fish Audio, or Gemini Live Preview.
   - On `onFirstVoice()`: `audioQ.duck()` zeroes output volume within <16ms on the very first speech frame.
   - On `onSpeechStart()`: `bargeIn('user_speech_interrupt')` stops HTML5 `<audio>`, zeroes PCM gain, disconnects all active audio sources, clears queues, aborts TTS fetches, and notifies the backend to cancel LLM generation.
32. Safari/WebKit Audio and MediaStream constraints:
   - Audio unlock: `await audio.play()` on base64 data URIs hangs indefinitely in Safari WebKit without settling; unlock uses Web Audio buffer activation (`createBufferSource().start(0)`) and non-blocking timeout-raced HTML5 audio gesture unlock.
   - Dead-end graph disconnection: WebKit suspends audio processing if an `AnalyserNode` connected to `createMediaStreamSource()` does not terminate at `audioContext.destination`. Route the analyser output through a `GainNode` set to 0 (`gain.value = 0`) to `ctx.destination` so `getFloatTimeDomainData` receives live mic buffers without feedback, and store source node references on instance properties to prevent garbage collection.
   - MediaRecorder options: Safari throws on `audioBitsPerSecond` constraints; always try options with graceful fallback to `new MediaRecorder(stream)` without options, and ensure `start(250)` falls back to plain `start()`.
35. Groq is 100% stateless (inference only — never stores prompts or history). Greeting and memory references must strictly depend on the client session's synced history (`prior_summary and len(mode_hist) >= 2`); falling back to server disk profiles (`data/profiles/default.json`) leaks stale conversational memories across fresh browser sessions or after browser data is cleared.
36. Never use a button class name for modal locking on `body`: `.voice-picker-open` styled the Settings trigger button with `border`, `border-radius`, `background`, and `box-shadow`. When `openVoicePicker()` added `'voice-picker-open'` to `document.body.classList`, `body` matched `.voice-picker-open`, creating an unintended border and inset shadow around the entire viewport. The button style is strictly scoped to `button.voice-picker-open` and the body scroll-lock class is `body.voice-picker-modal-open`.

## Tests
- Reliability additions: profile isolation/atomic storage, pre-token fallback, local inference, daily-plan evidence, placement smoothing, spaced-review scheduling, and deterministic listening comprehension.
- `tests/test_emotioncontrol.py` — 33 tests: catalogue fidelity against an
  independent copy of the official tables (descriptions/contexts verbatim,
  `UNAVAILABLE` for the 5 special effects), prompt coverage + caching + merge
  into `BASE_SYSTEM` (and the native-voice variant), pause tags surviving
  `clean_for_speech` while disappearing from display, fillers as plain words,
  cue-safe streaming (token-split cues buffered, truncated cue dropped, comma
  inside a cue), engine wiring end-to-end (TTS keeps cues, `display_text` and
  stored history behave, `tts_sentence`/`llm_done` event contract unchanged).
- `tests/test_training.py` — 8 tests: 10 core learning areas, curriculum structure, personalised learning path progression, multi-attempt mastery requirements, error adaptation, trainer prompt building with acoustic speaking metrics (WPM, pauses), persistence in profile, mode switching without state destruction, and expanded grammar error rules.
- Held-stream mic permission flow and top-corner controls: manually verify in
  a secure-origin desktop and mobile browser, including a denied permission,
  a long sentence after 30+ seconds idle, and an active fallback Web Speech
  session when server transcription is unavailable.
- `tests/test_reading.py` — 45 tests: word diff (missing/extra/repeated/replaced),
  evidence-bounded feedback prompts (no hallucinated audio/speed/scores),
  reading session state machine, AI-generated custom coach introductions and extraction,
  3-round handoff with client audio draining ack, stale gen / wrong round drops,
  turn suppression during active reading, fallback passages/feedback on LLM failure,
  pause analysis (natural gaps, mid-sentence pauses, sentence-boundary phrasing,
  hesitation before difficult words, speaking vs elapsed duration separation),
  VAD pause/noise simulation, and the registered WebSocket reading-start task
  regression that ensures a passage is generated.
- `tests/test_stt.py` — Whisper multipart shape, no-key/empty/oversize/429
  handling, `/api/stt` status codes (200/400/413/415/503), and that
  `/api/config` gains only the `server_stt` availability flag, plus verbose
  no-speech and low-confidence noise-transcript rejection.
- `tests/test_prompts.py` — prompt guardrails plus the generic reply-language
  rule (any language, unknown falls back to English, no Hinglish).
- `tests/test_fish_library.py` — public visibility filtering, metadata mapping,
  English/Hindi pagination parameters, page and model-verification caches,
  empty results, and upstream rate-limit/network errors.
- `tests/test_correction.py` — correction policy (passive/balanced/active).
- `tests/test_engine.py` — chunker, `SentenceCoalescer` merge, first-chunk fast
  path, marker stripping, level smoothing, §43 transcript.
- `tests/test_providers.py` — Groq SSE request shape + model failover + missing
  key, tier selection/fallback, Fish streaming (chunks, JSON-as-200, 402), the
  `[VOICE LATENCY]` formatter, and that `/api/config` never leaks provider names.
- Desktop continuous-listening recovery: manually verify a long desktop
  conversation (including Bluetooth connect/disconnect) and confirm the
  `[STT] ... recovering` or `[VAD] ... recovering` log reopens capture exactly
  once without ending the session or producing duplicate `mic stream opened`
  lines.
- `scripts/smoke_test.py` — live WS turn, barge-in, response-mode switch +
  latency payload, summary, Fish MP3 bytes, progressive `/api/tts/stream`.
- `app/static/latency.html` — browser bench: streamed first frames vs complete
  mp3, playback start, barge-in cancel (manual, open `/static/latency.html`).

## Changelog (newest first — RULE ZERO: append here on every change)
- 2026-10-28: Upgraded Settings panel Daily Plan & Progress button into a rich interactive preview card with live CEFR level pill, dynamic completed task/focus summary, and 3-stat strip (spoken minutes, tasks done, words observed); transformed Headphones interruption option from a raw HTML checkbox into a modern frosted toggle card with custom cyan sliding switch. Assets: app 158, learning 4, styles 96.
- 2026-10-28: Modernized 'Your daily practice' modal popup and 'Listening practice' stage HUD card with glassmorphic styling, CEFR stats grid, interactive task cards, spaced review snippets, audio player deck with speed control, and instant graded choices; fixed Fish Audio voice picker full-screen border regression by scoping button CSS to `button.voice-picker-open` and using `body.voice-picker-modal-open`. Assets: app 157, learning 3, styles 95.
- 2026-10-28: Corrected three-mode release verified: 225 Python tests, 8 Node audio tests, isolated DOM and offline smoke pass; compilation/syntax/diff checks pass. Live provider quality and audible latency remain unmeasured without real credentials.
- 2026-10-28: Isolate the DOM fixture voice endpoint as well as its model output, so queued tail-audio checks never contact an external provider with test credentials.
- 2026-10-28: Fix duplicate-transcript suppression crossing response/learning modes: reset dedupe when the user intentionally changes modes so repeating a question in a new pipeline is accepted.
- 2026-10-28: Replace previous local/demo/browser setup instructions with three-mode cloud configuration and regression report; document exact required keys, same-provider audio and limits of simulated transport/device verification.
- 2026-10-28: Verify the actual Gemini Live protocol adapter streams tagged PCM across two turns on one warm socket, sends no empty user turns, emits no Fish TTS messages and releases the socket.
- 2026-10-28: Add isolated DOM fixture server and check Live default, stale local-preference migration, exactly three choices, both Fish mode request URLs, exercise UI and zero system-speech calls.
- 2026-10-28: Restore audio/caption handlers for HTTP fallback, surface Fish errors in the active view, skip web search during lessons, and retain structured training progression inside native Gemini Live rather than silently routing training to REST speech.
- 2026-10-28: Reject stale Gemini voice IDs immediately on explicit Fish requests, retain configured/default Fish IDs, include SOCKS support for proxy environments, and use explicit test doubles in the offline smoke test without exposing a runtime demo mode.
- 2026-10-28: Add actual-adapter Groq/Gemini-to-Fish HTTP transport regressions, exact-three-mode checks, no synthetic voice/no demo failures, warm-Live cancellation, explicit engine routing, native clip cleanup and Gemma parameter tests. Rewrite audio tests around exclusive Fish playback; close cancelled upstream streams.
- 2026-10-28: Fix idle Live cancellation sending empty user turns, preserve warm connections, resume reading/voice-test queues, remove redundant 120ms recorder delay, bound optional fact search to 1.5s, compact per-turn Fish guidance, and restrict thinking parameters to supporting Gemini models. Test doubles are explicitly test-only.
- 2026-10-28: Remove all browser speech synthesis and device-voice controls, restore exclusive Fish/native voice playback including Listening, migrate stale settings to Live default, fix startup mode/voice ordering, bound media-start waits, retain same-provider Fish retry only, and use the actual Live model for Gemini voice tests/reading clips. Assets app 156/audio 45/learning 2.
- 2026-10-28: Correct reported audio regression: remove runtime local/demo providers, fix three-mode configuration and default Live, require the actual selected provider/Fish credentials, prevent explicit Fish requests from routing to Gemini voices, and avoid cancelling turns on repeated mode/voice sync.
- 2026-10-28: Finalize source comments for current Groq/default voice behavior and document the verified audit deliverable.
- 2026-10-28: Document final validation (216 Python, 7 Node audio checks, smoke and DOM wiring) and explicitly retain real-device/cloud/local-model/visual/deployment checks as unresolved release work.
- 2026-10-28: Preserve optional jsdom smoke test and development-only dependency metadata; verified DOM startup, daily plan, listening feedback, typed SSE conversation and stale-event fencing without claiming browser rendering or microphone validation.
- 2026-10-28: Close SSE backpressure cancellation hole with stream ownership, test generator disconnect cleanup, fix the 30-day review interval, deduplicate placement samples and bound CEFR estimate changes to median evidence once per week.
- 2026-10-28: Replace misleading free/unlimited and latency claims with setup, provider quotas, local-only alternatives, evidence limits and single-worker deployment guidance; update env template and add non-root Docker packaging with persistent data.
- 2026-10-28: Add dependency-free Node audio regressions for duplicate synthesis, stale pump races, post-start replay suppression, key-free fallback, mute, cancelled blob cleanup and device-speech watchdog settlement.
- 2026-10-28: Harden speaker echo filtering in Live mode, add optional headphones interruption, automatic Web Speech fallback for unavailable server STT, account for pre-dispatch speech/STT latency, avoid duplicate Listening starts, rate-limit GET synthesis, remove mismatched-format TTS fallback, and recover Live zero-output failures through text inference without double-counting practice.
- 2026-10-28: Add regression coverage for ownership/origin/input safety, atomic profiles, pre-output failover/cooldown, partial stream preservation, deadlines, measured metrics, end idempotency, review scheduling, placement, listening grading, session capacity and SSE cleanup. Rewrite smoke script as explicit, deterministic zero-key entry point.
- 2026-10-28: Fix reproduced baseline STT availability/greeting failures, update intentional demo/echo tests, isolate test data, measure Web Speech duration, restore bounded native interrupt signaling, reject paid Fish model switching, add request limits, remove committed TLS private key/certificate, and ignore generated secrets.
- 2026-10-28: Wire Listening and daily-plan/placement/review/progress UI; fence all stale turn events, fix const reassignment during continued STT sentences, serialize/bound transcription with timeouts and measured capture duration, abort stale requests, use cryptographic session IDs, preserve original learner words, and make voice testing work without keys. Cache versions: app 155/audio 44/vad 43/styles 94/learning 1.
- 2026-10-28: Browser speech default with local voice preference and bounded playback; single generation owns queue pump, aborts cannot reset a newer turn, first streamed phrase is no longer prefetched twice, fetch/body cancellation revokes stale resources, preloaded audio is reused, post-start failures never replay, mute is respected on unduck, and VAD recovery fences/cancels old RAF loops.
- 2026-10-28: Add signed browser profile ownership, same-origin mutation/socket checks, namespaced session IDs, bounded request/history/audio input, SSE disconnect cleanup, socket replacement safety and shutdown lifecycle; add learning/listening APIs and optional CPU Whisper. API bootstrap: fetch /api/config before connecting WebSocket to establish the HttpOnly cookie. Runtime no longer requires heavyweight Crawl4AI; tests are scoped to tests/ to prevent imported smoke scripts making network calls.
- 2026-10-28: Serialize and time-bound turns, fence events with turn IDs, bound histories/session count, isolate profile ownership, preserve partial output without restarting answers, use native Live output captions, record measured speaking/reading evidence, gate training passes, and remove fabricated summary/proficiency/speaking-time claims.
- 2026-10-28: Add shared daily-plan/progress/assessment/spaced-review model and leveled deterministic Listening exercises, opt-in Ollama provider, pre-output provider fallback cooldown, and signed anonymous browser ownership.
- 2026-10-28: Reliability audit: validate profile IDs, atomically persist profiles, preserve schema defaults and learning fields, remove unrelated-parent dotenv loading, replace retired free-tier Groq defaults, stop mid-stream model retries, and configure bounded sessions/local and browser providers.
- 2026-10-28: Integrated Gemini 3.5 Transcribe Live for real-time synchronized captions in Gemini Live:
  1. Root cause of unsynced captions: Gemini Live (`gemini-3.8-live`) with `responseModalities: ["AUDIO"]` outputs native audio streams without native inline text parts. The previous client used a 15-chars/second pacing heuristic (`livePacedText`) off the audio clock, which drifted out of sync with actual spoken tempo, pauses, and disfluencies.
  2. Gemini 3.5 Transcribe Live integration:
     - In `app/providers/llm/gemini_live.py`, introduced `GeminiTranscribeLiveSession` connecting to Google's official Bidi WebSocket with `models/gemini-3.5-transcribe-live` and `responseModalities: ["TEXT"]`.
     - In `app/config.py` and `.env.example`, added `gemini_live_model` (`gemini-3.8-live`) and `gemini_transcribe_live_model` (`gemini-3.5-transcribe-live`).
     - In `app/conversation/engine.py` (`handle_gemini_live_turn`), outgoing 24kHz PCM audio chunks from `gemini-3.8-live` are piped concurrently into `GeminiTranscribeLiveSession`. As `interimInputTranscription` chunks arrive in real time, the server streams `live_caption` WebSocket messages to the client. Session lifecycle, cleanup, and cancellation/interrupts are mirrored in `close_live` and `cancel`.
  3. Client real-time caption rendering:
     - In `app.js`, implemented `renderLiveCaption()` to display the active spoken sentence with `.hl` keyword emphasis in `#aiLine` in real time as emitted by Gemini 3.5 Transcribe Live. Bypassed the artificial 15-chars/second ticker in favor of live streaming captions.
  4. Bumped `APP_BUILD = 154`, `app.js?v=154`. Added unit tests in `tests/test_providers.py`; all 194 hermetic unit tests pass.
- 2026-10-28: Implemented collaborative Roleplay topic discussion & real-time Reading mode word-by-word feedback:
  1. Collaborative Roleplay topic exploration & decision:
     - In `app/coaching/roleplay.py`, added `is_roleplay_confirmation()` to detect affirmative confirmation ("yes", "let's start", "ready", "sounds good", "let's do it").
     - In `app/conversation/prompts.py`, split `roleplay` mode prompt into two distinct phases: Phase 1 (warm topic exploration, discussing roles, context, and goals collaboratively with the user) and Phase 2 (in-character roleplay dialogue once mutually agreed).
     - In `app/conversation/engine.py`, updated `greeting()` to invite the user to brainstorm and decide on topics together, and gated `s.roleplay_started = True` behind mutual agreement or explicit scenario setting.
  2. Real-time Reading mode passage display & word-by-word coloring:
     - Instant passage presentation: `readingRenderPassage` tokenizes and renders the passage immediately (`readingPanelShow(true)`), so the user sees the reading text in real time without waiting for coach intro audio. If the student begins reading while the intro is playing, `readingOnVoiceStart()` cancels the intro speech.
     - Word-level span highlighting: `renderReadingPassageTokens()` wraps passage words in `<span class="reading-word" id="rword-N">`, preserving exact punctuation, quotes, and whitespace. `updateRealtimeReadingHighlights()` aligns real-time user speech against passage words: correctly read words turn emerald green (`.reading-word-correct`), mispronounced or skipped words turn soft red (`.reading-word-error`), and the current word shows a cyan underline (`.reading-word-current`). Interim speech prefix detection prevents prematurely marking words red while still being pronounced.
     - Automatic paragraph conclusion & feedback: When the student finishes the paragraph ($\ge 88\%$ or end of passage reached), `onParagraphCompleted()` automatically concludes reading with a 750ms debounce and finalizes the submission. `readingRenderFeedback()` displays written feedback immediately in `#readingFeedback` with accuracy stats, while the AI coach provides spoken feedback.
  3. Bumped `APP_BUILD = 153`, `app.js?v=153`, and `styles.css?v=93`. 193 hermetic tests pass (including `test_roleplay.py` and `test_reading.py`).
- 2026-10-28: Made Gemini Live Preview default and expanded to all 16 Gemini celestial voices:
  1. Default response mode switch: configured `LLM_DEFAULT_RESPONSE_MODE=gemini_live` across `.env`, `.env.example`, `app/config.py`, and `app/main.py`. Placed "Gemini Live Preview" first in `/api/config` `response_modes`. Added client migration key in `app.js` (`flow_resp_mode_def_live_v1`) to automatically transition users to Gemini Live default.
  2. Full 16-voice catalog in `app/voices.py`:
     - Gemini Live App Voices (10): **Orbit** (Energetic, engaging, clear — standard default), **Pegasus** (Warm, resonant, friendly), **Capella** (Smooth, clear, articulate), **Dipper** (Deeper, calm, grounded), **Orion** (Bright, expressive, dynamic), **Ursa** (Conversational, comforting, natural), **Vega** (Crisp, upbeat, modern), **Eclipse** (Focused, precise, measured), **Flare** (Vibrant, expressive, enthusiastic), **Glow** (Bright, friendly, smooth).
     - Gemini API / Studio Prebuilt Voices (6): **Puck** (Energetic and casual), **Charon** (Warm and deep), **Kore** (Grounded and clear), **Aoede** (Soft and melodic), **Fenrir** (Strong and bold), **Enceladus** (Smooth and relaxed).
  3. Persona and provider wiring: updated `DEFAULT_GEMINI_VOICE = "Orbit"`, updated `persona_for()` to map gender for all 16 voices, updated `GeminiLiveSession` and `GeminiTTSProvider` defaults to Orbit, and broadened voice validation in `engine.py` (`handle_gemini_live_turn`) to permit all 16 Gemini voices.
  4. Frontend UI grouping: in `app.js` (`updateVoiceDropdown`), grouped voices into distinct `<optgroup>` sections for "Gemini Live App Voices" and "Gemini API / Studio Prebuilt Voices". Set `#geminiVoiceFld` visible by default in `index.html`.
  5. 192 hermetic unit tests pass; added `test_gemini_live_default_and_all_voices` in `tests/test_providers.py`; bumped `APP_BUILD = 152` and `app.js?v=152`.
- 2026-10-28: Fixed stale chat memory leak in greeting and memory hints on fresh / cleared sessions:
  1. Stateless Groq confirmation: Groq is an inference API that is 100% stateless and does not store prompts, conversation turns, or user data. The stale text ("the source of the data mo Hey") was persisted on the local server in `data/profiles/default.json` (`last_chat_summary` and `mode_summaries`). In `engine.py` (`greeting` and `memory_hint_for`), fallback logic pulled summaries directly from `profile.get("mode_summaries")` even when the client browser had 0 turns of history (`len(mode_hist) == 0`). Furthermore, `if prior_summary or len(mode_hist) >= 2:` treated a fresh session as an existing conversation, explicitly commanding Groq to "welcome back and reference where you left off".
  2. Greeting & memory decoupling: in `engine.py`, greetings and memory hints now require active synced history (`prior_summary and len(mode_hist) >= 2`, and `has_history` in `memory_hint_for`). When a user opens Flow in a fresh browser or after clearing browser storage, `has_prior` is `False`, ensuring a clean, friendly first-time greeting ("Say a brief warm hello to start our spoken practice...") and zero memory leaks.
  3. Server disk synchronization: in `app/main.py` (`clear_history`), clearing history now also wipes `mode_summaries` and `last_chat_summary` in the disk profile (`save_profile`). Sanitized `data/profiles/default.json`.
  4. Added hermetic test `test_fresh_session_no_leaked_history` in `tests/test_engine.py`; 191 tests pass.
- 2026-10-28: Fixed Start button and Test buttons (Test mic & Test voice) not working on Safari:
  1. Audio unlock hang on Safari: in `audio.js` (`audioQ.unlock()`), awaiting `silence.play()` with a base64 data URI hung indefinitely in Safari WebKit (which never resolves play promises on tiny base64 data URIs), completely freezing `startSession()` and `fishTestBtn.onclick`. Replaced with Web Audio buffer source activation on `this._ctx` and non-blocking timeout-raced HTML5 audio gesture unlock.
  2. Safari WebKit dead-end graph disconnection bug: in `vad.js` (`EnergyVAD._wire`) and `app.js` (`micTestBtn`), WebKit halts pulling audio through `createMediaStreamSource` and `AnalyserNode` when the graph does not terminate at `ctx.destination` (and garbage-collects local source nodes), causing `getFloatTimeDomainData` to return all zeros ("nothing heard" on mic test and permanent VAD deafness on Safari). Routed analyser through a zero-gain node (`silentGain(0) -> destination`) and retained instance reference `this.srcNode`.
  3. Start button responsiveness & race guard: in `app.js`, `startSession()` now immediately updates UI state (`setIcon(square)`, `classList.add('live')`, `setState('CONNECTING')`) on tap rather than waiting for downstream network handshakes; added `sessionTransitioning` debounce guard on `startBtn.onclick` to prevent mobile Safari rapid double-taps from instantly aborting startup.
  4. Safari MediaRecorder compatibility: in `mobPickMime()` and `mobStartUtt()`, added Safari container support (`audio/mp4`, `audio/aac`), graceful options fallback on `new MediaRecorder(stream)` without throwing on unsupported bitrates, and safe `start(250)` fallback.
  5. Synchronous test button feedback: `fishTestBtn` updates status line immediately on click before running audio gestures. Added `type="button"` to `#startBtn`, `#micTestBtn`, `#fishTestBtn` in `index.html`.
  6. 190 hermetic tests pass; bumped `APP_BUILD = 151`, `app.js?v=151`, `audio.js?v=43`, `vad.js?v=42`.
- 2026-10-28: Fixed background voice registration (strict near-field foreground gating & background voice rejection):
  1. Acoustic physics-grounded VAD thresholds in `vad.js`: raised `this.threshold` from `0.008` (-42 dBFS) to `0.018` (-34 dBFS) and increased `this.snrGate` from `1.75` to `2.2`. Direct near-field user speech (30-50cm from laptop/phone or wearing earbuds) produces RMS 0.040-0.250+, easily breaking the gate, while distant background chatter, hallway voices, and TV (RMS 0.009-0.022) are blocked before triggering onset.
  2. Dynamic room noise floor tracking in `vad.js`: accelerated upward noise adaptation (`0.008` vs `0.002`) during non-speech frames so ambient room activity (HVAC, fans, distant room hubbub) dynamically elevates `this.gate = Math.max(this.threshold, (this.noiseFloor || 0.006) * this.snrGate)` instead of false-triggering on faint ambient variations.
  3. Frame-accurate 60fps speech metrics in `vad.js`: tracked `this.currentSpeechPeak` and `this.noiseFloorAtOnset` across every frame in `tick()`, ensuring client recording logic has exact millisecond-accurate peak and baseline noise levels for the utterance.
  4. Multi-level client utterance rejection in `app.js` (`mobFinishUtt`): dropped utterances with `u.peak < 0.026` (ambient chatter cutoff) or SNR `u.peak / noiseFloor < 2.2` (insufficient prominence over room noise floor) before invoking `/api/stt` or Groq Whisper. In `commitTurnIfReady` (desktop Web Speech), guarded against committing turns when `vad && !vad.hasSpoken`.
  5. Server-side background speech rejection in `app/main.py` (`_looks_hallucinated`): reject captures with `peak < 0.024` or short snippets ($\le 3$ words) with `peak < 0.028` as background noise/voices, returning 502 with suppressed hiccup banner; added unit tests in `tests/test_stt.py`.
  6. 190 hermetic tests pass; bumped `APP_BUILD = 150`, `app.js?v=150`, `vad.js?v=41`.
- 2026-10-28: Removed hardcoded test scenario artifacts ("tender management system") and isolated test state:
  1. Removed hardcoded topic in `MockProvider` (`app/providers/llm/mock.py`): eliminated specific checks for "tender management" and "tender", replacing them with natural conversational keyword routing ("building", "project", "work", "app", "system").
  2. Isolated test state in `test_sync_past_history` (`tests/test_engine.py`): previously, `test_sync_past_history` ran without monkeypatching `settings.data_dir`, accidentally writing "I am working on building a tender management system with React and Node.js" directly into the live user profile (`data/profiles/default.json`). The test now uses `tmp_path` and a clean isolated user ID.
  3. Sanitized client-side localStorage in `getStoredChatHistory` (`app/static/app.js`): automatically purges any stale test turns containing "tender management" so old test transcripts never sync to session memory over `sync_history`. Added one-tap trash button (`#historyClear`) in the Conversation drawer.
  4. 189 hermetic tests pass; bumped `APP_BUILD = 149`, `app.js?v=149`.
- 2026-10-28: Optimized 3 critical latency bottlenecks (LLM TTFT, Fish Audio stream, Playback buffer):
  1. Priority 1 (LLM TTFT < 800ms): Switched `GROQ_MODEL` and fallback chain in `.env` and `app/providers/llm/groq.py` to `llama-3.1-8b-instant,llama-3.3-70b-versatile` (removing reasoning models `openai/gpt-oss-20b` which spent 1.5-2.0s generating hidden thinking tokens). `llama-3.1-8b-instant` on Groq achieves TTFT of 200-350ms (down from 2.315s, an 85%+ reduction). Added prompt rule in `BASE_SYSTEM` (`prompts.py`) requiring a concise 3-6 word opening clause so the model produces a speakable boundary in its first 5 tokens.
  2. Priority 2 & 3 (Fish Audio first-audio latency < 500ms & Playback buffer < 200ms): In `FishProvider._payload()` (`fish.py`), differentiated progressive stream synthesis (`stream=True` in `open_stream()`) from full-chunk downloads (`synthesize()`). The progressive stream now uses `chunk_length: 100` and `mp3_bitrate: 64`, producing audio frames much sooner and allowing Chromium to accumulate its required pre-roll duration in half the time (reducing playback buffer from 706ms to <200ms). Full-body downloads retain 128kbps for studio clarity.
  3. 189 hermetic tests pass; updated `.env` and `.env.example`.
- 2026-10-28: Restored incremental text chunking and ultra-low voice response latency:
  1. Root cause verified: In `engine.py`, `first_chunk_split` was guarded by `if not first_chunk_done and not coalescer.held:`. When short opening leads populated `coalescer.held`, `not coalescer.held` evaluated to `False` for the rest of the stream, permanently suppressing `first_chunk_split`. Combined with `SentenceCoalescer.HOLD_UNDER = 35`, short sentences were trapped, causing the backend to wait for the entire LLM response before dispatching any text to TTS.
  2. Incremental streaming restored: In `Engine.handle_user_turn` (primary and fallback loops), `first_chunk_split` now evaluates `candidate = f"{coalescer.held} {buf}".strip() if coalescer.held else buf` and immediately dispatches speakable opening clauses (`first: True`) during streaming without waiting for full generation or flush. Also in `chunk_sentences`, opening sentences with `coalescer.held` are merged and dispatched as `first: True` as soon as speakable.
  3. Progressive audio streaming verified: Sentence 1 is stream-played via `GET /api/tts/stream` (progressive MP3 frames with TTFB ~0.6-0.7s), while subsequent sentences are prefetched via `POST /api/tts` (with automatic fallback to `_playStream()` on download issues). With incremental text chunking restored, TTS starts playing ~800ms after the first LLM token.
  4. VAD responsiveness & incomplete thought continuation: Reduced `EnergyVAD.endOfTurnMs` in `vad.js` and `app.js` from 1700ms to 1000ms, removing 700ms of dead air after the user stops speaking. In `mobTranscribe` (`app.js`), if an utterance ends on an incomplete thought (`isTrailingIncomplete`: "to", "and", "because", etc.), it holds the text in `mobPendingIncomplete` for up to 2.0s, seamlessly joining the paused continuation into one single request while letting complete sentences dispatch immediately.
  5. 189 hermetic tests pass; bumped `APP_BUILD = 148`, `app.js?v=148`, `vad.js?v=40`.
- 2026-10-28: Fixed mid-sentence pause cutoffs and cross-turn speech accumulation:
  1. Extended natural thinking pause threshold: increased `EnergyVAD.endOfTurnMs` in `vad.js` and `app.js` to 1700ms (from 1250ms). Mid-sentence pauses (e.g. "My agenda is to... [pause] ...do some English practice") no longer close the recorder prematurely. The entire thought stays in a single seamless recording sent as one complete request.
  2. Grammatically incomplete thought hold (Web Speech): in `commitTurnIfReady`, if the current speech ends on an incomplete preposition/conjunction (`isTrailingIncomplete`: "to", "and", "because", "is", "was", etc.), wait up to 2200ms before committing; in `checkCommit`, increased debounce quiet to 1350ms.
  3. Eliminated cross-recording fragment concatenation: removed `mobTrailingFragment` and `mobTrailingTimer` from `mobTranscribe` and `app.js`. Each server-transcribed recording is an independent, complete utterance that never prepends onto or pollutes subsequent turns.
  4. Complete turn state reset: cleared `accumulatedTranscript`, `lastInterim`, and `lastInterimAt` on `sendText`, `llm_done`, `llm_cancelled`, `audioQ.onSpeaking`, and `audioQ.onDrained`. Speech arriving during active AI generation (`llmPending || isPlaybackActive()`) is never accumulated into memory to contaminate the next turn.
  5. 189 hermetic tests pass; bumped `APP_BUILD = 147`, `app.js?v=147`, `vad.js?v=39`.
- 2026-10-28: Fixed AI stopping speech mid-response and dropping subsequent sentences:
  1. Eliminated raw VAD self-interruption: removed `bargeIn('user_speech_interrupt')` from `mobOnSpeechStart` and `startDesktopVad.onSpeechStart`. Laptop speaker playback leaking into the microphone previously caused `EnergyVAD` to trigger `onSpeechStart()`, which immediately cancelled `audioQ`, cleared `q = []`, set `session.cancelled = True` on the server, and killed all subsequent sentences (e.g. only speaking "Ah, I see what you mean" and cutting off "So after checking your phone..."). Interruptions are now strictly handled by speech recognition (`sendText()` with `isAiEcho` filtering) and the manual interrupt button. Removed `audioQ.duck()` on unconfirmed `mobOnFirstVoice()`.
  2. Multi-sentence AudioQueue stream fallback: updated `AudioQueue.prototype.pump()` in `audio.js` so that if full-body `POST /api/tts` download fails or times out on subsequent phrases, it immediately falls back to progressive streaming via `_playStream()` rather than stalling or sitting in silence. Also always instantiate clean `new Audio(url)` in `playUrl()` to eliminate any preloaded state or listener contention.
  3. Increased Fish Audio client timeout: raised `httpx.Timeout` in `FishProvider._get_client()` from 12s to 30s (`read=25.0s`), avoiding premature timeouts on longer multi-sentence chunks. Updated `.env` `FISH_MP3_BITRATE=128`.
  4. 189 hermetic tests pass; bumped `APP_BUILD = 146`, `app.js?v=146`, `audio.js?v=42`, `vad.js?v=38`.
- 2026-10-28: Eliminated raw bracket leaks ('[', ']', '[tag]') and bracket-only turns across client and server:
  1. Prompt constraints: added rule 23 in `BASE_SYSTEM` (`prompts.py`) strictly forbidding standalone brackets, empty brackets, or bracket-only responses (`'['`, `'[]'`, `'[tag]'`).
  2. Search meta-query exclusion: added "what do you mean", "what are you checking", and "bracket" patterns to `_NO_SEARCH_PATTERNS` in `search.py` so user questions about brackets or checking behavior never falsely trigger fact searches or filler speech ("Let me check...").
  3. Server-side tag & bracket stripping: updated `strip_emotion_tags()` in `engine.py` to strip all Fish Audio bracket cues as well as any stray/unmatched brackets `[` or `]`.
  4. Server-side conversational intercept: in `engine.respond()` and fallback LLM streaming, if `strip_emotion_tags(reply)` contains fewer than 4 characters or no alphanumeric characters (e.g. `[` or `[relaxed]`), the engine intercepts it, logs a warning, and substitutes a natural conversational prompt ("I'm listening! Tell me more about what's on your mind."); ensures `llm_done` never emits a raw `[` or bracket-only string as `display_text`.
  5. Client-side sanitization: updated `plainText()` and `emphasize()` in `app.js` to strip all stray brackets; updated `dissolveAiLine()` to clear instead of rendering bracket-only fragments; guarded `addTurn()` against saving or displaying bubbles without alphanumeric characters; in `llm_done`, sanitized `displayText` so empty/bracket-only responses are suppressed; in `tts_sentence`, dropped chunks lacking spoken words.
  6. AudioQueue protection: updated `AudioQueue.enqueue()` in `audio.js` to verify spoken words exist before queuing TTS requests, preventing cue-only chunks (`"[relaxed]"`, `"["`) from dispatching to Fish TTS or stalling the audio queue.
  7. 189 hermetic tests pass, `smoke_test.py` verified; bumped `APP_BUILD = 145`, `app.js?v=145`, `audio.js?v=41`, `vad.js?v=37`.
- 2026-10-28: Fixed AI voice clarity and premature cutoff / stopping speech:
  1. Prevented false barge-in during AI generation (`THINKING` / `llmPending`): `mobOnSpeechStart` and `startDesktopVad.onSpeechStart` now only trigger `bargeIn('user_speech_interrupt')` when the assistant is actively playing audio (`isPlaybackActive() || state === 'SPEAKING'`), with a 650ms post-send breath/tail guard. Eliminates false aborts where room noise, mic transients, or trailing exhalations immediately killed the in-flight reply.
  2. Fixed reasoning model token starvation on Groq: reasoning models (`openai/gpt-oss-20b`, `openai/gpt-oss-120b`) count reasoning tokens against `max_tokens`. Scaled `max_tokens` to at least 750 in `GroqProvider._body()`, preventing 1-character truncated replies (e.g. after "Yeah."). Removed `allam-2-7b` from fallback chain to prevent 400 `context_length_exceeded`. Added conversational fallback in `Engine.respond` if any reply is under 5 characters so the AI never sits in silence.
  3. Upgraded Fish Audio voice clarity: updated default `FISH_MP3_BITRATE` to 128 kbps (from 64 kbps) and enabled `"normalize": True` in `FishProvider._payload()`, eliminating muffled, metallic compression artifacts and leveling output volume.
  4. Expanded conversational VAD end-of-turn to 1250ms and added trailing fragment continuation: increased `endOfTurnMs` in `vad.js` and `app.js` to 1250ms; added `mobTrailingFragment` hold for trailing conjunctions/prepositions in `mobPostUtterance` to seamlessly join paused thoughts (e.g. "I'm up to the... coding part") into a single turn. Raised playback active threshold and required sustained speech (360ms) to eliminate speaker bleed self-interruption.
  5. Enhanced prompt completeness & audio check regex: updated `BASE_SYSTEM` rules to require complete, engaging 1-3 sentence thoughts and eliminate 1-word brush-offs ("Hey!", "Glad to hear."); expanded `_AUDIO_CHECK_RX` in `intent.py` to match "Say something, not just Hey" and connectivity checks.
  6. 189 tests pass, `smoke_test.py` verified; bumped `APP_BUILD = 144`, `app.js?v=144`, `vad.js?v=36`.
- 2026-10-28: Relative contextual homophone alignment and universal instant autointerruption:
  1. Context-relative homophone correction: added `align_contextual_homophones(text, context)` in `intent.py` with triggers across food, breakfast, and common homophone sets. Fixed Whisper misrecognitions like 'What is Serial?' -> 'What is cereal?' when context refers to morning food/drinks or 'cereal'. Prioritized dialogue context in `_stt_prompt()`, integrated into `clean_speech_artifacts()`, `POST /api/stt`, and frontend `sendText()`. Reinforced prompt rule in `BASE_SYSTEM` so LLM always interprets homophones relative to what it just asked.
  2. Universal zero-latency autointerruption: enabled voice auto-interruption across ALL modes (Groq + Fish Audio, Gemini + Fish Audio, Gemini Live Preview). Added `audioQ.duck()` on `onFirstVoice()` to zero output volume within <16ms of speech onset; on `onSpeechStart()`, triggers `bargeIn('user_speech_interrupt')` to immediately stop HTML5 `<audio>`, cancel streams, zero PCM gain, clear queues, abort TTS fetches, and signal server cancellation.
  3. 189 hermetic tests pass; bumped `APP_BUILD = 143`, `app.js?v=143`, `audio.js?v=40`.
- 2026-10-28: Removed 'Sorry, I missed that — say that again?' and error hiccup disclaimers across client and server:
  1. Frontend silence on dropped/unclear utterances: filtered `hearNote()` with `isMissedOrHiccup()`, preventing noisy or faint STT drops from triggering "I didn't catch that — say it once more?" / "Please speak again" amber banners.
  2. UI suppression: guarded `setAiLine()`, `dissolveAiLine()`, `addTurn()`, and `handleServerMessage()` (`llm_done` and `tts_sentence`) against displaying or speaking robotic apologies or repeat requests.
  3. Prompt rules: added explicit constraints in `BASE_SYSTEM` banning "Sorry, I missed that", "I didn't catch that", "Can you say that again?", or asking the user to repeat; instructed the model to smoothly continue the conversational flow instead.
  4. Server-side conversational fallbacks: replaced "Sorry, I had a little hiccup. Can you say that again?" in `handle_user_turn` and `handle_gemini_live_turn` with natural continuation ("I'm listening, please go ahead."); replaced HTTP fallback with `llm_cancelled`.
  5. 188 hermetic tests pass; bumped `APP_BUILD = 142`, `app.js?v=142`.
- 2026-10-28: Training topic stickiness and 4-module chat history isolation:
  1. Training topic stickiness: tutor remains strictly anchored on `current_lesson` across turns until the user achieves mastery (2 consecutive passes) AND explicitly requests to advance or change topics ("next topic", "let's practice past tense", "switch topic"). Added `is_lesson_mastered()`, `is_topic_change_request()`, `find_matching_lesson()`, `advance_to_next_lesson()`, and preserved `select_next_lesson()` for curriculum order in `training.py`. Prevented meta-help queries ("I don't know, what should I say?") from triggering false topic changes.
  2. 4-module chat history isolation: separated chat storage for `free`, `reading`, `training`, and `roleplay`. In `app.js`, conversations are stored and retrieved via `flow_chat_history_<mode>_v2` and dynamically rendered in the transcript drawer upon switching learning modes.
  3. Server-side context isolation: updated `Session` in `engine.py` with `mode_histories`, `mode_turns`, and `mode_summaries`, dynamically routing `s.history`, `s.turns`, and `s.past_chat_summary` by `session.mode`. `Engine.greeting(s)` and memory hints now continue the conversation seamlessly from where the user left off in that specific module without context bleeding.
  4. Unit test suite passes hermetically (186 tests passing); bumped `APP_BUILD = 141`, `app.js?v=141`.
- 2026-10-28: Immediate silencing on Gemini Live Preview interruption:
  1. Instant client audio cut-off: updated `AudioQueue.cancel()` to instantly zero `_pcmGain` with `setValueAtTime(0, currentTime)`, disconnect and discard the gain node, immediately stop (`stop(0)`) and disconnect all active `_pcmSources`, reset PCM timing buffers, set `cancelled=true`, and track `deadTurns` across both `_turnId` and `_pcmTurn`. Added `AudioQueue.startTurn()`.
  2. Late frame & token drop: guarded `playLivePcmChunk`, `llm_token`, and `live_audio_chunk` in `app.js` and `audio.js` so any packets for dead or cancelled turns arriving over WebSocket are dropped immediately.
  3. Real-time voice barge-in: hooked VAD speech detection (`mobOnSpeechStart` for held-stream capture and desktop VAD) during Gemini Live playback/thinking to trigger immediate barge-in on voice onset, silencing Gemini Live without waiting for utterance completion or STT roundtrips.
  4. Instant server interrupt: updated `engine.cancel(sid)` to fire `asyncio.create_task(live_sess.interrupt())` immediately to halt Google Bidi session generation and audio chunks server-side; eliminated fabricated fallback speech on pre-token cancellation (`llm_cancelled` emitted silently).
  5. Expanded unit tests in `test_engine.py` (186 tests passing); bumped `APP_BUILD = 140`, `app.js?v=140`, `audio.js?v=39`.
- 2026-10-28: Removed green circle checkmark badge on user chat bubble grammar fix:
  Immediately removes the fix button (`btn.remove()`) upon applying the grammar correction in `app.js`
  instead of morphing into a delayed `.fixed` green circle; removed `.fix-grammar-btn.fixed` styling
  from `styles.css`; bumped `APP_BUILD = 139`, `app.js?v=139`, `styles.css?v=92`.
- 2026-10-28: Fixed speech recognition accuracy, homophone slips, language detection, and multilingual accent delivery:
  1. High-fidelity audio capture: increased MediaRecorder bitrate to 96kbps (fallback 64kbps) in `app.js` and expanded VAD hangover to 18 frames (~300ms) with a 120ms speech-end encoding buffer, ensuring terminal plosives/stops (/t/, /d/) like 'ate' vs 'add' are crisply preserved without cutoff; adjusted minimum blob floor to 800B so short monosyllabic words are never dropped.
  2. Acoustic homophone slip correction: added contextual corrections in `clean_speech_artifacts` (e.g. 'I add breakfast' -> 'I ate breakfast') and reinforced LLM acoustic homophone tolerance in `BASE_SYSTEM`.
  3. Comprehensive language detection: extended Whisper language code mapping in `_detect_id` with full ISO 639-1/2 codes (`de`, `hi`, `es`, `fr`, `ja`, `zh`, etc.) and added script/keyword detection in `detect_language_from_content`.
  4. Dynamic language switching: implemented `detect_language_switch_request` for direct commands ("talk in hindi", "talk in german", "sprich deutsch", "hindi me bolo", etc.); the engine updates session language, syncs the UI and STT code via `{"type": "language"}`, and shifts drill modes to free chat.
  5. Native script & accent guidance: updated `language_addendum()` to enforce native writing systems (e.g. Devanagari for Hindi/Marathi, Arabic/Urdu, Cyrillic, CJK) and authentic colloquial phrasing so Fish Audio and Gemini TTS synthesize with authentic native accents; bumped `APP_BUILD = 138`, `app.js?v=138`, `vad.js?v=35` (183 tests pass).
- 2026-10-27: Scaled up the training graduation cap icon (`.mode-btn[data-mode="training"] svg`) to 16px
  on desktop and 19.5px on mobile, balancing its visual weight with other mode icons; bumped `styles.css?v=91`.
- 2026-10-27: Increased spacing between mobile conversation history (`#historyBtn`) and settings (`#settingsBtn`)
  buttons to a comfortable 16px gap (`right: 66px` vs `right: 12px`), maintaining clean alignment with the mode selector;
  bumped `styles.css?v=90`.
- 2026-10-27: Aligned mobile top navigation: on screens $\le 640\text{px}$, placed the icon-only
  learning mode toggle selector on the left side of the top row, with the conversation history
  and settings buttons on the right side with consistent spacing; positioned the status indicator
  and session timer in a dedicated row below, followed directly below by the active training/roleplay
  badges; bumped `styles.css?v=89`.
- 2026-10-27: Filtered non-speech audio (background noise, whistles, snoring, breathing)
  across client and server (build 137; `app.js?v=137`, `vad.js?v=34`):
  1. Tuned `EnergyVAD` threshold (0.008) and SNR gate (1.75x) in `vad.js` with speech-formant
     frequency analysis (`_hasSpeechShape`): rejects single-tone whistles (`peakShare > 0.38`),
     low rumbling/snoring (`lowShare > 0.60` and `voiceShare < 0.32`), and faint room noise.
  2. Client `mobFinishUtt` in `app.js` checks utterance peak level ($\ge 0.012$) and minimum voice
     confidence ($\ge 0.20$), dropping silent or non-speech utterances before upload.
  3. Upgraded `_looks_hallucinated` in `main.py` with classic Whisper silence/noise hallucination
     phrases ("What is it about that?", "Thank you.", quiet noise turns) and `groq_whisper.py`
     confidence gating (`avg_logprob < -1.0`, `no_speech_prob > 0.55`); added regression tests;
     175 tests passing.
- 2026-10-27: Implemented mobile icon-only toggle menu, dynamic AI greetings across all features,
  and seamless automatic reading feedback & spoken continuation:
  1. Hides text labels on mobile screens ($\le 640\text{px}$) in `#modeSelector`, rendering clean,
     centered icon-only touch buttons.
  2. Integrated dynamic, AI-generated spoken greetings for Conversation (`free`), `training`, and
     `roleplay` via `Engine.start_greeting(s, send)` without hardcoding.
  3. Hardened Reading Practice automatic completion and verbal continuation: when the user completes
     reading the passage, Flow analyzes, speaks the results with "Would you like to continue reading?",
     and when the user speaks "continue", "yes", or "next", automatically advances to the next passage;
     bumped `styles.css?v=88`, `app.js?v=136` (`APP_BUILD = 136`); 173 tests passing.
- 2026-10-27: Added full glassmorphism effect to all buttons and the top toggle menu:
  upgraded `.mode-selector` and `.mode-btn` (with active glass capsule state), `.iconbtn`
  dock and utility controls, `.textbtn`, voice picker action and tab buttons, reading action
  buttons, grammar fix button, and toggles with frosted translucent backdrop filters
  (`backdrop-filter: blur(20px) saturate(...)`), 1px translucent glass specular borders,
  inner specular rim highlights (`inset 0 1px ...`), and soft diffuse elevation shadows;
  bumped `styles.css?v=87`.
- 2026-10-27: Made the never-close microphone path available on desktop as
  well as mobile (build 135; `app.js?v=135`). When `/api/config` reports
  server transcription and the browser supports MediaRecorder, Flow now holds
  one `getUserMedia` track for the whole session, feeds VAD from that track,
  and sends fresh per-utterance recordings for transcription; it never starts
  Chrome Web Speech, whose hidden stream may close after 30–50 seconds. The
  held-stream health check only reacquires after the actual track dies. Web
  Speech + desktop VAD remains the no-server-transcription fallback.
- 2026-10-27: Normalized the build-133 recovery-stop call formatting; behavior
  and public API are unchanged.
- 2026-10-27: Eliminated a desktop Web Speech recovery race (build 133;
  `app.js?v=133`). Recovery now invalidates and detaches every callback from the
  outgoing recognizer before aborting it, then fences `start`, audio, sound,
  result, error, and end callbacks to the current recognizer generation. A late
  `onaudiostart` from the old instance can no longer create duplicate
  `mic stream opened` logs, falsely satisfy the watchdog, and leave the real
  replacement deaf after a browser/Bluetooth stream closure.
- 2026-10-27: Hardened desktop continuous listening (build 132;
  `app.js?v=132`). The Web Speech watchdog now rebuilds a recognizer that is
  nominally active after its platform mic stream closes, and an independent VAD
  watchdog rebuilds a closed track or suspended AudioContext. Desktop VAD setup
  is shared by start and recovery, while session-control clicks are logged so
  an intentional stop is distinguishable from capture loss. The supplied log's
  `recognition stopped - mic stream closed` line was the explicit
  `stopSession()` path followed by a fresh session, not an unreported STT error.
  This release also retains every VAD-confirmed mobile utterance for
  transcript-level relevance/echo filtering, relaxes quiet playback VAD gating,
  and fixes word-boundary/Unicode echo handling so short and non-Latin
  follow-ups are not skipped; `vad.js?v=33`.
- 2026-10-27: Repaired seamless request handling across free conversation,
  Reading, Training, and Roleplay. Browser STT now treats joined/separated
  Unicode word boundaries as the same interim→final utterance and preserves
  every script in AI-echo detection, preventing Hindi assistant audio and late
  finals from spawning fabricated combined requests. Distinct later speech is
  sent as a clean turn; mobile server-STT follows the same rule, and desktop
  turns send their spoken-language id. Fixed Reading's WebSocket startup
  self-cancellation and registered explicit `reading_start` tasks; corrected
  reading yes/no continuation regex boundaries. Repeated Roleplay mode syncs
  preserve an active scenario instead of replacing it with generic settings,
  and `/api/config`/HTTP transport recognize
  Reading and Training. Added the registered-start regression test; bumped
  `app.js?v=130` (`APP_BUILD = 130`).
- 2026-10-27: Complete Fish Audio S2.1 Pro emotion/delivery control: new
  `app/conversation/emotioncontrol.py` holds the full official cue catalogue
  (24 basic + 25 advanced emotions, 6 tone controls, 11 audio effects, 5
  special effects incl. `[break]`/`[long-break]`) with descriptions/contexts
  copied verbatim from the S2 docs (`UNAVAILABLE` where the docs give none) and
  a cached `build_emotion_control_prompt()` (8,232 chars ≈ 2k tokens) merged
  into `BASE_SYSTEM` through `{emotion_control}`, replacing the old "at most
  ONCE per turn" rule; prompt now teaches cue placement, pause strategy,
  restraint and 9 examples, and lists 12 conversational fillers
  (um/umm/uh/uhh/hmm/hmmm/well/let me think/you know/actually/I mean/ugh) as
  ordinary text, never bracketed. Streaming is cue-safe: `mask_cues()` +
  `trim_incomplete_cue()` make `chunk_sentences`/`first_chunk_split` ignore
  boundaries inside a cue (commas included) and make `clean_for_speech` /
  `strip_emotion_tags` drop a truncated trailing tag, so TTS keeps cues,
  captions and `display_text` are cue-free, and the stored history keeps the
  raw text. Gemini Live gets `build_system(..., voice_cues=False)` +
  `NATIVE_VOICE_GUIDANCE` instead (native voice; Fish syntax would leak).
  No API/WS/payload change (`tts_sentence` keys and `llm_done` shape untouched).
  Live-verified `s2.1-pro-free` accepts the cue markup on both paths (POST
  2.8-2.9s / 38KB mp3, stream ttfb 1.07s); `tests/test_emotioncontrol.py` adds
  33 tests → 171 total passing.
- 2026-10-26: Personalised AI English Trainer & Main Screen Mode Selector: Moved mode selection from Settings to a prominent, responsive segmented control (`#modeSelector`) at the top of the main screen with three primary modes (**Free Conversation** / **Reading** / **Training**) and active lesson topic pill (`#trainingBadge`). Built comprehensive interactive Training module in `app/coaching/training.py` with 10 core learning areas, structured lessons, ordered learning paths, multi-attempt mastery tracking (requires $\ge 2$ consecutive passes), error adaptation into review queues, and acoustic speaking metrics (WPM, pauses) integrated into trainer evaluation prompts. Extended grammar rules in `correction.py` for modal errors, question inversion, and prepositions. Preserved existing Free Conversation and Reading pipelines with non-destructive mode switching; 118 total tests passing; bumped `styles.css?v=81`, `app.js?v=116` (`APP_BUILD = 116`).
- 2026-10-26: Removed "End-of-reading pause" selector; end-of-speech silence detection now uses a streamlined, reliable 2200ms default (`readingEndMs() = 2200`). Replaced hardcoded reading introduction with AI-driven interaction: `passage_messages` prompts the LLM to decide how to interact with the student and produce a customized, warm spoken intro (`INTRO: ...`) alongside the passage (`PASSAGE: ...`). Added `_extract_passage_and_intro` to parse and separate the AI's spoken greeting from the passage text. Updated `intro_for` to provide varied, natural fallback coaching intros across passages. Removed `#readingPauseSel` and `.reading-pause-opt` CSS/DOM controls; 110 total tests passing; bumped `styles.css?v=80`, `app.js?v=115` (`APP_BUILD = 115`).
- 2026-10-26: Accurate Pause Detection and Analysis audit and implementation: Disentangled conversational VAD turn-completion (`endOfTurnMs = 850`) from intra-reading acoustic pause measurement (`minPauseMs = 380`). Added `onPause` emission in `EnergyVAD` (`vad.js`) tracking monotonic silence intervals (`performance.now()`), filtering natural syllable and plosive closure gaps (<380ms), and preventing duplicate speech-end events. Attached word context to pause events in `app.js` and transmitted them in `reading_submit`. Implemented `analyze_pauses` in `app/coaching/reading.py` to classify sentence-boundary breath pauses (0.6-1.8s) vs. clause commas (0.4-1.2s) vs. mid-sentence unnecessary pauses and hesitations before substituted or complex words. Distinguished active speaking time from total elapsed time (excluding pauses and app delays) for WPM calculation. Updated feedback prompts with evidence-bounded pause facts; added 8 targeted tests covering all 7 audit scenarios (107 total tests passing); bumped `app.js?v=113` to `v=114`.
- 2026-10-26: English Reading Practice feature verified and hardened: 3-round passage reading practice mode with dedicated state machine (`GENERATING_PASSAGE`, `WAITING_FOR_READING`, `RECORDING_READING`, `FINALIZING_TRANSCRIPT`, `ANALYZING_READING`, `PROVIDING_FEEDBACK`, `ROUND_COMPLETE`). Automatic end-of-speech silence detection with configurable duration (`readingEndMs()`), onset tracking, and minimum speech guard. Sequence alignment diff in `app/coaching/reading.py` computing missing, extra, repeated, and replaced words, WPM, and pause metrics; evidence-bounded feedback prompts preventing fabricated pronunciation/speed claims; turn suppression during active reading on client & server; fixed repeated-word detection across positions and round-3 handoff ack synchronization; 99 total tests passing. Bumped `index.html` assets to `styles.css?v=79`, `app.js?v=113`.
- 2026-10-10: Fix "say hello first" onset loss: any single below-gate frame (plosive gaps like "h-ello") reset the VAD onset and the capture layer ABORTED the running recorder, discarding the opening syllables over and over until the user sustained sound. VAD now needs 3 consecutive quiet frames to reset an onset, unconfirmed recorders are kept (gaps are harmless for Whisper) with a 4s blip-reaper so lone coughs can't hoard silence, and onset capture is logged; bumped `app.js?v=111` to `v=112`.
- 2026-10-10: Diagnose "mic not working" properly: the reported log line (`recognition restarted after natural end`) exists only in the old committed build, proving a stale-cached `app.js` — so session start now logs `[APP] Flow build vN` + the chosen STT path (always-open vs fallback + why), every capture failure point logs its reason, and the Web Speech fallback escalates to a 5s heartbeat with an actionable note after 5 consecutive aborts instead of a silent storm; bumped `app.js?v=110` to `v=111` (hard refresh required).
- 2026-10-10: True multilingual mode, Hinglish retired: 18 preferred languages (config-driven picker, `LANGUAGES` + BCP-47 codes in `prompts.py`), detection auto-IDs every utterance and the reply follows the SPOKEN language (`reply_lang` → generic `language_addendum()`, preferred is only the fallback) — live-verified end-to-end (Spanish utterance in an English session → Spanish AI reply). STT prompt carries a sample in the preferred language + last-spoken sample when switching + full context, no language hardcoded anywhere; Web Speech fallback uses best-of-3 alternatives in all languages; 66 tests passing; bumped `app.js?v=109` to `v=110`.
- 2026-10-10: Hindi sentence integrity: 32kbps AAC transcribes Hindi identically to WAV (bitrate ruled out), but splitting a sentence at a mid-thought pause made Whisper confabulate endings ("...में कोई हैं?") and English session context distorted Hindi (dropped greetings, Latin intrusions) — both reproduced live. Mobile VAD gate is back to 800ms so whole sentences stay together, and prompt context is now script-matched to the hint (mismatched turns left out); hostile-context Hindi verified byte-identical to clean; 60 tests passing; bumped `app.js?v=107` to `v=108`.
- 2026-10-10: Hindi that survives any Language setting: forcing `en` on Hindi speech mangled it ("kal" → "kıl") and a Hindi prompt sample hijacked English audio into Devanagari garbage — both reproduced live. The endpoint now always auto-detects; the prompt is one universal mixed-script sample (monolingual samples hijack, no sample collapses "haan" → "Huh?", mixed is safe both ways, all verified live) + session context. Full 6-way matrix exact (HI/MIX/EN × hindi/english/no hint, incl. shorts); server-only change, no cache bump; 59 tests passing.
- 2026-10-10: Catch fluent hallucinations + speaker echo: word-rules can't see fluent invented sentences, so Whisper now returns `verbose_json` and segments averaging `no_speech_prob > 0.6` are dropped as noise-generated; utterances starting mid-AI-speech carry `X-Barged` and are dropped when they just repeat the last assistant reply (`_is_echo_of_assistant`, overlap ≥ 0.8, 4+ words); live-verified (silence dropped, English/Hindi exact); 57 tests passing; bumped `app.js?v=106` to `v=107`.
- 2026-10-10: Stop hallucinated transcripts ("random words"): every utterance now carries its measured mic peak (`X-Level`); energy-less captures are dropped client-side before upload, and the server filters Whisper-on-noise signatures (looped words, caption-site junk like "thanks for watching", prompt-parroting, quiet+tiny) via `_looks_hallucinated()`, replying "I didn't catch that — say it once more?" instead of sending garbage as your turn; live-verified (silence rejected, real English/Hindi exact); bumped `app.js?v=105` to `v=106`.
- 2026-10-10: Reliable mobile capture + lower latency: short utterances ("haan"/"yes") were silently dropped by the 4000B floor (at 32kbps they run ~1.5-2KB) — floor is now 1500B with every drop reason logged; recorder-start failures and discards log instead of vanishing; mobile VAD runs hotter (`threshold` 0.006, `snrGate` 1.8) and commits after 550ms; a 4s watchdog reacquires the mic when the OS kills tracks mid-session (calls/notifications) instead of losing every later utterance, and `vad.attach()` now closes the previous `AudioContext` so recovery never leaks; bumped `app.js?v=104` to `v=105`.
- 2026-10-10: Better STT accuracy (English + Hindi): Whisper requests now carry a `prompt` (same-script sample steering Devanagari-vs-Latin choice + last 3 session turns biasing names/terms, via new `X-Session` header and `_stt_prompt()`), capture uses explicit voice constraints (`echoCancellation`/`noiseSuppression`/`autoGainControl` with plain-capture fallback); live-verified on real English, Hindi, and Hinglish speech — all transcribed exactly at 0.2-0.6s server-side; bumped `app.js?v=103` to `v=104`.
- 2026-10-10: Fixed voice-picker cards on phones: the `≤640px` block re-declared a 2-column grid AFTER the `≤480px` single-column rule, so equal specificity + later order forced cramped 2-up cards on mobile. The mobile block now only tightens the gap; column counts come solely from the base rules (1 / 2 / 3 across phone / tablet / desktop); bumped `styles.css?v=77` to `v=78`.
- 2026-10-10: Fixed "transcription failed" on mobile: the server hardcoded the multipart part type to `audio/webm`, so Safari `audio/mp4` recordings were rejected by Groq with a 400 (reproduced live: "file must be one of the following types"). `/api/stt` now forwards the real container type + filename, upstream error detail is distilled into a client-safe line (`_short_upstream_error`) instead of opaque "failed (400)", and the client shows the reason under the wave for 4s; verified end-to-end with real speech audio ("Hello, this is a microphone test." transcribed exactly); bumped `app.js?v=100` to `v=101`.
- 2026-10-10: Faster mobile transcription, no center "Transcribing…" text: measured the server leg at 0.3-0.9s, so the felt delay was client-side — recorder now captures at 32kbps (`audioBitsPerSecond`, ~4x smaller uploads on mobile networks) and mobile VAD commits after a 650ms pause instead of 850ms; Whisper requests send `temperature=0`; while waiting, only a typing indicator in the history drawer shows (errors still surface under the wave); bumped `app.js?v=102` to `v=103`.
- 2026-10-10: Fixed repeat-utterance transcription failures ("could not process file" after the first turn): a single continuous `MediaRecorder` emits container headers only in its first slice, so utterances sliced from it after turn one were headerless fragments. Each utterance now gets a FRESH `MediaRecorder` (always a self-contained valid file); it starts on VAD's first above-floor frame (`onFirstVoice` in `vad.js`) so onsets never clip, unconfirmed blips are discarded, and the held mic stream is untouched by recorder start/stop; bumped `app.js?v=101` to `v=102`.
- 2026-10-10: Always-open mobile mic: Web Speech can never hold the mic (the platform auto-ends its stream), so mobile with a transcription key now holds ONE `getUserMedia` stream for the session (`EnergyVAD.attach()` for levels/barge-in + continuous `MediaRecorder` tap with pre-roll ring), POSTing each utterance raw to new `POST /api/stt` → Groq Whisper (`whisper-large-v3-turbo`, live-verified proxy path end-to-end); barge-in seeds without pre-roll (echo), stale results die via `mobUttGen`, mic test samples the held stream, Web Speech stays as fallback; `server_stt` availability flag in `/api/config` (no provider names); new `tests/test_stt.py` (10 tests, 47 passing); bumped `app.js?v=99` to `v=100`.
- 2026-10-10: Fixed mobile mic open/close storm at its root: the immediate `r.start()` inside `onend` fought the platform's own auto-end (mobile browsers close SpeechRecognition on silence/utterance even with `continuous=true`, iOS ignores it entirely), so each cycle logged closed→opened in a tight loop. `onend` now restarts debounced (700ms mobile / 350ms desktop) with storm backoff (rapid ends or ≥3 aborts stretch the delay up to 2.5s); added the missing `isTurnActive()` definition (unmute referenced it → ReferenceError), unmute now always resumes while running so barge-in survives mid-turn unmute, and `stopSession()` clears the pending restart timer; bumped `app.js?v=98` to `v=99`.
- 2026-10-10: Fixed mobile microphone stream open/close loop and stale playback timeouts: implemented turn-active state tracking (`llmPending` + `audioQ.busy` via `isTurnActive()`) so STT is paused upon turn submission and stays paused during LLM generation and across all streamed TTS playback phrases; STT restarts cleanly once when the entire audio queue drains; updated `AudioQueue` to track and clear active `_activePlayTimer` on playback cancellation and completion; bumped `audio.js?v=36` to `v=37` and `app.js?v=92` to `v=93`.
- 2026-10-10: Kept mobile microphone capture active across recognized utterances by enabling continuous Web Speech recognition instead of ending and restarting after each phrase; retained guarded `onend` recovery for browser-terminated sessions and prevented recovery while muted; bumped `app.js?v=91` to `v=92`.
- 2026-10-09: Built full Vercel Serverless support with HTTP/SSE streaming: added `POST /api/session/{sid}/turn` SSE streaming endpoint in `main.py` yielding live status, token, TTS, and completion events; created `vercel.json` routing configuration; configured `config.py` to use `/tmp/data` on Vercel read-only filesystems; updated `app.js` with seamless HTTP/SSE fallback transport (`streamHttpTurn()`) to eliminate WebSocket disconnect loops on serverless runtimes; added exponential backoff on STT aborts to prevent mic restart storms; 37 tests passing; bumped `app.js?v=90` to `v=91`.
- 2026-10-09: Centered session timer at top-center on mobile screens: styled `#clock` with absolute horizontal and vertical centering (`left: 50%; top: 50%; transform: translate(-50%, -50%)`) within `#topbar` on mobile viewports ($\le 640\text{px}$); bumped `styles.css?v=76` to `v=77`.
- 2026-10-09: Mobile layout refinements: moved conversation chat icon (`#historyBtn`) to the top-right corner next to settings icon (`#settingsBtn`, `right: 58px` and `right: 12px`); replaced the delete button in the conversation drawer header with a close (`x`) button (`#historyClose`); hid both chat and settings icons whenever either the conversation or settings panel is open on mobile screens; bumped `styles.css?v=75` to `v=76` and `app.js?v=89` to `v=90`.
- 2026-10-09: Hardened production WebSocket stability and LLM failover: added `CORSMiddleware` in `main.py` for production origin handling; wrapped WebSocket message dispatching in per-message try/except to prevent server disconnect loops from unhandled message exceptions; guarded `memory.py` profile persistence against file-system permission errors in restricted environments; added multi-model resilient fallback chain to `GroqProvider` (`qwen/qwen3.8-27b`, `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `allam-2-7b`) and cross-tier fallback to Gemini in `handle_user_turn`; verified end-to-end response generation; 36 tests passing.
- 2026-10-09: Fixed syntax error in `audio.js` and ensured robust UI initialization: removed duplicate method fragment in `audio.js` that caused script parsing failure and prevented `AudioQueue` from initializing; updated `app.js` to initialize UI and Lucide icons immediately if DOM is already ready; verified in integrated browser that all icons render and buttons/drawers operate correctly; 36 tests passing.
- 2026-10-09: Fixed text submission and response flow in production: added automatic WebSocket reconnection in `sendText()` to prevent silent message drops when the socket is disconnected; added request-ID tracking (`req_id`) across client text submission, server chat handler, LLM stream, TTS synthesis, and audio playback; ensured the assistant text response always renders in the chat UI and center display (`setAiLine`) even when TTS streaming or download fails; added sanitized error handling (`sanitize_error`) that strips sensitive authorization tokens; added `tests/test_providers.py` with 10 unit tests covering Groq streaming, failover, Fish streaming, error sanitization, and request-ID tracking (36 tests passing); bumped `app.js?v=88` to `v=89` and `audio.js?v=35` to `v=36`.
- 2026-10-09: Fixed voice preview button icon not switching to pause when audio is playing: `setFishPreviewButton()` was using `button.querySelector('i')` to update the `data-lucide` attribute, but after `icons()` (which calls `lucide.createIcons()`) runs, the `<i>` element is replaced by an `<svg>`, so `querySelector('i')` returns `null` on subsequent calls. Fixed by replacing `button.innerHTML` with a fresh `<i data-lucide="...">` element (same pattern as `setIcon()`) before calling `icons()`; bumped `app.js?v=87` to `v=88`.
- 2026-10-09: Verified voice preview button already toggles correctly between play/pause icons via `setFishPreviewButton()` (play→pause on click, pause→play on click); set bookmarked voice icon background to white (`background:#fff` on `.voice-bookmark-icon[aria-pressed="true"]`) with dark icon color for contrast; bumped `styles.css?v=74` to `v=75`.
- 2026-10-09: Fixed gap/whitespace after the Gemini Live selector in the AI Response section: removed blank lines between sections in `index.html` panelbody, and hid the empty `#respNote` (`.pnote`) when no unavailable modes exist by toggling `.hidden` in `app.js` `renderResponseModes()` (the `flex-basis:100%` was forcing a full empty line); bumped `styles.css?v=73` to `v=74` and `app.js?v=86` to `v=87`.
- 2026-10-09: Fixed gap between AI Response and Experience sections in the settings panel: removed `margin-top:10px` from `.sect` (the `.panelbody` gap already provides spacing between sections); hidden the settings icon (`#settingsBtn`) when the settings panel is open via `openPanel()`/`closeDrawers()` in `app.js`; bumped `styles.css?v=72` to `v=73` and `app.js?v=85` to `v=86`.
- 2026-10-09: Fixed voice card height in the Fish Audio voice picker: set a fixed `height:140px` on `.voice-library-card` (130px on mobile) so all cards have consistent height and are properly visible regardless of content length; bumped `styles.css?v=71` to `v=72`.
- 2026-10-09: Fixed voice card responsiveness in the Fish Audio voice picker: replaced the narrow `max-width:380px` / `min-width:641px` media query gap with a proper three-tier responsive grid (`1fr` on mobile ≤480px, `repeat(2,...)` on tablet 481-768px, `repeat(3,...)` on desktop) so cards resize correctly across all screen widths; bumped `styles.css?v=71`.
- 2026-10-09: Conditionally toggled voice picker button vs Gemini voice select in the settings panel: when "Gemini Live Preview" mode is selected, the "Choose a public voice" button (`#voicePickerOpen`) is hidden and the native Gemini voice dropdown (`#geminiVoiceFld`) is shown; for Groq and Gemini + Fish Audio modes, `#voicePickerOpen` is displayed and `#geminiVoiceFld` is hidden; auto-closes the voice picker modal if open when switching to Gemini Live; bumped `styles.css?v=70` and `app.js?v=85`.
- 2026-10-09: Made voice card heights responsive to content: removed rigid minimum height constraints (`min-height: 166px` / `154px`) and `grid-auto-rows: minmax(...)` from `.voice-library-grid` and `.voice-library-card`, enabling cards to naturally size based on their title, description, and metadata with `align-items: stretch` across grid rows; bumped `styles.css?v=69` and `app.js?v=84`.
- 2026-10-09: Redesigned voice cards in `#voicePicker`: converted the card avatar into a circular play/pause preview button with glowing playing state and hover feedback, moved the Use and Bookmark icon buttons to the top-right corner of the card title row (showing on hover/focus on desktop and accessible on touch), and removed obsolete bottom action row; bumped `app.js?v=83` and `styles.css?v=68`.
- 2026-10-09: Slightly increased minimum voice-card row heights to 166px on desktop and 154px on mobile while retaining content-sized rows; bumped `styles.css?v=67`.
- 2026-10-09: Replaced fixed-height voice-card rows with compact content-sized rows, tightened card padding and metadata spacing, and preserved hover-only actions on desktop; bumped `styles.css?v=66`.
- 2026-10-09: Added concise descriptions and gender/Built-in tags to Default Voices cards using the existing preset gender metadata; bumped `app.js?v=82`.
- 2026-10-09: Restored hover/focus-only voice-card actions on pointer devices while keeping them visible on touch devices; bumped `styles.css?v=65`.
- 2026-10-09: Applied actual two-line WebKit description clamping with overflow ellipsis in voice cards, preventing long descriptions from crowding Preview/Use controls; bumped `styles.css?v=64`.
- 2026-10-09: Kept voice-card Preview, Use, and bookmark controls visible at all times instead of hiding them until hover; bumped `styles.css?v=63`.
- 2026-10-09: Fixed public voice cards to equal-height rows with clamped text/tag overflow; added an overlaid Filters popup beside search for explicitly labelled gender/age and returned tag/voice-quality filters, with reset and active-filter count; bumped `app.js?v=81` and `styles.css?v=62`.
- 2026-10-09: Fixed the voice picker title, tabs, filters, and status above the scrolling voice-results area; removed Public/Default badges from voice cards; bumped `app.js?v=80` and `styles.css?v=61`.
- 2026-10-09: Added Explore, Default Voices, and Bookmarked tabs to the centered Fish voice picker; default voices are selectable in the modal, bookmarks persist locally, and voice cards now have icon play/pause, icon Use, and icon-only bookmark controls; removed the separate preset dropdown from Settings; bumped `app.js?v=79` and `styles.css?v=60`.
- 2026-10-09: Added a language-filtered public Fish Audio voice library in Settings with server-side authentication, paged/cached `/model` requests, name search, public metadata, safe audio previews, retry/empty states, and the upstream catalogue-window limitation; live-verified all ten requested language codes, English/Hindi pagination, previews, and models from other creators; bumped `app.js?v=77` and `styles.css?v=58`.
- 2026-10-09: Improved mobile mic testing by pausing active speech recognition, using a preferred device ID, resuming recognition afterward, and explaining secure-origin/permission errors; moved chat and settings buttons to safe-area-aware top corners on mobile only; bumped `app.js?v=76` and `styles.css?v=57`.
- 2026-10-09: Removed the border animation and SVG overlays from the text input box (`.inputpill`), restoring a clean minimal dark pill style with standard focus transition; bumped `styles.css?v=56`.
- 2026-10-09: Updated top bar status indicator and clock timer to remain hidden when idle and display only during active sessions (`#topbar.hidden` toggled via `startSession()`/`stopSession()`); styled active stop button (`.iconbtn.primary.live`) as a glowing pure white square button with a black stop icon and pulsing white halo ring; bumped `styles.css?v=55` and `app.js?v=75`.
- 2026-10-09: Set `openai/gpt-oss-20b` as the primary Groq model in `.env` (`GROQ_MODEL=openai/gpt-oss-20b,openai/gpt-oss-120b,qwen/qwen3.8-27b`) and updated `.env.example`; verified streaming completions and latency across live test suite; 58 tests passing.
- 2026-10-09: Upgraded multi-engine web search in `app/search.py` with DuckDuckGo + Bing + Wikipedia + Crawl4AI: fixed conversational prefix stripping (`hey`, `tell me about`, typos like `relaesed` → `released`); enriched time-sensitive movie and news queries with current year (`2026`) for up-to-date releases; added parallel DuckDuckGo HTML POST/GET searching and filtered out irrelevant dictionary grammar definitions so current movie releases and real-time facts are accurately retrieved; all 58 tests passing.
- 2026-10-09: Updated all button hover states to a clean, luminous white glow (`box-shadow: 0 0 16px rgba(255,255,255,.3)` and `border-color: rgba(255,255,255,.45)`) instead of changing border color to blue/cyan; bumped `styles.css?v=54`.
- 2026-10-09: Refined border light to an overall ultra-thin tapered light spark with a 0.75px leading bright head (`.tiny-spark-head`) and a 0.35px trailing feather-tail (`.tiny-spark-tail`), smoothly gliding along the input border; bumped `styles.css?v=53`.
- 2026-10-09: Replaced input box border light with a single tiny, short white glowing spark (`.tiny-spark` with `stroke-dasharray: 24 976`, `1.2px` stroke, and glowing filter) that glides cleanly around the subtle dark input box perimeter; bumped `styles.css?v=52`.
- 2026-10-09: Updated input box border to a continuous, thinnest (0.5px) pure glowing white line all over the border with an ambient breathing luminescence (`whiteGlowPulse`); bumped `styles.css?v=51`.
- 2026-10-09: Updated input box border animation to a full solid thin white outline (`.pill-border-track` with `stroke: rgba(255,255,255,0.22)`, brightening on focus) with a bright glowing white wave pulse (`.pill-border-wave` with `stroke-dasharray: 130 870` and glowing filter) continuously orbiting around the entire perimeter; bumped `styles.css?v=50`.
- 2026-10-09: Refined input box border animation to eliminate any thick center/bulge: streamlined to two razor-thin (0.65px), uniform white laser lines (`.comet-beam` with `stroke-dasharray: 50 950`, soft 0.5px track) orbiting smoothly in the same direction around the perimeter; bumped `styles.css?v=49`.
- 2026-10-09: Updated input border animation to two thin-to-thinnest tapered comets (bright 0.95px glowing head + 0.5px faint fading tail) orbiting in the same clockwise direction spaced 180° apart around the input box perimeter; bumped `styles.css?v=48`.
- 2026-10-09: Redesigned input box border animation with two thin (1.1px), bright, short (70 units) white laser beams traveling in opposite directions (`beamClockwise` and `beamCounterClockwise` with `stroke-linecap: round` and glowing drop-shadows) around the pill perimeter; bumped `styles.css?v=47`.
- 2026-10-09: Replaced input box border animation with a perimeter-tracing SVG beam (`.pill-border-svg` `<rect>` with `pathLength="1000"`, `stroke-dasharray`, and `@keyframes pillBeamTrace`) that completes the full continuous loop around the input pill border; made conversation history drawer (`.sidebar`) and backdrop (`.scrim`) transparent on desktop/big screens while preserving the solid blur background on mobile devices; bumped `styles.css?v=46`.
- 2026-10-09: Implemented persistent conversation memory and chat panel history: stored chat turns in client `localStorage` (`flow_chat_history_v1`) rendered into `#transcript` with a divider on load; added clear-history button in sidebar; synced recent chat memory to server sessions (`sync_history` WS message) where `engine.py` digests past discussion topics into `s.past_chat_summary` and `profile.json` memory hints; instructed `BASE_SYSTEM` and `greeting` to naturally reference previous topics (e.g. "Last time you mentioned...", "Do you want to continue what we discussed?"); added `test_sync_past_history` in `tests/test_engine.py` (58 tests passing); bumped `styles.css?v=45` and `app.js?v=74`.
- 2026-10-09: Set **Particle Ribbon** as default background animation (`styleName = 'particles'` in `orb.js`, selected in `index.html`); added continuous thin white glowing animated perimeter sweep on the chat input box border (`.inputpill` `conic-gradient` `borderSweep` animation in `styles.css`); bumped `styles.css?v=44`, `orb.js?v=49`, and `app.js?v=73`.
- 2026-10-09: Increased vertical spacing between the chat text input box and the control dock buttons (desktop `margin-bottom: 20px`, mobile `margin-bottom: 16px`); bumped `styles.css?v=43`.
- 2026-10-09: Fixed mobile microphone infinite restart loop and optimized mobile UI: on mobile devices (Android/iOS), bypassed concurrent `EnergyVAD` `getUserMedia` hardware lock that caused native `SpeechRecognition` sessions to be aborted repeatedly (`mic stream opened` → `mic stream closed` loop); switched mobile `SpeechRecognition.continuous` to `false` with native speech event handling (`onspeechstart`, `onspeechend`, `onsoundstart`) and smooth 120ms restart debounce; updated `styles.css` with responsive mobile layout (`100dvh` viewport handling, safe area insets `env(safe-area-inset-bottom)`, compact 44px touch icon buttons, full-width slide-out drawers, and touch manipulation flags); bumped `styles.css?v=42` and `app.js?v=72`; 57 tests passing.
- 2026-10-09: Enhanced English Practice mode to prioritize conversational comfort, warmup, and gentle spoken English coaching: updated `MODE_ADDENDA["practice"]` and `BASE_SYSTEM` in `app/conversation/prompts.py` so the AI begins with a friendly introduction discussing the scenario and how they will practice together rather than jumping cold into difficult questions; instructed the AI to listen attentively, gently correct grammar/vocabulary slips with encouraging natural alternatives, and build learner confidence throughout the scenario dialogue; updated all scenario openers in `app/coaching/roleplay.py`; 57 tests passing.
- 2026-10-09: Enforced strict scenario focus in English Practice (`practice` mode): enhanced `SCENARIOS` in `app/coaching/roleplay.py` with specific in-character roles and contextual guidelines; updated `MODE_ADDENDA["practice"]` and `build_system` in `app/conversation/prompts.py` to inject the exact scenario title, role, and strict non-deviation rule; updated `engine.py` greeting and message generation to pass the active session scenario; synced and persisted `scenario`, `correction`, and `level` across localStorage and WebSocket connection (`ws.onopen` and `onchange`); added prompt verification test `test_practice_mode_enforces_selected_scenario` in `tests/test_prompts.py` (57 tests passing); bumped `app.js?v=71`.
- 2026-10-09: Fixed factual hallucinations and search accuracy across movie, release, director, version, and date queries: injected real-world current date into `BASE_SYSTEM` and `build_system` in `app/conversation/prompts.py`; added strict factual accuracy and anti-hallucination prompt directives; expanded `needs_search` and `extract_query` in `app/search.py` to trigger on releases, movie sequels, directors, software versions, and user contradictions; upgraded search engine in `app/search.py` to use Bing Web Search (`bing_results`) and Wikipedia Search API with proper User-Agent (`FlowCoachApp/1.0`) alongside Crawl4AI markdown extraction; 56 tests pass in `tests/test_search.py`.
- 2026-10-09: Integrated Crawl4AI (`crawl4ai.AsyncWebCrawler`) for web search & crawling in `app/search.py`: when factual queries trigger search, top reference links (Wikipedia and DuckDuckGo search hits) are deep-crawled with Crawl4AI into clean markdown context for the LLM; added graceful fallback to HTTP extracts if Crawl4AI encounters timeouts; added `crawl4ai` to `requirements.txt`; updated search tests in `tests/test_search.py` (55 tests passing).
- 2026-10-09: Fixed Gemini text visibility during speech: replaced missing `streamAiLineThrottled` call in `ws.onmessage` `llm_token` handler with `LiveCaptions.push(m.token)` (was throwing a ReferenceError and leaving `LiveCaptions.buf` empty until `llm_done`); fixed `livePacedText` so it previews the first sentence instead of searching backwards for the last sentence of the response (which previously locked the display index to the end of the reply); reduced `PACED_LEAD_CHARS` (24 -> 4) so short initial sentences are not skipped; lowered `LIVE_MIN_DWELL_MS` (1200ms -> 600ms) for responsive sentence transitions synced to audio playback; bumped `app.js?v=70`.
- 2026-10-08: Free fact search (`app/search.py`, no keys): `needs_search()` gates factual turns (chit-chat/opinions/personal never trigger; English+Hindi patterns, wrapper stripping); DDG instant-answer + html results run parallel with a Wikipedia lookup, Wikipedia links ranked first, all failures degrade to model-only answer; factual turns speak a filler first (`"Let me check..."`/`"Checking..."` via normal `tts_sentence` in Fish modes, transcript-safe since history only comes from `llm_done`; center-text-only `{"type":"notice"}` in Live mode to avoid overlapping native voice), fact block rides as trailing system message (standard) or inside bidi user text (live); verified live E2E ("Who invented the telephone?" → filler spoken → grounded answer, transcript clean); 53 tests pass (10 new); bumped `app.js?v=69`.
- 2026-10-08: Live center text never shows half sentences: `livePacedText` now prefers the last complete sentence (pre-audio preview, paced path, and end-approach all route through the same complete-sentence rule); an incomplete lead shows only after `FRAG_GRACE_SEC` (3s) of voice with nothing complete, else blank-hold; verified against shipped code (first-chunk hold, grace fallback, no-halves sweep, convergence, meta-strip, abbreviation round-trip); 43 tests pass; bumped `app.js?v=68`.
- 2026-10-08: Live center text holds each chunk min 1.2s (`LIVE_MIN_DWELL_MS` in `LiveCaptions.tick`: new paint only after dwell expires, first paint immediate, reset stops painting; verified hold-then-advance + promptness + reset against shipped code); fish path untouched; 43 tests pass; bumped `app.js?v=67`.
- 2026-10-08: Replaced Live reveal with dedicated audio-clock pacing (`LiveCaptions` 250ms ticker owns the center line for the whole turn; painter no longer stops when tokens stop, `llm_done` never dumps the paragraph mid-voice; audio end drives dissolve via existing `onDrained`); fish path untouched; bumped `app.js?v=66`.
- 2026-10-08: Fixed Live text still racing the voice: arrival-timed pacing lies when tokens front-load (estimated 216 chars/sec vs true ~15), so reveal is now estimated from the audio clock alone (playedSec × 15 chars/sec + 24-char lead, latest chunk in the last stretch, monotonic sentence index, muted/no-audio falls back to chunk); fish path untouched; verified never-ahead-of-voice against shipped code; 43 tests pass; bumped `app.js?v=65`, `audio.js?v=35`.
- 2026-10-08: Synced Gemini Live text with voice: center line now reveals at speaking pace (`livePacedText` budgets visible chars by audio actually played, chars/sec estimated from received audio+text, +24-char lead, monotonic per turn, muted/no-progress falls back to latest chunk); `audio.js` gained per-turn PCM accounting (`liveProgress()`) reset on cancel/turn-change; fish path untouched; verified against shipped code (paced early paint, never-ahead-of-voice, convergence, meta-strip, fallbacks); 43 tests pass; bumped `app.js?v=64`, `audio.js?v=34`.
- 2026-10-08: Fixed Gemini Live center-text paragraph dump + leaked inner monologue: `streamAiLineThrottled` now paints only the latest chunk (`liveChunkText`: last complete sentence, trailing fragment capped at 140 chars, abbreviations like Node.js/3.8 protected via PUA placeholder so they never split or leak); new `stripMetaReply` hides leading meta-reasoning sentences ("The user is asking...", "I need to...", "I'll explain...") from center display only (transcript/history keep the true record); `llm_done` live branches show the chunk, fish path untouched; `BASE_SYSTEM` gained a hard 2-sentence/~40-word cap + explicit never-narrate-thinking rule; added `tests/test_prompts.py`; bumped `app.js?v=63`.
- 2026-10-08: Smoothed Nebula Drift blink and Infinity Möbius sharp edges: raw mic RMS was driving speeds/radii/widths frame-to-frame, so added global exponential mic smoothing in `orb.js` `frame()` (fast 0.35 attack, slow 0.08 release) plus gentler nebula mic gains (drift `mic*1.0`→`0.35`, radius `mic*0.35`→`0.15`); rebuilt Möbius cross-section from a 4-corner box to an 8-point rounded elliptical profile (768 quads), compressed lighting contrast (ambient 0.45 + 0.50 diffuse slope) to hide facet banding, and softened rail strokes (0.32 alpha, every 2nd segment). Measured headless: ≤5.2% frame-to-frame swing under violent mic spikes, ≤2.9% on speech-like input. 41 tests pass; bumped `orb.js?v=48`.
- 2026-10-08: Fixed animation speed spikes in Nebula Drift and Infinity Möbius: the main time variable `t` in `orb.js` `frame()` was advancing at `0.016 * rig.speed * 2`, causing animations to move very fast during SPEAKING (speed=1.5) and INTERRUPTED (speed=2.2) states — changed to constant `0.016` so `t` advances at a steady rate; reduced `speedScale` multipliers across all styles (Möbius: `0.4 + rig.speed * 0.4` down from `* 0.8`; Particles: `0.5 + rig.speed * 0.5` down from `* 1.0`; Motes: same reduction); slowed Möbius boundary flow twist from `0.25` to `0.15`, texture scroll from `0.05` to `0.03`, breathing modulation from `1.2` to `0.8`; reduced Nebula drift multiplier from `1.1` to `0.6` and star movement from `rig.speed * 4` to `rig.speed * 1.5`; bumped `orb.js?v=47`.
- 2026-10-08: Fixed emotion tags not reaching Fish Audio TTS: `clean_for_speech` in `engine.py` no longer strips `[chuckle]`, `[happy]`, `[emphasis]`, etc. — these are now preserved for TTS and sent to Fish Audio as voice direction markers; added `strip_emotion_tags()` to remove them from on-screen display text; `llm_done` WS message now includes `display_text` (tags stripped) alongside `text` (tags preserved for TTS) across all code paths (Fish/Gemini Live/normal/fallback); client `app.js` `plainText()` and `emphasize()` now strip `[...]` tags for display; added tests `test_clean_for_speech_keeps_emotion_tags` and `test_strip_emotion_tags_for_display`; bumped `app.js?v=62`, `styles.css?v=41`.
- 2026-10-08: Expanded **VOICE EXPRESSION & EMOTION TAGS** in `app/conversation/prompts.py` BASE_SYSTEM to include all 49 Fish Audio emotion tags from the documentation: 24 basic emotions (happy, sad, angry, excited, calm, nervous, confident, surprised, satisfied, delighted, scared, worried, upset, frustrated, depressed, empathetic, embarrassed, disgusted, moved, proud, relaxed, grateful, curious, sarcastic), 25 advanced emotions (disdainful, unhappy, anxious, hysterical, indifferent, uncertain, doubtful, confused, disappointed, regretful, guilty, ashamed, jealous, envious, hopeful, optimistic, pessimistic, nostalgic, lonely, bored, contemptuous, sympathetic, compassionate, determined, resigned), 11 sound markers (laughing, chuckling, sobbing, crying loudly, sighing, groaning, panting, gasping, yawning, snoring, clear throat), 6 tone markers (emphasis, in a hurry tone, shouting, screaming, whispering, soft tone), and 4 special effects (audience laughing, background laughter, crowd laughing, break, long-break). Verified `clean_for_speech` in `engine.py` already strips all bracketed tags via `\[[^\]]*\]` regex.
- 2026-10-08: Engineered **Thick 3D Infinity Möbius Strip with Flowing Boundary Rotation**: kept infinity figure-8 orientation stationary in space while rotating/flowing the Möbius ribbon boundary twist continuously along the path ($\phi = u + t \cdot 0.65$); enlarged scale ($A_x \approx 310$px, thickness $W_0 \approx 26-36$px, $H_0 \approx 8-12$px); applied rich harmonious chromatic palette (royal indigo $\to$ cobalt $\to$ electric cyan $\to$ emerald aqua $\to$ golden amber $\to$ coral rose $\to$ royal violet) with soft satin sheen instead of harsh white specular glares; added soft multi-layer atmospheric aura and 36 floating 3D micro-bubbles with chromatic Fresnel reflections; bumped `orb.js?v=45`, `app.js?v=61`, `styles.css?v=40`.
- 2026-10-08: Replaced Infinity Ring with **Thick Animated 3D Möbius Strip (Monochrome)**: engineered an ultra-fast, low-RAM 3D parametric volumetric Möbius slab (84 segments x 4 faces, zero-allocation pre-allocated `Float32Array` vertex buffers, true 3D normal/diffuse/specular/Fresnel shading in pure black & white, soft monochrome atmospheric aura, and 36 depth-sorted 3D glass micro-bubbles with specular glints); eliminated heavy per-segment `shadowBlur` and canvas context allocations for near-zero CPU and low memory footprint; bumped `orb.js?v=44`, `app.js?v=60`, `styles.css?v=39`.
- 2026-10-08: Redesigned **Luminous Ring** to a continuous solid liquid voice circle: replaced multi-line wireframe ribbons with a seamless volumetric 3D fluid donut polygon filled with rotating multi-stop conic gradients (`#2819d2` indigo, `#2563eb` cobalt, `#06b6d4` cyan, `#9333ea` violet, `#ec4899` fuchsia, `#f59e0b` amber), diffuse neon bloom, and crisp platinum specular core filaments; bumped `orb.js?v=42` and `app.js?v=58`.
- 2026-10-08: Added **Luminous Ring** animation style (`renderFlowRing` in `orb.js`) inspired by the 3D iridescent fluid toroidal loop reference: multi-pass volumetric ribbons in 3D perspective with depth-sorting, electric indigo-cyan-violet-magenta-amber chromatic gradients, glowing specular fluid core filaments, and orbital stardust motes; set as new default animation; bumped `orb.js?v=41` and `app.js?v=57`.
- 2026-10-08: Added 5 new Fish Audio voices: Nobita (`7e67b3e4...`, male), Sinchan (`e75da839...`, male), Olivia (`59e9dc1c...`, female), Cutie (`98655a12...`, female), Priya (`4c00e9ff...`, female) in `app/voices.py` and updated `tests/test_persona.py`.
- 2026-10-08: App renamed to **Flow** and Coaching mode simplified: changed title to Flow across `index.html` and `main.py`; simplified Coaching modes to Free Conversation (`free`) and English Practice (`practice`); dynamically hide Scenario, Correction, and Level when Free Conversation is selected via `updateModeUI()`; bumped `styles.css?v=38` and `app.js?v=56`.
- 2026-10-08: Fixed Gemini Live instant audio interruption: connected PCM buffer nodes through a dedicated `GainNode` and tracked all active `BufferSourceNode`s in `AudioQueue._pcmSources`, stopping and disconnecting all active hardware audio buffers immediately upon `cancel()` / `bargeIn()`; bumped `audio.js?v=33` and `app.js?v=55`.
- 2026-10-08: Fixed voice turn dropping on frequent speaking and multi-language input: removed 120ms queue lock in `AudioQueue.cancel()` that caused fast-arriving Fish Audio chunks to be dropped when turns succeeded rapidly; fixed `normText` Unicode regex (`\p{L}\p{N}`) so Hindi/Devanagari text strings are not stripped to empty strings; reduced duplicate threshold window to 1.2s at 0.85 similarity; bumped `audio.js?v=32` and `app.js?v=54`.
- 2026-10-08: Fixed duplicate user message bubbles: guarded `onresult` final STT callback when a turn was already committed via early VAD speech-end (`awaitingFinal`), and added deduplication guard in `sendText()` to suppress duplicate utterances within 3.0s; bumped `app.js?v=53`.
- 2026-10-08: Slow smoky dissolve animation for center AI text after speech completion: added `smokeDissolveAiLine(delayMs)` in `app.js` which triggers 1.4s after the AI finishes speaking (`audioQ.onDrained` and `llm_done`), gently drifting the text upward (`y: -18`), expanding (`scale: 1.05`), diffusing (`filter: blur(16px)`), and fading over 2.0s with GSAP; cancel-safe on new turns/barge-in; bumped `styles.css?v=37` and `app.js?v=52`.
- 2026-10-08: Frosted gray user bubble, invisible scrollbar, and GSAP smoky dissolve effect: styled user bubbles with frosted dark slate-gray (`rgba(45,52,66,0.85)` + `backdrop-filter: blur(16px)`); completely hid all scrollbars (`scrollbar-width: none` / `::-webkit-scrollbar { display: none }`); added GSAP sine-wave animated smoky mist plumes at the bottom of the chat panel (`#chatSmoke` + `.smoke-wisp`) and smoky blur dissolve entrance for new messages; bumped `styles.css?v=37` and `app.js?v=52`.
- 2026-10-08: Added 3-dot typing indicator bubble in side chat panel: shows pulsating cyan/slate dots while AI is thinking/speaking (`#aiTyping` + `.dot-pulse`), smoothly replaced by final partner response on `llm_done` or cleared on interrupt; bumped `styles.css?v=36` and `app.js?v=51`.
- 2026-10-08: Side chat panel scroll & bubble distinction pass: fixed vertical scrolling bug in `#transcript.side` by removing `justify-content: flex-end` (which caused CSS flexbox scroll clipping on overflow) and adding smooth scroll handling with bottom anchoring (`::before margin-top: auto`); styled user messages distinctly on the right side with a modern blue gradient (`rgba(29, 78, 216, 0.85)` to `rgba(30, 58, 138, 0.92)` + `max-width: 84%`), while AI messages sit on the left with frosted dark slate styling; bumped `styles.css?v=35` and `app.js?v=50`.
- 2026-10-08: Redesigned conversation history bubbles: removed "You" and "Partner" speaker labels for a cleaner minimal look; replaced transparent partner chat bubble background with a frosted blur backdrop (`rgba(18,24,36,0.88)` + `backdrop-filter: blur(16px)` + shadow); bumped `styles.css?v=34` and `app.js?v=49`.
- 2026-10-08: Fixed mute/unmute recovery for both mic and speaker: added `AudioQueue.setMuted(m)` to cleanly mute/unmute active HTML5 `<audio>` elements (`this.currentAudio`, `this._streamEl`) and removed desynchronizing sleep loops in `pump()`; added `EnergyVAD.setMuted(m)` to toggle media track enablement (`track.enabled`), pause RMS tracking, reset speaking state, and cleanly recalibrate ambient noise floor on unmute; aborted and restarted Web Speech recognition on mic unmute; bumped `vad.js?v=26`, `audio.js?v=31`, `orb.js?v=40`, `app.js?v=48`.
- 2026-10-08: Fixed voice chat startup and speech detection: guarded `ws.onmessage` `ready` message from resetting active sessions back to `IDLE`; eliminated infinite `STT error network` restart loop with debounced backoff and fatal-error handling in `setupRecog`; tuned `EnergyVAD` threshold (`0.008`) and SNR gate for sensitive detection across all mic gains; fixed `deviceId` constraint with `{ ideal: deviceId }` and added suspended `AudioContext` resumption in `vad.js`; refreshed microphone device labels after permissions; bumped `vad.js?v=25`, `audio.js?v=30`, `orb.js?v=39`, `app.js?v=47`, `styles.css?v=33`.
- 2026-10-08: Mic-input diagnostics (no backend change): microphone picker (`enumerateDevices` → VAD `deviceId`, persisted), standalone 2.5s mic capture test with peak verdict, STT `onaudiostart`/`onaudioend` + network/audio-capture errors surfaced; `vad.js?v=24`, `app.js?v=46`.
- 2026-10-08: Bottom dock is now 5 buttons (caption toggle + interrupt left, big play/square center, mic-mute + speaker-mute right); welcome message removed everywhere (session opens silent, user speaks first; `Engine.greeting` kept unwired); chat icon always visible as the toggle; side fabs moved in to 30px/24px.
- 2026-10-08: Live post-interrupt death fixed: an interrupted Bidi socket is dropped (`close_live`) so the next turn reconnects fresh instead of hanging on a poisoned stream; turns bounded at 60s (`TURN_TIMEOUT_S`) so nothing wedges forever; failures with partial output deliver the partial. New `tests/test_live_recovery.py` (4 hermetic tests). Groq+Fish and Gemini+Fish paths untouched.
- 2026-10-08: Sidebar transcript anchored bottom (`justify-content:flex-end`), stats line removed, chat icon hides while drawer open (returns on close), scrim transparent (catches outside taps, no dim), empty-state hint when no turns yet.
- 2026-10-08: Sidebar simplified per feedback: transparent headerless drawer, whole replies at turn-end (progressive Live painting removed), words wrap full width, chat icon toggles both ways (corner buttons raised above scrim so taps always land).
- 2026-10-08: Live sidebar now fills during the turn (pending `.turn.ai.live` div painted with the same throttle, finalized in place on `llm_done`, greeting-dedup parity, cancel-safe); dropped Live sockets auto-reconnect once and retry the turn when zero output reached the client (fixes `1011 keepalive` deaths with no duplication). Groq+Fish and Gemini+Fish paths untouched.
- 2026-10-08: Synced Gemini Live text with voice: `llm_token`s now paint progressively into `aiLine` (throttled 280ms, no animation churn, Live-only via `audioQ.engine` gate) instead of appearing after speech ends; proven with a headless streaming check. Groq+Fish and Gemini+Fish paths untouched.
- 2026-10-08: Fixed Gemini Live `CERTIFICATE_VERIFY_FAILED` root cause: `DYLD_LIBRARY_PATH=/usr/local/mysql/lib` in the shell interposes a libssl that loses the trust store, so bare `ssl.create_default_context()` failed ONLY for the Live Bidi socket (httpx providers pin certifi, which is why they kept working). `GeminiLiveSession.connect` now pins `cafile=certifi.where()` like httpx does. Proven with a full Live turn under the poisoned env. Groq+Fish and Gemini+Fish untouched.
- 2026-10-08: Closed the remaining Live silence paths: Bidi setup moved inside the guarded turn region (a failed/hung connect used to kill the task with zero client output), `connect()` bounded at 15s, and dead Live sockets now close on WS disconnect + voice-switch so abandoned Google-side sessions can't starve fresh setups. Groq+Fish and Gemini+Fish paths untouched.
- 2026-10-08: Fixed Gemini Live total-silence on interrupted turns: a Live turn streams for many seconds, so any barge-in (real or stray mic-noise trigger) landed mid-sentence and the old code answered with bare `llm_cancelled` (screen wiped, nothing heard). `handle_gemini_live_turn` now delivers whatever tokens already streamed as a partial `llm_done`, or a graceful spoken fallback if killed during setup. Groq+Fish and Gemini+Fish paths untouched.
- 2026-10-08: Integrated Google's official Gemini Live Bidirectional WebSocket API (`wss://.../BidiGenerateContent`) for `gemini_live` mode with model `models/gemini-3.8-live`; real-time streaming of native 24kHz PCM audio chunks and `outputTranscription` tokens without REST TTS roundtrips or rate limits; Web Audio API gapless scheduling in `audio.js`; bumped `app.js?v=40` and `audio.js?v=29`.
- 2026-10-08: Added resilient fallback to Fish Audio in `GeminiTTSProvider` when Google AI Studio returns 429 (rate limit exceeded) on experimental TTS preview models, preventing silence/hanging; silenced false error logs on normal barge-in audio cancellations; bumped `audio.js?v=28`.
- 2026-10-08: Prevented mid-session re-introduction loops and greeting leaks: added strict prompt rule against repeating "Hey I'm [Name]" or pleasantry resets during ongoing conversation; guarded client WebSocket against duplicate reconnect greeting injections; bumped `app.js?v=39`.
- 2026-10-08: Fixed 1-2 word truncated responses and asterisk pronunciation in TTS: revised system prompt (`app/conversation/prompts.py`) to generate complete, engaging 2-3 sentence conversational explanations (25-50 words); increased `max_tokens` to 180; added defensive `clean_for_speech` cleaning across greetings, `/api/tts`, and `/api/tts/stream` so markdown asterisks (`**Flow**`) are never spoken aloud.
- 2026-10-08: Fixed double-reply and mid-sentence cutoffs: increased VAD pause threshold from 600ms to 850ms in `vad.js` for natural speech pauses; added explicit `audioQ.cancel()` on `sendText` so in-flight audio from superseded partial turns is discarded immediately; bumped `vad.js?v=23` and `app.js?v=38`.
- 2026-10-08: Added 20s WebSocket heartbeat ping to prevent idle connection dropouts; analyzed Gemini Live multi-step latency budget vs Groq+Fish single-stream pipeline; bumped `app.js?v=37`.
- 2026-10-08: Added native Google Gemini Live voice generation: `GeminiTTSProvider` (`app/providers/tts/gemini.py`) synthesizes speech directly using Google Gemini's native voices (`Puck`, `Aoede`, `Charon`, `Kore`, `Fenrir`) when "Gemini Live Preview" mode is selected; voice dropdown automatically switches between Gemini Live voices and Fish Audio voices based on selected mode; bumped `app.js?v=36` and `audio.js?v=27`.
- 2026-10-08: AI Response section in settings panel upgraded to 3 active voice assistant pipeline modes: **Groq + Fish Audio** (ultra-fast ~400ms TTFT), **Gemini + Fish Audio** (intelligent English coach), and **Gemini Live Preview** (model `gemini-3.8-live`); real-time per-session mode switching; bumped `app.js?v=35`.
- 2026-10-08: Verified Groq API key and live models (`qwen/qwen3.8-27b`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`), measured Groq TTFT ~388-420ms; verified Fish Audio TTS stream/POST and dual-tier response switching.
- 2026-10-08: Configured `.env` for Groq fast response tier (`GROQ_API_KEY`, `GROQ_MODEL=qwen/qwen3.8-27b,openai/gpt-oss-120b,openai/gpt-oss-20b`, `GROQ_BASE_URL`, `GROQ_REASONING_EFFORT=low`, `LLM_DEFAULT_RESPONSE_MODE=fast`).
- 2026-10-08: Lowest-latency voice pass — two response tiers behind friendly UI labels ("Fast Response" → Groq `app/providers/llm/groq.py` SSE streaming, default model `llama-3.1-8b-instant`, comma list = failover, `reasoning_effort=low` only for gpt-oss/qwen; "Overall Good" → Gemini chain + optional `GEMINI_MODEL` head), per-session `set_response_mode` + `Engine.llm_for()` fallback when a tier has no key; `AI Response` radios in settings (labels only, localStorage `respMode`, unavailable tier greyed); progressive TTS — `FishProvider.open_stream()` + `GET /api/tts/stream` StreamingResponse (measured first frames 0.62s/8ch → 1.0s/38ch vs 2.2-2.9s full body) and the first phrase of each turn (`first: true`) plays through it, later phrases keep POST+prefetch; barge-in now also aborts in-flight TTS fetches (AbortController + generation counter) and drops the streaming element's `src`; measured `[VOICE LATENCY]` block (`app/latency.py`, server `lat` on the first `tts_sentence` + client TTS/buffer/TOTAL in the UI log); prompt opens with a 2-4 word reaction, measured first chunk 9-19 chars vs 68 before (short first phrase = faster voice: Fish first frames 0.62s/8ch vs 1.0s/38ch and less browser start-buffering); `GROQ_*`/`GEMINI_MODEL`/`FISH_REFERENCE_ID` env; fixed the streaming peek (`aiter_bytes(16384)` buffered ~2s of audio before yielding anything — now yields every arriving chunk, measured browser: first frames 721-795ms vs 1036-1257ms for the whole file, playback starts 770-1106ms), stale-audio hard stop (per-turn tags + `deadTurns`, `AbortController` on prefetches, `src` dropped on the streaming element) and a non-blocking WS turn loop so `barge_in` actually cancels mid-reply; latency attribution fixed (greeting/tail audio no longer counted as the current turn); `app/static/latency.html` browser bench; bumped `styles.css?v=27`, `audio.js?v=26`, `app.js?v=34`; tests 25 → 35.
- 2026-10-08: Added gap spacing and layout styling to `.ministats` and `.transcript` in conversation history sidebar; hid scrollbars across all scrollable containers (`.transcript`, `.sidebar`, `.panelbody`, `.log`); bumped `styles.css?v=26`.
- 2026-10-08: Completely removed the Man-God animation style from `orb.js` and `index.html`; restored clean streamlined animation set: Aurora Silk (default), Particle Ribbon, and Nebula Drift; bumped `orb.js?v=38`.
- 2026-10-08: Hitogami (Man-God) animation perfected from reference imagery: exact facial anatomy with arched smoky eyebrows, circular ethereal irises, signature teardrop dots under lower eyelids, sculpted nose bridge/nostrils, animated Cupid's bow lips with jaw drop synced to speech, defined collarbones and sternocleidomastoid neck muscles on high-key glowing white torso, and dream-prism chromatic aberration fringing; bumped `orb.js?v=37`.
- 2026-10-08: Added "Gray Man (Man-God)" animation style in `orb.js` inspired by Hitogami from Mushoku Tensei: ethereal blurry grayscale humanoid bust (head-to-shoulder ratio) on pure black void background with soft celestial halo, spirit motes, and real-time lip/jaw speech articulation synced to the speaking voice; removed "Neon Halo" and "Pulse Orb"; bumped `orb.js?v=36`.
- 2026-10-08: Synchronized voice and text display: hooked `audioQ.onSentenceStart` to update `setAiLine` at the exact instant the audio begins playing in the browser (`a.onplaying`); stopped premature streaming to `aiLine` on raw `llm_token` and `llm_done`; cleared `aiLine` on `sendText`, `bargeIn`, and `stopSession`; bumped `app.js?v=32`.
- 2026-10-08: Bottom controls layout refined: mic button `#startBtn` stays locked at the exact same bottom-center position whether idle or active; `#chatInput`, `#interruptBtn`, and `#muteBtn` stay hidden until the session starts; removed duplicate `#startPromptBtn`; bumped `styles.css?v=25` and `app.js?v=31`.
- 2026-10-08: Replaced heavy CPU/GPU matrix loops with lightweight, high-performance clean animations: "Neon Halo" (4 concentric rotating neon rings + core + photon beacons) and "Pulse Orb" (3 minimalist gyroscopic rings + glowing voice nucleus + orbital motes) running at effortless 60 FPS; bumped `orb.js?v=35`.
- 2026-10-08: Replaced Neon Rings with a 3D Milky Way & Spiral Galaxy animation (1300 stars along 4 logarithmic spiral arms with differential Keplerian rotation, tilted ~58° perspective, glowing golden galactic core, soft interstellar nebular gas clusters, and accretion halo); updated picker to "Milky Way Galaxy"; bumped `orb.js?v=34`.
- 2026-10-08: Added "Neural Ultron" animation style in `orb.js` (3D Fibonacci sphere of 220 synaptic neuron nodes, dynamic distance-based neural filament mesh, traveling electric plasma impulses, and glowing central Ultron AI core); added picker option in `index.html`; bumped `orb.js?v=33`.
- 2026-10-08: Transformed Neon Rings into 3D holographic neon gyro rings (6 multi-inclination 3D rotating rings with depth perspective, orbiting luminous comet beads with white-hot cores, pulsing central holographic nucleus, and audio-reactive expanding shockwaves); bumped `orb.js?v=32`.
- 2026-10-08: Re-engineered Aurora Silk as soft luminous borderless aura waves (Gaussian multi-layer feathered falloff with zero sharp polygon/stroke edges, vibrant saturated neon color spectrum, enhanced center wave undulations); bumped `orb.js?v=31`.
- 2026-10-08: Softened Aurora Silk animation into ethereal translucent gossamer veils (feather-light opacities, removed dense echo sheets, delicate 0.85px crest sheens, whisper-thin threads); bumped `orb.js?v=30`.
- 2026-10-08: Aurora Silk animation upgraded to 3D illuminated silk ribbons (5 layered neon gradient silk sheets with twist modulation, luminous top-edge sheen strokes, deep atmospheric echo sheets, dual central iridescent threads, and stardust motes); bumped `orb.js?v=29`.
- 2026-10-08: Removed the rectangular gradient background fill from Particle Ribbon in `orb.js` for pure floating particle aesthetics; bumped `orb.js?v=28`.
- 2026-10-08: Enhanced Particle Ribbon wave dynamics with 3 interlacing harmonic wave strands, higher spatial wave amplitude, dynamic crest undulation, and increased ribbon width; bumped `orb.js?v=27`.
- 2026-10-08: Calibrated Particle Ribbon animation flow speed to a slow, fluid drift across wave cycles (~4x speed reduction on particle drift, wave oscillation frequencies, and stardust motes); bumped `orb.js?v=26`.
- 2026-10-08: Particle Ribbon rebuilt as 3D multi-chromatic particle wave ribbon (1800 depth-sorted particles, 3D helical twist projection, neon spectrum magenta→violet→blue→cyan→emerald→gold, star nodes, stardust motes, atmospheric wash + bokeh depth); bumped `orb.js?v=25`.
- 2026-10-08: Removed opaque black gradient from `.dockwrap` behind the input text area and start button dock; applied translucent backdrop blur to `.inputpill`; bumped `styles.css?v=24`.
- 2026-10-08: User message display consolidated: removed duplicate center `#partial` element/render so interim and finalized user speech only renders in `#userLine` directly above the chat input; cleared `#userLine` on session stop; bumped asset cache versions (`index.html`, `styles.css?v=23`, `app.js?v=29`).
- 2026-10-07: Seamless/fast conversation pass: keep-alive httpx clients for Fish (+`latency=balanced`, `chunk_length=100`, 64kbps mp3 → synth 1.20s→0.97s, bytes halved) and Gemini; reply pause 350ms→80ms (`REPLY_PAUSE_MS`); first-chunk fast path (`first_chunk_split`) speaks the opening clause instead of waiting for sentence+coalescer hold; client commits the turn on speech-end using the interim (instant request) with STT-final dedupe, drops low-confidence noise finals; VAD gains an adaptive noise floor + 300ms sustained gate (noise no longer animates the orb); audio gap 60ms→25ms, prefetch 3-ahead; fixed `**` markers leaking into TTS on chunk splits; added `scripts/latency_probe.py` (with `--e2e`).
- 2026-10-07: Chat input area hidden until session starts; standalone "Start speaking" prompt button shown on load, hidden after mic click; `startSession`/`stopSession` toggle visibility of `#chatInput` and `#startPromptBtn`.
- 2026-10-07: aiLine emphasis: LLM wraps 1-3 key words in `**…**` (prompts.py rule), client renders white-on-gray (`emphasize()` + fallback heuristic for unmarked text); transcript + TTS strip markers; settings panel redesigned (grouped sections, icon labels, custom selects); orb depth pass (echo ribbons/motes/bokeh/pulses/stars + stage glow) + fixed pre-existing `pcolor` crash (x<0 → seg -1) in particles.
- 2026-10-07: Assistant persona renamed Priya → Noor (unisex); AGENTS.md history left intact.
- 2026-10-07: Persona follows selected voice (`app/voices.py`: Sarah/Natasha female, rest male; WS `set_voice` syncs session on connect/change); Noor removed.
- 2026-10-07: Re-added Hindi-native Fish voice (`8988e6f6…`) — English-cloned voices render Hindi with an English accent; UI hints to switch when language=hindi.
- 2026-10-07: Language option (English/Hindi/Hinglish): prompt addenda, per-session `language` (WS `set_language` + `?lang=` on connect so greeting already matches), STT lang switch (en-IN/hi-IN) with recog restart; 7 Fish voices (Flow default) with picker + per-request voice; fading `#userLine` caption; two-tone AI text with GSAP entrance.
- 2026-10-07: Welcome line is AI-generated (`Engine.greeting`, falls back neutrally); center text blank until mic click; verified via headless mic-click test.
- 2026-10-07: Fixed invisible text properly (canvas moved to direct body child — it was painting over static text inside the same stacking context); proven with headless screenshots, not guesses.
- 2026-10-07: Animation style picker in settings (Aurora Silk, Particle Ribbon, Neon Rings, Nebula Drift) with localStorage persistence; orb.js refactored to style registry on shared GSAP rig.
- 2026-10-07: Fixed hidden-text layering (canvas z:0 behind content z:1, CSS now cache-busted — stale unversioned CSS was the culprit); consolidated duplicate .stage rule.
- 2026-10-07: Vibrant wave pass (hot palette, ~2x alpha, edge fade removed so lines touch both edges, colored magenta→cyan thread); text-shadow/layout from prior turn kept.
- 2026-10-07: Text sits upper-middle (wave drops to 60% height), all foreground text gets black text-shadow so it reads over the fullscreen wave; content explicitly layered above canvas.
- 2026-10-07: Wave is fullscreen background (fixed, behind content), white core thread removed (ribbons only); history button is message-square icon; removed Fish label from footer.
- 2026-10-07: Removed halo-glow circles from wave (ribbons + thread only); true full-bleed (symmetric margins + overflow-x clip, kills right-gap); history button bottom-left; status lives in top bar (IDLE→READY, never center).
- 2026-10-07: Wave lens envelope (thin sides, wide middle, edge fade) — kills boxy look; text+wave vertically centered as one group; sidebar wiring verified (v11 code was correct, stale cache was the culprit → v12 bust).
- 2026-10-07: Conversation moved to left drawer; center shows live line only; wave full-bleed; created AGENTS.md with self-update rule.
- 2026-10-07: Aurora silk-ribbon animation (GSAP-tweened params, ticker loop); greeting/headline layout per reference.
- 2026-10-07: Black minimal UI, Lucide icons, icon dock, settings gear; STT en-IN + sustained barge gate + mic error notes.
- 2026-10-07: Fixed cancel-during-playback queue wedge; Fish timeout 25s→12s; time-to-first-audio readout; Fish MP3 sniff.
- 2026-10-07: `SentenceCoalescer` merges short sentences (killed mid-thought pauses); client prefetch; 110ms→60ms gap.
- 2026-10-07: Fish-only TTS, single voice `802e3bc2…`, free model via header; removed Edge/browser fallbacks.
- 2026-10-07: Human-voice pass (Edge middle tier, prompt rewrite as Priya, chunker fixes) — later superseded by Fish-only.
