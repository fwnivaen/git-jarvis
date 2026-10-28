const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {Element,deferred,flush}=require('./support/workflow-view.cjs');
const source=fs.readFileSync('viewer/local-services.js','utf8');
const action={id:'a'.repeat(24),status:'pending',title:'Plan <script>',fingerprint:'b'.repeat(64),before:{text:'Original text'},after:{text:'Reviewed text'}};
async function setup(handler,mount='mountApprovals'){
  const requests=[],timers=new Map();let timerId=0,graphRefreshes=0,keyId=0;
  const parent=new Element('main');parent.connected=true;
  const context={AbortSignal:{timeout(){}},document:{createElement:tag=>new Element(tag)},
    window:{addEventListener(){},JarvisUI:{refreshGraph(){graphRefreshes++;},operationKey(){return (++keyId).toString(16).padStart(24,'0');},setWorkspace(){}}},
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
  const before={preferences:{tone:'Brief'},revision:'c'.repeat(64)},after={preferences:{tone:'Direct'},revision:'d'.repeat(64)};
  let preferenceAction={...action,kind:'preferences',operation:'set',key:'tone',title:'Change preference',before,after},preferenceCalls=[];
  const preferences=await setup(async(path,body)=>{
    if(path==='/preference-actions')return {items:[preferenceAction],pending_count:preferenceAction.status==='pending'?1:0};
    if(path.startsWith('/preference-actions?id='))return preferenceAction;
    preferenceCalls.push({path,body});preferenceAction={...preferenceAction,status:path.endsWith('/undo')?'undone':'applied',can_undo:true};
    const target=path.endsWith('/undo')?before:after;return {approval:preferenceAction,result:{verified:true,...target}};
  },'mountPreferenceApprovals');
  await preferences.button('Review').onclick();assert.ok(preferences.parent.textContent.includes('Brief'));assert.ok(preferences.parent.textContent.includes('Direct'));
  await preferences.button('Approve and save').onclick();assert.equal(preferenceCalls[0].path,'/preference-actions/decide');assert.ok(preferences.parent.textContent.includes('Preference change saved and verified.'));
  await preferences.button('Review').onclick();await preferences.button('Undo this edit').onclick();assert.equal(preferenceCalls[1].path,'/preference-actions/undo');assert.ok(preferences.parent.textContent.includes('Preference change undone and verified.'));
  const mismatched=await setup(async path=>path==='/preference-actions'?{items:[{...preferenceAction,status:'pending'}],pending_count:1}:path.includes('?id=')?{...preferenceAction,status:'pending'}:{approval:{...preferenceAction,status:'applied'},result:{verified:true,revision:after.revision,preferences:{tone:'Wrong'}}},'mountPreferenceApprovals');
  await mismatched.button('Review').onclick();await mismatched.button('Approve and save').onclick();assert.ok(mismatched.parent.textContent.includes('does not match the reviewed state'));assert.ok(!mismatched.parent.textContent.includes('saved and verified'));
  let tries=0;const bodies=[];
  const editor=await setup(async(path,body)=>{
    if(path==='/preferences')return before;
    bodies.push(body);if(++tries===1)throw Error('Connection lost');
    return {...action,kind:'preferences',operation:'set',key:'tone',before,after};
  },'mountPreferences');
  const inputs=editor.parent.querySelectorAll('input');inputs[0].value='tone';inputs[1].value='Direct';const form=editor.parent.querySelectorAll('form')[0],submit=()=>form.onsubmit({preventDefault(){}});
  await submit();assert.ok(editor.parent.textContent.includes('Connection lost'));await submit();await submit();
  assert.equal(bodies.length,3);assert.equal(bodies[0].request_key,bodies[1].request_key);assert.equal(bodies[1].request_key,bodies[2].request_key);assert.equal(bodies[0].revision,before.revision);assert.ok(editor.parent.textContent.includes('Nothing has changed yet'));assert.equal(editor.graphRefreshes,0);
  let staleTry=0;const staleBodies=[],fresh={preferences:{tone:'New external value'},revision:'e'.repeat(64)};
  const stale=await setup(async(path,body)=>{
    if(path==='/preferences')return staleTry?fresh:before;
    staleBodies.push(body);if(++staleTry===1)return {response:{ok:false,status:409,json:async()=>({answer:'Preferences changed after review.'})}};
    return {...action,kind:'preferences',operation:'set',key:'tone',before:fresh,after};
  },'mountPreferences');
  const staleInputs=stale.parent.querySelectorAll('input');staleInputs[0].value='tone';staleInputs[1].value='Direct';const staleSubmit=()=>stale.parent.querySelectorAll('form')[0].onsubmit({preventDefault(){}});
  await staleSubmit();await staleSubmit();assert.notEqual(staleBodies[0].request_key,staleBodies[1].request_key);assert.equal(staleBodies[1].revision,fresh.revision);
  const detachedPreference=deferred(),detached=await setup(()=>detachedPreference.promise,'mountPreferences');detached.api.dispose();const detachedText=detached.parent.textContent;detachedPreference.resolve(before);await flush();assert.equal(detached.parent.textContent,detachedText);
  console.log('LOCAL SERVICES PASS: exact note/preference review, verified apply/undo receipts, stale refusal, retry identity, detached responses, bounded polling and read-only readiness.');

})().catch(error=>{console.error(error);process.exitCode=1;});
