/* Fish audio or Gemini native audio only. One generation owns playback. */
class AudioQueue {
  constructor() {
    this.q = []; this.playing = false; this.fetching = false; this.muted = false;
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
    this._activePlayTimer = null;
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
    // resume AudioContext + play a frame of silence so later .play() is allowed.
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
        // Play 1 sample of silence via Web Audio to fully activate audio hardware
        try {
          const buf = this._ctx.createBuffer(1, 1, 22050);
          const src = this._ctx.createBufferSource();
          src.buffer = buf;
          src.connect(this._pcmGain);
          src.start(0);
        } catch (e) {}
      }
      // Non-blocking gesture unlock for HTMLAudioElement on Safari/WebKit:
      // In Safari, awaiting audio.play() on base64 data URIs can hang indefinitely.
      // We invoke play() synchronously within the user gesture and race with a short timeout.
      try {
        const silence = new Audio('data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA');
        silence.volume = 0;
        const p = silence.play();
        if (p && p.catch) p.catch(() => {});
        await Promise.race([
          p || Promise.resolve(),
          new Promise(r => setTimeout(r, 120))
        ]).catch(() => {});
      } catch (e) {}
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
  startTurn(turnId = '') {
    this.cancelled = false;
    this._turnId = turnId || ('turn_' + Date.now());
    this._pcmTurn = this._turnId;
    this._turnT0 = performance.now();
    this._expectFirst = true;
    this.turnLat = null;
    this._nextPcmTime = 0;
    this._pcmRecvSec = 0;
    this._pcmPlayStart = 0;
  }
  duck() {
    if (this._pcmGain && this._ctx) {
      try { this._pcmGain.gain.setValueAtTime(0, this._ctx.currentTime); } catch (e) {}
    }
    if (this.currentAudio) {
      try { this.currentAudio.volume = 0; } catch (e) {}
    }
    if (this._streamEl) {
      try { this._streamEl.volume = 0; } catch (e) {}
    }
  }
  unduck() {
    if (this.cancelled) return;
    if (this._pcmGain && this._ctx) {
      try { this._pcmGain.gain.setValueAtTime(this.muted ? 0 : 1, this._ctx.currentTime); } catch (e) {}
    }
    if (this.currentAudio) {
      try { this.currentAudio.volume = 1; } catch (e) {}
    }
    if (this._streamEl) {
      try { this._streamEl.volume = 1; } catch (e) {}
    }
  }
  cancel(reason = '') {
    this.cancelled = true;
    this._pumpGen = null;
    this._nextPcmTime = 0;
    // The server may already have sent the next phrases/chunks of this turn:
    // mark all known turn tags dead so late frames are dropped.
    if (this._turnId) this.deadTurns.add(this._turnId);
    if (this._pcmTurn) this.deadTurns.add(this._pcmTurn);
    if (this.deadTurns.size > 24) this.deadTurns = new Set([...this.deadTurns].slice(-8));

    this._pcmTurn = '';
    this._pcmRecvSec = 0;
    this._pcmPlayStart = 0;
    this.q = [];
    this._gen++;
    if (this._activePlayTimer) {
      clearTimeout(this._activePlayTimer);
      this._activePlayTimer = null;
    }
    if (this.currentAudio) {
      try {
        this.currentAudio.volume = 0;
        this.currentAudio.onended = null;
        this.currentAudio.onerror = null;
        this.currentAudio.onplaying = null;
        this.currentAudio.pause();
        this.currentAudio.currentTime = 0;
        this.currentAudio.removeAttribute('src');
        this.currentAudio.load();
      } catch (e) {}
      this.currentAudio = null;
    }
    this._stopStream(); // kills the progressive <audio> request too
    for (const ac of this._aborts) { try { ac.abort(); } catch (e) {} }
    this._aborts.clear();

    // STOP ALL ACTIVE GEMINI LIVE PCM AUDIO BUFFER NODES IMMEDIATELY
    if (this._pcmSources && this._pcmSources.size > 0) {
      for (const src of this._pcmSources) {
        try {
          src.onended = null;
          src.stop(0);
          src.disconnect();
        } catch (e) {}
      }
      this._pcmSources.clear();
    }
    if (this._pcmGain && this._ctx) {
      try {
        this._pcmGain.gain.cancelScheduledValues(this._ctx.currentTime);
        this._pcmGain.gain.setValueAtTime(0, this._ctx.currentTime);
        this._pcmGain.disconnect();
      } catch (e) {}
      this._pcmGain = null;
    }

    // settle the in-flight playUrl promise — pause() never fires 'ended',
    // so without this the queue wedges silent forever after an interrupt
    try { this._playResolve && this._playResolve({ ok: false, reason: 'cancelled' }); } catch (e) {}
    this._playResolve = null;
    for (const p of this._cache.values()) {
      Promise.resolve(p).then(r => {
        try {
          if (r && r.audio) {
            r.audio.onended = null;
            r.audio.onerror = null;
            r.audio.onplaying = null;
            r.audio.pause();
            r.audio.removeAttribute('src');
            r.audio.load();
          }
          if (r && r.url) URL.revokeObjectURL(r.url);
        } catch (e) {}
      });
    }
    this._cache.clear();
    this.playing = false;
    this.fetching = false;
    this._expectFirst = false;
    this._log('[AUDIO] cancel — playback stopped, queue cleared (reason=' + (reason || 'unknown') + ')');
  }
  get busy() { return this.playing || this.fetching || this.q.length > 0 || (this._pcmSources && this._pcmSources.size > 0); }
  enqueue(text, opts) {
    if (!text || !text.trim()) return;
    const spokenWords = text.replace(/\[[^\]]*\]/g, '').replace(/[\[\]]/g, '').trim();
    if (!spokenWords || !/[\p{L}\p{N}]/u.test(spokenWords)) {
      return;
    }
    const turn = (opts && opts.turn) || '';
    const reqId = (opts && opts.req_id) || turn || '';
    // stale audio from an interrupted turn must never resume
    if (turn && this.deadTurns.has(turn)) {
      this._log(`[AUDIO req_id=${reqId || 'n/a'}] dropped stale audio from an interrupted turn`);
      return;
    }
    this._log(`[TTS req_id=${reqId || 'n/a'}] TTS request queued text="${text.slice(0, 40)}" (stream=${!!(opts && opts.stream)})`);
    // opts.stream: play this phrase progressively (first phrase of a turn — the
    // browser starts talking on the first frames instead of the whole mp3)
    this.q.push({ text, rate: Number(opts && opts.rate) || 1, stream: !!(opts && opts.stream), turn, req_id: reqId });
    this._prefetch(); // always start prefetching queued items
    this.pump();
  }
  _key(text) { return (this.engine || 'fish') + '|' + (this.fishVoice || 'default') + '|' + text; }
  _fetchOne(text, reqId = '') {
    const voice = this.fishVoice || '';
    const engine = this.engine || 'fish';
    const gen = this._gen;
    const ac = new AbortController();
    this._aborts.add(ac);
    const timeout = setTimeout(() => ac.abort(), 12000);
    const t0 = performance.now();
    const stamp = (extra) => ({ t0, t1: performance.now(), ...extra });
    this._log(`[TTS req_id=${reqId || 'n/a'}] TTS fetch request started text="${text.slice(0, 40)}" engine=${engine}`);
    return (async () => {
      try {
        const r = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, voice, engine }), signal: ac.signal });
        if (gen !== this._gen) return stamp({ url: null, audio: null, err: 'cancelled' });
        if (r.ok && (r.headers.get('content-type') || '').includes('audio')) {
          const blob = await r.blob();
          if (gen !== this._gen || ac.signal.aborted) return stamp({ url: null, audio: null, err: 'cancelled' });
          const url = URL.createObjectURL(blob);
          const audio = new Audio(url);
          audio.preload = 'auto';
          audio.load();
          return stamp({ url, audio, err: null });
        }
        let msg = `Voice synthesis error (${r.status})`;
        try { const j = await r.json(); if (j.error) msg = j.error; } catch (e) {}
        return stamp({ url: null, audio: null, err: msg });
      } catch (e) {
        return stamp({ url: null, audio: null, err: (e && e.name === 'AbortError') ? 'cancelled' : 'Could not reach server' });
      } finally {
        clearTimeout(timeout);
        this._aborts.delete(ac);
      }
    })();
  }
  _prefetch() {
    // download the waiting line(s) in the background while the current one
    // plays — so there is no network gap between sentences
    for (const item of this.q.slice(0, 3)) {
      if (item.stream) continue;
      const t = item.text;
      const k = this._key(t);
      if (!this._cache.has(k)) {
        this._cache.set(k, this._fetchOne(t, item.req_id));
        if (this._cache.size > 6) {
          const oldest = this._cache.keys().next().value;
          if (oldest !== k) {
            const oldP = this._cache.get(oldest);
            if (oldP) {
              Promise.resolve(oldP).then(r => {
                try {
                  if (r && r.audio) { r.audio.pause(); r.audio.removeAttribute('src'); r.audio.load(); }
                  if (r && r.url) URL.revokeObjectURL(r.url);
                } catch (e) {}
              });
            }
            this._cache.delete(oldest);
          }
        }
      }
    }
  }
  async pump() {
    if (this._pumpGen === this._gen || this.cancelled) return;
    const gen = this._gen;
    this._pumpGen = gen;
    try {
      while (!this.cancelled && gen === this._gen && this.q.length) {
        const item = this.q.shift();
        if (item.turn && this.deadTurns.has(item.turn)) continue;
        this.fetching = true;
        this._prefetch();
        let result;
        this._playbackRate = item.rate;
        if (item.stream) {
          result = await this._playStream(item.text, performance.now(), item.turn, item.req_id);
        } else {
          const key = this._key(item.text);
          const job = this._cache.get(key) || this._fetchOne(item.text, item.req_id);
          this._cache.delete(key);
          const data = await job;
          if (gen !== this._gen || this.cancelled) {
            if (data.url) URL.revokeObjectURL(data.url);
            return;
          }
          try {
            result = data.url ? await this.playUrl(data.url, item.text, data, item.turn, item.req_id, data.audio)
                              : { ok: false, reason: data.err };
          } finally { if (data.url) URL.revokeObjectURL(data.url); }
        }
        if (gen !== this._gen || this.cancelled) return;
        // A failed progressive media element may retry once with the SAME voice/provider.
        if (!result.ok && result.reason !== 'cancelled' && !result.started && item.stream) {
          const data = await this._fetchOne(item.text, item.req_id);
          if (gen !== this._gen || this.cancelled) {
            if (data.url) URL.revokeObjectURL(data.url);
            return;
          }
          if (data.url) {
            try { result = await this.playUrl(data.url, item.text, data, item.turn, item.req_id, data.audio); }
            finally { URL.revokeObjectURL(data.url); }
          } else { result = {ok: false, reason: data.err}; }
        }
        if (gen !== this._gen || this.cancelled) return;
        if (!result.ok && result.reason !== 'cancelled') {
          try { this.onError && this.onError(result.reason); } catch (e) {}
          try { this.onSentenceStart && this.onSentenceStart(item.text, item.turn); } catch (e) {}
        }
        this.fetching = false;
        this.playing = false;
      }
    } finally {
      if (gen === this._gen) {
        this._pumpGen = null;
        this.fetching = false;
        this.playing = false;
        if (this.q.length && !this.cancelled) this.pump();
        else { try { this.onDrained && this.onDrained(); } catch (e) {} }
      }
    }
  }
  playUrl(url, text = '', timing = null, turn = '', reqId = '', preAudio = null) {
    return this._playElement(preAudio || new Audio(url), text, timing, turn, false);
  }
  _playStream(text, tReq, turn, reqId = '') {
    const a = new Audio();
    a.src = '/api/tts/stream?voice=' + encodeURIComponent(this.fishVoice || '') +
      '&engine=' + encodeURIComponent(this.engine || 'fish') + '&text=' + encodeURIComponent(text);
    return this._playElement(a, text, { t0: tReq }, turn, true);
  }
  _playElement(a, text, timing, turn, streamed) {
    const gen = this._gen;
    return new Promise(resolve => {
      if (this.cancelled) return resolve({ ok: false, reason: 'cancelled' });
      let done = false, started = false, readyAt = timing && timing.t1;
      const settle = result => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        a.onended = a.onerror = a.onplaying = a.onprogress = a.onloadedmetadata = null;
        try { a.pause(); a.removeAttribute('src'); a.load(); } catch (e) {}
        if (this.currentAudio === a) this.currentAudio = null;
        if (this._streamEl === a) this._streamEl = null;
        if (this._playResolve === settle) this._playResolve = null;
        resolve({ started, ...result });
      };
      let timer = setTimeout(() => settle({ ok: false, reason: 'Audio did not start within 12 seconds' }), 12000);
      this._playResolve = settle;
      this.currentAudio = a;
      if (streamed) this._streamEl = a;
      a.muted = this.muted;
      a.preload = 'auto';
      a.playbackRate = this._playbackRate || 1;
      a.onloadedmetadata = a.onprogress = () => { if (!readyAt) readyAt = performance.now(); };
      a.onplaying = () => {
        if (started || done || gen !== this._gen) return;
        started = true;
        clearTimeout(timer);
        timer = setTimeout(() => settle({ok: false, reason: 'Audio playback timed out'}), Math.min(90000, Math.max(20000, text.length * 160)));
        this.fetching = false;
        this.playing = true;
        try { this.onSpeaking && this.onSpeaking(); } catch (e) {}
        try { this.onSentenceStart && this.onSentenceStart(text, turn); } catch (e) {}
        this._reportFirstAudio(readyAt || null, timing && timing.t0, streamed, turn);
      };
      a.onended = () => settle({ ok: true });
      a.onerror = () => settle({ ok: false, reason: 'Voice service or audio decoding failed' });
      try {
        const job = a.play();
        if (job && job.catch) job.catch(e => settle({ ok: false, reason: e.name || 'Playback refused' }));
      } catch (e) { settle({ ok: false, reason: e.name || 'Playback refused' }); }
    });
  }
  _stopStream() {
    const a = this._streamEl;
    this._streamEl = null;
    if (a) { try { a.pause(); a.removeAttribute('src'); a.load(); } catch (e) {} }
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
    if (this._turnId && turn && turn !== this._turnId && this.deadTurns.has(this._turnId)) return;
    if (this._pcmTurn && this.deadTurns.has(this._pcmTurn)) return;
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
      if (!this._pcmTurn || this._pcmTurn !== turn) {
        this._log(`[AUDIO req_id=${turn || 'n/a'}] live audio playback started`);
      }
      try { this.onSpeaking && this.onSpeaking(); } catch (e) {}

      if (this._expectFirst) {
        this._reportFirstAudio(performance.now(), this._turnT0, true, turn);
      }

      src.onended = () => {
        this._pcmSources.delete(src);
        try { src.disconnect(); } catch (e) {}
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
