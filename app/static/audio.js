/* Audio queue: Fish only. No fallback — failures surface as errors. */
class AudioQueue {
  constructor() {
    this.q = []; this.playing = false; this.muted = false;
    this.currentAudio = null; this.cancelled = false;
    this.onSpeaking = null; this.onDrained = null;
    this.onSentenceStart = null; // fired when a sentence's voice is ready and starts playing
    this.onError = null;
    this.onLog = null; // mirror key events to the on-page log box
    this.onFirstAudio = null; // ({totalMs, ttsMs, bufferMs, streamed}) first sound of a turn
    this.turnLat = null; // server-side latency stages for the current turn
    this.fishVoice = ''; // reference_id or gemini voice name
    this.engine = 'fish'; // 'fish' | 'gemini'
    this._cache = new Map(); // text -> Promise<{url, err}> prefetched while current plays
    this._unlocked = false;
    this._aborts = new Set(); // in-flight TTS fetches — aborted on barge-in
    this._gen = 0; // bumped on cancel: a late response from the old turn is dropped
    this._streamEl = null; // the progressively-playing element (barge-in must stop it)
    this._turnId = 0;
    this.deadTurns = new Set(); // turns interrupted mid-flight: their late audio is dropped
    this._turnT0 = 0;
    this._expectFirst = false;
    this._pcmSources = new Set();
    this._pcmGain = null;
    this._pcmTurn = ''; // live turn id these PCM stats belong to
    this._pcmRecvSec = 0; // total PCM audio seconds received this turn
    this._pcmPlayStart = 0; // ctx.currentTime when this turn's first chunk plays
  }
  setMuted(m) {
    this.muted = !!m;
    if (this.currentAudio) {
      try { this.currentAudio.muted = this.muted; } catch (e) {}
    }
    if (this._streamEl) {
      try { this._streamEl.muted = this.muted; } catch (e) {}
    }
    if (this._pcmGain) {
      try { this._pcmGain.gain.value = this.muted ? 0 : 1; } catch (e) {}
    }
  }
  _log(...a) {
    try { console.log('[AUDIO]', ...a); } catch (e) {}
    try { this.onLog && this.onLog(...a); } catch (e) {}
  }
  async unlock() {
    // Browsers gate audio behind a user gesture. Start is that gesture:
    // resume AudioContext + play 0.2s of silence so later .play() is allowed.
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) {
        this._ctx = this._ctx || new AC();
        if (this._ctx.state === 'suspended') await this._ctx.resume();
        if (!this._pcmGain) {
          this._pcmGain = this._ctx.createGain();
          this._pcmGain.gain.value = this.muted ? 0 : 1;
          this._pcmGain.connect(this._ctx.destination);
        }
      }
      const silence = new Audio('data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA');
      silence.volume = 0;
      await silence.play().catch(() => {});
      this._unlocked = true;
      this._log('audio unlocked (gesture accepted)');
    } catch (e) {
      this._log('unlock failed:', e);
    }
  }
  async init() {
    console.log('[AUDIO] fish-only');
    return null;
  }
  cancel() {
    this._nextPcmTime = 0;
    this._pcmTurn = '';
    this._pcmRecvSec = 0;
    this._pcmPlayStart = 0;
    this.cancelled = false;
    this.q = [];
    this._gen++;
    // the server may already have sent the next phrases of this turn: mark the
    // turn dead so they are dropped instead of starting to speak again
    if (this._turnId) this.deadTurns.add(this._turnId);
    if (this.deadTurns.size > 24) this.deadTurns = new Set([...this.deadTurns].slice(-8));
    try { this.currentAudio && this.currentAudio.pause(); } catch (e) {}
    this.currentAudio = null;
    this._stopStream(); // kills the progressive <audio> request too
    for (const ac of this._aborts) { try { ac.abort(); } catch (e) {} }
    this._aborts.clear();

    // STOP ALL ACTIVE GEMINI LIVE PCM AUDIO BUFFER NODES IMMEDIATELY
    if (this._pcmSources && this._pcmSources.size > 0) {
      for (const src of this._pcmSources) {
        try { src.stop(0); src.disconnect(); } catch (e) {}
      }
      this._pcmSources.clear();
    }
    if (this._pcmGain && this._ctx) {
      try {
        this._pcmGain.gain.setValueAtTime(0, this._ctx.currentTime);
        this._pcmGain.disconnect();
        this._pcmGain = this._ctx.createGain();
        this._pcmGain.gain.value = this.muted ? 0 : 1;
        this._pcmGain.connect(this._ctx.destination);
      } catch (e) {}
    }

    // settle the in-flight playUrl promise — pause() never fires 'ended',
    // so without this the queue wedges silent forever after an interrupt
    try { this._playResolve && this._playResolve({ ok: false, reason: 'cancelled' }); } catch (e) {}
    this._playResolve = null;
    for (const p of this._cache.values()) {
      Promise.resolve(p).then(r => { try { r && r.url && URL.revokeObjectURL(r.url); } catch (e) {} });
    }
    this._cache.clear();
    this.playing = false;
    this._expectFirst = false;
    console.log('[AUDIO] cancel — playback stopped, queue cleared');
  }
  get busy() { return this.playing || this.q.length > 0; }
  enqueue(text, opts) {
    if (!text || !text.trim()) return;
    // stale audio from an interrupted turn must never resume
    if (opts && opts.turn && this.deadTurns.has(opts.turn)) {
      this._log('dropped stale audio from an interrupted turn');
      return;
    }
    // opts.stream: play this phrase progressively (first phrase of a turn — the
    // browser starts talking on the first frames instead of the whole mp3)
    this.q.push({ text, stream: !!(opts && opts.stream), turn: (opts && opts.turn) || '' });
    try { this.onSpeaking && this.onSpeaking(); } catch (e) {}
    if (this.playing) this._prefetch();
    this.pump();
  }
  _key(text) { return (this.engine || 'fish') + '|' + (this.fishVoice || 'default') + '|' + text; }
  _fetchOne(text) {
    const voice = this.fishVoice || '';
    const engine = this.engine || 'fish';
    const gen = this._gen;
    const ac = new AbortController();
    this._aborts.add(ac);
    const t0 = performance.now();
    const stamp = (extra) => ({ t0, t1: performance.now(), ...extra });
    return (async () => {
      try {
        const r = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, voice, engine }), signal: ac.signal });
        if (gen !== this._gen) return stamp({ url: null, err: 'cancelled' });
        if (r.ok && (r.headers.get('content-type') || '').includes('audio')) {
          const blob = await r.blob();
          return stamp({ url: URL.createObjectURL(blob), err: null });
        }
        let msg = `Voice synthesis error (${r.status})`;
        try { const j = await r.json(); if (j.error) msg = j.error; } catch (e) {}
        return stamp({ url: null, err: msg });
      } catch (e) {
        return stamp({ url: null, err: (e && e.name === 'AbortError') ? 'cancelled' : 'Could not reach server' });
      } finally {
        this._aborts.delete(ac);
      }
    })();
  }
  _prefetch() {
    // download the waiting line(s) in the background while the current one
    // plays — so there is no network gap between sentences
    for (const t of this.q.slice(0, 3).map((i) => i.text)) {
      const k = this._key(t);
      if (!this._cache.has(k)) {
        this._cache.set(k, this._fetchOne(t));
        if (this._cache.size > 6) {
          const oldest = this._cache.keys().next().value;
          if (oldest !== k) this._cache.delete(oldest);
        }
      }
    }
  }
  async pump() {
    if (this.playing || this.cancelled) return;
    const item = this.q.shift();
    if (!item) { try { this.onDrained && this.onDrained(); } catch (e) {} return; }
    const next = item.text;
    const gen = this._gen;
    this.playing = true;
    try { this.onSpeaking && this.onSpeaking(); } catch (e) {}
    const t0 = performance.now();
    this._prefetch();
    if (item.stream) {
      // First phrase of the turn: play it progressively. Fish emits mp3 frames
      // while it synthesises, so sound starts ~1s before the body is complete.
      const res = await this._playStream(next, performance.now(), item.turn);
      if (res.ok) {
        this._log(`streamed first phrase (${next.length} chars)`);
      } else if (res.reason !== 'cancelled') {
        this._log('stream playback failed:', res.reason, '— retrying as a full download');
        const j = await this._fetchOne(next);
        if (j.url && !this.cancelled && gen === this._gen) {
          // pass the turn through: a retry of an old turn must not be reported
          // as the current turn's first audio
          const r2 = await this.playUrl(j.url, next, j, item.turn);
          try { URL.revokeObjectURL(j.url); } catch (e) {}
          if (!r2.ok) this._log('stream retry failed:', r2.reason);
        }
      }
      if (!this.cancelled) await new Promise(r => setTimeout(r, 25));
      this.playing = false;
      this.pump();
      return;
    }
    const ck = this._key(next);
    let job = this._cache.get(ck);
    this._cache.delete(ck);
    if (!job) job = this._fetchOne(next);
    const { url, err } = await job;
    if (this.cancelled || gen !== this._gen) { try { url && URL.revokeObjectURL(url); } catch (e) {} this.playing = false; if (!this.cancelled) this.pump(); return; }
    if (url) {
      const res = await this.playUrl(url, next, job, item.turn);
      try { URL.revokeObjectURL(url); } catch (e) {}
      if (res.ok) this._log(`playing (${next.length} chars)`);
      else if (res.reason !== 'cancelled') {
        this._log('PLAYBACK FAILED:', res.reason, '— tap "Test voice" and read the status line');
        try { this.onError && this.onError('Browser refused to play audio (' + res.reason + '). Tap Test voice.'); } catch (e) {}
      }
      this._prefetch();
    } else {
      this._log('fetch failed:', err);
      try { this.onError && this.onError(err); } catch (e) {}
      // Fallback display when TTS is unavailable
      try { this.onSentenceStart && this.onSentenceStart(next); } catch (e) {}
      const readTime = Math.max(1500, next.length * 55);
      await new Promise(r => setTimeout(r, readTime));
    }
    if (!this.cancelled) await new Promise((r) => setTimeout(r, 25));
    this.playing = false;
    this.pump();
  }
  playUrl(url, text = '', timing = null, turn = '') {
    // Returns {ok, reason}. Retries once. Never hangs: cancel() settles it
    // via _playResolve, plus a 45s safety timeout.
    return new Promise((resolve) => {
      if (this.cancelled) return resolve({ ok: false, reason: 'cancelled' });
      this._playResolve = resolve;
      let done = false;
      const settle = (v) => { if (!done) { done = true; this._playResolve = null; resolve(v); } };
      const timer = setTimeout(() => settle({ ok: false, reason: 'playback timeout' }), 45000);
      const a = new Audio(url);
      a.muted = this.muted;
      this.currentAudio = a;
      a.onended = () => { clearTimeout(timer); settle({ ok: true }); };
      a.onerror = () => { clearTimeout(timer); settle({ ok: false, reason: 'audio decode error' }); };
      let started = false;
      const triggerStart = () => {
        if (!started) {
          started = true;
          try { text && this.onSentenceStart && this.onSentenceStart(text); } catch (e) {}
        }
      };
      a.onplaying = triggerStart;
      const t0 = timing && timing.t0;
      const readyAt = timing && timing.t1;
      const attempt = (n) => {
        a.play().then(() => {
          triggerStart();
          this._reportFirstAudio(readyAt, t0, false, turn);
        }).catch((e) => {
          const reason = (e && e.name) || 'play rejected';
          if (n < 1 && !this.cancelled) {
            setTimeout(() => attempt(n + 1), 300);
          } else {
            triggerStart();
            clearTimeout(timer);
            settle({ ok: false, reason });
          }
        });
      };
      attempt(0);
    });
  }
  _playStream(text, tReq, turn) {
    // Progressive first-phrase playback: the element streams /api/tts/stream and
    // starts on the first frames. Barge-in cancels it by dropping src + load().
    return new Promise((resolve) => {
      if (this.cancelled) return resolve({ ok: false, reason: 'cancelled' });
      this._playResolve = resolve;
      let done = false, dataAt = 0, started = false;
      const settle = (v) => { if (!done) { done = true; this._playResolve = null; resolve(v); } };
      // 20s: a suspended/background tab defers media start — surfacing that as
      // a failure quickly is better than a 45s dead wait, and the caller retries
      // through the download path
      const timer = setTimeout(() => { this._stopStream(); settle({ ok: false, reason: 'playback timeout' }); }, 20000);
      const a = new Audio();
      a.muted = this.muted;
      this.currentAudio = a;
      this._streamEl = a;
      a.preload = 'auto';
      const onData = () => {
        if (dataAt) return;
        dataAt = performance.now();
        this._log('first audio frames ready ' + ((dataAt - tReq) / 1000).toFixed(2) + 's after TTS request');
      };
      a.onprogress = onData;
      a.onloadedmetadata = onData;
      a.onplaying = () => {
        if (started) return;
        started = true;
        try { this.onSentenceStart && this.onSentenceStart(text); } catch (e) {}
        // dataAt stays null when the browser never reported progress — report
        // n/a rather than a fake 0ms
        this._reportFirstAudio(dataAt || null, tReq, true, turn);
      };
      a.onended = () => { clearTimeout(timer); settle({ ok: true }); };
      a.onerror = () => { clearTimeout(timer); settle({ ok: false, reason: 'tts stream failed' }); };
      const eng = encodeURIComponent(this.engine || 'fish');
      a.src = '/api/tts/stream?voice=' + encodeURIComponent(this.fishVoice || '') +
              '&engine=' + eng +
              '&text=' + encodeURIComponent(text);
      const attempt = (n) => {
        a.play().catch((e) => {
          if (n < 1 && !this.cancelled) setTimeout(() => attempt(n + 1), 200);
          else { clearTimeout(timer); settle({ ok: false, reason: (e && e.name) || 'play rejected' }); }
        });
      };
      attempt(0);
    });
  }
  _stopStream() {
    const a = this._streamEl;
    this._streamEl = null;
    if (!a) return;
    try { a.pause(); } catch (e) {}
    try { a.removeAttribute('src'); a.load(); } catch (e) {} // aborts the in-flight request
  }
  _reportFirstAudio(tReady, tReq, streamed, turn) {
    // One report per turn: the number the user actually feels. Only the audio
    // that BELONGS to this turn counts — the greeting or the previous turn's
    // tail finishing right after the user stops must not be reported as a
    // lightning-fast reply.
    if (!this._expectFirst || !this._turnT0) return;
    if (turn && this._turnId && turn !== this._turnId) return;
    this._expectFirst = false;
    const now = performance.now();
    const info = {
      turnId: this._turnId,
      streamed: !!streamed,
      totalMs: now - this._turnT0,
      ttsMs: (tReady == null || tReq == null) ? null : Math.max(0, tReady - tReq),
      bufferMs: (tReady == null) ? null : Math.max(0, now - tReady),
      lat: this.turnLat || null,
    };
    try { this.onFirstAudio && this.onFirstAudio(info); } catch (e) {}
  }
  playLivePcmChunk(pcmB64, rate = 24000, turn = '') {
    if (this.cancelled || this.muted) return;
    if (turn && this.deadTurns.has(turn)) return;
    try {
      const binary = atob(pcmB64);
      const len = Math.floor(binary.length / 2);
      if (len === 0) return;
      const float32 = new Float32Array(len);
      for (let i = 0; i < len; i++) {
        const low = binary.charCodeAt(i * 2);
        const high = binary.charCodeAt(i * 2 + 1);
        let int16 = (high << 8) | low;
        if (int16 >= 32768) int16 -= 65536;
        float32[i] = int16 / 32768.0;
      }
      const AC = window.AudioContext || window.webkitAudioContext;
      const ctx = this._ctx || new AC();
      this._ctx = ctx;
      if (ctx.state === 'suspended') ctx.resume();

      if (!this._pcmGain) {
        this._pcmGain = ctx.createGain();
        this._pcmGain.gain.value = this.muted ? 0 : 1;
        this._pcmGain.connect(ctx.destination);
      }

      const buf = ctx.createBuffer(1, float32.length, rate);
      buf.copyToChannel(float32, 0);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this._pcmGain);

      this._pcmSources.add(src);

      const now = ctx.currentTime;
      const playAt = Math.max(now, this._nextPcmTime || now);
      src.start(playAt);
      this._nextPcmTime = playAt + buf.duration;
      if (!turn || turn !== this._pcmTurn) {
        this._pcmTurn = turn || '';
        this._pcmRecvSec = 0;
        this._pcmPlayStart = playAt;
      }
      this._pcmRecvSec += buf.duration;
      this.playing = true;
      try { this.onSpeaking && this.onSpeaking(); } catch (e) {}

      if (this._expectFirst) {
        this._reportFirstAudio(performance.now(), this._turnT0, true, turn);
      }

      src.onended = () => {
        this._pcmSources.delete(src);
        if (this._pcmSources.size === 0 || (ctx.currentTime >= (this._nextPcmTime - 0.05))) {
          this.playing = false;
          try { this.onDrained && this.onDrained(); } catch (e) {}
        }
      };
    } catch (err) {
      console.warn('[AUDIO] Live PCM decode error:', err);
    }
  }
  liveProgress() {
    // {playedSec, recvSec} for this Live turn's PCM audio, or null when
    // unknown. Lets center text reveal at speaking pace instead of racing
    // ahead of the voice.
    try {
      const ctx = this._ctx;
      if (!ctx || !this._pcmRecvSec) return null;
      const played = Math.max(0, Math.min(this._pcmRecvSec, ctx.currentTime - this._pcmPlayStart));
      return { playedSec: played, recvSec: this._pcmRecvSec };
    } catch (e) { return null; }
  }
}
window.AudioQueue = AudioQueue;
