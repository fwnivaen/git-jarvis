// Exercise the production controller without opening a microphone or playing audio.
const {readFileSync}=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const SOURCE=readFileSync('viewer/orb.js','utf8'),SIZE=280,PREVIEW_MS=6000,STALE_MS=601;
function setup(){
  let now=1000,next=0,microphones=0,canceled=0,disconnected=0;
  const elements=new Map(),timers=new Map(),frames=new Map();
  function element(name){
    if(!elements.has(name))elements.set(name,{name,textContent:'',dataset:{},attributes:{},events:{},style:{setProperty(){}},
      classList:{add(){}},clientWidth:SIZE,setAttribute(key,value){this.attributes[key]=value;},
      addEventListener(key,handler){this.events[key]=handler;},getContext(){return new Proxy({},{get:()=>()=>{}});},
      getBoundingClientRect(){return {left:0,top:0,width:SIZE,height:SIZE};},click(){microphones++;}});
    return elements.get(name);
  }
  const host=element('host'),buttons=['idle','listening','thinking','speaking','confirm','offline','wake','live'].map(mode=>{
    const button=element(mode);button.dataset.orbPreview=mode;return button;
  });
  host.querySelector=element;host.querySelectorAll=()=>buttons;
  const media={matches:false,addEventListener(){},removeEventListener(){}};
  const context={console,URLSearchParams,Float32Array,Uint8Array,AbortController,Option:function(){},
    location:{search:'?mute=1'},devicePixelRatio:1,performance:{now:()=>now},Date:{now:()=>now},matchMedia:()=>media,
    sessionStorage:{getItem:()=>true},getComputedStyle:()=>({getPropertyValue:()=> '#5eead4'}),
    navigator:{mediaDevices:{getUserMedia(){microphones++;throw Error('unexpected microphone');}}},
    document:{hidden:false,documentElement:{dataset:{}},querySelector:()=>null,getElementById:name=>name==='jarvis-orb-host'?host:name==='mic'?element('mic'):null},
    window:{addEventListener(){},createLocalSpeechPlayer:()=>({cancel(){},unlock(){},warm(){}})},ResizeObserver:class{observe(){}disconnect(){disconnected++;}},
    MutationObserver:class{observe(){}disconnect(){disconnected++;}},
    requestAnimationFrame(fn){frames.set(++next,fn);return next;},cancelAnimationFrame(id){frames.delete(id);canceled++;},
    setTimeout(fn,ms){timers.set(++next,{fn,ms});return next;},clearTimeout(id){timers.delete(id);}};
  vm.runInNewContext(SOURCE,context);
  return {api:context.window.JarvisOrb,element,host,buttons,media,context,
    preview(mode){element('.orb-preview-controls').events.click({target:{closest:()=>element(mode)}});},
    expire(){const timer=[...timers.values()].find(timer=>timer.ms===PREVIEW_MS);assert.ok(timer);timer.fn();},
    advance(ms){now+=ms;},frame(){const [id,fn]=frames.entries().next().value;frames.delete(id);now+=200;fn(now);},
    counts:()=>({microphones,canceled,disconnected,frames:frames.size})};
}
const app=setup(),orb=app.api;
assert.equal(orb.snapshot().state,'IDLE');assert.equal(orb.snapshot().visualMode,'idle');
for(const [state,mode] of [['LISTENING','listening'],['THINKING','thinking'],['SPEAKING','speaking'],['CONFIRM','confirm'],['OFFLINE','offline'],['ERROR','error'],['AWAITING_APPROVAL','confirm'],['EXECUTING','thinking']]){
  orb.setStatus(state);assert.equal(orb.snapshot().visualMode,mode,state);
}
orb.setStatus(null);app.preview('speaking');
assert.equal(orb.snapshot().state,'IDLE');assert.equal(orb.snapshot().visualMode,'speaking');
assert.equal(orb.snapshot().micEnabled,false);assert.equal(orb.snapshot().spectrum,false);
assert.match(app.element('.orb-state').textContent,/PREVIEW/);assert.equal(app.counts().microphones,0);
app.expire();assert.equal(orb.snapshot().preview,false);
app.preview('confirm');orb.updateVoice({enabled:true,backend:'native'});
assert.equal(orb.snapshot().preview,false,'live activity interrupts a preview');assert.equal(orb.snapshot().state,'LISTENING');
assert.equal(app.element('thinking').disabled,true,'previews cannot replace live voice/approval activity');
orb.nativeAudio({bands:Array(24).fill(.5),rms:.2});assert.equal(orb.snapshot().spectrum,true);
app.advance(STALE_MS);assert.equal(orb.snapshot().spectrum,false,'old microphone samples cannot drive live audio');
orb.updateVoice({thinking:true});assert.equal(orb.snapshot().state,'THINKING');
orb.updateVoice({thinking:false,speaking:true});assert.equal(orb.snapshot().state,'SPEAKING');
orb.setStatus('AWAITING APPROVAL',{target:'#approval-card'});orb.setConnected(false);
assert.equal(orb.snapshot().state,'OFFLINE','connection loss overrides stale approval/voice state');
orb.setConnected(true);assert.equal(orb.snapshot().state,'AWAITING APPROVAL');
orb.setStatus(null);orb.updateVoice({enabled:false,speaking:false,notice:'permission denied'});
assert.equal(orb.snapshot().state,'ERROR');orb.updateVoice({notice:''});assert.equal(orb.snapshot().state,'IDLE');
orb.setStatus('INVALID');assert.equal(orb.snapshot().state,'IDLE');
app.media.matches=true;app.frame();orb.wake();app.frame();
orb.destroy();assert.equal(app.counts().frames,0);assert.equal(app.counts().canceled,1);assert.equal(app.counts().disconnected,2);
assert.equal(app.counts().microphones,0,'visual previews never request audio permissions');
console.log('ORB PASS: six reference modes, live voice/approval mapping, measured audio freshness, isolated expiring previews, offline priority, reduced motion and teardown.');
