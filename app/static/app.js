/* Always-listening client: mic open from Start, auto-interrupt always on.
   mic -> local VAD -> held-stream server STT (or Web Speech fallback) -> WS -> streaming LLM -> Fish TTS. */
const APP_BUILD = 156; // bump with every app.js change; printed on session start so bug reports identify the build
const $ = (id) => document.getElementById(id);
let ws = null, sid = 's' + crypto.randomUUID().replace(/-/g, '');
let speechDurationS = null, speechEndedAt = 0, browserSpeechStartedAt = 0;
let state = 'IDLE';
let recog = null, vad = null, audioQ = new AudioQueue();
let listening = false, aiSpeaking = false, muted = false, running = false;
let micMuted = false, captionsOn = true;
let llmPending = false;
let fullReply = '';
let userWords = 0, userTurns = 0, sessStart = 0, clockTimer = null;
let speechTimer = null;
let userFadeTimer = null;
let sttLang = 'en-US'; // BCP-47 code for the browser-STT fallback; follows the preferred language
// turn state: accumulate interim and final speech transcripts across natural pauses
let lastInterim = '', lastInterimAt = 0, awaitingFinal = false;
let accumulatedTranscript = '', lastPlaybackEndedAt = 0;
function isPlaybackActive() {
  return !!(aiSpeaking || (typeof audioQ !== 'undefined' && audioQ && (audioQ.playing || audioQ.busy || (audioQ._pcmSources && audioQ._pcmSources.size > 0))));
}
let lastSentText = '', lastSentAt = 0;
// AI Response mode (friendly labels only — the provider behind them is server-side)
let respModes = [], respMode = 'gemini_live', respModeLabel = 'Gemini Live Preview';
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
  const defaultMode = cfg.default_response_mode || 'gemini_live';
  const ids = respModes.map(m => m.id);
  const versionKey = 'flow_resp_mode_cloud_v3';
  let migrated = false;
  try { migrated = !!localStorage.getItem(versionKey); } catch (e) {}
  if (!migrated) {
    try {
      localStorage.setItem(versionKey, '1');
      saved = defaultMode;
      localStorage.setItem('respMode', defaultMode);
    } catch (e) {}
  }
  respMode = (saved && respModes.some(m => m.id === saved && m.available)) ? saved : defaultMode;
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
      note.textContent = respModes.filter(m => !m.available).map(m => m.label + ': ' + (m.error || 'Check server API keys.')).join(' ');
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
  { id: 'english', label: 'English', stt: 'en-US' },
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
  if (!langId || langId === 'english') return 'en-US';
  const found = serverLanguages().filter(l => l.id === langId)[0];
  return (found && found.stt) || 'en-US';
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
  const defaultVoice = isGeminiLive ? (serverConfig.default_gemini_voice || 'Orbit') : (serverConfig.default_fish_voice || '');
  const storageKey = isGeminiLive ? 'geminiVoice' : 'fishVoice';

  v.innerHTML = '';
  const groups = {};
  voices.forEach(x => {
    const o = document.createElement('option');
    o.value = x.id;
    o.textContent = x.label;
    if (x.group) {
      if (!groups[x.group]) {
        const og = document.createElement('optgroup');
        og.label = x.group;
        groups[x.group] = og;
        v.appendChild(og);
      }
      groups[x.group].appendChild(o);
    } else {
      v.appendChild(o);
    }
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
      fs.textContent = serverConfig.fish_key_set ? 'Fish Audio ready' : 'FISH_API_KEY is missing — add it on the server';
    }
  }
}

function applyResponseMode(id, silent) {
  if (!respModes.some(mode => mode.id === id)) return;
  if (respMode !== id) {
    audioQ.cancel('response_mode_change');
    llmPending = false; fullReply = '';
    lastSentText = ''; lastSentAt = 0;
    if (httpTurnAbortController) httpTurnAbortController.abort();
    try { LiveCaptions.stop(); } catch (e) {}
  }
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
function isMissedOrHiccup(t) {
  if (!t || typeof t !== 'string') return false;
  const s = t.trim();
  return /^(sorry[,\s]+)?(i\s+)?(missed that|didn't catch that|did not catch that|catch that|had a little hiccup|couldn't hear)/i.test(s) ||
         /(sorry,?\s+)?i\s+missed\s+that/i.test(s) ||
         /(say that again|say it once more|repeat that|कृपया फिर से बोलें)/i.test(s) ||
         /(no speech detected|background noise|noise detected|empty audio|heard nothing intelligible)/i.test(s);
}
function hearNote(t, ms = 2600) {
  if (isMissedOrHiccup(t)) return;
  const el = $('hearNote');
  if (!el) return;
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

const CHAT_STORAGE_PREFIX = 'flow_chat_history_';

function getChatStorageKey(mode) {
  const m = mode || (typeof currentMode !== 'undefined' ? currentMode : 'free') || 'free';
  return CHAT_STORAGE_PREFIX + m + '_v2';
}

function getStoredChatHistory(mode) {
  const m = mode || (typeof currentMode !== 'undefined' ? currentMode : 'free') || 'free';
  try {
    const key = getChatStorageKey(m);
    const raw = localStorage.getItem(key);
    if (!raw) {
      // One-time fallback: migrate legacy history into free mode
      if (m === 'free') {
        const legacy = localStorage.getItem('flow_chat_history_v1');
        if (legacy) {
          const arr = JSON.parse(legacy);
          if (Array.isArray(arr) && arr.length) return arr;
        }
      }
      return [];
    }
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return [];
    const cleaned = arr.filter(x => !/tender management/i.test((x && x.text) || ''));
    if (cleaned.length !== arr.length) {
      try { localStorage.setItem(key, JSON.stringify(cleaned)); } catch (e) {}
    }
    return cleaned;
  } catch (e) {
    return [];
  }
}

function saveStoredChatHistory(history, mode) {
  const m = mode || (typeof currentMode !== 'undefined' ? currentMode : 'free') || 'free';
  try {
    const key = getChatStorageKey(m);
    localStorage.setItem(key, JSON.stringify((history || []).slice(-40)));
  } catch (e) {}
}

function loadChatHistoryUI(mode = currentMode) {
  const t = $('transcript');
  if (!t) return;
  const history = getStoredChatHistory(mode);
  t.innerHTML = '';
  if (history.length > 0) {
    const divider = document.createElement('div');
    divider.className = 'history-divider';
    const modeLabel = mode === 'training' ? 'English Training History' : mode === 'roleplay' ? 'Roleplay History' : mode === 'reading' ? 'Reading Practice History' : 'Previous Chat';
    divider.innerHTML = `<span>${modeLabel}</span>`;
    t.appendChild(divider);

    history.forEach(item => {
      const clean = plainText(item.text);
      if (item.who === 'You') {
        const gRes = checkGrammarJs(clean);
        if (gRes && gRes.matches && gRes.matches.length > 0) {
          const row = document.createElement('div');
          row.className = 'turn-row you past';

          const d = document.createElement('div');
          d.className = 'turn you past';
          d.innerHTML = formatGrammarBubble(clean, gRes.matches);
          d.dataset.raw = clean;
          d.dataset.fixed = gRes.fixedText;

          const fixBtn = makeFixButton(() => {
            d.innerHTML = `<span style="color: #ffffff !important;">${escHtml(gRes.fixedText)}</span>`;
            d.dataset.fixed = 'true';
            updateStoredTurn(clean, gRes.fixedText, mode);
          });

          d.onclick = () => {
            row.classList.toggle('show-fix');
          };

          row.appendChild(fixBtn);
          row.appendChild(d);
          t.appendChild(row);
          return;
        }
      }
      const d = document.createElement('div');
      d.className = 'turn ' + (item.who === 'You' ? 'you' : 'ai') + ' past';
      d.textContent = clean;
      t.appendChild(d);
    });
    t.scrollTop = t.scrollHeight;
  }
  refreshEmptyHint();
}

function clearChatHistory(mode = currentMode) {
  try {
    localStorage.removeItem(getChatStorageKey(mode));
  } catch (e) {}
  const t = $('transcript');
  if (t) t.innerHTML = '';
  refreshEmptyHint();
  if (ws && ws.readyState === 1) {
    try { ws.send(JSON.stringify({ type: 'clear_history', mode })); } catch (e) {}
  }
  log('[HISTORY] conversation cleared for mode →', mode);
}


function makeFixButton(onFix) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'fix-grammar-btn';
  btn.title = 'Fix grammar';
  btn.setAttribute('aria-label', 'Fix grammar');
  // Icon only - Sparkles SVG
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></svg>';
  btn.onclick = (e) => {
    e.stopPropagation();
    onFix();
    btn.remove();
  };
  return btn;
}

function checkGrammarJs(text) {
  if (!text) return null;
  const t = text.trim();

  const PAST_MAP = {
    'went': 'go', 'saw': 'see', 'came': 'come', 'ate': 'eat', 'did': 'do',
    'had': 'have', 'took': 'take', 'made': 'make', 'knew': 'know', 'spoke': 'speak',
    'wrote': 'write', 'bought': 'buy', 'gave': 'give', 'told': 'tell', 'found': 'find',
    'heard': 'hear', 'thought': 'think', 'understood': 'understand', 'brought': 'bring',
    'drove': 'drive', 'felt': 'feel', 'kept': 'keep', 'left': 'leave', 'ran': 'run',
    'met': 'meet', 'said': 'say', 'sent': 'send', 'spent': 'spend', 'slept': 'sleep',
    'broke': 'break', 'drank': 'drink'
  };

  const PARTICIPLE_MAP = {
    'went': 'gone', 'saw': 'seen', 'ate': 'eaten', 'came': 'come', 'took': 'taken',
    'wrote': 'written', 'spoke': 'spoken', 'gave': 'given', 'broke': 'broken',
    'drank': 'drunk', 'did': 'done'
  };

  const PRES_TO_PAST = {
    'go': 'went', 'come': 'came', 'eat': 'ate', 'work': 'worked', 'do': 'did',
    'see': 'saw', 'meet': 'met', 'sleep': 'slept', 'buy': 'bought', 'watch': 'watched',
    'talk': 'talked'
  };

  const rules = [
    {
      rx: /\b(did\s+not|didn'?t|did\s+you)\s+(went|saw|came|ate|did|had|took|made|knew|spoke|wrote|bought|gave|told|found|heard|thought|understood|brought|drove|felt|kept|left|ran|met|said|sent|spent|slept|broke|drank)\b/i,
      fix: (m) => `${m[1]} ${PAST_MAP[m[2].toLowerCase()] || m[2]}`
    },
    {
      rx: /\b(have|has|having)\s+(went|saw|ate|came|took|wrote|spoke|gave|broke|drank|did)\b/i,
      fix: (m) => `${m[1]} ${PARTICIPLE_MAP[m[2].toLowerCase()] || m[2]}`
    },
    {
      rx: /\b(could|would|should|can|might|must)\s+(went|saw|ate|came|took|done|spoke|wrote|bought|gave)\b/i,
      fix: (m) => `${m[1]} ${PAST_MAP[m[2].toLowerCase()] || (m[2].toLowerCase() === 'done' ? 'do' : m[2])}`
    },
    {
      rx: /\b(should|could|must|can|might)\s+to\s+(\w+)\b/i,
      fix: (m) => `${m[1]} ${m[2]}`
    },
    {
      rx: /\byesterday\s+i\s+(go|come|eat|work|do|see|meet|sleep|buy|watch|talk)\b/i,
      fix: (m) => `yesterday I ${PRES_TO_PAST[m[1].toLowerCase()] || m[1]}`
    },
    {
      rx: /\bi\s+(go|come|eat|work|do|see|meet|sleep|buy|watch|talk)\s+((?:to\s+)?(?:\w+\s+){0,3})yesterday\b/i,
      fix: (m) => `I ${PRES_TO_PAST[m[1].toLowerCase()] || m[1]} ${m[2] || ''}yesterday`
    },
    {
      rx: /\blast\s+(night|week|month|year)\s+i\s+(go|come|eat|work|do|see|meet|sleep|buy|watch|talk)\b/i,
      fix: (m) => `last ${m[1]} I ${PRES_TO_PAST[m[2].toLowerCase()] || m[2]}`
    },
    {
      rx: /\bi\s+(?:am|'m)\s+agree\b/i,
      fix: () => "I agree"
    },
    {
      rx: /\bfrom\s+last\s+(\d+|few|several)\s+years?\b/i,
      fix: (m) => `for the last ${m[1]} years`
    },
    {
      rx: /\bi\s+am\s+(working|living|staying)\s+(from|since)\s+(\d+)\s+years?\b/i,
      fix: (m) => `I have been ${m[1]} for ${m[3]} years`
    },
    {
      rx: /\b(went|go|going)\s+office\b/i,
      fix: (m) => `${m[1]} to the office`
    },
    {
      rx: /\bdiscuss\s+about\b/i,
      fix: () => "discuss"
    },
    {
      rx: /\bdo\s+a\s+(decision|mistake)\b/i,
      fix: (m) => `make a ${m[1]}`
    },
    {
      rx: /\blisten\s+music\b/i,
      fix: () => "listen to music"
    },
    {
      rx: /\bmarried\s+with\b/i,
      fix: () => "married to"
    },
    {
      rx: /\bcongratulate\s+(?:\w+\s+)?for\b/i,
      fix: (m) => m[0].replace(/\bfor\b/i, 'on')
    },
    {
      rx: /\bgood\s+in\s+(english|maths?|sports|studies)\b/i,
      fix: (m) => `good at ${m[1]}`
    },
    {
      rx: /\bprefer\s+(\w+)\s+than\s+(\w+)\b/i,
      fix: (m) => `prefer ${m[1]} to ${m[2]}`
    },
    {
      rx: /\btold\s+to\s+me\b/i,
      fix: () => "told me"
    },
    {
      rx: /\bexplained\s+me\b/i,
      fix: () => "explained to me"
    },
    {
      rx: /\b(he|she|it)\s+don'?t\b/i,
      fix: (m) => `${m[1]} doesn't`
    },
    {
      rx: /\bi\s+doesn'?t\b/i,
      fix: () => "I don't"
    },
    {
      rx: /\b(he|she|it)\s+have\b/i,
      fix: (m) => `${m[1]} has`
    },
    {
      rx: /\b(they|we)\s+has\b/i,
      fix: (m) => `${m[1]} have`
    },
    {
      rx: /\b(they|we|you)\s+was\b/i,
      fix: (m) => `${m[1]} were`
    },
    {
      rx: /\bthere\s+is\s+(\d+|two|three|four|five|many|several)\b/i,
      fix: (m) => `there are ${m[1]}`
    },
    {
      rx: /\b(he|she)\s+(go|come|want|know|like|need|play|think)\b/i,
      fix: (m) => {
        const v = m[2].toLowerCase();
        const third = v === 'go' ? 'goes' : v === 'come' ? 'comes' : `${v}s`;
        return `${m[1]} ${third}`;
      }
    },
    {
      rx: /\b(i)\s+is\b/i,
      fix: () => "I am"
    },
    {
      rx: /\b(you|we|they)\s+is\b/i,
      fix: (m) => `${m[1]} are`
    },
    {
      rx: /\b(he|she|it)\s+are\b/i,
      fix: (m) => `${m[1]} is`
    },
    {
      rx: /\b(return|revert)\s+back\b/i,
      fix: (m) => m[1]
    },
    {
      rx: /\brepeat\s+again\b/i,
      fix: () => "repeat"
    },
    {
      rx: /\bmore\s+(better|easier|faster|taller|smarter|bigger|smaller)\b/i,
      fix: (m) => m[1]
    },
    {
      rx: /\bpeoples\b/i,
      fix: () => "people"
    },
    {
      rx: /\b(childs|childrens)\b/i,
      fix: () => "children"
    },
    {
      rx: /\binformations\b/i,
      fix: () => "information"
    },
    {
      rx: /\bfurnitures\b/i,
      fix: () => "furniture"
    },
    {
      rx: /\ba\s+(apple|orange|hour|umbrella|egg|island|uncle)\b/i,
      fix: (m) => `an ${m[1]}`
    },
    {
      rx: /\ban\s+(car|book|dog|cat|house|friend|man|woman|boy|girl|table|chair|computer|phone|pen)\b/i,
      fix: (m) => `a ${m[1]}`
    },
    {
      rx: /\bone\s+of\s+my\s+friend\b/i,
      fix: () => "one of my friends"
    },
    {
      rx: /\b(why|where|what|how)\s+(you\s+are|he\s+is|they\s+are|she\s+is)\b/i,
      fix: (m) => {
        const pair = m[2].toLowerCase();
        let inv = pair;
        if (pair === 'you are') inv = 'are you';
        else if (pair === 'he is') inv = 'is he';
        else if (pair === 'they are') inv = 'are they';
        else if (pair === 'she is') inv = 'is she';
        return `${m[1]} ${inv}`;
      }
    }
  ];

  let matches = [];
  let fixedText = t;

  for (const r of rules) {
    const m = fixedText.match(r.rx);
    if (m) {
      const orig = m[0];
      const repl = r.fix(m);
      if (orig.toLowerCase() !== repl.toLowerCase()) {
        matches.push({ original: orig, replacement: repl });
        fixedText = fixedText.replace(orig, repl);
      }
    }
  }

  if (matches.length === 0) return null;
  return { original: t, fixedText, matches };
}

function formatGrammarBubble(cleanText, matches) {
  let formatted = escHtml(cleanText);
  const sorted = [...matches].sort((a, b) => ((b.original || '').length - (a.original || '').length));
  for (const m of sorted) {
    if (!m.original) continue;
    const escOrig = escHtml(m.original);
    const escRepl = escHtml(m.replacement || '');
    const rx = new RegExp(escOrig.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&'), 'i');
    const tip = escRepl ? ` title="Fix: ${escRepl}"` : '';
    formatted = formatted.replace(rx, `<span class="err-word"${tip}>${escOrig}</span>`);
  }
  return formatted;
}

function updateStoredTurn(oldText, newText, mode = currentMode) {
  try {
    const hist = getStoredChatHistory(mode);
    for (let i = hist.length - 1; i >= 0; i--) {
      if (hist[i].who === 'You' && (hist[i].text === oldText || hist[i].text === newText)) {
        hist[i].text = newText;
        break;
      }
    }
    saveStoredChatHistory(hist, mode);
  } catch (e) {}
}

function applyServerGrammarCorrection(mistakes, fixedText) {
  const t = $('transcript');
  if (!t || !mistakes || mistakes.length === 0) return;
  const turns = t.querySelectorAll('.turn.you');
  if (!turns || turns.length === 0) return;
  const lastTurn = turns[turns.length - 1];
  if (!lastTurn || lastTurn.dataset.fixed === 'true') return;

  const raw = lastTurn.dataset.raw || lastTurn.textContent;
  let row = lastTurn.closest('.turn-row.you');
  if (!row) {
    row = document.createElement('div');
    row.className = 'turn-row you';
    lastTurn.parentNode.insertBefore(row, lastTurn);
    row.appendChild(lastTurn);
  }

  let fixBtn = row.querySelector('.fix-grammar-btn');
  if (!fixBtn) {
    fixBtn = makeFixButton(() => {
      lastTurn.innerHTML = `<span style="color: #ffffff !important;">${escHtml(fixedText)}</span>`;
      lastTurn.dataset.fixed = 'true';
      updateStoredTurn(raw, fixedText);
    });
    row.insertBefore(fixBtn, lastTurn);
  }

  lastTurn.innerHTML = formatGrammarBubble(raw, mistakes);
  lastTurn.dataset.raw = raw;
  lastTurn.dataset.fixed = fixedText;
  lastTurn.onclick = () => {
    row.classList.toggle('show-fix');
  };
}

function addTurn(who, text, save = true) {
  hideTypingIndicator();
  const clean = plainText(text);
  if (!clean.trim() || !/[\p{L}\p{N}]/u.test(clean) || (who !== 'You' && isMissedOrHiccup(clean))) return;

  const t = $('transcript');
  const d = document.createElement('div');
  d.className = 'turn ' + (who === 'You' ? 'you' : 'ai');

  if (who === 'You') {
    const gRes = checkGrammarJs(clean);
    if (gRes && gRes.matches && gRes.matches.length > 0) {
      const row = document.createElement('div');
      row.className = 'turn-row you';

      d.innerHTML = formatGrammarBubble(clean, gRes.matches);
      d.dataset.raw = clean;
      d.dataset.fixed = gRes.fixedText;

      const fixBtn = makeFixButton(() => {
        d.innerHTML = `<span style="color: #ffffff !important;">${escHtml(gRes.fixedText)}</span>`;
        d.dataset.fixed = 'true';
        updateStoredTurn(clean, gRes.fixedText);
      });

      d.onclick = () => {
        row.classList.toggle('show-fix');
      };

      row.appendChild(fixBtn);
      row.appendChild(d);
      if (t) {
        t.appendChild(row);
        if (window.gsap) {
          gsap.fromTo(row,
            { opacity: 0, y: 14, filter: 'blur(8px)', scale: 0.98 },
            { opacity: 1, y: 0, filter: 'blur(0px)', scale: 1, duration: 0.42, ease: 'power2.out' }
          );
        }
        t.scrollTop = t.scrollHeight;
      }
      refreshEmptyHint();

      if (save) {
        const hist = getStoredChatHistory(currentMode);
        hist.push({ who, text: clean, t: Date.now() });
        saveStoredChatHistory(hist, currentMode);
      }
      return;
    }
  }

  d.textContent = clean;
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
    const hist = getStoredChatHistory(currentMode);
    hist.push({ who, text: clean, t: Date.now() });
    saveStoredChatHistory(hist, currentMode);
  }
}
// --- aiLine emphasis: **key words** render white, everything else gray ---
function escHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}
function plainText(t) {
  return (t || '')
    .replace(/\*\*/g, '')
    .replace(/\[[^\]]*\]/g, '')
    .replace(/[\[\]]/g, '')
    .trim();
}
function emphasize(t) {
  t = escHtml(t || '');
  t = t.replace(/\[[^\]]*\]/g, '').replace(/[\[\]]/g, ''); // strip emotion tags and stray brackets for display
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
function maskAbbreviations(t) { return protectAbbr(t); }
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
          el.style.filter = '';
          el.style.letterSpacing = '';
          gsap.set(el, { opacity: 1, y: 0, scale: 1, letterSpacing: 'normal', clearProps: 'all' });
          aiSmokeTimeline = null;
        }
      });
      // Ethereal smoky vapor dissipation: text floats upward, softens into mist, and gently evaporates
      aiSmokeTimeline.to(el, {
        opacity: 0,
        y: -18,
        scale: 1.05,
        letterSpacing: '0.05em',
        filter: 'blur(12px)',
        duration: 1.3,
        ease: 'power2.out'
      });
    } else {
      el.innerHTML = '';
      el.style.filter = '';
    }
  }, delayMs);
}

let aiDissolveTimeline = null;
function dissolveAiLine(text) {
  if (isMissedOrHiccup(text)) return;
  clearTimeout(aiSmokeFadeTimer);
  if (aiSmokeTimeline) {
    aiSmokeTimeline.kill();
    aiSmokeTimeline = null;
  }
  const el = $('aiLine');
  if (!el) return;
  const cleanSpoken = plainText(text);
  if (!cleanSpoken || !cleanSpoken.trim() || !/[\p{L}\p{N}]/u.test(cleanSpoken)) {
    if (window.gsap) {
      gsap.to(el, {
        opacity: 0,
        y: -12,
        scale: 1.03,
        filter: 'blur(8px)',
        duration: 0.35,
        ease: 'power2.out',
        onComplete: () => {
          el.innerHTML = '';
          el.style.filter = '';
          gsap.set(el, { opacity: 1, y: 0, scale: 1, clearProps: 'all' });
        }
      });
    } else {
      el.innerHTML = '';
      el.style.filter = '';
    }
    return;
  }
  const newHtml = emphasize(text);
  if (!window.gsap) {
    el.innerHTML = newHtml;
    el.style.filter = '';
    return;
  }
  if (aiDissolveTimeline) {
    aiDissolveTimeline.kill();
    aiDissolveTimeline = null;
  }
  el.style.filter = '';
  const currentContent = el.textContent.trim();
  const currentOpacity = parseFloat(getComputedStyle(el).opacity || '1');
  if (!currentContent || currentOpacity < 0.05) {
    el.innerHTML = newHtml;
    gsap.fromTo(el,
      { opacity: 0, y: 8, filter: 'blur(4px)', scale: 0.99 },
      {
        opacity: 1,
        y: 0,
        filter: 'blur(0px)',
        scale: 1,
        duration: 0.35,
        ease: 'power2.out',
        overwrite: true,
        onComplete: () => {
          el.style.filter = '';
          gsap.set(el, { clearProps: 'filter' });
        }
      }
    );
    return;
  }
  aiDissolveTimeline = gsap.timeline({
    onComplete: () => {
      el.style.filter = '';
      gsap.set(el, { clearProps: 'filter' });
      aiDissolveTimeline = null;
    }
  });
  aiDissolveTimeline
    .to(el, {
      opacity: 0,
      y: -8,
      filter: 'blur(6px)',
      scale: 1.02,
      duration: 0.18,
      ease: 'power2.in'
    })
    .call(() => {
      el.innerHTML = newHtml;
    })
    .fromTo(el,
      { opacity: 0, y: 8, filter: 'blur(4px)', scale: 0.99 },
      {
        opacity: 1,
        y: 0,
        filter: 'blur(0px)',
        scale: 1,
        duration: 0.32,
        ease: 'power2.out',
        onComplete: () => {
          el.style.filter = '';
          gsap.set(el, { clearProps: 'filter' });
        }
      }
    );
}

function renderLiveCaption(txt) {
  if (!txt) return;
  const el = $('aiLine');
  if (!el) return;

  let clean = stripMetaReply(txt).replace(/\s+/g, ' ').trim();
  clean = maskAbbreviations(clean);

  // Split into sentences so multi-sentence turns display the current spoken sentence in real time
  const sentences = clean.match(/[^.!?]+[.!?]+["']?|[^.!?]+$/g) || [clean];
  const activeSentence = (sentences[sentences.length - 1] || '').split(ABBR_DOT).join('.').trim();
  if (!activeSentence) return;

  const html = emphasize(activeSentence);
  if (html && el.innerHTML !== html) {
    clearTimeout(aiSmokeFadeTimer);
    if (aiSmokeTimeline) {
      aiSmokeTimeline.kill();
      aiSmokeTimeline = null;
    }
    if (aiDissolveTimeline) {
      aiDissolveTimeline.kill();
      aiDissolveTimeline = null;
    }
    el.innerHTML = html;
    el.style.filter = '';
    try { if (window.gsap) gsap.set(el, { opacity: 1, y: 0, clearProps: 'filter' }); } catch (e) {}
  }
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
  const masked = protectAbbr(clean);
  const parts = masked.match(/[^.!?]+[.!?]+["']?|[^.!?]+$/g) || [masked];
  const sents = [];
  const weights = [];
  let totWeight = 0;
  for (let i = 0; i < parts.length; i++) {
    const s = parts[i].trim();
    if (!s) continue;
    sents.push(s);
    // Weight length + natural inter-sentence boundary pause buffer
    const w = Math.max(10, s.length) + 14;
    weights.push(w);
    totWeight += w;
  }
  if (!sents.length) return '';

  const played = (prog && prog.playedSec) ? Math.max(0, prog.playedSec) : 0;
  const recvSec = (prog && prog.recvSec) ? prog.recvSec : 0;
  let idx = 0;

  if (!prog || recvSec < 0.05 || played <= 0.02) {
    // Audio has not yet started or is at the very first frames: preview the first sentence
    idx = 0;
  } else {
    const totalDur = Math.max(recvSec, played, 0.5);
    let accumulated = 0;
    for (let i = 0; i < sents.length; i++) {
      const segDur = (weights[i] / totWeight) * totalDur;
      if (played >= accumulated && (played < accumulated + segDur || i === sents.length - 1)) {
        idx = i;
        break;
      }
      accumulated += segDur;
    }
  }

  // Ensure index advances with playback
  idx = Math.max(idx, Math.min(pacedSentIdx, sents.length - 1));
  pacedSentIdx = idx;

  const out = sents[idx];
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
const LIVE_MIN_DWELL_MS = 250;
const LiveCaptions = {
  timer: null,
  buf: '',
  done: false,
  lastPaint: '',
  lastPaintAt: 0,
  start() {
    // new turn: reset pacing + buffer, (re)start the audio-clock ticker
    try { if (this.timer) clearInterval(this.timer); } catch (e) {}
    clearTimeout(aiSmokeFadeTimer);
    if (aiSmokeTimeline) { aiSmokeTimeline.kill(); aiSmokeTimeline = null; }
    if (aiDissolveTimeline) { aiDissolveTimeline.kill(); aiDissolveTimeline = null; }
    const el = $('aiLine');
    if (el) {
      el.style.filter = '';
      try { if (window.gsap) gsap.set(el, { opacity: 1, y: 0, clearProps: 'filter' }); } catch (e) {}
    }
    this.buf = '';
    this.done = false;
    this.lastPaint = '';
    this.lastPaintAt = 0;
    pacedSentIdx = 0;
    this.timer = setInterval(() => this.tick(), 60);
  },
  reset() {
    // new user turn before any reply audio: stop and forget everything
    try { if (this.timer) clearInterval(this.timer); } catch (e) {}
    clearTimeout(aiSmokeFadeTimer);
    if (aiSmokeTimeline) { aiSmokeTimeline.kill(); aiSmokeTimeline = null; }
    if (aiDissolveTimeline) { aiDissolveTimeline.kill(); aiDissolveTimeline = null; }
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
    this.buf += (tok || '');
    this.tick();
  },
  update(text) {
    if (!this.timer) this.start();
    this.buf = text || '';
    this.tick();
  },
  finish(text) {
    clearTimeout(aiSmokeFadeTimer);
    if (aiSmokeTimeline) { aiSmokeTimeline.kill(); aiSmokeTimeline = null; }
    if (aiDissolveTimeline) { aiDissolveTimeline.kill(); aiDissolveTimeline = null; }
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
      clearTimeout(aiSmokeFadeTimer);
      if (aiSmokeTimeline) {
        aiSmokeTimeline.kill();
        aiSmokeTimeline = null;
      }
      if (aiDissolveTimeline) {
        aiDissolveTimeline.kill();
        aiDissolveTimeline = null;
      }
      el.style.filter = '';
      el.innerHTML = html;
      try {
        if (window.gsap) {
          gsap.fromTo(el,
            { opacity: 0.5, y: 4, filter: 'blur(3px)' },
            {
              opacity: 1,
              y: 0,
              filter: 'blur(0px)',
              duration: 0.22,
              ease: 'power2.out',
              overwrite: true,
              onComplete: () => {
                el.style.filter = '';
                gsap.set(el, { clearProps: 'filter' });
              }
            }
          );
        }
      } catch (e) {}
      this.lastPaint = html;
      this.lastPaintAt = now;
    }
    if (this.done) {
      // turn text is final: stop once the voice has caught up with it
      let prog = null;
      try { prog = audioQ.liveProgress(); } catch (e) {}
      if (!prog || !audioQ.busy || (prog.recvSec > 0.25 && prog.playedSec >= prog.recvSec - 0.05)) {
        this.stop();
        smokeDissolveAiLine(1400);
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

let currentMode = 'free';

function setLearningMode(m, userInitiated = true) {
  if (!m || (m !== 'free' && m !== 'reading' && m !== 'training' && m !== 'practice' && m !== 'roleplay' && m !== 'listening')) m = 'free';
  const modeChanged = currentMode !== m;
  if (modeChanged) {
    lastSentText = ''; lastSentAt = 0;
    audioQ.cancel('mode_change');
    llmPending = false;
    if (httpTurnAbortController) httpTurnAbortController.abort();
    mobUttGen++;
  }
  currentMode = m;
  if (modeChanged && window.FlowLearning) FlowLearning.showMode(m);
  try { localStorage.setItem('mode', m); } catch (e) {}
  if ($('mode')) $('mode').value = m;

  // Highlight selected mode button in top segmented control
  const btns = document.querySelectorAll('#modeSelector .mode-btn');
  btns.forEach(btn => {
    const isAct = (btn.getAttribute('data-mode') === m);
    btn.classList.toggle('active', isAct);
    btn.setAttribute('aria-selected', isAct ? 'true' : 'false');
  });

  const opt = $('practiceOptions');
  if (opt) {
    opt.classList.toggle('hidden', m !== 'practice');
  }

  if (m === 'reading') {
    if ($('trainingBadge')) $('trainingBadge').classList.add('hidden');
    if ($('roleplayBadge')) $('roleplayBadge').classList.add('hidden');
    if (userInitiated) setReadingEnabled(true);
  } else if (m === 'training') {
    if (reading.active) setReadingEnabled(false);
    if ($('roleplayBadge')) $('roleplayBadge').classList.add('hidden');
    if ($('trainingBadge')) $('trainingBadge').classList.remove('hidden');
  } else if (m === 'roleplay') {
    if (reading.active) setReadingEnabled(false);
    if ($('trainingBadge')) $('trainingBadge').classList.add('hidden');
    if ($('roleplayBadge')) $('roleplayBadge').classList.remove('hidden');
  } else {
    // free or practice
    if (reading.active) setReadingEnabled(false);
    if ($('trainingBadge')) $('trainingBadge').classList.add('hidden');
    if ($('roleplayBadge')) $('roleplayBadge').classList.add('hidden');
  }

  const stage = document.querySelector('.stage');
  if (stage) {
    stage.classList.toggle('listening-on', m === 'listening');
  }

  loadChatHistoryUI(m);

  if (userInitiated && ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: 'set_mode', mode: m }));
    const past = getStoredChatHistory(m).slice(-10);
    ws.send(JSON.stringify({
      type: 'sync_history',
      mode: m,
      turns: past.map(x => ({ role: x.who === 'You' ? 'user' : 'assistant', content: x.text }))
    }));
  }
  log('[MODE] switched to →', m);
}

function updateModeUI() {
  setLearningMode(currentMode, false);
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
    setLearningMode(savedMode, false);
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
  if (m.turn && !/^r\d|^greet/.test(String(m.turn))) {
    if (audioQ.deadTurns.has(m.turn)) return;
    if (audioQ._turnId && String(m.turn) !== String(audioQ._turnId)) return;
  }
  if (m.type === 'ready') { if (running) setState('LISTENING'); else setState('IDLE'); }
  else if (m.type === 'status') {
    setState(m.state, m.note);
    if (m.state === 'THINKING' || m.state === 'SPEAKING') {
      llmPending = true;
      if (!$('aiTyping')) showTypingIndicator();
    }
  }
  else if (m.type === 'live_caption') {
    if (state === 'INTERRUPTED' || audioQ.cancelled || (m.turn && audioQ.deadTurns.has(m.turn)) || (m.req_id && audioQ.deadTurns.has(m.req_id))) {
      return;
    }
    const cap = (m.text || '').trim();
    if (!cap) return;
    recordAiLine(cap);
    if (!currentTurnAiText.includes(cap)) {
      currentTurnAiText = cap;
    }
    if (audioQ.engine === 'gemini') {
      LiveCaptions.update(cap);
    } else {
      renderLiveCaption(cap);
    }
  }
  else if (m.type === 'llm_token') {
    if (state === 'INTERRUPTED' || audioQ.cancelled || (m.turn && audioQ.deadTurns.has(m.turn)) || (m.req_id && audioQ.deadTurns.has(m.req_id))) {
      return;
    }
    fullReply += m.token;
    currentTurnAiText += m.token;
    recordAiLine(m.token);
    recordAiLine(currentTurnAiText);
    if (audioQ.engine === 'gemini') {
      LiveCaptions.update(currentTurnAiText);
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
    setState('SPEAKING');
    if (!$('aiTyping')) showTypingIndicator();
    if (audioQ.engine === 'gemini') {
      LiveCaptions.start();
    }
  }
  else if (m.type === 'tts_sentence') {
    if (isMissedOrHiccup(m.text)) return;
    if (/^greet-/.test(String(m.turn)) && audioQ.cancelled && !audioQ.deadTurns.has(m.turn)) audioQ.startTurn(m.turn);
    if (/^r\d/.test(String(m.turn)) && reading.active && audioQ.cancelled && !audioQ.deadTurns.has(m.turn)) audioQ.startTurn(m.turn);
    const spoken = (m.text || '').replace(/\[[^\]]*\]/g, '').replace(/[\[\]]/g, '').trim();
    if (!spoken || !/[\p{L}\p{N}]/u.test(spoken)) return;
    // Reading-speech turn tags (r{gen}-intro/fb/final) echo-track so the AI's
    // own voice can be stripped from captured transcripts later
    if (m.turn && /^r\d/.test(String(m.turn)) && !audioQ.deadTurns.has(m.turn)) {
      reading.aiLines.push(m.text || '');
      if (reading.aiLines.length > 6) reading.aiLines.shift();
      if (/r\d+-intro/.test(String(m.turn))) {
        reading.introSpeaking = true;
      }
    }
    const stale = !!(m.turn && audioQ.deadTurns.has(m.turn));
    if (m.lat && !stale) audioQ.turnLat = m.lat;  // server stages for the latency block
    recordAiLine(m.text);
    if (!currentTurnAiText.includes(m.text)) {
      currentTurnAiText = (currentTurnAiText + ' ' + m.text).trim();
    }
    recordAiLine(currentTurnAiText);
    audioQ.enqueue(m.text, { stream: !!m.first, turn: m.turn, req_id: m.req_id || m.turn });
  }
  else if (m.type === 'resp_mode') { labelFor(m.value); log('[AI] mode confirmed:', respModeLabel); }
  else if (m.type === 'reading_state') readingHandleState(m);
  else if (m.type === 'reading_passage') readingRenderPassage(m);
  else if (m.type === 'reading_retry') readingRenderRetry(m);
  else if (m.type === 'reading_continue_prompt') readingRenderContinuePrompt(m);
  else if (m.type === 'reading_next_round') readingRenderNextRound(m);
  else if (m.type === 'reading_feedback') readingRenderFeedback(m);
  else if (m.type === 'reading_summary') readingRenderSummary(m);
  else if (m.type === 'reading_error') readingError(m);
  else if (m.type === 'live_audio_chunk') {
    if (state === 'INTERRUPTED' || audioQ.cancelled || (m.turn && audioQ.deadTurns.has(m.turn)) || (m.req_id && audioQ.deadTurns.has(m.req_id))) {
      return;
    }
    audioQ.playLivePcmChunk(m.data, 24000, m.turn);
    if (audioQ.engine === 'gemini' && !LiveCaptions.timer) {
      LiveCaptions.start(m.turn);
    }
  }
  else if (m.type === 'response_mode') {
    labelFor(m.value);
    if (m.available === false && m.message) hearNote(m.message, 6000);
    log('[AI] mode confirmed:', respModeLabel);
  }
  else if (m.type === 'llm_done') {
    const reqId = m.req_id || m.turn || String(turnSeq);
    log(`[LLM req_id=${reqId}] LLM response received in client (chars=${(m.text || '').length})`);
    hideTypingIndicator();
    if (window.FlowLearning) FlowLearning.refresh();
    fullReply = '';
    llmPending = false;
    accumulatedTranscript = '';
    lastInterim = '';
    lastInterimAt = 0;
    const rawDisplay = (m.display_text !== undefined && m.display_text !== null && m.display_text !== '') ? m.display_text : m.text;
    let displayText = plainText(rawDisplay);
    if (!displayText || !displayText.trim() || !/[\p{L}\p{N}]/u.test(displayText)) {
      log(`[UI req_id=${reqId}] suppressed bracket-only or empty response:`, rawDisplay);
      if (running && state !== 'INTERRUPTED') setState('LISTENING');
      return;
    }
    if (isMissedOrHiccup(displayText) || isMissedOrHiccup(m.text)) {
      log(`[UI req_id=${reqId}] suppressed missed/hiccup message:`, displayText);
      if (running && state !== 'INTERRUPTED') setState('LISTENING');
      return;
    }
    recordAiLine(displayText);
    if (!currentTurnAiText.includes(displayText)) {
      currentTurnAiText = (currentTurnAiText + ' ' + displayText).trim();
    }
    recordAiLine(currentTurnAiText);
    // Live center line mirrors the voice in real time: LiveCaptions synchronizes
    // sentences to the PCM audio clock; Fish path keeps its per-sentence display.
    const centerText = m.live ? (liveChunkText(displayText) || displayText) : displayText;
    addTurn('Partner', displayText);
    if (m.mistakes && m.mistakes.length > 0 && m.fixed_text) {
      applyServerGrammarCorrection(m.mistakes, m.fixed_text);
    }
    log(`[UI req_id=${reqId}] assistant message rendered in chat`);
    if (m.live || audioQ.engine === 'gemini') {
      LiveCaptions.finish(displayText);
    }
    if (m.correction && m.correction.length) log('[COACH]', JSON.stringify(m.correction));
    if (!audioQ.busy && audioQ.engine !== 'gemini') {
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
    accumulatedTranscript = '';
    lastInterim = '';
    lastInterimAt = 0;
    aiSpeaking = false;
    if (m.turn) audioQ.deadTurns.add(m.turn);
    if (m.req_id) audioQ.deadTurns.add(m.req_id);
    audioQ.cancel('llm_cancelled');
    try { LiveCaptions.stop(); } catch (e) {}
    setAiLine('');
    if (running && !audioQ.busy) {
      setState('LISTENING');
    }
  }
  else if (m.type === 'mode') {
    if (m.mode === 'free' || m.mode === 'reading' || m.mode === 'training' || m.mode === 'practice' || m.mode === 'roleplay' || m.mode === 'listening') {
      setLearningMode(m.mode, false);
    }
    log('[MODE]', m.mode);
  }
  else if (m.type === 'language') {
    if (m.value && $('language')) {
      $('language').value = m.value;
      applyLanguage(m.value, true);
    }
    log('[LANG] synchronized →', m.value);
  }
  else if (m.type === 'roleplay_update') {
    if ($('roleplayTopic')) {
      $('roleplayTopic').textContent = m.title || 'Roleplay: Choose a Scenario';
    }
    if (currentMode === 'roleplay' && $('roleplayBadge')) {
      $('roleplayBadge').classList.remove('hidden');
    }
    log('[ROLEPLAY] scenario →', m.title || 'updated');
  }
  else if (m.type === 'training_update') {
    if (m.lesson && $('trainingTopic')) {
      $('trainingTopic').textContent = m.lesson.title || m.lesson.concept || 'English Training';
    }
    if (currentMode === 'training' && $('trainingBadge')) {
      $('trainingBadge').classList.remove('hidden');
    }
    log('[TRAINING] topic →', m.lesson ? m.lesson.title : 'updated');
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
    if (m.scope === 'configuration') hearNote(m.message, 8000);
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
    history: getStoredChatHistory(currentMode).slice(-10).map(x => ({ role: x.who === 'You' ? 'user' : 'assistant', content: x.text })),
    t0: speechEndedAt || Date.now(),
    speech_duration_s: speechDurationS,
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
      handleServerMessage({ type: 'llm_cancelled', req_id: reqId, turn: turnId });
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
      handleServerMessage({ type: 'llm_cancelled', req_id: reqId, turn: turnId });
    }
  } finally {
    if (httpTurnAbortController && httpTurnAbortController.signal === signal) {
      httpTurnAbortController = null;
    }
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
    if (vad && vad.setPlaybackActive) vad.setPlaybackActive(true);
  };
  audioQ.onDrained = () => {
    lastPlaybackEndedAt = performance.now();
    if (vad && vad.setPlaybackActive) vad.setPlaybackActive(false);
    if (vad && vad.reset) vad.reset();
    readingAckIfPending();
    if (reading.active && (reading.introSpeaking || reading.pendingPassage)) {
      reading.introSpeaking = false;
      if (reading.pendingPassage) {
        triggerReadingPassagePopup();
      }
    }
    if (reading.active && reading.pendingFeedback) {
      try {
        const fb = $('readingFeedback');
        if (fb) fb.textContent = reading.pendingFeedback.displayText;
        if (reading.pendingFeedback.completed) showReadingContinuePrompt(true);
      } catch (e) {}
      if (!reading.pendingFeedback.turnAdded) {
        reading.pendingFeedback.turnAdded = true;
        addTurn('Partner', reading.pendingFeedback.displayText);
      }
      reading.pendingFeedback = null;
    }
    if (audioQ.engine === 'gemini') {
      try { LiveCaptions.stop(); } catch (e) {}
    }
    if (!llmPending) {
      aiSpeaking = false;
      if (running && state !== 'INTERRUPTED') {
        setState('LISTENING');
      } else if (!running) {
        setState('IDLE');
      }
      smokeDissolveAiLine(1200);
    }
  };
  audioQ.onSentenceStart = (text, turn) => {
    recordAiLine(text);
    if (!currentTurnAiText.includes(text)) {
      currentTurnAiText = (currentTurnAiText + ' ' + text).trim();
    }
    setAiLine(text);
    if (reading.active && reading.pendingFeedback) {
      reading.pendingFeedback.chunks.push(text);
      const combined = reading.pendingFeedback.chunks.join(' ');
      try {
        const fb = $('readingFeedback');
        if (fb) fb.textContent = combined;
      } catch (e) {}
      if (!reading.pendingFeedback.turnAdded) {
        reading.pendingFeedback.turnAdded = true;
        addTurn('Partner', reading.pendingFeedback.displayText);
      }
      if (reading.pendingFeedback.completed && text.toLowerCase().includes('continue reading')) {
        showReadingContinuePrompt(true);
      }
    }
  };
  audioQ.onFirstAudio = (info) => logLatency(info);
  audioQ.onError = (msg) => { const fs = $('fishStatus'); if (fs) fs.textContent = msg; hearNote(msg, 6000); };
  audioQ.onLog = (...a) => log('[AUDIO]', ...a);
  await audioQ.init();
  if (!serverConfig) await loadConfig();
  if (useHttpTransport) return;
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
    // Select the voice pipeline before mode greetings or user turns can start.
    try { ws.send(JSON.stringify({ type: 'set_response_mode', value: respMode })); } catch (e) {}
    try { if (audioQ.fishVoice) ws.send(JSON.stringify({ type: 'set_voice', value: audioQ.fishVoice })); } catch (e) {}
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
      const past = getStoredChatHistory(currentMode).slice(-10);
      if (past.length > 0) {
        ws.send(JSON.stringify({
          type: 'sync_history',
          mode: currentMode,
          turns: past.map(x => ({ role: x.who === 'You' ? 'user' : 'assistant', content: x.text }))
        }));
      }
    } catch (e) {}
    // Reading Practice pref ON → start it for this session automatically
    try {
      if (reading.active && !useHttpTransport) {
        readingPanelShow(false);
        reading.pendingPassage = null;
        reading.introSpeaking = true;
        sendReadingMsg({ type: 'reading_start' });
        setReadingStatus('GENERATING_PASSAGE');
        try { $('interruptBtn').disabled = true; } catch (e) {}
      }
    } catch (e) {}
  };

  ws.onmessage = (ev) => {
    try {
      const m = JSON.parse(ev.data);
      handleServerMessage(m);
    } catch (e) {}
  };

  const connectedSocket = ws;
  ws.onclose = () => {
    if (ws !== connectedSocket) return;
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
      clearTimeout(window.__flowReconnect);
      window.__flowReconnect = setTimeout(() => { if (running) connect(); }, 2000);
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
  // Main screen mode selector
  try { $('headphonesMode').checked = localStorage.getItem('flow_headphones') === '1'; } catch (e) {}
  if ($('headphonesMode')) $('headphonesMode').onchange = () => { try { localStorage.setItem('flow_headphones', $('headphonesMode').checked ? '1' : '0'); } catch (e) {} };
  const modeBtns = document.querySelectorAll('#modeSelector .mode-btn');
  modeBtns.forEach(btn => {
    btn.onclick = () => {
      const targetMode = btn.getAttribute('data-mode');
      setLearningMode(targetMode, true);
    };
  });
  // Reading Practice: persisted toggle
  try {
    const saved = localStorage.getItem('readingPractice') === '1';
    if (saved) {
      reading.active = true;
      const b = $('readingToggle');
      if (b) b.setAttribute('aria-checked', 'true');
    }
  } catch (e) {}
  if ($('readingToggle')) $('readingToggle').onclick = () => setReadingEnabled(!reading.active);
  if ($('readingClose')) $('readingClose').onclick = () => setReadingEnabled(false);
  if ($('readingExit')) $('readingExit').onclick = () => setReadingEnabled(false);
  if ($('readingNewPassage')) $('readingNewPassage').onclick = () => {
    if (!running) { hearNote('Start the mic first — Reading Practice opens with the session.', 3200); return; }
    resetReadingCapture();
    if (sendReadingMsg({ type: 'reading_start' })) setReadingStatus('GENERATING_PASSAGE');
  };
  if ($('readingContinueYes')) $('readingContinueYes').onclick = () => handleReadingContinue(true);
  if ($('readingContinueNo')) $('readingContinueNo').onclick = () => handleReadingContinue(false);
  if ($('readingFinishBtn')) $('readingFinishBtn').onclick = () => {
    if (reading.active && (reading.listening || reading.recording)) {
      finalizeReading();
    }
  };
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
  // Browser speech recognition often changes only word boundaries when it
  // upgrades an interim result to final (for example Hindi "फॉरलिसनिंग" →
  // "फॉर लिसनिंग"). Treat that as the same utterance, not a second turn.
  const compactX = x.replace(/\s/g, ''), compactY = y.replace(/\s/g, '');
  const shorter = Math.min(compactX.length, compactY.length);
  const longer = Math.max(compactX.length, compactY.length);
  if (shorter >= 6 && (compactX.includes(compactY) || compactY.includes(compactX)) && shorter / longer >= 0.7) {
    return true;
  }
  const xs = new Set(x.split(' ').filter(Boolean));
  const ys = y.split(' ').filter(Boolean);
  if (!ys.length || !xs.size) return false;
  let hit = 0;
  for (const w of ys) if (xs.has(w)) hit++;
  return hit / Math.max(ys.length, 1) >= thr;
}
const recentAiLines = [];
let currentTurnAiText = '';
const currentAiWordSet = new Set();

function recordAiLine(t) {
  if (!t) return;
  // Keep every script supported by Web Speech. ASCII-only normalisation made
  // Hindi (and other non-Latin) assistant speech invisible to echo detection.
  const clean = normText(t.replace(/\[[^\]]*\]/g, ''));
  if (clean && clean.length > 2) {
    if (!recentAiLines.includes(clean)) {
      recentAiLines.push(clean);
      if (recentAiLines.length > 30) recentAiLines.shift();
    }
    const words = clean.split(' ').filter(Boolean);
    for (const w of words) {
      if (w.length >= 2) currentAiWordSet.add(w);
    }
  }
}

const INTERRUPT_REGEX = /\b(stop|wait|pause|hold on|shut up|cancel|listen|quiet|hush|no wait|actually|hang on|excuse me|hey|wait a second|wait a minute)\b/i;

const COMMON_STOP_WORDS = new Set([
  'a', 'an', 'the', 'and', 'or', 'but', 'so', 'if', 'to', 'of', 'in', 'on', 'at', 'by', 'for', 'with',
  'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
  'i', 'you', 'he', 'she', 'it', 'we', 'they', 'my', 'your', 'our', 'their', 'me', 'him', 'her', 'us', 'them',
  'that', 'this', 'what', 'how', 'why', 'when', 'where', 'who', 'which', 'can', 'will', 'just', 'not',
  'no', 'yes', 'ok', 'okay', 'like', 'as', 'there'
]);

function isAiEcho(candidateText) {
  if (!candidateText || (!recentAiLines.length && !currentTurnAiText)) return false;

  // Explicit user interruption stop-words are NEVER echo
  if (/^(stop|wait|pause|hold on|cancel|quiet|no wait|hang on|excuse me|wait a second|wait a minute)[.!?]*$/i.test(candidateText.trim())) {
    return false;
  }

  const candNorm = normText(candidateText);
  if (!candNorm || candNorm.length < 2) return false;
  const candWords = candNorm.split(' ').filter(Boolean);
  if (!candWords.length) return false;

  const isPlaying = isPlaybackActive();
  const recentlyEnded = (performance.now() - lastPlaybackEndedAt < 2500);

  // Echo can only happen while audio is playing or immediately after (<2.5s)
  if (!isPlaying && !recentlyEnded) return false;

  const activeAiLines = recentAiLines.slice(-6);
  const aiCombined = normText([currentTurnAiText, ...activeAiLines].join(' '));
  if (!aiCombined) return false;

  // 1. Short replies such as "yes", "no", "okay", and "haan" are often a
  // user's meaningful answer to the assistant's question. Only treat a
  // short match as echo while the assistant is actively audible; once audio
  // has drained, never discard it merely because the assistant used that word.
  if (candWords.length <= 2) {
    if (!isPlaying) return false;
    if (aiCombined.includes(candNorm)) return true;
    for (const line of activeAiLines) {
      const lNorm = normText(line);
      if (lNorm && lNorm.includes(candNorm)) return true;
    }
  }

  // 2. Full phrase substring match against AI speech
  if (candNorm.length >= 8) {
    if (aiCombined.includes(candNorm)) return true;
    for (const line of activeAiLines) {
      const lNorm = normText(line);
      if (lNorm && (lNorm.includes(candNorm) || (candNorm.includes(lNorm) && lNorm.length >= 10))) return true;
    }
  }

  // 3. High word-level overlap: if 75%+ of words come from what the AI said
  if (candWords.length >= 3) {
    let matched = 0;
    for (const w of candWords) {
      if (currentAiWordSet.has(w) || aiCombined.includes(w)) matched++;
    }
    const ratio = matched / candWords.length;
    if (ratio >= 0.75) return true;
  }

  return false;
}

function stripLeadingAiEcho(text) {
  if (!text || (!recentAiLines.length && !currentTurnAiText)) return text;
  // After playback ends, preserve the user's entire follow-up. A real reply
  // can naturally begin with words the assistant used moments earlier.
  if (!isPlaybackActive()) return text;
  const rawWords = text.trim().split(/\s+/);
  if (rawWords.length < 3) return text;

  const activeAiLines = recentAiLines.slice(-6);
  const aiCombined = normText([currentTurnAiText, ...activeAiLines].join(' '));
  if (!aiCombined) return text;

  const maxPrefix = Math.min(rawWords.length - 1, 15);
  for (let len = maxPrefix; len >= 2; len--) {
    const prefixWords = rawWords.slice(0, len);
    const prefixNorm = normText(prefixWords.join(' '));
    if (prefixNorm.length < 7) continue;

    let isPrefixEcho = false;
    for (const line of activeAiLines) {
      const lNorm = normText(line);
      if (lNorm && lNorm.includes(prefixNorm)) {
        isPrefixEcho = true;
        break;
      }
    }
    if (!isPrefixEcho && aiCombined.includes(prefixNorm)) {
      isPrefixEcho = true;
    }

    if (isPrefixEcho) {
      const remaining = rawWords.slice(len).join(' ').trim();
      log('[STT] stripped leading AI echo prefix:', JSON.stringify(prefixWords.join(' ')), '→ remaining:', JSON.stringify(remaining.slice(0, 60)));
      return remaining;
    }
  }
  return text;
}
const CONTEXTUAL_HOMOPHONES_JS = [
  { target: 'cereal', wrong: 'serial', triggers: ['cereal', 'eat', 'drink', 'morning', 'breakfast', 'food', 'milk', 'bowl', 'snack'] },
  { target: 'ate', wrong: 'eight', triggers: ['eat', 'ate', 'food', 'breakfast', 'dinner', 'lunch', 'yesterday', 'meal', 'apple'] },
  { target: 'weather', wrong: 'whether', triggers: ['weather', 'climate', 'rain', 'sunny', 'forecast', 'hot', 'cold', 'outside'] },
  { target: 'flour', wrong: 'flower', triggers: ['bake', 'baking', 'cake', 'bread', 'dough', 'kitchen', 'cook', 'flour'] },
  { target: 'meat', wrong: 'meet', triggers: ['food', 'eat', 'chicken', 'beef', 'pork', 'vegetarian', 'vegan', 'meat', 'butcher'] },
  { target: 'piece', wrong: 'peace', triggers: ['cake', 'pie', 'bread', 'part', 'slice', 'bit', 'puzzle', 'piece'] },
  { target: 'brake', wrong: 'break', triggers: ['car', 'drive', 'stop', 'pedal', 'bicycle', 'vehicle', 'brake'] },
  { target: 'sweet', wrong: 'suite', triggers: ['sugar', 'dessert', 'candy', 'taste', 'delicious', 'chocolate', 'sweet'] },
  { target: 'weak', wrong: 'week', triggers: ['strong', 'tired', 'feeble', 'muscle', 'sick', 'frail', 'weak'] },
  { target: 'stair', wrong: 'stare', triggers: ['step', 'climb', 'up', 'down', 'building', 'floor', 'stair'] },
  { target: 'son', wrong: 'sun', triggers: ['daughter', 'father', 'mother', 'child', 'boy', 'family', 'son'] },
  { target: 'sea', wrong: 'see', triggers: ['ocean', 'water', 'beach', 'ship', 'fish', 'wave', 'sea'] }
];

function alignContextualHomophonesJs(text, context) {
  if (!text || !context) return text;
  const ctx = (context || '').toLowerCase();
  let out = text;
  for (const h of CONTEXTUAL_HOMOPHONES_JS) {
    const rx = new RegExp('\\b' + h.wrong + '\\b', 'gi');
    if (rx.test(out)) {
      if (h.triggers.some(trig => ctx.includes(trig))) {
        out = out.replace(rx, (match) => {
          if (match === match.toUpperCase()) return h.target.toUpperCase();
          if (match[0] === match[0].toUpperCase()) return h.target.charAt(0).toUpperCase() + h.target.slice(1);
          return h.target;
        });
      }
    }
  }
  return out;
}

async function sendText(text, replyLang, isAppended = false) {
  text = (text || '').trim();
  if (!text) return;
  // Keep the learner's original words. Homophone rewriting can hide real mistakes.

  if (reading.active && reading.state === 'AWAITING_CONTINUE') {
    readingOnFinal(text);
    return;
  }

  // Guard against duplicate send of the exact same utterance or prefix within 3.5s
  if (!isAppended && lastSentText && (performance.now() - lastSentAt < 3500) && (sameText(text, lastSentText, 0.75) || lastSentText.startsWith(text))) {
    log('[TURN] duplicate/prefix turn suppressed:', JSON.stringify(text));
    return;
  }
  currentAiWordSet.clear();
  currentTurnAiText = '';
  audioQ.cancel('new_user_turn'); // Cancel any superseded in-flight audio from prior turn
  lastPlaybackEndedAt = performance.now();
  if (vad && vad.setPlaybackActive) vad.setPlaybackActive(false);
  if (vad && vad.reset) vad.reset();
  if (httpTurnAbortController) {
    try { httpTurnAbortController.abort(); } catch (e) {}
    httpTurnAbortController = null;
  }
  llmPending = true;
  aiSpeaking = false;
  accumulatedTranscript = '';
  lastInterim = '';
  lastInterimAt = 0;
  clearTimeout(speechTimer);
  speechTimer = null;
  lastSentText = text; lastSentAt = performance.now();
  if (isAppended) {
    const t = $('transcript');
    const lastChild = t ? t.lastElementChild : null;
    const isLastTurnUser = lastChild && (lastChild.classList.contains('turn-row') || lastChild.classList.contains('you'));
    if (isLastTurnUser) {
      const turnDiv = lastChild.classList.contains('you') && lastChild.classList.contains('turn')
        ? lastChild
        : lastChild.querySelector('.turn.you');
      if (turnDiv) {
        turnDiv.textContent = text;
      } else {
        addTurn('You', text);
      }
    } else {
      addTurn('You', text);
    }
  } else {
    addTurn('You', text);
  }
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
  if (typeof audioQ !== 'undefined' && audioQ && audioQ.startTurn) {
    audioQ.startTurn(turnId);
    if (speechEndedAt) audioQ._turnT0 = performance.now() - Math.max(0, Date.now() - speechEndedAt);
  } else {
    audioQ._turnId = turnId;   // string: the server echoes ids back as strings
    audioQ._turnT0 = performance.now();
    audioQ._expectFirst = true;
    audioQ.turnLat = null;
  }
  const transportLabel = useHttpTransport || !ws || ws.readyState !== WebSocket.OPEN ? 'HTTP/SSE' : 'WebSocket';
  log(`[CHAT req_id=${reqId}] text submission started: "${text.slice(0, 80)}" (${transportLabel})`);

  if (useHttpTransport || !ws || ws.readyState !== WebSocket.OPEN) {
    streamHttpTurn(text, turnId, reqId, replyLang || '');
  } else {
    ws.send(JSON.stringify({ type: 'user_transcript', text, t0: speechEndedAt || Date.now(), turn_id: turnId, req_id: reqId, reply_lang: replyLang || '', speech_duration_s: speechDurationS }));
  }
}
const isMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) || ('ontouchstart' in window && window.innerWidth < 800);

function isTrailingIncomplete(text) {
  if (!text) return false;
  const trailingRegex = /\b(and|or|but|so|because|that|with|to|for|if|when|as|like|the|a|an|my|your|our|their|his|her|its|also|then|however|although|though|which|who|whose|where|while|whether|since|about|into|through|after|before|is|are|was|were|be|been|being)\s*$/i;
  return trailingRegex.test(text.trim());
}

// fire the turn the moment the user stops talking (VAD end-of-turn or silence debounce), using the
// accumulated transcript — ensures full sentences are sent, not halves
function commitTurnIfReady() {
  if (reading.active) return; // reading capture owns speech-end during practice

  let textToCommit = (accumulatedTranscript || lastInterim || '').trim();
  if (!textToCommit || textToCommit.length < 2) return;

  // If user is vocalizing right now, defer commit
  if (vad && vad.speaking) {
    clearTimeout(speechTimer);
    speechTimer = setTimeout(commitTurnIfReady, 350);
    return;
  }

  // If local VAD is active and never detected foreground speech, drop Web Speech text as background chatter
  if (vad && !mobCaptureOn && !vad.hasSpoken) {
    log('[STT] Web Speech text ignored — no foreground speech detected by VAD:', JSON.stringify(textToCommit.slice(0, 60)));
    accumulatedTranscript = '';
    lastInterim = '';
    lastInterimAt = 0;
    return;
  }

  // Strip leading AI echo before checking echo or committing
  textToCommit = stripLeadingAiEcho(textToCommit);
  if (!textToCommit || textToCommit.length < 2) {
    accumulatedTranscript = '';
    lastInterim = '';
    lastInterimAt = 0;
    return;
  }

  if (isAiEcho(textToCommit)) {
    log('[STT] speaker echo suppressed in commitTurnIfReady:', JSON.stringify(textToCommit.slice(0, 80)));
    accumulatedTranscript = '';
    lastInterim = '';
    lastInterimAt = 0;
    return;
  }

  // If the user ended on an incomplete conjunction/preposition (e.g. "because", "and", "to"), wait for continuation
  if (isTrailingIncomplete(textToCommit)) {
    if (performance.now() - (lastInterimAt || 0) < 2200) {
      clearTimeout(speechTimer);
      speechTimer = setTimeout(commitTurnIfReady, 600);
      return;
    }
  }

  // If playback is still active, but textToCommit is genuine user speech (not echo),
  // stop playback now so we can commit the user's turn
  if (isPlaybackActive()) {
    bargeIn('commit_user_speech');
  }

  // Duplicate suppression
  if (lastSentText && (performance.now() - lastSentAt < 3000) && (sameText(textToCommit, lastSentText, 0.85) || lastSentText === textToCommit)) {
    accumulatedTranscript = '';
    lastInterim = '';
    return;
  }

  accumulatedTranscript = '';
  lastInterim = '';
  lastInterimAt = 0;
  clearTimeout(speechTimer);
  speechTimer = null;
  if (vad && vad.reset) vad.reset();

  // Do not rewrite a previously dispatched request with a later speech
  // segment. It caused a final STT correction to cancel the active answer and
  // concatenate unrelated sentences. sameText() above absorbs genuine late
  // interim→final upgrades; a distinct utterance is a new, clean turn.
  log('[STT] speech-end → turn committed:', JSON.stringify(textToCommit.slice(0, 80)));
  sendText(textToCommit, sttLangToLanguage());
}

function sttLangToLanguage() {
  const prefix = String(sttLang || '').toLowerCase().split('-')[0];
  const byCode = (serverConfig && serverConfig.languages || []).find(lang =>
    String(lang.stt || '').toLowerCase().split('-')[0] === prefix || String(lang.id || '').toLowerCase() === prefix
  );
  return byCode ? byCode.id : (($('language') && $('language').value) || '');
}

let recogRestartTimer = null;
let recogFatal = false;
let consecutiveAborts = 0;
let lastRecogEndAt = 0;
let recogRapidEnds = 0;
let lastRecogError = null;
let recogMicStreamOpen = false;
let recogActive = false;
let recogWatchdog = null;
let recogStartedAt = 0;
let recogGeneration = 0;
let vadHealthTimer = null;
let vadRecoveryInFlight = false;

function startRecognition() {
  if (!listening || !running || micMuted || recogFatal) return;
  if (mobCaptureOn) return; // held stream + server STT owns the microphone
  if (recogActive && recog) {
    // Chromium can leave recognition marked active after its audio stream
    // dies (notably with Bluetooth devices). Rebuild that stale recognizer.
    if (recogMicStreamOpen || performance.now() - recogStartedAt < 3000) return;
    log('[STT] recognition active without an audio stream — recovering');
  }
  // Invalidate before aborting: Chromium can still deliver late callbacks from
  // an aborted recognizer. Those callbacks must never make a replacement look
  // healthy or schedule a second restart.
  const previousRecog = recog;
  recogGeneration++;
  recog = null;
  recogActive = false;
  recogMicStreamOpen = false;
  if (previousRecog) {
    try {
      previousRecog.onstart = null;
      previousRecog.onspeechstart = null;
      previousRecog.onspeechend = null;
      previousRecog.onsoundstart = null;
      previousRecog.onsoundend = null;
      previousRecog.onaudiostart = null;
      previousRecog.onaudioend = null;
      previousRecog.onend = null;
      previousRecog.onerror = null;
      previousRecog.onresult = null;
      previousRecog.abort();
    } catch (e) {}
  }
  recog = setupRecog(recogGeneration);
  if (recog) {
    try {
      recog.start();
    } catch (err) {
      log('[STT] recognition start error:', (err && err.message) || (err && err.name));
    }
  }
}

function startRecogWatchdog() {
  stopRecogWatchdog();
  recogWatchdog = setInterval(() => {
    if (!running || !listening || micMuted || recogFatal) return;
    if (mobCaptureOn) return; // held stream + server STT owns the microphone
    if ((!recogActive || (!recogMicStreamOpen && performance.now() - recogStartedAt >= 3000)) && !recogRestartTimer) {
      startRecognition();
    }
  }, 1500);
}

function stopRecogWatchdog() {
  if (recogWatchdog) {
    clearInterval(recogWatchdog);
    recogWatchdog = null;
  }
}

function desktopVadHealthy() {
  try {
    return !!(vad && vad.ctx && vad.ctx.state === 'running' && vad.stream &&
      vad.stream.getAudioTracks().some(track => track.readyState === 'live'));
  } catch (e) {
    return false;
  }
}

async function startDesktopVad() {
  try { if (vad) vad.stop(); } catch (e) {}
  vad = new EnergyVAD({
    onSpeechStart: () => {
      if (readingOnVoiceStart()) return;
      clearTimeout(speechTimer);
      speechTimer = null;
    },
    onSpeechEnd: () => {
      if (readingOnVoiceEnd()) return;
      clearTimeout(speechTimer);
      speechTimer = setTimeout(commitTurnIfReady, 350);
    },
    onPause: (ev) => { if (reading.active) readingOnPause(ev); },
  });
  const micOk = await vad.start(selectedMicDevice());
  if (!micOk) {
    const why = vad.lastError === 'NotAllowedError' ? 'Mic blocked — allow microphone access in the browser.'
      : vad.lastError === 'NotFoundError' ? 'No microphone found on this device.'
      : 'Microphone unavailable (' + (vad.lastError || 'unknown') + '). You can still type below.';
    hearNote(why, 6000);
    log('[VAD]', why);
    return false;
  }
  loadMicDevices();
  return true;
}

function startVadHealthWatchdog() {
  stopVadHealthWatchdog();
  vadHealthTimer = setInterval(async () => {
    if (!running || micMuted || mobCaptureOn || vadRecoveryInFlight || desktopVadHealthy()) return;
    // Do not repeatedly request a permission that the browser has denied.
    if (vad && (vad.lastError === 'NotAllowedError' || vad.lastError === 'SecurityError')) return;
    vadRecoveryInFlight = true;
    log('[VAD] audio stream unavailable — recovering microphone analysis');
    try {
      await startDesktopVad();
    } finally {
      vadRecoveryInFlight = false;
    }
  }, 3000);
}

function stopVadHealthWatchdog() {
  try { clearInterval(vadHealthTimer); } catch (e) {}
  vadHealthTimer = null;
  vadRecoveryInFlight = false;
}

function setupRecog(generation) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { hearNote('Speech recognition not supported here — use Chrome, or type below.'); return null; }
  const r = new SR();
  const isCurrent = () => recog === r && generation === recogGeneration;
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

  r.onstart = () => {
    if (!isCurrent()) return;
    recogActive = true;
    recogStartedAt = performance.now();
    recogFatal = false;
    consecutiveAborts = 0;
  };

  r.onspeechstart = () => {
    if (!isCurrent()) return;
    if (micMuted || !listening) return;
    $('micLive').classList.add('on');
    try { window.__micLevel = 0.08; } catch (e) {}
    if (reading.active && reading.listening) { readingOnVoiceStart(); return; }
    browserSpeechStartedAt = performance.now();
    // Note: raw onspeechstart must NOT call bargeIn() unconditionally, as speaker
    // echo leakage or room noise would abruptly interrupt the AI mid-sentence.
    // Real barge-in is safely handled by EnergyVAD (sustainedBargeIn) or transcribed interim speech.
  };

  r.onspeechend = () => {
    if (!isCurrent()) return;
    if (micMuted || !listening) return;
    if (browserSpeechStartedAt) {
      speechDurationS = Math.max(0, (performance.now() - browserSpeechStartedAt) / 1000);
      speechEndedAt = Date.now(); browserSpeechStartedAt = 0;
    }
    $('micLive').classList.remove('on');
    try { window.__micLevel = 0; } catch (e) {}
  };

  r.onsoundstart = () => {
    if (!isCurrent()) return;
    if (micMuted || !listening) return;
    $('micLive').classList.add('on');
    try { window.__micLevel = 0.06; } catch (e) {}
  };

  r.onsoundend = () => {
    if (!isCurrent()) return;
    if (micMuted || !listening) return;
    $('micLive').classList.remove('on');
    try { window.__micLevel = 0; } catch (e) {}
  };

  r.onresult = (ev) => {
    if (!isCurrent()) return;
    if (micMuted || !listening) return; // deaf only while mic is explicitly muted
    consecutiveAborts = 0;
    recogStartedAt = performance.now();
    let interim = '', fin = '', conf = 1;
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const res = ev.results[i];
      if (res.isFinal) {
        let bestTranscript = res[0].transcript;
        let bestConf = (typeof res[0].confidence === 'number' && res[0].confidence > 0) ? res[0].confidence : 0.9;
        for (let alt = 1; alt < res.length; alt++) {
          if (typeof res[alt].confidence === 'number' && res[alt].confidence > bestConf) {
            bestTranscript = res[alt].transcript;
            bestConf = res[alt].confidence;
          }
        }
        fin += (fin ? ' ' : '') + bestTranscript.trim();
        conf = Math.min(conf, bestConf);
      } else {
        interim += res[0].transcript;
      }
    }
    if (interim) {
      // auto-interrupt on real speech while audio is playing
      const isSpeaking = isPlaybackActive();
      if (isSpeaking) {
        if (isAiEcho(interim)) {
          // drop speaker echo leaking into microphone
          return;
        }
        const cleanInterim = interim.trim();
        const words = cleanInterim.split(/\s+/).filter(Boolean);
        const hasStopWord = INTERRUPT_REGEX.test(cleanInterim);

        // Check for content words that do not belong to AI speech
        const activeAiLines = recentAiLines.slice(-6);
        const aiCombined = ([currentTurnAiText, ...activeAiLines]).join(' ').toLowerCase();
        const contentWords = words.filter(w => !COMMON_STOP_WORDS.has(w.toLowerCase()));
        const newWords = contentWords.filter(w => !currentAiWordSet.has(w.toLowerCase()) && !aiCombined.includes(w.toLowerCase()));

        const isUserInterruption = hasStopWord || (newWords.length >= 1 && words.length >= 2 && !isAiEcho(cleanInterim)) || (words.length >= 3 && !isAiEcho(cleanInterim));
        if (isUserInterruption && (performance.now() - lastSentAt > 600)) {
          bargeIn('user_speech: ' + cleanInterim);
          accumulatedTranscript = cleanInterim;
          lastInterim = cleanInterim;
          lastInterimAt = performance.now();
          userSaid(cleanInterim);
          clearTimeout(speechTimer);
          speechTimer = setTimeout(commitTurnIfReady, 850);
          return;
        } else {
          // While assistant is playing, drop speaker leakage so it never self-interrupts
          return;
        }
      }

      if (isAiEcho(interim)) return;

      const fullCurrent = (accumulatedTranscript ? (accumulatedTranscript + ' ' + interim) : interim).trim();
      lastInterim = fullCurrent;
      lastInterimAt = performance.now();
      userSaid(fullCurrent);
      try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'partial', text: fullCurrent })); } catch (e) {}
      if (reading.active && reading.listening) {
        // one-pole into the reading transcript; conversational turns are off
        readingOnInterim(fullCurrent);
        return;
      }

      // Reset speechTimer while streaming interim speech continues (850ms quiet commits turn promptly on pause)
      clearTimeout(speechTimer);
      speechTimer = setTimeout(function checkCommit() {
        if (vad && vad.speaking) {
          speechTimer = setTimeout(checkCommit, 350);
          return;
        }
        commitTurnIfReady();
      }, 850);
    }
    if (fin && fin.trim()) {
      let text = fin.trim();

      if (reading.active && (reading.listening || reading.state === 'FINALIZING_TRANSCRIPT')) {
        // Reading capture consumes all finals — never mint a conversational turn.
        log('[STT] reading final', JSON.stringify(text.slice(0, 100)));
        readingOnFinal(text);
        return;
      }

      // If audio is currently playing or response is in flight, handle user barge-in or drop echo
      if (isPlaybackActive() || state === 'SPEAKING' || llmPending) {
        if (isAiEcho(text)) {
          log('[STT] speaker audio during playback dropped:', JSON.stringify(text.slice(0, 60)));
          return;
        }
        bargeIn('user_speech_final: ' + text);
        accumulatedTranscript = text;
        lastInterim = text;
        lastInterimAt = performance.now();
        userSaid(text);
        commitTurnIfReady();
        return;
      }

      // Check if this final segment is assistant echo
      if (isAiEcho(text)) {
        log('[STT] dropped speaker echo final:', JSON.stringify(text.slice(0, 80)));
        return;
      }

      text = stripLeadingAiEcho(text);
      if (!text || text.length < 2 || isAiEcho(text)) {
        return;
      }

      const isHindi = (sttLang && sttLang.startsWith('hi'));
      const minConfThreshold = isHindi ? 0.35 : 0.20;
      if (conf < minConfThreshold && text.length < 2) {
        log('[STT] dropped as noise', JSON.stringify(text), 'conf=' + conf.toFixed(2));
        return;
      }

      // Check if this final segment was already sent in the last 3.5s
      if (lastSentText && (performance.now() - lastSentAt < 3500) && (sameText(text, lastSentText, 0.85) || lastSentText === text)) {
        log('[STT] final matched recently sent turn — ignoring duplicate');
        return;
      }

      // ACCUMULATE final segments across pauses so the full sentence is preserved!
      accumulatedTranscript = (accumulatedTranscript ? (accumulatedTranscript + ' ' + text) : text).trim();
      lastInterim = accumulatedTranscript;
      lastInterimAt = performance.now();
      userSaid(accumulatedTranscript);
      log('[STT] segment final (accumulated=' + JSON.stringify(accumulatedTranscript.slice(0, 80)) + ') conf=' + conf.toFixed(2));

      // Schedule turn completion: wait for natural pause (1350ms) before committing
      clearTimeout(speechTimer);
      speechTimer = setTimeout(function checkCommit() {
        if (vad && vad.speaking) {
          speechTimer = setTimeout(checkCommit, 350);
          return;
        }
        commitTurnIfReady();
      }, 1350);
    }
  };
  r.onerror = (e) => {
    if (!isCurrent()) return;
    lastRecogError = e.error;
    if (e.error === 'aborted') {
      consecutiveAborts++;
      if (consecutiveAborts <= 2) log('[STT] aborted');
    } else if (e.error === 'no-speech') {
      // Normal browser idle silence: this is completely normal when the user pauses or listens.
      // Do not log as an error; onend keeps mic open seamlessly with zero gap.
    } else {
      log('[STT] error', e.error);
    }
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      recogFatal = true;
      listening = false;
      hearNote('Mic blocked — allow microphone access, then press mic again.');
    } else if (e.error === 'audio-capture') {
      recogFatal = true;
      listening = false;
      hearNote('No microphone found — plug one in, then press mic again.');
    }
  };
  r.onaudiostart = () => {
    if (!isCurrent()) return;
    recogActive = true;
    recogStartedAt = performance.now();
    consecutiveAborts = 0;
    recogMicStreamOpen = true;
    log('[STT] mic stream opened');
  };
  r.onaudioend = () => {
    if (!isCurrent()) return;
    recogMicStreamOpen = false;
    if (!listening || !running || micMuted || recogFatal) {
      log('[STT] mic stream closed');
    }
  };
  r.onend = () => {
    if (!isCurrent()) return;
    recogActive = false;
    recogMicStreamOpen = false;
    if (!listening || !running || micMuted || recogFatal) {
      return; // intentionally stopped — stay closed
    }
    // Salvage unfinalized interim speech from before the pause/restart so pauses never drop words
    if (lastInterim && lastInterim.trim()) {
      const cleanInterim = stripLeadingAiEcho(lastInterim.trim());
      if (cleanInterim && !isAiEcho(cleanInterim)) {
        if (!accumulatedTranscript) {
          accumulatedTranscript = cleanInterim;
        } else if (!accumulatedTranscript.includes(cleanInterim)) {
          accumulatedTranscript = (accumulatedTranscript + ' ' + cleanInterim).trim();
        }
        lastInterim = accumulatedTranscript;
      }
    }
    clearTimeout(recogRestartTimer);
    const wasSilence = (lastRecogError === 'no-speech');
    lastRecogError = null;

    const now = performance.now();
    const sinceEnd = now - lastRecogEndAt;
    lastRecogEndAt = now;
    if (sinceEnd < 2000) recogRapidEnds++;
    else recogRapidEnds = 0;

    let delay = wasSilence ? 40 : (typeof isMobile !== 'undefined' && isMobile ? 400 : 120);
    if (consecutiveAborts >= 3) delay = Math.max(delay, 1000);
    if (recogRapidEnds >= 4) delay = Math.min(2000, delay * 2);
    recogRestartTimer = setTimeout(() => {
      recogRestartTimer = null;
      startRecognition();
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
// --- English Reading Practice -----------------------------------------------
// A dedicated interaction state ON TOP of the existing mic/VAD/STT pipeline:
// toggle ON → server generates a passage → user reads it aloud → end-of-reading
// detected by a configurable long silence (far above the 850ms conversational
// end-of-turn, so breath pauses never cheat) → server diffs transcript vs
// passage → spoken feedback → three rounds on the same passage → summary.
// While a reading is active, conversational turns are suppressed (client and
// server) so Gemini never interrupts the reader or answers mid-reading.
const READING_MIN_SPEECH_MS = 4000;   // readings shorter than this never finalize
const READING_MAX_RECORD_MS = 240000; // hard cap for one reading capture
const READING_GIVEUP_MS = 30000;      // meaningless silence mid-round → deliver partial
const READING_ROUND_ACK_MAX_MS = 20000;
const READING_REMINDER_MS = 20000;
const READING_STATUS_TEXT = {
  GENERATING_PASSAGE: 'Generating a passage…',
  WAITING_FOR_READING: 'Ready — read the passage aloud.',
  RECORDING_READING: 'Listening — take your time.',
  FINALIZING_TRANSCRIPT: 'Finishing up…',
  ANALYZING_READING: 'Analyzing your reading…',
  PROVIDING_FEEDBACK: 'Giving feedback…',
  ROUND_COMPLETE: 'Completed.',
  AWAITING_CONTINUE: 'Completed — would you like to continue reading?',
  summary: 'Practice complete — new passage, or exit.',
  off: '',
};
const reading = {
  active: false, state: 'off', gen: 0, round: 0, passage: '',
  listening: false, recording: false, voiceReported: false, mime: '',
  transcript: '', interim: '',
  speechStartedAt: 0, lastVoiceAt: 0, speechMs: 0, peak: 0,
  voiceSegmentStart: 0,
  pauseTotalMs: 0, longPauses: 0,
  pauses: [],
  rec: null,
  silenceTimer: null, maxTimer: null, reminderTimer: null,
  ackTimer: null, ackIv: null, confirmAck: false,
  aiLines: [],
  pendingPassage: null,
  introSpeaking: false,
  introSafetyTimer: null,
  passageWords: [],
  _completing: false,
  completionTimer: null,
};

function wordsMatch(s, p) {
  if (!s || !p) return false;
  if (s === p) return true;
  const sClean = s.replace(/[^a-z0-9]/g, '');
  const pClean = p.replace(/[^a-z0-9]/g, '');
  if (sClean === pClean) return true;
  const numMap = { '1': 'one', '2': 'two', '3': 'three', '4': 'four', '5': 'five',
                   '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine', '10': 'ten' };
  if (numMap[sClean] === pClean || numMap[pClean] === sClean) return true;
  if (sClean.length >= 4 && pClean.length >= 4 && Math.abs(sClean.length - pClean.length) <= 1) {
    let diff = 0, i = 0, j = 0;
    while (i < sClean.length && j < pClean.length) {
      if (sClean[i] !== pClean[j]) {
        diff++;
        if (diff > 1) break;
        if (sClean.length > pClean.length) i++;
        else if (pClean.length > sClean.length) j++;
        else { i++; j++; }
      } else {
        i++; j++;
      }
    }
    diff += (sClean.length - i) + (pClean.length - j);
    if (diff <= 1) return true;
  }
  return false;
}

function renderReadingPassageTokens(passageText) {
  const container = $('readingPassage');
  if (!container) return [];
  container.innerHTML = '';
  reading.passageWords = [];
  if (!passageText) return [];

  const regex = /([a-zA-Z0-9]+(?:['\u2019][a-zA-Z0-9]+)?)|([^a-zA-Z0-9]+)/g;
  let match;
  let wordIdx = 0;

  while ((match = regex.exec(passageText)) !== null) {
    if (match[1]) {
      const rawWord = match[1];
      const normWord = rawWord.toLowerCase().replace(/['\u2019]/g, "'");
      const span = document.createElement('span');
      span.className = 'reading-word';
      if (wordIdx === 0) span.classList.add('reading-word-current');
      span.id = `rword-${wordIdx}`;
      span.dataset.idx = String(wordIdx);
      span.textContent = rawWord;
      container.appendChild(span);

      reading.passageWords.push({
        idx: wordIdx,
        raw: rawWord,
        norm: normWord,
        el: span,
        status: 'pending',
      });
      wordIdx++;
    } else if (match[2]) {
      container.appendChild(document.createTextNode(match[2]));
    }
  }
  return reading.passageWords;
}

function readingResetWordHighlights() {
  if (!reading.passageWords || !reading.passageWords.length) return;
  for (let i = 0; i < reading.passageWords.length; i++) {
    const pw = reading.passageWords[i];
    pw.status = 'pending';
    if (pw.el) {
      pw.el.classList.remove('reading-word-correct', 'reading-word-error');
      if (i === 0) pw.el.classList.add('reading-word-current');
      else pw.el.classList.remove('reading-word-current');
    }
  }
}

function getSpokenReadingText(t) {
  const trans = (reading.transcript || '').trim();
  const input = (t || '').trim();
  if (!trans) return input;
  if (!input) return trans;
  if (input.toLowerCase().startsWith(trans.toLowerCase())) {
    return input;
  }
  return (trans + ' ' + input).trim();
}

function updateRealtimeReadingHighlights(spokenText, isInterim = false) {
  if (!reading.passageWords || !reading.passageWords.length) return;
  if (!spokenText || !spokenText.trim()) return;

  const rawSpoken = spokenText.toLowerCase().replace(/['\u2019]/g, "'");
  const spokenWords = rawSpoken.split(/\s+/).map(w => w.replace(/[^a-z0-9']/g, '')).filter(Boolean);
  if (!spokenWords.length) return;

  const pWords = reading.passageWords;
  const pLen = pWords.length;
  const sLen = spokenWords.length;

  let pIdx = 0;
  let sIdx = 0;
  let correctCount = 0;
  let errorCount = 0;

  while (pIdx < pLen && sIdx < sLen) {
    const sWord = spokenWords[sIdx];
    const pWord = pWords[pIdx];

    // Case 1: Direct match
    if (wordsMatch(sWord, pWord.norm)) {
      pWord.status = 'correct';
      correctCount++;
      pIdx++;
      sIdx++;
      continue;
    }

    // Case 2: Passage word was skipped (lookahead in passage by 1 or 2 words)
    if (pIdx + 1 < pLen && wordsMatch(sWord, pWords[pIdx + 1].norm)) {
      pWord.status = 'error';
      errorCount++;
      pWords[pIdx + 1].status = 'correct';
      correctCount++;
      pIdx += 2;
      sIdx++;
      continue;
    }

    if (pIdx + 2 < pLen && wordsMatch(sWord, pWords[pIdx + 2].norm)) {
      pWords[pIdx].status = 'error';
      pWords[pIdx + 1].status = 'error';
      errorCount += 2;
      pWords[pIdx + 2].status = 'correct';
      correctCount++;
      pIdx += 3;
      sIdx++;
      continue;
    }

    // Case 3: Spoken word was an extra/filler word (lookahead in spoken by 1 word)
    if (sIdx + 1 < sLen && wordsMatch(spokenWords[sIdx + 1], pWord.norm)) {
      sIdx++;
      continue;
    }

    // Case 4: If this is the last interim word and it's a prefix of the expected word, user is still pronouncing it!
    if (isInterim && sIdx === sLen - 1 && pWord.norm.startsWith(sWord)) {
      break;
    }

    // Case 5: Mispronunciation / incorrect word substitution
    pWord.status = 'error';
    errorCount++;
    pIdx++;
    sIdx++;
  }

  // Update DOM classes for all passage words
  for (let i = 0; i < pLen; i++) {
    const pw = pWords[i];
    const el = pw.el;
    if (!el) continue;

    if (i < pIdx) {
      if (pw.status === 'correct') {
        el.classList.add('reading-word-correct');
        el.classList.remove('reading-word-error', 'reading-word-current');
      } else {
        el.classList.add('reading-word-error');
        el.classList.remove('reading-word-correct', 'reading-word-current');
      }
    } else if (i === pIdx) {
      el.classList.remove('reading-word-correct', 'reading-word-error');
      el.classList.add('reading-word-current');
      pw.status = 'pending';
    } else {
      el.classList.remove('reading-word-correct', 'reading-word-error', 'reading-word-current');
      pw.status = 'pending';
    }
  }

  // Check for paragraph completion:
  const isFinished = (pIdx >= pLen) ||
    (pIdx >= Math.floor(pLen * 0.88) && (correctCount + errorCount) >= Math.floor(pLen * 0.85) && sLen >= Math.floor(pLen * 0.75));
  if (isFinished && !reading._completing) {
    onParagraphCompleted();
  }
}

function onParagraphCompleted() {
  if (!reading.active || reading._completing) return;
  if (reading.state !== 'RECORDING_READING' && reading.state !== 'WAITING_FOR_READING') return;
  reading._completing = true;
  stopReadingTimers();
  log('[READING] paragraph completed by user; concluding reading and generating feedback');
  setReadingStatus(null, 'Paragraph complete! Analyzing your reading…');

  clearTimeout(reading.completionTimer);
  reading.completionTimer = setTimeout(() => {
    finalizeReading();
  }, 750);
}

function readingEndMs() {
  return 2200;
}
function sendReadingMsg(msg) {
  if (useHttpTransport || !ws || ws.readyState !== WebSocket.OPEN) return false;
  try { ws.send(JSON.stringify(msg)); return true; } catch (e) { return false; }
}
function readingStale(m) {
  return !!(m && m.gen && reading.gen && m.gen < reading.gen);
}
function readingStatusNote(note) {
  const el = $('readingStatus');
  if (!el) return;
  if (note) {
    el.textContent = note;
    clearTimeout(reading._noteTimer);
    reading._noteTimer = setTimeout(() => {
      if (!reading.active) { el.textContent = ''; return; }
      el.textContent = READING_STATUS_TEXT[reading.state] || '';
    }, 4200);
  } else {
    el.textContent = READING_STATUS_TEXT[reading.state] || '';
  }
}
function setReadingStatus(state, note) {
  if (state) reading.state = state;
  const badge = $('readingRoundBadge');
  if (badge) badge.classList.add('hidden');
  const finishBtn = $('readingFinishBtn');
  if (finishBtn) {
    const showFinish = (reading.active && (reading.state === 'RECORDING_READING' || reading.state === 'WAITING_FOR_READING'));
    finishBtn.classList.toggle('hidden', !showFinish);
  }
  readingStatusNote(note || '');
}
function readingPanelShow(show) {
  const p = $('readingPanel');
  if (p) p.classList.toggle('hidden', !show);
  try { document.querySelector('.stage').classList.toggle('reading-on', show); } catch (e) {}
}
function stopReadingTimers() {
  clearTimeout(reading.silenceTimer); reading.silenceTimer = null;
  clearTimeout(reading.maxTimer); reading.maxTimer = null;
  clearTimeout(reading.reminderTimer); reading.reminderTimer = null;
  clearTimeout(reading.ackTimer); reading.ackTimer = null;
  try { clearInterval(reading.ackIv); } catch (e) {}
  reading.ackIv = null;
  clearTimeout(reading._noteTimer);
}
function resetReadingCapture() {
  stopReadingTimers();
  reading.transcript = ''; reading.interim = '';
  reading.speechStartedAt = 0; reading.lastVoiceAt = 0; reading.speechMs = 0;
  reading.voiceSegmentStart = 0;
  reading.pauseTotalMs = 0; reading.longPauses = 0; reading.peak = 0;
  reading.pauses = [];
  reading.rec = null; reading.mime = '';
  reading.listening = false; reading.recording = false; reading.voiceReported = false;
  reading.pendingFeedback = null;
  reading.lastFinishPromptAt = 0;
  reading.finishPromptCount = 0;
  reading._completing = false;
  clearTimeout(reading.completionTimer);
  lastInterim = ''; lastInterimAt = 0; awaitingFinal = false;
  try { $('readingLiveTranscript').textContent = ''; } catch (e) {}
  try { $('readingContinuePrompt').classList.add('hidden'); } catch (e) {}
  const finishBtn = $('readingFinishBtn');
  if (finishBtn) finishBtn.classList.add('hidden');
  readingResetWordHighlights();
}
function stopReadingSession() {
  stopReadingTimers();
  try {
    if (reading.rec) { const r = reading.rec; reading.rec = null; try { r.onstop = null; r.onerror = null; r.stop(); } catch (e) {} }
  } catch (e) {}
  reading.state = 'off'; reading.gen = 0; reading.round = 0;
  reading.listening = false; reading.recording = false; reading.voiceReported = false;
  reading.passage = ''; reading.confirmAck = false;
  reading.passageWords = [];
  reading._completing = false;
  clearTimeout(reading.completionTimer);
  reading.pendingPassage = null;
  resetReadingCapture();
  readingPanelShow(false);
  try { const ib = $('interruptBtn'); if (ib && running) ib.disabled = false; } catch (e) {}
}
function setReadingEnabled(on) {
  const btn = $('readingToggle');
  if (on === reading.active) { if (btn) btn.setAttribute('aria-checked', on ? 'true' : 'false'); return; }
  if (on && useHttpTransport) { hearNote('Reading Practice needs the WebSocket transport to work.', 4000); return; }
  reading.active = on;
  try { localStorage.setItem('readingPractice', on ? '1' : ''); } catch (e) {}
  if (btn) btn.setAttribute('aria-checked', on ? 'true' : 'false');
  log('[READING] practice', on ? 'enabled' : 'disabled');
  if (!on) {
    sendReadingMsg({ type: 'reading_stop' });
    stopReadingSession();
    if (currentMode === 'reading') {
      currentMode = 'free';
      try { localStorage.setItem('mode', 'free'); } catch (e) {}
      if ($('mode')) $('mode').value = 'free';
      const btns = document.querySelectorAll('#modeSelector .mode-btn');
      btns.forEach(btn => {
        const isAct = (btn.getAttribute('data-mode') === 'free');
        btn.classList.toggle('active', isAct);
        btn.setAttribute('aria-selected', isAct ? 'true' : 'false');
      });
    }
    return;
  }
  if (currentMode !== 'reading') {
    currentMode = 'reading';
    try { localStorage.setItem('mode', 'reading'); } catch (e) {}
    if ($('mode')) $('mode').value = 'reading';
    const btns = document.querySelectorAll('#modeSelector .mode-btn');
    btns.forEach(btn => {
      const isAct = (btn.getAttribute('data-mode') === 'reading');
      btn.classList.toggle('active', isAct);
      btn.setAttribute('aria-selected', isAct ? 'true' : 'false');
    });
  }
  if (!running) {
    hearNote('Start the mic, and Reading Practice begins automatically.', 3000);
    return;
  }
  resetReadingCapture();
  readingPanelShow(false);
  reading.pendingPassage = null;
  reading.introSpeaking = true;
  sendReadingMsg({ type: 'reading_start' });
  setReadingStatus('GENERATING_PASSAGE');
  try { $('interruptBtn').disabled = true; } catch (e) {}
}
function armReadingReminder() {
  clearTimeout(reading.reminderTimer);
  reading.reminderTimer = setTimeout(() => {
    if (!reading.active || reading.state !== 'WAITING_FOR_READING' || reading.voiceReported) return;
    setReadingStatus(null, 'Take your time — read the passage aloud whenever you are ready.');
    try { audioQ.enqueue('Take your time — read the passage aloud whenever you are ready.', {}); } catch (e) {}
  }, READING_REMINDER_MS);
}
// ~6s after waiting opens: very early starts must not trigger before the
// reader has even looked at the passage
function readingHandleState(m) {
  if (readingStale(m)) return;
  if (m.gen) reading.gen = m.gen;
  const st = m.state;
  reading.state = st;
  if (st === 'off') {
    stopReadingTimers();
    return;
  }
  if (st === 'GENERATING_PASSAGE') {
    reading.listening = false; reading.recording = false;
  } else if (st === 'WAITING_FOR_READING') {
    reading.listening = true; reading.recording = false;
    if (!m.note) reading.voiceReported = false;
    clearTimeout(reading.maxTimer);
    armReadingReminder();
  } else if (st === 'RECORDING_READING') {
    reading.listening = true; reading.recording = true;
    clearTimeout(reading.reminderTimer);
    clearTimeout(reading.maxTimer);
    reading.maxTimer = setTimeout(finalizeReading, READING_MAX_RECORD_MS);
  } else if (st === 'ROUND_COMPLETE' || st === 'AWAITING_CONTINUE') {
    reading.listening = (st === 'AWAITING_CONTINUE'); reading.recording = false;
  } else {
    reading.listening = (st === 'FINALIZING_TRANSCRIPT');
  }
  setReadingStatus(st, m.note);
}

function triggerReadingPassagePopup() {
  clearTimeout(reading.introSafetyTimer);
  reading.introSpeaking = false;
  const m = reading.pendingPassage;
  reading.pendingPassage = null;
  if (m) {
    readingRenderPassage(m);
    return;
  }
  readingPanelShow(true);
  reading.listening = true;
  if (reading.state === 'off' || reading.state === 'WAITING_FOR_READING') {
    reading.state = 'WAITING_FOR_READING';
    setReadingStatus('WAITING_FOR_READING', 'Ready — please read the passage aloud clearly.');
  }
}
function readingRenderPassage(m) {
  if (readingStale(m)) return;
  reading.passage = m.passage || reading.passage;
  reading.round = m.round || reading.round || 1;
  reading.pendingPassage = null;
  clearTimeout(reading.introSafetyTimer);

  renderReadingPassageTokens(reading.passage);
  try {
    $('readingInstr').textContent = m.instructions || 'Read the passage aloud clearly at your own pace.';
    $('readingFeedback').textContent = '';
    $('readingContinuePrompt').classList.add('hidden');
    $('readingActions').classList.add('hidden');
    $('readingLiveTranscript').textContent = '';
    const finishBtn = $('readingFinishBtn');
    if (finishBtn) finishBtn.classList.remove('hidden');
  } catch (e) {}

  readingPanelShow(true);
  log('[READING] passage rendered and displayed in real time');
  resetReadingCapture();
  reading.listening = true;
  reading.state = 'WAITING_FOR_READING';
  setReadingStatus('WAITING_FOR_READING', 'Ready — please read the passage aloud clearly.');
}
function readingRenderRetry(m) {
  if (readingStale(m)) return;
  reading.passage = m.passage || reading.passage;
  renderReadingPassageTokens(reading.passage);
  try {
    $('readingInstr').textContent = m.instructions || 'Please read the passage again, focusing on speaking clearly and accurately.';
    $('readingContinuePrompt').classList.add('hidden');
    $('readingActions').classList.add('hidden');
    $('readingLiveTranscript').textContent = '';
    $('readingFeedback').textContent = '';
    const finishBtn = $('readingFinishBtn');
    if (finishBtn) finishBtn.classList.remove('hidden');
  } catch (e) {}
  resetReadingCapture();
  reading.listening = true;
  reading.state = 'WAITING_FOR_READING';
  setReadingStatus('WAITING_FOR_READING', 'Ready — please read the passage again.');
  readingPanelShow(true);
}
function readingRenderNextRound(m) {
  readingRenderRetry(m);
}
function showReadingContinuePrompt(show) {
  const p = $('readingContinuePrompt');
  if (p) p.classList.toggle('hidden', !show);
  if (show) {
    reading.listening = true;
    reading.state = 'AWAITING_CONTINUE';
    setReadingStatus('AWAITING_CONTINUE');
  }
}
function readingRenderContinuePrompt(m) {
  if (readingStale(m)) return;
  showReadingContinuePrompt(true);
}
function handleReadingContinue(choice) {
  showReadingContinuePrompt(false);
  if (choice) {
    log('[READING] user chose to continue reading');
    resetReadingCapture();
    try {
      $('readingFeedback').textContent = '';
      $('readingLiveTranscript').textContent = '';
    } catch (e) {}
    setReadingStatus('GENERATING_PASSAGE');
    sendReadingMsg({ type: 'reading_start' });
  } else {
    log('[READING] user chose not to continue');
    setReadingEnabled(false);
  }
}
function readingRenderFeedback(m) {
  if (readingStale(m)) return;
  reading._completing = false;
  const disp = plainText(m.display_text || m.text || '');
  reading.pendingFeedback = {
    msg: m,
    displayText: disp,
    chunks: [],
    turnAdded: false,
    completed: !!m.completed,
  };
  try {
    $('readingFeedback').textContent = disp;
    $('readingContinuePrompt').classList.add('hidden');
    if (m.stats) {
      const s = m.stats;
      const bits = [];
      if (s.word_accuracy !== undefined) {
        bits.push(Math.round(s.word_accuracy * 100) + '% accuracy');
      }
      if (s.words_per_minute) bits.push(Math.round(s.words_per_minute) + ' WPM');
      if (s.speaking_duration_s) bits.push(s.speaking_duration_s + 's spoken');
      if (s.total_pauses !== undefined) {
        bits.push(s.total_pauses + ' pause' + (s.total_pauses === 1 ? '' : 's') +
          (s.mid_sentence_pauses_count ? ' (' + s.mid_sentence_pauses_count + ' mid-sentence)' : ''));
      }
      if (bits.length) setReadingStatus(null, bits.join(' • '));
    }
  } catch (e) {}
  setReadingStatus('PROVIDING_FEEDBACK');
  reading.confirmAck = true;
}
function readingRenderSummary(m) {
  if (readingStale(m)) return;
  reading._completing = false;
  const disp = plainText(m.display_text || m.text || '');
  try {
    $('readingFeedback').textContent = disp;
    $('readingActions').classList.remove('hidden');
  } catch (e) {}
  addTurn('Partner', disp);
  setReadingStatus('summary');
  reading.confirmAck = true;
}
function readingError(m) {
  log('[READING] server:', m.message || 'error');
  hearNote(m.message || 'Reading practice hiccup — try again.', 4200);
  setReadingStatus(null, m.retryable ? 'Ready to try again — read whenever you like.' : '');
}
function readingOnPause(ev) {
  if (!reading.active || !reading.listening || micMuted) return;
  if (reading.state !== 'RECORDING_READING' && reading.state !== 'WAITING_FOR_READING') return;
  const durSec = Math.round(ev.duration / 10) / 100;
  if (durSec < 0.35) return;

  const fullText = (reading.transcript + ' ' + (reading.interim || '')).trim();
  const words = fullText.split(/\s+/).filter(Boolean);
  const afterWord = words.length > 0 ? words[words.length - 1].replace(/[^\w'-]/g, '') : '';

  const pauseItem = {
    duration_s: durSec,
    start_s: reading.speechStartedAt ? Math.round(Math.max(0, ev.start - reading.speechStartedAt) / 10) / 100 : null,
    end_s: reading.speechStartedAt ? Math.round(Math.max(0, ev.end - reading.speechStartedAt) / 10) / 100 : null,
    after_word: afterWord,
    before_word: '',
    word_index: words.length,
  };
  reading.pauses.push(pauseItem);
  reading.pauseTotalMs += ev.duration;
  if (durSec >= 1.0) reading.longPauses++;
  log('[READING] measured pause:', durSec + 's after "' + afterWord + '" (total: ' + reading.pauses.length + ')');
}
function readingUpdateBeforeWord(newWordsStr) {
  if (!reading.pauses || !reading.pauses.length) return;
  const words = (newWordsStr || '').trim().split(/\s+/).filter(Boolean);
  for (let i = reading.pauses.length - 1; i >= 0; i--) {
    const p = reading.pauses[i];
    if (!p.before_word && words.length > p.word_index) {
      p.before_word = words[p.word_index].replace(/[^\w'-]/g, '');
    }
  }
}
function readingOnInterim(t) {
  if (!reading.active) return;
  if (reading.state === 'AWAITING_CONTINUE') {
    const clean = (t || '').toLowerCase().trim();
    if (/\b(yes|yeah|sure|yep|continue|next|go ahead|proceed|ok|okay)\b/i.test(clean)) {
      handleReadingContinue(true);
      return;
    }
    if (/\b(no|nope|stop|exit|not now|no thanks|quit|cancel)\b/i.test(clean)) {
      handleReadingContinue(false);
      return;
    }
    return;
  }
  if (!reading.listening) return;
  try { if (audioQ && audioQ.busy) return; } catch (e) {} // AI voice playing: ignore echo
  reading.interim = t;
  const spokenText = getSpokenReadingText(t);
  const tail = spokenText.slice(-200);
  try { $('readingLiveTranscript').textContent = '“' + tail.slice(-300) + '”'; } catch (e) {}
  readingUpdateBeforeWord(spokenText);
  updateRealtimeReadingHighlights(spokenText, true);
}
function readingOnFinal(t) {
  if (!reading.active) return;
  if (reading.state === 'AWAITING_CONTINUE') {
    const clean = (t || '').toLowerCase().trim();
    if (/\b(yes|yeah|sure|yep|continue|next|go ahead|proceed|ok|okay)\b/i.test(clean)) {
      handleReadingContinue(true);
      return;
    }
    if (/\b(no|nope|stop|exit|not now|no thanks|quit|cancel)\b/i.test(clean)) {
      handleReadingContinue(false);
      return;
    }
    return;
  }
  if (!reading.listening && reading.state !== 'FINALIZING_TRANSCRIPT') return;
  if (reading.state !== 'FINALIZING_TRANSCRIPT') {
    try { if (audioQ && audioQ.busy) return; } catch (e) {} // AI feedback/intro echo: never captured
  }
  if (!t || !t.trim()) return;
  reading.transcript = ((reading.transcript + ' ' + t).trim());
  reading.interim = '';
  lastInterim = ''; lastInterimAt = 0;
  const tail = reading.transcript.slice(-300);
  try { $('readingLiveTranscript').textContent = '“' + tail + '”'; } catch (e) {}
  readingUpdateBeforeWord(reading.transcript);
  updateRealtimeReadingHighlights(reading.transcript, false);
}
function readingOnVoiceStart() {
  if (!reading.active || !reading.listening || micMuted || reading.state === 'AWAITING_CONTINUE') return false;
  const now = performance.now();
  if (reading.introSpeaking && audioQ && audioQ.busy) {
    log('[READING] user began reading; cancelling intro speech');
    audioQ.cancel();
    reading.introSpeaking = false;
  }
  if (!reading.voiceReported) {
    reading.voiceReported = true;
    sendReadingMsg({ type: 'reading_voice' });
    // RECORDING_READING acks from the server; capture starts here already
  }
  if (reading.state === 'WAITING_FOR_READING') {
    reading.state = 'RECORDING_READING';
    setReadingStatus('RECORDING_READING', 'Listening to your reading…');
  }
  if (reading.speechStartedAt === 0) reading.speechStartedAt = now;
  reading.voiceSegmentStart = now;
  clearTimeout(reading.silenceTimer);
  reading.silenceTimer = null;
  return true;
}
function readingOnVoiceEnd() {
  if (!reading.active || !reading.listening || micMuted || reading.state === 'AWAITING_CONTINUE') return false;
  const now = performance.now();
  const last = (vad && vad.lastSpeech) ? vad.lastSpeech : now;
  if (reading.voiceSegmentStart) {
    reading.speechMs += Math.max(0, last - reading.voiceSegmentStart);
    reading.voiceSegmentStart = 0;
  }
  reading.lastVoiceAt = last;
  armReadingSilence();
  return true;
}
function armReadingSilence() {
  if (!reading.active) return;
  clearTimeout(reading.silenceTimer);
  reading.silenceTimer = setTimeout(readingMaybeFinalize, readingEndMs());
}
function checkReadingProgress() {
  const pTokens = (reading.passage || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
  const currentText = getSpokenReadingText(reading.interim).toLowerCase();
  const sTokens = currentText.split(/\s+/).filter(Boolean);
  if (!pTokens.length) return { complete: false, spokenCount: sTokens.length, passageCount: 0, ratio: 0 };

  const ratio = sTokens.length / pTokens.length;

  const pTail = pTokens.slice(-4).map(w => w.replace(/[^\w]/g, '')).filter(Boolean);
  const sTail = sTokens.slice(-8).map(w => w.replace(/[^\w]/g, '')).filter(Boolean);
  let tailMatches = 0;
  for (const pw of pTail) {
    if (pw && sTail.includes(pw)) tailMatches++;
  }
  const reachedEnd = (tailMatches >= Math.min(2, pTail.length));

  // User completed full phrase/paragraph if spoken >= 65% of words OR (>= 40% and reached passage end)
  const complete = (ratio >= 0.65) || (ratio >= 0.40 && reachedEnd);
  return {
    complete,
    spokenCount: sTokens.length,
    passageCount: pTokens.length,
    ratio,
    reachedEnd,
  };
}

function promptFinishParagraph() {
  if (!reading.active || (reading.state !== 'RECORDING_READING' && reading.state !== 'WAITING_FOR_READING')) return;
  const now = performance.now();
  if (reading.lastFinishPromptAt && (now - reading.lastFinishPromptAt < 10000)) {
    armReadingSilence();
    return;
  }
  reading.lastFinishPromptAt = now;
  reading.finishPromptCount = (reading.finishPromptCount || 0) + 1;

  if (reading.finishPromptCount > 3) {
    if (reading.lastVoiceAt && (now - reading.lastVoiceAt > 25000)) {
      log('[READING] giveup after repeated finish prompts');
      finalizeReading();
      return;
    }
  }

  const promptMsg = "Please continue reading and finish the paragraph.";
  setReadingStatus(null, promptMsg);
  reading.aiLines.push(promptMsg);
  if (reading.aiLines.length > 6) reading.aiLines.shift();
  try {
    audioQ.enqueue(promptMsg, { stream: false, turn: `r-prompt-${reading.gen}` });
  } catch (e) {}

  clearTimeout(reading.silenceTimer);
  reading.silenceTimer = setTimeout(readingMaybeFinalize, 8000);
}

function readingMaybeFinalize() {
  reading.silenceTimer = null;
  if (!reading.active || (reading.state !== 'RECORDING_READING' && reading.state !== 'WAITING_FOR_READING')) return;
  const progress = checkReadingProgress();
  const now = performance.now();
  const sinceVoice = reading.lastVoiceAt ? now - reading.lastVoiceAt : Infinity;

  // If user hasn't really started speaking yet (< 2 words), keep waiting
  if (progress.spokenCount < 2) {
    if (sinceVoice < READING_GIVEUP_MS) {
      armReadingSilence();
      return;
    }
    return;
  }

  // If user completed the full phrase / paragraph:
  if (progress.complete || (progress.spokenCount >= Math.max(4, Math.floor(progress.passageCount * 0.5)) && sinceVoice >= 2200)) {
    log('[READING] user completed phrase (' + progress.spokenCount + '/' + progress.passageCount + ' words). Finalizing.');
    finalizeReading();
    return;
  }

  // User has NOT completed the paragraph yet (spoke only few words or partial):
  // Do NOT conclude based on few spoken words!
  // If user is silent for too long (>= 4500ms), ask him to finish the paragraph!
  if (sinceVoice >= 4500) {
    promptFinishParagraph();
  } else {
    // Less than 4.5s of silence: re-arm timer for remaining duration
    clearTimeout(reading.silenceTimer);
    reading.silenceTimer = setTimeout(readingMaybeFinalize, Math.max(1200, 4500 - sinceVoice));
  }
}
function readingRecorderStart() {
  if (!mobStreamLive()) return null;
  let rec = null;
  try {
    rec = new MediaRecorder(mobStream, { mimeType: mobMime, audioBitsPerSecond: 32000 });
  } catch (e) {
    try { rec = new MediaRecorder(mobStream, { mimeType: mobMime }); } catch (e2) { return null; }
  }
  reading.mime = mobMime || 'audio/webm';
  reading.chunks = [];
  reading.peak = 0;
  rec.ondataavailable = (ev) => {
    try {
      if (ev.data && ev.data.size) {
        reading.chunks.push(ev.data);
        try { if (vad && vad.level > reading.peak) reading.peak = vad.level; } catch (e) {}
      }
    } catch (e) {}
  };
  rec.onerror = () => { log('[READING] recorder error — capture lost for this reading'); };
  try { rec.start(250); } catch (e) { return null; }
  return rec;
}
function readingOnFirstVoice() {
  if (!reading.active || !reading.listening || micMuted || reading.state === 'AWAITING_CONTINUE') return false;
  if (reading.rec) return true;
  reading.rec = readingRecorderStart();
  if (reading.rec) log('[READING] reading capture started (held stream, full reading)');
  else log('[READING] recorder failed to start');
  return true;
}
function stopReadingRecorder() {
  return new Promise((resolve) => {
    const rec = reading.rec;
    if (!rec) return resolve(null);
    reading.rec = null;
    let settled = false;
    const finish = (blob) => {
      if (settled) return;
      settled = true;
      try { rec.ondataavailable = null; rec.onstop = null; rec.onerror = null; } catch (e) {}
      resolve(blob || null);
    };
    const timer = setTimeout(() => finish(null), 1500);
    rec.onstop = () => {
      clearTimeout(timer);
      const blob = reading.chunks.length ? new Blob(reading.chunks, { type: reading.mime || 'audio/webm' }) : null;
      reading.chunks = [];
      finish(blob);
    };
    try { rec.stop(); } catch (e) { clearTimeout(timer); finish(null); }
  });
}
async function readingPostStt(blob) {
  try {
    const headers = {
      'Content-Type': reading.mime || 'audio/webm',
      'X-Filename': 'reading.' + ((reading.mime || '').indexOf('mp4') >= 0 ? 'm4a' : 'webm'),
      'X-Session': sid,
      'X-Level': String(reading.peak || 0),
    };
    const res = await fetch('/api/stt', { method: 'POST', headers, body: blob });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      log('[READING] transcription failed:', (body && body.error) || ('HTTP ' + res.status));
      return '';
    }
    return (body && body.text) || '';
  } catch (e) {
    log('[READING] transcription error:', (e && e.name) || e);
    return '';
  }
}
// The AI's spoken intro/feedback can leak into the reading capture (speaker
// echo past cancellation). Drop any leading transcript chunk that just
// repeats it, so the analysis compares only the student's own reading.
function stripAiEchoPrefix(text) {
  let out = (text || '').trim();
  const pieces = reading.aiLines.slice(-4);
  for (const ai of pieces) {
    const aw = (ai || '').split(/\s+/).filter(Boolean);
    if (aw.length < 3) continue;
    let changed = true;
    while (changed) {
      changed = false;
      const tw = out.split(/\s+/);
      for (let n = Math.min(aw.length + 4, tw.length); n >= 3; n--) {
        if (sameText(aw.slice(0, n).join(' '), tw.slice(0, n).join(' '), 0.85)) {
          log('[READING] stripped AI echo from transcript head');
          out = tw.slice(n).join(' ');
          changed = true;
          break;
        }
      }
    }
  }
  return out.trim();
}
async function finalizeReading() {
  if (!reading.active || (reading.state !== 'RECORDING_READING' && reading.state !== 'WAITING_FOR_READING')) return;
  stopReadingTimers();
  const t0 = performance.now();
  reading.state = 'FINALIZING_TRANSCRIPT';
  setReadingStatus('FINALIZING_TRANSCRIPT');
  if (reading.voiceSegmentStart) {
    reading.speechMs += Math.max(0, t0 - reading.voiceSegmentStart);
    reading.voiceSegmentStart = 0;
  }
  // Web Speech finals can trail the last word slightly
  if (!reading.rec) await new Promise(r => setTimeout(r, 600));
  let text = ((reading.transcript || '') + ' ' + (reading.interim || '')).replace(/\s+/g, ' ').trim();
  if (reading.rec) {
    const blob = await stopReadingRecorder();
    if (blob && blob.size >= 1500) {
      const stt = (await readingPostStt(blob)).trim();
      if (stt) text = stt;
    }
  }
  text = stripAiEchoPrefix(text);
  const wc = text ? text.trim().split(/\s+/).filter(Boolean).length : 0;
  const dur = Math.round(reading.speechMs / 100) / 10;
  const elapsed = (reading.speechStartedAt && reading.lastVoiceAt) ?
    Math.round(Math.max(0, reading.lastVoiceAt - reading.speechStartedAt) / 100) / 10 : dur;

  // Fill any remaining before_word from finalized text
  const finalWords = text.trim().split(/\s+/).filter(Boolean);
  for (const p of reading.pauses) {
    if (!p.before_word && p.word_index < finalWords.length) {
      p.before_word = finalWords[p.word_index].replace(/[^\w'-]/g, '');
    }
  }

  log('[READING] reading finalized', wc, 'words,', dur + 's speech,',
      elapsed + 's elapsed,', (reading.pauseTotalMs / 1000).toFixed(1) + 's pauses (' + reading.pauses.length + ' pauses)');
  sendReadingMsg({
    type: 'reading_submit',
    text,
    round: reading.round,
    gen: reading.gen,
    word_count: wc,
    duration_s: dur || null,
    elapsed_s: elapsed || null,
    pause_total_s: Math.round(reading.pauseTotalMs / 100) / 10 || null,
    long_pause_count: reading.longPauses,
    pauses: reading.pauses,
  });
  reading.listening = false;
}
function readingAckIfPending() {
  if (!reading.confirmAck) return;
  reading.confirmAck = false;
  try { clearTimeout(reading.ackTimer); clearInterval(reading.ackIv); } catch (e) {}
  reading.ackTimer = setTimeout(() => sendReadingMsg({ type: 'reading_round_ok' }), 600);
}

function micTestErrorMessage(error) {  const name = (error && error.name) || String(error || 'unknown error');
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
  if (recogFatal || micMuted) return;
  listening = true;
  startRecognition();
  log('[STT] recognition started (mic stays open)');
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
let mobPendingIncomplete = null;
let mobPendingTimer = null;

function mobClearPending() {
  if (mobPendingTimer) { clearTimeout(mobPendingTimer); mobPendingTimer = null; }
  mobPendingIncomplete = null;
}

function mobPickMime() {
  if (typeof MediaRecorder === 'undefined') return '';
  const cands = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
    'audio/mp4;codecs=mp4a.40.2',
    'audio/aac',
  ];
  try {
    for (const c of cands) {
      if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(c)) return c;
    }
  } catch (e) {}
  try {
    if (typeof MediaRecorder !== 'undefined') return 'audio/mp4';
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
  const u = {
    startedAt: performance.now(),
    endedAt: 0,
    rec: null,
    chunks: [],
    confirmed: false,
    dead: false,
    gen: mobUttGen,
    peak: (vad && vad.currentSpeechPeak) || 0,
    noiseFloor: (vad && (vad.noiseFloorAtOnset || vad.noiseFloor)) || 0.008,
    voiceConf: 0,
    barged: isPlaybackActive(),
  };
  let rec = null;
  const mime = mobMime || mobPickMime();
  const optionsToTry = [
    mime ? { mimeType: mime, audioBitsPerSecond: 96000 } : null,
    mime ? { mimeType: mime, audioBitsPerSecond: 64000 } : null,
    mime ? { mimeType: mime } : null,
    {},
  ].filter(Boolean);
  for (const opt of optionsToTry) {
    try {
      rec = new MediaRecorder(mobStream, opt);
      if (rec) break;
    } catch (e) {}
  }
  if (!rec) {
    try { rec = new MediaRecorder(mobStream); } catch (e) { return null; }
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
        // Track live peak across whole utterance (measured at 60fps in VAD frame loop)
        try {
          const livePeak = Math.max((vad && vad.level) || 0, (vad && vad.currentSpeechPeak) || 0);
          if (livePeak > u.peak) u.peak = livePeak;
        } catch (e2) {}
        try { if (vad && vad.voiceConfidence > u.voiceConf) u.voiceConf = vad.voiceConfidence; } catch (e3) {}
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
    try { rec.start(); } catch (e2) { return null; }
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
  }
  if (vad && vad.currentSpeechPeak > (u.peak || 0)) u.peak = vad.currentSpeechPeak;

  // 1. Minimum foreground speech peak gate:
  // Intentional near-field user speech directed at the device microphone produces peaks >= 0.035 - 0.250+.
  // Background voices (distant room chatter, TV, hallway) arrive attenuated by distance with peaks < 0.026.
  if ((u.peak || 0) < 0.026) {
    log('[STT] utterance dropped: background voice or low energy (peak ' + (u.peak || 0).toFixed(4) + ' < 0.026)');
    return;
  }

  // 2. SNR gate relative to ambient room noise:
  // Foreground speech stands out >= 2.2x above the ambient noise floor.
  // Ambient room chatter and distant noise have low SNR (< 2.2x).
  const floor = Math.max(0.005, u.noiseFloor || (vad && vad.noiseFloor) || 0.008);
  const snr = (u.peak || 0) / floor;
  if (snr < 2.2) {
    log('[STT] utterance dropped: insufficient SNR above ambient noise (' + snr.toFixed(1) + 'x < 2.2x, peak=' + (u.peak || 0).toFixed(4) + ')');
    return;
  }

  // 3. Spectral speech confidence (rejects snoring, hum, whistles, coughs):
  if (u.voiceConf !== undefined && u.voiceConf < 0.25) {
    log('[STT] non-speech spectral signature (conf ' + u.voiceConf.toFixed(2) + ') — dropped');
    return;
  }
  const duration = Math.max(0, ((u.endedAt || performance.now()) - u.startedAt - 850) / 1000);
  if (duration < 0.28 && (u.peak || 0) < 0.040) {
    log('[STT] transient noise burst dropped (dur=' + duration.toFixed(2) + 's, peak=' + (u.peak || 0).toFixed(4) + ')');
    return;
  }
  mobTranscribe(u.chunks, u.peak || 0, !!u.barged, duration, u.wallEnd || Date.now());
}
function mobOnFirstVoice() {
  // First frame above the noise floor — start the recorder BEFORE the 250ms
  // confirmation gate so the onset is never clipped. Never restart a running
  // unconfirmed recorder here: a micro-dip inside the onset ("h-ello") would
  // otherwise discard the opening syllables over and over, forcing the user
  // to say hello twice. Gaps inside the utterance are harmless for Whisper.
  if (micMuted || mobTestPaused || !mobCaptureOn || !running) return;
  if (reading.active && reading.listening && reading.state !== 'AWAITING_CONTINUE') { readingOnFirstVoice(); return; }
  if (!mobUtt) {
    mobUtt = mobStartUtt();
    if (mobUtt) log('[STT] voice onset — capturing');
    else log('[STT] recorder failed to start — utterance lost');
  }
}
function mobOnSpeechStart() {
  if (reading.active && reading.listening && reading.state !== 'AWAITING_CONTINUE') {
    if (readingOnVoiceStart()) return;
  }
  if (micMuted || mobTestPaused || !mobCaptureOn || !running) return;
  if (!mobUtt) {
    mobUtt = mobStartUtt(); // fallback — first-voice normally beats us here
    if (!mobUtt) {
      log('[STT] recorder failed to start — utterance lost');
      return;
    }
  }
  // Preserve whether the assistant was audible at any point in this
  // utterance; the server can apply its strict full-phrase echo check after
  // transcription without discarding genuine user speech before it is heard.
  mobUtt.barged = mobUtt.barged || isPlaybackActive();
  mobUtt.confirmed = true;
  if (isPlaybackActive() && $("headphonesMode") && $("headphonesMode").checked) bargeIn("confirmed_headphone_speech");
  if (vad && vad.noiseFloorAtOnset) mobUtt.noiseFloor = vad.noiseFloorAtOnset;
  else if (vad && vad.noiseFloor) mobUtt.noiseFloor = vad.noiseFloor;
  if (vad && vad.currentSpeechPeak > mobUtt.peak) mobUtt.peak = vad.currentSpeechPeak;
  try { clearTimeout(mobUtt.blipTimer); } catch (e) {}
}
function mobOnSpeechEnd() {
  if (reading.active && reading.listening && reading.state !== 'AWAITING_CONTINUE') {
    if (readingOnVoiceEnd()) { clearTimeout(speechTimer); return; }
  }
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
  u.endedAt = performance.now();
  u.wallEnd = Date.now() - 850;
  // VAD already includes the complete 850ms silence tail; do not add another capture delay.
  // Preserve trailing plosives (e.g. 't' in 'ate')
  // are fully encoded into the final slice before stopping the recorder.
  setTimeout(() => {
    try {
      if (u.rec && u.rec.state !== 'inactive') u.rec.stop(); // onstop → mobFinishUtt
      else mobFinishUtt(u);
    } catch (e) {
      u.dead = true;
      u.chunks = [];
    }
  }, 0);
}
let mobTranscriptionTail = Promise.resolve(), mobTranscriptionCount = 0;
const mobSttRequests = new Set();
function mobTranscribe(chunks, peak, barged, duration = null, endedAt = Date.now()) {
  const queuedGen = mobUttGen;
  if (mobTranscriptionCount >= 3) { hearNote('Transcription is busy. Pause briefly before your next turn.', 3000); return; }
  mobTranscriptionCount++;
  mobTranscriptionTail = mobTranscriptionTail.catch(() => {}).then(async () => {
    if (queuedGen !== mobUttGen) return;
    await mobTranscribeNow(chunks, peak, barged, duration, endedAt);
  }).finally(() => { mobTranscriptionCount--; });
  return mobTranscriptionTail;
}
async function mobTranscribeNow(chunks, peak, barged, duration, endedAt) {
  const gen = mobUttGen;
  let blob = null;
  try {
    blob = new Blob(chunks, { type: mobMime || 'audio/webm' });
  } catch (e) {
    return;
  }
  if (!blob || blob.size < 800) {
    // Short-word floor: smaller than this is mic bump or cutoff, not speech.
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
  const controller = new AbortController();
  mobSttRequests.add(controller);
  const timeout = setTimeout(() => controller.abort(), 20000);
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
    const r = await fetch('/api/stt', { method: 'POST', headers, body: blob, signal: controller.signal });
    const body = await r.json().catch(() => ({}));
    if (gen !== mobUttGen) return; // stopped/muted/tested mid-flight — stale
    if (!r.ok) {
      const msg = (body && body.error) || ('HTTP ' + r.status);
      log('[STT] transcription failed:', msg);
      try { hideTypingIndicator(); } catch (e) {}
      if ((r.status === 503 || /rate.limit|unavailable|rejected/i.test(msg)) && (window.SpeechRecognition || window.webkitSpeechRecognition)) {
        stopMobileCapture();
        if (!isMobile) await startDesktopVad();
        setupRecog(); resumeSTT();
        hearNote('Using browser speech recognition while the transcription service is unavailable.', 4000);
      } else if (!isMissedOrHiccup(msg)) {
        hearNote(msg, 4000);
      }
      return;
    }
    const text = ((body && body.text) || '').trim();
    let replyLang = (body && body.language) || '';
    log('[STT] server transcript (' + Math.round(performance.now() - t0) + 'ms): ' + JSON.stringify(text.slice(0, 100)) + (replyLang ? ' lang=' + replyLang : ''));
    if (!text) {
      try { hideTypingIndicator(); } catch (e) {}
      return;
    }
    let cleanText = stripLeadingAiEcho(text);
    if (!cleanText || cleanText.length < 2 || isAiEcho(cleanText)) {
      log('[STT] server transcript dropped as echo:', JSON.stringify(text.slice(0, 80)));
      try { hideTypingIndicator(); } catch (e) {}
      return;
    }

    if (mobPendingIncomplete) {
      if (mobPendingTimer) { clearTimeout(mobPendingTimer); mobPendingTimer = null; }
      cleanText = (mobPendingIncomplete.text + ' ' + cleanText).trim();
      replyLang = replyLang || mobPendingIncomplete.lang;
      mobPendingIncomplete = null;
      log('[STT] joined trailing incomplete thought: ' + JSON.stringify(cleanText));
    }

    if (isTrailingIncomplete(cleanText)) {
      mobPendingIncomplete = { text: cleanText, lang: replyLang };
      log('[STT] incomplete trailing phrase held for continuation: ' + JSON.stringify(cleanText));
      mobPendingTimer = setTimeout(() => {
        if (mobPendingIncomplete && gen === mobUttGen) {
          const pending = mobPendingIncomplete;
          mobPendingIncomplete = null;
          mobPendingTimer = null;
          log('[STT] incomplete phrase dispatch timeout: ' + JSON.stringify(pending.text));
          sendText(pending.text, pending.lang);
        }
      }, 2000);
      return;
    }

    speechDurationS = duration;
    speechEndedAt = endedAt;
    sendText(cleanText, replyLang); // clears hearNote + guards duplicates; reply follows the spoken language
  } catch (e) {
    if (gen !== mobUttGen) return;
    log('[STT] transcription error:', (e && e.name) || e);
    try { hideTypingIndicator(); } catch (e2) {}
    hearNote('Transcription failed — check connection or type below.', 4000);
  } finally {
    clearTimeout(timeout);
    mobSttRequests.delete(controller);
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
  listening = true;
  vad = new EnergyVAD({
    onSpeechStart: mobOnSpeechStart,
    onSpeechEnd: mobOnSpeechEnd,
    onFirstVoice: mobOnFirstVoice,
    onPause: (ev) => { if (reading.active) readingOnPause(ev); },
  });
  let wired = false;
  try {
    wired = await vad.attach(stream);
  } catch (e) {
    wired = false;
  }
  // End-of-turn (1000ms pause window) ensures natural clause transitions
  try {
    if (vad) {
      vad.endOfTurnMs = 850;
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
  mobClearPending();
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

function bargeIn(reason = 'user') {
  // Reading Practice owns the airwaves: barge-in (VAD auto or user button)
  // must never cut the AI's reading intro/feedback — the mic is deaf anyway.
  if (reading.active && running) {
    log('[READING] barge-in suppressed during Reading Practice');
    return;
  }
  // Only interrupt if playback or generation is active
  if (!isPlaybackActive() && !llmPending && state !== 'SPEAKING' && state !== 'THINKING') {
    return;
  }
  log('[BARGE-IN] AI speech interrupted (reason=' + reason + ')');
  lastPlaybackEndedAt = performance.now();
  llmPending = false;
  aiSpeaking = false;
  if (vad && vad.setPlaybackActive) vad.setPlaybackActive(false);
  if (vad && vad.reset) vad.reset();
  audioQ.cancel(reason);
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
  }, 200);
}

function sustainedBargeIn() {
  // Acoustic volume / energy (mic RMS) must NEVER interrupt the AI voice,
  // because audio from laptop speakers enters the microphone and falsely
  // cuts off the AI mid-sentence.
  // Intentional user interruption is handled via transcribed speech in r.onresult
  // or via the manual Interrupt button.
  clearTimeout(speechTimer);
}

async function startSession() {
  running = true;
  llmPending = false;
  aiSpeaking = false;
  // Immediate UI feedback on click so Safari responds with zero perceptible latency
  setIcon($('startBtn'), 'square');
  $('startBtn').classList.add('live');
  if ($('topbar')) $('topbar').classList.remove('hidden');
  setState('CONNECTING');
  startClock();

  try {
    await audioQ.unlock();
    await connect();
    $('chatInput').classList.remove('hidden');
    $('interruptBtn').classList.remove('hidden');
    $('muteBtn').classList.remove('hidden');
    $('captionBtn').classList.remove('hidden');
    $('micMuteBtn').classList.remove('hidden');
    if ($('startBtn')) $('startBtn').blur();
    $('interruptBtn').disabled = false;
    setState('LISTENING');

    // When server transcription is available, every supported browser uses one
    // held getUserMedia stream. This avoids Chrome Web Speech's platform-owned
    // stream, which it may close after tens of seconds without user intent.
    if (mobCanUse() && await startMobileCapture()) {
      log('[APP] Flow build v' + APP_BUILD + ' • held microphone + server transcription');
      loadMicDevices();
    } else if (!isMobile) {
      await startDesktopVad();
      resumeSTT();
      startRecogWatchdog();
      startVadHealthWatchdog();
      log('[APP] Flow build v' + APP_BUILD + ' • desktop VAD + browser speech recognition');
    } else {
      // Fallback: browser speech recognition (its platform-owned stream may
      // still open/close between utterances — see the onend note in setupRecog).
      if (mobCanUse()) log('[STT] always-open capture failed — falling back to browser speech recognition');
      else log('[STT] fallback: server_stt=' + !!((serverConfig || {}).server_stt) +
        ' mediarecorder=' + (typeof MediaRecorder !== 'undefined') + ' — using browser speech recognition');
      log('[APP] Flow build v' + APP_BUILD + ' • browser speech recognition path');
      loadMicDevices();
      resumeSTT();
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
  } catch (err) {
    log('[SESSION] start failed:', (err && err.name) || err);
    stopSession();
  }
}

function stopSession() {
  for (const controller of mobSttRequests) controller.abort();
  clearTimeout(window.__flowReconnect);
  running = false;
  listening = false;
  llmPending = false;
  aiSpeaking = false;
  recogFatal = true;
  accumulatedTranscript = '';
  lastInterim = ''; lastInterimAt = 0; awaitingFinal = false; lastSentText = '';
  clearTimeout(speechTimer); speechTimer = null;
  if (vad && vad.setPlaybackActive) vad.setPlaybackActive(false);
  if (vad && vad.reset) vad.reset();
  userSaid('');
  // Reading Practice ends with the session (server clears its state + locally)
  try { if (ws && ws.readyState === 1 && reading.active) ws.send(JSON.stringify({ type: 'reading_stop' })); } catch (e) {}
  stopReadingSession();
  try { LiveCaptions.stop(); } catch (e) {}
  setAiLine('');
  hideTypingIndicator();
  clearTimeout(speechTimer);
  clearTimeout(recogRestartTimer);
  recogRestartTimer = null;
  stopRecogWatchdog();
  stopVadHealthWatchdog();
  const currentRecog = recog;
  recogGeneration++;
  recog = null;
  recogMicStreamOpen = false;
  recogActive = false;
  recogStartedAt = 0;
  // Only here we truly stop recognition and close the mic stream
  try { 
    if (currentRecog) {
      currentRecog.stop();
      log('[STT] recognition stopped - mic stream closed');
    }
  } catch (e) {
    log('[STT] stop error:', e.name);
  }
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

let sessionTransitioning = false;
$('startBtn').onclick = async () => {
  if (sessionTransitioning) return;
  sessionTransitioning = true;
  try {
    if ($('startBtn')) $('startBtn').blur();
    log(running ? '[SESSION] stop requested from main voice control' : '[SESSION] start requested from main voice control');
    if (running) {
      stopSession();
    } else {
      await startSession();
    }
  } finally {
    sessionTransitioning = false;
  }
};
if ($('startBtn')) {
  $('startBtn').onfocus = () => { $('startBtn').blur(); };
}
$('interruptBtn').onclick = () => {
  if (aiSpeaking || llmPending || state === 'SPEAKING' || state === 'THINKING' || (typeof audioQ !== 'undefined' && audioQ && (audioQ.playing || audioQ.busy || (audioQ._pcmSources && audioQ._pcmSources.size > 0)))) {
    bargeIn('manual_button');
  }
};
$('muteBtn').onclick = (e) => {
  muted = !muted;
  audioQ.setMuted(muted);
  setIcon($('muteBtn'), muted ? 'volume-x' : 'volume-2');
  log('[AUDIO] speaker', muted ? 'muted' : 'unmuted');
};
$('micMuteBtn').onclick = (e) => {
  micMuted = !micMuted;
  if (vad) vad.setMuted(micMuted);
  accumulatedTranscript = '';
  lastInterim = '';
  lastInterimAt = 0;
  awaitingFinal = false;
  clearTimeout(speechTimer);
  speechTimer = null;
  if (micMuted) {
    mobClearPending();
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
$('sendBtn').onclick = () => {
  speechDurationS = null;
  speechEndedAt = 0;
  const t = $('textInput').value; $('textInput').value = '';
  if (reading.active && reading.listening && t.trim()) {
    // Typed text during a reading becomes the reading transcript directly
    readingOnFinal(t.trim());
    return;
  }
  if ((aiSpeaking || llmPending || state === 'SPEAKING' || state === 'THINKING' || (typeof audioQ !== 'undefined' && audioQ && (audioQ.playing || audioQ.busy))) && !reading.active) bargeIn('spacebar');
  sendText(t);
};
$('textInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('sendBtn').click(); });
$('mode').onchange = (e) => {
  // Coaching-mode change invalidates an active reading session
  if (reading.active && running) setReadingEnabled(false);
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
  const newStt = sttCodeFor(v);
  const changed = (newStt !== sttLang);
  sttLang = newStt;
  try { localStorage.setItem('lang', v); } catch (e) {}
  if (ws && ws.readyState === 1 && !silent) ws.send(JSON.stringify({ type: 'set_language', value: v }));
  if (changed && recog && listening) {
    // restart recognition only if the language ACTUALLY changed
    try {
      recog.lang = sttLang;
      recog.stop();
    } catch (e) {}
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
  // getUserMedia here would fight the session for the held mic hardware.
  if (mobCaptureOn && mobStreamLive() && vad) {
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
  let stream = null, ctx = null, src = null, silentGain = null;
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
    src = ctx.createMediaStreamSource(stream);
    const an = ctx.createAnalyser();
    an.fftSize = 1024;
    src.connect(an);
    // Safari WebKit fix: route through a silent gain node to destination to force audio processing
    silentGain = ctx.createGain();
    silentGain.gain.value = 0;
    an.connect(silentGain);
    silentGain.connect(ctx.destination);
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
    try { src && src.disconnect(); } catch (e) {}
    try { silentGain && silentGain.disconnect(); } catch (e) {}
    try { stream && stream.getTracks().forEach(t => t.stop()); } catch (e) {}
    try { ctx && ctx.close(); } catch (e) {}
    resumeRecognition();
  }
};
function closeDrawers() {
  $('panel').classList.add('hidden');
  $('scrim').classList.add('hidden');
  document.body.classList.remove('drawer-open');
  $('settingsBtn').classList.remove('hidden');
  // Note: history/chat drawer stays open when clicking outside; only closed via #historyClose cross button
}
function openPanel(o) {
  $('panel').classList.toggle('hidden', !o);
  $('scrim').classList.toggle('hidden', !o);
  document.body.classList.toggle('drawer-open', o);
  if (o && window.FlowLearning && typeof FlowLearning.refresh === 'function') {
    FlowLearning.refresh();
  }
  icons();
}
function openHistory(o) {
  $('history').classList.toggle('hidden', !o);
  document.body.classList.toggle('history-open', o);
  // Do NOT open scrim for history so clicks outside do not close chat and remain interactive
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
  document.body.classList.add('voice-picker-modal-open');
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
  document.body.classList.remove('voice-picker-modal-open');
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
    const dp = $('dailyPanel');
    if (dp && !dp.classList.contains('hidden')) {
      dp.classList.add('hidden');
    }
  }
});
$('panelClose').onclick = () => openPanel(false);
if ($('historyClose')) $('historyClose').onclick = () => openHistory(false);
if ($('historyClear')) {
  $('historyClear').onclick = () => {
    try {
      localStorage.removeItem(getChatStorageKey(currentMode));
      localStorage.removeItem('flow_chat_history_v1');
    } catch (e) {}
    const t = $('transcript');
    if (t) t.innerHTML = '';
    refreshEmptyHint();
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'clear_history', mode: currentMode }));
    }
    log('[HISTORY] cleared for mode:', currentMode);
  };
}
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
  const isGemini = (audioQ.engine === 'gemini');
  const fs = $('fishStatus');
  if (fs) fs.textContent = isGemini ? 'Testing Gemini Live voice…' : 'Testing Fish voice…';
  await audioQ.unlock();
  audioQ.cancel('voice_test');
  audioQ.startTurn('voice_test_' + Date.now());
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
        if (fs) fs.textContent = (isGemini ? 'Gemini Live OK — playing ' : 'Fish OK — playing ') + label + '.';
        log('[TTS] voice test OK:', label);
      } else {
        if (fs) fs.textContent = 'Got audio but browser blocked playback (' + res.reason + '). Check volume + site permission.';
        log('[TTS] test playback blocked:', res.reason);
      }
    } else {
      let msg = `Voice synthesis error (${r.status})`;
      try { const j = await r.json(); if (j.error) msg = j.error; } catch (e) { msg = (await r.text()).slice(0, 160); }
      if (fs) fs.textContent = msg;
      log('[TTS] voice test:', msg);
    }
  } catch (e) {
    if (fs) fs.textContent = 'Test error: ' + e;
  }
};
