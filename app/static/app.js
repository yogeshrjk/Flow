/* Always-listening client: mic open from Start, auto-interrupt always on.
   mic -> local VAD -> WebSpeech STT (en-IN) -> WS -> streaming LLM -> Fish TTS. */
const APP_BUILD = 111; // bump with every app.js change; printed on session start so bug reports identify the build
const $ = (id) => document.getElementById(id);
let ws = null, sid = 's' + Math.random().toString(36).slice(2, 8);
let state = 'IDLE';
let recog = null, vad = null, audioQ = new AudioQueue();
let listening = false, aiSpeaking = false, muted = false, running = false;
let micMuted = false, captionsOn = true;
let llmPending = false;
let fullReply = '';
let userWords = 0, userTurns = 0, sessStart = 0, clockTimer = null;
let speechTimer = null;
let userFadeTimer = null;
let sttLang = 'en-IN'; // BCP-47 code for the browser-STT fallback; follows the preferred language
// instant-turn state: commit on speech-end using the interim transcript instead
// of waiting ~1s for the STT engine to finalize
let lastInterim = '', lastInterimAt = 0, awaitingFinal = false;
let lastSentText = '', lastSentAt = 0;
// AI Response mode (friendly labels only — the provider behind them is server-side)
let respModes = [], respMode = 'fast', respModeLabel = 'Fast Response';
let turnSeq = 0;
const fishLibraryStates = new Map();
let fishLibraryLanguage = 'en', fishLibrarySearchTimer = null;
let fishLibraryRequestToken = 0, fishLibraryPreview = null, fishLibraryPreviewButton = null;
let fishLibraryTab = 'explore';
const fishLibraryFilters = { gender: '', age: '', tags: [], qualities: [] };
const FISH_BOOKMARKS_KEY = 'flow_fish_voice_bookmarks_v1';
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
    if (un.length) {
      note.textContent = un.join(' + ') + ' is unavailable right now — the other option is used instead.';
      note.classList.remove('hidden');
    } else {
      note.textContent = '';
      note.classList.add('hidden');
    }
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

// Preferred-language picker, driven by /api/config (18 languages). The pick
// is only the DEFAULT: every turn answers in whatever language was actually
// spoken (server-side detection), falling back to this.
const LANG_FALLBACK = [
  { id: 'english', label: 'English', stt: 'en-IN' },
  { id: 'hindi', label: 'Hindi', stt: 'hi-IN' },
];
function serverLanguages() {
  try {
    const list = serverConfig && serverConfig.languages;
    if (Array.isArray(list) && list.length) return list;
  } catch (e) {}
  return LANG_FALLBACK;
}
function sttCodeFor(langId) {
  const found = serverLanguages().filter(l => l.id === langId)[0];
  return (found && found.stt) || 'en-IN';
}
function renderLanguageOptions(cfg) {
  const sel = $('language');
  if (!sel) return;
  const langs = (cfg && Array.isArray(cfg.languages) && cfg.languages.length)
    ? cfg.languages : LANG_FALLBACK;
  let saved = 'english';
  try { saved = localStorage.getItem('lang') || 'english'; } catch (e) {}
  if (saved === 'hinglish') saved = 'english'; // retired option: multilingual detection covers the mix
  if (!langs.some(l => l.id === saved)) saved = 'english';
  sel.innerHTML = '';
  langs.forEach(l => {
    const o = document.createElement('option');
    o.value = l.id;
    o.textContent = l.label || l.id;
    sel.appendChild(o);
  });
  sel.value = saved;
  sttLang = sttCodeFor(saved);
}

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

  let saved = '';
  try {
    saved = localStorage.getItem(storageKey) || '';
  } catch (e) {}
  const customFishVoice = !isGeminiLive && saved && !voices.some(x => x.id === saved);
  let customFishName = '';
  if (customFishVoice) {
    try { customFishName = localStorage.getItem('fishVoiceName') || 'Public voice'; }
    catch (e) { customFishName = 'Public voice'; }
    const option = document.createElement('option');
    option.value = saved;
    option.textContent = customFishName;
    v.appendChild(option);
  }
  const pick = saved && (customFishVoice || voices.some(x => x.id === saved)) ? saved : defaultVoice;
  if (pick) v.value = pick;
  audioQ.fishVoice = customFishVoice ? saved : (v.value || '');
  const selectedName = $('selectedVoiceName');
  if (selectedName) {
    if (customFishVoice) {
      selectedName.textContent = customFishName;
    } else {
      const option = [...v.options].find(x => x.value === audioQ.fishVoice);
      selectedName.textContent = option ? option.textContent : 'Browse the Fish Audio library';
    }
  }

  const geminiVoiceFld = $('geminiVoiceFld');
  const voicePickerOpen = $('voicePickerOpen');
  if (geminiVoiceFld) {
    geminiVoiceFld.classList.toggle('hidden', !isGeminiLive);
  }
  if (voicePickerOpen) {
    voicePickerOpen.classList.toggle('hidden', isGeminiLive);
  }
  if (isGeminiLive && typeof voiceLibraryState !== 'undefined' && voiceLibraryState.isOpen) {
    closeVoicePicker();
  }
  icons();

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

const CHAT_STORAGE_KEY = 'flow_chat_history_v1';

function getStoredChatHistory() {
  try {
    const raw = localStorage.getItem(CHAT_STORAGE_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch (e) {
    return [];
  }
}

function saveStoredChatHistory(history) {
  try {
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify((history || []).slice(-40)));
  } catch (e) {}
}

function loadChatHistoryUI() {
  const t = $('transcript');
  if (!t) return;
  const history = getStoredChatHistory();
  t.innerHTML = '';
  if (history.length > 0) {
    const divider = document.createElement('div');
    divider.className = 'history-divider';
    divider.innerHTML = '<span>Previous Chat</span>';
    t.appendChild(divider);

    history.forEach(item => {
      const d = document.createElement('div');
      d.className = 'turn ' + (item.who === 'You' ? 'you' : 'ai') + ' past';
      d.textContent = plainText(item.text);
      t.appendChild(d);
    });
    t.scrollTop = t.scrollHeight;
  }
  refreshEmptyHint();
}

function clearChatHistory() {
  try {
    localStorage.removeItem(CHAT_STORAGE_KEY);
  } catch (e) {}
  const t = $('transcript');
  if (t) t.innerHTML = '';
  refreshEmptyHint();
  if (ws && ws.readyState === 1) {
    try { ws.send(JSON.stringify({ type: 'clear_history' })); } catch (e) {}
  }
  log('[HISTORY] conversation cleared');
}

function addTurn(who, text, save = true) {
  hideTypingIndicator();
  const clean = plainText(text);
  if (!clean.trim()) return;

  const d = document.createElement('div');
  d.className = 'turn ' + (who === 'You' ? 'you' : 'ai');
  d.textContent = clean;
  const t = $('transcript');
  if (t) {
    t.appendChild(d);
    if (window.gsap) {
      gsap.fromTo(d,
        { opacity: 0, y: 14, filter: 'blur(8px)', scale: 0.98 },
        { opacity: 1, y: 0, filter: 'blur(0px)', scale: 1, duration: 0.42, ease: 'power2.out' }
      );
    }
    t.scrollTop = t.scrollHeight;
  }
  refreshEmptyHint();

  if (save) {
    const hist = getStoredChatHistory();
    hist.push({ who, text: clean, t: Date.now() });
    saveStoredChatHistory(hist);
  }
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

function fishLibraryKey(language, title) {
  return language + '\n' + title.trim().toLocaleLowerCase();
}
function fishLibraryState(language, title) {
  const key = fishLibraryKey(language, title);
  if (!fishLibraryStates.has(key)) {
    fishLibraryStates.set(key, {
      language, title: title.trim(), pages: [], total: 0, hasMore: true,
      windowLimited: false, loadingPage: 0, error: '', errorPage: 0,
      pending: new Map()
    });
  }
  return fishLibraryStates.get(key);
}
function getFishBookmarks() {
  try {
    const saved = JSON.parse(localStorage.getItem(FISH_BOOKMARKS_KEY) || '[]');
    return Array.isArray(saved) ? saved.filter(voice => voice && typeof voice.id === 'string') : [];
  } catch (error) {
    return [];
  }
}
function saveFishBookmarks(bookmarks) {
  try {
    localStorage.setItem(FISH_BOOKMARKS_KEY, JSON.stringify(bookmarks));
    return true;
  } catch (error) {
    $('fishLibraryNote').textContent = 'Could not save bookmarks in this browser.';
    log('[FISH LIBRARY] bookmark save failed:', error && error.name || error);
    return false;
  }
}
function defaultFishVoices() {
  return (serverConfig && serverConfig.fish_voices || []).map(voice => {
    const gender = voice.gender || '';
    const label = gender ? `${gender[0].toUpperCase()}${gender.slice(1)}` : 'AI';
    return {
      id: voice.id,
      name: voice.label,
      description: voice.description || `${label} voice preset.`,
      languages: [],
      tags: voice.tags || (gender ? [label, 'Built-in'] : ['Built-in']),
      creator: {},
      samples: [],
      isDefault: true
    };
  });
}
const FISH_VOICE_AGES = [
  ['Child', /\b(child|kid|children)\b/i],
  ['Teen', /\b(teen|teenager|adolescent)\b/i],
  ['Young', /\byoung\b/i],
  ['Adult', /\badult\b/i],
  ['Middle-aged', /\bmiddle[\s-]+aged\b/i],
  ['Mature', /\bmature\b/i],
  ['Older', /\bolder|senior\b/i],
  ['Elderly', /\belderly\b/i]
];
const FISH_VOICE_QUALITIES = [
  'Warm', 'Bright', 'Deep', 'Soft', 'Clear', 'Expressive', 'Energetic',
  'Calm', 'Smooth', 'Raspy', 'Breathy', 'Playful', 'Natural', 'Friendly',
  'Confident', 'Soothing', 'Professional', 'Conversational', 'Storytelling'
];
function fishVoiceText(voice) {
  return [voice.name, voice.description, ...(voice.tags || [])].filter(Boolean).join(' ');
}
function fishVoiceGender(voice) {
  const text = fishVoiceText(voice);
  if (/\b(female|woman|women|girl|feminine)\b/i.test(text)) return 'female';
  if (/\b(male|man|men|boy|masculine)\b/i.test(text)) return 'male';
  return '';
}
function fishVoiceAge(voice) {
  const text = fishVoiceText(voice);
  const match = FISH_VOICE_AGES.find(([, pattern]) => pattern.test(text));
  return match ? match[0] : '';
}
function fishVoiceQualities(voice) {
  const text = fishVoiceText(voice);
  return FISH_VOICE_QUALITIES.filter(quality =>
    new RegExp(`\\b${quality.toLocaleLowerCase()}\\b`, 'i').test(text)
  );
}
function fishLibraryLoadedVoices(state) {
  const seen = new Set();
  const voices = [];
  state.pages.forEach(page => (page && page.voices || []).forEach(voice => {
    if (voice.id && !seen.has(voice.id)) {
      seen.add(voice.id);
      voices.push(voice);
    }
  }));
  return voices;
}
function renderVoiceFilterOptions(containerId, values, selectedValues, onChange, emptyText) {
  const container = $(containerId);
  container.replaceChildren();
  if (!values.length) {
    const empty = document.createElement('span');
    empty.className = 'voice-filter-empty';
    empty.textContent = emptyText;
    container.appendChild(empty);
    return;
  }
  values.forEach(value => {
    const label = document.createElement('label');
    label.className = 'voice-filter-option';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = value;
    input.checked = selectedValues.includes(value);
    input.onchange = () => onChange(value, input.checked);
    const text = document.createElement('span');
    text.textContent = value;
    label.append(input, text);
    container.appendChild(label);
  });
}
function updateFishVoiceFilterOptions(voices) {
  const genders = [...new Set(voices.map(fishVoiceGender).filter(Boolean))].sort();
  const genderSelect = $('voiceFilterGender');
  const selectedGender = fishLibraryFilters.gender;
  genderSelect.replaceChildren(new Option('Any gender', ''));
  genders.forEach(gender => genderSelect.add(new Option(
    `${gender[0].toUpperCase()}${gender.slice(1)} (${voices.filter(voice => fishVoiceGender(voice) === gender).length})`,
    gender
  )));
  genderSelect.value = genders.includes(selectedGender) ? selectedGender : '';
  fishLibraryFilters.gender = genderSelect.value;
  $('voiceFilterGenderNote').textContent = genders.length ? '' : 'No explicit gender labels among loaded voices.';
  $('voiceFilterGenderNote').classList.toggle('hidden', !!genders.length);

  const ages = [...new Set(voices.map(fishVoiceAge).filter(Boolean))]
    .sort((a, b) => FISH_VOICE_AGES.findIndex(([age]) => age === a) - FISH_VOICE_AGES.findIndex(([age]) => age === b));
  const ageSelect = $('voiceFilterAge');
  const selectedAge = fishLibraryFilters.age;
  ageSelect.replaceChildren(new Option('Any age', ''));
  ages.forEach(age => ageSelect.add(new Option(age, age)));
  ageSelect.value = ages.includes(selectedAge) ? selectedAge : '';
  fishLibraryFilters.age = ageSelect.value;
  $('voiceFilterAgeNote').textContent = ages.length ? '' : 'No explicit age labels among loaded voices.';
  $('voiceFilterAgeNote').classList.toggle('hidden', !!ages.length);

  const tags = [...new Set(voices.flatMap(voice => voice.tags || [])
    .map(tag => String(tag).trim())
    .filter(tag => tag && !['public', 'default'].includes(tag.toLocaleLowerCase())))].sort((a, b) => a.localeCompare(b));
  renderVoiceFilterOptions('voiceFilterTags', tags, fishLibraryFilters.tags, (tag, checked) => {
    fishLibraryFilters.tags = checked
      ? [...fishLibraryFilters.tags, tag]
      : fishLibraryFilters.tags.filter(item => item !== tag);
    renderFishLibrary();
  }, 'No tags are available on loaded voices.');

  const qualities = [...new Set(voices.flatMap(fishVoiceQualities))].sort((a, b) => a.localeCompare(b));
  renderVoiceFilterOptions('voiceFilterQualities', qualities, fishLibraryFilters.qualities, (quality, checked) => {
    fishLibraryFilters.qualities = checked
      ? [...fishLibraryFilters.qualities, quality]
      : fishLibraryFilters.qualities.filter(item => item !== quality);
    renderFishLibrary();
  }, 'No voice-quality labels are available on loaded voices.');
}
function activeFishVoiceFilterCount() {
  return (fishLibraryFilters.gender ? 1 : 0) + (fishLibraryFilters.age ? 1 : 0) +
    fishLibraryFilters.tags.length + fishLibraryFilters.qualities.length;
}
function setVoiceFilterOpen(open) {
  const overlay = $('voiceFilterOverlay');
  overlay.classList.toggle('hidden', !open);
  $('voiceFilterOpen').setAttribute('aria-expanded', String(open));
  if (open) setTimeout(() => $('voiceFilterGender').focus(), 0);
  else $('voiceFilterOpen').focus();
}
function resetFishVoiceFilters() {
  fishLibraryFilters.gender = '';
  fishLibraryFilters.age = '';
  fishLibraryFilters.tags = [];
  fishLibraryFilters.qualities = [];
  renderFishLibrary();
}
function setFishLibraryTab(tab) {
  fishLibraryTab = tab;
  if (tab !== 'explore' && !$('voiceFilterOverlay').classList.contains('hidden')) {
    $('voiceFilterOverlay').classList.add('hidden');
    $('voiceFilterOpen').setAttribute('aria-expanded', 'false');
  }
  const tabs = [
    ['explore', 'voiceTabExplore'],
    ['default', 'voiceTabDefault'],
    ['bookmarked', 'voiceTabBookmarked']
  ];
  tabs.forEach(([name, id]) => {
    const button = $(id);
    const active = name === tab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  const languageField = $('fishLibraryLanguage').closest('.fld');
  languageField.classList.toggle('hidden', tab === 'default');
  $('voiceFilterOpen').classList.toggle('hidden', tab !== 'explore');
  const more = $('fishLibraryMore'), retry = $('fishLibraryRetry');
  more.classList.toggle('hidden', tab !== 'explore');
  retry.classList.toggle('hidden', tab !== 'explore');
  if (tab === 'explore') {
    const state = fishLibraryState(fishLibraryLanguage, $('fishLibrarySearch').value || '');
    if (!state.pages.length && !state.loadingPage && !state.error) {
      loadFishLibraryPage(state.language, state.title, 1);
      return;
    }
  }
  renderFishLibrary();
}
function renderFishLibrary() {
  const title = $('fishLibrarySearch').value || '';
  const state = fishLibraryState(fishLibraryLanguage, title);
  const grid = $('fishLibraryGrid'), note = $('fishLibraryNote');
  const more = $('fishLibraryMore'), retry = $('fishLibraryRetry');
  let voices = [];
  if (fishLibraryTab === 'default') {
    voices = defaultFishVoices();
  } else if (fishLibraryTab === 'bookmarked') {
    voices = getFishBookmarks();
  } else {
    voices = fishLibraryLoadedVoices(state);
  }
  if (fishLibraryTab === 'explore') updateFishVoiceFilterOptions(voices);
  const query = title.trim().toLocaleLowerCase();
  const filtered = voices.filter((voice) => {
    const matchesQuery = !query || (voice.name || '').toLocaleLowerCase().includes(query);
    const matchesLanguage = fishLibraryTab === 'default' || !voice.languages?.length ||
      voice.languages.includes(fishLibraryLanguage);
    const gender = fishVoiceGender(voice);
    const age = fishVoiceAge(voice);
    const tags = (voice.tags || []).map(tag => String(tag).trim());
    const qualities = fishVoiceQualities(voice);
    const matchesFilters = fishLibraryTab !== 'explore' ||
      (!fishLibraryFilters.gender || gender === fishLibraryFilters.gender) &&
      (!fishLibraryFilters.age || age === fishLibraryFilters.age) &&
      fishLibraryFilters.tags.every(tag => tags.includes(tag)) &&
      fishLibraryFilters.qualities.every(quality => qualities.includes(quality));
    return matchesQuery && matchesLanguage && matchesFilters;
  });
  grid.replaceChildren();
  if (fishLibraryPreviewButton && !fishLibraryPreviewButton.isConnected) {
    stopFishLibraryPreview();
  }
  filtered.forEach((voice) => grid.appendChild(makeFishLibraryCard(voice)));
  icons();
  more.classList.toggle('hidden', fishLibraryTab !== 'explore' || !state.hasMore || !!state.error);
  more.disabled = fishLibraryTab === 'explore' && !!state.loadingPage;
  more.textContent = state.loadingPage ? 'Loading…' : 'Load more';
  retry.classList.toggle('hidden', fishLibraryTab !== 'explore' || !state.error);
  const filterCount = activeFishVoiceFilterCount();
  $('voiceFilterCount').textContent = String(filterCount);
  $('voiceFilterCount').classList.toggle('hidden', !filterCount);
  if (fishLibraryTab === 'default') {
    note.textContent = `${filtered.length} built-in ${filtered.length === 1 ? 'voice' : 'voices'}.`;
  } else if (fishLibraryTab === 'bookmarked') {
    note.textContent = filtered.length
      ? `${filtered.length} bookmarked ${filtered.length === 1 ? 'voice' : 'voices'}.`
      : 'Your bookmarked voices will appear here.';
  } else if (state.loadingPage && !voices.length) {
    note.textContent = 'Loading public voices…';
  } else if (state.error) {
    note.textContent = state.error;
  } else if (!filtered.length && state.pages.length) {
    note.textContent = 'No public voices found for this language and search.';
  } else if (state.windowLimited) {
    note.textContent = `Loaded ${voices.length} of ${state.total.toLocaleString()} public results. Fish Audio marks this catalogue window as limited, so additional public voices may not be accessible through this endpoint.`;
  } else if (voices.length && state.hasMore) {
    note.textContent = `Loaded ${voices.length} of ${state.total.toLocaleString()} public results.`;
  } else if (voices.length) {
    note.textContent = `Showing ${voices.length} public ${voices.length === 1 ? 'voice' : 'voices'}.`;
  } else {
    note.textContent = state.loadingPage ? 'Loading public voices…' : 'Choose a language to browse public voices.';
  }
}
function makeFishLibraryCard(voice) {
  const card = document.createElement('article');
  card.className = 'voice-library-card';
  const content = document.createElement('div');
  content.className = 'voice-library-content';
  
  const sample = (voice.samples || []).find((item) => item.audio);
  const isBookmarked = getFishBookmarks().some(bookmark => bookmark.id === voice.id);
  
  const previewBtn = document.createElement('button');
  previewBtn.type = 'button';
  previewBtn.className = 'voice-library-preview-avatar';
  previewBtn.title = 'Preview voice';
  previewBtn.setAttribute('aria-label', 'Preview voice');
  previewBtn.innerHTML = '<i data-lucide="play"></i>';
  previewBtn.disabled = !sample && !voice.isDefault;
  previewBtn.onclick = () => toggleFishLibraryPreview(voice, sample, previewBtn);
  content.appendChild(previewBtn);
  
  const info = document.createElement('div');
  info.className = 'voice-library-info';
  const head = document.createElement('div');
  head.className = 'voice-library-title';
  const name = document.createElement('span');
  name.textContent = voice.name || 'Unnamed voice';
  head.appendChild(name);
  
  const topActions = document.createElement('div');
  topActions.className = 'voice-card-top-actions';
  
  const use = document.createElement('button');
  use.type = 'button';
  use.className = 'voice-use-icon';
  use.title = 'Use this voice';
  use.setAttribute('aria-label', 'Use this voice');
  use.innerHTML = '<i data-lucide="check"></i>';
  use.onclick = () => useFishLibraryVoice(voice, use);
  
  const bookmark = document.createElement('button');
  bookmark.type = 'button';
  bookmark.className = 'voice-bookmark-icon';
  bookmark.title = isBookmarked ? 'Remove bookmark' : 'Bookmark voice';
  bookmark.setAttribute('aria-label', bookmark.title);
  bookmark.setAttribute('aria-pressed', String(isBookmarked));
  bookmark.innerHTML = `<i data-lucide="${isBookmarked ? 'bookmark-check' : 'bookmark'}"></i>`;
  bookmark.onclick = () => toggleFishVoiceBookmark(voice);
  
  topActions.append(use, bookmark);
  head.appendChild(topActions);
  info.appendChild(head);

  if (voice.description) {
    const description = document.createElement('p');
    description.className = 'voice-library-description';
    description.textContent = voice.description;
    description.title = voice.description;
    info.appendChild(description);
  }
  const metadata = [];
  if (voice.languages && voice.languages.length) metadata.push('Languages: ' + voice.languages.join(', '));
  if (voice.creator && voice.creator.name) metadata.push('By ' + voice.creator.name);
  if (metadata.length) {
    const details = document.createElement('div');
    details.className = 'voice-library-meta';
    details.textContent = metadata.join(' · ');
    details.title = metadata.join(' · ');
    info.appendChild(details);
  }
  content.appendChild(info);
  card.appendChild(content);
  const visibleTags = (voice.tags || []).filter(tag =>
    !['public', 'default'].includes(String(tag).trim().toLocaleLowerCase())
  );
  if (visibleTags.length) {
    const tags = document.createElement('div');
    tags.className = 'voice-library-tags';
    tags.title = visibleTags.join(', ');
    visibleTags.slice(0, 3).forEach((tag) => {
      const chip = document.createElement('span');
      chip.className = 'voice-library-tag';
      chip.textContent = tag;
      tags.appendChild(chip);
    });
    if (visibleTags.length > 3) {
      const extra = document.createElement('span');
      extra.className = 'voice-library-tag voice-library-tag-extra';
      extra.textContent = `+${visibleTags.length - 3}`;
      tags.appendChild(extra);
    }
    card.appendChild(tags);
  }
  return card;
}
async function toggleFishLibraryPreview(voice, sample, button) {
  if (fishLibraryPreview && fishLibraryPreviewButton === button) {
    if (fishLibraryPreview.paused) {
      try {
        await fishLibraryPreview.play();
        setFishPreviewButton(button, true);
      } catch (error) {
        log('[FISH LIBRARY] preview resume failed:', error && error.name || error);
      }
    } else {
      fishLibraryPreview.pause();
      setFishPreviewButton(button, false);
    }
    return;
  }
  stopFishLibraryPreview();
  try {
    let source = sample?.audio || '';
    let objectUrl = '';
    if (voice.isDefault) {
      const params = new URLSearchParams({ voice: voice.id, engine: 'fish' });
      const response = await fetch('/api/tts/test?' + params.toString());
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || `Voice preview failed (${response.status}).`);
      }
      objectUrl = URL.createObjectURL(await response.blob());
      source = objectUrl;
    }
    const audio = new Audio(source);
    fishLibraryPreview = audio;
    fishLibraryPreviewButton = button;
    button.classList.add('playing');
    setFishPreviewButton(button, true);
    audio.onended = () => stopFishLibraryPreview();
    audio.onerror = () => stopFishLibraryPreview();
    audio.onpause = () => {
      if (fishLibraryPreview === audio && !audio.ended) setFishPreviewButton(button, false);
    };
    audio.onplay = () => setFishPreviewButton(button, true);
    audio.__flowObjectUrl = objectUrl;
    await audio.play();
  } catch (error) {
    stopFishLibraryPreview();
    $('fishLibraryNote').textContent = error && error.message
      ? error.message : 'Preview playback failed. Check audio playback permissions.';
    log('[FISH LIBRARY] preview failed:', error && error.name || error);
  }
}
function setFishPreviewButton(button, playing) {
  if (!button) return;
  button.innerHTML = `<i data-lucide="${playing ? 'pause' : 'play'}"></i>`;
  button.classList.toggle('playing', playing);
  button.title = playing ? 'Pause preview' : 'Preview voice';
  button.setAttribute('aria-label', button.title);
  icons();
}
function stopFishLibraryPreview() {
  const audio = fishLibraryPreview;
  const button = fishLibraryPreviewButton;
  fishLibraryPreview = null;
  fishLibraryPreviewButton = null;
  if (audio) {
    audio.onended = null;
    audio.onerror = null;
    audio.onpause = null;
    audio.onplay = null;
    audio.pause();
    if (audio.__flowObjectUrl) URL.revokeObjectURL(audio.__flowObjectUrl);
  }
  if (button) {
    button.classList.remove('playing');
    setFishPreviewButton(button, false);
  }
}
function toggleFishVoiceBookmark(voice) {
  const bookmarks = getFishBookmarks();
  const existing = bookmarks.findIndex(item => item.id === voice.id);
  if (existing >= 0) bookmarks.splice(existing, 1);
  else bookmarks.unshift({
    id: voice.id,
    name: voice.name || 'Unnamed voice',
    description: voice.description || '',
    languages: Array.isArray(voice.languages) ? voice.languages : [],
    tags: Array.isArray(voice.tags) ? voice.tags : [],
    visibility: voice.visibility || 'Public',
    creator: voice.creator || {},
    cover_image: voice.cover_image || '',
    samples: (voice.samples || []).slice(0, 3),
    isDefault: !!voice.isDefault
  });
  if (saveFishBookmarks(bookmarks)) renderFishLibrary();
}
async function useFishLibraryVoice(voice, button) {
  if (!voice || !voice.id) return;
  button.disabled = true;
  button.innerHTML = '<i data-lucide="loader-2"></i>';
  icons();
  try {
    if (!voice.isDefault) {
      const params = new URLSearchParams({ voice_id: voice.id });
      const response = await fetch('/api/voice-library/validate?' + params.toString());
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || `Voice verification failed (${response.status}).`);
    }

    if (respMode === 'gemini_live') applyResponseMode('gemini_fish', false);
    audioQ.fishVoice = voice.id;
    try {
      localStorage.setItem('fishVoice', voice.id);
      if (voice.isDefault) localStorage.removeItem('fishVoiceName');
      else localStorage.setItem('fishVoiceName', voice.name || 'Public voice');
    } catch (e) {}
    updateVoiceDropdown();
    const selectedName = $('selectedVoiceName');
    if (selectedName) selectedName.textContent = voice.name || 'Public voice';
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'set_voice', value: voice.id }));
    }
    log('[TTS] public Fish Audio voice selected:', voice.name || voice.id);
    closeVoicePicker();
  } catch (error) {
    $('fishLibraryNote').textContent = error && error.message
      ? error.message : 'Could not use this voice. Please try again.';
    log('[FISH LIBRARY] voice selection failed:', error && error.message || error);
  } finally {
    button.disabled = false;
    button.innerHTML = '<i data-lucide="check"></i>';
    icons();
  }
}
async function loadFishLibraryPage(language, title, pageNumber, retry = false) {
  const state = fishLibraryState(language, title);
  if (!retry && state.pages[pageNumber - 1]) return;
  if (state.pending.has(pageNumber)) return state.pending.get(pageNumber);
  const token = ++fishLibraryRequestToken;
  state.loadingPage = pageNumber;
  state.error = '';
  state.errorPage = 0;
  renderFishLibrary();
  const request = (async () => {
    try {
      const params = new URLSearchParams({ language, page: String(pageNumber) });
      if (title.trim()) params.set('title', title.trim());
      const response = await fetch('/api/voice-library?' + params.toString());
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const retryAfter = response.headers.get('Retry-After');
        const waitNote = response.status === 429 && retryAfter ? ` Retry after ${retryAfter} seconds.` : '';
        throw new Error((body.error || `Voice library request failed (${response.status}).`) + waitNote);
      }
      const oldIds = new Set(state.pages.flatMap((entry) => entry && entry.voices || []).map((item) => item.id));
      const unique = (body.voices || []).filter((item) => item && item.id && !oldIds.has(item.id));
      state.pages[pageNumber - 1] = { ...body, voices: unique };
      state.total = Number.isFinite(body.total) ? body.total : unique.length;
      state.hasMore = !!body.has_more;
      state.windowLimited = state.windowLimited || !!body.window_limited;
      log('[FISH LIBRARY] loaded', unique.length, language, 'voices, page', pageNumber);
    } catch (error) {
      state.error = error && error.message ? error.message : 'Could not load public voices.';
      state.errorPage = pageNumber;
      log('[FISH LIBRARY] request failed:', state.error);
    } finally {
      state.loadingPage = 0;
      state.pending.delete(pageNumber);
      if (token === fishLibraryRequestToken || fishLibraryLanguage === language) renderFishLibrary();
    }
  })();
  state.pending.set(pageNumber, request);
  return request;
}
function initializeFishLibrary(languages) {
  const select = $('fishLibraryLanguage');
  if (!select) return;
  select.replaceChildren();
  (languages || []).forEach((language) => {
    const option = document.createElement('option');
    option.value = language.id;
    option.textContent = language.label;
    select.appendChild(option);
  });
  fishLibraryLanguage = select.value || 'en';
  select.onchange = () => {
    fishLibraryLanguage = select.value;
    renderFishLibrary();
    if (fishLibraryTab !== 'explore') return;
    const state = fishLibraryState(fishLibraryLanguage, $('fishLibrarySearch').value || '');
    if (!state.pages.length && !state.loadingPage) loadFishLibraryPage(state.language, state.title, 1);
  };
  $('fishLibrarySearch').oninput = () => {
    clearTimeout(fishLibrarySearchTimer);
    renderFishLibrary();
    fishLibrarySearchTimer = setTimeout(() => {
      if (fishLibraryTab !== 'explore') return;
      const state = fishLibraryState(fishLibraryLanguage, $('fishLibrarySearch').value || '');
      if (!state.pages.length && !state.loadingPage) loadFishLibraryPage(state.language, state.title, 1);
    }, 350);
  };
  $('voiceTabExplore').onclick = () => setFishLibraryTab('explore');
  $('voiceTabDefault').onclick = () => setFishLibraryTab('default');
  $('voiceTabBookmarked').onclick = () => setFishLibraryTab('bookmarked');
  $('fishLibraryMore').onclick = () => {
    const state = fishLibraryState(fishLibraryLanguage, $('fishLibrarySearch').value || '');
    if (!state.loadingPage) loadFishLibraryPage(state.language, state.title, state.pages.length + 1);
  };
  $('fishLibraryRetry').onclick = () => {
    const state = fishLibraryState(fishLibraryLanguage, $('fishLibrarySearch').value || '');
    if (state.errorPage) loadFishLibraryPage(state.language, state.title, state.errorPage, true);
  };
  $('voiceFilterOpen').onclick = () => setVoiceFilterOpen($('voiceFilterOverlay').classList.contains('hidden'));
  $('voiceFilterClose').onclick = () => setVoiceFilterOpen(false);
  $('voiceFilterDone').onclick = () => setVoiceFilterOpen(false);
  $('voiceFilterOverlay').onclick = event => {
    if (event.target === $('voiceFilterOverlay')) setVoiceFilterOpen(false);
  };
  $('voiceFilterGender').onchange = () => {
    fishLibraryFilters.gender = $('voiceFilterGender').value;
    renderFishLibrary();
  };
  $('voiceFilterAge').onchange = () => {
    fishLibraryFilters.age = $('voiceFilterAge').value;
    renderFishLibrary();
  };
  $('voiceFilterReset').onclick = resetFishVoiceFilters;
  renderFishLibrary();
}

async function loadConfig() {
  try {
    const cfg = await (await fetch('/api/config')).json();
    serverConfig = cfg;
    const sc = $('scenario');
    if (sc) {
      sc.innerHTML = '';
      (cfg.scenarios || []).forEach(s => { const o = document.createElement('option'); o.value = s.id; o.textContent = s.title; sc.appendChild(o); });
      let savedScenario = '';
      try { savedScenario = localStorage.getItem('scenario') || ''; } catch (e) {}
      if (savedScenario && [...sc.options].some(o => o.value === savedScenario)) {
        sc.value = savedScenario;
      }
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
    let savedCorr = '';
    try { savedCorr = localStorage.getItem('correction') || ''; } catch (e) {}
    if ($('correction') && savedCorr) $('correction').value = savedCorr;
    let savedLevel = '';
    try { savedLevel = localStorage.getItem('level') || ''; } catch (e) {}
    if ($('level') && savedLevel) $('level').value = savedLevel;

    renderResponseModes(cfg);
    updateVoiceDropdown();
    renderLanguageOptions(cfg);
    initializeFishLibrary(cfg.fish_library_languages || []);
  } catch (e) {
    const fs = $('fishStatus');
    if (fs) fs.textContent = 'Server unreachable — is it running on :8000?';
    log('[CFG] failed:', e);
  }
}

let useHttpTransport = false;
let wsRapidCloseCount = 0;
let wsConnectTime = 0;
let httpTurnAbortController = null;

function handleServerMessage(m) {
  if (!m) return;
  if (m.type === 'ready') { if (running) setState('LISTENING'); else setState('IDLE'); }
  else if (m.type === 'status') {
    setState(m.state, m.note);
    if (m.state === 'THINKING' || m.state === 'SPEAKING') {
      llmPending = true;
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
    llmPending = true;
    if (audioQ.engine === 'gemini') { try { LiveCaptions.start(); } catch (e) {} }
    setState('SPEAKING');
    if (!$('aiTyping')) showTypingIndicator();
  }
  else if (m.type === 'tts_sentence') {
    if (m.turn === 'greet' && userTurns > 0) return; // Drop reconnect greeting audio mid-session
    const stale = !!(m.turn && audioQ.deadTurns.has(m.turn));
    if (m.lat && !stale) audioQ.turnLat = m.lat;  // server stages for the latency block
    audioQ.enqueue(m.text, { stream: !!m.first, turn: m.turn, req_id: m.req_id || m.turn });
  }
  else if (m.type === 'live_audio_chunk') {
    audioQ.playLivePcmChunk(m.data, 24000, m.turn);
  }
  else if (m.type === 'response_mode') { labelFor(m.value); log('[AI] mode confirmed:', respModeLabel); }
  else if (m.type === 'llm_done') {
    const reqId = m.req_id || m.turn || String(turnSeq);
    log(`[LLM req_id=${reqId}] LLM response received in client (chars=${(m.text || '').length})`);
    hideTypingIndicator();
    fullReply = '';
    llmPending = false;
    const displayText = m.display_text || m.text;
    // Live center line mirrors the voice: latest chunk only (fish path keeps
    // its own per-sentence display and is untouched by liveChunkText).
    const centerText = m.live ? (liveChunkText(displayText) || displayText) : displayText;
    if (!m.greeting || userTurns === 0) {
      addTurn('Partner', displayText);
    }
    log(`[UI req_id=${reqId}] assistant message rendered in chat`);
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
      aiSpeaking = false;
      if (!m.partial && state !== 'INTERRUPTED') {
        setAiLine(centerText);
        smokeDissolveAiLine(1400);
      }
      if (running && state !== 'INTERRUPTED') {
        setState('LISTENING');
      }
    }
  } else if (m.type === 'llm_cancelled') {
    const reqId = m.req_id || m.turn || String(turnSeq);
    log(`[TURN req_id=${reqId}] turn cancelled`);
    hideTypingIndicator();
    fullReply = '';
    llmPending = false;
    aiSpeaking = false;
    try { LiveCaptions.stop(); } catch (e) {}
    setAiLine('');
    if (running && !audioQ.busy) {
      setState('LISTENING');
    }
  }
  else if (m.type === 'mode') {
    if ($('mode') && (m.mode === 'free' || m.mode === 'practice')) {
      $('mode').value = m.mode;
      updateModeUI();
    }
    log('[MODE]', m.mode);
  }
  else if (m.type === 'voice_error') {
    const status = $('fishStatus');
    if (status) status.textContent = m.error || 'The selected voice could not be used.';
    log('[TTS] voice selection rejected:', m.error || m.value);
  }
  else if (m.type === 'error') {
    const reqId = m.req_id || m.turn || String(turnSeq);
    llmPending = false;
    hideTypingIndicator();
    log(`[ERROR req_id=${reqId}] stage=${m.stage || m.scope || 'unknown'} error=${sanitizeError(m.message)}`);
    if (running && !audioQ.busy) {
      aiSpeaking = false;
      setState('LISTENING');
    }
  }
}

async function streamHttpTurn(text, turnId, reqId, replyLang) {
  if (httpTurnAbortController) {
    try { httpTurnAbortController.abort(); } catch (e) {}
  }
  httpTurnAbortController = new AbortController();
  const signal = httpTurnAbortController.signal;

  const payload = {
    text,
    mode: $('mode') ? $('mode').value : 'free',
    scenario: $('scenario') ? $('scenario').value : 'casual',
    correction: $('correction') ? $('correction').value : 'balanced',
    level: $('level') ? $('level').value : 'auto',
    language: $('language') ? $('language').value : 'english',
    voice_id: audioQ.fishVoice || '',
    response_mode: respMode,
    history: getStoredChatHistory().slice(-10).map(x => ({ role: x.who === 'You' ? 'user' : 'assistant', content: x.text })),
    t0: Date.now(),
    turn_id: turnId,
    req_id: reqId,
    reply_lang: replyLang || ''
  };

  try {
    const response = await fetch('/api/session/' + encodeURIComponent(sid) + '/turn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal
    });

    if (!response.ok) {
      let errMsg = 'HTTP ' + response.status;
      try { const errObj = await response.json(); errMsg = errObj.error || errObj.message || errMsg; } catch (e) {}
      handleServerMessage({ type: 'error', scope: 'http', stage: 'HTTP_TURN', message: errMsg, req_id: reqId, turn: turnId });
      handleServerMessage({ type: 'llm_done', text: "Sorry, I had a little hiccup. Can you say that again?", req_id: reqId, turn: turnId });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n\n');
      buffer = lines.pop() || '';
      for (const block of lines) {
        const trimmed = block.trim();
        if (!trimmed || !trimmed.startsWith('data:')) continue;
        const rawJson = trimmed.slice(5).trim();
        if (rawJson === '[DONE]') continue;
        try {
          const msg = JSON.parse(rawJson);
          handleServerMessage(msg);
        } catch (err) {
          console.warn('[SSE] parse error:', err, rawJson);
        }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      log(`[TURN req_id=${reqId}] turn aborted by client`);
      handleServerMessage({ type: 'llm_cancelled', req_id: reqId, turn: turnId });
    } else {
      log(`[ERROR req_id=${reqId}] HTTP stream failed: ${sanitizeError(e.message || e)}`);
      handleServerMessage({ type: 'error', scope: 'http', stage: 'HTTP_STREAM', message: e.message || 'Stream error', req_id: reqId, turn: turnId });
      handleServerMessage({ type: 'llm_done', text: "Sorry, I had a little hiccup. Can you say that again?", req_id: reqId, turn: turnId });
    }
  } finally {
    if (httpTurnAbortController && httpTurnAbortController.signal === signal) {
      httpTurnAbortController = null;
    }
  }
}

async function connect() {
  if (useHttpTransport) return;
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
    if (!llmPending) {
      aiSpeaking = false;
      if (running && state !== 'INTERRUPTED') {
        setState('LISTENING');
      } else if (!running) {
        setState('IDLE');
      }
      smokeDissolveAiLine(1400);
    }
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

  try {
    ws = new WebSocket(`${proto}://${location.host}/ws/session/${sid}?lang=${encodeURIComponent(langParam)}`);
  } catch (err) {
    useHttpTransport = true;
    log('[TRANSPORT] WebSocket unavailable — switched to HTTP/SSE streaming transport');
    return;
  }

  ws.onopen = () => {
    wsConnectTime = Date.now();
    log('[WS] connected', sid);
    try {
      const saved = localStorage.getItem('lang');
      if (saved && serverLanguages().some(l => l.id === saved)) {
        $('language').value = saved;
        applyLanguage(saved, true);
      }
    } catch (e) {}
    // sync current mode, scenario, correction, and level to session
    try {
      ws.send(JSON.stringify({
        type: 'set_mode',
        mode: $('mode') ? $('mode').value : 'free',
        scenario: $('scenario') ? $('scenario').value : 'casual'
      }));
      ws.send(JSON.stringify({ type: 'set_correction', value: $('correction') ? $('correction').value : 'balanced' }));
      ws.send(JSON.stringify({ type: 'set_level', value: $('level') ? $('level').value : 'auto' }));
    } catch (e) {}
    // sync past chat history memory to session so AI can reference previous discussions
    try {
      const past = getStoredChatHistory().slice(-10);
      if (past.length > 0) {
        ws.send(JSON.stringify({
          type: 'sync_history',
          turns: past.map(x => ({ role: x.who === 'You' ? 'user' : 'assistant', content: x.text }))
        }));
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
    try {
      const m = JSON.parse(ev.data);
      handleServerMessage(m);
    } catch (e) {}
  };

  ws.onclose = () => {
    ws = null;
    const sessionDuration = Date.now() - wsConnectTime;
    if (sessionDuration < 3500) {
      wsRapidCloseCount++;
      if (wsRapidCloseCount >= 2) {
        useHttpTransport = true;
        log('[TRANSPORT] WebSocket closed in serverless environment — switched to HTTP/SSE streaming transport');
        return;
      }
    }
    if (running && !useHttpTransport) {
      log('[WS] closed — reconnect in 2s');
      setTimeout(connect, 2000);
    }
  };

  if (!window.__wsPingTimer) {
    window.__wsPingTimer = setInterval(() => {
      try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'ping' })); } catch (e) {}
    }, 20000);
  }
}

function initApp() {
  loadConfig();
  loadMicDevices();
  loadChatHistoryUI();
  initChatSmoke();
  icons();
  if ($('clearChatBtn')) $('clearChatBtn').onclick = clearChatHistory;
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}

function sanitizeError(err) {
  const s = String(err || '');
  return s.replace(/Bearer\s+[A-Za-z0-9_\-\.]+/gi, 'Bearer [REDACTED]')
          .replace(/(key|token|secret|authorization)=['"][^'"]+['"]/gi, '$1=[REDACTED]');
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
async function sendText(text, replyLang) {
  text = (text || '').trim();
  if (!text) return;

  // Guard against duplicate send of the exact same utterance within 1.2s
  if (lastSentText && (performance.now() - lastSentAt < 1200) && sameText(text, lastSentText, 0.85)) {
    log('[TURN] duplicate turn suppressed:', JSON.stringify(text));
    return;
  }
  audioQ.cancel(); // Cancel any superseded in-flight audio from prior turn
  if (httpTurnAbortController) {
    try { httpTurnAbortController.abort(); } catch (e) {}
    httpTurnAbortController = null;
  }
  llmPending = true;
  aiSpeaking = true;
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
  const reqId = 'req_' + turnId + '_' + Date.now();
  audioQ._turnId = turnId;   // string: the server echoes ids back as strings
  audioQ._turnT0 = performance.now();
  audioQ._expectFirst = true;
  audioQ.turnLat = null;
  const transportLabel = useHttpTransport || !ws || ws.readyState !== WebSocket.OPEN ? 'HTTP/SSE' : 'WebSocket';
  log(`[CHAT req_id=${reqId}] text submission started: "${text.slice(0, 80)}" (${transportLabel})`);

  if (useHttpTransport || !ws || ws.readyState !== WebSocket.OPEN) {
    streamHttpTurn(text, turnId, reqId, replyLang || '');
  } else {
    ws.send(JSON.stringify({ type: 'user_transcript', text, t0: Date.now(), turn_id: turnId, req_id: reqId, reply_lang: replyLang || '' }));
  }
}
const isMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) || ('ontouchstart' in window && window.innerWidth < 800);

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
let consecutiveAborts = 0;
// Mobile browsers auto-end SpeechRecognition on silence/utterance boundaries
// even with continuous=true (iOS ignores continuous entirely), so onend fires
// on its own and each cycle logs open/close. Track end spacing to back off
// when the platform is throttling restarts instead of hammering start().
let lastRecogEndAt = 0;
let recogRapidEnds = 0;

function setupRecog() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { hearNote('Speech recognition not supported here — use Chrome, or type below.'); return null; }
  const r = new SR();
  r.lang = sttLang;
  r.interimResults = true;
  // Hint only: mobile browsers still auto-end on silence/utterance boundaries
  // (iOS ignores continuous entirely) — onend below reopens with debounce.
  r.continuous = true;
  // Best-confidence alternative wins, in every language.
  r.maxAlternatives = 3;
  recogFatal = false;
  consecutiveAborts = 0;
  lastRecogEndAt = 0;
  recogRapidEnds = 0;
  clearTimeout(recogRestartTimer);
  recogRestartTimer = null;

  r.onspeechstart = () => {
    if (micMuted || !listening) return;
    $('micLive').classList.add('on');
    try { window.__micLevel = 0.08; } catch (e) {}
    if (aiSpeaking) bargeIn();
  };

  r.onspeechend = () => {
    if (micMuted || !listening) return;
    $('micLive').classList.remove('on');
    try { window.__micLevel = 0; } catch (e) {}
  };

  r.onsoundstart = () => {
    if (micMuted || !listening) return;
    $('micLive').classList.add('on');
    try { window.__micLevel = 0.06; } catch (e) {}
  };

  r.onsoundend = () => {
    if (micMuted || !listening) return;
    $('micLive').classList.remove('on');
    try { window.__micLevel = 0; } catch (e) {}
  };

  r.onresult = (ev) => {
    if (micMuted || !listening) return; // deaf only while mic is explicitly muted
    consecutiveAborts = 0;
    let interim = '', fin = '', conf = 1;
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const res = ev.results[i];
      if (res.isFinal) {
        // Check all alternatives and pick the highest-confidence one.
        if (res.length > 1) {
          let bestTranscript = res[0].transcript;
          let bestConf = res[0].confidence || 0.9;
          for (let alt = 1; alt < res.length; alt++) {
            const altConf = res[alt].confidence || 0.9;
            if (altConf > bestConf) {
              bestTranscript = res[alt].transcript;
              bestConf = altConf;
            }
          }
          fin += bestTranscript;
          conf = Math.min(conf, bestConf);
        } else {
          fin += res[0].transcript; conf = Math.min(conf, res[0].confidence || 0.9);
        }
      } else {
        interim += res[0].transcript;
      }
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
      
      // For Hindi: stricter filtering due to lower model quality
      const minConfThreshold = isHindi ? 0.35 : 0.25;
      const minLengthThreshold = isHindi ? 3 : 2;
      
      // room noise / stray fragments: low confidence AND near-empty
      if (conf < minConfThreshold && text.length < minLengthThreshold) {
        log('[STT] dropped as noise', JSON.stringify(text), 'conf=' + conf.toFixed(2));
        if (isHindi) hearNote('कृपया फिर से बोलें (Please speak again)');
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
      
      // Warn user about low confidence - more lenient for Hindi
      const warnThreshold = isHindi ? 0.45 : 0.35;
      if (conf < warnThreshold) {
        const msg = isHindi 
          ? 'मुझे सही से समझ नहीं आया — कृपया फिर से बोलें (I did not catch that clearly — please speak again)'
          : 'I did not catch that clearly — say it once more?';
        hearNote(msg);
      }
      sendText(text);
    }
  };
  r.onerror = (e) => {
    if (e.error === 'aborted') {
      consecutiveAborts++;
      if (consecutiveAborts <= 2) log('[STT] aborted');
    } else {
      log('[STT] error', e.error);
    }
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      recogFatal = true;
      listening = false;
      hearNote('Mic blocked — allow microphone access, then press mic again.');
    } else if (e.error === 'no-speech') {
      // transient silence on mobile — normal, onend keeps mic open seamlessly
    } else if (e.error === 'aborted') {
      // aborted by hardware contention or turn change
    } else if (e.error === 'network') {
      // transient network interruption with cloud speech service
    } else if (e.error === 'audio-capture') {
      recogFatal = true;
      listening = false;
      hearNote('No microphone found — plug one in, then press mic again.');
    }
  };
  r.onaudiostart = () => {
    consecutiveAborts = 0;
    log('[STT] mic stream opened');
  };
  r.onaudioend = () => {
    log('[STT] mic stream closed');
  };
  r.onend = () => {
    // Mobile browsers end recognition on their own (silence endpointing,
    // single-utterance mode on iOS, audio-focus/power management) even though
    // we never call stop() mid-session and continuous=true. Each auto-end
    // closes the mic stream (onaudioend), so the open/close pair in the log
    // is the platform lifecycle, not a leak. Reopen with a short debounce so
    // the audio hardware settles and iOS restart throttling is respected —
    // an immediate start() inside onend causes the rapid open/close storm.
    if (!listening || !running || micMuted || recogFatal) {
      return; // intentionally stopped — stay closed
    }
    clearTimeout(recogRestartTimer);
    const now = performance.now();
    const sinceEnd = now - lastRecogEndAt;
    lastRecogEndAt = now;
    // Storm guard: ends arriving <2s apart repeatedly mean the platform is
    // throttling us — back off instead of hammering start().
    if (sinceEnd < 2000) recogRapidEnds++;
    else recogRapidEnds = 0;
    let delay = (typeof isMobile !== 'undefined' && isMobile) ? 700 : 350;
    if (consecutiveAborts >= 3) delay = Math.max(delay, 1500);
    if (recogRapidEnds >= 4) delay = Math.min(2500, delay * 2);
    // Abort storm (mic owned elsewhere / hardware fault): keep a slow
    // heartbeat so a transient fault can still recover, but tell the user —
    // a tight restart loop otherwise looks like a working mic.
    if (consecutiveAborts >= 5) {
      delay = 5000;
      hearNote('Mic keeps failing — close other apps using the mic, then tap stop/start.', 5000);
    }
    recogRestartTimer = setTimeout(() => {
      recogRestartTimer = null;
      if (!listening || !running || micMuted || recogFatal) return;
      try {
        r.start();
      } catch (err) {
        if (err && err.name !== 'InvalidStateError') {
          log('[STT] restart failed:', (err && err.message) || (err && err.name));
        }
        // InvalidStateError = already running — the mic is open, nothing to do.
      }
    }, delay);
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
function pauseRecognitionForMicTest() {
  if (!running || !listening || micMuted || !recog) return Promise.resolve(() => {});
  const current = recog;
  const originalOnEnd = current.onend;
  listening = false;
  clearTimeout(recogRestartTimer);
  let finish;
  const ended = new Promise((resolve) => {
    let done = false;
    const complete = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(complete, 750);
    finish = complete;
    current.onend = (event) => {
      try { originalOnEnd && originalOnEnd.call(current, event); }
      finally { complete(); }
    };
  });
  try { current.stop(); } catch (e) { finish(); }
  return ended.then(() => {
    if (current.onend !== originalOnEnd) current.onend = originalOnEnd;
    return () => {
      if (running && !micMuted && recog === current && !recogFatal) {
        listening = true;
        try { current.start(); }
        catch (e) { log('[STT] restart after mic test failed:', e); }
      }
    };
  });
}
function micTestErrorMessage(error) {
  const name = (error && error.name) || String(error || 'unknown error');
  if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return 'Mic access requires HTTPS (or localhost). Open Flow using a secure URL.';
  }
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Microphone permission is blocked. Allow it for this site in browser settings, then reload Flow.';
  }
  if (name === 'NotFoundError') return 'No microphone was found on this device.';
  if (name === 'NotReadableError') return 'The microphone is busy or unavailable. Close other apps using it and try again.';
  return 'Mic test failed: ' + name;
}

function resumeSTT() {
  if (!recog || recogFatal || micMuted) return;
  listening = true;
  // Only start if recognition is not already running
  try {
    recog.start();
    log('[STT] recognition started (mic stays open)');
  } catch (e) {
    if (e.name === 'InvalidStateError') {
      // Already running - just set listening=true (no need to restart)
      log('[STT] recognition already active (listening enabled)');
    } else {
      log('[STT] start failed:', e.name);
    }
  }
}

function pauseSTT() {
  // Only set listening=false, do NOT stop recognition
  // This keeps the mic stream open but ignores speech events
  listening = false;
  log('[STT] listening paused (mic stays open)');
}

// True while the assistant owns the turn (LLM generating or TTS audio
// queued/playing). STT stays listening through it so barge-in keeps working;
// this helper exists so callers can reason about turn state explicitly.
function isTurnActive() {
  try {
    return !!llmPending || !!(typeof audioQ !== 'undefined' && audioQ && audioQ.busy);
  } catch (e) {
    return !!llmPending;
  }
}

// True while the assistant owns the turn (LLM generating or TTS audio
// queued/playing). STT stays listening through it so barge-in keeps working;
// this helper exists so callers can reason about turn state explicitly.
function isTurnActive() {
  try {
    return !!llmPending || !!(typeof audioQ !== 'undefined' && audioQ && audioQ.busy);
  } catch (e) {
    return !!llmPending;
  }
}

// --- Mobile always-open capture: ONE held mic stream, no SpeechRecognition -
// Web Speech owns its own mic stream and the platform auto-ends it on mobile
// (silence/utterance/audio-focus), so "always open" is impossible through it.
// This path holds a single getUserMedia stream for the whole session (opened
// on start, closed only on stop), runs VAD levels on it for speech start/end
// + barge-in, and records each utterance with its own fresh MediaRecorder —
// one recorder per utterance, because a recorder emits container headers only
// in its first slice (later slices of a shared recording are unparseable).
// Each utterance is POSTed to /api/stt. Recorders come and go; the mic stream
// indicator stays solid for the entire session. Falls back to Web Speech when
// the server has no transcription key (serverConfig.server_stt false) or
// MediaRecorder is unavailable.
let mobStream = null, mobMime = '';
let mobUtt = null;              // active per-utterance {rec, chunks, confirmed, dead, gen}
let mobUttGen = 0;          // bumped on stop/mute/test: stale results dropped
let mobTestPaused = false;  // mic-test hold: assembly paused, stream untouched
let mobCaptureOn = false;

function mobPickMime() {
  if (typeof MediaRecorder === 'undefined') return '';
  const cands = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
  try {
    for (const c of cands) if (MediaRecorder.isTypeSupported(c)) return c;
  } catch (e) {}
  return '';
}
function mobFileExt() { return (mobMime || '').indexOf('mp4') >= 0 ? 'm4a' : 'webm'; }
function mobLangCode() {
  // Preferred-language id for the transcription prompt sample. Detection
  // itself is always automatic — this only steers short utterances.
  try {
    const v = ($('language') && $('language').value) || localStorage.getItem('lang') || 'english';
    if (v && v !== 'hinglish') return v;
  } catch (e) {}
  return '';
}
function mobCanUse() {
  try {
    return !!(!micMuted && typeof MediaRecorder !== 'undefined' && mobPickMime() &&
      navigator.mediaDevices && navigator.mediaDevices.getUserMedia &&
      serverConfig && serverConfig.server_stt);
  } catch (e) {
    return false;
  }
}
function mobStreamLive() {
  try {
    return !!(mobStream && mobStream.getAudioTracks().some(t => t.readyState === 'live'));
  } catch (e) {
    return false;
  }
}
function mobCleanupStream() {
  mobAbortUtt();
  mobCaptureOn = false;
  try { mobStream && mobStream.getTracks().forEach(t => t.stop()); } catch (e) {}
  mobStream = null;
}
// One fresh MediaRecorder per utterance. A recorder emits its container
// headers only in its FIRST slice, so slicing utterances out of a single
// continuous recording yields headerless fragments after the first turn —
// the transcriber rejects those ("could not process file"). A recorder per
// utterance is always a self-contained valid file, and starting/stopping it
// never touches the held mic stream.
function mobStartUtt() {
  if (!mobStreamLive()) return null;
  const u = { rec: null, chunks: [], confirmed: false, dead: false, gen: mobUttGen, peak: 0,
    barged: (typeof aiSpeaking !== 'undefined' && !!aiSpeaking) };
  let rec = null;
  try {
    // 32kbps is plenty for speech and keeps uploads small on mobile networks.
    rec = new MediaRecorder(mobStream, { mimeType: mobMime, audioBitsPerSecond: 32000 });
  } catch (e) {
    try {
      rec = new MediaRecorder(mobStream, { mimeType: mobMime });
    } catch (e2) {
      return null;
    }
  }
  u.rec = rec;
  // Reaper: a recorder that never confirms (cough with no speech after it)
  // would otherwise run forever, hoarding silence. 4s is generous — real
  // onsets confirm in ~250ms; confirmation clears this timer.
  try {
    u.blipTimer = setTimeout(() => {
      if (mobUtt === u && !u.confirmed) {
        mobAbortUtt();
        log('[STT] unconfirmed blip discarded');
      }
    }, 4000);
  } catch (e) {}
  rec.ondataavailable = (ev) => {
    try {
      if (!u.dead && ev.data && ev.data.size) {
        u.chunks.push(ev.data);
        // Peak mic level during THIS utterance: proof real speech happened.
        // A VAD-confirmed utterance with no energy above the floor is noise
        // the gate briefly let through — hallucination fodder, drop it early.
        try { if (vad && vad.level > u.peak) u.peak = vad.level; } catch (e2) {}
      }
    } catch (e) {}
  };
  rec.onstop = () => { mobFinishUtt(u); };
  rec.onerror = () => {
    // Faulted mid-utterance: drop it. The held stream is unaffected and the
    // next speech starts a new recorder.
    u.dead = true;
    if (mobUtt === u) mobUtt = null;
  };
  try {
    rec.start(250);
  } catch (e) {
    return null;
  }
  return u;
}
function mobAbortUtt() {
  const u = mobUtt;
  mobUtt = null;
  if (!u) return;
  u.dead = true;
  u.chunks = [];
  try { clearTimeout(u.blipTimer); } catch (e) {}
  try { if (u.rec && u.rec.state !== 'inactive') u.rec.stop(); } catch (e) {}
}
function mobFinishUtt(u) {
  if (!u || u.dead || u.gen !== mobUttGen) return; // discarded / stale session
  if (!u.confirmed || !u.chunks.length) {
    log('[STT] utterance discarded (unconfirmed blip or no audio)');
    return;
  }  if ((u.peak || 0) < 0.006) {
    // Confirmed by the gate but the mic captured no real energy: fan knock,
    // handling noise, or residual echo. Uploading it only buys a hallucinated
    // transcript ("random words"), so drop it here.
    log('[STT] utterance has no speech energy (peak ' + (u.peak || 0).toFixed(4) + ') — dropped');
    return;
  }
  mobTranscribe(u.chunks, u.peak || 0, !!u.barged);
}
function mobOnFirstVoice() {
  // First frame above the noise floor — start the recorder BEFORE the 250ms
  // confirmation gate so the onset is never clipped. Never restart a running
  // unconfirmed recorder here: a micro-dip inside the onset ("h-ello") would
  // otherwise discard the opening syllables over and over, forcing the user
  // to say hello twice. Gaps inside the utterance are harmless for Whisper.
  if (micMuted || mobTestPaused || !mobCaptureOn || !running) return;
  if (!mobUtt) {
    mobUtt = mobStartUtt();
    if (mobUtt) log('[STT] voice onset — capturing');
    else log('[STT] recorder failed to start — utterance lost');
  }
}
function mobOnSpeechStart() {
  // VAD-confirmed speech (250ms above the floor): barge-in + mark live.
  sustainedBargeIn();
  if (micMuted || mobTestPaused || !mobCaptureOn || !running) return;
  if (!mobUtt) {
    mobUtt = mobStartUtt(); // fallback — first-voice normally beats us here
    if (!mobUtt) {
      log('[STT] recorder failed to start — utterance lost');
      return;
    }
  }
  mobUtt.confirmed = true;
  try { clearTimeout(mobUtt.blipTimer); } catch (e) {}
}
function mobOnSpeechEnd() {
  clearTimeout(speechTimer);
  const u = mobUtt;
  mobUtt = null;
  if (!u) return;
  try { clearTimeout(u.blipTimer); } catch (e) {}
  if (micMuted || mobTestPaused || !mobCaptureOn || !running || !u.confirmed) {
    u.dead = true;
    u.chunks = [];
    try { if (u.rec && u.rec.state !== 'inactive') u.rec.stop(); } catch (e) {}
    return;
  }
  try {
    if (u.rec.state !== 'inactive') u.rec.stop(); // onstop → mobFinishUtt
    else mobFinishUtt(u);
  } catch (e) {
    u.dead = true;
    u.chunks = [];
  }
}
async function mobTranscribe(chunks, peak, barged) {
  const gen = mobUttGen;
  let blob = null;
  try {
    blob = new Blob(chunks, { type: mobMime || 'audio/webm' });
  } catch (e) {
    return;
  }
  if (!blob || blob.size < 1500) {
    // Short-word floor ("haan"/"yes" at 32kbps ≈ 1.5-2KB): smaller than this
    // is mic bump or cutoff, not speech. Logged so misses stay diagnosable.
    log('[STT] utterance too short — dropped (' + (blob ? blob.size : 0) + 'B)');
    return;
  }
  if (blob.size > 8 * 1024 * 1024) {
    log('[STT] utterance too large — dropped');
    return;
  }
  // No center text while waiting: the reply appears when ready. A typing
  // indicator in the history drawer marks the in-flight request.
  try { showTypingIndicator(); } catch (e) {}
  const t0 = performance.now();
  try {
    const lang = mobLangCode();
    const headers = {
      'Content-Type': mobMime || 'audio/webm',
      'X-Filename': 'utterance.' + mobFileExt(),
      'X-Session': (typeof sid !== 'undefined' ? sid : ''),
      'X-Level': String(peak || 0),
      'X-Barged': barged ? '1' : '0',
    };
    if (lang) headers['X-Language'] = lang;
    const r = await fetch('/api/stt', { method: 'POST', headers, body: blob });
    const body = await r.json().catch(() => ({}));
    if (gen !== mobUttGen) return; // stopped/muted/tested mid-flight — stale
    if (!r.ok) {
      const msg = (body && body.error) || ('HTTP ' + r.status);
      log('[STT] transcription failed:', msg);
      try { hideTypingIndicator(); } catch (e) {}
      hearNote(r.status === 503 ? 'Server transcription is off — type below instead.' : msg, 4000);
      return;
    }
    const text = ((body && body.text) || '').trim();
    const replyLang = (body && body.language) || '';
    log('[STT] server transcript (' + Math.round(performance.now() - t0) + 'ms): ' + JSON.stringify(text.slice(0, 100)) + (replyLang ? ' lang=' + replyLang : ''));
    if (!text) {
      try { hideTypingIndicator(); } catch (e) {}
      return;
    }
    sendText(text, replyLang); // clears hearNote + guards duplicates; reply follows the spoken language
  } catch (e) {
    if (gen !== mobUttGen) return;
    log('[STT] transcription error:', (e && e.name) || e);
    try { hideTypingIndicator(); } catch (e2) {}
    hearNote('Transcription failed — check connection.', 4000);
  }
}
async function startMobileCapture() {
  mobAbortUtt();
  mobUttGen++;
  mobTestPaused = false;
  mobMime = mobPickMime();
  if (!mobMime) {
    log('[STT] always-open unavailable: no supported recorder mime type');
    return false;
  }
  let stream = null;
  try {
    // Voice-optimized capture: echo cancellation (speaker feedback), noise
    // suppression + auto gain (quiet mics / street noise) help the
    // transcriber hear exact words.
    const dev = selectedMicDevice();
    const enhanced = {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    if (dev) enhanced.deviceId = { ideal: dev };
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: enhanced });
    } catch (e) {
      // Older browsers may reject processing constraints: retry plain.
      log('[STT] enhanced constraints rejected (' + ((e && e.name) || e) + ') — retrying basic capture');
      stream = await navigator.mediaDevices.getUserMedia({ audio: dev ? { deviceId: { ideal: dev } } : true });
    }
  } catch (e) {
    log('[STT] held mic stream denied:', (e && e.name) || e);
    return false;
  }
  mobStream = stream;
  mobCaptureOn = true;
  vad = new EnergyVAD({ onSpeechStart: mobOnSpeechStart, onSpeechEnd: mobOnSpeechEnd, onFirstVoice: mobOnFirstVoice });
  let wired = false;
  try {
    wired = await vad.attach(stream);
  } catch (e) {
    wired = false;
  }
  // End-of-turn on mobile (800ms, near desktop's 850): splitting a sentence
  // at a mid-thought pause makes the transcriber confabulate an ending for
  // each fragment ("...में कोई हैं?") — verified live on Hindi halves. Whole
  // sentences stay exact; the extra ~250ms is minor next to the round trip.
  // Slightly hotter VAD than desktop (quiet phone mics miss soft onsets
  // otherwise); the adaptive floor still absorbs steady fan/AC hum.
  try {
    if (vad) {
      vad.endOfTurnMs = 800;
      vad.threshold = 0.006;
      vad.snrGate = 1.8;
    }
  } catch (e) {}
  if (!wired) {
    log('[STT] always-open unavailable: VAD attach failed (' + (vad && vad.lastError) + ')');
    try { vad && vad.stop(); } catch (e) {}
    vad = null;
    mobCleanupStream();
    return false;
  }
  log('[STT] always-open capture ON — mic stream held for the session');
  mobStartHealth();
  return true;
}
// Watchdog: the OS can kill mic tracks mid-session (phone calls,
// notifications, browser throttling) — then every utterance is silently lost
// because dead tracks deliver no audio. Poll every 4s and reacquire.
let mobHealthTimer = null, mobHealthNoted = false, mobRecovering = false;
function mobStartHealth() {
  mobStopHealth();
  mobHealthNoted = false;
  try {
    mobHealthTimer = setInterval(() => {
      try {
        if (!mobCaptureOn || !running || mobRecovering) return;
        if (mobStreamLive()) {
          mobHealthNoted = false;
          return;
        }
        mobRecoverStream();
      } catch (e) {}
    }, 4000);
  } catch (e) {}
}
function mobStopHealth() {
  try { clearInterval(mobHealthTimer); } catch (e) {}
  mobHealthTimer = null;
}
async function mobRecoverStream() {
  if (mobRecovering || !mobCaptureOn || !running) return;
  mobRecovering = true;
  try {
    log('[STT] mic stream died — reacquiring');
    mobAbortUtt();
    try { mobStream && mobStream.getTracks().forEach(t => { try { t.stop(); } catch (e) {} }); } catch (e) {}
    mobStream = null;
    const dev = selectedMicDevice();
    let stream = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: Object.assign(
          { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          dev ? { deviceId: { ideal: dev } } : {}
        ),
      });
    } catch (e) {
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (e2) { stream = null; }
    }
    if (!stream || !mobCaptureOn || !running) throw new Error('reacquire-failed');
    mobStream = stream;
    if (vad) {
      const ok = await vad.attach(stream);
      try { vad.noiseFloor = null; } catch (e) {} // recalibrate to the new track
      if (!ok) throw new Error('vad-attach-failed');
    }
    mobHealthNoted = false;
    log('[STT] mic stream reacquired');
  } catch (e) {
    if (!mobHealthNoted) {
      mobHealthNoted = true;
      hearNote('Microphone stopped — tap stop, then start again.', 5000);
      log('[STT] mic reacquire failed');
    }
  } finally {
    mobRecovering = false;
  }
}
function stopMobileCapture() {
  if (!mobStream && !mobUtt && !mobCaptureOn) return; // never started — desktop / fallback path
  mobStopHealth();
  mobUttGen++; // drop any in-flight transcription
  mobAbortUtt();
  mobTestPaused = false;
  mobCaptureOn = false;
  try { vad && vad.stop(); } catch (e) {} // also closes the shared stream tracks
  vad = null;
  try { mobStream && mobStream.getTracks().forEach(t => { try { t.stop(); } catch (e) {} }); } catch (e) {}
  mobStream = null;
  hearNote('');
}

function bargeIn() {
  llmPending = false;
  aiSpeaking = false;
  audioQ.cancel();
  if (httpTurnAbortController) {
    try { httpTurnAbortController.abort(); } catch (e) {}
    httpTurnAbortController = null;
  }
  hideTypingIndicator();
  try { LiveCaptions.stop(); } catch (e) {}
  setState('INTERRUPTED');
  setAiLine('');
  try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'barge_in' })); } catch (e) {}
  fullReply = '';
  setTimeout(() => {
    if (state === 'INTERRUPTED') {
      setState('LISTENING');
      resumeSTT();
    }
  }, 400);
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
  llmPending = false;
  aiSpeaking = false;
  await audioQ.unlock();
  await connect();
  if ($('topbar')) $('topbar').classList.remove('hidden');
  $('chatInput').classList.remove('hidden');
  $('interruptBtn').classList.remove('hidden');
  $('muteBtn').classList.remove('hidden');
  $('captionBtn').classList.remove('hidden');
  $('micMuteBtn').classList.remove('hidden');
  setIcon($('startBtn'), 'square');
  $('startBtn').classList.add('live');
  $('interruptBtn').disabled = false;
  setState('LISTENING');
  startClock();

  if (!isMobile) {
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
      resumeSTT();
    }
    log('[APP] Flow build v' + APP_BUILD + ' • desktop VAD + browser speech recognition');
  } else if (mobCanUse() && await startMobileCapture()) {
    // Preferred mobile path: one held mic stream, never cycles.
    log('[APP] Flow build v' + APP_BUILD + ' • mobile always-open capture');
    loadMicDevices();
  } else {
    // Fallback: browser speech recognition (its platform-owned stream may
    // still open/close between utterances — see the onend note in setupRecog).
    if (mobCanUse()) log('[STT] always-open capture failed — falling back to browser speech recognition');
    else log('[STT] fallback: server_stt=' + !!((serverConfig || {}).server_stt) +
      ' mediarecorder=' + (typeof MediaRecorder !== 'undefined') + ' — using browser speech recognition');
    log('[APP] Flow build v' + APP_BUILD + ' • browser speech recognition path');
    loadMicDevices();
    recog = setupRecog();
    if (recog) {
      resumeSTT();
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
  llmPending = false;
  aiSpeaking = false;
  recogFatal = true;
  lastInterim = ''; lastInterimAt = 0; awaitingFinal = false; lastSentText = '';
  userSaid('');
  try { LiveCaptions.stop(); } catch (e) {}
  setAiLine('');
  hideTypingIndicator();
  clearTimeout(speechTimer);
  clearTimeout(recogRestartTimer);
  recogRestartTimer = null;
  // Only here we truly stop recognition and close the mic stream
  try { 
    if (recog) {
      recog.stop(); 
      log('[STT] recognition stopped - mic stream closed');
    }
  } catch (e) {
    log('[STT] stop error:', e.name);
  }
  recog = null;
  if (vad) {
    try { vad.stop(); } catch (e) {}
    vad = null;
  }
  // Always-open mobile path: stop the recorder and close the held stream.
  // (No-ops on desktop / Web Speech fallback — recorder and stream are null.)
  try { stopMobileCapture(); } catch (e) {}
  try { window.__micLevel = 0; } catch (e) {}
  $('micLive').classList.remove('on');
  if ($('topbar')) $('topbar').classList.add('hidden');
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
    pauseSTT();
    // Mobile capture: drop any stranded utterance + in-flight transcription.
    // The held stream stays alive; the next speech starts a new recorder.
    mobUttGen++;
    mobAbortUtt();
    $('micLive').classList.remove('on');
    hearNote('Mic muted — I cannot hear you.');
  } else {
    lastInterim = '';
    lastInterimAt = 0;
    awaitingFinal = false;
    hearNote('');
    // Always resume on unmute while the session runs — even mid-turn, so
    // barge-in keeps working while the AI is speaking (isTurnActive stays
    // available for callers that need to reason about turn state).
    if (running) {
      resumeSTT();
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
$('scenario').onchange = (e) => {
  try { localStorage.setItem('scenario', e.target.value); } catch (err) {}
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: 'set_mode', mode: $('mode') ? $('mode').value : 'practice', scenario: e.target.value }));
  }
  const label = e.target.selectedOptions[0] ? e.target.selectedOptions[0].textContent : e.target.value;
  log('[SCENARIO] →', label);
};
$('correction').onchange = (e) => {
  try { localStorage.setItem('correction', e.target.value); } catch (err) {}
  ws && ws.readyState === 1 && ws.send(JSON.stringify({ type: 'set_correction', value: e.target.value }));
};
$('level').onchange = (e) => {
  try { localStorage.setItem('level', e.target.value); } catch (err) {}
  ws && ws.readyState === 1 && ws.send(JSON.stringify({ type: 'set_level', value: e.target.value }));
};
function applyLanguage(v, silent) {
  if (v === 'hinglish') v = 'english'; // retired: detection covers the mix
  sttLang = sttCodeFor(v);
  try { localStorage.setItem('lang', v); } catch (e) {}
  if (ws && ws.readyState === 1 && !silent) ws.send(JSON.stringify({ type: 'set_language', value: v }));
  if (recog && listening) {
    // restart recognition so the new language takes effect
    try { recog.lang = sttLang; recog.stop(); } catch (e) {}
  }
  log('[LANG] preferred →', v, '(' + sttLang + ') — replies follow the spoken language');
}
$('language').onchange = (e) => applyLanguage(e.target.value);
$('anim').onchange = (e) => {
  try { window.Orb && Orb.setStyle(e.target.value); } catch (err) {}
  log('[UI] animation →', e.target.value);
};
$('micDevice').onchange = (e) => {
  try { localStorage.setItem('micDevice', e.target.value || ''); } catch (err) {}
  log('[VAD] microphone →', e.target.selectedOptions[0] ? e.target.selectedOptions[0].textContent : e.target.value);
};
$('micTestBtn').onclick = async () => {
  // standalone 2.5s capture test: proves the mic path independent of STT
  const st = $('micTestStatus');
  // Always-open path: sample the HELD stream's live level — opening a second
  // getUserMedia here would fight the session for the mic hardware on mobile.
  if (typeof isMobile !== 'undefined' && isMobile && mobCaptureOn && mobStreamLive() && vad) {
    if (micMuted) {
      st.textContent = 'Mic is muted — unmute, then test.';
      return;
    }
    st.textContent = 'Listening for 2.5s — speak now…';
    mobTestPaused = true;
    mobUttGen++;
    mobAbortUtt();
    try {
      let peak = 0;
      const t0 = performance.now();
      await new Promise((res) => {
        const tick = () => {
          try { peak = Math.max(peak, (vad && vad.level) || 0); } catch (e) {}
          if (performance.now() - t0 < 2500) requestAnimationFrame(tick);
          else res();
        };
        tick();
      });
      const pct = Math.round(Math.min(1, peak * 8) * 100);
      const verdict = pct < 3 ? 'nothing heard — wrong mic or muted in system settings' : 'mic is capturing (peak ' + pct + '%)';
      st.textContent = verdict;
      log('[VAD] mic test peak:', pct + '% —', verdict);
    } finally {
      mobTestPaused = false;
    }
    return;
  }
  st.textContent = 'Listening for 2.5s — speak now…';
  let stream = null, ctx = null;
  let resumeRecognition = () => {};
  try {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new DOMException('Microphone access requires a secure context.', 'NotAllowedError');
    }
    resumeRecognition = await pauseRecognitionForMicTest();
    const dev = selectedMicDevice();
    stream = await navigator.mediaDevices.getUserMedia({ audio: dev ? { deviceId: { ideal: dev } } : true });
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === 'suspended') await ctx.resume();
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
    st.textContent = micTestErrorMessage(e);
    log('[VAD] mic test failed:', (e && e.name) || e);
  } finally {
    try { stream && stream.getTracks().forEach(t => t.stop()); } catch (e) {}
    try { ctx && ctx.close(); } catch (e) {}
    resumeRecognition();
  }
};
function closeDrawers() {
  $('panel').classList.add('hidden');
  $('history').classList.add('hidden');
  $('scrim').classList.add('hidden');
  document.body.classList.remove('drawer-open');
  $('settingsBtn').classList.remove('hidden');
  $('historyBtn').classList.remove('hidden');
}
function openPanel(o) {
  if (o) $('history').classList.add('hidden');
  $('panel').classList.toggle('hidden', !o);
  $('scrim').classList.toggle('hidden', !o);
  document.body.classList.toggle('drawer-open', o);
  icons();
}
function openHistory(o) {
  if (o) $('panel').classList.add('hidden');
  $('history').classList.toggle('hidden', !o);
  $('scrim').classList.toggle('hidden', !o);
  document.body.classList.toggle('drawer-open', o);
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
function openVoicePicker() {
  const picker = $('voicePicker');
  if (!picker) return;
  picker.classList.remove('hidden');
  document.body.classList.add('voice-picker-open');
  if (fishLibraryTab === 'explore') {
    const state = fishLibraryState(fishLibraryLanguage, $('fishLibrarySearch').value || '');
    if (!state.pages.length && !state.loadingPage && !state.error) {
      loadFishLibraryPage(state.language, state.title, 1);
    }
  }
  setTimeout(() => $('fishLibrarySearch').focus(), 0);
  icons();
}
function closeVoicePicker() {
  const picker = $('voicePicker');
  if (!picker || picker.classList.contains('hidden')) return;
  $('voiceFilterOverlay').classList.add('hidden');
  $('voiceFilterOpen').setAttribute('aria-expanded', 'false');
  picker.classList.add('hidden');
  document.body.classList.remove('voice-picker-open');
  stopFishLibraryPreview();
  $('voicePickerOpen').focus();
}
$('settingsBtn').onclick = () => {
  openPanel(true);
};
$('voicePickerOpen').onclick = openVoicePicker;
$('voicePickerClose').onclick = closeVoicePicker;
$('voicePicker').onclick = (event) => {
  if (event.target === $('voicePicker')) closeVoicePicker();
};
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('voiceFilterOverlay').classList.contains('hidden')) {
    setVoiceFilterOpen(false);
  } else if (event.key === 'Escape') {
    closeVoicePicker();
  }
});
$('panelClose').onclick = () => openPanel(false);
if ($('historyClose')) $('historyClose').onclick = () => openHistory(false);
$('historyBtn').onclick = () => {
  // icon toggles the history drawer both ways
  openHistory($('history').classList.contains('hidden'));
};
$('scrim').onclick = closeDrawers;
$('voice').onchange = (e) => {
  audioQ.fishVoice = e.target.value || '';
  const storageKey = (audioQ.engine === 'gemini') ? 'geminiVoice' : 'fishVoice';
  try { localStorage.setItem(storageKey, audioQ.fishVoice); } catch (err) {}
  if (storageKey === 'fishVoice') {
    try { localStorage.removeItem('fishVoiceName'); } catch (err) {}
  }
  try { ws && ws.readyState === 1 && ws.send(JSON.stringify({ type: 'set_voice', value: audioQ.fishVoice })); } catch (err) {}
  const label = e.target.selectedOptions[0] ? e.target.selectedOptions[0].textContent : e.target.value;
  const selectedName = $('selectedVoiceName');
  if (selectedName) selectedName.textContent = label;
  log(`[TTS] ${audioQ.engine === 'gemini' ? 'Gemini Live' : 'Fish Audio'} voice →`, label);
};
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
        const label = $('selectedVoiceName') ? $('selectedVoiceName').textContent : 'Voice';
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
