// Browser speech doubles + virtual clock: no audio, network, or paid service.
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = readFileSync('viewer/index.html', 'utf8');
const source = html.match(/<script id="voice-controller">([\s\S]*?)<\/script>/)[1];
const FINISH = 900, MID_PAUSE = 600, BRAIN_WAIT = 8000;
function setup(search = '', supported = true) {
  let now = 0, next = 0, cancels = 0;
  const timers = new Map(), elements = new Map(), speech = [], recognizers = [], sent = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, {textContent: id === 'answer' ? 'Ready when you are.' : '', setAttribute() {}});
    return elements.get(id);
  };
  let click;
  class Recognition {
    constructor() { recognizers.push(this); }
    start() {}
    abort() { this.onend?.(); }
    result(parts) { this.onresult({resultIndex: 0, results: parts.map(([text, final]) => Object.assign([{transcript: text}], {isFinal: final}))}); }
  }
  const synth = {getVoices: () => [{name: 'Samantha', lang: 'en-US'}, {name: 'Serena', lang: 'en-GB'}, {name: 'Daniel (English (UK))', lang: 'en-GB', voiceURI: 'com.apple.voice.enhanced.en-GB.Daniel'}], cancel() {cancels++;}, speak() {throw Error('System synthesis must never be used');}};
  const context = {URLSearchParams, location: {search}, SpeechSynthesisUtterance: function(text) {this.text = text;},
    document: {getElementById: el, addEventListener(_, fn) {click = fn;}},
    setTimeout(fn, ms) {timers.set(++next, {fn, at: now + ms}); return next;},
    clearTimeout(id) {timers.delete(id);}, window: {JarvisOrb:{nativeAudio(){},updateVoice(){},unlockSpeech(){},warmSpeech(){},stopSpeech(){cancels++;},playSpeech(text,options){speech.push({text,onend:options.onEnd});options.onStart?.();return true;}},speechSynthesis: synth, webkitSpeechRecognition: supported ? Recognition : undefined}};
  vm.runInNewContext(source, context);
  const voice = context.window.JarvisVoice;
  voice.setSubmit(q => {sent.push(q); voice.setThinking(true);});
  function tick(ms) {now += ms; for (const [id, t] of [...timers]) if (t.at <= now) {timers.delete(id); t.fn();}}
  return {context, voice, speech, sent, el, tick, click: () => click({isTrusted: true}), mic: () => el('mic').onclick(), current: () => recognizers.at(-1), cancels: () => cancels};
}
const a = setup();
assert.equal(a.speech.length, 0);
a.click(); assert.equal(a.speech.length, 1); assert.equal(a.speech[0].text, 'Ready when you are.');
a.click(); assert.equal(a.speech.length, 1);
a.speech[0].onend(); a.mic();
assert.equal(a.el('voice-status').textContent, 'Listening…');
a.current().result([['Tell me about', true]]); a.tick(MID_PAUSE); assert.deepEqual(a.sent, []);
a.current().onspeechstart(); a.tick(FINISH); assert.deepEqual(a.sent, []);
a.current().result([['Tell me about', true], ['the quarterly', false]]); a.tick(FINISH); assert.deepEqual(a.sent, []);
a.current().result([['Tell me about', true], ['the quarterly goals', true]]);
a.tick(FINISH - 1); assert.deepEqual(a.sent, []); a.tick(1);
assert.deepEqual(a.sent, ['Tell me about the quarterly goals']);
assert.equal(a.el('voice-status').textContent, 'Thinking…');
const stale = a.current(); stale.result([['Unwanted question', true]]); a.tick(BRAIN_WAIT);
assert.equal(a.el('voice-status').textContent, 'Thinking…'); assert.equal(a.sent.length, 1);
a.voice.stopSpeech(); a.voice.setThinking(false); a.voice.speak('A long answer');
assert.equal(a.el('voice-status').textContent, 'Speaking…');
const count = a.cancels(); a.current().result([['stop', false]]);
assert.ok(a.cancels() > count); assert.equal(a.el('voice-status').textContent, 'Listening…');
a.tick(FINISH); assert.equal(a.sent.length, 1);
a.voice.speak('Another answer'); a.current().result([['wait', true]]);
assert.equal(a.el('voice-status').textContent, 'Listening…');
a.current().result([['Across', true]]); a.tick(MID_PAUSE); a.current().onend();
a.current().result([['sessions', true]]); a.tick(FINISH);
assert.equal(a.sent.at(-1), 'Across sessions');
const capture = setup(); capture.mic();
capture.current().result([['remember that the finish window', true]]); capture.tick(MID_PAUSE);
capture.current().result([['remember that the finish window', true], ['should be 900 milliseconds', false]]); capture.tick(FINISH);
assert.equal(capture.sent.length, 0);
capture.current().result([['remember that the finish window', true], ['should be 900 milliseconds', true]]); capture.tick(FINISH);
assert.deepEqual(capture.sent, ['remember that the finish window should be 900 milliseconds']);
const oneAnswer = setup();
const answers = [];
oneAnswer.voice.answerOnce(text => answers.push(text));
oneAnswer.voice.setExternalSpeaking(true);
assert.equal(oneAnswer.current(), undefined);
oneAnswer.voice.setExternalSpeaking(false);
assert.equal(oneAnswer.current(), undefined); // An organ cannot open the microphone.
oneAnswer.mic();
const answerRecognition = oneAnswer.current();
answerRecognition.result([['Finish the quarterly budget', true]]); oneAnswer.tick(FINISH);
assert.deepEqual(answers, ['Finish the quarterly budget']);
assert.deepEqual(oneAnswer.sent, []);
assert.equal(oneAnswer.el('mic').textContent, 'Mic on');
answerRecognition.result([['Do not submit twice', true]]); oneAnswer.tick(FINISH);
assert.equal(answers.length, 1);
const muted = setup('?mute=1'); muted.click(); muted.mic(); muted.voice.speak('Never audible');
assert.equal(muted.speech.length, 0);
muted.current().result([['Still works', true]]); muted.tick(FINISH); assert.deepEqual(muted.sent, ['Still works']);
const missingVoice = setup();
missingVoice.context.window.speechSynthesis.getVoices = () => [{name: 'Samantha', lang: 'en-US'}];
missingVoice.click();
assert.equal(missingVoice.speech.length, 1, 'Local voice does not depend on a system voice');
const denied = setup(); denied.mic(); denied.current().onerror({error: 'not-allowed'});
assert.match(denied.el('voice-status').textContent, /permission denied/);
const unsupported = setup('', false); assert.equal(unsupported.el('mic').disabled, false);
console.log('VOICE PASS: activation, reference-conditioned local voice, 900ms buffering, interim speech, session restart, 8s thinking, busy mic, immediate interrupts, mute, permission and unsupported states.');

(async () => {
  const loadingVoice = setup();
  delete loadingVoice.context.window.speechSynthesis;
  loadingVoice.click();
  assert.equal(loadingVoice.speech.length, 1, 'Local audio works without speechSynthesis');

  const native = setup('?mute=1');
  const calls=[];
  native.context.fetch=async(path)=>{calls.push(path);return {ok:true,json:async()=>path.endsWith('/start')?{token:'test',status:'starting'}:path.endsWith('/stop')?{status:'stopped'}:{status:'listening',sequence:1,text:'A native sentence'}}};
  native.mic();native.current().onerror({error:'network'});
  for(let i=0;i<12;i++)await Promise.resolve();
  assert.ok(calls.includes('/voice/start'));
  assert.ok(calls.includes('/voice/poll'));
  native.tick(FINISH);
  assert.deepEqual(native.sent,['A native sentence']);
  assert.ok(calls.includes('/voice/stop'));
  const selectedInput=setup('?mute=1'),inputRequests=[];
  selectedInput.context.fetch=async(path,options)=>{inputRequests.push({path,body:JSON.parse(options.body)});return {ok:true,json:async()=>path.endsWith('/start')?{token:'selected',status:'starting'}:path.endsWith('/stop')?{status:'stopped'}:{status:'listening',sequence:1,text:''}};};
  selectedInput.voice.setInputDevice('native-input-uid');assert.equal(selectedInput.voice.isEnabled(),false);
  selectedInput.mic();for(let i=0;i<12;i++)await Promise.resolve();
  assert.equal(inputRequests.find(request=>request.path==='/voice/start').body.device_id,'native-input-uid');
  assert.equal(selectedInput.voice.inputDevice(),'native-input-uid');selectedInput.mic();
  const echo = setup();
  let heard='',seq=0;
  echo.context.fetch=async(path)=>({ok:true,json:async()=>path.endsWith('/start')?{token:'echo',status:'starting'}:path.endsWith('/stop')?{status:'stopped'}:{status:'listening',sequence:++seq,text:heard}});
  echo.click();echo.speech[0].onend();echo.mic();echo.current().onerror({error:'network'});
  for(let i=0;i<12;i++)await Promise.resolve();
  echo.voice.speak('The answer is ten pounds.');
  heard='The answer is ten pounds';echo.tick(150);
  for(let i=0;i<12;i++)await Promise.resolve();
  echo.tick(FINISH);assert.deepEqual(echo.sent,[]);
  heard='';echo.speech.at(-1).onend();
  for(let i=0;i<12;i++)await Promise.resolve();
  heard='My next question';echo.tick(150);
  for(let i=0;i<12;i++)await Promise.resolve();
  echo.tick(FINISH);assert.deepEqual(echo.sent,['My next question']);
  const external = setup('?mute=1');
  external.mic(); const oldRecognition = external.current();
  external.voice.setExternalSpeaking(true);
  oldRecognition.result([['Back to work sir', true]]); external.tick(FINISH);
  assert.deepEqual(external.sent, []);
  external.voice.setExternalSpeaking(false);
  external.current().result([['pause', true]]); external.tick(FINISH);
  assert.deepEqual(external.sent, ['pause']);
  const relief = setup('?mute=1');
  let reliefCount=0;
  relief.context.window.JarvisEyes={isRelief:t=>t==='give me a minute',matches:()=>false,isCommand:()=>false,relief:()=>{reliefCount++;}};
  relief.voice.setExternalSpeaking(true);
  assert.equal(relief.current(),undefined); // A callout never enables the mic.
  relief.mic();
  relief.current().result([['Back to work sir',true]]);
  assert.deepEqual(relief.sent,[]);
  relief.current().result([['give me',true],['a minute',true]]);
  assert.equal(reliefCount,1);assert.deepEqual(relief.sent,[]);
  relief.mic(); relief.voice.setExternalSpeaking(false);
  assert.equal(relief.voice.isEnabled(),false);
  const app = setup('?mute=1');
  let resolveReply, request;
  const response = new Promise(resolve => { resolveReply = resolve; });
  for (const id of ['send', 'question', 'suggestions', 'sources', 'answer-meta', 'answer-label', 'answer']) {
    Object.assign(app.el(id), {value: '', classList: {add() {}, remove() {}}, replaceChildren() {}, focus() {}});
  }
  Object.assign(app.context, {console, AbortController, fetch: (path, options) => {request = {path, options}; return response;}});
  vm.runInNewContext("const $ = id => document.getElementById(id); let screenMode = false, screenGeneration = 0; let busy = false, highlighted, activeLinks, selected; const GRAPH = {nodes: [], links: []}; const SETTINGS = {chatTimeoutMs: 100000, timePrecision: 2}; function isModelCommand() { return false; } function holdCamera() {} function showAnswerSources() {} function linkKey() {} function refreshHighlights() {}", app.context);
  vm.runInNewContext(html.match(/async function ask\(question[\s\S]*?(?=\n<\/script>)/)[0], app.context);
  vm.runInNewContext(html.match(/const CAPTURE_TRIGGER = .*;/)[0], app.context);
  const pendingReply = app.context.ask('A typed question');
  assert.equal(request.path, '/chat');
  assert.equal(JSON.parse(request.options.body).question, 'A typed question');
  assert.equal(JSON.parse(request.options.body).voice, undefined);
  app.tick(BRAIN_WAIT);
  assert.equal(app.el('voice-status').textContent, 'Thinking…');
  assert.equal(app.el('mic').disabled, false);
  resolveReply({ok: true, json: async () => ({answer: 'A real-shaped answer', nodes: [], elapsed_s: 8})});
  await pendingReply;
  assert.equal(app.el('answer').textContent, 'A real-shaped answer');
  assert.equal(app.el('voice-status').textContent, 'Ready');
  assert.equal(app.el('mic').disabled, false);
  assert.equal(app.speech.length, 0);
  await app.context.ask('A spoken question', {voice: true});
  assert.equal(JSON.parse(request.options.body).voice, true);
  console.log('CHAT PASS: shared /chat request, full delayed-response thinking, response display, mic recovery, mute.');
})().catch(error => { console.error(error); process.exitCode = 1; });

const barge = setup(); barge.click(); barge.speech[0].onend(); barge.mic();
barge.voice.speak('Long reply'); const before=barge.cancels();
barge.current().onspeechstart(); assert.ok(barge.cancels()>before);
barge.voice.speak('Unwanted posture nudge'); assert.equal(barge.speech.length,2);
barge.current().result([['Actually listen to me',true]]); barge.tick(FINISH);
assert.deepEqual(barge.sent,['Actually listen to me']);
barge.voice.quietFor(180000); barge.voice.speak('Late response'); assert.equal(barge.speech.length,2);

const thinkingRelief=setup('?mute=1'); let pauses=0;
thinkingRelief.context.window.JarvisEyes={isRelief:t=>t==='gimme 3 minutes',relief:()=>{pauses++;},matches:()=>false,isCommand:()=>false};
thinkingRelief.mic(); thinkingRelief.voice.setThinking(true);
thinkingRelief.current().result([['gimme 3 minutes',true]]);assert.equal(pauses,1);
assert.equal(thinkingRelief.voice.isEnabled(),true);thinkingRelief.mic();assert.equal(thinkingRelief.voice.isEnabled(),false);

const continued=setup();continued.click();continued.speech[0].onend();continued.mic();
continued.voice.setThinking(true);continued.current().result([['I have more',false]]);
continued.voice.setThinking(false);continued.voice.speak('Do not interrupt this sentence');
assert.equal(continued.speech.length,1);
continued.current().result([['I have more to add',true]]);continued.tick(FINISH);
assert.deepEqual(continued.sent,['I have more to add']);

const wake=setup('?mute=1');
assert.equal(wake.voice.wakeWordEnabled(),false);
wake.voice.setWakeWord(true);assert.equal(wake.current(),undefined); // A preference never opens a microphone.
wake.mic();assert.match(wake.el('voice-status').textContent,/Awaiting/);
wake.current().result([['An unrelated conversation',true]]);wake.tick(FINISH);assert.deepEqual(wake.sent,[]);
wake.current().result([['Jarvison is a different word',true]]);wake.tick(FINISH);assert.deepEqual(wake.sent,[]);
wake.current().result([['Hey Jarvis, what are the quarterly goals',true]]);wake.tick(FINISH);
assert.deepEqual(wake.sent,['what are the quarterly goals']);

const wakeFragments=setup('?mute=1');wakeFragments.voice.setWakeWord(true);wakeFragments.mic();
wakeFragments.current().result([['Jarvis',true]]);wakeFragments.tick(MID_PAUSE);assert.deepEqual(wakeFragments.sent,[]);
wakeFragments.current().result([['Jarvis',true],['remember that the',false]]);wakeFragments.tick(FINISH);assert.deepEqual(wakeFragments.sent,[]);
wakeFragments.current().result([['Jarvis',true],['remember that the budget is approved',true]]);wakeFragments.tick(FINISH);
assert.deepEqual(wakeFragments.sent,['remember that the budget is approved']);

const nativeWake=setup('?mute=1');nativeWake.voice.setWakeWord(true);nativeWake.mic();
nativeWake.current().onresult({native:true,text:'Unrelated prior words. Jarvis show my captures'});nativeWake.tick(FINISH);
assert.deepEqual(nativeWake.sent,['show my captures']);

const wakeIntent=setup('?mute=1'),wakeAnswers=[];wakeIntent.voice.setWakeWord(true);
wakeIntent.voice.answerOnce(text=>wakeAnswers.push(text));wakeIntent.mic();
wakeIntent.current().result([['Finish the budget',true]]);wakeIntent.voice.finishHold();
assert.deepEqual(wakeAnswers,['Finish the budget']);assert.deepEqual(wakeIntent.sent,[]);

const wakeHold=setup('?mute=1');wakeHold.voice.setWakeWord(true);wakeHold.voice.beginHold();wakeHold.mic();
wakeHold.current().result([['Tell me about sales',false]]);wakeHold.voice.finishHold();
assert.deepEqual(wakeHold.sent,['Tell me about sales']);assert.equal(wakeHold.voice.isEnabled(),false);

const wakeTimeout=setup('?mute=1');wakeTimeout.voice.setWakeWord(true);wakeTimeout.mic();
wakeTimeout.current().result([['Jarvis',true]]);wakeTimeout.tick(8001);
wakeTimeout.current().result([['Still unrelated',true]]);wakeTimeout.tick(FINISH);assert.deepEqual(wakeTimeout.sent,[]);
assert.match(wakeTimeout.el('voice-status').textContent,/Awaiting/);

const wakeRelief=setup('?mute=1');let wakeReliefs=0;
wakeRelief.context.window.JarvisEyes={isRelief:text=>text==='gimme 3 minutes',relief:()=>{wakeReliefs++;}};
wakeRelief.voice.setWakeWord(true);wakeRelief.mic();
wakeRelief.current().onresult({native:true,text:'Earlier ambient words Jarvis gimme 3 minutes'});
assert.equal(wakeReliefs,1);assert.deepEqual(wakeRelief.sent,[]);

const wakeStop=setup();wakeStop.click();wakeStop.speech[0].onend();wakeStop.voice.setWakeWord(true);wakeStop.mic();
wakeStop.voice.speak('A spoken response');const wakeCancels=wakeStop.cancels();
wakeStop.current().result([['stop',false]]);assert.ok(wakeStop.cancels()>wakeCancels);wakeStop.tick(FINISH);assert.deepEqual(wakeStop.sent,[]);
console.log('WAKE PASS: explicit-mic-only preference, ambient rejection, exact phrase, stripped/cumulative/interim commands, timeout, stop, hold-Space and local camera commands.');
