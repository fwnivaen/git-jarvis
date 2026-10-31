// Real wake handler: OrbitControls may emit change synchronously while resuming.
const fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync('viewer/galaxy.js','utf8');
const handler=source.slice(source.indexOf('  function wake('),source.indexOf('  function persist('));
let resumes=0,frames=0,wake;
const graph={resumeAnimation(){resumes++;assert.ok(resumes<4,'synchronous change must not recurse');wake();}};
const factory=new Function('graph','requestAnimationFrame','performance',`
  const GALAXY={idleSettleMs:240},document={hidden:false};let visible=true;let disposed=false,awakeUntil=0,resuming=false,frame=null,ticking=false;const stats={idle:true};function frameTick(){}
  ${handler}
  return {wake,hide(){document.hidden=true;},show(){document.hidden=false;},startTick(){frame=null;ticking=true;},dispose(){disposed=true;},snapshot(){return {awakeUntil,frame,idle:stats.idle};}};
`);
const api=factory(graph,()=>++frames,{now:()=>100});wake=api.wake;
wake();assert.equal(resumes,1);assert.equal(frames,1);assert.equal(api.snapshot().awakeUntil,340);
wake(1000);assert.equal(resumes,2);assert.equal(frames,1);assert.equal(api.snapshot().awakeUntil,1100);
api.startTick();wake();assert.equal(resumes,3);assert.equal(frames,1,'change during a running tick must not create a second RAF loop');
api.hide();wake();assert.equal(resumes,3,'a hidden tab must not restart rendering');api.show();api.dispose();wake();assert.equal(resumes,3);
console.log('GALAXY LIFECYCLE PASS: synchronous control changes cannot recurse on resume; wake coalesces frames; hidden tabs and disposed renderers stay stopped.');

// The production numeric grid keeps border cells distinct and returns the nearest
// projected note. Empty and out-of-radius queries must not select a distant note.
const picker=source.slice(source.indexOf('  function pick('),source.indexOf('  function showHover('));
const columns=6,spatial=new Map(),points=[{x:-2,y:12,node:{id:1}},{x:127,y:12,node:{id:2}},{x:33,y:35,node:{id:3}},{x:36,y:36,node:{id:4}}];
for(const point of points){const key=Math.floor(point.x/32)+1+(Math.floor(point.y/32)+1)*columns;if(!spatial.has(key))spatial.set(key,[]);spatial.get(key).push(point);}
const pick=new Function('spatial','spatialColumns','performance',`const GALAXY={hitCell:32,hitRadius:14},stats={hoverMs:0,maxHoverMs:0};${picker};return pick;`)(spatial,columns,{now:()=>0});
assert.equal(pick(0,12).id,1);assert.equal(pick(126,12).id,2);assert.equal(pick(33,35).id,3);assert.equal(pick(36,36).id,4);assert.equal(pick(80,80),null);assert.equal(pick(0,70),null);
console.log('GALAXY PICKING PASS: nearest notes, negative border cells, right edge and empty-space refusal.');

// Regression: a visible, settled dashboard used to submit GPU work continuously,
// competing with local speech even when orbit was off.
const framePolicy=source.slice(source.indexOf('  function needsFrames('),source.indexOf('  function frameTick('));
const policy=new Function('state',`const {document,visible,reducedMotion,continuous,speechBusy,awakeUntil,motions,detachRemaining}=state;${framePolicy};return needsFrames(1000);`);
const settled={document:{hidden:false},visible:true,reducedMotion:false,continuous:false,speechBusy:false,awakeUntil:900,motions:new Map(),detachRemaining:0};
assert.equal(policy(settled),false,'settled galaxy must yield the GPU');
assert.equal(policy({...settled,awakeUntil:1100}),true,'interaction must remain responsive');
assert.equal(policy({...settled,continuous:true}),true,'an enabled orbit still moves');
assert.equal(policy({...settled,continuous:true,speechBusy:true}),false,'speech generation takes priority over decorative orbit');
assert.equal(policy({...settled,continuous:true,reducedMotion:true}),false,'reduced motion still applies');
assert.equal(policy({...settled,motions:new Map([[1,{}]])}),true,'layout must finish');
assert.equal(policy({...settled,detachRemaining:2}),true,'initial cleanup must finish');
assert.equal(policy({...settled,document:{hidden:true},continuous:true}),false,'hidden tabs stay paused');
console.log('GALAXY GPU IDLE PASS: settled views yield the GPU; interaction, layout and explicit orbit remain live.');
