/* Local readiness and durable note decisions. All user text is rendered as text. */
(() => {
  'use strict';
  const SETTINGS=Object.freeze({requestMs:10000,refreshMs:10000});
  let generation=0;const timers=new Set();
  const make=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const button=(text,handler)=>{const node=make('button','control',text);node.type='button';node.onclick=handler;return node;};
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(SETTINGS.requestMs),...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const data=await response.json();if(!response.ok)throw Error(data.answer||'The local service is unavailable.');return data;
  }
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
  function mountApprovals(parent){
    const current=generation,box=section(parent,'Note decisions','Review queued edits and undo verified edits when the note still matches.'),status=make('p','answer-meta','Loading…'),list=make('div'),detail=make('div');
    status.setAttribute('role','status');box.append(status,list,detail);let selected=null,serial=0,busy=false,loadSerial=0,timer=null;
    const alive=()=>current===generation&&box.isConnected;
    async function load(){
      const sequence=++loadSerial;if(timer!==null){clearTimeout(timer);timers.delete(timer);timer=null;}
      try{const data=await request('/approvals');if(!alive()||sequence!==loadSerial)return;
        if(!Array.isArray(data.items))throw Error('The note decision list could not be verified.');
        list.replaceChildren();status.textContent=data.pending_count?data.pending_count+' note '+(data.pending_count===1?'edit needs':'edits need')+' a decision.':'No note edits waiting.';
        for(const item of data.items){const row=make('div','list-row'),copy=make('div');copy.append(make('strong',null,item.title||'Note edit'),make('p',null,String(item.status).replaceAll('_',' ')));row.append(copy,button('Review',()=>review(item.id)));list.append(row);}
      }catch(error){if(alive()&&sequence===loadSerial)status.textContent=error.message;}
      finally{if(alive()&&sequence===loadSerial){timer=setTimeout(()=>{timers.delete(timer);timer=null;load();},SETTINGS.refreshMs);timers.add(timer);}}
    }
    async function review(id){
      if(busy)return;const sequence=++serial;selected=id;detail.replaceChildren(make('p','answer-meta','Opening exact changes…'));
      try{const item=await request('/approvals?id='+encodeURIComponent(id));if(!alive()||sequence!==serial)return;
        if(item.id!==id||!/^[a-f0-9]{64}$/.test(item.fingerprint)||typeof item.before?.text!=='string'||typeof item.after?.text!=='string')throw Error('The exact note contents could not be verified.');
        detail.replaceChildren();detail.append(make('h3',null,item.title||'Note edit'),make('p','answer-meta','Decision applies only to these exact contents and the original file revision.'));
        const changes=make('details','workflow-output');changes.open=true;changes.append(make('summary',null,'Review contents'),make('h4',null,'Before'),make('pre',null,item.before_text??item.before?.text??''),make('h4',null,'After'),make('pre',null,item.after_text??item.text??item.after?.text??''));detail.append(changes);
        const actions=make('div','card-actions');
        if(item.status==='pending')actions.append(button('Approve and save',()=>decide(item,'approve')),button('Reject edit',()=>decide(item,'reject')));
        if(item.status==='applied'&&item.can_undo===true)actions.append(button('Undo this edit',()=>decide(item,'undo')));
        detail.append(actions);
      }catch(error){if(alive()&&sequence===serial)detail.replaceChildren(make('p','answer-meta',error.message));}
    }
    async function decide(item,decision){
      if(busy||selected!==item.id)return;busy=true;detail.querySelectorAll('button').forEach(node=>node.disabled=true);
      try{const response=await request(decision==='undo'?'/approvals/undo':'/approvals/decide',{id:item.id,fingerprint:item.fingerprint,...(decision==='undo'?{}:{decision})});if(!alive())return;
        const expected=decision==='undo'?'undone':decision==='approve'?'applied':'rejected';
        if(response.approval?.id!==item.id||response.approval?.fingerprint!==item.fingerprint||response.approval?.status!==expected||(decision!=='reject'&&response.result?.verified!==true))throw Error('The note decision could not be confirmed. Reopen the note before retrying.');
        if(response.result?.graph)window.JarvisUI?.refreshGraph?.();
        detail.replaceChildren(make('p','answer-meta',decision==='undo'?'Edit undone and verified.':decision==='approve'?'Edit saved and verified.':'Edit rejected.'));await load();
      }catch(error){if(alive())detail.append(make('p','answer-meta',error.message));}
      finally{busy=false;if(alive())detail.querySelectorAll('button').forEach(node=>node.disabled=false);}
    }
    load();return box;
  }
  window.JarvisLocalServices={mountHealth,mountApprovals,dispose};window.addEventListener('pagehide',dispose);
})();
