const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {Element,deferred,flush}=require('./support/workflow-view.cjs');
const source=fs.readFileSync('viewer/local-services.js','utf8');
const action={id:'a'.repeat(24),status:'pending',title:'Plan <script>',fingerprint:'b'.repeat(64),before:{text:'Original text'},after:{text:'Reviewed text'}};
async function setup(handler,mount='mountApprovals'){
  const requests=[],timers=new Map();let timerId=0,graphRefreshes=0;
  const parent=new Element('main');parent.connected=true;
  const context={AbortSignal:{timeout(){}},document:{createElement:tag=>new Element(tag)},
    window:{addEventListener(){},JarvisUI:{refreshGraph(){graphRefreshes++;}}},
    setTimeout(callback,ms){const id=++timerId;timers.set(id,{callback,ms});return id;},clearTimeout(id){timers.delete(id);},
    fetch:async(path,options)=>{const body=options.body?JSON.parse(options.body):undefined;requests.push({path,body});
      const data=await handler(path,body);return data?.response||{ok:true,json:async()=>data};}};
  vm.runInNewContext(source,context);const api=context.window.JarvisLocalServices;api[mount](parent);await flush();
  return {parent,requests,timers,api,get graphRefreshes(){return graphRefreshes;},button:text=>parent.querySelectorAll('button').find(node=>node.textContent===text)};
}
(async()=>{
  let current=action,decisions=[];
  const handler=async(path,body)=>{
    if(path==='/approvals')return {items:[current],pending_count:current.status==='pending'?1:0};
    if(path.startsWith('/approvals?id='))return current;
    decisions.push({path,body});current={...current,status:path==='/approvals/undo'?'undone':'applied',can_undo:true};
    return {approval:current,result:{verified:true,graph:{nodes:[]}}};
  };
  const view=await setup(handler);assert.equal(view.requests.length,1);assert.equal(decisions.length,0);
  assert.ok(view.parent.textContent.includes('1 note edit needs a decision'));assert.equal(view.parent.querySelectorAll('pre').length,0,'contents fetched only for review');
  await view.button('Review').onclick();assert.equal(view.parent.querySelectorAll('pre').length,2);assert.ok(view.parent.textContent.includes('Reviewed text'));
  await view.button('Approve and save').onclick();assert.equal(decisions.length,1);assert.equal(decisions[0].path,'/approvals/decide');
  assert.equal(decisions[0].body.fingerprint,action.fingerprint);assert.equal(decisions[0].body.decision,'approve');assert.equal(view.graphRefreshes,1);assert.equal(view.timers.size,1,'a manual refresh replaces the polling timer');
  await view.button('Review').onclick();await view.button('Undo this edit').onclick();assert.equal(decisions[1].path,'/approvals/undo');assert.ok(!('decision' in decisions[1].body));assert.equal(view.graphRefreshes,2);
  const expired=await setup(async path=>path==='/approvals'?{items:[{...action,status:'applied'}],pending_count:0}:{...action,status:'applied',can_undo:false});
  await expired.button('Review').onclick();assert.equal(expired.button('Undo this edit'),undefined);
  const failed=await setup(async path=>path==='/approvals'?{items:[action],pending_count:1}:path.startsWith('/approvals?id=')?action:{response:{ok:false,json:async()=>({answer:'The note changed after review.'})}});
  await failed.button('Review').onclick();await failed.button('Approve and save').onclick();assert.ok(failed.parent.textContent.includes('The note changed'));assert.equal(failed.graphRefreshes,0);
  const unverified=await setup(async path=>path==='/approvals'?{items:[action],pending_count:1}:path.startsWith('/approvals?id=')?action:{approval:{...action,status:'applied'},result:{verified:false}});
  await unverified.button('Review').onclick();await unverified.button('Approve and save').onclick();assert.ok(unverified.parent.textContent.includes('could not be confirmed'));assert.ok(!unverified.parent.textContent.includes('Edit saved and verified.'));
  const pending=deferred(),removed=await setup(path=>path==='/approvals'?{items:[action],pending_count:1}:pending.promise);
  const reviewing=removed.button('Review').onclick();removed.api.dispose();const text=removed.parent.textContent;pending.resolve(action);await reviewing;assert.equal(removed.parent.textContent,text);assert.equal(removed.timers.size,0);
  const health=await setup(async()=>({schema_version:1,checks:[{title:'Microphone',status:'not_checked',detail:'Not opened.',action:'Check permissions from the menu.'}],first_run:{guidance:[{action:'Try the physical hotkey.'}]}}),'mountHealth');
  assert.equal(health.requests[0].path,'/health');assert.ok(health.parent.textContent.includes('Try the physical hotkey'));assert.equal(health.requests.length,1);assert.equal(health.timers.size,0,'health checks never schedule themselves');
  console.log('LOCAL SERVICES PASS: exact review/decision/undo, stale and expired refusal, bounded polling, detached responses, and read-only readiness.');
})().catch(error=>{console.error(error);process.exitCode=1;});
