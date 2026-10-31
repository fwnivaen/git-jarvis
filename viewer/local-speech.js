'use strict';
const LOCAL_SPEECH_SETTINGS = Object.freeze({pollMs:100, startLeadS:0.05, progressMs:80, maxRevealCharsPerS:45, waitMs:260000,
  silenceUrl:'/voice-silence.wav', failure:'Voice could not play. The reply is still on screen.'});

// One reusable audio element, unlocked on the owner's activation gesture.
// Receipts are cancelled even if Stop arrived before the POST response.
function createLocalSpeechPlayer(env = window) {
  const audio = new env.Audio();
  // Web Audio schedules streamed parts back to back with no gap; the element
  // remains the fallback for complete files where Web Audio is unavailable.
  const Context = env.AudioContext || env.webkitAudioContext;
  let context = null;
  let current = null, unlocking = Promise.resolve();
  const valid = job => current === job && !job.cancelled;
  const requestID = () => env.crypto?.randomUUID ? env.crypto.randomUUID().replaceAll('-','') :
    Array.from({length:32},()=>Math.floor(Math.random()*16).toString(16)).join('');
  function remoteCancel(id) {
    env.fetch('/speech/cancel', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({request_id:id}), keepalive:true}).catch(()=>{});
  }
  function release(job) {
    if (current !== job) return;
    current = null;
    env.clearTimeout(job.timer); env.clearInterval?.(job.progress);
    audio.onplaying = null; audio.onended = null; audio.onerror = null;
    for (const source of job.sources || []) { source.onended = null; try { source.stop(); } catch (_) {} }
    job.sources = [];
  }
  function cancel(keepAcknowledgement = false) {
    ackEpoch++; // an acknowledgement still being fetched never plays after this
    if (keepAcknowledgement !== true) {
      try { acknowledgement?.stop(); } catch (_) {}
      acknowledgement = null; acknowledgementEnds = 0;
    }
    const job = current;
    if (!job) return;
    job.cancelled = true;
    release(job);
    job.controller.abort();
    audio.pause();
    audio.removeAttribute?.('src'); audio.load?.();
    remoteCancel(job.id);
  }
  function warm(prepare = []) {
    env.fetch('/speech/warm', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(prepare.length ? {prepare} : {})}).catch(()=>{});
  }
  // A short phrase only if already rendered (instant). The next reply is queued after it,
  // never over it; Stop silences it.
  let acknowledgement = null, acknowledgementEnds = 0, ackEpoch = 0;
  // wanted() is the caller's ownership check (still waiting on the same request). It is
  // re-read after every await: Stop, an answer or a newer request meanwhile silences it.
  async function acknowledge(text, wanted = () => true) {
    if (!context || current) return;
    const epoch = ackEpoch;
    const stale = () => Boolean(current) || epoch !== ackEpoch || !wanted();
    try {
      const response = await env.fetch('/speech', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text, request_id:requestID(), cached_only:true})});
      const packet = await response.json();
      if (stale() || packet.state !== 'ready' || !/^\/speech\/audio\/[a-f0-9]{32}\.wav$/.test(packet.audio_url || '')) return;
      const buffer = await context.decodeAudioData(await (await env.fetch(packet.audio_url, {cache:'no-store'})).arrayBuffer());
      if (stale()) return;
      const source = context.createBufferSource();
      source.buffer = buffer; source.connect(context.destination);
      const at = context.currentTime + LOCAL_SPEECH_SETTINGS.startLeadS;
      source.start(at); acknowledgement = source; acknowledgementEnds = at + buffer.duration;
    } catch (_) {}
  }
  function unlock() {
    if (Context && !context) { try { context = new Context(); } catch (_) { context = null; } }
    context?.resume?.().catch?.(()=>{});
    if (current) return unlocking;
    audio.src = LOCAL_SPEECH_SETTINGS.silenceUrl;
    try {
      unlocking = Promise.resolve(audio.play()).then(()=>{if(!current?.playing)audio.pause();}).catch(()=>{});
    } catch (_) { unlocking = Promise.resolve(); }
    return unlocking;
  }
  function start(text, callbacks = {}) {
    cancel(true); // a playing acknowledgement finishes; this reply is queued after it
    const job = {id:requestID(), controller:new env.AbortController(), cancelled:false, playing:false, sources:[], next:0, at:0,
      complete:false, anchors:[], anchorsComplete:false, length:Math.max(1, String(text).length)};
    current = job;
    const failed = () => {
      if (!valid(job)) return;
      release(job); job.controller.abort(); audio.pause(); remoteCancel(job.id);
      callbacks.onerror?.(LOCAL_SPEECH_SETTINGS.failure);
    };
    job.timer = env.setTimeout(failed, LOCAL_SPEECH_SETTINGS.waitMs);
    (async()=>{
      const options = {signal:job.controller.signal, cache:'no-store'};
      let response = await env.fetch('/speech', {...options, method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text, request_id:job.id, ...(callbacks.delivery?{delivery:callbacks.delivery}:{})})});
      if (!valid(job)) return;
      if (!response.ok) throw new Error('Voice unavailable');
      let packet = await response.json();
      if (context) return stream(job, packet, options, callbacks, failed);
      while (valid(job) && packet.state !== 'ready') {
        if (packet.state === 'failed') throw new Error('Voice failed');
        if (packet.state === 'cancelled') {release(job);callbacks.onend?.();return;}
        if (!/^\/speech\/jobs\/[a-f0-9]{32}$/.test(packet.status_url || '')) throw new Error('Invalid receipt');
        callbacks.onprogress?.(packet.state);
        await new Promise(resolve=>env.setTimeout(resolve, LOCAL_SPEECH_SETTINGS.pollMs));
        if (!valid(job)) return;
        response = await env.fetch(packet.status_url, options);
        if (!response.ok) throw new Error('Voice expired');
        packet = await response.json();
      }
      if (!valid(job)) return;
      if (!/^\/speech\/audio\/[a-f0-9]{32}\.wav$/.test(packet.audio_url || '')) throw new Error('Invalid audio');
      await unlocking;
      if (!valid(job)) return;
      audio.src = packet.audio_url;
      const began = () => {
        if (valid(job) && !job.playing) {job.playing=true;callbacks.onstart?.();}
      };
      audio.onplaying = began;
      audio.onended = () => {if(valid(job)&&job.playing){release(job);callbacks.onend?.();}};
      audio.onerror = failed;
      await audio.play();
      if (valid(job)) began();
    })().catch(failed);
    return job.id;
  }
  async function stream(job, packet, options, callbacks, failed) {
    // Streamed parts (or one complete cached file) are decoded and queued in order.
    const play = async url => {
      const response = await env.fetch(url, options);
      if (!valid(job)) return;
      if (!response.ok) throw new Error('Voice part unavailable');
      const buffer = await context.decodeAudioData(await response.arrayBuffer());
      if (!valid(job)) return;
      await context.resume?.();
      const source = context.createBufferSource();
      source.buffer = buffer; source.connect(callbacks.outputNode?.(context) || context.destination);
      job.at = Math.max(job.at, context.currentTime + LOCAL_SPEECH_SETTINGS.startLeadS, acknowledgementEnds);
      source.start(job.at);
      job.at += buffer.duration;
      job.sources.push(source);
      source.onended = () => {
        if (!valid(job)) return;
        job.sources = job.sources.filter(item => item !== source);
        if (job.complete && !job.sources.length) {release(job);callbacks.onend?.();}
      };
      if (!job.playing) {
        job.playing = true; job.first = job.at - buffer.duration;
        // Text progress follows the audio: pause-anchored timeline minus output latency.
        if (callbacks.onfraction && env.setInterval) job.progress = env.setInterval(()=>{
          if (!valid(job)) return;
          const heard = context.currentTime - job.first - (context.outputLatency || 0);
          const target = spokenFraction(job.anchors, heard, job.anchorsComplete && job.complete);
          // Never backwards, and a timeline correction is eased in rather than flashed ahead.
          const step = LOCAL_SPEECH_SETTINGS.maxRevealCharsPerS * LOCAL_SPEECH_SETTINGS.progressMs / 1000 / job.length;
          job.shown = Math.max(job.shown || 0, Math.min(target, (job.shown || 0) + step));
          callbacks.onfraction(job.shown);
        }, LOCAL_SPEECH_SETTINGS.progressMs);
        // Report "playing" when the first sample is due, not when it is queued.
        env.setTimeout(()=>{if(valid(job))callbacks.onstart?.();}, Math.max(0, (job.at - buffer.duration - context.currentTime) * 1000));
      }
    };
    while (valid(job)) {
      if (Array.isArray(packet.timeline)) {job.anchors = packet.timeline; job.anchorsComplete = !!packet.timeline_complete;}
      if (packet.state === 'failed') throw new Error('Voice failed');
      if (packet.state === 'cancelled') {release(job);callbacks.onend?.();return;}
      if (packet.parts > 0) {
        if (!/^\/speech\/audio\/[a-f0-9]{32}\/$/.test(packet.part_url || '')) throw new Error('Invalid audio');
        while (valid(job) && job.next < packet.parts) {
          const index = job.next++;
          if (packet.state === 'ready' && job.next === packet.parts) job.complete = true;
          await play(packet.part_url + index + '.wav');
        }
        if (packet.state === 'ready') {
          // All parts may already be queued: finish when they have played (or now, if they have).
          job.complete = true;
          if (!job.sources.length) {release(job);callbacks.onend?.();}
          return;
        }
      } else if (packet.state === 'ready') {
        if (!/^\/speech\/audio\/[a-f0-9]{32}\.wav$/.test(packet.audio_url || '')) throw new Error('Invalid audio');
        job.complete = true;
        return play(packet.audio_url);
      } else callbacks.onprogress?.(packet.state);
      if (!/^\/speech\/jobs\/[a-f0-9]{32}$/.test(packet.status_url || '')) throw new Error('Invalid receipt');
      await new Promise(resolve=>env.setTimeout(resolve, LOCAL_SPEECH_SETTINGS.pollMs));
      if (!valid(job)) return;
      const response = await env.fetch(packet.status_url, options);
      if (!response.ok) throw new Error('Voice expired');
      packet = await response.json();
    }
  }
  // Word-synchronised text needs the streamed Web Audio path (the element fallback has no timeline).
  const canSync = () => !!context;
  return {start, cancel, unlock, warm, acknowledge, canSync, dispose:cancel};
}
// Fraction of the reply spoken at a playback time; holds at the last anchor rather than running ahead.
function spokenFraction(anchors, seconds, complete) {
  if (!anchors.length || seconds <= anchors[0][0]) return 0;
  for (let i = 1; i < anchors.length; i++) {
    const [t0,f0] = anchors[i-1], [t1,f1] = anchors[i];
    if (seconds <= t1) return f0 + (f1 - f0) * (seconds - t0) / Math.max(t1 - t0, 1e-6);
  }
  return complete ? 1 : anchors[anchors.length-1][1];
}
if (typeof module !== 'undefined' && module.exports) module.exports={createLocalSpeechPlayer,spokenFraction,LOCAL_SPEECH_SETTINGS};
else window.createLocalSpeechPlayer=createLocalSpeechPlayer;
