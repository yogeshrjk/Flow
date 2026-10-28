/* Local energy VAD (free, no server). Distinguishes short pause vs end-of-turn. */
class EnergyVAD {
  constructor({ onSpeechStart, onSpeechEnd, onPartial, onFirstVoice, onPause } = {}) {
    this.onSpeechStart = onSpeechStart || (() => {});
    this.onSpeechEnd = onSpeechEnd || (() => {});
    this.onFirstVoice = onFirstVoice || (() => {});
    this.onPause = onPause || (() => {});
    this.ctx = null; this.analyser = null; this.stream = null;
    this.speaking = false; this.lastSpeech = 0; this.level = 0;
    // natural end-of-turn (1000ms pause window avoids premature cutoffs between speech clauses while staying responsive)
    this.endOfTurnMs = 850;
    this.threshold = 0.018;  // reliable gate against ambient room noise, background voices, and quiet breathing
    this.minSpeechMs = 240;  // confirms sustained voice (bridges plosives, rejects clicks/snorts)
    this.snrGate = 2.2;      // speech must beat ambient noise by 2.2x
    this.noiseFloor = null;  // tracked live: fans, AC, hum, ambient sounds
    this.gate = 0.018;
    this._above = 0;
    this._below = 0; // consecutive below-gate frames
    this._hangoverFrames = 18; // ~300ms hangover bridges syllable stops and consonant plosives
    this.lastError = null;
    this._stopped = false;
    this.muted = false;
    this.playbackActive = false;
    this.currentSpeechPeak = 0;
    this.noiseFloorAtOnset = null;
    // Energy alone cannot tell a voice from a whistle or a snore. The compact
    // spectral check below admits the broad, mid-frequency shape of speech and
    // rejects narrow pure tones and low-frequency rumble before an utterance
    // recorder is ever confirmed.
    this.speechLike = false;
    this.voiceConfidence = 0;
    // Pause tracking:
    this.minPauseMs = 380;   // minimum silence duration to report as an acoustic pause
    this.lastVoiceEnd = 0;   // timestamp when voice energy dropped below gate
    this.hasSpoken = false;  // only measure pauses after sustained speech has begun
    try { window.__micLevel = 0; } catch (e) {}
  }
  setPlaybackActive(active) {
    this.playbackActive = !!active;
    if (this.playbackActive) {
      this._above = 0;
      this._below = 0;
      this.lastVoiceEnd = 0;
    }
  }
  reset() {
    this.speaking = false;
    this._above = 0;
    this._below = 0;
    this.lastSpeech = 0;
    this.lastVoiceEnd = 0;
    this.hasSpoken = false;
    this.level = 0;
    this.speechLike = false;
    this.voiceConfidence = 0;
    this.currentSpeechPeak = 0;
    this.noiseFloorAtOnset = null;
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
    this.lastVoiceEnd = 0;
    this.hasSpoken = false;
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
      const audio = {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      };
      if (deviceId) {
        audio.deviceId = { ideal: deviceId };
      }
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
    const generation = (this._wireGeneration || 0) + 1;
    this._wireGeneration = generation;
    if (this._raf) cancelAnimationFrame(this._raf);
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return false;
      this.ctx = new AC();
      if (this.ctx.state === 'suspended') {
        await this.ctx.resume().catch(() => {});
      }
      this.srcNode = this.ctx.createMediaStreamSource(this.stream);
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.srcNode.connect(this.analyser);
      // Safari WebKit fix: WebKit halts processing through AnalyserNode if the graph does not
      // terminate at an active destination. Route through a silent gain node (0 gain) so microphone
      // analysis functions continuously without looping into device speakers.
      this.silentGain = this.ctx.createGain();
      this.silentGain.gain.value = 0;
      this.analyser.connect(this.silentGain);
      this.silentGain.connect(this.ctx.destination);
    } catch (e) {
      this.lastError = (e && e.name) || 'audio-ctx-error';
      try { console.warn('[VAD] setup failed:', e); } catch (err) {}
      return false;
    }

    const buf = new Float32Array(this.analyser.fftSize);
    const freq = new Uint8Array(this.analyser.frequencyBinCount);
    const tick = () => {
      if (this._stopped || !this.analyser || generation !== this._wireGeneration) return;
      if (this.muted) {
        this.level = 0;
        try { window.__micLevel = 0; } catch (e) {}
        this._raf = requestAnimationFrame(tick);
        return;
      }
      this.analyser.getFloatTimeDomainData(buf);
      this.analyser.getByteFrequencyData(freq);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length);
      this.level = rms;
      const now = performance.now();
      // adaptive noise floor: drops fast on quiet, climbs steadily on ambient background
      // so a fan, AC hum, or distant murmurs raise the gate dynamically
      if (this.noiseFloor === null) this.noiseFloor = Math.max(0.004, Math.min(rms, 0.030));
      else if (!this.playbackActive && !this.speaking && !this._above) this.noiseFloor += (rms - this.noiseFloor) * (rms < this.noiseFloor ? 0.20 : 0.008);
      // Keep listening while the assistant is audible. Raise the gate and require
      // sustained speech so speaker output never causes false self-interruption.
      const effectiveThreshold = this.playbackActive ? Math.max(this.threshold * 2.5, 0.028) : this.threshold;
      const effectiveSnr = this.playbackActive ? (this.snrGate * 1.5) : this.snrGate;
      this.gate = Math.max(effectiveThreshold, (this.noiseFloor || 0.006) * effectiveSnr);
      this.speechLike = this._hasSpeechShape(freq);
      // level for the orb/mic dot is signal ABOVE the floor, so wall noise
      // never animates the screen
      try { window.__micLevel = Math.max(0, rms - (this.noiseFloor || 0)); } catch (e) {}
      if (rms > this.gate && this.speechLike) {
        this._below = 0;
        if (!this._above) {
          this._above = now;
          this.noiseFloorAtOnset = this.noiseFloor;
          this.currentSpeechPeak = rms;
          // first frame above the floor: lets recorders start before the
          // confirmation gate, so the utterance onset is never clipped
          try { this.onFirstVoice(); } catch (e) {}
        } else {
          if (rms > this.currentSpeechPeak) this.currentSpeechPeak = rms;
        }

        // If we had confirmed speech and then paused, check if silence is an acoustic pause
        if (this.hasSpoken && this.lastVoiceEnd > 0) {
          const pauseDur = now - this.lastVoiceEnd;
          if (pauseDur >= this.minPauseMs) {
            try {
              this.onPause({
                start: this.lastVoiceEnd,
                end: now,
                duration: pauseDur,
              });
            } catch (e) {}
          }
          this.lastVoiceEnd = 0;
        }

        this.lastSpeech = now;
        const reqSpeechMs = this.playbackActive ? 360 : this.minSpeechMs;
        if (!this.speaking && now - this._above >= reqSpeechMs) {
          this.speaking = true;
          this.hasSpoken = true;
          console.log('[VAD] speech-start');
          this.onSpeechStart();
        }
      } else {
        // Hangover: plosive consonant gaps ("p", "t", "k") or brief syllable dips
        // must not reset the onset — only sustained quiet does.
        this._below++;
        if (this._below >= (this._hangoverFrames || 18)) {
          this._above = 0;
          if (!this.speaking) {
            this.currentSpeechPeak = 0;
          }
        }

        // Record the moment voice dropped below gate if we were actively speaking
        if (this.hasSpoken && this.lastSpeech > 0 && !this.lastVoiceEnd) {
          this.lastVoiceEnd = this.lastSpeech;
        }

        if (this.speaking && now - this.lastSpeech > this.endOfTurnMs) {
          this.speaking = false;
          console.log('[VAD] end-of-turn');
          this.onSpeechEnd();
        }
      }
      this._raf = requestAnimationFrame(tick);
    };
    tick();
    return true;
  }
  _hasSpeechShape(freq) {
    // Speech normally spreads energy across roughly 220–3400 Hz. A whistle is
    // a single sharp spectral peak, while snoring/handling rumble stays mostly
    // below 200 Hz.
    try {
      const hzPerBin = (this.ctx && this.ctx.sampleRate ? this.ctx.sampleRate : 48000) / this.analyser.fftSize;
      let total = 0, voice = 0, low = 0, peak = 0;
      for (let i = 1; i < freq.length; i++) {
        const energy = freq[i] * freq[i];
        if (!energy) continue;
        const hz = i * hzPerBin;
        total += energy;
        if (hz >= 220 && hz <= 3400) voice += energy;
        if (hz < 200) low += energy;
        if (energy > peak) peak = energy;
      }
      if (total < 120) { this.voiceConfidence = 0; return false; }
      const voiceShare = voice / total;
      const lowShare = low / total;
      const peakShare = peak / total;
      // Store a useful diagnostic/utterance confidence without exposing it in
      // the UI. A single-bin tone gets near zero; voiced speech trends high.
      this.voiceConfidence = Math.max(0, Math.min(1, voiceShare * (1 - peakShare)));
      return voiceShare >= 0.32 && lowShare <= 0.60 && peakShare <= 0.38;
    } catch (e) {
      // If a browser omits frequency data, retain the existing energy VAD
      // rather than making its microphone unusable.
      this.voiceConfidence = 1;
      return true;
    }
  }
  stop() {
    this._wireGeneration = (this._wireGeneration || 0) + 1;
    if (this._raf) cancelAnimationFrame(this._raf);
    this._stopped = true;
    this.lastVoiceEnd = 0;
    this.hasSpoken = false;
    try { this.stream && this.stream.getTracks().forEach(t => t.stop()); } catch(e){}
    try { this.srcNode && this.srcNode.disconnect(); } catch(e){}
    try { this.analyser && this.analyser.disconnect(); } catch(e){}
    try { this.silentGain && this.silentGain.disconnect(); } catch(e){}
    try { this.ctx && this.ctx.close(); } catch(e){}
    this.srcNode = null;
    this.silentGain = null;
    this.analyser = null;
    this.stream = null;
    this.ctx = null;
  }
}
window.EnergyVAD = EnergyVAD;
