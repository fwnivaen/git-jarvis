const fs=require('node:fs'),vm=require('node:vm');
const SOURCE=fs.readFileSync('viewer/workflows.js','utf8');
class Element{
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.dataset={};this.attributes={};this.listeners={};this.value='';this.hidden=false;this.disabled=false;this._text='';}
  get isConnected(){return this.connected===true||!!this.parentNode?.isConnected;}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join(' ');}
  set textContent(value){for(const child of this.children)child.parentNode=null;this.children=[];this._text=String(value??'');}
  get options(){return this.tagName==='SELECT'?this.children:[];}
  append(...children){for(const child of children){child.parentNode=this;this.children.push(child);}}
  replaceChildren(...children){this.textContent='';this.append(...children);}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name];}
  addEventListener(name,callback){(this.listeners[name]??=[]).push(callback);}
  emit(name){for(const callback of this.listeners[name]||[])callback({target:this});}
  focus(){this.focused=true;}
  remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;}}
  querySelectorAll(selector){const found=[];for(const child of this.children){if(selector.startsWith('.')?child.className?.split(' ').includes(selector.slice(1)):child.tagName===selector.toUpperCase())found.push(child);found.push(...child.querySelectorAll(selector));}return found;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function catalog(activeIds=[]){return {enabled:true,available:true,active:activeIds[0]||null,active_ids:activeIds,
  limits:{concurrent_runs:2,max_workers:3},workflows:[{id:'research',label:'Evidence research'},{id:'code',label:'Code review'}],runs:[]};}
function plan(overrides={}){return {schema_version:1,dry_run:true,admitted:false,engine:'LangGraph',question:'Compare the evidence',workflow:'research',label:'Evidence research',
  roles:[{name:'Research analyst',assignment:'Find supported claims.'},{name:'Source reviewer',assignment:'Compare exact excerpts.'}],criteria:['Cite the source evidence.'],
  steps:[{phase:'retrieve',description:'Search local notes.',tool:'notes_search'},{phase:'assign',description:'Assign selected roles.',workers:2},{phase:'workers',description:'Analyze evidence.'},{phase:'review',description:'Verify arithmetic.',model_review_may_be_skipped:false}],
  permitted_tools:['notes_search','notes_read'],execution_enabled:true,prerequisites:{notes_search_registered:true,runtime_checked:false,model_login_checked:false},
  limits:{concurrent_tasks:2,workers:2,shared_model_calls:3,shared_tool_calls:3,tool_calls_per_task:10,request_timeout_s:240,worker_timeout_s:45,worker_retries:1,max_restarts:2,max_saved_runs:100,
    adaptive_model_budget_enforced:false,model_calls_per_task:null,observed_tokens_per_task:null},
  side_effects:{preview:{model_calls:false,tool_calls:false,task_admission:false,database_creation:false,note_writes:false},
    execution:{model_calls:true,local_note_reads:true,private_checkpoints:true,private_tool_journal:true,stores_question_and_results:true,note_writes:false,external_actions:false}},
  checkpoint_storage:{location:'Private local Work catalog and per-task journals.',created_by_preview:false,contains_source_passages:true},
  execution_limits:['Preview does not verify model login or read source contents.','No web browsing, file edits or general action approvals.'],...overrides};}
function run(status='complete'){return {id:'saved-run',workflow:'research',question:'Evidence summary',status,phase:status,elapsed_s:3,tool_calls:2,total_tokens:null,usage_complete:false,
  created_at:1,agents:[],events:[],result:{answer:'Supported answer from local notes.',nodes:[]},capabilities:{cancel:false}};}
function wordResponse({type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',name='Evidence summary.docx',text='PK test document',ok=true,answer='Word export unavailable.'}={}){
  return {response:{ok,headers:{get(nameRequested){return {'content-type':type,'content-disposition':'attachment; filename="'+name+'"'}[nameRequested.toLowerCase()];}},
    blob:async()=>new Blob([text],{type}),json:async()=>({answer})}};
}
async function setup({data=catalog(),preview=async()=>plan(),start=async()=>{throw Error('Start response not configured');},exportWord=async()=>wordResponse()}={}){
  let nextTimer=0,nextKey=0,nextURL=0;const timers=new Map(),requests=[],streams=[],events=[],listeners={},downloads=[],createdURLs=[],revokedURLs=[];
  const parent=new Element('main');parent.connected=true;
  const body=new Element('body');body.connected=true;
  class EventSource{constructor(path){this.path=path;this.listeners={};streams.push(this);}addEventListener(name,callback){this.listeners[name]=callback;}close(){this.closed=true;}}
  const context={console,AbortSignal:{timeout(){}},EventSource,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    crypto:{getRandomValues(bytes){bytes.fill(++nextKey);return bytes;}},
    URL:{createObjectURL(blob){const url='blob:word-'+(++nextURL);createdURLs.push({url,blob});return url;},revokeObjectURL(url){revokedURLs.push(url);}},
    setTimeout(callback,delay){const id=++nextTimer;timers.set(id,{callback,delay});return id;},clearTimeout(id){timers.delete(id);},
    document:{body,createElement(tag){const element=new Element(tag);if(tag==='a')element.click=()=>downloads.push({href:element.href,name:element.download});return element;}},window:{addEventListener(name,callback){listeners[name]=callback;},dispatchEvent(event){events.push(event);}},
    fetch:async(path,options)=>{
      const body=options.body===undefined?undefined:JSON.parse(options.body);requests.push({path,body,method:options.method||'GET'});
      let result;if(path==='/workflows')result=data;else if(path.startsWith('/workflows/run?id='))result=data.runs.find(item=>item.id===decodeURIComponent(path.split('=')[1]));
      else if(path==='/stage2/tasks/plan')result=await preview(body);else if(path==='/workflows/start')result=await start(body);else if(path==='/export/word')result=await exportWord(body);else throw Error('Unexpected request: '+path);
      return result?.response||{ok:true,json:async()=>result};
    }};
  vm.runInNewContext(SOURCE,context);context.window.JarvisWorkflows.mount(parent);await flush();
  const question=parent.querySelector('textarea'),select=parent.querySelector('select'),form=parent.querySelector('form');
  const button=label=>parent.querySelectorAll('button').find(item=>item.textContent===label);
  return {parent,question,select,form,requests,streams,events,downloads,createdURLs,revokedURLs,api:context.window.JarvisWorkflows,
    preview:button('Preview plan'),start:button('Launch workflow'),plan:parent.querySelector('details'),
    planStatus:parent.querySelectorAll('.workflow-notice')[0],notice:parent.querySelectorAll('.workflow-notice')[1],
    input(value){question.value=value;question.emit('input');},change(value){select.value=value;select.emit('change');},
    async submit(){await form.onsubmit({preventDefault(){}});await flush();},
    async refresh(next=data){data=next;const timer=[...timers.values()].find(item=>item.delay===4000);await timer.callback();await flush();},
    async runTimers(delay){for(const [id,timer] of timers)if(timer.delay===delay){timers.delete(id);await timer.callback();}await flush();}};
}
module.exports={setup,catalog,plan,run,wordResponse,deferred,flush,Element};
