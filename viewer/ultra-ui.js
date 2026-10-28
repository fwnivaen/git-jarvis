// Explicit imports and preferences. All model activity remains metadata only.
(() => {
  'use strict';
  const SETTINGS=Object.freeze({pollMs:1500,maxFileBytes:16*1024*1024,recentRuns:5,requestMs:50000,preferenceKeyChars:80,preferenceValueChars:2000});
  let workflow='',screenGeneration=0,stopped=false,timer=null;
  const pendingWrites=new Map();
  const make=(tag,text)=>{const element=document.createElement(tag);if(text!==undefined)element.textContent=text;return element;};
  async function request(path,body){
    let pending;
    if(body!==undefined){const fingerprint=JSON.stringify(body);pending=pendingWrites.get(path);if(!pending||pending.fingerprint!==fingerprint){pending={fingerprint,key:window.JarvisUI.operationKey()};pendingWrites.set(path,pending);}body={...body,operation_session:pending.key};}
    const response=await fetch(path,{...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),signal:AbortSignal.timeout(SETTINGS.requestMs)});
    const data=await response.json();if(!response.ok)throw Error(data.answer||'The local operation could not be confirmed.');if(pendingWrites.get(path)===pending)pendingWrites.delete(path);return data;
  }
  function section(parent,title){const block=make('section');block.className='ultra-settings material';block.append(make('h2',title));parent.append(block);return block;}
  function fileControl(parent){
    const input=make('input');input.type='file';input.accept='.pdf,.docx,.xlsx';input.setAttribute('aria-label','Import a PDF, Word document or spreadsheet');
    const status=make('p','PDF, Word or Excel · up to 16 MiB. Scanned PDFs need OCR first.');status.className='answer-meta';status.setAttribute('role','status');
    input.onchange=async()=>{
      const file=input.files?.[0];if(!file)return;if(file.size>SETTINGS.maxFileBytes){status.textContent='Choose a file smaller than 16 MiB.';return;}
      input.disabled=true;status.textContent='Turning your document into a note…';
      try{
        const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
        const result=await request('/memory/import',{filename:file.name,data});
        await window.JarvisUI?.refreshGraph?.();status.textContent=(result.created?'Imported: ':'Already in your notes: ')+result.note.title;
      }catch(error){status.textContent=error.message||'The import could not be confirmed.';}finally{input.disabled=false;input.value='';}
    };
    parent.append(input,status);
  }
  async function mountNotes(parent){
    try{const state=await request('/memory');if(!parent.isConnected||!state.imports_enabled)return;fileControl(section(parent,'Import a document'));}catch{}
  }
  async function mountSettings(parent){
    try{
      const [state,templates,runs]=await Promise.all([request('/memory'),request('/agents/workflows'),request('/agents/runs')]);
      if(!parent.isConnected)return;
      if(state.enabled||state.imports_enabled){
        const block=section(parent,'Memory');
        if(state.enabled)block.append(make('p',state.source==='obsidian'?'Obsidian notes use local keyword search and bounded excerpts.':state.semantic.semantic_ready?'Semantic and keyword search are ready.':'Keyword search is ready. Semantic search loads when you ask about your notes.'));
        if(state.imports_enabled)fileControl(block);
        if(state.preferences_enabled)window.JarvisLocalServices?.mountPreferences(block);
      }
      if(templates.enabled){const block=section(parent,'Crew workflow'),select=make('select'),automatic=make('option','Automatic');automatic.value='';select.append(automatic);
        for(const item of templates.workflows){const option=make('option',item.label||item.name||item.id);option.value=item.id;select.append(option);}select.value=workflow;select.setAttribute('aria-label','Workflow for your next request');select.onchange=()=>{workflow=select.value;};block.append(select,make('p','Choose how the crew approaches your next request. Automatic keeps quick questions fast.'));
        if(runs.enabled&&runs.runs.length){block.append(make('h3','Recent crew work'));for(const run of runs.runs.slice(0,SETTINGS.recentRuns))block.append(make('p',run.workflow+' · '+run.status+' · '+(run.evaluation?.total_tokens??'—')+' tokens'));}
      }
    }catch{}
  }
  async function pollScreen(){
    if(stopped)return;
    try{const state=await request('/tools/screen');if(state.generation>screenGeneration&&(!state.action||window.JarvisScreen)){screenGeneration=state.generation;if(state.action&&window.JarvisScreen)await window.JarvisScreen.command(state.action==='stop'?'stop watching my screen':'watch my screen');}}catch{}
    if(!stopped)timer=setTimeout(pollScreen,SETTINGS.pollMs);
  }
  window.JarvisUltra={mountSettings,mountNotes,chatOptions:()=>workflow?{workflow}:{}};
  window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);});pollScreen();
})();
