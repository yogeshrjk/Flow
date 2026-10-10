/* Local energy VAD (free, no server). Distinguishes short pause vs end-of-turn. */
class EnergyVAD {
  constructor({ onSpeechStart, onSpeechEnd, onPartial, onFirstVoice } = {}) {
    this.onSpeechStart = onSpeechStart || (() => {});
    this.onSpeechEnd = onSpeechEnd || (() => {});
    this.onFirstVoice = onFirstVoice || (() => {});
    this.ctx = null; this.analyser = null; this.stream = null;
    this.speaking = false; this.lastSpeech = 0; this.level = 0;
    // natural end-of-turn (850ms pause window avoids mid-sentence splits), after SUSTAINED speech
    this.endOfTurnMs = 850;
    this.threshold = 0.008;  // responsive floor for normal & quiet room speech
    this.minSpeechMs = 250;  // ignore coughs/knocks/clicks shorter than this
    this.snrGate = 2.0;      // speech must beat the room noise by this factor
    this.noiseFloor = null;  // tracked live: fans, AC, hum
    this.gate = 0.008;
    this._above = 0;
    this._below = 0; // consecutive below-gate frames; _above resets after 3
    this.lastError = null;
    this._stopped = false;
    this.muted = false;
    try { window.__micLevel = 0; } catch (e) {}
  }
  setMuted(m) {
    this.muted = !!m;
    if (this.stream) {
      try { this.stream.getAudioTracks().forEach(t => { t.enabled = !this.muted; }); } catch (e) {}
    }
    this.speaking = false;
    this._above = 0;
    this._below = 0;
    this.lastSpeech = 0;
    if (this.muted) {
      this.level = 0;
      try { window.__micLevel = 0; } catch (e) {}
    } else {
      this.noiseFloor = null;
    }
  }
  async start(deviceId) {
    this.lastError = null;
    this._stopped = false;
    try {
      const audio = deviceId ? { deviceId: { ideal: deviceId } } : true;
      this.stream = await navigator.mediaDevices.getUserMedia({ audio });
    } catch (e) {
      this.lastError = (e && e.name) || 'mic-error';
      try { console.warn('[VAD] mic denied:', this.lastError); } catch (err) {}
      return false;
    }
    return this._wire();
  }
  async attach(stream) {
    // Wire analysis to an ALREADY-OPEN stream (always-open capture holds one
    // getUserMedia stream for the session; opening a second one here would
    // fight it for the mic hardware). stop() still closes the shared stream.
    this.lastError = null;
    this._stopped = false;
    if (!stream) {
      this.lastError = 'no-stream';
      return false;
    }
    // Re-attach (mic recovery) must drop the previous context, or each
    // recovery leaks an AudioContext still rendering the dead stream.
    try { this.ctx && this.ctx.close(); } catch (e) {}
    this.ctx = null;
    this.analyser = null;
    this.stream = stream;
    return this._wire();
  }
  async _wire() {
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return false;
      this.ctx = new AC();
      if (this.ctx.state === 'suspended') {
        await this.ctx.resume().catch(() => {});
      }
      const src = this.ctx.createMediaStreamSource(this.stream);
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      src.connect(this.analyser);
    } catch (e) {
      this.lastError = (e && e.name) || 'audio-ctx-error';
      try { console.warn('[VAD] setup failed:', e); } catch (err) {}
      return false;
    }

    const buf = new Float32Array(this.analyser.fftSize);
    const tick = () => {
      if (this._stopped || !this.analyser) return;
      if (this.muted) {
        this.level = 0;
        try { window.__micLevel = 0; } catch (e) {}
        requestAnimationFrame(tick);
        return;
      }
      this.analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length);
      this.level = rms;
      const now = performance.now();
      // adaptive noise floor: drops fast, climbs slowly, so a fan or AC hum
      // raises the bar instead of triggering a turn
      if (this.noiseFloor === null) this.noiseFloor = Math.min(rms, 0.01);
      else this.noiseFloor += (rms - this.noiseFloor) * (rms < this.noiseFloor ? 0.20 : 0.002);
      this.gate = Math.max(this.threshold, this.noiseFloor * this.snrGate);
      // level for the orb/mic dot is signal ABOVE the floor, so wall noise
      // never animates the screen
      try { window.__micLevel = Math.max(0, rms - (this.noiseFloor || 0)); } catch (e) {}
      if (rms > this.gate) {
        this._below = 0;
        if (!this._above) {
          this._above = now;
          // first frame above the floor: lets recorders start before the
          // 250ms confirmation gate, so the utterance onset is never clipped
          try { this.onFirstVoice(); } catch (e) {}
        }
        this.lastSpeech = now;
        if (!this.speaking && now - this._above >= this.minSpeechMs) {
          this.speaking = true; console.log('[VAD] speech-start'); this.onSpeechStart();
        }
      } else {
        // Hangover: a single below-gate frame (plosive gaps like "h-ello",
        // syllable dips) must not reset the onset — only sustained quiet does.
        // Without this, every micro-dip refires onFirstVoice and the capture
        // layer restarts the recorder, discarding the onset over and over.
        this._below++;
        if (this._below >= 3) this._above = 0;
        if (this.speaking && now - this.lastSpeech > this.endOfTurnMs) {
          this.speaking = false; console.log('[VAD] end-of-turn'); this.onSpeechEnd();
        }
      }
      requestAnimationFrame(tick);
    };
    tick();
    return true;
  }
  stop() {
    this._stopped = true;
    try { this.stream && this.stream.getTracks().forEach(t => t.stop()); } catch(e){}
    try { this.ctx && this.ctx.close(); } catch(e){}
    this.analyser = null;
    this.stream = null;
    this.ctx = null;
  }
}
window.EnergyVAD = EnergyVAD;
