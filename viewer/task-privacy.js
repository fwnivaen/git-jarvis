/* Explicit removal of settled Work data. Opening this view never erases it. */
(() => {
  'use strict';
  const SETTINGS=Object.freeze({requestMs:10000,idPattern:/^[a-f0-9]{24}$/,revisionPattern:/^[a-f0-9]{64}$/});
  const STATES=new Set(['retained','pending','erased']);
  let generation=0;
  const make=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const button=(label,handler)=>{const node=make('button','control',label);node.type='button';node.onclick=handler;return node;};
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(SETTINGS.requestMs),...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const data=await response.json();if(!response.ok)throw Error(data.answer||'Saved task privacy is unavailable.');return data;
  }
  function dispose(){generation++;}
  function mount(parent){
    const current=generation,box=make('section','local-service'),status=make('p','answer-meta','Reading saved task privacy…'),list=make('div'),review=make('div');
    const refresh=button('Refresh saved tasks',()=>load());
    status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    box.append(make('h2',null,'Saved task privacy'),make('p','answer-meta','Work saves questions, answers and source passages for recovery. Review one finished task to remove its saved contents. Nothing is removed automatically.'),status,refresh,list,review);parent.append(box);
    const alive=()=>current===generation&&box.isConnected;
    let serial=0,busy=false,plan=null;
    const active=sequence=>alive()&&sequence===serial;
    function setBusy(value){busy=value;for(const node of box.querySelectorAll('button'))node.disabled=value||node.dataset.unavailable==='true';}
    function clearReview(){plan=null;review.replaceChildren();}
    function renderCatalog(data){
      if(data.schema_version!==1||typeof data.available!=='boolean'||!Array.isArray(data.items)||data.items.some(item=>!SETTINGS.idPattern.test(item.id)||!STATES.has(item.state)||typeof item.eligible!=='boolean'||typeof item.label!=='string'))throw Error('Saved task privacy could not be verified.');
      list.replaceChildren();clearReview();
      for(const item of data.items){
        const row=make('div','list-row'),copy=make('div');copy.append(make('strong',null,item.label),make('p','answer-meta',item.state==='pending'?'Removal pending; saved contents may remain.':item.state==='erased'?'Saved task contents removed.':item.status+(item.eligible?' · Ready to review':' · Still running, settling, or unavailable for removal')));row.append(copy);
        if(item.state!=='erased'){
          const open=button(item.state==='pending'?'Review pending cleanup':'Review removal',()=>preview(item));open.dataset.unavailable=String(!item.eligible);open.disabled=!item.eligible;row.append(open);
        }list.append(row);
      }
      return data;
    }
    async function load(show=true){
      if(!alive()||busy)return;
      const sequence=++serial;
      setBusy(true);clearReview();
      try{const data=await request('/workflows/retention');if(!active(sequence))return;renderCatalog(data);if(show)status.textContent=!data.available?'No readable saved Work store is available.':data.items.length?'Choose a task to review. This does not change Obsidian notes or preferences.':'No retained Work tasks.';}
      catch(error){if(active(sequence)){list.replaceChildren();status.textContent=error.message;}}
      finally{if(active(sequence))setBusy(false);}
    }
    async function preview(item){
      if(busy||!alive())return;const sequence=++serial;setBusy(true);clearReview();status.textContent='Reviewing this saved task…';
      try{
        const data=await request('/workflows/erase-plan',{id:item.id});if(!active(sequence))return;
        if(data.schema_version!==1||data.id!==item.id||!SETTINGS.revisionPattern.test(data.revision)||!STATES.has(data.state)||typeof data.eligible!=='boolean'||!Array.isArray(data.effects)||data.effects.some(text=>typeof text!=='string')||!Array.isArray(data.exclusions)||data.exclusions.some(text=>typeof text!=='string')||!Number.isSafeInteger(data.total_bytes)||data.total_bytes<0)throw Error('The exact removal review could not be verified.');
        plan=data;review.append(make('h3',null,'Review saved task removal'),make('p',null,item.label));
        const effects=make('ul');for(const text of data.effects)effects.append(make('li',null,text));review.append(effects,make('p','answer-meta','Reviewed task data: '+data.total_bytes.toLocaleString()+' bytes. Pending cleanup may already have removed some files.'));
        review.append(make('h4',null,'Limits'));const exclusions=make('ul');for(const text of data.exclusions)exclusions.append(make('li',null,text));review.append(exclusions);
        const actions=make('div','card-actions'),confirm=button(data.state==='pending'?'Retry reviewed cleanup':'Erase saved task',()=>erase(data,sequence));confirm.dataset.unavailable=String(!data.eligible);confirm.disabled=!data.eligible;
        actions.append(confirm,button('Keep saved task',()=>{if(busy||!alive())return;serial++;clearReview();status.textContent='Removal review closed. No new removal was requested.';}));review.append(actions);
        status.textContent=data.eligible?'Review the effects and limits. Confirming removal cannot be undone.':data.reason||'This task cannot be removed in its current state.';
      }catch(error){if(active(sequence)){clearReview();status.textContent=error.message;}}
      finally{if(active(sequence))setBusy(false);}
    }
    async function erase(reviewed,sequence){
      if(busy||!active(sequence)||plan!==reviewed||!reviewed.eligible)return;setBusy(true);status.textContent='Removing only this saved task…';
      try{
        const data=await request('/workflows/erase',{id:reviewed.id,expected_revision:reviewed.revision,confirmed:true});if(!active(sequence))return;
        if(data.schema_version!==1||data.id!==reviewed.id||data.revision!==reviewed.revision||!['erased','pending'].includes(data.state)||data.erased!==(data.state==='erased')||typeof data.retryable!=='boolean')throw Error('Removal could not be confirmed. Refresh saved tasks and inspect the current state.');
        clearReview();const receiptText=data.erased?'This task’s saved contents were removed. A minimal duplicate-prevention record remains.':'Removal is pending. Saved contents may remain; refresh and review cleanup before retrying.';status.textContent=receiptText;
        // Fetching the receipt never automatically authorizes a second removal.
        try{const fresh=await request('/workflows/retention');if(active(sequence))renderCatalog(fresh);}
        catch{if(active(sequence))status.textContent=receiptText+' The task list could not refresh.';}
      }catch(error){if(active(sequence)){clearReview();status.textContent=error.message;}}
      finally{if(active(sequence))setBusy(false);}
    }
    load();return box;
  }
  window.JarvisTaskPrivacy={mount,dispose};window.addEventListener('pagehide',dispose);
})();
