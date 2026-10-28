/* Local readiness and durable note decisions. All user text is rendered as text. */
(() => {
  'use strict';
  const SETTINGS=Object.freeze({requestMs:10000,refreshMs:10000,preferenceKeyChars:80,preferenceValueChars:2000,preferenceCount:64});
  let generation=0;const timers=new Set();
  const make=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const button=(text,handler)=>{const node=make('button','control',text);node.type='button';node.onclick=handler;return node;};
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(SETTINGS.requestMs),...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const data=await response.json();if(!response.ok){const error=Error(data.answer||'The local service is unavailable.');error.status=response.status;throw error;}return data;
  }
  const preferenceMap=value=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length<=SETTINGS.preferenceCount&&Object.entries(value).every(([key,text])=>key.trim().length>0&&key.length<=SETTINGS.preferenceKeyChars&&typeof text==='string'&&text.trim().length>0&&text.length<=SETTINGS.preferenceValueChars);
  const samePreferences=(first,second)=>preferenceMap(first)&&preferenceMap(second)&&Object.keys(first).length===Object.keys(second).length&&Object.keys(first).every(key=>Object.prototype.hasOwnProperty.call(second,key)&&first[key]===second[key]);
  function dispose(){generation++;for(const timer of timers)clearTimeout(timer);timers.clear();}
  function section(parent,title,description){const box=make('section','local-service');box.append(make('h2',null,title),make('p','answer-meta',description));parent.append(box);return box;}
  function mountHealth(parent){
    const current=generation,box=section(parent,'Readiness','Checks local setup without opening the microphone or making a model call.'),status=make('p','answer-meta','Checking…'),list=make('div'),refresh=button('Refresh checks',load);
    const checks=make('details');checks.append(make('summary',null,'View setup checks'),list);
    status.setAttribute('role','status');box.append(status,refresh,checks);
    async function load(){
      refresh.disabled=true;
      try{const data=await request('/health');if(current!==generation||!box.isConnected)return;
        if(data.schema_version!==1||!Array.isArray(data.checks))throw Error('The readiness response could not be verified.');
        list.replaceChildren();
        for(const check of data.checks){const row=make('div','list-row'),copy=make('div');copy.append(make('strong',null,check.title),make('p',null,check.detail));
          if(check.action)copy.append(make('p','answer-meta',check.action));row.append(copy,make('span','service-state',String(check.status).replaceAll('_',' ')));list.append(row);}
        const guidance=data.first_run?.guidance;
        if(Array.isArray(guidance)&&guidance.length){const steps=make('ol');for(const item of guidance)steps.append(make('li',null,typeof item==='string'?item:item.action||item.detail||item.title));list.append(steps);}
        const attention=data.checks.filter(item=>item.status==='blocked'||item.status==='warning').length;
        status.textContent=attention?attention+' setup '+(attention===1?'check needs':'checks need')+' attention. Open the checks for guidance.':'Local checks refreshed. Physical voice and screen-sharing behavior need their own checks.';
      }catch(error){if(current===generation&&box.isConnected)status.textContent=error.message;}
      finally{if(current===generation&&box.isConnected)refresh.disabled=false;}
    }
    load();return box;
  }
  function mountApprovals(parent, preferences=false){
    const endpoint=preferences?'/preference-actions':'/approvals',noun=preferences?'preference change':'note edit';
    const current=generation,box=section(parent,preferences?'Preference decisions':'Note decisions',preferences?'Review exact saved preferences before changing them. Undo requires the same complete preference state.':'Review queued edits and undo verified edits when the note still matches.'),status=make('p','answer-meta','Loading…'),list=make('div'),detail=make('div');
    status.setAttribute('role','status');box.append(status,list,detail);let selected=null,serial=0,busy=false,loadSerial=0,timer=null;
    const alive=()=>current===generation&&box.isConnected;
    async function load(){
      const sequence=++loadSerial;if(timer!==null){clearTimeout(timer);timers.delete(timer);timer=null;}
      try{const data=await request(endpoint);if(!alive()||sequence!==loadSerial)return;
        if(!Array.isArray(data.items))throw Error('The note decision list could not be verified.');
        list.replaceChildren();status.textContent=data.pending_count?data.pending_count+' '+noun+(data.pending_count===1?' needs':'s need')+' a decision.':'No '+noun+'s waiting.';
        for(const item of data.items){const row=make('div','list-row'),copy=make('div');copy.append(make('strong',null,item.title||'Note edit'),make('p',null,String(item.status).replaceAll('_',' ')));row.append(copy,button('Review',()=>review(item.id)));list.append(row);}
      }catch(error){if(alive()&&sequence===loadSerial)status.textContent=error.message;}
      finally{if(alive()&&sequence===loadSerial){timer=setTimeout(()=>{timers.delete(timer);timer=null;load();},SETTINGS.refreshMs);timers.add(timer);}}
    }
    async function review(id){
      if(busy)return;const sequence=++serial;selected=id;detail.replaceChildren(make('p','answer-meta','Opening exact changes…'));
      try{const item=await request(endpoint+'?id='+encodeURIComponent(id));if(!alive()||sequence!==serial)return;
        if(item.id!==id||!/^[a-f0-9]{64}$/.test(item.fingerprint)||(preferences?(item.kind!=='preferences'||!preferenceMap(item.before?.preferences)||!preferenceMap(item.after?.preferences)||!/^[a-f0-9]{64}$/.test(item.before?.revision)||!/^[a-f0-9]{64}$/.test(item.after?.revision)):typeof item.before?.text!=='string'||typeof item.after?.text!=='string'))throw Error('The exact '+(preferences?'preference state':'note contents')+' could not be verified.');
        detail.replaceChildren();detail.append(make('h3',null,item.title||'Note edit'),make('p','answer-meta',preferences?'Decision applies only to the complete before and after preference maps shown below.':'Decision applies only to these exact contents and the original file revision.'));
        const changes=make('details','workflow-output');changes.open=true;changes.append(make('summary',null,'Review contents'),make('h4',null,'Before'),make('pre',null,preferences?JSON.stringify(item.before.preferences,null,2):item.before_text??item.before?.text??''),make('h4',null,'After'),make('pre',null,preferences?JSON.stringify(item.after.preferences,null,2):item.after_text??item.text??item.after?.text??''));detail.append(changes);
        const actions=make('div','card-actions');
        if(item.status==='pending')actions.append(button('Approve and save',()=>decide(item,'approve')),button('Reject edit',()=>decide(item,'reject')));
        if(item.status==='applied'&&item.can_undo===true)actions.append(button('Undo this edit',()=>decide(item,'undo')));
        detail.append(actions);
      }catch(error){if(alive()&&sequence===serial)detail.replaceChildren(make('p','answer-meta',error.message));}
    }
    async function decide(item,decision){
      if(busy||selected!==item.id)return;busy=true;detail.querySelectorAll('button').forEach(node=>node.disabled=true);
      try{const response=await request(endpoint+(decision==='undo'?'/undo':'/decide'),{id:item.id,fingerprint:item.fingerprint,...(decision==='undo'?{}:{decision})});if(!alive())return;
        const expected=decision==='undo'?'undone':decision==='approve'?'applied':'rejected';
        if(response.approval?.id!==item.id||response.approval?.fingerprint!==item.fingerprint||response.approval?.status!==expected||(decision!=='reject'&&response.result?.verified!==true))throw Error('The note decision could not be confirmed. Reopen the note before retrying.');
        if(preferences&&decision!=='reject'){const target=decision==='undo'?item.before:item.after;if(response.result.revision!==target.revision||!samePreferences(response.result.preferences,target.preferences))throw Error('The preference result does not match the reviewed state. Inspect memory before another change.');}
        if(response.result?.graph)window.JarvisUI?.refreshGraph?.();
        detail.replaceChildren(make('p','answer-meta',preferences?(decision==='undo'?'Preference change undone and verified.':decision==='approve'?'Preference change saved and verified.':'Preference change rejected.'):(decision==='undo'?'Edit undone and verified.':decision==='approve'?'Edit saved and verified.':'Edit rejected.')));await load();
      }catch(error){if(alive())detail.append(make('p','answer-meta',error.message));}
      finally{busy=false;if(alive())detail.querySelectorAll('button').forEach(node=>node.disabled=false);}
    }
    load();return box;
  }
  function mountPreferences(parent){
    const current=generation,box=section(parent,'Saved preferences','Only preferences you explicitly request are saved. Changes need review in Work; credentials are refused.'),list=make('div'),status=make('p','answer-meta','Loading preferences…'),form=make('form','preference-form'),key=make('input'),value=make('input'),save=make('button','control','Review new preference');
    key.placeholder='Preference name';value.placeholder='What should Jarvis remember?';key.required=value.required=true;key.maxLength=SETTINGS.preferenceKeyChars;value.maxLength=SETTINGS.preferenceValueChars;key.setAttribute('aria-label','Preference name');value.setAttribute('aria-label','Preference value');status.setAttribute('role','status');save.type='submit';save.disabled=true;form.append(key,value,save);box.append(list,form,status);let state=null,busy=false,pending=null;
    const alive=()=>current===generation&&box.isConnected;
    function controls(){box.querySelectorAll('button').forEach(node=>node.disabled=busy);save.disabled=busy||!state;key.disabled=value.disabled=busy;}
    async function propose(operation,name,text){
      if(busy||!state||!alive())return;name=name.trim();text=operation==='set'?text.trim():undefined;
      if(!name||operation==='set'&&!text){status.textContent='Enter a preference name and value before requesting review.';return;}
      const intent=JSON.stringify([operation,name,text]);if(!pending||pending.intent!==intent||pending.confirmed&&pending.body.revision!==state.revision)pending={intent,confirmed:false,body:{operation,key:name,...(operation==='set'?{value:text}:{}),revision:state.revision,request_key:window.JarvisUI.operationKey()}};
      const proposal=pending;busy=true;controls();
      try{const action=await request('/preference-actions/propose',proposal.body);if(!alive())return;
        const states=['pending','applying','applied','rejected','expired','stale','uncertain','undoing','undone','undo_stale','undo_uncertain'];
        if(!states.includes(action.status)||action.kind!=='preferences'||action.operation!==operation||action.key!==name||!/^[a-f0-9]{24}$/.test(action.id)||!/^[a-f0-9]{64}$/.test(action.fingerprint)||!preferenceMap(action.before?.preferences)||!preferenceMap(action.after?.preferences)||action.before.revision!==proposal.body.revision)throw Error('The preference review could not be verified.');
        const expected={...action.before.preferences};if(operation==='set')Object.defineProperty(expected,name,{value:text,enumerable:true,configurable:true,writable:true});else delete expected[name];if(!samePreferences(expected,action.after.preferences))throw Error('The preference review does not match the requested change.');
        proposal.confirmed=true;status.replaceChildren(make('span',null,action.status==='pending'?'Queued for exact review. Nothing has changed yet. ':'This request already has a '+action.status.replaceAll('_',' ')+' decision. Inspect it in Work. '),button('Open review in Work',()=>window.JarvisUI?.setWorkspace?.('work')));
        if(['expired','rejected','stale','undo_stale'].includes(action.status))status.append(button('Start a new review',async()=>{pending=null;status.textContent='Check the preference fields, then submit a new review.';await load(false);}));
        if(action.status!=='pending')await load(false);
      }catch(error){if(alive()){if([400,404,409,429].includes(error.status)&&pending===proposal)pending=null;status.textContent=error.message;await load(false);}}finally{busy=false;if(alive())controls();}
    }
    async function load(show=true){try{const data=await request('/preferences');if(!alive())return;if(!preferenceMap(data.preferences)||!/^[a-f0-9]{64}$/.test(data.revision))throw Error('Saved preferences could not be verified.');state=data;list.replaceChildren();for(const [name,text]of Object.entries(data.preferences)){const row=make('div','list-row');row.append(make('p',null,name+': '+text),button('Review forgetting '+name,()=>propose('forget',name)));list.append(row);}controls();if(show)status.textContent=Object.keys(data.preferences).length?'Saved preferences loaded.':'No saved preferences yet.';}catch(error){if(alive())status.textContent=error.message;}}
    form.onsubmit=event=>{event.preventDefault();return propose('set',key.value,value.value);};load();return box;
  }
  window.JarvisLocalServices={mountHealth,mountApprovals,mountPreferences,mountPreferenceApprovals:parent=>mountApprovals(parent,true),dispose};window.addEventListener('pagehide',dispose);
})();
