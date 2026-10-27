// Exercise the real workflow controller with a small DOM and deferred HTTP responses.
const assert=require('node:assert/strict'),{setup,catalog,plan,run,wordResponse,deferred,flush}=require('./support/workflow-view.cjs');
(async()=>{
  const view=await setup();view.input('Compare the evidence');
  assert.equal(view.preview.type,'button','preview cannot submit the launch form');
  await view.preview.onclick();
  assert.deepEqual(view.requests.filter(item=>item.path==='/stage2/tasks/plan'),[{path:'/stage2/tasks/plan',method:'POST',body:{question:'Compare the evidence',workflow:'research'}}]);
  assert.ok(!view.requests.some(item=>item.path==='/workflows/start'),'preview never launches a task');
  assert.equal(view.streams.length,0);assert.equal(view.events.length,0);assert.equal(view.plan.hidden,false);assert.equal(view.plan.open,true);
  assert.equal(view.plan.getAttribute('aria-label'),'Workflow plan preview');assert.equal(view.planStatus.getAttribute('role'),'status');
  for(const text of ['Research analyst','Find supported claims.','Source reviewer','Search local notes.','notes_search','notes_read','Concurrent tasks','240','Not enforced',
      'Preview effects','Task admitted: No','Question and results stored: Yes','External actions: No','Contains source passages: Yes.','No web browsing'])assert.ok(view.plan.textContent.includes(text),text);
  assert.match(view.planStatus.textContent,/No task has started/);assert.equal(view.start.disabled,false);

  const pending=deferred(),stale=await setup({preview:()=>pending.promise});stale.input('Old question');
  const oldRequest=stale.preview.onclick();stale.input('New question');pending.resolve(plan({question:'OLD PRIVATE PLAN'}));await oldRequest;
  assert.equal(stale.plan.hidden,true);assert.ok(!stale.parent.textContent.includes('OLD PRIVATE PLAN'));assert.match(stale.planStatus.textContent,/Task changed/);assert.equal(stale.preview.disabled,false);

  const first=deferred(),second=deferred();let count=0;
  const changed=await setup({preview:()=>++count===1?first.promise:second.promise});changed.input('Review sources');
  const original=changed.preview.onclick();changed.change('code');const replacement=changed.preview.onclick();
  first.resolve(plan({question:'STALE TYPE'}));await original;assert.equal(changed.preview.disabled,true,'an older response cannot enable the current request button');
  second.resolve(plan({label:'Code review',workflow:'code',question:'CURRENT TYPE'}));await replacement;
  assert.ok(changed.plan.textContent.includes('CURRENT TYPE'));assert.ok(!changed.plan.textContent.includes('STALE TYPE'));assert.equal(changed.preview.disabled,false);

  const detached=deferred(),unmounted=await setup({preview:()=>detached.promise});unmounted.input('Private task');
  const unmountedRequest=unmounted.preview.onclick();const before=unmounted.parent.textContent;unmounted.api.dispose();detached.resolve(plan({question:'LATE PLAN'}));await unmountedRequest;
  assert.equal(unmounted.parent.textContent,before,'unmounted preview does not mutate the old view');

  const staleError=deferred(),edited=await setup({preview:()=>staleError.promise});edited.input('Old task');const failedOld=edited.preview.onclick();
  edited.input('Current task');const currentNotice=edited.planStatus.textContent;staleError.reject(Error('PRIVATE OLD ERROR'));await failedOld;
  assert.equal(edited.planStatus.textContent,currentNotice);assert.ok(!edited.parent.textContent.includes('PRIVATE OLD ERROR'));
  const detachedReply=deferred(),removed=await setup({preview:()=>detachedReply.promise});removed.input('Removed task');const removedRequest=removed.preview.onclick();
  const removedText=removed.parent.textContent;removed.parent.connected=false;detachedReply.resolve(plan({question:'DETACHED PLAN'}));await removedRequest;
  assert.equal(removed.parent.textContent,removedText,'a detached DOM also rejects late responses without disposal');

  const failed=await setup({preview:async()=>({response:{ok:false,json:async()=>({answer:'Plan service unavailable.'})}})});failed.input('A task');
  await failed.preview.onclick();assert.equal(failed.plan.hidden,true);assert.equal(failed.planStatus.textContent,'Plan service unavailable.');assert.equal(failed.preview.disabled,false);
  assert.equal(failed.requests.filter(item=>item.path==='/workflows/start').length,0);
  const bad=await setup({preview:async()=>plan({admitted:true})});bad.input('A task');await bad.preview.onclick();assert.equal(bad.plan.hidden,true);assert.match(bad.planStatus.textContent,/unstarted plan preview/);
  const empty=await setup();await empty.preview.onclick();assert.match(empty.planStatus.textContent,/Enter a short task/);assert.equal(empty.requests.length,1);

  const full=await setup({data:catalog(['one','two'])});full.input('Preview while capacity is full');assert.equal(full.start.disabled,true);assert.equal(full.preview.disabled,false);
  await full.preview.onclick();assert.equal(full.start.disabled,true,'preview cannot override task capacity');
  await full.refresh(catalog(['one']));assert.equal(full.start.disabled,false,'one free slot permits a second task');
  await full.refresh({...catalog(),enabled:false});assert.equal(full.start.disabled,true,'disabled workflows cannot launch');

  const retried=await setup({start:async()=>{throw Error('Response interrupted');}});retried.input('Original task');await retried.submit();await retried.preview.onclick();await retried.submit();
  let submissions=retried.requests.filter(item=>item.path==='/workflows/start');assert.equal(submissions[0].body.request_key,submissions[1].body.request_key,'preview preserves retry idempotency');
  retried.input('Changed task');await retried.submit();submissions=retried.requests.filter(item=>item.path==='/workflows/start');assert.notEqual(submissions[1].body.request_key,submissions[2].body.request_key);

  const saved=run(),word=await setup({data:{...catalog(),runs:[saved]}});await flush();
  assert.equal(word.requests.filter(item=>item.path==='/export/word').length,0,'completed work is not exported in the background');
  const download=word.parent.querySelectorAll('button').find(item=>item.textContent==='Download Word document');assert.ok(download);assert.equal(download.type,'button');
  await download.onclick();assert.deepEqual(word.requests.find(item=>item.path==='/export/word').body,{title:saved.question,text:saved.result.answer});
  assert.equal(word.downloads.length,1);assert.deepEqual(word.downloads[0],{href:'blob:word-1',name:'Evidence summary.docx'});assert.ok(word.createdURLs[0].blob.size>0);
  assert.equal(word.revokedURLs.length,0);await word.runTimers(1000);assert.deepEqual(word.revokedURLs,['blob:word-1']);assert.match(word.parent.textContent,/Word download started/);
  for(const status of ['running','failed','cancelled','interrupted']){const unfinished=await setup({data:{...catalog(),runs:[run(status)]}});await flush();assert.ok(!unfinished.parent.querySelectorAll('button').some(item=>item.textContent==='Download Word document'),status+' has no completed-answer export');}
  const exportError=await setup({data:{...catalog(),runs:[saved]},exportWord:async()=>wordResponse({ok:false,answer:'Answer exceeds the export limit.'})});await flush();
  const failedDownload=exportError.parent.querySelectorAll('button').find(item=>item.textContent==='Download Word document');await failedDownload.onclick();
  assert.match(exportError.parent.textContent,/Answer exceeds the export limit/);assert.equal(failedDownload.disabled,false);assert.equal(exportError.createdURLs.length,0);
  const wrongType=await setup({data:{...catalog(),runs:[saved]},exportWord:async()=>wordResponse({type:'text/html'})});await flush();await wrongType.parent.querySelectorAll('button').find(item=>item.textContent==='Download Word document').onclick();assert.equal(wrongType.downloads.length,0);assert.match(wrongType.parent.textContent,/did not return a Word document/);
  const lateExport=deferred(),leaving=await setup({data:{...catalog(),runs:[saved]},exportWord:()=>lateExport.promise});await flush();
  const lateDownload=leaving.parent.querySelectorAll('button').find(item=>item.textContent==='Download Word document').onclick();leaving.api.dispose();lateExport.resolve(wordResponse());await lateDownload;
  assert.equal(leaving.createdURLs.length,0,'leaving a task discards its pending download before making a blob URL');
  await flush();
  console.log('WORKFLOWS PASS: metadata-only preview, stale reply refusal, capacity/idempotency, completed-answer Word download, blob URL cleanup and export errors.');
})().catch(error=>{console.error(error);process.exitCode=1;});
