/* Run with: node --test scripts/test_audio.cjs. No dependencies or cloud keys. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
function setup() {
  const context = {console: {log(){},warn(){}}, performance, setTimeout,clearTimeout,AbortController,
    URL: {createObjectURL(){throw Error('stale blob became a URL')},revokeObjectURL(){}},
    Audio: class {pause(){} load(){} removeAttribute(){}}, window: {}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/audio.js'),'utf8'),context);
  return {context, q: new context.window.AudioQueue()};
}
const tick = () => new Promise(resolve=>setImmediate(resolve));
const deferred = () => {let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};

test('streamed first phrase is requested once without prefetch',async()=>{
  const {q}=setup();let fetches=0,streams=0;
  q._fetchOne=async()=>{fetches++;return {err:'unexpected'}};
  q._playStream=async()=>{streams++;return {ok:true}};
  q.startTurn('one');q.enqueue('A complete streamed sentence.',{stream:true,turn:'one'});
  await tick();assert.equal(streams,1);assert.equal(fetches,0);assert.equal(q.busy,false);
});

test('a cancelled old pump cannot reset playback in a new turn',async()=>{
  const {q}=setup();const old=deferred(),next=deferred();
  q._playStream=text=>{q.playing=true;return text==='old'?old.promise:next.promise};
  q.startTurn('old');q.enqueue('old',{stream:true,turn:'old'});q.cancel('interrupt');
  q.startTurn('new');q.enqueue('new',{stream:true,turn:'new'});old.resolve({ok:true});await tick();
  assert.equal(q.playing,true);assert.equal(q._turnId,'new');
  next.resolve({ok:true});await tick();assert.equal(q.busy,false);
});

test('a partially audible failed phrase is not spoken a second time',async()=>{
  const {q}=setup();let requests=0;
  q._playStream=async()=>({ok:false,started:true,reason:'network'});
  q._fetchOne=async()=>{requests++;return {err:"provider unavailable"}};
  q.startTurn('partial');q.enqueue('Partial reply.',{stream:true,turn:'partial'});await tick();
  assert.equal(requests,0);assert.equal(q.busy,false);
});

test('voice failure retries Fish once and reports failure without a system voice',async()=>{
  const {q}=setup();let requests=0;
  q._playStream=async()=>({ok:false,started:false,reason:'429'});
  q._fetchOne=async()=>{requests++;return {err:"provider unavailable"}};
  q.startTurn('fallback');q.enqueue('A useful reply.',{stream:true,turn:'fallback'});await tick();
  assert.equal(requests,1);
});

test('unduck preserves speaker mute',()=>{
  const {q}=setup();let gain=1;q.muted=true;q._ctx={currentTime:0};q._pcmGain={gain:{setValueAtTime(v){gain=v}}};
  q.unduck();assert.equal(gain,0);
});

test('cancel during blob download never creates an orphan URL',async()=>{
  const {q,context}=setup();const body=deferred();
  context.fetch=async()=>({ok:true,headers:{get:()=> 'audio/mpeg'},blob:()=>body.promise});
  const job=q._fetchOne('late');await tick();q.cancel('stop');body.resolve({});
  const result=await job;assert.equal(result.url,null);assert.equal(q._aborts.size,0);
});

test('cancel settles an active Fish element and clears its watchdog',async()=>{
  const {q}=setup();const audio={pause(){},load(){},removeAttribute(){},play(){return Promise.resolve()}};
  q.startTurn('speech');const job=q.playUrl('voice.mp3','Hello world.',null,'speech','',audio);
  q.cancel('stop');assert.equal((await job).reason,'cancelled');assert.equal(q.busy,false);
});

test('runtime has no browser voice synthesis path',()=>{
  const {q}=setup();assert.equal(q.playBrowser,undefined);
  const source=fs.readFileSync(path.join(__dirname,'../app/static/audio.js'),'utf8');
  assert.doesNotMatch(source,/speechSynthesis|SpeechSynthesisUtterance/);
});
