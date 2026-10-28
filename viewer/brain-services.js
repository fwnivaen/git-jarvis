/* Local memory, operations, selected-data export and retained activity. */
(() => {
  'use strict';
  const SETTINGS=Object.freeze({requestMs:10000,exportBytes:20*1024*1024,revokeMs:1000,maxQuery:2000,maxSourceURI:4096});
  let generation=0;const downloads=new Set(),timers=new Set();
  const make=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const button=(text,handler)=>{const node=make('button','control',text);node.type='button';node.onclick=handler;return node;};
  const section=(parent,title,description)=>{const box=make('section','local-service');box.append(make('h2',null,title),make('p','answer-meta',description));parent.append(box);return box;};
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(SETTINGS.requestMs),...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const data=await response.json();if(!response.ok)throw Error(data.answer||'The local service is unavailable.');return data;
  }
  function dispose(){generation++;for(const timer of timers)clearTimeout(timer);timers.clear();for(const url of downloads)URL.revokeObjectURL(url);downloads.clear();}
  function obsidianLink(uri,label='Open in Obsidian'){
    try{if(typeof uri!=='string'||uri.length>SETTINGS.maxSourceURI||uri!==uri.trim())return null;const url=new URL(uri),paths=url.searchParams.getAll('path');
      if(url.protocol!=='obsidian:'||url.hostname!=='open'||url.username||url.password||url.port||url.hash||url.pathname&&url.pathname!=='/'||[...url.searchParams.keys()].some(key=>key!=='path')||paths.length!==1||!paths[0].startsWith('/')||/[\u0000-\u001f]/.test(paths[0]))return null;}
    catch{return null;}
    const link=make('a','control',label);link.href=uri;link.rel='noreferrer';return link;
  }
  function mountBrain(parent){
    const current=generation,box=section(parent,'Obsidian brain','One folder of Markdown notes, shared with Obsidian. Local searches below use no model.'),status=make('p','answer-meta','Reading memory settings…'),controls=make('div','card-actions brain-controls'),meta=make('p','answer-meta'),results=make('div');
    status.setAttribute('role','status');box.append(status,controls,meta);
    const form=make('form','workflow-form brain-search'),query=make('input'),search=make('button','control','Search locally');query.type='search';query.maxLength=SETTINGS.maxQuery;query.placeholder='Find a fact, project, or decision';query.setAttribute('aria-label','Search Jarvis Brain');query.required=true;search.type='submit';form.append(query,search);box.append(form,results);
    const alive=()=>current===generation&&box.isConnected;let state=null,serial=0,busy=false;
    function render(data){
      if(!alive())return;
      if(data.schema_version!==1||typeof data.active!=='boolean'||typeof data.configured!=='boolean'||!Number.isInteger(data.context_chars))throw Error('Memory settings could not be verified.');
      serial++;results.replaceChildren();state=data;controls.replaceChildren();search.disabled=!data.active;query.disabled=!data.active;
      status.textContent=data.restart_required?'Folder selection saved. Jarvis must restart to apply it.':data.active?'Connected to '+data.vault_name+'. '+data.index.notes+' shared notes indexed.':data.configured?'Folder prepared; connection will apply when Jarvis restarts.':'Your separate Jarvis Brain folder is ready to prepare.';
      if(!data.configured){controls.append(make('p','answer-meta','New folder: '+data.proposed_folder),button('Prepare this folder',async()=>{
        if(busy)return;busy=true;try{render(await request('/brain/setup',{confirmed:true}));}catch(error){if(alive())status.textContent=error.message;}finally{busy=false;}
      }));}
      if(data.active)controls.append(button('Refresh notes',async()=>{if(busy)return;busy=true;try{const fresh=await request('/brain/refresh',{});if(alive()){render(fresh);window.JarvisUI?.refreshGraph?.();}}catch(error){if(alive())status.textContent=error.message;}finally{busy=false;}}));
      if(data.configured)controls.append(button('Disconnect after restart',async()=>{if(busy)return;busy=true;try{const fresh=await request('/brain/disconnect',{confirmed:true});if(alive())render(fresh);}catch(error){if(alive())status.textContent=error.message;}finally{busy=false;}}));
      const budget=make('select');budget.setAttribute('aria-label','Maximum note context');for(const value of [3000,6000,12000]){const option=make('option',null,value.toLocaleString()+' characters');option.value=String(value);budget.append(option);}budget.value=String(data.context_chars);
      budget.onchange=async()=>{if(busy)return;busy=true;budget.disabled=true;try{const fresh=await request('/brain/settings',{context_chars:Number(budget.value)});if(alive())render(fresh);}catch(error){if(alive()){budget.value=String(state.context_chars);status.textContent=error.message;}}finally{busy=false;if(alive())budget.disabled=false;}};
      const budgetRow=make('label','list-row brain-budget');budgetRow.append(make('span',null,'Maximum relevant note text per model request'),budget);controls.append(budgetRow);
      meta.textContent=(data.metrics?.model_calls_avoided||0)+' explicit note lookups handled without a model in this server session. Source text is bounded; this is not a billed-token measurement.';
    }
    query.oninput=()=>{if(!alive())return;serial++;results.replaceChildren();search.disabled=!state?.active;status.textContent='Search changed. Submit it to find current shared notes.';};
    form.onsubmit=async event=>{event.preventDefault();if(!state?.active||busy||!query.value.trim())return;const sequence=++serial;search.disabled=true;results.replaceChildren();status.textContent='Searching local notes…';
      try{const data=await request('/brain/search',{query:query.value.trim()});if(!alive()||sequence!==serial)return;if(data.schema_version!==1||data.model_calls!==0||!Array.isArray(data.items))throw Error('The local search response could not be verified.');
        for(const item of data.items){if(typeof item.title!=='string'||typeof item.text!=='string')throw Error('A source could not be verified.');const note=make('article','local-service'),actions=make('div','card-actions');note.append(make('h3',null,item.title),make('pre','brain-passage',item.text));
          const link=obsidianLink(item.source?.uri);if(link)actions.append(link);if(Number.isInteger(item.id)&&item.id>=0&&/^[a-f0-9]{64}$/.test(item.source?.stable_id)){const read=button('Read in Jarvis',async()=>{if(read.disabled)return;read.disabled=true;try{if(await window.JarvisUI?.openBrainSource?.(item.id,item.source.stable_id)!==true)throw Error('This source is no longer available. Refresh your notes before opening it.');}catch(error){if(alive()&&sequence===serial)status.textContent=error.message||'This source could not be opened.';}finally{if(alive()&&sequence===serial)read.disabled=false;}});actions.append(read);}note.append(actions);results.append(note);}
        status.textContent=data.items.length?data.items.length+' matching '+(data.items.length===1?'note':'notes')+'. No model call was made.':'No matching shared notes. No model call was made.';
      }catch(error){if(alive()&&sequence===serial)status.textContent=error.message;}finally{if(alive()&&sequence===serial)search.disabled=false;}
    };
    request('/brain').then(data=>{if(alive())render(data);}).catch(error=>{if(alive())status.textContent=error.message;});return box;
  }
  function mountOperations(parent){
    const current=generation,box=section(parent,'Background and daily checks','Daily checks run only while Jarvis is running and never call a model. Quiet hours affect background updates.'),status=make('p','answer-meta','Reading local settings…'),form=make('form'),fields=make('div'),save=make('button','control','Save local settings'),signals=make('p','answer-meta');
    const run=button('Run health check now',async()=>{if(busy||!alive())return;const sequence=++serial;setBusy(true);try{const data=await request('/operations/run',{});if(active(sequence)){status.textContent=data.status==='complete'?'Local health check finished.':data.status==='busy'?'A health check is already running.':'Health check could not finish.';await load(false,sequence);}}catch(error){if(active(sequence))status.textContent=error.message;}finally{if(active(sequence))setBusy(false);}});
    status.setAttribute('role','status');save.type='submit';save.disabled=true;form.append(fields,save);box.append(status,form,run,signals);let state=null,inputs={},serial=0,busy=false;const alive=()=>current===generation&&box.isConnected,active=sequence=>alive()&&sequence===serial;
    function setBusy(value){busy=value;save.disabled=busy||!state;run.disabled=busy;for(const input of Object.values(inputs))input.disabled=busy;}
    const definitions=[['daily_health_enabled','Daily health checks','checkbox'],['daily_health_time','Daily check time · New York','time'],['quiet_hours_enabled','Quiet hours','checkbox'],['quiet_start','Quiet hours start','time'],['quiet_end','Quiet hours end','time'],['speak_background_updates','Speak background updates','checkbox'],['local_signals_enabled','Read local battery and network status','checkbox'],['minimum_battery_percent','Defer daily checks below battery percent','number'],['external_power_required','Require external power for daily checks','checkbox'],['network_required','Require a configured network for daily checks','checkbox']];
    function render(data){
      if(data.schema_version!==1||!data.settings||!/^[a-f0-9]{64}$/.test(data.revision))throw Error('Local settings could not be verified.');state=data;fields.replaceChildren();inputs={};
      for(const [key,label,type] of definitions){const row=make('label','list-row'),input=make('input');input.type=type;input.setAttribute('aria-label',label);if(type==='checkbox')input.checked=data.settings[key]===true;else input.value=String(data.settings[key]);if(type==='number'){input.min='0';input.max='100';input.step='1';}inputs[key]=input;row.append(make('span',null,label),input);fields.append(row);}
      const battery=data.signals?.battery,network=data.signals?.network;signals.textContent=data.settings.local_signals_enabled?(battery?.status==='known'?(battery.power_source==='external'?'External power':'Battery')+' · '+battery.percent+'%. ':'Battery status unknown. ')+(network?.status==='known'?(network.configured?'Network configured; internet not tested.':'No configured network found.'):'Network status unknown.'):'Battery and network checks are off.';
      setBusy(busy);
    }
    async function load(show=true,sequence=++serial){try{const data=await request('/operations');if(active(sequence)){render(data);if(show)status.textContent=data.notice?'Local maintenance settings need attention.':data.scheduled?'Daily checks enabled while Jarvis is running.':'Automatic daily checks are off.';}}catch(error){if(active(sequence))status.textContent=error.message;}}
    form.onsubmit=async event=>{event.preventDefault();if(!state||busy||!alive())return;const sequence=++serial,settings={};for(const [key,,type]of definitions)settings[key]=type==='checkbox'?inputs[key].checked:type==='number'?Number(inputs[key].value):inputs[key].value;setBusy(true);
      try{const data=await request('/operations/settings',{settings,expected_revision:state.revision});if(active(sequence)){render(data);status.textContent='Local settings saved.';}}catch(error){if(active(sequence)){status.textContent=error.message;await load(false,sequence);}}finally{if(active(sequence))setBusy(false);}
    };load();return box;
  }
  function mountActivity(parent){
    const current=generation,box=section(parent,'Task and approval history','Recent verified states from retained local work. Chat transcripts and note contents are excluded.'),status=make('p','answer-meta','Reading recent activity…'),list=make('div'),refresh=button('Refresh history',load);status.setAttribute('role','status');box.append(status,refresh,list);const alive=()=>current===generation&&box.isConnected;
    async function load(){refresh.disabled=true;try{const data=await request('/activity');if(!alive())return;if(data.schema_version!==1||!Array.isArray(data.items))throw Error('Activity could not be verified.');list.replaceChildren();
        for(const item of data.items){const row=make('div','list-row'),copy=make('div');copy.append(make('strong',null,item.label),make('p','answer-meta',new Date(item.at*1000).toLocaleString()));row.append(copy);if(item.source==='workflows'&&/^[a-f0-9]{24}$/.test(item.subject_id))row.append(button('View saved work',()=>window.JarvisUI?.openWorkRun?.(item.subject_id)));if(item.source==='approvals')row.append(button('Review decisions',()=>window.JarvisUI?.setWorkspace?.('work')));list.append(row);}
        status.textContent=data.items.length?data.items.length+' recent events'+(data.truncated?' · older events are outside this window.':'.'):'No retained task or approval activity yet.';
      }catch(error){if(alive())status.textContent=error.message;}finally{if(alive())refresh.disabled=false;}}
    load();return box;
  }
  function mountExport(parent){
    const current=generation,box=section(parent,'Export your data','Download only selected sections. Private notes, credentials and internal task checkpoints are excluded.'),choices=make('div'),status=make('p','answer-meta'),selected={};status.setAttribute('role','status');
    for(const [key,title]of [['settings','Settings'],['notes','Shared Markdown notes'],['preferences','Saved preferences'],['workflows','Saved task questions and results'],['activity','Recent activity metadata']]){const label=make('label','list-row'),input=make('input');input.type='checkbox';input.checked=key==='settings';input.setAttribute('aria-label','Export '+title);selected[key]=input;label.append(make('span',null,title),input);choices.append(label);}
    const download=button('Download selected data',async()=>{if(download.disabled||current!==generation||!box.isConnected)return;const sections=Object.keys(selected).filter(key=>selected[key].checked);if(!sections.length){status.textContent='Choose at least one section.';return;}download.disabled=true;status.textContent='Preparing your selected data…';let ownedURL=null,link=null;
      const release=()=>{if(ownedURL&&downloads.delete(ownedURL))URL.revokeObjectURL(ownedURL);ownedURL=null;};
      try{const response=await fetch('/export/data',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sections}),signal:AbortSignal.timeout(SETTINGS.requestMs)});if(!response.ok){const error=await response.json();throw Error(error.answer||'The data export is unavailable.');}if(String(response.headers.get('content-type')).split(';',1)[0].trim().toLowerCase()!=='application/zip')throw Error('The data export could not be verified.');const blob=await response.blob();if(current!==generation||!box.isConnected)return;if(!blob.size||blob.size>SETTINGS.exportBytes)throw Error('The data export exceeds the download limit.');const signature=new Uint8Array(await blob.slice(0,4).arrayBuffer());if(signature[0]!==80||signature[1]!==75||signature[2]!==3||signature[3]!==4)throw Error('The data export is not a verified ZIP archive.');if(current!==generation||!box.isConnected)return;
        ownedURL=URL.createObjectURL(blob);downloads.add(ownedURL);link=make('a');link.href=ownedURL;link.download='Jarvis user data.zip';document.body.append(link);link.click();link.remove();link=null;if(current!==generation||!box.isConnected){release();return;}const url=ownedURL,timer=setTimeout(()=>{timers.delete(timer);if(downloads.delete(url))URL.revokeObjectURL(url);},SETTINGS.revokeMs);timers.add(timer);ownedURL=null;status.textContent='Download prepared. Only your selected sections are included.';
      }catch(error){link?.remove();release();if(current===generation&&box.isConnected)status.textContent=error.message;}finally{if(current===generation&&box.isConnected)download.disabled=false;}
    });box.append(choices,download,status);return box;
  }
  window.JarvisBrainServices={mountBrain,mountOperations,mountActivity,mountExport,obsidianLink,dispose};window.addEventListener('pagehide',dispose);
})();
