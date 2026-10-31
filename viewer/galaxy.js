/* Concept G's GalaxyStage, ported to vanilla Three.js on the existing app scene.
 * Only the dust is decorative. Every foreground point and edge comes from getData(). */
const GALAXY = Object.freeze({cache:'jarvis.galaxy.layout.spiral.v1',idleSettleMs:240,layoutMs:600,captureSettleMs:2000,workerTimeout:1600,
  dustCount:9000,dustRadius:15,dustArms:3,dustTwist:.42,dustSeed:11,dustSpread:.5,
  dustDistribution:.7,dustAngleSpread:.28,dustHeight:.35,dustCenterHeight:.2,dustColorBase:.22,dustColorSpread:.25,dustColorDim:.45,dustSizeBase:.25,dustSizeSpread:.45,
  clusterRadius:5.2,clusterRadiusSpread:1.6,clusterAngle:.4,clusterSpread:1.1,clusterHeight:1.2,clusterXZ:1.3,clusterY:.6,phaseRange:6.28,
  damping:.08,pitchMin:-.35,pitchMax:.5,dragRadiansPerPixel:.004,cameraFov:40,cameraNear:.1,cameraFar:2000,initialHeight:5,background:'#030509',lightBackground:'#F4F3F0',lightInk:.22,
  labelDepth:32,labelOpacityMin:.25,labelFalloff:1.6,labelCharWidth:7,performanceQuantile:.95,noteStateDim:.6,linkStateDim:.7,
  tilt:.42,rotationSpeed:.025,maxDelta:.05,dimLerp:.05,thinkingDim:.55,offlineDim:.5,
  labelLimit:14,labelsPerGroup:2,labelOffsetX:10,labelOffsetY:-8,labelHeight:18,labelGap:8,
  hitCell:32,hitRadius:14,clickDistance:5,projectionIntervalMs:16,performanceSamples:180,reportMs:1000,
  zoomStep:1.2,zoomMs:600,minDistance:7,maxDistance:75,fitPadding:1.35,
  starSize:2.6,smallStarSize:1.5,selectedScale:1.5,hoverScale:1.2,dim:.25,
  edgeBrightness:.2,focusOpacity:.35,sparkSize:1.3,sparkSpeed:.25,sparkOffset:.13,
  pixelRatioLimit:2,denseNodes:5000,densePixelRatio:1.25,detachFrames:4,
  bloomIntensity:.9,bloomThreshold:.18,bloomSmoothing:.3,bloomRadius:.7,
  palette:{projects:'#f2a65a',research:'#7c9cff',finance:'#a78bfa',health:'#5eead4',people:'#f472b6',ideas:'#d9e06a',captures:'#9aa8ad',
    operations:'#f2a65a',marketing:'#7c9cff',strategy:'#d9e06a',team:'#f472b6',sales:'#f472b6'},
  fallback:['#f2a65a','#7c9cff','#a78bfa','#5eead4','#f472b6','#d9e06a','#9aa8ad']});
// Unchanged point shaders from the active Concept C GalaxyStage used by Concept G.
const PT_VS = `
attribute float aSize; attribute vec3 aColor; attribute float aPhase;
uniform float uPx, uT, uDim; varying vec3 vC; varying float vA;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  float tw = 0.8 + 0.2 * sin(uT * 1.4 + aPhase);
  gl_PointSize = aSize * uPx * (300.0 / -mv.z) * tw;
  vC = aColor; vA = (1.0 - uDim) * clamp(28.0 / -mv.z, 0.35, 1.0);
}`;
const PT_FS = `
varying vec3 vC; varying float vA;
void main() {
  float d = length(gl_PointCoord - 0.5) * 2.0;
  if (d > 1.0) discard;
  float core = smoothstep(0.32, 0.0, d);
  float halo = exp(-d * d * 5.0) * 0.55;
  gl_FragColor = vec4(mix(vC * 1.35, vec3(1.0), core * 0.3) * (core + halo), (core + halo) * vA);
}`;
function hash(text){let value=2166136261;for(let i=0;i<text.length;i++)value=Math.imul(value^text.charCodeAt(i),16777619);return value>>>0;}
function identity(node){return `${node.group}\u0000${node.label}`;}
function endId(value){return typeof value==='object'?value.id:value;}
function key(link){return `${Math.min(endId(link.source),endId(link.target))}:${Math.max(endId(link.source),endId(link.target))}`;}
function rng(seed){return ()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};}
function gauss(R){return Math.sqrt(-2*Math.log(R()+1e-9))*Math.cos(2*Math.PI*R());}
export function categoryColor(group){return GALAXY.palette[String(group).toLowerCase()]||GALAXY.fallback[hash(String(group))%GALAXY.fallback.length];}
export function hudEase(t){return 1-Math.pow(1-Math.max(0,Math.min(1,t)),3);}
function cacheRead(storageKey){try{return JSON.parse(localStorage.getItem(storageKey)||'{}')||{};}catch{return {};}}
export function seedPositions(nodes,{storageKey=GALAXY.cache}={}){
  const cached=cacheRead(storageKey),categories=[...new Set(nodes.map(n=>n.group))].sort((a,b)=>GALAXY.fallback.indexOf(categoryColor(a))-GALAXY.fallback.indexOf(categoryColor(b))||a.localeCompare(b));
  const centers=new Map(categories.map((group,i)=>{const R=rng(hash(group)),a=i/categories.length*Math.PI*2+GALAXY.clusterAngle,r=i===0?0:GALAXY.clusterRadius+R()*GALAXY.clusterRadiusSpread;return [group,{x:Math.cos(a)*r,y:(R()-.5)*GALAXY.clusterHeight,z:Math.sin(a)*r}];}));
  nodes.forEach(node=>{const saved=cached[identity(node)],R=rng(hash(identity(node))),c=centers.get(node.group),s=GALAXY.clusterSpread;
    const p=saved&&['x','y','z'].every(k=>Number.isFinite(saved[k]))?saved:{x:c.x+gauss(R)*s*GALAXY.clusterXZ,y:c.y+gauss(R)*s*GALAXY.clusterY,z:c.z+gauss(R)*s*GALAXY.clusterXZ};
    for(const axis of ['x','y','z'])node[axis]=node['f'+axis]=p[axis];
  });
}
export function createGalaxy({graph,THREE,getData,stage,labels,onSelect,onClear,onGroup,flyCamera,reducedMotion,initialDistance,storageKey=GALAXY.cache}){
  const scene=graph.scene(),camera=graph.camera(),renderer=graph.renderer(),controls=graph.controls(),host=stage.querySelector('#graph');
  const root=new THREE.Group();root.rotation.x=GALAXY.tilt;scene.add(root);
  const vector=new THREE.Vector3(),projected=new Map(),spatial=new Map(),labelElements=new Map(),labelWidths=new Map();
  const stats={frames:0,drawCalls:0,triangles:0,hoverMs:0,maxHoverMs:0,frameMs:0,p95FrameMs:0,layoutWorker:false,layoutPersisted:false,idle:true,nodes:0,links:0,firstFrameMs:null,bufferMs:0,projectionMs:0,renderMs:0,bloom:false};
  const started=performance.now(),frameSamples=[],reserveIds=['chat-form','jarvis-orb-host','groups','detail','selected-note','answer-card'];
  let nodes=[],links=[],nodeRef,linkRef,stars,edges,focusEdges,sparks,starSizes=[],labelIds=[],edgeKeys=[],detachRemaining=0;
  let selected=null,highlighted=new Set(),activeLinks=new Set(),activeGroup=null,hovered=null,hoverNeighbors=new Set(),hoverLinks=new Set(),preview=null;
  let frame=null,ticking=false,resuming=false,disposed=false,awakeUntil=0,lastFrame=0,lastReport=0,lastProjection=0,projectionDirty=true,spatialColumns=0;
  let projectedWidth=0,projectedHeight=0,pointerDown=null,pickPending=null,interactionSource='system',continuous=false,speechBusy=false,visible=true,worker=null,workerTimer=null;
  let composer=null,composerWidth=0,composerHeight=0,composerDpr=0;
  const settleTimers=new Set(),motions=new Map();
  const nativeComposer=graph.postProcessingComposer?.(),nativeComposerRender=nativeComposer?.render;
  renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.NoToneMapping;scene.background=new THREE.Color(GALAXY.background);
  // Browser-ready local module. The launcher needs no package manager or build.
  import('/postprocessing.module.js').then(({EffectComposer,RenderPass,EffectPass,BloomEffect})=>{
    if(disposed)return;
    composer=new EffectComposer(renderer,{multisampling:0,frameBufferType:THREE.HalfFloatType});
    composer.addPass(new RenderPass(scene,camera));
    const bloom=new BloomEffect({mipmapBlur:true,intensity:GALAXY.bloomIntensity,luminanceThreshold:GALAXY.bloomThreshold,luminanceSmoothing:GALAXY.bloomSmoothing,radius:GALAXY.bloomRadius});
    composer.addPass(new EffectPass(camera,bloom));
    if(nativeComposer){nativeComposer.render=()=>{const began=performance.now(),autoReset=renderer.info.autoReset;renderer.info.autoReset=false;renderer.info.reset();try{composer.render();}finally{renderer.info.autoReset=autoReset;stats.renderMs=performance.now()-began;}};stats.bloom=true;}
    wake();
  }).catch(error=>console.error('Galaxy bloom unavailable:',error));
  function material(){return new THREE.ShaderMaterial({uniforms:{uPx:{value:renderer.getPixelRatio()},uT:{value:0},uDim:{value:0}},vertexShader:PT_VS,fragmentShader:PT_FS,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending});}
  const starMaterial=material(),dustMaterial=material(),sparkMaterial=material();
  const edgeMaterial=new THREE.LineBasicMaterial({vertexColors:true,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending});
  const focusMaterial=new THREE.LineBasicMaterial({color:'#5eead4',transparent:true,opacity:GALAXY.focusOpacity,depthWrite:false,blending:THREE.AdditiveBlending});
  const lightMode=()=>document.documentElement.dataset.theme==='light';
  function applyTheme(){const light=lightMode();scene.background=new THREE.Color(light?GALAXY.lightBackground:GALAXY.background);for(const mat of [starMaterial,dustMaterial,sparkMaterial,edgeMaterial,focusMaterial]){mat.blending=light?THREE.NormalBlending:THREE.AdditiveBlending;mat.needsUpdate=true;}if(stars)updateBuffers();wake();}
  const themeObserver=new MutationObserver(applyTheme);themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  const emit=(name,detail)=>window.dispatchEvent(new CustomEvent('jarvis:'+name,{detail}));
  function geometry(count){const g=new THREE.BufferGeometry();for(const [name,size] of [['position',3],['aColor',3],['aSize',1],['aPhase',1]])g.setAttribute(name,new THREE.BufferAttribute(new Float32Array(count*size),size).setUsage(THREE.DynamicDrawUsage));return g;}
  function remove(object){if(object){root.remove(object);object.geometry.dispose();}}
  const dustGeometry=geometry(GALAXY.dustCount),R=rng(GALAXY.dustSeed),tints=['#5eead4','#7c9cff','#a78bfa','#e8eff1'].map(c=>new THREE.Color(c));
  for(let i=0;i<GALAXY.dustCount;i++){
    const r=Math.pow(R(),GALAXY.dustDistribution)*GALAXY.dustRadius,arm=Math.floor(R()*GALAXY.dustArms),a=arm*Math.PI*2/GALAXY.dustArms+r*GALAXY.dustTwist+gauss(R)*GALAXY.dustAngleSpread;
    dustGeometry.attributes.position.setXYZ(i,Math.cos(a)*r+gauss(R)*GALAXY.dustSpread,gauss(R)*(GALAXY.dustHeight+GALAXY.dustCenterHeight*(1-r/GALAXY.dustRadius)),Math.sin(a)*r+gauss(R)*GALAXY.dustSpread);
    const c=tints[Math.floor(R()*tints.length)].clone().multiplyScalar((GALAXY.dustColorBase+R()*GALAXY.dustColorSpread)*GALAXY.dustColorDim);dustGeometry.attributes.aColor.setXYZ(i,c.r,c.g,c.b);
    dustGeometry.attributes.aSize.setX(i,GALAXY.dustSizeBase+R()*GALAXY.dustSizeSpread);dustGeometry.attributes.aPhase.setX(i,R()*GALAXY.phaseRange);
  }
  const dust=new THREE.Points(dustGeometry,dustMaterial);dust.frustumCulled=false;root.add(dust);
  function build(){
    const previousIds=new Set(nodes.map(n=>n.id)),firstBuild=!nodeRef,data=getData(),cached=cacheRead(storageKey);
    const desired=data.nodes.map(n=>({...n}));seedPositions(desired,{storageKey});
    nodes=data.nodes;links=data.links;
    nodes.forEach((n,i)=>{const target=desired[i];if(![n.x,n.y,n.z].every(Number.isFinite)){for(const axis of ['x','y','z'])n[axis]=n['f'+axis]=target[axis];}
      else if(!firstBuild&&!previousIds.has(n.id)&&!cached[identity(n)]){const timer=setTimeout(()=>{settleTimers.delete(timer);if(disposed)return;motions.set(n.id,{start:performance.now(),from:{x:n.x,y:n.y,z:n.z},to:target});wake(GALAXY.layoutMs);},GALAXY.captureSettleMs);settleTimers.add(timer);}});nodeRef=nodes;linkRef=links;stats.nodes=nodes.length;stats.links=links.length;detachRemaining=GALAXY.detachFrames;
    renderer.setPixelRatio(Math.min(devicePixelRatio||1,nodes.length>=GALAXY.denseNodes?GALAXY.densePixelRatio:GALAXY.pixelRatioLimit));
    remove(stars);remove(edges);remove(focusEdges);remove(sparks);
    const degrees=new Map(nodes.map(n=>[n.id,0]));for(const l of links){degrees.set(endId(l.source),(degrees.get(endId(l.source))||0)+1);degrees.set(endId(l.target),(degrees.get(endId(l.target))||0)+1);}
    const primary=[];for(const group of new Set(nodes.map(n=>n.group)))primary.push(...nodes.filter(n=>n.group===group).sort((a,b)=>(degrees.get(b.id)-degrees.get(a.id))||a.id-b.id).slice(0,GALAXY.labelsPerGroup));
    labelIds=primary.slice(0,GALAXY.labelLimit).map(n=>n.id);
    starSizes=nodes.map(n=>labelIds.includes(n.id)?GALAXY.starSize:GALAXY.smallStarSize+hash(identity(n))/4294967296);
    stars=new THREE.Points(geometry(nodes.length),starMaterial);stars.frustumCulled=false;root.add(stars);
    const lineGeometry=new THREE.BufferGeometry();lineGeometry.setAttribute('position',new THREE.BufferAttribute(new Float32Array(links.length*6),3));lineGeometry.setAttribute('color',new THREE.BufferAttribute(new Float32Array(links.length*6),3));
    edges=new THREE.LineSegments(lineGeometry,edgeMaterial);edges.frustumCulled=false;root.add(edges);
    focusEdges=new THREE.LineSegments(new THREE.BufferGeometry(),focusMaterial);focusEdges.frustumCulled=false;root.add(focusEdges);
    sparks=new THREE.Points(geometry(links.length*2),sparkMaterial);sparks.frustumCulled=false;root.add(sparks);edgeKeys=links.map(key);
    for(const element of labelElements.values())element.remove();labelElements.clear();labelWidths.clear();
    for(const node of nodes){if(!labels)break;const e=document.createElement('span');e.className='galaxy-note-label';e.textContent=node.label;e.hidden=true;labels.append(e);labelElements.set(node.id,e);}
    projected.clear();projectionDirty=true;updateBuffers();persist(nodes.filter(n=>firstBuild||previousIds.has(n.id)).map(n=>({...n,key:identity(n)})));requestLayout();
  }
  function updateBuffers(){const began=performance.now(),byId=new Map(nodes.map(n=>[n.id,n])),focusLinks=[];
    nodes.forEach((n,i)=>{const a=stars.geometry.attributes,c=new THREE.Color(categoryColor(n.group)),lit=(!highlighted.size||highlighted.has(n.id))&&(!activeGroup||activeGroup===n.group)&&(!preview||preview===n.group)&&(!hoverNeighbors.size||hoverNeighbors.has(n.id));
      c.multiplyScalar((lit?1:GALAXY.dim)*(lightMode()?GALAXY.lightInk:1));a.position.setXYZ(i,n.x||0,n.y||0,n.z||0);a.aColor.setXYZ(i,c.r,c.g,c.b);a.aSize.setX(i,starSizes[i]*(n.id===selected?GALAXY.selectedScale:n.id===hovered?GALAXY.hoverScale:1));a.aPhase.setX(i,hash(identity(n))/4294967296*GALAXY.phaseRange);
    });
    links.forEach((link,i)=>{const a=byId.get(endId(link.source)),b=byId.get(endId(link.target));if(!a||!b)return;
      const attrs=edges.geometry.attributes,c=new THREE.Color(categoryColor(a.group)).multiplyScalar(GALAXY.edgeBrightness*((highlighted.size&&(!highlighted.has(a.id)||!highlighted.has(b.id)))||(activeGroup&&a.group!==activeGroup&&b.group!==activeGroup)?GALAXY.dim:1));
      attrs.position.setXYZ(i*2,a.x,a.y,a.z);attrs.position.setXYZ(i*2+1,b.x,b.y,b.z);attrs.color.setXYZ(i*2,c.r,c.g,c.b);attrs.color.setXYZ(i*2+1,c.r,c.g,c.b);
      if((hovered!==null?hoverLinks:activeLinks).has(edgeKeys[i]))focusLinks.push([a,b]);
    });
    focusEdges.userData.links=focusLinks;focusEdges.geometry.setAttribute('position',new THREE.Float32BufferAttribute(focusLinks.flatMap(([a,b])=>[a.x,a.y,a.z,b.x,b.y,b.z]),3));
    sparks.geometry.setDrawRange(0,focusLinks.length*2);const sa=sparks.geometry.attributes;
    for(let i=0;i<focusLinks.length*2;i++){sa.aColor.setXYZ(i,.6,1,.95);sa.aSize.setX(i,GALAXY.sparkSize);}
    for(const a of Object.values(stars.geometry.attributes))a.needsUpdate=true;for(const a of Object.values(edges.geometry.attributes))a.needsUpdate=true;for(const a of Object.values(sa))a.needsUpdate=true;
    stats.bufferMs=performance.now()-began;
  }
  function worldPosition(node){return root.localToWorld(new THREE.Vector3(node.x||0,node.y||0,node.z||0));}
  function project(node,output={},width=stage.clientWidth,height=stage.clientHeight){if(!node)return null;vector.set(node.x||0,node.y||0,node.z||0).applyMatrix4(root.matrixWorld);output.depth=vector.distanceTo(camera.position);vector.project(camera);output.x=(vector.x+1)*width/2;output.y=(1-vector.y)*height/2;output.visible=vector.z>-1&&vector.z<1;return output;}
  function reserved(){const bounds=stage.getBoundingClientRect();return reserveIds.map(id=>document.getElementById(id)).filter(e=>e&&!e.hidden&&e.getClientRects().length&&!(e.id==='answer-card'&&e.dataset.idle==='true')).map(e=>{const r=e.getBoundingClientRect();return {left:r.left-bounds.left,top:r.top-bounds.top,right:r.right-bounds.left,bottom:r.bottom-bounds.top};});}
  const overlaps=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;
  function placeLabels(){const began=performance.now();projectedWidth=stage.clientWidth;projectedHeight=stage.clientHeight;spatialColumns=Math.ceil(projectedWidth/GALAXY.hitCell)+2;spatial.clear();const occupied=reserved();
    // Read visible widths together, before hiding or moving labels, to avoid a layout per note.
    for(const [id,e]of labelElements)if(!e.hidden)labelWidths.set(id,e.offsetWidth);
    for(const node of nodes){const p=project(node,projected.get(node.id)||{node});projected.set(node.id,p);if(!p.visible)continue;
      if(p.x>=-GALAXY.hitRadius&&p.x<=projectedWidth+GALAXY.hitRadius&&p.y>=-GALAXY.hitRadius&&p.y<=projectedHeight+GALAXY.hitRadius){const cell=Math.floor(p.x/GALAXY.hitCell)+1+(Math.floor(p.y/GALAXY.hitCell)+1)*spatialColumns;if(!spatial.has(cell))spatial.set(cell,[]);spatial.get(cell).push(p);}
    }
    const order=[selected,hovered,...labelIds].filter((id,i,all)=>id!==null&&all.indexOf(id)===i);for(const e of labelElements.values())e.hidden=true;
    for(const id of order){const p=projected.get(id),e=labelElements.get(id);if(!p?.visible||!e)continue;const x=p.x+GALAXY.labelOffsetX,y=p.y+GALAXY.labelOffsetY,w=labelWidths.get(id)||e.textContent.length*GALAXY.labelCharWidth;
      const box={left:x,top:y,right:x+w,bottom:y+GALAXY.labelHeight};if(x<0||box.right>projectedWidth||y<0||box.bottom>projectedHeight||occupied.some(r=>overlaps(box,r)))continue;
      e.hidden=false;e.style.transform=`translate3d(${Math.round(x)}px,${Math.round(y)}px,0)`;e.style.opacity=Math.max(GALAXY.labelOpacityMin,Math.min(1,GALAXY.labelFalloff-p.depth/GALAXY.labelDepth));e.classList.toggle('active',id===selected||id===hovered);occupied.push({...box,bottom:box.bottom+GALAXY.labelGap});
    }
    const zoom=Math.round(initialDistance/camera.position.distanceTo(controls.target)*100),out=document.getElementById('zoom-value');if(out)out.textContent=zoom+'%';emit('camera',{zoom,selected:projected.get(selected)||null});stats.projectionMs=performance.now()-began;
  }
  function pick(x,y){const started=performance.now(),cx=Math.floor(x/GALAXY.hitCell),cy=Math.floor(y/GALAXY.hitCell);let best=null,distance=GALAXY.hitRadius*GALAXY.hitRadius;
    for(let dx=-1;dx<=1;dx++)for(let dy=-1;dy<=1;dy++){const gx=cx+dx+1,gy=cy+dy+1;if(gx<0||gx>=spatialColumns||gy<0)continue;const cell=spatial.get(gx+gy*spatialColumns);if(!cell)continue;for(let i=0;i<cell.length;i++){const point=cell[i],d=(point.x-x)*(point.x-x)+(point.y-y)*(point.y-y);if(d<distance){best=point.node;distance=d;}}}
    stats.hoverMs=performance.now()-started;stats.maxHoverMs=Math.max(stats.maxHoverMs,stats.hoverMs);return best;
  }
  function showHover(node){if((node?.id??null)===hovered)return;hovered=node?.id??null;hoverNeighbors=new Set(node?[node.id]:[]);hoverLinks=new Set();
    if(node)for(const l of links)if(endId(l.source)===node.id||endId(l.target)===node.id){hoverNeighbors.add(endId(l.source));hoverNeighbors.add(endId(l.target));hoverLinks.add(key(l));}
    host.style.cursor=node?'pointer':'';projectionDirty=true;updateBuffers();emit('hover',{node:node||null});
  }
  // Leave a settled galaxy on screen without occupying the shared M1 GPU.
  // Interaction, layout work and an explicitly enabled orbit still animate.
  function needsFrames(now){return !document.hidden&&visible&&(now<awakeUntil||motions.size>0||detachRemaining>0||!speechBusy&&!reducedMotion&&continuous);}
  function frameTick(now){frame=null;if(disposed)return;ticking=true;const dt=lastFrame?Math.min(GALAXY.maxDelta,(now-lastFrame)/1000):0;
    if(lastFrame){frameSamples.push(now-lastFrame);if(frameSamples.length>GALAXY.performanceSamples)frameSamples.shift();}lastFrame=now;
    if(getData().nodes!==nodeRef||getData().links!==linkRef)build();
    if(motions.size){for(const [id,motion]of motions){const n=nodes.find(n=>n.id===id);if(!n){motions.delete(id);continue;}const t=Math.min(1,(now-motion.start)/GALAXY.layoutMs),e=hudEase(t);for(const axis of ['x','y','z'])n[axis]=n['f'+axis]=motion.from[axis]+(motion.to[axis]-motion.from[axis])*e;if(t===1)motions.delete(id);}updateBuffers();projectionDirty=true;if(!motions.size)persist(nodes.map(n=>({...n,key:identity(n)})));}
    if(detachRemaining>0){detachRemaining--;for(const n of nodes)if(n.__threeObj?.parent)n.__threeObj.parent.remove(n.__threeObj);}
    if(continuous&&!reducedMotion&&!pointerDown){root.rotation.y+=dt*GALAXY.rotationSpeed;root.updateMatrixWorld(true);projectionDirty=true;}
    const mode=window.JarvisOrb?.snapshot?.().visualMode,dim=mode==='offline'?GALAXY.offlineDim:['thinking','speaking'].includes(mode)?GALAXY.thinkingDim:0;
    for(const mat of [starMaterial,dustMaterial,sparkMaterial]){mat.uniforms.uPx.value=renderer.getPixelRatio();mat.uniforms.uT.value=reducedMotion?0:now/1000;}
    starMaterial.uniforms.uDim.value+=(dim*GALAXY.noteStateDim-starMaterial.uniforms.uDim.value)*GALAXY.dimLerp;edgeMaterial.opacity+=(1-dim*GALAXY.linkStateDim-edgeMaterial.opacity)*GALAXY.dimLerp;
    (focusEdges.userData.links||[]).forEach(([a,b],k)=>{for(let m=0;m<2;m++){const u=reducedMotion?.5:(now/1000*GALAXY.sparkSpeed+k*GALAXY.sparkOffset+m*.5)%1;sparks.geometry.attributes.position.setXYZ(k*2+m,a.x+(b.x-a.x)*u,a.y+(b.y-a.y)*u,a.z+(b.z-a.z)*u);}});sparks.geometry.attributes.position.needsUpdate=true;
    if(composer&&(composerWidth!==stage.clientWidth||composerHeight!==stage.clientHeight||composerDpr!==renderer.getPixelRatio())){composerWidth=stage.clientWidth;composerHeight=stage.clientHeight;composerDpr=renderer.getPixelRatio();composer.setSize(composerWidth,composerHeight);}
    controls.rotateSpeed=GALAXY.dragRadiansPerPixel*stage.clientHeight/(Math.PI*2);
    if((projectionDirty&&now-lastProjection>=GALAXY.projectionIntervalMs)||projectedWidth!==stage.clientWidth||projectedHeight!==stage.clientHeight){placeLabels();projectionDirty=false;lastProjection=now;}
    if(pickPending&&!pointerDown){const p=pickPending;pickPending=null;showHover(pick(p.x,p.y));}
    stats.frames++;stats.drawCalls=renderer.info.render.calls;stats.triangles=renderer.info.render.triangles;if(stats.firstFrameMs===null)stats.firstFrameMs=Math.max(0,now-started);
    if(now-lastReport>GALAXY.reportMs){lastReport=now;const sorted=[...frameSamples].sort((a,b)=>a-b);stats.frameMs=frameSamples.reduce((a,b)=>a+b,0)/Math.max(1,frameSamples.length);stats.p95FrameMs=sorted[Math.floor(sorted.length*GALAXY.performanceQuantile)]||0;host.dataset.performance=JSON.stringify(stats);}
    const active=needsFrames(now);ticking=false;
    if(active){if(frame===null)frame=requestAnimationFrame(frameTick);}else{stats.idle=true;lastFrame=0;graph.pauseAnimation();}
  }
  function wake(duration=GALAXY.idleSettleMs){if(disposed||document.hidden||!visible)return;awakeUntil=Math.max(awakeUntil,performance.now()+duration);stats.idle=false;if(!resuming){resuming=true;try{graph.resumeAnimation();}finally{resuming=false;}}if(frame===null&&!ticking)frame=requestAnimationFrame(frameTick);}
  function persist(positions){const saved={};for(const p of positions)saved[p.key]={x:p.x,y:p.y,z:p.z};try{localStorage.setItem(storageKey,JSON.stringify(saved));stats.layoutPersisted=true;}catch{stats.layoutPersisted=false;}}
  function requestLayout(){if(!worker)return;clearTimeout(workerTimer);workerTimer=setTimeout(()=>stats.layoutWorker=false,GALAXY.workerTimeout);worker.postMessage({nodes:nodes.map(({id,label,group})=>({id,label,group})),cached:cacheRead(storageKey),revision:1});}
  try{worker=new Worker(new URL('./layout-worker.js',import.meta.url));worker.onmessage=()=>{clearTimeout(workerTimer);stats.layoutWorker=true;};worker.onerror=()=>{stats.layoutWorker=false;worker?.terminate();worker=null;};}catch{}
  function zoom(direction){const target=controls.target,delta=camera.position.clone().sub(target),length=Math.max(GALAXY.minDistance,Math.min(GALAXY.maxDistance,delta.length()*(direction>0?1/GALAXY.zoomStep:GALAXY.zoomStep)));delta.setLength(length).add(target);flyCamera(delta,target,GALAXY.zoomMs);wake(GALAXY.zoomMs);}
  function fit(){onClear();flyCamera({x:0,y:GALAXY.initialHeight,z:Math.max(initialDistance,initialDistance/(stage.clientWidth/stage.clientHeight)*GALAXY.fitPadding)},{x:0,y:0,z:0});wake(GALAXY.zoomMs);}
  function selectionDetail(source){return {node:selected===null?null:nodes.find(n=>n.id===selected),source,open:source!=='pointer'};}
  function selectNode(node,source){interactionSource=source;onSelect(node,{fly:source!=='pointer',expand:source!=='pointer'});interactionSource='system';emit('selection',selectionDetail(source));projectionDirty=true;wake();}
  function pointerMove(e){const r=stage.getBoundingClientRect();pickPending={x:e.clientX-r.left,y:e.clientY-r.top};}
  function pointerStart(e){pointerDown={x:e.clientX,y:e.clientY};controls.mouseButtons.LEFT=e.shiftKey?THREE.MOUSE.PAN:THREE.MOUSE.ROTATE;showHover(null);wake();}
  function pointerEnd(e){if(!pointerDown)return;const moved=Math.hypot(e.clientX-pointerDown.x,e.clientY-pointerDown.y);pointerDown=null;if(moved<GALAXY.clickDistance&&e.button===0){placeLabels();const r=stage.getBoundingClientRect(),n=pick(e.clientX-r.left,e.clientY-r.top);if(n)selectNode(n,'pointer');else onClear();}wake();}
  function doubleClick(e){const r=stage.getBoundingClientRect(),n=pick(e.clientX-r.left,e.clientY-r.top);if(n){e.preventDefault();selectNode(n,'doubleclick');}}
  function keyDown(e){if(e.defaultPrevented||e.metaKey||e.ctrlKey||e.altKey||e.target.closest?.('input,textarea,select,[contenteditable="true"],dialog'))return;
    if(e.key==='Escape'){onClear();return;}if(e.key.toLowerCase()==='f'&&e.shiftKey){e.preventDefault();fit();return;}
    if(e.key==='Tab'&&(e.target===host||e.target===renderer.domElement)){e.preventDefault();const categories=[...new Set(nodes.map(n=>n.group))],i=categories.indexOf(activeGroup);onGroup(categories[(i+(e.shiftKey?-1:1)+categories.length)%categories.length]);}
    if(e.key.startsWith('Arrow')&&selected!==null){const candidates=links.filter(l=>endId(l.source)===selected||endId(l.target)===selected).map(l=>nodes.find(n=>n.id===(endId(l.source)===selected?endId(l.target):endId(l.source)))).filter(Boolean);if(candidates.length){e.preventDefault();candidates.sort((a,b)=>{const pa=projected.get(a.id),pb=projected.get(b.id);return e.key==='ArrowLeft'?pa.x-pb.x:e.key==='ArrowRight'?pb.x-pa.x:e.key==='ArrowUp'?pa.y-pb.y:pb.y-pa.y;});selectNode(candidates[0],'keyboard');}}
  }
  function visibility(){if(document.hidden||!visible){if(frame!==null)cancelAnimationFrame(frame);frame=null;graph.pauseAnimation();stats.idle=true;host.dataset.paused='true';lastFrame=0;}else{host.dataset.paused='false';wake();}}
  const controlChange=()=>{projectionDirty=true;wake();},pointerLeave=()=>{pointerDown=null;showHover(null);};
  controls.enableDamping=!reducedMotion;controls.dampingFactor=GALAXY.damping;controls.minDistance=GALAXY.minDistance;controls.maxDistance=GALAXY.maxDistance;const initialPolar=Math.atan2(camera.position.z,camera.position.y);controls.minPolarAngle=initialPolar-GALAXY.pitchMax;controls.maxPolarAngle=initialPolar-GALAXY.pitchMin;controls.rotateSpeed=GALAXY.dragRadiansPerPixel*stage.clientHeight/(Math.PI*2);controls.minAzimuthAngle=-Infinity;controls.maxAzimuthAngle=Infinity;controls.zoomToCursor=false;controls.mouseButtons.RIGHT=THREE.MOUSE.PAN;
  camera.fov=GALAXY.cameraFov;camera.near=GALAXY.cameraNear;camera.far=GALAXY.cameraFar;camera.updateProjectionMatrix();root.updateMatrixWorld(true);
  host.tabIndex=0;host.setAttribute('role','application');host.setAttribute('aria-description','Drag to orbit; scroll to zoom. F toggles full view, Shift F fits notes. Arrow keys follow selected links. Escape clears selection.');
  const events=[['pointermove',pointerMove],['pointerdown',pointerStart],['pointerup',pointerEnd],['pointerleave',pointerLeave],['dblclick',doubleClick]];for(const [name,fn]of events)host.addEventListener(name,fn);
  document.addEventListener('keydown',keyDown);document.addEventListener('visibilitychange',visibility);controls.addEventListener('change',controlChange);
  const zoomIn=()=>zoom(1),zoomOut=()=>zoom(-1),pinsChanged=()=>{updateBuffers();wake();},pageHide=()=>api.destroy();
  document.getElementById('zoom-in')?.addEventListener('click',zoomIn);document.getElementById('zoom-out')?.addEventListener('click',zoomOut);document.getElementById('fit')?.addEventListener('click',fit);
  window.addEventListener('jarvis:pins',pinsChanged);window.addEventListener('pagehide',pageHide);
  build();applyTheme();wake(GALAXY.layoutMs);
  const api={wake,project,worldPosition,fit,zoom,categoryColor,ease:hudEase,boot(){wake();},setReducedMotion(value){reducedMotion=Boolean(value);controls.enableDamping=!reducedMotion;wake();},setContinuous(value){continuous=Boolean(value);wake();},setSpeechBusy(value){const next=Boolean(value);if(next!==speechBusy){speechBusy=next;wake();}},setVisible(value){visible=Boolean(value);visibility();},
    refresh(){if(getData().nodes!==nodeRef||getData().links!==linkRef)build();else updateBuffers();wake();},
    highlight(state){const changed=selected!==state.selected;selected=state.selected;highlighted=state.highlighted;activeLinks=state.activeLinks;activeGroup=state.activeGroup;updateBuffers();projectionDirty=true;if(changed&&interactionSource==='system')emit('selection',selectionDetail('system'));wake();},previewGroup(group){preview=group;updateBuffers();projectionDirty=true;wake();},
    get performance(){return {...stats};},
    destroy(){if(disposed)return;disposed=true;themeObserver.disconnect();if(frame!==null)cancelAnimationFrame(frame);clearTimeout(workerTimer);for(const timer of settleTimers)clearTimeout(timer);worker?.terminate();for(const [name,fn]of events)host.removeEventListener(name,fn);document.removeEventListener('keydown',keyDown);document.removeEventListener('visibilitychange',visibility);controls.removeEventListener('change',controlChange);window.removeEventListener('jarvis:pins',pinsChanged);window.removeEventListener('pagehide',pageHide);document.getElementById('zoom-in')?.removeEventListener('click',zoomIn);document.getElementById('zoom-out')?.removeEventListener('click',zoomOut);document.getElementById('fit')?.removeEventListener('click',fit);if(nativeComposer)nativeComposer.render=nativeComposerRender;composer?.dispose();root.traverse(o=>o.geometry?.dispose());for(const m of [starMaterial,dustMaterial,sparkMaterial,edgeMaterial,focusMaterial])m.dispose();scene.remove(root);for(const e of labelElements.values())e.remove();graph.pauseAnimation();}
  };return api;
}
