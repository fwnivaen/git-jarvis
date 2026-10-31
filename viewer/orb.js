/* The orb listens to runtime events. Every moving bar comes from measured audio. */
(() => {
  'use strict';
  const CONFIG = Object.freeze({bands:24,ticks:112,fft:1024,pixelRatio:2,minHz:80,maxHz:8000,
    floorDB:-70,ceilingDB:-12,smoothing:.65,staleMs:600,activeFrameMs:33,idleFrameMs:100,
    cometMs:1600,idleBreathMs:6000,ringTurnMs:48000,micGain:4,renderTimeoutMs:16000,
    ringRadius:.405,tickRadius:.35,waveRadius:.23,coreRadius:.27,progressWidth:2,
    waveformWidth:1.4,tickWidth:1,dotRadius:1,staticDots:96,modelCharacters:44});
  // Ported from the supplied Jarvis orb; no React runtime or build step is needed.
  const VISUAL = Object.freeze({particles:16000,pixelRatio:2,viewSize:320,planeSize:400,
    cameraZ:100,cameraNear:.1,cameraFar:1000,wakeMs:3600,blend:2.2,maxDelta:.05,
    inflowRate:.45,beatSeconds:1.4,dubSeconds:.26,pulseSeconds:.06,reducedFrameMs:160,
    hintMs:2000,hintKey:'jarvis.hud.voice-hint',previewMs:6000,kick:.7,clickKick:1.4,
    hoverBlend:5,hoverDepth:6,hoverTiltX:.5,hoverTiltY:.6,meterBlend:15,
    scatterMin:2.2,scatterRange:2.5,voiceBoost:1.8,breathRate:1.3,breathDepth:.035,
    flickerMin:5,flickerRange:4,flickerDecay:.05,kickDecay:.012,
    offlineSettle:1.1,offlineFlash:.15,offlineFlashGain:1.35,offlineFloor:.15,
    offlineDelay:2.4,offlineDelayRange:2,outageMin:.2,outageRange:.35,
    outageDelay:3,outageDelayRange:5,outageFloor:.3,stutterRate:26,
    stutterSeed:91.37,stutterHash:43758.5,stutterThreshold:.45,stutterFloorRange:.4,
    wakeLock:.9,wakeKick:1.6,surgeAt:.95,surgeWidth:.006,surgeGain:.6,
    pointerScale:.02,voiceScale:.035,heartbeatScale:.03,breathScale:.4,
    absorptionRate:9,absorptionRings:3,absorptionGain:.8,heartbeatGain:.2,
    introScale:.84,introDepth:.16,introDuration:1.1,ringRadius:.425,
    fallbackRingRadii:[.406,.297,.256],fallbackRingSpeeds:[.6,-1.1,-1.5],
    ringTurnMs:48000,progressRadius:.48,progressArc:.48,signalRadius:.425,signalDepth:.045,
    rotationRate:.3,turnRate:.25,heartbeatDubGain:.6,kickVolume:.6,heartbeatVolume:.25,
    maxFrameDeltaMs:100,completeWake:2,fallbackAlpha:.65,fallbackArc:1.6});
  const LOOKS = Object.freeze({
    idle:{a:.07,s:.35,g:.4,b:1,accent:'#5eead4'},
    listening:{a:.05,s:.4,g:.8,b:1.04,accent:'#5eead4'},
    thinking:{a:.08,s:2.2,g:.7,b:1.05,accent:'#9a8cff'},
    speaking:{a:.22,s:1.1,g:1,b:1.12,accent:'#5eead4'},
    confirm:{a:.09,s:.5,g:.75,b:1.06,accent:'#f0b46e'},
    offline:{a:.02,s:.06,g:.12,b:.9,accent:'#7d8a90'},
    error:{a:.02,s:.06,g:.12,b:.9,accent:'#e5636b'}
  });
  const MODES = Object.freeze(Object.keys(LOOKS));
  const INFLOW_RATE = VISUAL.inflowRate;
  const easeOut = value => 1-Math.pow(1-Math.min(1,Math.max(0,value)),3);
  const heartbeat = value => value<=0?0:(value/VISUAL.pulseSeconds)*Math.exp(1-value/VISUAL.pulseSeconds);
  function stutter(time,floor){const value=Math.sin(Math.floor(time*VISUAL.stutterRate)*VISUAL.stutterSeed)*VISUAL.stutterHash;
    const fraction=value-Math.floor(value);return fraction>VISUAL.stutterThreshold?1:floor+fraction*VISUAL.stutterFloorRange;}
  const COMMON = /* glsl */ `
const float TAU = 6.28318530718;
const vec3 TEAL  = vec3(0.369, 0.918, 0.831);
const vec3 ICE   = vec3(0.510, 0.725, 1.000);
const vec3 LAV   = vec3(0.667, 0.588, 0.961);
const vec3 ROSE  = vec3(0.941, 0.588, 0.745);
const vec3 WHITE = vec3(0.882, 0.980, 0.961);
const vec3 AMBER = vec3(0.941, 0.706, 0.431);
const vec3 GREY  = vec3(0.549, 0.588, 0.608);
vec3 tint(float h) {
  float f = mod(h, TAU) / TAU * 4.0;
  float i = floor(f);
  float k = f - i;
  if (i < 1.0) return mix(TEAL, ICE, k);
  if (i < 2.0) return mix(ICE, LAV, k);
  if (i < 3.0) return mix(LAV, ROSE, k);
  return mix(ROSE, TEAL, k);
}
float ease(float k) { k = clamp(k, 0.0, 1.0); return 1.0 - pow(1.0 - k, 3.0); }
// Thinking palette: dim parts sink to deep blue, bright parts lift to violet
const vec3 DEEP_BLUE = vec3(0.24, 0.36, 1.00);
const vec3 VIOLET    = vec3(0.66, 0.46, 1.00);
vec3 thinkShift(vec3 c, float k) {
  float l = dot(c, vec3(0.299, 0.587, 0.114));
  vec3 tc = mix(DEEP_BLUE, VIOLET, smoothstep(0.35, 0.9, l)) * (0.55 + 0.6 * l);
  return mix(c, tc, k * 0.8);
}
// Listening: soft teal-white for the rings flowing into the orb
const vec3 INFLOW = vec3(0.62, 0.97, 0.92);

uniform float uError;
vec3 errorShift(vec3 c) { return mix(c, vec3(0.9, 0.23, 0.3), uError * 0.85); }
`;
  const PTS_VS = /* glsl */ `
${COMMON}
attribute float aTh;
attribute float aPh;
attribute float aS;
attribute vec3 aSc;
uniform float uT, uPh, uThinkPh, uAmp, uThink, uSpeak, uListen, uV, uKick, uBreath, uAbsorb;
uniform float uW, uConf, uOffl, uFlick, uRy, uRx, uPx, uGain;
varying vec3 vCol;
varying float vA;
void main() {
  float th = aTh + uThinkPh * (0.6 + aS * 1.6);
  float ph = aPh;
  float ringy = aS < 0.35 ? uThink : 0.0;
  float d = 1.0
    + uAmp * (sin(3.0 * th + uPh * 1.3) * cos(2.0 * ph + uT * 0.9) + 0.6 * sin(5.0 * ph - uPh))
    + uBreath + (aS - 0.5) * 0.07 + ringy * 0.35
    + uSpeak * uV * 0.16 * sin(ph * 6.0 - uT * 10.0 + th * 2.0) + uV * 0.05
    + uKick * 0.08 * sin(ph * 5.0 + uT * 20.0);
  d += uAbsorb * 0.025;
  vec3 p = vec3(sin(ph) * cos(th), cos(ph) * (1.0 - 0.8 * ringy), sin(ph) * sin(th)) * d;
  float pk = ease((uW - aS * 0.35) / 0.65);
  p = mix(aSc, p, pk);
  float cY = cos(uRy), sY = sin(uRy), cX = cos(uRx), sX = sin(uRx);
  float X2 = p.x * cY - p.z * sY;
  float Z2 = p.x * sY + p.z * cY;
  float Y2 = p.y * cX - Z2 * sX;
  Z2 = p.y * sX + Z2 * cX;
  float persp = max(0.25, 1.0 + Z2 * 0.25);
  float k = 56.0 / persp;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(X2 * k, -Y2 * k, 0.0, 1.0);
  gl_PointSize = uPx * (1.9 + uV * 0.5) / persp;
  float b = 0.3 + 0.6 * (1.0 - Z2) / 2.0;
  vec3 c = mix(tint(aTh + ph * 0.8 + uT * 0.35), vec3(0.92, 0.96, 0.98), 0.55);
  vCol = errorShift(mix(mix(thinkShift(c, uThink), AMBER, uConf * 0.85), GREY, uOffl * 0.8));
  vA = min(1.0, b * (0.75 + uV * 0.3)) * (0.15 + 0.85 * pk) * (1.0 - uOffl * 0.65) * (1.0 + uFlick * 0.5 + uAbsorb * 0.25) * uGain;
}
`;
  const PTS_FS = /* glsl */ `
varying vec3 vCol;
varying float vA;
void main() {
  float d = length(gl_PointCoord - 0.5) * 2.0;
  if (d > 1.0) discard;
  float a = 1.0 - d;
  a *= a;
  gl_FragColor = vec4(vCol * 1.6, a * vA * 0.4);
}
`;
  const HUD_VS = /* glsl */ `
varying vec2 vP;
void main() {
  vP = position.xy;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;
  const HUD_FS = /* glsl */ `
${COMMON}
varying vec2 vP;
uniform float uT, uRot, uGl, uV, uKick, uW, uConf, uOffl, uFlick, uWave, uGain;
uniform float uThink, uBeat, uRip, uListen, uAbsorb;

vec3 shade(vec3 c) { return errorShift(mix(mix(thinkShift(c, uThink), AMBER, uConf * 0.85), GREY, uOffl * 0.8)); }
float rv(float i) { return ease((uW - 0.45 - i * 0.05) / 0.35); }

vec2 arc(vec2 cp, float R, float w, float rot, float segs, float gap, float rev, float gw) {
  if (rev <= 0.0) return vec2(0.0);
  float r = length(cp);
  float a = atan(cp.y, cp.x);
  float af = fwidth(r) * 0.75;
  float seg = TAU / segs;
  float s = mod(a - rot, seg) * R;
  float L = seg * (1.0 - gap) * rev * R;
  float SL = seg * R;
  float dA = s <= L ? 0.0 : min(s - L, SL - s);
  float d = length(vec2(dA, abs(r - R)));
  float m = 1.0 - smoothstep(w * 0.5 - af, w * 0.5 + af, d);
  float h = exp(-max(d - w * 0.5, 0.0) / gw);
  return vec2(m, h);
}

void A(inout vec3 col, vec2 cp, float R, float w, vec3 c, float al, float rot, float segs, float gap, float glow, float rev, float dim) {
  vec2 m = arc(cp, R, w, rot, segs, gap, rev, max(glow * 0.35, 0.001));
  float halo = glow > 0.0 ? m.y * al * 0.22 * min(glow / 10.0, 2.0) : 0.0;
  col += shade(c) * (m.x * al * 1.35 + halo) * dim;
}

float tick(vec2 cp, float R, float n, float len, float lw, float rot, float every, float wave, float rev, out float ang) {
  float r = length(cp);
  float a = atan(cp.y, cp.x);
  float st = TAU / n;
  float x = a - rot;
  float i = floor(x / st + 0.5);
  float da = (x - i * st) * r;
  i = mod(i, n);
  ang = rot + i * st;
  if (i >= floor(n * clamp(rev, 0.0, 1.0))) return 0.0;
  float l = mod(i, every) < 0.5 ? len * 2.0 : len;
  l += wave * max(0.0, sin(i * 0.7 + uT * 9.0) * sin(i * 0.23 - uT * 5.5)) * 14.0 * uV;
  l += uKick * 4.0 * max(0.0, sin(i * 0.5));
  float af = fwidth(r) * 0.75;
  float tg = 1.0 - smoothstep(lw * 0.5 - af, lw * 0.5 + af, abs(da));
  float rd = smoothstep(R - af, R + af, r) * (1.0 - smoothstep(R + l - af, R + l + af, r));
  return tg * rd;
}

vec3 flare(vec2 cp, float R, float a, vec3 c, float s, float dim) {
  float fa = clamp((uW - 0.9) * 5.0, 0.0, 1.0) * dim;
  if (fa <= 0.0) return vec3(0.0);
  float d = length(cp - R * vec2(cos(a), sin(a))) / s;
  if (d > 1.0) return vec3(0.0);
  vec3 sc = shade(c) * 0.5;
  vec3 w = d < 0.3 ? mix(vec3(0.95), sc, d / 0.3) : mix(sc, vec3(0.0), (d - 0.3) / 0.7);
  return w * fa;
}

void main() {
  vec2 cp = vec2(vP.x, -vP.y);
  float r = length(cp);
  float dim = 1.0 - uOffl * 0.65;
  vec3 col = vec3(0.0);

  float bgA = clamp(uW * 2.0, 0.0, 1.0) * dim;
  vec2 o1 = vec2(cos(uT * 0.4), sin(uT * 0.4)) * 22.0;
  vec2 o2 = vec2(-cos(uT * 0.33) * 24.0, -sin(uT * 0.33) * 18.0);
  col += shade(vec3(0.157, 0.510, 0.471)) * (0.14 * uGl + 0.1 * uV + 0.05) * bgA * max(0.0, 1.0 - length(cp - o1) / 140.0);
  col += shade(vec3(0.353, 0.275, 0.667)) * (0.09 * uGl + 0.07 * uV + 0.03) * bgA * max(0.0, 1.0 - length(cp - o2) / 120.0);

  float R = uRot;
  float ang;
  float tk = tick(cp, 136.0, 150.0, 4.0, 1.6, R * 0.15, 6.0, uWave, rv(0.0), ang);
  col += shade(tint(ang + uT * 0.4)) * tk * (0.4 + 0.35 * uGl) * dim * 1.3;

  A(col, cp, 130.0, 3.0, TEAL, 0.85, R * 0.6, 1.0, 0.7, 8.0 + 10.0 * uV, rv(1.0), dim);
  A(col, cp, 130.0, 3.0, LAV, 0.55, R * 0.6 + 3.14159, 1.0, 0.86, 6.0 * uGl, rv(1.0), dim);
  A(col, cp, 123.0, 1.4, ICE, 0.28, 0.0, 1.0, 0.0, 0.0, rv(2.0), dim);

  float tk2 = tick(cp, 113.0, 90.0, 4.0, 1.4, -R * 0.4, 4.0, 0.0, rv(3.0), ang);
  col += shade(TEAL) * tk2 * 0.5 * dim * 1.3;

  A(col, cp, 104.0, 7.0, TEAL, 0.18 + 0.12 * uGl + 0.15 * uV, R * 0.8, 40.0, 0.48, 0.0, rv(4.0), dim);
  A(col, cp, 95.0, 2.4, WHITE, 0.7, -R * 1.1, 4.0, 0.26, 6.0 + 8.0 * uV, rv(5.0), dim);
  A(col, cp, 88.0, 1.4, ICE, 0.45, R * 1.6, 10.0, 0.5, 0.0, rv(6.0), dim);
  A(col, cp, 82.0, 3.5, TEAL, 0.8, -R * 1.5, 3.0, 0.58, 10.0 + 10.0 * uV, rv(7.0), dim);
  A(col, cp, 82.0, 3.5, ROSE, 0.4, -R * 1.5 + 1.0, 1.0, 0.9, 6.0 * uGl, rv(7.0), dim);

  col += flare(cp, 130.0, R * 0.6 + 0.2, TEAL, 13.0 + 10.0 * uV, dim);
  col += flare(cp, 95.0, -R * 1.1 + 2.4, LAV, 10.0 + 6.0 * uV, dim);
  col += flare(cp, 82.0, -R * 1.5 + 4.1, ICE, 8.0 + 6.0 * uV, dim);

  float cd = r / 54.0;
  if (cd < 1.0) {
    vec3 c0 = shade(vec3(0.725, 0.922, 0.961)) * (0.13 + 0.12 * uGl + 0.2 * uV + uFlick * 0.15 + uBeat * 0.35 + uAbsorb * 0.3) * dim * clamp(uW, 0.0, 1.0);
    vec3 c1 = shade(vec3(0.588, 0.549, 0.941)) * (0.04 + 0.05 * uV) * dim;
    col += cd < 0.6 ? mix(c0, c1, cd / 0.6) : mix(c1, vec3(0.0), (cd - 0.6) / 0.4);
  }

  // Listening: soft rings flow inward from outside the orb, as if drawing your voice in
  if (uListen > 0.01) {
    for (int i = 0; i < 3; i++) {
      float ph = fract(uT * ${INFLOW_RATE.toFixed(2)} + float(i) / 3.0);
      float rr = 168.0 - ease(ph) * 110.0;
      float wd = 1.2 + (1.0 - ph) * 3.0;
      // Fade in at the edge, stay visible all the way in, then dissolve into the core
      float fade = smoothstep(0.0, 0.15, ph) * (1.0 - smoothstep(0.88, 1.0, ph)) * (0.5 + 0.5 * ph);
      col += INFLOW * exp(-abs(r - rr) / wd) * fade * uListen * (0.3 + 0.5 * uV) * dim;
    }
  }

  // Confirm heartbeat: each beat sends a soft amber shockwave out from the core
  if (uConf > 0.01) {
    for (int i = 0; i < 2; i++) {
      float rp = uRip - float(i) * 0.2;
      if (rp > 0.0 && rp < 1.0) {
        float rr = 58.0 + ease(rp) * 105.0;
        float wd = 1.5 + rp * 6.0;
        float fade = pow(1.0 - rp, 2.2) * (i == 0 ? 1.0 : 0.6);
        col += AMBER * exp(-abs(r - rr) / wd) * fade * uConf * 0.55 * dim;
      }
    }
  }

  vec3 light = col * uGain;
  float opacity = clamp(max(max(light.r, light.g), light.b), 0.0, 1.0);
  // Preserve additive light with true transparency over the existing galaxy.
  gl_FragColor = vec4(light / max(opacity, 0.001), opacity);
}
`;
  const STATES = Object.freeze(['IDLE','LISTENING','THINKING','SPEAKING','EXECUTING','AWAITING APPROVAL','CONFIRM','OFFLINE','ERROR']);
  const host = document.getElementById('jarvis-orb-host');
  if (!host) return;
  const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
  const reduced = {get matches(){return motionQuery.matches||document.documentElement.dataset.motion==='reduced';}};
  host.classList.add('jarvis-orb');
  host.innerHTML = `<div class="orb-instrument" data-state="IDLE">
    <div class="orb-halo" aria-hidden="true"></div>
    <canvas class="orb-particles" aria-hidden="true"></canvas>
    <canvas class="orb-telemetry" aria-hidden="true"></canvas>
    <button type="button" class="orb-core" aria-label="Turn microphone on" aria-pressed="false">
      <span class="sr-only">J.A.R.V.I.S. voice control</span>
    </button><svg class="orb-approval-line" aria-hidden="true"><path></path></svg>
  </div><div class="orb-status-chip"><span class="orb-state" role="status" aria-live="polite">IDLE</span><span class="orb-status-divider" aria-hidden="true">·</span>
  <button type="button" class="orb-model" aria-haspopup="dialog" aria-controls="brain-picker">Connecting brain</button></div>
  <div class="orb-voice-hint">Hold Space to talk</div>
  <details class="orb-input-settings"><summary aria-label="Orb and microphone settings" title="Orb and microphone settings"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 2v12M8 2v12M13 2v12M1 5h4M6 11h4M11 6h4"/></svg></summary>
    <label>Input device<select class="orb-device" aria-label="Microphone input device"><option value="">System default</option></select></label>
    <p class="orb-device-note">Device selection uses the local Mac recognizer.</p>
    <p class="orb-signal-detail">Microphone off · no audio sampled</p>
    <div class="orb-detail">Hold Space to talk</div>
    <details class="orb-preview-settings"><summary>Preview animations</summary>
      <p class="orb-preview-note">Try each look with the microphone off. Returns to live automatically.</p>
      <div class="orb-preview-controls" aria-label="Orb animation previews">
        ${['Idle','Listening','Thinking','Speaking','Confirm','Offline','Wake','Live'].map(name=>`<button type="button" data-orb-preview="${name.toLowerCase()}" aria-pressed="false">${name}</button>`).join('')}
      </div>
    </details>
  </details>`;
  const $ = selector => host.querySelector(selector);
  const instrument = $('.orb-instrument'), canvas = $('.orb-telemetry'), ctx = canvas.getContext('2d');
  const core = $('.orb-core'), label = $('.orb-state'), model = $('.orb-model'), detail = $('.orb-detail');
  let voice = {enabled:false,thinking:false,speaking:false,externalSpeaking:false,pending:false,notice:'',backend:'browser'};
  let explicit = null, currentState = 'IDLE', native = null, browserMic = null, browserSource = null;
  let micRequest = 0, audioContext = null, playback = null, renderController = null, outputUnavailable = false;
  let heldSpace = false, enabledBeforeSpace = false, lastDraw = 0, spectrum = new Array(CONFIG.bands).fill(0);
  let connected = true, stopped = false, micPending = false, outputLoading = false, dirty = true;
  let particleView = null, lastAnimation = 0, animationFrame = null, preview = null, previewTimer = null;
  const motion={wake:performance.now(),kick:0,mx:0,my:0,hover:0};
  const sim={amp:LOOKS.idle.a,spd:LOOKS.idle.s,gl:LOOKS.idle.g,gain:1,vol:0,hov:0,flick:0,nextFlick:VISUAL.flickerMin,
    t:0,ph:0,rot:0,ry:0,thinkPh:0,lastW:0,lastMode:'idle',beatT:0,offT:0,nextOff:0,outage:0,
    env:Object.fromEntries(MODES.map(mode=>[mode,mode==='idle'?1:0]))};
  try{if(!sessionStorage.getItem(VISUAL.hintKey)){host.classList.add('show-voice-hint');sessionStorage.setItem(VISUAL.hintKey,'1');setTimeout(()=>host.classList.remove('show-voice-hint'),VISUAL.hintMs);}}catch{}
  async function initParticles(){
    let renderer;
    const resources=[];
    try{
      const THREE=await import('/three.module.js');if(stopped)return;
      renderer=new THREE.WebGLRenderer({canvas:$('.orb-particles'),alpha:true,antialias:true,powerPreference:'low-power'});
      renderer.setPixelRatio(Math.min(devicePixelRatio||1,VISUAL.pixelRatio));renderer.setClearColor(0,0);
      renderer.toneMapping=THREE.NoToneMapping;renderer.outputColorSpace=THREE.LinearSRGBColorSpace;
      const scene=new THREE.Scene(),half=VISUAL.viewSize/2;
      const camera=new THREE.OrthographicCamera(-half,half,half,-half,VISUAL.cameraNear,VISUAL.cameraFar);camera.position.z=VISUAL.cameraZ;
      const count=VISUAL.particles,geometry=new THREE.BufferGeometry();resources.push(geometry);
      const theta=new Float32Array(count),phi=new Float32Array(count),seed=new Float32Array(count),scatter=new Float32Array(count*3);
      for(let i=0;i<count;i++){
        theta[i]=Math.random()*Math.PI*2;phi[i]=Math.acos(Math.random()*2-1);seed[i]=Math.random();
        const radius=VISUAL.scatterMin+Math.random()*VISUAL.scatterRange,y=Math.random()*2-1,angle=Math.random()*Math.PI*2,r=Math.sqrt(1-y*y);
        scatter.set([r*Math.cos(angle)*radius,y*radius,r*Math.sin(angle)*radius],i*3);
      }
      geometry.setAttribute('position',new THREE.BufferAttribute(new Float32Array(count*3),3));
      for(const [name,data,size] of [['aTh',theta,1],['aPh',phi,1],['aS',seed,1],['aSc',scatter,3]])geometry.setAttribute(name,new THREE.BufferAttribute(data,size));
      const material=(vertexShader,fragmentShader,names)=>new THREE.ShaderMaterial({vertexShader,fragmentShader,
        uniforms:Object.fromEntries(names.split(' ').map(name=>[name,{value:0}])),transparent:true,depthWrite:false,depthTest:false,blending:THREE.AdditiveBlending});
      const pointsMaterial=material(PTS_VS,PTS_FS,'uT uPh uThinkPh uAmp uThink uSpeak uListen uV uKick uBreath uW uConf uOffl uFlick uRy uRx uPx uGain uAbsorb uError');resources.push(pointsMaterial);
      const hudMaterial=material(HUD_VS,HUD_FS,'uT uRot uGl uV uKick uW uConf uOffl uFlick uWave uGain uThink uBeat uRip uListen uAbsorb uError');resources.push(hudMaterial);
      const plane=new THREE.PlaneGeometry(VISUAL.planeSize,VISUAL.planeSize);resources.push(plane);
      const group=new THREE.Group(),points=new THREE.Points(geometry,pointsMaterial);points.frustumCulled=false;
      group.add(new THREE.Mesh(plane,hudMaterial),points);scene.add(group);
      particleView={renderer,scene,camera,group,pointsMaterial,hudMaterial,resources};
      host.classList.add('orb-renderer-ready');motion.wake=performance.now();resize();dirty=true;
    }catch{resources.forEach(resource=>resource.dispose());renderer?.dispose();host.classList.add('orb-particle-fallback');}
  }
  function visualMode(state=currentState){
    if(state==='AWAITING APPROVAL'||state==='CONFIRM')return 'confirm';
    if(state==='EXECUTING')return 'thinking';
    return state.toLowerCase();
  }
  function refreshLook(){
    const mode=preview||visualMode();host.dataset.mode=mode;host.dataset.preview=String(!!preview);
    host.style.setProperty('--orb-accent',LOOKS[mode].accent);
    const text=preview?mode.toUpperCase()+' · PREVIEW':currentState;
    if(label.textContent!==text)label.textContent=text;
    for(const button of host.querySelectorAll('[data-orb-preview]')){
      button.disabled=currentState!=='IDLE'&&button.dataset.orbPreview!=='live';
      button.setAttribute('aria-pressed',String(button.dataset.orbPreview===(preview||'live')));
    }
    dirty=true;
  }
  function clearPreview(){clearTimeout(previewTimer);previewTimer=null;preview=null;refreshLook();}
  function wake(){motion.wake=performance.now();motion.kick=VISUAL.clickKick;sim.lastW=0;dirty=true;}
  $('.orb-preview-controls').addEventListener('click',event=>{
    const button=event.target.closest('[data-orb-preview]');if(!button||button.disabled)return;
    clearPreview();const mode=button.dataset.orbPreview;if(mode==='live')return;
    if(mode==='wake')wake();else preview=mode;refreshLook();previewTimer=setTimeout(clearPreview,VISUAL.previewMs);
  });
  core.addEventListener('pointermove',event=>{const bounds=instrument.getBoundingClientRect();motion.mx=(event.clientX-bounds.left)/bounds.width*2-1;motion.my=(event.clientY-bounds.top)/bounds.height*2-1;motion.hover=1;});
  core.addEventListener('pointerleave',()=>{motion.mx=0;motion.my=0;motion.hover=0;});
  function renderParticles(now,delta,amplitude){
    if(!particleView)return;
    const mode=preview||visualMode(),look=LOOKS[mode],S=sim;
    const dt=reduced.matches?0:Math.min(VISUAL.maxDelta,delta/1000);
    const blend=reduced.matches?1:1-Math.exp(-dt*VISUAL.blend);
    S.t+=dt;const t=S.t;
    for(const [key,target] of [['amp',look.a],['spd',look.s],['gl',look.g],['gain',look.b]])S[key]+=(target-S[key])*blend;
    for(const key of MODES)S.env[key]+=((mode===key?1:0)-S.env[key])*blend;
    S.hov=reduced.matches?0:S.hov+(motion.hover-S.hov)*Math.min(1,dt*VISUAL.hoverBlend);motion.kick*=Math.pow(VISUAL.kickDecay,dt);
    const kick=reduced.matches?0:motion.kick;
    S.ph+=dt*S.spd;S.rot+=dt*S.spd*VISUAL.rotationRate;S.ry+=dt*S.spd*VISUAL.turnRate;S.thinkPh+=dt*S.env.thinking;
    // Voice displacement and the spectrum always use measured input/output, never a fake talking signal.
    const targetVolume=mode==='offline'||mode==='error'?0:Math.min(1,amplitude*VISUAL.voiceBoost);
    S.vol+=(targetVolume-S.vol)*Math.min(1,dt*VISUAL.meterBlend);
    if(mode==='idle'&&t>S.nextFlick){S.flick=1;S.nextFlick=t+VISUAL.flickerMin+Math.random()*VISUAL.flickerRange;}
    S.flick*=Math.pow(VISUAL.flickerDecay,dt);
    if(mode!==S.lastMode){S.lastMode=mode;motion.kick=VISUAL.kick;if(mode==='confirm')S.beatT=0;
      if(mode==='offline'||mode==='error'){S.offT=0;S.outage=0;S.nextOff=VISUAL.offlineDelay+Math.random()*VISUAL.offlineDelayRange;}}
    S.beatT+=dt;S.offT+=dt;const bp=S.beatT%VISUAL.beatSeconds;
    const hb=reduced.matches?0:(heartbeat(bp)+VISUAL.heartbeatDubGain*heartbeat(bp-VISUAL.dubSeconds))*S.env.confirm;
    let power=1;
    if(!reduced.matches&&(mode==='offline'||mode==='error')){
      if(S.offT<VISUAL.offlineFlash)power=VISUAL.offlineFlashGain;
      else if(S.offT<VISUAL.offlineSettle)power=stutter(t,VISUAL.offlineFloor);
      else if(S.offT>S.nextOff){S.outage=VISUAL.outageMin+Math.random()*VISUAL.outageRange;S.nextOff=S.offT+VISUAL.outageDelay+Math.random()*VISUAL.outageDelayRange;}
      if(S.outage>0){S.outage-=dt;power=stutter(t,VISUAL.outageFloor);}
    }
    const powerMix=1+(power-1)*(S.env.offline+S.env.error);
    const volume=reduced.matches?0:S.vol+kick*VISUAL.kickVolume+hb*VISUAL.heartbeatVolume;
    const w=reduced.matches?VISUAL.completeWake:(now-motion.wake)/VISUAL.wakeMs;
    if(!reduced.matches&&S.lastW<VISUAL.wakeLock&&w>=VISUAL.wakeLock)motion.kick=VISUAL.wakeKick;S.lastW=w;
    const surge=reduced.matches?0:Math.exp(-((w-VISUAL.surgeAt)**2)/VISUAL.surgeWidth)*VISUAL.surgeGain;
    const gain=(S.gain+surge+hb*VISUAL.heartbeatGain)*powerMix;
    let absorb=0;
    if(!reduced.matches)for(let i=0;i<VISUAL.absorptionRings;i++)absorb+=Math.exp(-((t*VISUAL.inflowRate+i/VISUAL.absorptionRings)%1)*VISUAL.absorptionRate);
    absorb*=S.env.listening*(1+volume*VISUAL.absorptionGain);
    const breath=reduced.matches?0:Math.sin(t*VISUAL.breathRate)*VISUAL.breathDepth*S.env.idle;
    const p=particleView.pointsMaterial.uniforms,h=particleView.hudMaterial.uniforms;
    const common={uT:t,uW:w,uV:volume,uKick:kick,uThink:S.env.thinking,uConf:S.env.confirm,uOffl:S.env.offline,uError:S.env.error,uFlick:reduced.matches?0:S.flick,uGain:gain,uListen:S.env.listening,uAbsorb:absorb};
    for(const [name,value] of Object.entries(common)){if(p[name])p[name].value=value;if(h[name])h[name].value=value;}
    const points={uPh:S.ph,uThinkPh:S.thinkPh,uAmp:reduced.matches?0:S.amp,uSpeak:S.env.speaking,uBreath:breath,
      uRy:S.ry+motion.mx*VISUAL.hoverTiltY*S.hov,uRx:motion.my*VISUAL.hoverTiltX*S.hov,uPx:instrument.clientWidth/VISUAL.viewSize*particleView.renderer.getPixelRatio()};
    for(const [name,value] of Object.entries(points))p[name].value=value;
    for(const [name,value] of Object.entries({uRot:S.rot,uGl:S.gl,uWave:amplitude>0?1:0,uBeat:hb,uRip:bp/VISUAL.beatSeconds}))h[name].value=value;
    particleView.group.position.set(motion.mx*VISUAL.hoverDepth*S.hov,-motion.my*VISUAL.hoverDepth*S.hov,0);
    particleView.group.scale.setScalar((VISUAL.introScale+VISUAL.introDepth*easeOut(w/VISUAL.introDuration))*(1+volume*VISUAL.voiceScale+S.hov*VISUAL.pointerScale+breath*VISUAL.breathScale+hb*VISUAL.heartbeatScale));
    particleView.renderer.render(particleView.scene,particleView.camera);
  }

  const clamp = value => Math.max(0, Math.min(1, Number(value) || 0));
  const editable = element => !!element?.closest?.('input,textarea,select,[contenteditable="true"],[role="textbox"]');
  const rootStyle = () => getComputedStyle(document.documentElement);
  const color = (name,fallback='--accent') => rootStyle().getPropertyValue(name).trim() || rootStyle().getPropertyValue(fallback).trim();
  const activeContext = async () => {
    if (!audioContext || audioContext.state === 'closed') {
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) throw new Error('Web Audio is unavailable');
      audioContext = new Context();
    }
    if (audioContext.state === 'suspended') await audioContext.resume();
    return audioContext;
  };
  function analyser(context) {
    const node = context.createAnalyser(); node.fftSize=CONFIG.fft;
    node.minDecibels=CONFIG.floorDB;node.maxDecibels=CONFIG.ceilingDB;node.smoothingTimeConstant=CONFIG.smoothing;
    return {node,frequencies:new Uint8Array(node.frequencyBinCount),samples:new Float32Array(node.fftSize)};
  }
  function readSpectrum(meter) {
    meter.node.getByteFrequencyData(meter.frequencies);meter.node.getFloatTimeDomainData(meter.samples);
    const rate = meter.node.context.sampleRate;
    const maximum = Math.min(CONFIG.maxHz,rate/2);
    const bands = Array.from({length:CONFIG.bands},(_,index)=>{
      const low=CONFIG.minHz*Math.pow(maximum/CONFIG.minHz,index/CONFIG.bands);
      const high=CONFIG.minHz*Math.pow(maximum/CONFIG.minHz,(index+1)/CONFIG.bands);
      const start=Math.max(1,Math.floor(low/rate*CONFIG.fft)),end=Math.min(meter.frequencies.length,Math.max(start+1,Math.ceil(high/rate*CONFIG.fft)));
      let peak=0;for(let bin=start;bin<end;bin++)peak=Math.max(peak,meter.frequencies[bin]/255);return peak;
    });
    let squared=0;for(const sample of meter.samples)squared+=sample*sample;
    return {bands,rms:clamp(Math.sqrt(squared/meter.samples.length)*CONFIG.micGain)};
  }
  function stopMicMeter() {
    micRequest++;micPending=false;browserSource?.disconnect();browserSource=null;
    if(browserMic){browserMic.stream.getTracks().forEach(track=>track.stop());browserMic=null;}
  }
  async function startMicMeter() {
    if(browserMic||micPending||!voice.enabled||voice.backend==='native'||!navigator.mediaDevices?.getUserMedia)return;
    const revision=++micRequest;
    micPending=true;
    try {
      const stream=await navigator.mediaDevices.getUserMedia({audio:true,video:false});
      if(revision!==micRequest||!voice.enabled||voice.backend==='native'){stream.getTracks().forEach(track=>track.stop());return;}
      const context=await activeContext();
      if(revision!==micRequest||!voice.enabled){stream.getTracks().forEach(track=>track.stop());return;}
      const meter=analyser(context);browserSource=context.createMediaStreamSource(stream);browserSource.connect(meter.node);
      browserMic={stream,meter};refreshDetail();
    }catch { if(voice.enabled){$('.orb-signal-detail').textContent='Recognition active · microphone spectrum unavailable';} }
    finally { if(revision===micRequest)micPending=false; }
  }
  function selectedState() {
    if(!connected)return 'OFFLINE';
    if(explicit)return explicit.state;
    if( /denied|unavailable|could not|failed|ended|retry/i.test(voice.notice||''))return 'ERROR';
    if(voice.thinking||outputLoading)return 'THINKING';if(voice.speaking)return 'SPEAKING';
    return voice.enabled?'LISTENING':'IDLE';
  }
  function refreshDetail() {
    const muted = new URLSearchParams(location.search).getAll('mute').includes('1');
    let message=explicit?.message||voice.notice||'';
    if(!message)message=currentState==='SPEAKING'?(voice.externalSpeaking?'Native JARVIS voice · output spectrum unavailable':outputUnavailable?'M1 voice preview · spectrum unavailable':'M1 voice preview · local audio'):
      currentState==='THINKING'?(outputLoading?'Preparing M1 voice preview':'Working on your request'):currentState==='LISTENING'?(voice.wakeListening?'Awaiting “Jarvis” · microphone enabled':'Listening · say stop to interrupt'):
      currentState==='OFFLINE'?'Local server disconnected · reconnect to resume':currentState==='CONFIRM'||currentState==='AWAITING APPROVAL'?'Waiting for your confirmation':currentState==='ERROR'?'Reconnect or choose an input':muted?'Silent session · hold Space to talk':'Hold Space to talk';
    detail.textContent=message;
    const micLive=voice.enabled&&(browserMic||(native&&Date.now()-native.received<CONFIG.staleMs));
    $('.orb-signal-detail').textContent=micLive?(voice.backend==='native'?'Live local microphone FFT':'Live browser microphone FFT'):
      voice.enabled?'Recognition active · waiting for microphone samples':'Microphone off · no audio sampled';
  }
  function sync() {
    const next=selectedState();
    if(next!==currentState){clearTimeout(previewTimer);preview=null;currentState=next;instrument.dataset.state=next;host.dataset.state=next;dirty=true;window.JarvisGalaxy?.wake();}
    refreshLook();
    core.setAttribute('aria-pressed',String(voice.enabled));core.setAttribute('aria-label',voice.enabled?'Turn microphone off':'Turn microphone on');
    refreshDetail();
  }
  function updateVoice(value) {
    voice={...voice,...value};
    if(!voice.enabled||voice.backend==='native')stopMicMeter();else startMicMeter();
    if(!voice.speaking&&!playback)outputUnavailable=false;
    sync();
  }
  function nativeAudio(audio) {
    if(!audio||!Array.isArray(audio.bands)||audio.bands.length!==CONFIG.bands)return;
    native={bands:audio.bands.map(clamp),rms:clamp(audio.rms),received:Date.now()};
    refreshDetail();
  }
  async function release(token) {
    if(token)try{await fetch('/voice/release',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token}),keepalive:true});}catch{}
  }
  const localSpeech = window.createLocalSpeechPlayer();
  let speechEpoch = 0;
  function stopSpeech() {
    speechEpoch++;
    localSpeech.cancel();
    outputLoading=false;
    if(playback?.meter)playback.meter.node.disconnect();
    playback=null;
    sync();
  }
  async function playSpeech(text,options={}) {
    stopSpeech();
    const epoch=speechEpoch;
    const current=()=>epoch===speechEpoch && options.isCurrent?.()!==false;
    let meter=null;
    const finish=()=>{if(!current())return;if(meter)meter.node.disconnect();playback=null;outputLoading=false;sync();options.onEnd?.();};
    outputLoading=true;sync();
    localSpeech.start(text,{
      outputNode:context=>{if(!meter){meter=analyser(context);meter.node.connect(context.destination);}playback={meter};return meter.node;},
      onstart:()=>{if(current()){outputLoading=false;outputUnavailable=false;sync();options.onStart?.();}},
      onend:finish,
      onerror:message=>{if(!current())return;outputUnavailable=true;if(meter)meter.node.disconnect();playback=null;outputLoading=false;sync();options.onError?.(message);}
    });
    return true;
  }
  function setStatus(state,options={}) {
    if(!state){explicit=null;sync();return;}
    const normalized=String(state).toUpperCase().replace(/_/g,' ');
    if(!STATES.includes(normalized))return;
    explicit={state:normalized,progress:Number.isFinite(options.progress)?clamp(options.progress):null,message:options.message||'',target:options.target||null};sync();
  }
  function setModel(brain) {
    const text=typeof brain==='string'?brain:brain?.model||brain?.label;
    if(text){model.textContent=text.replace(/^(?:CODEX|OPENROUTER)\s*[·/]\s*/i,'').slice(0,CONFIG.modelCharacters).toUpperCase();model.title='Switch brain · '+text;}
  }
  function toggleMicrophone() {document.getElementById('mic')?.click();}
  core.addEventListener('click',()=>{if(currentState==='ERROR'){explicit=null;voice.notice='';sync();}toggleMicrophone();});
  model.addEventListener('click',()=>document.getElementById('brain-chip')?.click());
  $('.orb-input-settings').addEventListener('toggle',async event=>{
    if(!event.target.open)return;
    try{
      const response=await fetch('/voice/devices');if(!response.ok)throw new Error();const data=await response.json();
      const select=$('.orb-device'),chosen=window.JarvisVoice?.inputDevice?.()??select.value;select.replaceChildren(new Option('System default',''));
      for(const device of data.devices||[])if(typeof device.id==='string'&&typeof device.name==='string')select.add(new Option(device.name,device.id));
      select.value=chosen;select.disabled=!data.available;
      $('.orb-device-note').textContent=data.available?'Selecting an input uses local Mac recognition.':data.reason||'Device selection unavailable';
    }catch{$('.orb-device-note').textContent='Input list unavailable · use macOS Sound settings';$('.orb-device').disabled=true;}
  });
  $('.orb-device').addEventListener('change',event=>{
    if(typeof window.JarvisVoice?.setInputDevice==='function')window.JarvisVoice.setInputDevice(event.target.value);
    else{$('.orb-device-note').textContent='Input selection needs the voice adapter';event.target.value='';}
  });
  window.addEventListener('keydown',event=>{
    if(event.code!=='Space'||event.repeat||editable(event.target)||event.altKey||event.ctrlKey||event.metaKey||document.querySelector('dialog[open]'))return;
    event.preventDefault();window.JarvisVoice?.activate?.();window.JarvisVoice?.beginHold?.();heldSpace=true;enabledBeforeSpace=Boolean(window.JarvisVoice?.isEnabled?.());
    if(!enabledBeforeSpace)toggleMicrophone();
  });
  function releaseSpace(){if(!heldSpace)return;heldSpace=false;if(!enabledBeforeSpace&&window.JarvisVoice?.isEnabled?.()){
    if(typeof window.JarvisVoice.finishHold==='function')window.JarvisVoice.finishHold();else toggleMicrophone();
  }else window.JarvisVoice?.endHold?.();}
  window.addEventListener('keyup',event=>{if(event.code==='Space'){if(heldSpace)event.preventDefault();releaseSpace();}});
  window.addEventListener('blur',releaseSpace);
  function resize(){const size=instrument.clientWidth;const ratio=Math.min(devicePixelRatio||1,CONFIG.pixelRatio);canvas.width=size*ratio;canvas.height=size*ratio;canvas.style.width=size+'px';canvas.style.height=size+'px';ctx.setTransform(ratio,0,0,ratio,0,0);particleView?.renderer.setSize(size,size,false);dirty=true;}
  const resizeObserver=new ResizeObserver(resize);resizeObserver.observe(instrument);
  function line(angle,radius,extension,width,ink,alpha,size){ctx.globalAlpha=alpha;ctx.strokeStyle=ink;ctx.lineWidth=width;ctx.beginPath();ctx.moveTo(size/2+Math.cos(angle)*radius,size/2+Math.sin(angle)*radius);ctx.lineTo(size/2+Math.cos(angle)*(radius+extension),size/2+Math.sin(angle)*(radius+extension));ctx.stroke();}
  function draw(now) {
    if(stopped)return;animationFrame=requestAnimationFrame(draw);
    const active=currentState!=='IDLE'&&currentState!=='ERROR'&&currentState!=='OFFLINE';
    const frameMs=reduced.matches?VISUAL.reducedFrameMs:!active||currentState==='THINKING'?CONFIG.idleFrameMs:CONFIG.activeFrameMs;
    if(document.hidden||now-lastDraw<frameMs||reduced.matches&&!active&&!dirty)return;
    const delta=lastAnimation?Math.min(now-lastAnimation,VISUAL.maxFrameDeltaMs):0;lastAnimation=now;lastDraw=now;dirty=false;
    const size=instrument.clientWidth;if(!size)return;ctx.clearRect(0,0,size,size);
    const mode=preview||visualMode(),ink=LOOKS[mode].accent,center=size/2;
    let measured=null;
    if(currentState==='SPEAKING'&&playback)measured=readSpectrum(playback.meter);
    else if(currentState==='LISTENING'&&voice.enabled){if(voice.backend==='native'&&native&&Date.now()-native.received<CONFIG.staleMs)measured=native;else if(browserMic)measured=readSpectrum(browserMic.meter);}
    spectrum=measured?.bands||new Array(CONFIG.bands).fill(0);
    const amplitude=measured?.rms||0;instrument.style.setProperty('--orb-level',String(amplitude));
    renderParticles(now,delta,amplitude);
    if(!particleView)VISUAL.fallbackRingRadii.forEach((radius,index)=>{
      const turn=reduced.matches?0:now/VISUAL.ringTurnMs*Math.PI*2*VISUAL.fallbackRingSpeeds[index];
      ctx.strokeStyle=ink;ctx.lineWidth=CONFIG.waveformWidth;ctx.globalAlpha=VISUAL.fallbackAlpha;ctx.beginPath();ctx.arc(center,center,size*radius,turn,turn+Math.PI*VISUAL.fallbackArc);ctx.stroke();
    });
    if(measured&&!reduced.matches)for(let i=0;i<CONFIG.ticks;i++){
      const energy=spectrum[Math.floor(i/CONFIG.ticks*CONFIG.bands)],angle=i/CONFIG.ticks*Math.PI*2-Math.PI/2;
      line(angle,size*VISUAL.signalRadius,energy*size*VISUAL.signalDepth,CONFIG.waveformWidth,ink,.35+energy*.65,size);
    }
    if(currentState==='EXECUTING'){
      const start=-Math.PI/2+(reduced.matches?0:sim.rot);
      const span=currentState==='EXECUTING'&&explicit?.progress!==null?Math.PI*2*(explicit?.progress||0):Math.PI*VISUAL.progressArc;
      ctx.globalAlpha=.8;ctx.strokeStyle=ink;ctx.lineWidth=CONFIG.progressWidth;ctx.beginPath();ctx.arc(center,center,size*VISUAL.progressRadius,start,start+span);ctx.stroke();
    }
    ctx.globalAlpha=1;
    if(explicit?.target&&currentState==='AWAITING APPROVAL'){
      const target=document.querySelector(explicit.target),path=$('.orb-approval-line path');
      if(target){const from=instrument.getBoundingClientRect(),to=target.getBoundingClientRect();path.setAttribute('d',`M ${size/2} 0 L ${to.left+to.width/2-from.left} ${to.bottom-from.top}`);}
    }
  }
  const brainChip=document.getElementById('brain-chip');
  const themeObserver=new MutationObserver(()=>{dirty=true;});themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','data-motion','class']});
  const motionChanged=()=>{dirty=true;};motionQuery.addEventListener('change',motionChanged);
  if(brainChip){new MutationObserver(()=>setModel(brainChip.textContent.replace(/\s*▾\s*$/,''))).observe(brainChip,{childList:true,subtree:true});setModel(brainChip.textContent.replace(/\s*▾\s*$/,''));}
  window.JarvisOrb={wake,updateVoice,nativeAudio,setStatus,setModel,playSpeech,stopSpeech,unlockSpeech:()=>localSpeech.unlock(),warmSpeech:()=>localSpeech.warm(),microphoneDevice:()=>window.JarvisVoice?.inputDevice?.()??$('.orb-device').value,
    setConnected(value){const next=Boolean(value);if(next&&!connected)wake();connected=next;sync();},
    snapshot:()=>({state:currentState,visualMode:preview||visualMode(),preview:!!preview,renderer:particleView?'webgl':'fallback',micEnabled:voice.enabled,audioSource:playback?'local-daniel-output':voice.enabled&&voice.backend==='native'&&native?'native-microphone':browserMic?'browser-microphone':null,spectrum:!!playback||!!browserMic||!!(voice.enabled&&native&&Date.now()-native.received<CONFIG.staleMs)}),
    destroy(){stopped=true;cancelAnimationFrame(animationFrame);clearTimeout(previewTimer);resizeObserver.disconnect();themeObserver.disconnect();motionQuery.removeEventListener('change',motionChanged);stopSpeech();stopMicMeter();audioContext?.close();particleView?.resources.forEach(resource=>resource.dispose());particleView?.renderer.dispose();}};
  window.addEventListener('pagehide',()=>{stopSpeech();stopMicMeter();});
  resize();sync();initParticles();animationFrame=requestAnimationFrame(draw);
})();
