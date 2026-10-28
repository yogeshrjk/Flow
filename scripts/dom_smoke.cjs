/* npm ci; start python -m tests.fixture_server; npm run test:dom. Uses simulated media, never a real microphone. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '..');
const base = process.env.FLOW_TEST_URL || 'http://127.0.0.1:8020';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check) {
  for (let i = 0; i < 100; i++) { if (check()) return; await pause(25); }
  throw Error('Timed out waiting for interface state');
}
(async () => {
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(base + '/health')).ok) break; } catch {}
    await pause(25);
  }
  const errors = [], sources = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'app/static/index.html'), 'utf8'), {
    url: base, runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole
  });
  try {
    const w = dom.window;
    const run = code => vm.runInContext(code, dom.getInternalVMContext());
    let cookie = '', systemVoices = 0;
    w.AbortController = AbortController; w.TextDecoder = TextDecoder;
    w.fetch = async (url, options = {}) => {
      const headers = {...options.headers};
      if (cookie) headers.Cookie = cookie;
      const response = await fetch(new URL(url, base), {...options, headers});
      if (response.headers.get('set-cookie')) cookie = response.headers.get('set-cookie').split(';')[0];
      return response;
    };
    w.console = {log(){}, warn(){}, error(){}};
    w.Orb = {set(){}, setStyle(){}}; w.lucide = {createIcons(){}};
    w.Audio = class {
      constructor(src = '') { this.src = src; this.volume = 1; }
      set src(value) { this.value = value; if (value) sources.push(value); }
      play() { setTimeout(() => { this.onplaying?.(); setTimeout(() => this.onended?.(), 10); }, 0); return Promise.resolve(); }
      pause(){} load(){} removeAttribute(){}
    };
    w.speechSynthesis = {speak(){ systemVoices++; throw Error('System voice must never run'); }};
    w.navigator.mediaDevices = {enumerateDevices: async () => []};
    w.URL.createObjectURL = () => 'blob:fixture'; w.URL.revokeObjectURL = () => {};
    w.requestAnimationFrame = () => 0; w.cancelAnimationFrame = () => {};
    w.localStorage.setItem('respMode', 'local');
    for (const file of ['vad.js', 'audio.js', 'app.js', 'learning.js']) {
      run(fs.readFileSync(path.join(root, 'app/static', file), 'utf8'));
    }
    await until(() => w.document.querySelectorAll('#respModes input').length === 3);
    assert.equal(run('respMode'), 'gemini_live');
    assert.equal(w.document.querySelector('#respModes input:checked').value, 'gemini_live');
    assert.doesNotMatch(w.document.querySelector('#respModes').textContent, /Local AI|Demo coach/);
    run('useHttpTransport = true;'); await run('connect()');
    w.document.querySelector('#dailyPlanBtn').click();
    await until(() => w.document.querySelector('#dailyProgress').textContent.includes('B1'));
    run("setLearningMode('listening')");
    await until(() => w.document.querySelectorAll('#listeningAnswers button').length === 3);
    w.document.querySelector('#listeningAnswers button').click();
    await until(() => /Correct|Keep practicing/.test(w.document.querySelector('#learningStatus').textContent));
    run("setLearningMode('free'); running = true;");
    for (const mode of ['groq_fish', 'gemini_fish']) {
      const count = w.document.querySelectorAll('.turn.ai:not(.typing)').length;
      run(`applyResponseMode('${mode}');`);
      assert.equal(run('audioQ.engine'), 'fish');
      assert.match(run('audioQ.fishVoice'), /^[a-f0-9]{32}$/);
      await run(`sendText('I practiced English with my family today in ${mode}.')`);
      await until(() => w.document.querySelectorAll('.turn.ai:not(.typing)').length > count);
      await until(() => !run('audioQ.busy'));
      run("const originalTurn = audioQ._turnId; handleServerMessage({type:'llm_cancelled', turn:'stale'}); if (audioQ.cancelled || audioQ._turnId !== originalTurn) throw Error('Stale cancellation affected the current turn');".replace('const originalTurn', 'var originalTurn'));
    }
    const streams = sources.filter(source => source.startsWith('/api/tts/stream'));
    assert.ok(streams.length >= 2);
    assert.ok(streams.every(source => new URL(source, base).searchParams.get('engine') === 'fish'));
    assert.equal(systemVoices, 0); assert.deepEqual(errors, []);
    console.log('PASS: three modes, Live default, old-setting migration, both Fish UI pipelines, plan/listening, stale events, no system speech');
  } finally { dom.window.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
