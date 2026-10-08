/* Always-listening client: mic open from Start, auto-interrupt always on.
   mic -> local VAD -> WebSpeech STT (en-IN) -> WS -> streaming LLM -> Fish TTS. */
const $ = (id) => document.getElementById(id);
let ws = null, sid = 's' + Math.random().toString(36).slice(2, 8);
let state = 'IDLE';
let recog = null, vad = null, audioQ = new AudioQueue();
let listening = false, aiSpeaking = false, muted = false, running = false;
let micMuted = false, captionsOn = true;
let fullReply = '';
let userWords = 0, userTurns = 0, sessStart = 0, clockTimer = null;
let speechTimer = null;
let userFadeTimer = null;
let sttLang = 'en-IN'; // follows Language setting: english/hinglish=en-IN, hindi=hi-IN
// instant-turn state: commit on speech-end using the interim transcript instead
// of waiting ~1s for the STT engine to finalize
let lastInterim = '', lastInterimAt = 0, awaitingFinal = false;
let lastSentText = '', lastSentAt = 0;
// AI Response mode (friendly labels only — the provider behind them is server-side)
let respModes = [], respMode = 'fast', respModeLabel = 'Fast Response';
let turnSeq = 0;
const icons = () => { try { window.lucide && lucide.createIcons(); } catch (e) {} };

// --- [VOICE LATENCY]: the number the user actually feels -------------------
function logLatency(info) {
  const L = info.lat || {};
  const ms = (v) => (v == null || !isFinite(v) ? 'n/a' : Math.round(v) + 'ms');
  const block = [
    '[VOICE LATENCY] ' + respModeLabel,
    'STT + network:      ' + ms(L.stt_ms),
    'LLM TTFT:           ' + ms(L.llm_ttft_ms),
    'First phrase:       ' + ms(L.chunk_ms),
    'TTS first audio:    ' + ms(info.ttsMs) + (info.streamed ? ' (streamed)' : ''),
    'Playback buffer:    ' + ms(info.bufferMs),
    '--------------------------------------',
    'TOTAL speech end → first AI audio: ' + ms(info.totalMs),
  ].join('\n');
  try { console.log(block); } catch (e) {}
  log(block);
}

function renderResponseModes(cfg) {
  const box = $('respModes');
  respModes = cfg.response_modes || [];
  if (!box || !respModes.length) return;
  let saved = null;
  try { saved = localStorage.getItem('respMode'); } catch (e) {}
  const ids = respModes.map(m => m.id);
  respMode = (saved && ids.indexOf(saved) >= 0) ? saved : (cfg.default_response_mode || 'fast');
  box.innerHTML = '';
  respModes.forEach(m => {
    const lab = document.createElement('label');
    lab.className = 'radio' + (m.available === false ? ' unavail' : '');
    lab.innerHTML = '<input type="radio" name="respMode" value="' + m.id + '"' +
      (m.id === respMode ? ' checked' : '') + '><span class="rlab">' + escHtml(m.label) +
      '<span class="rdesc">' + escHtml(m.desc || '') + '</span></span>';
    lab.querySelector('input').onchange = () => applyResponseMode(m.id);
    box.appendChild(lab);
  });
  labelFor(respMode);
  const note = $('respNote');
  if (note) {
    const un = respModes.filter(m => m.available === false).map(m => m.label);
    note.textContent = un.length ? un.join(' + ') + ' is unavailable right now — the other option is used instead.' : '';
  }
}
function labelFor(id) {
  const m = respModes.filter(x => x.id === id)[0];
  if (m) {
    respModeLabel = m.label;
  } else if (id === 'groq_fish' || id === 'fast') {
    respModeLabel = 'Groq + Fish Audio';
  } else if (id === 'gemini_fish' || id === 'quality') {
    respModeLabel = 'Gemini + Fish Audio';
  } else if (id === 'gemini_live' || id === 'live') {
    respModeLabel = 'Gemini Live Preview';
  } else {
    respModeLabel = id;
  }
  return respModeLabel;
}
let serverConfig = null;

function updateVoiceDropdown() {
  const v = $('voice');
  if (!v || !serverConfig) return;
  const isGeminiLive = (respMode === 'gemini_live');
  audioQ.engine = isGeminiLive ? 'gemini' : 'fish';
  const voices = isGeminiLive ? (serverConfig.gemini_voices || []) : (serverConfig.fish_voices || []);
  const defaultVoice = isGeminiLive ? (serverConfig.default_gemini_voice || 'Puck') : (serverConfig.default_fish_voice || '');
  const storageKey = isGeminiLive ? 'geminiVoice' : 'fishVoice';

  v.innerHTML = '';
  voices.forEach(x => {
    const o = document.createElement('option');
    o.value = x.id;
    o.textContent = x.label;
    v.appendChild(o);
  });

  let pick = defaultVoice;
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved && voices.some(x => x.id === saved)) pick = saved;
  } catch (e) {}
  if (pick) v.value = pick;
  audioQ.fishVoice = v.value || '';

  const fs = $('fishStatus');
  if (fs) {
    if (isGeminiLive) {
      fs.textContent = serverConfig.gemini_key_set ? 'Gemini Live Ready (Native Voices)' : 'Gemini API key missing';
    } else {
      fs.textContent = serverConfig.fish_key_set ? 'Fish S2.1 Pro Free ready' : 'Fish API key missing';
    }
  }
}

function applyResponseMode(id, silent) {
  respMode = id; labelFor(id);
  try { localStorage.setItem('respMode', id); } catch (e) {}
  updateVoiceDropdown();
  if (!silent) {
    try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'set_response_mode', value: id })); } catch (e) {}
    try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'set_voice', value: audioQ.fishVoice })); } catch (e) {}
    log('[AI] response mode →', respModeLabel, `(Voice: ${audioQ.engine}/${audioQ.fishVoice})`);
  }
}

// small caption under the wave: only the current user line, fades when quiet
function userSaid(text) {
  const el = $('userLine');
  if (!el) return;
  el.textContent = text || '';
  el.classList.toggle('show', !!text);
  clearTimeout(userFadeTimer);
  if (text) {
    userFadeTimer = setTimeout(() => {
      el.classList.remove('show');
      setTimeout(() => { if (!el.classList.contains('show')) el.textContent = ''; }, 700);
    }, 6000);
  }
}

function setState(s, note) {
  state = s;
  const shown = s === 'IDLE' ? 'READY' : s; // never show IDLE
  $('state').textContent = shown + (note ? ' — ' + note : '');
  const dot = $('dot');
  dot.className = 'dot ' + s.toLowerCase();
  aiSpeaking = (s === 'SPEAKING');
  try { window.Orb && Orb.set(s); } catch (e) {}
}
function log(...a) {
  const el = $('log');
  el.textContent += a.join(' ') + '\n';
  el.scrollTop = el.scrollHeight;
}
function setIcon(btn, name) {
  btn.innerHTML = `<i data-lucide="${name}"></i>`;
  icons();
}
function hearNote(t, ms = 2600) {
  const el = $('hearNote');
  el.textContent = t || '';
  if (t) setTimeout(() => { if (el.textContent === t) el.textContent = ''; }, ms);
}
let smokeAnimationStarted = false;
function initChatSmoke() {
  if (!window.gsap || smokeAnimationStarted) return;
  const w1 = document.querySelector('.smoke-wisp.wisp1');
  const w2 = document.querySelector('.smoke-wisp.wisp2');
  const w3 = document.querySelector('.smoke-wisp.wisp3');
  if (!w1 || !w2 || !w3) return;
  smokeAnimationStarted = true;
  gsap.to(w1, { x: 28, y: -20, scale: 1.3, opacity: 0.5, duration: 4.5, repeat: -1, yoyo: true, ease: 'sine.inOut' });
  gsap.to(w2, { x: -32, y: -24, scale: 1.4, opacity: 0.4, duration: 5.8, repeat: -1, yoyo: true, ease: 'sine.inOut', delay: 0.6 });
  gsap.to(w3, { x: 22, y: -16, scale: 1.2, opacity: 0.35, duration: 4.0, repeat: -1, yoyo: true, ease: 'sine.inOut', delay: 1.2 });
}

function showTypingIndicator() {
  hideTypingIndicator();
  const t = $('transcript');
  if (!t) return;
  const d = document.createElement('div');
  d.id = 'aiTyping';
  d.className = 'turn ai typing';
  d.innerHTML = '<span class="dot-pulse"></span><span class="dot-pulse"></span><span class="dot-pulse"></span>';
  t.appendChild(d);
  if (window.gsap) {
    gsap.fromTo(d,
      { opacity: 0, y: 10, filter: 'blur(6px)' },
      { opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.32, ease: 'power2.out' }
    );
  }
  t.scrollTop = t.scrollHeight;
  refreshEmptyHint();
}

function hideTypingIndicator() {
  const el = $('aiTyping');
  if (el) el.remove();
  refreshEmptyHint();
}

function addTurn(who, text) {
  hideTypingIndicator();
  const d = document.createElement('div');
  d.className = 'turn ' + (who === 'You' ? 'you' : 'ai');
  d.textContent = plainText(text);
  const t = $('transcript');
  t.appendChild(d);
  if (window.gsap) {
    gsap.fromTo(d,
      { opacity: 0, y: 14, filter: 'blur(8px)', scale: 0.98 },
      { opacity: 1, y: 0, filter: 'blur(0px)', scale: 1, duration: 0.42, ease: 'power2.out' }
    );
  }
  t.scrollTop = t.scrollHeight;
  refreshEmptyHint();
}
// --- aiLine emphasis: **key words** render white, everything else gray ---
function escHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}
function plainText(t) { return (t || '').replace(/\*\*/g, '').replace(/\[[^\]]*\]/g, ''); }
function emphasize(t) {
  t = escHtml(t || '');
  t = t.replace(/\[[^\]]*\]/g, ''); // strip emotion tags for display
  const hadMarkers = /\*\*/.test(t);
  t = t.replace(/\*\*([^*]+)\*\*/g, '<span class="hl">$1</span>');
  t = t.replace(/\*{1,2}/g, ''); // streaming: hide lone / still-open markers
  if (!hadMarkers) {
    // fallback for unmarked text (greeting, mock): numbers, quoted phrases, mid-sentence names
    t = t.replace(/'([A-Z][^']{1,60}?)'/g, '<span class="hl">\'$1\'</span>');
    t = t.replace(/\b(\d[\d.,:%]*)\b/g, '<span class="hl">$1</span>');
    t = t.replace(/([a-z,]) ([A-Z][a-zA-Z]{2,})\b/g, '$1 <span class="hl">$2</span>');
  }
  return t;
}
// --- live center-text: chunked display + inner-monologue strip ---
// The voice speaks sentence by sentence, so the center line must too —
// painting the whole accumulated reply at once reads like a paragraph dump.
// Leading meta-reasoning sentences ("The user is asking...", "I need to...")
// are stripped from DISPLAY only (transcript/history keep the true record).
const META_LEAD = /^\s*(the user is asking[^.!?]*[.!?]\s*|i need to[^.!?]*[.!?]\s*|i(?:'ll| will) explain[^.!?]*[.!?]\s*|as an ai\b[^.!?]*[.!?]\s*|here'?s my (plan|thinking)[^.!?]*[.!?]\s*|let me think( about (this|it))?[^.!?]*[.!?]\s*)+/i;
// Same openers, but unfinished (no terminator yet): while the model is still
// mid-sentence on meta talk, show nothing rather than flashing it.
const META_OPEN = /^\s*(the user is asking[^.!?]*|i need to[^.!?]*|i(?:'ll| will) explain[^.!?]*|as an ai\b[^.!?]*|here'?s my (plan|thinking)[^.!?]*|let me think( about (this|it))?[^.!?]*)$/i;
// Abbreviations ("Node.js", "3.8", "e.g.") contain periods that are NOT
// sentence ends. Hide those dots while splitting/stripping, restore after.
const ABBR_DOT = String.fromCharCode(0xE000); // private-use stand-in, never rendered (restored to "." before display)
function protectAbbr(t) {
  return (t || '')
    .replace(/([A-Za-z])\.([A-Za-z])/g, '$1' + ABBR_DOT + '$2')
    .replace(/(\d)\.(\d)/g, '$1' + ABBR_DOT + '$2');
}
function stripMetaReply(t) {
  return protectAbbr(t || '').replace(META_LEAD, '').trim();
}
function liveChunkText(full) {
  const clean = stripMetaReply(full).replace(/\s+/g, ' ').trim();
  if (!clean || META_OPEN.test(clean)) return '';
  const parts = clean.match(/[^.!?]+[.!?]+["']?|[^.!?]+$/g) || [clean];
  const chunks = parts.map(s => s.trim()).filter(Boolean);
  if (!chunks.length) return '';
  const last = chunks[chunks.length - 1];
  const out = /[.!?]["']?$/.test(last) ? last
    : (last.length > 140 ? last.slice(0, 140).trim() + '…' : last);
  return out.split(ABBR_DOT).join('.');
}
let aiSmokeFadeTimer = null;
let aiSmokeTimeline = null;

function smokeDissolveAiLine(delayMs = 1200) {
  clearTimeout(aiSmokeFadeTimer);
  if (aiSmokeTimeline) {
    aiSmokeTimeline.kill();
    aiSmokeTimeline = null;
  }
  const el = $('aiLine');
  if (!el || !el.textContent.trim()) return;

  aiSmokeFadeTimer = setTimeout(() => {
    if (!el || !el.textContent.trim()) return;
    if (aiSmokeTimeline) aiSmokeTimeline.kill();

    if (window.gsap) {
      aiSmokeTimeline = gsap.timeline({
        onComplete: () => {
          el.innerHTML = '';
          gsap.set(el, { opacity: 1, y: 0, scale: 1, filter: 'blur(0px)' });
          aiSmokeTimeline = null;
        }
      });
      aiSmokeTimeline.to(el, {
        opacity: 0,
        y: -18,
        scale: 1.05,
        filter: 'blur(16px)',
        duration: 2.0,
        ease: 'power2.out'
      });
    } else {
      el.innerHTML = '';
    }
  }, delayMs);
}

let aiDissolveTimeline = null;
function dissolveAiLine(text) {
  clearTimeout(aiSmokeFadeTimer);
  if (aiSmokeTimeline) {
    aiSmokeTimeline.kill();
    aiSmokeTimeline = null;
  }
  const el = $('aiLine');
  if (!el) return;
  if (!text || !text.trim()) {
    if (window.gsap) {
      gsap.to(el, { opacity: 0, y: -8, filter: 'blur(10px)', duration: 0.35, ease: 'power2.out', onComplete: () => { el.innerHTML = ''; } });
    } else {
      el.innerHTML = '';
    }
    return;
  }
  const newHtml = emphasize(text);
  if (!window.gsap) {
    el.innerHTML = newHtml;
    return;
  }
  if (aiDissolveTimeline) {
    aiDissolveTimeline.kill();
    aiDissolveTimeline = null;
  }
  gsap.set(el, { scale: 1 });
  const currentContent = el.textContent.trim();
  const currentOpacity = parseFloat(getComputedStyle(el).opacity || '1');
  if (!currentContent || currentOpacity < 0.05) {
    el.innerHTML = newHtml;
    gsap.fromTo(el,
      { opacity: 0, y: 12, filter: 'blur(8px)' },
      { opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.45, ease: 'power2.out', overwrite: true }
    );
    return;
  }
  aiDissolveTimeline = gsap.timeline();
  aiDissolveTimeline
    .to(el, {
      opacity: 0,
      y: -8,
      filter: 'blur(6px)',
      duration: 0.2,
      ease: 'power2.in'
    })
    .call(() => {
      el.innerHTML = newHtml;
    })
    .fromTo(el,
      { opacity: 0, y: 10, filter: 'blur(8px)' },
      { opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.4, ease: 'power2.out' }
    );
}
function setAiLine(t) { dissolveAiLine(t); }
// Live-mode progressive text: tokens paint as the voice speaks in sync with audio.
// Audio-paced reveal for Live mode: tokens arrive far faster than realtime
// speech, so unpaced text sprints ahead and the voice finishes later.
// Position in the text is estimated from the audio clock
// (spoken ~= playedSec x conversational rate) — monotonic per turn.
const PACED_RATE = 15; // chars/sec of conversational speech
const PACED_LEAD_CHARS = 4;
const TERM_RE = /[.!?]["']?$/;
let pacedSentIdx = 0;
function livePacedText(full) {
  const clean = stripMetaReply(full).replace(/\s+/g, ' ').trim();
  if (!clean || META_OPEN.test(clean)) return '';
  let prog = null;
  try { prog = (typeof muted !== 'undefined' && muted) ? null : audioQ.liveProgress(); } catch (e) {}
  // split protected text into sentences (abbreviation dots are masked)
  const parts = [];
  const offs = [];
  const re = /[^.!?]+[.!?]+["']?|[^.!?]+$/g;
  let m;
  while ((m = re.exec(clean)) !== null) {
    const s = m[0].trim();
    if (!s) continue;
    parts.push(s);
    offs.push(m.index);
  }
  if (!parts.length) return '';
  const played = prog ? prog.playedSec : 0;
  let idx = 0;
  if (!prog || prog.recvSec < 0.25) {
    // audio not started yet: preview the first sentence
    idx = 0;
  } else {
    const pos = played * PACED_RATE + PACED_LEAD_CHARS;
    for (let i = 0; i < parts.length; i++) {
      if (offs[i] <= pos) idx = i;
      else break;
    }
  }
  idx = Math.max(idx, Math.min(pacedSentIdx, parts.length - 1));
  pacedSentIdx = idx;
  let out = parts[idx];
  if (!TERM_RE.test(out)) {
    // if incomplete sentence, fall back to previous complete sentence if available;
    // if no previous complete sentence, display the current opening fragment
    let prev = '';
    for (let i = idx - 1; i >= 0; i--) {
      if (TERM_RE.test(parts[i])) { prev = parts[i]; break; }
    }
    if (prev) out = prev;
    else out = out.length > 140 ? out.slice(0, 140).trim() + '…' : out;
  }
  return out.split(ABBR_DOT).join('.');
}
// --- Gemini Live captions: SEPARATE logic from the Fish path, on purpose ---
// Fish paints one finished sentence each time that sentence's audio starts.
// Live delivers one fast token burst plus slow audio, so token arrival can
// never drive the display (painting stops the moment tokens stop, and
// llm_done would dump the paragraph while the voice is still talking).
// Instead this controller owns the center line for the whole turn on its own
// ticker off the AUDIO clock (see audioQ.liveProgress()): text only
// ever advances as far as the voice has actually spoken.
const LIVE_MIN_DWELL_MS = 600;
const LiveCaptions = {
  timer: null,
  buf: '',
  done: false,
  lastPaint: '',
  lastPaintAt: 0,
  start() {
    // new turn: reset pacing + buffer, (re)start the audio-clock ticker
    try { if (this.timer) clearInterval(this.timer); } catch (e) {}
    this.buf = '';
    this.done = false;
    this.lastPaint = '';
    this.lastPaintAt = 0;
    pacedSentIdx = 0;
    this.timer = setInterval(() => this.tick(), 150);
  },
  reset() {
    // new user turn before any reply audio: stop and forget everything
    try { if (this.timer) clearInterval(this.timer); } catch (e) {}
    this.timer = null;
    this.buf = '';
    this.done = false;
    this.lastPaint = '';
    this.lastPaintAt = 0;
    pacedSentIdx = 0;
  },
  stop() {
    try { if (this.timer) clearInterval(this.timer); } catch (e) {}
    this.timer = null;
  },
  push(tok) {
    if (!this.timer) this.start();
    this.buf += tok;
    if (!this.lastPaint) this.tick();
  },
  finish(text) {
    this.buf = text || this.buf;
    this.done = true;
    this.tick();
  },
  tick() {
    if (!this.timer && !this.done) return;
    let html = '';
    try {
      const chunk = livePacedText(this.buf);
      if (chunk) html = emphasize(chunk);
    } catch (e) {}
    const el = $('aiLine');
    if (!el) return;
    const now = performance.now();
    if (html && html !== this.lastPaint &&
        (!this.lastPaint || now - this.lastPaintAt >= LIVE_MIN_DWELL_MS)) {
      if (aiDissolveTimeline) {
        aiDissolveTimeline.kill();
        aiDissolveTimeline = null;
      }
      el.innerHTML = html;
      try { if (window.gsap) gsap.set(el, { opacity: 1, y: 0, filter: 'blur(0px)' }); } catch (e) {}
      this.lastPaint = html;
      this.lastPaintAt = now;
    }
    if (this.done) {
      // turn text is final: stop once the voice has caught up with it
      let prog = null;
      try { prog = audioQ.liveProgress(); } catch (e) {}
      if (!prog || prog.recvSec < 0.25 || prog.playedSec >= prog.recvSec - 0.1) {
        this.stop();
      }
    }
  }
};
function bumpStats() {
  // stats line removed from UI; keep counters harmless if re-added later
  try {
    const w = $('statWords'), t = $('statTurns');
    if (w) w.textContent = userWords + (userWords === 1 ? ' word' : ' words');
    if (t) t.textContent = userTurns + (userTurns === 1 ? ' turn' : ' turns');
  } catch (e) {}
}
function refreshEmptyHint() {
  try {
    const has = $('transcript').querySelector('.turn');
    $('emptyHint').style.display = has ? 'none' : '';
  } catch (e) {}
}
function startClock() {
  sessStart = Date.now();
  stopClock();
  clockTimer = setInterval(() => {
    const s = Math.floor((Date.now() - sessStart) / 1000);
    $('clock').textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }, 1000);
}
function stopClock() { try { clearInterval(clockTimer); } catch (e) {} }

function updateModeUI() {
  const m = $('mode') ? $('mode').value : 'free';
  const opt = $('practiceOptions');
  if (opt) {
    opt.classList.toggle('hidden', m !== 'practice');
  }
}

async function loadConfig() {
  try {
    const cfg = await (await fetch('/api/config')).json();
    serverConfig = cfg;
    const sc = $('scenario');
    if (sc) {
      sc.innerHTML = '';
      (cfg.scenarios || []).forEach(s => { const o = document.createElement('option'); o.value = s.id; o.textContent = s.title; sc.appendChild(o); });
    }
    const av = $('anim');
    if (av) {
      try { av.value = (window.Orb && Orb.style) || 'mobius'; } catch (e) {}
    }
    let savedMode = 'free';
    try { savedMode = localStorage.getItem('mode') || 'free'; } catch (e) {}
    if ($('mode')) {
      $('mode').value = (savedMode === 'practice') ? 'practice' : 'free';
      updateModeUI();
    }
    renderResponseModes(cfg);
    updateVoiceDropdown();
  } catch (e) {
    const fs = $('fishStatus');
    if (fs) fs.textContent = 'Server unreachable — is it running on :8000?';
    log('[CFG] failed:', e);
  }
}

async function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  audioQ.muted = muted;
  audioQ.onSpeaking = () => {
    clearTimeout(aiSmokeFadeTimer);
    if (aiSmokeTimeline) { aiSmokeTimeline.kill(); aiSmokeTimeline = null; }
    setState('SPEAKING');
  };
  audioQ.onDrained = () => {
    if (running) setState('LISTENING'); else setState('IDLE');
    smokeDissolveAiLine(1400);
  };
  audioQ.onSentenceStart = (text) => { setAiLine(text); };
  audioQ.onFirstAudio = (info) => logLatency(info);
  audioQ.onError = (msg) => { const fs = $('fishStatus'); if (fs) fs.textContent = msg; };
  audioQ.onLog = (...a) => log('[AUDIO]', ...a);
  await audioQ.init();
  if (!serverConfig) await loadConfig();
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  // language rides on connect so the AI greeting already speaks it (no race)
  let langParam = 'english';
  try {
    const sel = $('language') && $('language').value;
    langParam = (sel || localStorage.getItem('lang') || 'english');
  } catch (e) {}
  ws = new WebSocket(`${proto}://${location.host}/ws/session/${sid}?lang=${encodeURIComponent(langParam)}`);
  ws.onopen = () => {
    log('[WS] connected', sid);
    try {
      const saved = localStorage.getItem('lang');
      if (saved && STT_LANGS[saved]) {
        $('language').value = saved;
        applyLanguage(saved);
      }
    } catch (e) {}
    // persona follows the selected voice — sync it so replies match the speaker
    try {
      if (audioQ.fishVoice) ws.send(JSON.stringify({ type: 'set_voice', value: audioQ.fishVoice }));
    } catch (e) {}
    // chosen response mode rides the socket so the very next reply uses it
    try { ws.send(JSON.stringify({ type: 'set_response_mode', value: respMode })); } catch (e) {}
  };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === 'ready') { if (running) setState('LISTENING'); else setState('IDLE'); }
    else if (m.type === 'status') {
      setState(m.state, m.note);
      if (m.state === 'THINKING' || m.state === 'SPEAKING') {
        if (!$('aiTyping')) showTypingIndicator();
      }
    }
    else if (m.type === 'llm_token') {
      fullReply += m.token;
      // Live only: paint words as they're spoken in sync with audio clock.
      // Fish modes keep rendering per finished sentence via onSentenceStart (see audio.js).
      if (audioQ.engine === 'gemini') {
        try { LiveCaptions.push(m.token); } catch (e) {}
      }
    }
    else if (m.type === 'notice') {
      // transient center-text note ("Checking..."): display only — never
      // spoken, never added to transcript/history (those come from llm_done).
      setAiLine(m.text || 'Checking...');
    }
    else if (m.type === 'tts_start_hint') {
      fullReply = '';
      if (audioQ.engine === 'gemini') { try { LiveCaptions.start(); } catch (e) {} }
      setState('SPEAKING');
      if (!$('aiTyping')) showTypingIndicator();
    }
    else if (m.type === 'tts_sentence') {
      if (m.turn === 'greet' && userTurns > 0) return; // Drop reconnect greeting audio mid-session
      const stale = !!(m.turn && audioQ.deadTurns.has(m.turn));
      if (m.lat && !stale) audioQ.turnLat = m.lat;  // server stages for the latency block
      audioQ.enqueue(m.text, { stream: !!m.first, turn: m.turn });
    }
    else if (m.type === 'live_audio_chunk') {
      audioQ.playLivePcmChunk(m.data, 24000, m.turn);
    }
    else if (m.type === 'response_mode') { labelFor(m.value); log('[AI] mode confirmed:', respModeLabel); }
    else if (m.type === 'llm_done') {
      hideTypingIndicator();
      fullReply = '';
      const displayText = m.display_text || m.text;
      // Live center line mirrors the voice: latest chunk only (fish path keeps
      // its own per-sentence display and is untouched by liveChunkText).
      const centerText = m.live ? (liveChunkText(displayText) || displayText) : displayText;
      if (!m.greeting || userTurns === 0) {
        addTurn('Partner', displayText);
      }
      if (m.live) {
        // Live ticker owns the center line until the voice drains: never dump
        // the paragraph here. Hand it the final text and let it converge.
        // No dissolve here either — audioQ.onDrained fades the line when the
        // voice actually finishes (a timer now would fight the ticker).
        if (!m.partial && state !== 'INTERRUPTED') {
          try { LiveCaptions.finish(displayText); } catch (e) { setAiLine(centerText); }
        } else {
          try { LiveCaptions.stop(); } catch (e) {}
        }
      }
      if (m.correction && m.correction.length) log('[COACH]', JSON.stringify(m.correction));
      if (!audioQ.busy) {
        if (!m.partial && state !== 'INTERRUPTED') {
          setAiLine(centerText);
          smokeDissolveAiLine(1400);
        }
        if (running && state !== 'INTERRUPTED') setState('LISTENING');
      }
    } else if (m.type === 'llm_cancelled') {
      hideTypingIndicator();
      fullReply = '';
      try { LiveCaptions.stop(); } catch (e) {}
      setAiLine('');
    }
    else if (m.type === 'mode') {
      if ($('mode') && (m.mode === 'free' || m.mode === 'practice')) {
        $('mode').value = m.mode;
        updateModeUI();
      }
      log('[MODE]', m.mode);
    }
    else if (m.type === 'summary') { showSummary(m); }
    else if (m.type === 'error') {
      hideTypingIndicator();
      log(`[${m.scope}] error:`, m.message);
      if (running) setState('LISTENING');
    }
  };
  ws.onclose = () => {
    log('[WS] closed — reconnect in 2s');
    ws = null;
    if (running) setTimeout(connect, 2000);
  };
  if (!window.__wsPingTimer) {
    window.__wsPingTimer = setInterval(() => {
      try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'ping' })); } catch (e) {}
    }, 20000);
  }
}

document.addEventListener('DOMContentLoaded', () => { loadConfig(); loadMicDevices(); initChatSmoke(); icons(); });

function showSummary(s) {
  $('feedback').textContent =
`Duration ${s.duration_min} min · ${s.words_spoken} words · ${s.turns} turns
Fluency ${s.fluency} · Grammar ${s.grammar} · Vocab ${s.vocabulary}
Improve: ${s.main_improvement}
Try: "${s.useful_phrase}"
Pronunciation: ${s.pronunciation_focus}
Next: ${s.next_goal}`;
  if (s.spoken_feedback) audioQ.enqueue(s.spoken_feedback);
}

function normText(s) {
  return (s || '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s']/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}
function sameText(a, b, thr = 0.75) {
  const x = normText(a), y = normText(b);
  if (!x || !y) return false;
  if (x === y) return true;
  const xs = new Set(x.split(' ').filter(Boolean));
  const ys = y.split(' ').filter(Boolean);
  if (!ys.length || !xs.size) return false;
  let hit = 0;
  for (const w of ys) if (xs.has(w)) hit++;
  return hit / Math.max(ys.length, 1) >= thr;
}
function sendText(text) {
  if (!ws || ws.readyState !== 1) return;
  text = (text || '').trim();
  if (!text) return;
  // Guard against duplicate send of the exact same utterance within 1.2s
  if (lastSentText && (performance.now() - lastSentAt < 1200) && sameText(text, lastSentText, 0.85)) {
    log('[TURN] duplicate turn suppressed:', JSON.stringify(text));
    return;
  }
  audioQ.cancel(); // Cancel any superseded in-flight audio from prior turn
  lastSentText = text; lastSentAt = performance.now();
  addTurn('You', text);
  showTypingIndicator();
  userSaid(text);
  userWords += text.split(/\s+/).length;
  userTurns += 1;
  bumpStats();
  hearNote('');
  fullReply = '';
  try { LiveCaptions.reset(); } catch (e) {}
  setAiLine('');
  // latency anchors: the client clock marks the moment the user stopped talking
  turnSeq += 1;
  const turnId = String(turnSeq);
  audioQ._turnId = turnId;   // string: the server echoes ids back as strings
  audioQ._turnT0 = performance.now();
  audioQ._expectFirst = true;
  audioQ.turnLat = null;
  ws.send(JSON.stringify({ type: 'user_transcript', text, t0: Date.now(), turn_id: turnId }));
}
// fire the turn the moment the user stops talking (VAD end-of-turn), using the
// latest interim transcript — this is what makes the reply feel instant
function commitTurnIfReady() {
  if (!lastInterim) return;
  if (performance.now() - lastInterimAt > 2500) return;      // stale interim
  if (lastInterim.trim().length < 1) return;
  if (lastSentText && (performance.now() - lastSentAt < 1200) && sameText(lastInterim, lastSentText, 0.85)) return;
  const text = lastInterim.trim();
  awaitingFinal = true;
  lastInterim = '';
  log('[STT] speech-end → instant turn', JSON.stringify(text.slice(0, 80)));
  sendText(text);
}

let recogRestartTimer = null;
let recogFatal = false;

function setupRecog() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { hearNote('Speech recognition not supported here — use Chrome, or type below.'); return null; }
  const r = new SR();
  r.lang = sttLang; r.interimResults = true; r.continuous = true; r.maxAlternatives = 1;
  recogFatal = false;
  clearTimeout(recogRestartTimer);

  r.onresult = (ev) => {
    if (micMuted || !listening) return; // deaf while mic-muted: no turns, no interrupts
    let interim = '', fin = '', conf = 1;
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const res = ev.results[i];
      if (res.isFinal) { fin += res[0].transcript; conf = Math.min(conf, res[0].confidence || 0.9); }
      else interim += res[0].transcript;
    }
    if (interim) {
      lastInterim = interim; lastInterimAt = performance.now();
      userSaid(interim);
      try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'partial', text: interim })); } catch (e) {}
      // auto-interrupt on real speech
      if (aiSpeaking && interim.trim().length > 0) bargeIn();
    }
    if (fin && fin.trim()) {
      const text = fin.trim();
      // room noise / stray fragments: low confidence AND near-empty
      if (conf < 0.25 && text.length < 2) {
        log('[STT] dropped as noise', JSON.stringify(text), 'conf=' + conf.toFixed(2));
        lastInterim = '';
        return;
      }
      // if already answered from VAD speech-end commit:
      if (awaitingFinal) {
        log('[STT] final matched early commit — suppressing duplicate');
        awaitingFinal = false;
        lastInterim = '';
        lastInterimAt = 0;
        userSaid(text);
        return;
      }
      awaitingFinal = false;
      lastInterim = '';
      lastInterimAt = 0;
      userSaid(text);
      log('[STT] final', JSON.stringify(text.slice(0, 100)), 'conf=' + conf.toFixed(2));
      if (conf < 0.35) hearNote('I did not catch that clearly — say it once more?');
      sendText(text);
    }
  };
  r.onerror = (e) => {
    log('[STT] error', e.error);
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      recogFatal = true;
      listening = false;
      hearNote('Mic blocked — allow microphone access, then press mic again.');
    } else if (e.error === 'no-speech') {
      // transient silence — normal, let onend restart smoothly
    } else if (e.error === 'network') {
      // transient network interruption with cloud speech service
    } else if (e.error === 'audio-capture') {
      recogFatal = true;
      listening = false;
      hearNote('No microphone found — plug one in, then press mic again.');
    }
  };
  r.onaudiostart = () => log('[STT] mic stream opened');
  r.onaudioend = () => log('[STT] mic stream closed');
  r.onend = () => {
    if (listening && running && !recogFatal) {
      clearTimeout(recogRestartTimer);
      recogRestartTimer = setTimeout(() => {
        if (listening && running && !recogFatal) {
          try { r.start(); } catch (e) {
            setTimeout(() => { if (listening && running && !recogFatal) { try { r.start(); } catch (err) {} } }, 500);
          }
        }
      }, 350);
    }
  };
  return r;
}

// --- mic device picker + mic test (diagnoses "mic not working") ---
async function loadMicDevices() {
  const sel = $('micDevice');
  if (!sel || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
  try {
    const devs = (await navigator.mediaDevices.enumerateDevices())
      .filter(d => d.kind === 'audioinput');
    sel.innerHTML = '';
    if (!devs.length) {
      const o = document.createElement('option');
      o.value = ''; o.textContent = 'No microphone found';
      sel.appendChild(o);
      return;
    }
    let saved = '';
    try { saved = localStorage.getItem('micDevice') || ''; } catch (e) {}
    devs.forEach((d, i) => {
      const o = document.createElement('option');
      o.value = d.deviceId || '';
      o.textContent = d.label || ('Microphone ' + (i + 1));
      sel.appendChild(o);
    });
    if (saved && [...sel.options].some(o => o.value === saved)) sel.value = saved;
    log('[VAD] microphones:', devs.length, devs[0] && devs[0].label ? '(' + devs.map(d => d.label).join(' | ').slice(0, 120) + ')' : '(labels hidden until permission granted)');
  } catch (e) {
    log('[VAD] cannot list microphones:', e);
  }
}
function selectedMicDevice() {
  try {
    const v = $('micDevice') && $('micDevice').value;
    return v || '';
  } catch (e) { return ''; }
}

function bargeIn() {
  audioQ.cancel();
  hideTypingIndicator();
  try { LiveCaptions.stop(); } catch (e) {}
  setState('INTERRUPTED');
  setAiLine('');
  try { ws.send(JSON.stringify({ type: 'barge_in' })); } catch (e) {}
  fullReply = '';
  setTimeout(() => { if (state === 'INTERRUPTED') setState('LISTENING'); }, 600);
}

function sustainedBargeIn() {
  // VAD already requires speech above the noise floor; this extra beat
  // keeps coughs/clicks from cutting the AI off mid-sentence
  clearTimeout(speechTimer);
  speechTimer = setTimeout(() => {
    try {
      if (!micMuted && vad && vad.speaking && aiSpeaking) bargeIn();
    } catch (e) {}
  }, 200);
}

async function startSession() {
  running = true;
  await audioQ.unlock();
  await connect();
  $('chatInput').classList.remove('hidden');
  $('interruptBtn').classList.remove('hidden');
  $('muteBtn').classList.remove('hidden');
  $('captionBtn').classList.remove('hidden');
  $('micMuteBtn').classList.remove('hidden');
  setIcon($('startBtn'), 'square');
  $('startBtn').classList.add('live');
  $('interruptBtn').disabled = false;
  $('endBtn').disabled = false;
  setState('LISTENING');
  startClock();
  vad = new EnergyVAD({
    onSpeechStart: sustainedBargeIn,
    onSpeechEnd: () => { clearTimeout(speechTimer); commitTurnIfReady(); }
  });
  const micOk = await vad.start(selectedMicDevice());
  if (!micOk) {
    const why = vad.lastError === 'NotAllowedError' ? 'Mic blocked — allow microphone access in the browser.'
      : vad.lastError === 'NotFoundError' ? 'No microphone found on this device.'
      : 'Microphone unavailable (' + (vad.lastError || 'unknown') + '). You can still type below.';
    hearNote(why, 6000);
    log('[VAD]', why);
  } else {
    loadMicDevices();
  }
  recog = setupRecog();
  if (recog) {
    listening = true;
    try {
      recog.start();
    } catch (e) {
      log('[STT] start failed', e);
    }
  }
  log('[VAD] always-listening ON • auto-interrupt ON • STT ' + sttLang + ' • [TTS] Fish S2.1 Pro Free');
  if (!window.__micLiveTimer) {
    window.__micLiveTimer = setInterval(() => {
      try {
        const on = (window.__micLevel || 0) > 0.005;
        $('micLive').classList.toggle('on', !!on && listening && !micMuted);
      } catch (e) {}
    }, 120);
  }
}

function stopSession() {
  running = false;
  listening = false;
  recogFatal = true;
  clearTimeout(recogRestartTimer);
  lastInterim = ''; lastInterimAt = 0; awaitingFinal = false; lastSentText = '';
  userSaid('');
  try { LiveCaptions.stop(); } catch (e) {}
  setAiLine('');
  hideTypingIndicator();
  clearTimeout(speechTimer);
  try { recog && recog.stop(); } catch (e) {}
  recog = null;
  try { vad && vad.stop(); } catch (e) {}
  vad = null;
  try { window.__micLevel = 0; } catch (e) {}
  $('micLive').classList.remove('on');
  audioQ.cancel();
  stopClock();
  setIcon($('startBtn'), 'play');
  $('startBtn').classList.remove('live');
  $('interruptBtn').disabled = true;
  $('interruptBtn').classList.add('hidden');
  $('muteBtn').classList.add('hidden');
  $('captionBtn').classList.add('hidden');
  $('micMuteBtn').classList.add('hidden');
  $('chatInput').classList.add('hidden');
  try { if (ws) { ws.close(); ws = null; } } catch (e) {}
  setState('IDLE');
}

$('startBtn').onclick = () => { running ? stopSession() : startSession(); };
$('interruptBtn').onclick = () => { if (aiSpeaking) bargeIn(); };
$('muteBtn').onclick = (e) => {
  muted = !muted;
  audioQ.setMuted(muted);
  setIcon($('muteBtn'), muted ? 'volume-x' : 'volume-2');
  log('[AUDIO] speaker', muted ? 'muted' : 'unmuted');
};
$('micMuteBtn').onclick = (e) => {
  micMuted = !micMuted;
  if (vad) vad.setMuted(micMuted);
  if (micMuted) {
    lastInterim = '';
    lastInterimAt = 0;
    awaitingFinal = false;
    try { recog && recog.abort(); } catch (err) {}
    $('micLive').classList.remove('on');
    hearNote('Mic muted — I cannot hear you.');
  } else {
    lastInterim = '';
    lastInterimAt = 0;
    awaitingFinal = false;
    hearNote('');
    if (recog && listening && running) {
      try { recog.start(); } catch (err) {}
    }
  }
  setIcon($('micMuteBtn'), micMuted ? 'mic-off' : 'mic');
  log('[VAD] mic', micMuted ? 'muted (deaf to speech + interrupts)' : 'live');
};
$('captionBtn').onclick = (e) => {
  captionsOn = !captionsOn;
  try { document.querySelector('.stage').classList.toggle('nocaptions', !captionsOn); } catch (err) {}
  $('captionBtn').classList.toggle('off', !captionsOn);
  log('[UI] captions', captionsOn ? 'shown' : 'hidden');
};
$('sendBtn').onclick = () => { const t = $('textInput').value; $('textInput').value = ''; if (aiSpeaking) bargeIn(); sendText(t); };
$('textInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('sendBtn').click(); });
$('mode').onchange = (e) => {
  updateModeUI();
  try { localStorage.setItem('mode', e.target.value); } catch (err) {}
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: 'set_mode', mode: e.target.value, scenario: $('scenario') ? $('scenario').value : '' }));
  }
};
$('scenario').onchange = (e) => ws && ws.send(JSON.stringify({ type: 'set_mode', mode: $('mode').value, scenario: e.target.value }));
$('correction').onchange = (e) => ws && ws.send(JSON.stringify({ type: 'set_correction', value: e.target.value }));
$('level').onchange = (e) => ws && ws.send(JSON.stringify({ type: 'set_level', value: e.target.value }));
const STT_LANGS = { english: 'en-IN', hindi: 'hi-IN', hinglish: 'en-IN' };
function applyLanguage(v, silent) {
  sttLang = STT_LANGS[v] || 'en-IN';
  try { localStorage.setItem('lang', v); } catch (e) {}
  if (ws && ws.readyState === 1 && !silent) ws.send(JSON.stringify({ type: 'set_language', value: v }));
  if (recog && listening) {
    // restart recognition so the new language takes effect
    try { recog.lang = sttLang; recog.stop(); } catch (e) {}
  }
  log('[LANG] →', v, '(' + sttLang + ')');
  if (v === 'hindi') log('[LANG] tip: pick the Hindi voice in settings for a native accent.');
}
$('language').onchange = (e) => applyLanguage(e.target.value);
$('anim').onchange = (e) => {
  try { window.Orb && Orb.setStyle(e.target.value); } catch (err) {}
  log('[UI] animation →', e.target.value);
};
$('voice').onchange = (e) => {
  audioQ.fishVoice = e.target.value || '';
  const storageKey = (audioQ.engine === 'gemini') ? 'geminiVoice' : 'fishVoice';
  try { localStorage.setItem(storageKey, audioQ.fishVoice); } catch (err) {}
  try { ws && ws.readyState === 1 && ws.send(JSON.stringify({ type: 'set_voice', value: audioQ.fishVoice })); } catch (err) {}
  const label = e.target.selectedOptions[0] ? e.target.selectedOptions[0].textContent : e.target.value;
  log(`[TTS] ${audioQ.engine === 'gemini' ? 'Gemini Live' : 'Fish Audio'} voice →`, label);
};
$('micDevice').onchange = (e) => {
  try { localStorage.setItem('micDevice', e.target.value || ''); } catch (err) {}
  log('[VAD] microphone →', e.target.selectedOptions[0] ? e.target.selectedOptions[0].textContent : e.target.value);
};
$('micTestBtn').onclick = async () => {
  // standalone 2.5s capture test: proves the mic path independent of STT
  const st = $('micTestStatus');
  st.textContent = 'Listening for 2.5s — speak now…';
  let stream = null, ctx = null;
  try {
    const dev = selectedMicDevice();
    stream = await navigator.mediaDevices.getUserMedia({ audio: dev ? { deviceId: { exact: dev } } : true });
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = ctx.createMediaStreamSource(stream);
    const an = ctx.createAnalyser();
    an.fftSize = 1024;
    src.connect(an);
    const buf = new Float32Array(an.fftSize);
    let peak = 0;
    const t0 = performance.now();
    await new Promise((res) => {
      const tick = () => {
        an.getFloatTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        peak = Math.max(peak, Math.sqrt(sum / buf.length));
        if (performance.now() - t0 < 2500) requestAnimationFrame(tick);
        else res();
      };
      tick();
    });
    const pct = Math.round(Math.min(1, peak * 8) * 100);
    const verdict = pct < 3 ? 'nothing heard — wrong mic or muted in system settings' : 'mic is capturing (peak ' + pct + '%)';
    st.textContent = verdict;
    log('[VAD] mic test peak:', pct + '% —', verdict);
  } catch (e) {
    st.textContent = 'Mic test failed: ' + ((e && e.name) || e);
    log('[VAD] mic test failed:', (e && e.name) || e);
  } finally {
    try { stream && stream.getTracks().forEach(t => t.stop()); } catch (e) {}
    try { ctx && ctx.close(); } catch (e) {}
  }
};
function closeDrawers() {
  $('panel').classList.add('hidden');
  $('history').classList.add('hidden');
  $('scrim').classList.add('hidden');
}
function openPanel(o) {
  if (o) $('history').classList.add('hidden');
  $('panel').classList.toggle('hidden', !o);
  $('scrim').classList.toggle('hidden', !o);
  icons();
}
function openHistory(o) {
  if (o) $('panel').classList.add('hidden');
  $('history').classList.toggle('hidden', !o);
  $('scrim').classList.toggle('hidden', !o);
  if (o) {
    initChatSmoke();
    refreshEmptyHint();
    setTimeout(() => {
      const t = $('transcript');
      if (t) t.scrollTop = t.scrollHeight;
    }, 40);
  }
  icons();
}
$('settingsBtn').onclick = () => openPanel(true);
$('panelClose').onclick = () => openPanel(false);
$('historyBtn').onclick = () => {
  // icon toggles the history drawer both ways
  openHistory($('history').classList.contains('hidden'));
};
$('scrim').onclick = closeDrawers;
$('fishTestBtn').onclick = async () => {
  await audioQ.unlock();
  const isGemini = (audioQ.engine === 'gemini');
  $('fishStatus').textContent = isGemini ? 'Testing Gemini Live voice…' : 'Testing Fish voice…';
  try {
    const eng = encodeURIComponent(audioQ.engine || 'fish');
    const r = await fetch('/api/tts/test?voice=' + encodeURIComponent(audioQ.fishVoice || '') + '&engine=' + eng);
    const ct = r.headers.get('content-type') || '';
    if (r.ok && (ct.includes('audio') || ct.includes('wav') || ct.includes('mpeg'))) {
      const blob = await r.blob();
      log('[AUDIO] test clip bytes:', blob.size, `(${audioQ.engine})`);
      const url = URL.createObjectURL(blob);
      const res = await audioQ.playUrl(url);
      try { URL.revokeObjectURL(url); } catch (e) {}
      if (res.ok) {
        const label = ($('voice').selectedOptions && $('voice').selectedOptions[0])
          ? $('voice').selectedOptions[0].textContent : 'Voice';
        $('fishStatus').textContent = (isGemini ? 'Gemini Live OK — playing ' : 'Fish OK — playing ') + label + '.';
        log('[TTS] voice test OK:', label);
      } else {
        $('fishStatus').textContent = 'Got audio but browser blocked playback (' + res.reason + '). Check volume + site permission.';
        log('[TTS] test playback blocked:', res.reason);
      }
    } else {
      let msg = `Voice synthesis error (${r.status})`;
      try { const j = await r.json(); if (j.error) msg = j.error; } catch (e) { msg = (await r.text()).slice(0, 160); }
      $('fishStatus').textContent = msg;
      log('[TTS] voice test:', msg);
    }
  } catch (e) { $('fishStatus').textContent = 'Test error: ' + e; }
};
$('endBtn').onclick = () => ws && ws.send(JSON.stringify({ type: 'end' }));
$('challengeBtn').onclick = async () => {
  const c = await (await fetch('/api/challenge/today')).json();
  addTurn('Partner', `Today's challenge: ${c.prompt}`);
  setAiLine(c.prompt);
};
