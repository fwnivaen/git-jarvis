// Exercise the actual saved-task controller with deferred HTTP and text-only DOM.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {Element,deferred,flush}=require('./support/workflow-view.cjs');
const SOURCE=fs.readFileSync('viewer/task-privacy.js','utf8'),ID='a'.repeat(24),REV='b'.repeat(64);
const item=(changes={})=>({id:ID,label:'Compare <script> project evidence',state:'retained',status:'complete',eligible:true,created_at:1,...changes});
const catalog=(items=[item()])=>({schema_version:1,available:true,items});
const plan=(changes={})=>({schema_version:1,id:ID,state:'retained',eligible:true,revision:REV,
  total_bytes:100,effects:['Remove this task’s saved questions, results and source passages.'],
  exclusions:['Obsidian notes and preferences remain.','Previous exports and backups remain.'],...changes});
const receipt=(changes={})=>({schema_version:1,id:ID,revision:REV,state:'erased',erased:true,retryable:false,...changes});
class TextOnlyElement extends Element{set innerHTML(value){throw Error('Task contents must use textContent');}}
async function setup(handler){
  const parent=new TextOnlyElement('main');parent.connected=true;const requests=[],listeners={};
  const context={AbortSignal:{timeout(){}},document:{createElement:tag=>new TextOnlyElement(tag)},
    window:{addEventListener(name,callback){listeners[name]=callback;}},
    fetch:async(path,options)=>{const body=options.body?JSON.parse(options.body):undefined;requests.push({path,body,method:options.method||'GET'});
      const data=await handler(path,body);return data?.response||{ok:true,json:async()=>data};}};
  vm.runInNewContext(SOURCE,context);const api=context.window.JarvisTaskPrivacy;api.mount(parent);await flush();
  return {parent,requests,api,listeners,button:text=>parent.querySelectorAll('button').find(node=>node.textContent===text),
    get status(){return parent.querySelectorAll('p').find(node=>node.getAttribute('role')==='status').textContent;}};
}
(async()=>{
  let retained=true;
  const real=await setup(path=>path==='/workflows/retention'?catalog(retained?[item()]:[item({label:'Removed task',state:'erased',eligible:false})]):path==='/workflows/erase-plan'?plan():(retained=false,receipt()));
  assert.equal(real.requests.length,1,'opening Settings cannot plan or erase');
  assert.ok(real.parent.textContent.includes('Compare <script> project evidence'));
  await real.button('Review removal').onclick();assert.equal(real.requests.length,2);assert.ok(real.parent.textContent.includes('Previous exports and backups remain'));
  assert.equal(real.requests.filter(row=>row.path==='/workflows/erase').length,0,'review has no removal effect');
  await real.button('Keep saved task').onclick();assert.equal(real.requests.length,2);assert.equal(real.button('Erase saved task'),undefined);
  await real.button('Review removal').onclick();await real.button('Erase saved task').onclick();
  const effect=real.requests.find(row=>row.path==='/workflows/erase');assert.deepEqual(effect.body,{id:ID,expected_revision:REV,confirmed:true});
  assert.match(real.status,/saved contents were removed/);assert.equal(real.button('Review removal'),undefined);
  assert.equal(real.requests.filter(row=>row.path==='/workflows/erase').length,1,'refresh after removal never repeats it');

  const active=await setup(()=>catalog([item({eligible:false,status:'running'})]));assert.equal(active.button('Review removal').disabled,true);assert.equal(active.button('Erase saved task'),undefined);
  const ineligible=await setup(path=>path==='/workflows/retention'?catalog():plan({eligible:false,reason:'Task cleanup has not settled.'}));
  await ineligible.button('Review removal').onclick();assert.equal(ineligible.button('Erase saved task').disabled,true);await ineligible.button('Erase saved task').onclick();assert.equal(ineligible.requests.length,2);

  for(const data of [plan({id:'c'.repeat(24)}),plan({revision:'invalid'}),plan({total_bytes:-1}),plan({effects:['ok',{}]}),plan({exclusions:null})]){
    const invalid=await setup(path=>path==='/workflows/retention'?catalog():data);await invalid.button('Review removal').onclick();
    assert.equal(invalid.button('Erase saved task'),undefined);assert.match(invalid.status,/could not be verified/);
  }
  const badCatalog=await setup(()=>catalog([item({id:'../outside'})]));assert.equal(badCatalog.button('Review removal'),undefined);assert.match(badCatalog.status,/could not be verified/);

  for(const data of [receipt({id:'c'.repeat(24)}),receipt({revision:'c'.repeat(64)}),receipt({erased:false}),receipt({retryable:undefined})]){
    const unverified=await setup(path=>path==='/workflows/retention'?catalog():path==='/workflows/erase-plan'?plan():data);
    await unverified.button('Review removal').onclick();await unverified.button('Erase saved task').onclick();
    assert.match(unverified.status,/could not be confirmed/);assert.ok(!unverified.status.includes('were removed'));assert.equal(unverified.requests.length,3);
  }

  let pending=false;
  const partial=await setup(path=>path==='/workflows/retention'?catalog([item(pending?{state:'pending',label:'Removed task'}:{})]):path==='/workflows/erase-plan'?plan({state:pending?'pending':'retained'}):(pending=true,receipt({state:'pending',erased:false,retryable:true})));
  await partial.button('Review removal').onclick();await partial.button('Erase saved task').onclick();
  assert.match(partial.status,/Removal is pending/);assert.equal(partial.button('Retry reviewed cleanup'),undefined);assert.equal(partial.requests.filter(row=>row.path==='/workflows/erase').length,1);
  await partial.button('Review pending cleanup').onclick();assert.ok(partial.button('Retry reviewed cleanup'));assert.equal(partial.requests.filter(row=>row.path==='/workflows/erase').length,1);

  const failure=await setup(path=>path==='/workflows/retention'?catalog():path==='/workflows/erase-plan'?plan():{response:{ok:false,json:async()=>({answer:'The task changed after review.'})}});
  await failure.button('Review removal').onclick();await failure.button('Erase saved task').onclick();assert.match(failure.status,/changed after review/);assert.equal(failure.button('Erase saved task'),undefined);assert.equal(failure.requests.length,3);

  const first=deferred(),busy=await setup(()=>first.promise);await busy.button('Refresh saved tasks').onclick();assert.equal(busy.requests.length,1);
  first.resolve(catalog());await flush();assert.equal(busy.button('Refresh saved tasks').disabled,false,'duplicate refresh cannot invalidate the only pending request');
  const reviewReply=deferred(),reviewing=await setup(path=>path==='/workflows/retention'?catalog():reviewReply.promise);
  const reviewingRequest=reviewing.button('Review removal').onclick();await reviewing.button('Review removal').onclick();assert.equal(reviewing.requests.length,2);
  reviewing.api.dispose();const before=reviewing.parent.textContent;reviewReply.resolve(plan());await reviewingRequest;assert.equal(reviewing.parent.textContent,before);
  const eraseReply=deferred(),leaving=await setup(path=>path==='/workflows/retention'?catalog():path==='/workflows/erase-plan'?plan():eraseReply.promise);
  await leaving.button('Review removal').onclick();const removing=leaving.button('Erase saved task').onclick();await leaving.button('Erase saved task').onclick();assert.equal(leaving.requests.length,3);
  leaving.parent.connected=false;const detached=leaving.parent.textContent;eraseReply.resolve(receipt());await removing;
  assert.equal(leaving.parent.textContent,detached);assert.equal(leaving.requests.length,3,'detached receipt never fetches or renders another history list');

  let catalogReads=0;
  const noRefresh=await setup(path=>path==='/workflows/retention'?(++catalogReads===1?catalog():Promise.reject(Error('List unavailable'))):path==='/workflows/erase-plan'?plan():receipt());
  await noRefresh.button('Review removal').onclick();await noRefresh.button('Erase saved task').onclick();assert.match(noRefresh.status,/were removed.*list could not refresh/);
  console.log('TASK PRIVACY PASS: explicit review/confirm, exact task and revision, active/stale refusal, pending cleanup, verified receipts and detached/duplicate request fences.');
})().catch(error=>{console.error(error);process.exitCode=1;});
