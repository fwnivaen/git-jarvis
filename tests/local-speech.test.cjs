'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createLocalSpeechPlayer}=require('../viewer/local-speech.js');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const id='a'.repeat(32);

function fixture(handler) {
  const calls=[], events=[], elements=[];
  class Audio {
    constructor(){elements.push(this);this.src='';this.paused=true;}
    play(){this.paused=false;this.onplaying?.();return Promise.resolve();}
    pause(){this.paused=true;}
    removeAttribute(){this.src='';}
    load(){}
  }
  const env={Audio,AbortController,setTimeout,clearTimeout,crypto:{randomUUID:()=>id},
    fetch:async(path,options={})=>{
      calls.push({path,options});
      if(path==='/speech/cancel')return {ok:true,json:async()=>({cancelled:true})};
      return handler(path,options);
    }};
  const player=createLocalSpeechPlayer(env);
  const callbacks={onstart:()=>events.push('start'),onend:()=>events.push('end'),onerror:()=>events.push('error')};
  return {env,player,calls,events,elements,callbacks};
}
const ready={id,state:'ready',audio_url:'/speech/audio/'+id+'.wav'};
const response=value=>({ok:true,json:async()=>value});

test('ready local audio plays once and reports actual playback',async()=>{
  const f=fixture(()=>response(ready));
  f.player.start('A fresh reply.',f.callbacks);await flush();
  assert.deepEqual(f.events,['start']);
  assert.equal(f.elements[0].src,ready.audio_url);
  f.elements[0].onended();assert.deepEqual(f.events,['start','end']);f.player.dispose();
});
test('stop before a delayed POST receipt sends cancellation and ignores its audio',async()=>{
  let deliver;
  const f=fixture(()=>new Promise(resolve=>{deliver=resolve;}));
  f.player.start('A stopped reply.',f.callbacks);f.player.cancel();
  deliver(response(ready));await flush();
  assert.deepEqual(f.events,[]);
  assert.equal(f.calls.filter(x=>x.path==='/speech/cancel').length,1);
  assert.equal(JSON.parse(f.calls[1].options.body).request_id,id);
});
test('failure leaves an error and cannot fall back to a system or paid voice',async()=>{
  const f=fixture(()=>({ok:false}));
  f.player.start('A reply.',f.callbacks);await flush();
  assert.deepEqual(f.events,['error']);
  assert.equal(f.elements[0].src,'');f.player.dispose();
});
test('a receipt cannot redirect playback to another origin or a local private file',async()=>{
  const f=fixture(()=>response({...ready,audio_url:'https://outside.test/audio.wav'}));
  f.player.start('A reply.',f.callbacks);await flush();
  assert.deepEqual(f.events,['error']);assert.equal(f.elements[0].src,'');f.player.dispose();
});
test('late events from an older reply do not finish its replacement',async()=>{
  const f=fixture(()=>response(ready));
  f.player.start('First.',f.callbacks);await flush();
  const oldEnd=f.elements[0].onended;
  f.player.start('Second.',f.callbacks);await flush();
  oldEnd();assert.deepEqual(f.events,['start','start']);
  f.elements[0].onended();assert.deepEqual(f.events,['start','start','end']);
});
function streamFixture(packets) {
  const started=[], stopped=[];
  class Context {
    constructor(){this.currentTime=0;this.destination={};}
    resume(){return Promise.resolve();}
    decodeAudioData(){return Promise.resolve({duration:1});}
    createBufferSource(){const s={connect(){},start(at){started.push(at);s.at=at;},stop(){stopped.push(s.at);}};return s;}
  }
  const queue=[...packets];
  const f=fixture(path=>{
    if(/\/\d+\.wav$/.test(path)||path.endsWith('.wav'))return {ok:true,arrayBuffer:async()=>new ArrayBuffer(8)};
    return response(queue.length>1?queue.shift():queue[0]);
  });
  f.env.AudioContext=Context;
  const player=createLocalSpeechPlayer(f.env);player.unlock();
  return {...f,player,started,stopped};
}
const part={id,state:'streaming',parts:1,part_url:'/speech/audio/'+id+'/',status_url:'/speech/jobs/'+id};
test('streamed parts are queued back to back as soon as each is published',async()=>{
  const f=streamFixture([part,{...part,parts:3},{...part,state:'ready',parts:3}]);
  f.player.start('A long reply.',f.callbacks);
  for(let i=0;i<40&&f.started.length<3;i++)await new Promise(r=>setTimeout(r,20));
  assert.deepEqual(f.calls.filter(x=>x.path.endsWith('.wav')).map(x=>x.path.slice(-6)),['/0.wav','/1.wav','/2.wav']);
  assert.equal(f.started.length,3);
  assert.ok(Math.abs(f.started[1]-f.started[0]-1)<1e-9&&Math.abs(f.started[2]-f.started[1]-1)<1e-9,'no gap or overlap between parts');
  f.player.dispose();
});
test('stop during streamed playback silences queued parts and cancels the receipt',async()=>{
  const f=streamFixture([part,{...part,parts:2}]);
  f.player.start('A long reply.',f.callbacks);
  for(let i=0;i<40&&f.started.length<1;i++)await new Promise(r=>setTimeout(r,20));
  f.player.cancel();
  assert.ok(f.stopped.length>=1);
  assert.equal(f.calls.filter(x=>x.path==='/speech/cancel').length,1);
  const before=f.started.length;await new Promise(r=>setTimeout(r,150));
  assert.equal(f.started.length,before,'no part starts after Stop');
});
test('a stream that fails after starting reports an error, never a normal end',async()=>{
  const f=streamFixture([part,{...part,state:'failed'}]);
  f.player.start('A long reply.',f.callbacks);
  for(let i=0;i<40&&!f.events.includes('error');i++)await new Promise(r=>setTimeout(r,20));
  assert.ok(f.events.includes('error'));assert.ok(!f.events.includes('end'));
  f.player.dispose();
});
test('spoken fraction follows anchors and holds rather than running ahead',()=>{
  const {spokenFraction}=require('../viewer/local-speech.js');
  const anchors=[[0.1,0],[1.7,0.35],[2.1,0.35],[4.5,1]];
  assert.equal(spokenFraction(anchors,0,false),0);
  assert.equal(spokenFraction(anchors,1.9,false),0.35,'held during the pause');
  assert.equal(spokenFraction([[0,0],[2,0.4]],9,false),0.4,'unconfirmed speech is not guessed ahead');
  assert.equal(spokenFraction(anchors,9,true),1);
});
test('a reply whose parts all played before completion still ends (no stuck Speaking)',async()=>{
  let finishLater;const gate=new Promise(r=>{finishLater=r;});
  const sources=[];
  const queue=[part,part];let released=false;
  const f=fixture(async path=>{
    if(path.endsWith('.wav'))return {ok:true,arrayBuffer:async()=>new ArrayBuffer(8)};
    if(queue.length)return response(queue.shift());
    await gate; return response({...part,state:'ready'});
  });
  f.env.AudioContext=class{constructor(){this.currentTime=0;this.destination={};}resume(){return Promise.resolve();}
    decodeAudioData(){return Promise.resolve({duration:1});}
    createBufferSource(){const src={connect(){},start(){},stop(){}};sources.push(src);return src;}};
  const player=createLocalSpeechPlayer(f.env);player.unlock();
  player.start('A short reply.',f.callbacks);
  for(let i=0;i<50&&!sources.length;i++)await new Promise(r=>setTimeout(r,20));
  sources[0].onended();                       // the only part finishes while the reply is still streaming
  assert.ok(!f.events.includes('end'));
  finishLater();                              // then the service marks the reply complete
  for(let i=0;i<50&&!f.events.includes('end');i++)await new Promise(r=>setTimeout(r,20));
  assert.ok(f.events.includes('end'),'playback reports its end');
});
test('an acknowledgement plays only from cache and the reply is queued after it',async()=>{
  const starts=[];let stopped=0;
  const f=fixture(path=>{
    if(path.endsWith('.wav'))return {ok:true,arrayBuffer:async()=>new ArrayBuffer(8)};
    return response({...part,state:'ready',parts:1,audio_url:'/speech/audio/'+id+'.wav'});
  });
  f.env.AudioContext=class{constructor(){this.currentTime=0;this.destination={};}resume(){return Promise.resolve();}
    decodeAudioData(){return Promise.resolve({duration:1.2});}
    createBufferSource(){const src={connect(){},start(at){starts.push(at);},stop(){stopped++;}};return src;}};
  const player=createLocalSpeechPlayer(f.env);player.unlock();
  await player.acknowledge('One moment, sir.');
  assert.equal(JSON.parse(f.calls.find(c=>c.path==='/speech').options.body).cached_only,true);
  player.start('The answer.',f.callbacks);
  for(let i=0;i<50&&starts.length<2;i++)await new Promise(r=>setTimeout(r,20));
  assert.ok(starts[1]>=starts[0]+1.2-1e-9,'the reply starts when the acknowledgement ends');
  player.cancel();assert.ok(stopped>=1,'Stop silences everything');
});
test('an uncached acknowledgement is skipped, never waited for',async()=>{
  const f=fixture(()=>response({state:'not_cached'}));
  f.env.AudioContext=class{constructor(){this.currentTime=0;this.destination={};}resume(){return Promise.resolve();}createBufferSource(){throw Error('nothing should play');}};
  const player=createLocalSpeechPlayer(f.env);player.unlock();
  await player.acknowledge('One moment, sir.');
  assert.equal(f.calls.filter(c=>c.path.endsWith('.wav')).length,0);
});
test('an acknowledgement still being fetched never plays after Stop or after its request ends',async()=>{
  for(const finish of ['stop','unwanted']){
    const starts=[];let release;const gate=new Promise(r=>release=r);
    const f=fixture(async path=>{
      if(path.endsWith('.wav'))return {ok:true,arrayBuffer:async()=>new ArrayBuffer(8)};
      await gate;return response({state:'ready',audio_url:'/speech/audio/'+id+'.wav'});
    });
    f.env.AudioContext=class{constructor(){this.currentTime=0;this.destination={};}resume(){return Promise.resolve();}
      decodeAudioData(){return Promise.resolve({duration:1.2});}
      createBufferSource(){return {connect(){},start(at){starts.push(at);},stop(){}};}};
    const player=createLocalSpeechPlayer(f.env);player.unlock();
    let waiting=true;
    const pending=player.acknowledge('One moment, sir.',()=>waiting);
    if(finish==='stop')player.cancel();else waiting=false;   // Stop, or the answer arrived with nothing to speak
    release();await pending;
    assert.equal(starts.length,0,finish+': nothing becomes audible late');
  }
});
