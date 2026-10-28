// Run the real controllers with deferred responses, strict text-only DOM and URL ownership checks.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {Element,deferred,flush}=require('./support/workflow-view.cjs');
const source=fs.readFileSync('viewer/brain-services.js','utf8');
const REVISION='a'.repeat(64),NEXT_REVISION='b'.repeat(64),RUN='c'.repeat(24);
const brain=(changes={})=>({schema_version:1,active:true,configured:true,restart_required:false,
  context_chars:6000,vault_name:'Jarvis Brain',proposed_folder:'/Users/example/Documents/Jarvis Brain',
  index:{notes:2},metrics:{model_calls_avoided:3},...changes});
const operations=(changes={})=>({schema_version:1,revision:REVISION,scheduled:false,settings:{
  daily_health_enabled:false,daily_health_time:'09:00',quiet_hours_enabled:false,quiet_start:'22:00',quiet_end:'08:00',
  speak_background_updates:false,local_signals_enabled:false,minimum_battery_percent:20,
  external_power_required:false,network_required:false,timezone:'America/New_York'},...changes});
const zipResponse=({type='application/zip',blob=new Blob([new Uint8Array([80,75,3,4,0,0,0,0])]),ok=true,answer='Export unavailable.'}={})=>({response:{
  ok,headers:{get:name=>name.toLowerCase()==='content-type'?type:null},blob:async()=>blob,json:async()=>({answer})}});
class TextOnlyElement extends Element{set innerHTML(value){throw Error('Untrusted content must use textContent');}}
async function setup(mount,handler,{clickError=false}={}){
  const parent=new TextOnlyElement('main');parent.connected=true;const body=new TextOnlyElement('body');body.connected=true;
  const requests=[],listeners={},timers=new Map(),created=[],revoked=[],downloads=[],navigation=[];let timerId=0,urlId=0,graphRefreshes=0;
  class OwnedURL extends URL{static createObjectURL(blob){const url='blob:export-'+(++urlId);created.push({url,blob});return url;}static revokeObjectURL(url){revoked.push(url);}}
  const context={AbortSignal:{timeout(){}},URL:OwnedURL,Uint8Array,Date,
    document:{body,createElement(tag){const element=new TextOnlyElement(tag);if(tag==='a')element.click=()=>{if(clickError)throw Error('Download could not start.');downloads.push({href:element.href,name:element.download});};return element;}},
    window:{addEventListener(name,callback){listeners[name]=callback;},JarvisUI:{
      refreshGraph(){graphRefreshes++;},openSource(id){navigation.push(['source',id]);},
      async openBrainSource(id,stable){navigation.push(['source',id,stable]);return true;},
      openWorkRun(id){navigation.push(['work',id]);},setWorkspace(name){navigation.push(['workspace',name]);}}},
    setTimeout(callback,delay){const id=++timerId;timers.set(id,{callback,delay});return id;},clearTimeout(id){timers.delete(id);},
    fetch:async(path,options)=>{const request={path,method:options.method||'GET',body:options.body?JSON.parse(options.body):undefined};requests.push(request);
      const data=await handler(path,request.body,request);return data?.response||{ok:true,json:async()=>data};}};
  vm.runInNewContext(source,context);const api=context.window.JarvisBrainServices;api[mount](parent);await flush();
  return {parent,body,api,requests,timers,created,revoked,downloads,navigation,listeners,
    get graphRefreshes(){return graphRefreshes;},button:text=>parent.querySelectorAll('button').find(item=>item.textContent===text),
    field:label=>parent.querySelectorAll('input').find(item=>item.getAttribute('aria-label')===label),
    get status(){return parent.querySelectorAll('p').find(item=>item.getAttribute('role')==='status');},
    async submit(){await parent.querySelector('form').onsubmit({preventDefault(){}});await flush();},
    async runTimers(){for(const [id,timer]of [...timers]){timers.delete(id);await timer.callback();}await flush();}};
}
(async()=>{
  const pending=deferred(),removed=await setup('mountBrain',()=>pending.promise),before=removed.parent.textContent;
  removed.api.dispose();pending.resolve(brain({vault_name:'LATE PRIVATE VAULT'}));await flush();
  assert.equal(removed.parent.textContent,before,'disposed initial replies cannot change a view');
  const detached=deferred(),detachedOperations=await setup('mountOperations',()=>detached.promise),detachedText=detachedOperations.parent.textContent;
  detachedOperations.parent.connected=false;detached.resolve(operations());await flush();assert.equal(detachedOperations.parent.textContent,detachedText);

  const initial=deferred();let reads=0;
  const revision=await setup('mountOperations',path=>path==='/operations'?(++reads===1?initial.promise:operations({revision:NEXT_REVISION})):{status:'complete'});
  await revision.button('Run health check now').onclick();initial.resolve(operations());await flush();
  revision.field('Quiet hours').checked=true;await revision.submit();
  assert.equal(revision.requests.find(item=>item.path==='/operations/settings').body.expected_revision,NEXT_REVISION,'older initial GET cannot replace a newer settings revision');

  const oldError=deferred();let oldReads=0;
  const staleError=await setup('mountOperations',path=>path==='/operations'?(++oldReads===1?oldError.promise:operations({revision:NEXT_REVISION})):{status:'complete'});
  await staleError.button('Run health check now').onclick();const currentStatus=staleError.status.textContent;
  oldError.reject(Error('STALE INITIAL ERROR'));await flush();assert.equal(staleError.status.textContent,currentStatus);

  let current=operations(),saveCount=0;
  const conflict=await setup('mountOperations',(path,body)=>{if(path==='/operations')return current;if(path==='/operations/settings'){
    if(++saveCount===1){current=operations({revision:NEXT_REVISION});return {response:{ok:false,json:async()=>({answer:'Settings changed. Review the current values.'})}};}
    assert.equal(body.expected_revision,NEXT_REVISION);return current;}throw Error('Unexpected route');});
  await conflict.submit();assert.match(conflict.status.textContent,/Settings changed/);assert.equal(conflict.button('Save local settings').disabled,false);
  await conflict.submit();assert.match(conflict.status.textContent,/Local settings saved/);
  const saveReply=deferred(),leaveSave=await setup('mountOperations',path=>path==='/operations'?operations():saveReply.promise);
  const saving=leaveSave.submit();leaveSave.api.dispose();const saveText=leaveSave.parent.textContent;saveReply.reject(Error('LATE SAVE ERROR'));await saving;
  assert.equal(leaveSave.parent.textContent,saveText,'disposed settings errors never reload or mutate old fields');
  const savePending=deferred(),singleSave=await setup('mountOperations',path=>path==='/operations'?operations():savePending.promise);
  const firstSave=singleSave.submit();assert.equal(singleSave.field('Quiet hours').disabled,true);await singleSave.submit();
  assert.equal(singleSave.requests.filter(item=>item.path==='/operations/settings').length,1,'one outstanding save cannot submit a duplicate change');
  savePending.resolve(operations({revision:NEXT_REVISION}));await firstSave;assert.equal(singleSave.field('Quiet hours').disabled,false);
  const invalidRevision=await setup('mountOperations',()=>operations({revision:'not-a-revision'}));await invalidRevision.submit();
  assert.match(invalidRevision.status.textContent,/could not be verified/);assert.equal(invalidRevision.requests.length,1,'unverified settings cannot be submitted');

  let contextBudget=6000;
  const memorySettings=await setup('mountBrain',(path,body)=>{if(path==='/brain')return brain();if(path==='/brain/settings'){
    assert.equal(body.context_chars,12000);contextBudget=body.context_chars;return brain({context_chars:contextBudget});}throw Error('Unexpected route');});
  const budget=memorySettings.parent.querySelector('select');budget.value='12000';await budget.onchange();
  assert.equal(memorySettings.parent.querySelector('select').value,'12000');assert.equal(memorySettings.requests.length,2);
  const failedBudget=await setup('mountBrain',path=>path==='/brain'?brain():{response:{ok:false,json:async()=>({answer:'Budget setting could not be saved.'})}});
  const reverted=failedBudget.parent.querySelector('select');reverted.value='12000';await reverted.onchange();
  assert.equal(reverted.value,'6000');assert.equal(reverted.disabled,false);assert.match(failedBudget.status.textContent,/could not be saved/);

  const passage='<img src=x onerror=alert(1)> & literal source text',title='<script>Source title</script>';
  const search=await setup('mountBrain',path=>path==='/brain'?brain():{schema_version:1,model_calls:0,items:[
    {id:2,title,text:passage,source:{stable_id:REVISION,uri:'obsidian://open?path=%2FUsers%2Fexample%2FPlan.md'}},
    {id:-1,title:'Unsafe source link',text:'Text',source:{uri:'javascript:alert(1)'}}]});
  search.field('Search Jarvis Brain').value=' launch ';await search.submit();
  assert.deepEqual(search.requests[1].body,{query:'launch'});assert.equal(search.parent.querySelector('pre').textContent,passage);
  assert.equal(search.parent.querySelector('h3').textContent,title);assert.equal(search.parent.querySelectorAll('a').length,1);
  assert.equal(search.parent.querySelectorAll('button').filter(item=>item.textContent==='Read in Jarvis').length,1,'negative source IDs cannot navigate');
  await search.button('Read in Jarvis').onclick();assert.deepEqual(search.navigation,[['source',2,REVISION]]);
  for(const uri of ['javascript:alert(1)','https://example.com','obsidian://new?path=/safe','obsidian://open?path=',
    'obsidian://open?path=/safe&vault=other','obsidian://user:secret@open?path=/safe',
    'obsidian://open:123?path=/safe','obsidian://open?path=/safe&path=/other','obsidian://open?path=/safe#command',
    'obsidian://open?path=relative.md','obsidian://open?path=%00'])assert.equal(search.api.obsidianLink(uri),null,uri);

  const changedReply=deferred(),changed=await setup('mountBrain',path=>path==='/brain'?brain():changedReply.promise);
  const query=changed.field('Search Jarvis Brain');query.value='old question';const searching=changed.submit();
  query.value='new question';query.oninput?.();query.emit('input');changedReply.resolve({schema_version:1,model_calls:0,items:[{id:1,title:'OLD RESULT',text:'Old text'}]});await searching;
  assert.ok(!changed.parent.textContent.includes('OLD RESULT'),'input edits invalidate pending source passages');assert.equal(changed.button('Search locally').disabled,false);
  const changedError=deferred(),changedFailure=await setup('mountBrain',path=>path==='/brain'?brain():changedError.promise);
  const edited=changedFailure.field('Search Jarvis Brain');edited.value='old';const oldSearch=changedFailure.submit();edited.value='new';edited.oninput?.();edited.emit('input');
  const editedStatus=changedFailure.status.textContent;changedError.reject(Error('OLD SEARCH ERROR'));await oldSearch;assert.equal(changedFailure.status.textContent,editedStatus);
  const preRefresh=deferred(),refreshed=await setup('mountBrain',path=>path==='/brain'?brain():path==='/brain/refresh'?brain({index:{notes:1}}):preRefresh.promise);
  refreshed.field('Search Jarvis Brain').value='old source';const oldResult=refreshed.submit();await refreshed.button('Refresh notes').onclick();
  assert.equal(refreshed.graphRefreshes,1);const refreshedStatus=refreshed.status.textContent;
  preRefresh.reject(Error('STALE SOURCE ERROR'));await oldResult;assert.equal(refreshed.status.textContent,refreshedStatus,'source refresh invalidates pending search errors');
  const searchReply=deferred(),leaving=await setup('mountBrain',path=>path==='/brain'?brain():searchReply.promise);
  leaving.field('Search Jarvis Brain').value='private';const leavingSearch=leaving.submit();leaving.parent.connected=false;const leavingText=leaving.parent.textContent;
  searchReply.resolve({schema_version:1,model_calls:0,items:[{id:1,title:'DETACHED RESULT',text:'Text'}]});await leavingSearch;assert.equal(leaving.parent.textContent,leavingText);

  const exporting=await setup('mountExport',()=>zipResponse());assert.equal(exporting.requests.length,0,'exports never happen in the background');
  exporting.field('Export Settings').checked=false;await exporting.button('Download selected data').onclick();assert.equal(exporting.requests.length,0);assert.match(exporting.status.textContent,/Choose at least one/);
  exporting.field('Export Shared Markdown notes').checked=true;await exporting.button('Download selected data').onclick();
  assert.deepEqual(exporting.requests[0].body,{sections:['notes']});assert.deepEqual(exporting.downloads,[{href:'blob:export-1',name:'Jarvis user data.zip'}]);
  assert.equal(exporting.body.children.length,0);assert.equal(exporting.revoked.length,0);await exporting.runTimers();assert.deepEqual(exporting.revoked,['blob:export-1']);exporting.api.dispose();assert.equal(exporting.revoked.length,1);
  for(const response of [zipResponse({type:'text/html'}),zipResponse({type:'application/zip-unsafe'}),zipResponse({blob:new Blob(['PK wrong'])}),
    zipResponse({blob:new Blob([])}),zipResponse({blob:{size:20*1024*1024+1}}),zipResponse({ok:false,answer:'Selected source is unavailable.'})]){
    const invalid=await setup('mountExport',()=>response);await invalid.button('Download selected data').onclick();
    assert.equal(invalid.created.length,0);assert.equal(invalid.downloads.length,0);assert.equal(invalid.button('Download selected data').disabled,false);
  }
  const clickFailure=await setup('mountExport',()=>zipResponse(),{clickError:true});await clickFailure.button('Download selected data').onclick();
  assert.equal(clickFailure.created.length,1);assert.deepEqual(clickFailure.revoked,['blob:export-1'],'failed download releases its URL immediately');assert.equal(clickFailure.body.children.length,0);
  const outstanding=await setup('mountExport',()=>zipResponse());await outstanding.button('Download selected data').onclick();outstanding.listeners.pagehide();
  assert.deepEqual(outstanding.revoked,['blob:export-1']);assert.equal(outstanding.timers.size,0);
  const lateZIP=deferred(),lateExport=await setup('mountExport',()=>lateZIP.promise);const download=lateExport.button('Download selected data').onclick();
  lateExport.api.dispose();const exportText=lateExport.parent.textContent;lateZIP.resolve(zipResponse());await download;assert.equal(lateExport.created.length,0);assert.equal(lateExport.parent.textContent,exportText);
  const signature=deferred(),checking=await setup('mountExport',()=>zipResponse({blob:{size:32,slice:()=>({arrayBuffer:()=>signature.promise})}}));
  const checkingDownload=checking.button('Download selected data').onclick();await flush();checking.api.dispose();signature.resolve(new Uint8Array([80,75,3,4]).buffer);await checkingDownload;assert.equal(checking.created.length,0);

  const activity=await setup('mountActivity',()=>({schema_version:1,truncated:true,items:[
    {source:'workflows',subject_id:RUN,label:'Work <script> complete',at:1000},
    {source:'approvals',subject_id:'d'.repeat(24),label:'Preference awaiting review',at:1001},
    {source:'workflows',subject_id:'../../wrong',label:'Invalid run identity',at:1002}]}));
  assert.ok(activity.parent.textContent.includes('Work <script> complete'));assert.match(activity.status.textContent,/older events/);
  assert.equal(activity.parent.querySelectorAll('button').filter(item=>item.textContent==='View saved work').length,1);
  await activity.button('View saved work').onclick();await activity.button('Review decisions').onclick();
  assert.deepEqual(activity.navigation,[['work',RUN],['workspace','work']]);
  const history=deferred(),removedHistory=await setup('mountActivity',()=>history.promise);removedHistory.parent.connected=false;const historyText=removedHistory.parent.textContent;
  history.resolve({schema_version:1,items:[{source:'approvals',label:'LATE APPROVAL',at:1000}]});await flush();assert.equal(removedHistory.parent.textContent,historyText);
  console.log('BRAIN SERVICES PASS: detached/stale replies, revision-safe settings, text-only local search and URI validation, explicit ZIP checks/URL cleanup, and bounded activity navigation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
