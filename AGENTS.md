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
- LLM: **two response tiers behind friendly UI labels** (never show provider names
  in the UI — `/api/config` only exposes `response_modes` with labels/descriptions):
  - `"fast"` → **"Fast Response"** = Groq (`app/providers/llm/groq.py`, OpenAI-compat
    SSE, `GROQ_MODEL` default `llama-3.1-8b-instant`, comma list = failover chain;
    `reasoning_effort` only for gpt-oss/qwen). Lowest TTFT; the app default.
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
- STT: browser Web Speech, lang `en-IN`. VAD: local energy (`vad.js`) with an
  adaptive noise floor (fan/AC hum raises the gate instead of triggering).
  A turn commits the moment speech ENDS (latest interim), not when the STT
  engine finalizes; the final is then deduped (`sameText`).
- Frontend: vanilla JS + Tailwind CDN + GSAP CDN + Lucide icons. No emojis in UI.
- Audio pipeline: WS `tts_sentence` chunks → `AudioQueue`:
  the **first** phrase of a turn (`first: true`) is stream-played from
  `/api/tts/stream` (sound starts on the first frames), later phrases keep the
  Fish POST + 3-ahead prefetch + 25ms inter-chunk gap. Barge-in cancels the LLM
  task, aborts in-flight TTS fetches (`AbortController`), clears the queue/cache
  and drops the streaming element's `src` — stale audio can never resume.
  First chunk goes out via the engine's fast path (opening clause) so the voice
  starts before the reply is fully written.
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
python3 -m pytest tests/ -q        # 53 tests
python3 scripts/smoke_test.py      # WS turn + barge-in + mode switch + streaming audio
python3 scripts/latency_probe.py --e2e     # speech→voice budget (needs real keys)
python3 scripts/latency_probe.py --tiers   # per-tier TTFT + Fish stream TTFB (real keys)
```
Cache-bust static JS on every frontend edit: bump `?v=N` in `index.html`.
Same for `styles.css` (it was unversioned once — stale CSS hid all text behind
the fixed canvas). Layer rule: content `.stage` z-index:1 above canvas z-index:0.

## UI map (keep in sync with index.html)
- Top bar: live `dot`/`state`/`micLive` (status text; IDLE displays as READY,
  never in center) + session `clock`. Center: `greetLine` → `aiLine` (current
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
- No welcome message anywhere: session opens silent on mic click, user speaks first (`Engine.greeting` retained but unwired).
- Gear `settingsBtn` bottom-right → right `#panel` (grouped: AI RESPONSE /
  EXPERIENCE / COACHING / SESSION section headers, icon'd `.flbl` labels,
  custom-chevron selects in `.fld`, 2-col `.grid2` for correction+level):
  **AI Response** radio group `#respModes` (rendered from `/api/config`
  `response_modes`: **Groq + Fish Audio** / **Gemini + Fish Audio** / **Gemini Live Preview**
  and their descriptions, unavailable tiers greyed with a note in `#respNote`;
  persisted in localStorage `respMode`, sent as WS `set_response_mode`), then mode/scenario/correction/level,
  language (`#language`: english/hindi/hinglish), microphone (`#micDevice`
  picker from `enumerateDevices`, persisted, passed as `deviceId` to VAD
  `getUserMedia`; `#micTestBtn` standalone 2.5s capture test with peak readout
  in `#micTestStatus` (uses an ideal device constraint, pauses active speech
  recognition, and explains secure-context/permission errors); STT lifecycle
  (`onaudiostart`/`onaudioend`/extended error names) logged so mic failures are
  visible, never silent), voice controls: when "Gemini Live Preview" is active,
  `#geminiVoiceFld` renders the native Gemini voice dropdown (`#voice`); for
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
  (`clean_for_speech` / `plainText`). GSAP fade+rise on each completed reply
  (`animateAiLine`, never per-token).
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
1. Fish 402 with valid key = wrong model: free tier needs header
   `model: s2.1-pro-free`, NOT `"model": "s2.1-pro"` in body.
2. Fish can 200 with a JSON error body: `fish.py` sniffs MP3 magic bytes.
3. `audio.pause()` never fires `ended`: `cancel()` MUST settle the playUrl
   promise (`_playResolve`) or the queue wedges silent forever.
4. One Fish request per sentence = network pause mid-thought:
   `SentenceCoalescer` holds <45-char sentences and merges (see tests).
5. STT defaults to `en-IN`; mobile Web Speech recognition stays continuous so
   ordinary utterance boundaries do not reopen the mic stream. Keep the `onend`
   restart as recovery for browser-terminated sessions, but never restart while
   muted or while an assistant turn is active (`isTurnActive()`: generation in
   flight or TTS playback queue busy). STT is paused on turn submission and
   resumes once after the entire response drains. Barge-in needs a sustained-speech
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
15. Mobile mic capture requires a secure origin (HTTPS or localhost). The manual
   mic test pauses active Web Speech recognition first, uses a preferred rather
   than exact saved device ID, and gives a site-permission explanation for
   `NotAllowedError`; it cannot override a permission denied by the browser or OS.
16. Fish Audio `GET /model` documents public listing (`self=false`) and page
   controls up to 100 records per page; response `window_limited`,
   `max_offset`, and total fields must be preserved. Live verification returned
   public English and Hindi voices on pages 1 and 2 from creators different
   from the zero-result `self=true` owned-model query, but also set
   `window_limited=true` with an accessible total of 1,000. Never describe this
   endpoint as the complete public catalogue.

## Tests
- Mobile mic permission flow and top-corner controls: manually verify in a
  secure-origin mobile browser, including a denied permission and an active
  speech-recognition session.
- `tests/test_fish_library.py` — public visibility filtering, metadata mapping,
  English/Hindi pagination parameters, page and model-verification caches,
  empty results, and upstream rate-limit/network errors.
- `tests/test_correction.py` — correction policy (passive/balanced/active).
- `tests/test_engine.py` — chunker, `SentenceCoalescer` merge, first-chunk fast
  path, marker stripping, level smoothing, §43 transcript.
- `tests/test_providers.py` — Groq SSE request shape + model failover + missing
  key, tier selection/fallback, Fish streaming (chunks, JSON-as-200, 402), the
  `[VOICE LATENCY]` formatter, and that `/api/config` never leaks provider names.
- `scripts/smoke_test.py` — live WS turn, barge-in, response-mode switch +
  latency payload, summary, Fish MP3 bytes, progressive `/api/tts/stream`.
- `app/static/latency.html` — browser bench: streamed first frames vs complete
  mp3, playback start, barge-in cancel (manual, open `/static/latency.html`).

## Changelog (newest first — RULE ZERO: append here on every change)
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
