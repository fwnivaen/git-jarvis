/* Real LangGraph execution snapshots; no fabricated agent activity. */
(() => {
  'use strict';
  const SETTINGS=Object.freeze({refreshMs:4000,requestMs:10000,maxQuestion:2000,recentEvents:30,secondsDigits:1,requestBytes:12,exportTitleChars:160,exportTextChars:40000,downloadRevokeMs:1000});
  const WORD_MIME='application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  const TERMINAL=new Set(['complete','failed','cancelled','interrupted']);
  const STEPS=['retrieve','assign','workers','review','complete'];
  const LABELS={retrieve:'Retrieve sources',assign:'Assign work',workers:'Analyze',review:'Verify & answer',complete:'Complete'};
  const make=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
  let root=null,timer=null,stream=null,selected=null,generation=0,last=null,chooseCurrent=null;
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(SETTINGS.requestMs),
      ...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const data=await response.json();if(!response.ok)throw Error(data.answer||'Workflow service unavailable.');return data;
  }
  function dispose(){generation++;clearTimeout(timer);stream?.close();stream=null;root=null;selected=null;last=null;chooseCurrent=null;}
  function mount(parent){
    dispose();const current=generation;let historySignature='';
    root=make('section','workflow-console');parent.append(root);
    const header=make('div','workflow-header'),heading=make('div');
    heading.append(make('p','workflow-eyebrow','AGENT WORKFLOWS'),make('h2',null,'Give your agents a task'));
    const engine=make('span','workflow-engine','LangGraph · local');header.append(heading,engine);
    const form=make('form','workflow-form'),question=make('textarea'),select=make('select'),start=make('button','control workflow-primary','Launch workflow');
    const preview=make('button','control','Preview plan');preview.type='button';
    question.required=true;question.maxLength=SETTINGS.maxQuestion;question.rows=3;
    question.setAttribute('aria-label','Workflow task');question.placeholder='Compare Menu Pricing, Opening Checklist and Launch Campaign. Give three priorities with citations.';
    select.setAttribute('aria-label','Workflow type');start.type='submit';
    const controls=make('div','workflow-controls');controls.append(select,preview,start);form.append(question,controls);
    const planStatus=make('p','workflow-notice');planStatus.setAttribute('role','status');planStatus.setAttribute('aria-live','polite');
    const planDetails=make('details','workflow-output');planDetails.hidden=true;planDetails.setAttribute('aria-label','Workflow plan preview');
    let submission=null,planRevision=0;
    const resetSubmission=()=>{
      submission=null;planRevision++;
      const hadPlan=!planDetails.hidden||preview.disabled;
      preview.disabled=false;planDetails.hidden=true;planDetails.replaceChildren();
      planStatus.textContent=hadPlan?'Task changed. Preview the updated plan before launching.':'';
    };question.addEventListener('input',resetSubmission);select.addEventListener('change',resetSubmission);
    const notice=make('p','workflow-notice');notice.setAttribute('role','status');
    const privacy=make('p','workflow-privacy','Read-only analysis of your notes. Activity and saved work stay local; model calls use your existing signed-in backend.');
    const layout=make('div','workflow-layout'),history=make('aside','workflow-history'),detail=make('div','workflow-detail');
    history.setAttribute('aria-label','Saved workflows');layout.append(history,detail);root.append(header,form,planStatus,planDetails,notice,privacy,layout);
    function renderPlan(plan){
      if(plan.dry_run!==true||plan.admitted!==false||!Array.isArray(plan.roles)||!Array.isArray(plan.steps)||
          !Array.isArray(plan.permitted_tools)||!Array.isArray(plan.execution_limits)||!plan.limits||!plan.side_effects){
        throw Error('The server did not return an unstarted plan preview.');
      }
      planDetails.replaceChildren(make('summary',null,'Plan preview · '+plan.label),make('p',null,plan.question));
      const list=(heading,items)=>{
        planDetails.append(make('h4',null,heading));const ul=make('ul');
        for(const item of items)ul.append(make('li',null,item));planDetails.append(ul);
      };
      list('Agent roles',plan.roles.map(role=>role.name+': '+role.assignment));
      planDetails.append(make('h4',null,'Execution steps'));const steps=make('ol');
      for(const step of plan.steps){
        const text=(LABELS[step.phase]||step.phase)+': '+step.description+
          (step.tool?' Tool: '+step.tool+'.':'')+(step.workers!==undefined?' Workers: '+step.workers+'.':'')+
          (step.model_review_may_be_skipped?' A separate model review may be skipped for this recipe.':'');
        steps.append(make('li',null,text));
      }planDetails.append(steps);
      if(Array.isArray(plan.criteria))list('Review criteria',plan.criteria);
      list('Permitted read tools',plan.permitted_tools.length?plan.permitted_tools:['No read tools are currently registered.']);
      const limits=plan.limits,budgets=make('dl');planDetails.append(make('h4',null,'Execution budgets'));
      const budgetLabels={concurrent_tasks:'Concurrent tasks',workers:'Workers for this task',shared_model_calls:'Concurrent model calls across tasks',
        shared_tool_calls:'Concurrent tool calls across tasks',tool_calls_per_task:'Tool calls per task',request_timeout_s:'Task time budget (seconds)',
        worker_timeout_s:'Worker time budget (seconds)',worker_retries:'Worker retries',max_restarts:'Saved-work restarts',max_saved_runs:'Saved runs retained',
        model_calls_per_task:'Additional model calls per task',observed_tokens_per_task:'Additional observed tokens per task'};
      for(const [key,label] of Object.entries(budgetLabels))budgets.append(make('dt',null,label),make('dd',null,limits[key]===null?'Not enforced':limits[key]===undefined?'Unavailable':String(limits[key])));
      planDetails.append(budgets,make('p',null,'Additional model and token budgets: '+(limits.adaptive_model_budget_enforced?'enabled.':'disabled; time, tool and concurrency limits still apply.')));
      const sideEffects=(heading,values,labels)=>list(heading,Object.entries(labels).map(([key,label])=>label+': '+
        (values?.[key]===true?'Yes':values?.[key]===false?'No':'Unavailable')));
      sideEffects('Preview effects',plan.side_effects.preview,{model_calls:'Model calls',tool_calls:'Tool calls',task_admission:'Task admitted',database_creation:'Storage created',note_writes:'Notes written'});
      sideEffects('Effects when launched separately',plan.side_effects.execution,{model_calls:'Model calls',local_note_reads:'Local notes read',private_checkpoints:'Private checkpoints saved',
        private_tool_journal:'Private tool journal saved',stores_question_and_results:'Question and results stored',note_writes:'Notes written',external_actions:'External actions'});
      if(plan.checkpoint_storage){const storage=plan.checkpoint_storage;planDetails.append(make('h4',null,'Saved work'),make('p',null,storage.location),
        make('p',null,'Contains source passages: '+(storage.contains_source_passages?'Yes.':'No.')+' Created by this preview: '+(storage.created_by_preview?'Yes.':'No.')));}
      if(plan.prerequisites?.notes_search_registered===false)planDetails.append(make('p','workflow-error','Local note search is not registered. Launch will recheck tool availability.'));
      list('Limits and checks',plan.execution_limits);
      planDetails.hidden=false;planDetails.open=true;
    }
    preview.onclick=async()=>{
      if(current!==generation||!root?.isConnected)return;
      if(!question.value.trim()||question.value.length>SETTINGS.maxQuestion){planStatus.textContent='Enter a short task before previewing its plan.';return;}
      if(!select.value){planStatus.textContent='Wait for the workflow types to load, then preview again.';return;}
      const revision=++planRevision,body={question:question.value,workflow:select.value};
      const isCurrent=()=>current===generation&&revision===planRevision&&root?.isConnected;
      preview.disabled=true;planDetails.hidden=true;planStatus.textContent='Preparing plan preview…';
      try{const plan=await request('/stage2/tasks/plan',body);if(!isCurrent())return;renderPlan(plan);
        planStatus.textContent='Preview ready. No task has started; launch is a separate action.'+(plan.execution_enabled?'':' Execution is disabled in the current settings.');}
      catch(error){if(isCurrent()){planDetails.hidden=true;planStatus.textContent=error.message;}}
      finally{if(isCurrent())preview.disabled=false;}
    };
    function message(text){if(current===generation)notice.textContent=text;}
    async function act(action){
      if(!selected)return;
      try{const run=await request('/workflows/'+action,{id:selected});if(current!==generation)return;render(run);connect(run.id);refresh();}
      catch(error){message(error.message);}
    }
    function render(run){
      if(current!==generation||run.id!==selected)return;
      const visibleState=value=>JSON.stringify(value,(key,item)=>key==='remaining_s'?undefined:item);
      if(TERMINAL.has(run.status)&&visibleState(run)===visibleState(last))return;
      last=run;
      window.dispatchEvent(new CustomEvent('jarvis:workflow-state',{detail:run}));
      detail.replaceChildren();
      const title=make('div','workflow-run-heading'),state=make('span','workflow-state',run.status);state.dataset.state=run.status;
      title.append(make('h3',null,run.question),state);detail.append(title);
      const meta=make('p','workflow-meta',`${run.elapsed_s.toFixed(SETTINGS.secondsDigits)}s · ${run.tool_calls} tool calls · ${run.total_tokens===null?'tokens unavailable':run.total_tokens.toLocaleString()+' reported tokens'}${run.usage_complete?'':' (usage may be incomplete)'}`);detail.append(meta);
      const progress=make('ol','workflow-progress');progress.setAttribute('aria-label','Workflow progress');
      for(const [index,step] of STEPS.entries()){
        const li=make('li',null,LABELS[step]);li.dataset.state=run.phase===step?'current':index<STEPS.indexOf(run.phase)?'done':'pending';progress.append(li);
      }detail.append(progress);
      const actions=make('div','workflow-controls');
      if(run.capabilities?.cancel!==false&&['running','cancelling'].includes(run.status)){const cancel=make('button','control','Cancel workflow');cancel.type='button';cancel.disabled=run.status==='cancelling';cancel.onclick=()=>act('cancel');actions.append(cancel);}
      if(run.can_retry){const retry=make('button','control','Retry saved work');retry.type='button';retry.onclick=()=>act('retry');actions.append(retry);}
      if(TERMINAL.has(run.status)){const again=make('button','control','Use task again');again.type='button';again.onclick=()=>{resetSubmission();question.value=run.question;select.value=run.workflow;question.focus();};actions.append(again);}
      detail.append(actions);
      if(run.cleanup==='pending')detail.append(make('p','workflow-error','Cleanup pending · waiting for the underlying operation to settle.'));
      if(run.error)detail.append(make('p','workflow-error','Stopped: '+run.error.replaceAll('_',' ')+'.'+(run.can_retry?' Retry uses the remaining original budget.':' Start a new workflow for a fresh budget.')));
      const agents=make('div','workflow-agents');
      for(const agent of run.agents){
        const card=make('article','workflow-agent');card.dataset.state=agent.status;
        const head=make('div','workflow-agent-heading');head.append(make('h4',null,agent.slot==='reviewer'?'Lead reviewer':'Agent '+agent.slot.split('-')[1]),make('span','workflow-state',agent.status));
        card.append(head,make('p','workflow-task',agent.task),make('p','workflow-meta',`${agent.model} · ${agent.effort} · ${agent.elapsed_s.toFixed(SETTINGS.secondsDigits)}s · attempt ${agent.attempts}`));
        if(agent.error)card.append(make('p','workflow-error',agent.error.replaceAll('_',' ')));
        if(agent.output){const output=make('details','workflow-output'),summary=make('summary',null,'Verified findings');output.append(summary,make('p',null,agent.output));card.append(output);}
        agents.append(card);
      }
      if(!run.agents.length)agents.append(make('p','workflow-empty','Waiting for the first agent assignment…'));
      detail.append(agents);
      if(run.result?.sources_changed)detail.append(make('p','workflow-error','Source notes changed since this run. Its saved findings are historical.'));
      if(run.result){const output=make('section','workflow-answer');output.append(make('h3',null,'Completed answer'),make('p',null,run.result.answer));
        if(run.result.nodes.length){const sources=make('div','workflow-sources');sources.append(make('span',null,'Sources: '));for(const id of run.result.nodes){const link=make('a',null,window.JarvisUI?.sourceLabel(id)||'Note '+id);link.href='/note?id='+encodeURIComponent(id);link.onclick=event=>{if(window.JarvisUI?.openSource(id))event.preventDefault();};sources.append(link);}output.append(sources);}
        if(run.status==='complete'){
          const download=make('button','control','Download Word document'),downloadStatus=make('p','workflow-notice');download.type='button';
          downloadStatus.setAttribute('role','status');downloadStatus.setAttribute('aria-live','polite');output.append(download,downloadStatus);
          if(Array.from(run.result.answer).length>SETTINGS.exportTextChars){download.disabled=true;downloadStatus.textContent='This answer is too long for one Word download.';}
          download.onclick=async()=>{
            const isCurrent=()=>current===generation&&root?.isConnected&&selected===run.id&&download.isConnected;
            if(!isCurrent()||download.disabled)return;
            download.disabled=true;downloadStatus.textContent='Preparing Word document…';
            try{
              const response=await fetch('/export/word',{method:'POST',cache:'no-store',signal:AbortSignal.timeout(SETTINGS.requestMs),
                headers:{'Content-Type':'application/json'},body:JSON.stringify({title:Array.from(run.question).slice(0,SETTINGS.exportTitleChars).join(''),text:run.result.answer})});
              if(!isCurrent())return;
              if(!response.ok){let message='The Word document could not be prepared.';try{const data=await response.json();message=data.answer||message;}catch{}throw Error(message);}
              if(response.headers.get('Content-Type')?.split(';')[0].trim()!==WORD_MIME)throw Error('The server did not return a Word document.');
              const blob=await response.blob();if(!isCurrent())return;if(!blob.size)throw Error('The Word document was empty.');
              const url=URL.createObjectURL(blob),link=make('a');
              const filename=response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1];
              link.href=url;link.download=filename?.replace(/[\\/:*?"<>|\u0000-\u001f]/g,'_')||'Jarvis answer.docx';
              try{document.body.append(link);link.click();downloadStatus.textContent='Word download started.';}
              finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),SETTINGS.downloadRevokeMs);}
            }catch(error){if(isCurrent())downloadStatus.textContent=error.message;}
            finally{if(isCurrent())download.disabled=false;}
          };
        }detail.append(output);}
      const activity=make('details','workflow-events');activity.open=true;activity.append(make('summary',null,'Execution activity'));
      const list=make('ol');for(const event of run.events.slice(-SETTINGS.recentEvents)){
        list.append(make('li',null,`${event.elapsed_s.toFixed(SETTINGS.secondsDigits)}s · ${event.slot||event.node||'Workflow'} · ${event.kind.replaceAll('_',' ')}${event.tool?' · '+event.tool:''}${event.error?' · '+event.error:''}`));
      }activity.append(list);detail.append(activity);
      if(TERMINAL.has(run.status)){stream?.close();stream=null;}
    }
    async function choose(id){
      selected=id;stream?.close();stream=null;
      history.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.runId===id)));
      try{const run=await request('/workflows/run?id='+encodeURIComponent(id));if(current!==generation||selected!==id)return;render(run);if(!TERMINAL.has(run.status))connect(id);}
      catch(error){message(error.message);}
    }
    function connect(id){
      stream?.close();selected=id;stream=new EventSource('/workflows/events?id='+encodeURIComponent(id));
      stream.addEventListener('state',event=>{if(current!==generation||selected!==id)return;try{render(JSON.parse(event.data));message('Connected to real workflow activity.');}catch{message('Activity update could not be read.');}});
      stream.onerror=()=>{if(current===generation&&stream)message('Activity connection interrupted. Reconnecting; the workflow continues on the server.');};
    }
    async function refresh(){
      clearTimeout(timer);
      try{
        const data=await request('/workflows');if(current!==generation||!root?.isConnected)return;
        const activeIds=Array.isArray(data.active_ids)?data.active_ids:(data.active?[data.active]:[]);
        const concurrentLimit=Number.isInteger(data.limits?.concurrent_runs)?data.limits.concurrent_runs:1;
        start.disabled=!(data.enabled&&data.available)||activeIds.length>=concurrentLimit;
        if(!select.options.length)for(const item of data.workflows){const option=make('option',null,item.label);option.value=item.id;select.append(option);}if(!select.dataset.ready){select.value='research';select.dataset.ready='1';}
        if(!data.enabled)message('Launch JARVIS with ./start-workflows.sh to enable agent workflows.');else if(!data.available)message(data.notice);else if(data.capacity_notice)message(data.capacity_notice);else if(!selected)message('Ready. Up to '+concurrentLimit+' tasks at once, with '+data.limits.max_workers+' agents per workflow.');
        const nextHistory=JSON.stringify(data.runs.map(run=>[run.id,run.question,run.status,run.created_at]));
        if(nextHistory!==historySignature){
          historySignature=nextHistory;history.replaceChildren(make('h3',null,'Saved work'));
          for(const run of data.runs){const button=make('button','workflow-history-item');button.type='button';button.dataset.runId=run.id;button.setAttribute('aria-pressed',String(selected===run.id));button.append(make('strong',null,run.question),make('span',null,run.status+' · '+new Date(run.created_at*1000).toLocaleTimeString()));button.onclick=()=>choose(run.id);history.append(button);}
          if(!data.runs.length)history.append(make('p','workflow-empty','Your completed and interrupted runs will appear here.'));
        }
        history.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.runId===selected)));
        if(!selected&&data.runs.length)await choose(data.active||data.runs[0].id);
        else if(selected&&!stream){const run=data.runs.find(item=>item.id===selected);if(run)render(run);}
      }catch(error){message(error.message);}
      finally{if(current===generation&&root?.isConnected)timer=setTimeout(refresh,SETTINGS.refreshMs);}
    }
    form.onsubmit=async event=>{
      event.preventDefault();start.disabled=true;message('Starting workflow…');
      try{const fingerprint=JSON.stringify([question.value,select.value]);if(!submission||submission.fingerprint!==fingerprint){const bytes=new Uint8Array(SETTINGS.requestBytes);crypto.getRandomValues(bytes);submission={fingerprint,key:Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('')};}const run=await request('/workflows/start',{question:question.value,workflow:select.value,request_key:submission.key});if(current!==generation)return;selected=run.id;render(run);connect(run.id);}
      catch(error){message(error.message);}finally{if(current===generation)refresh();}
    };
    chooseCurrent=choose;refresh();
  }
  window.JarvisWorkflows={mount,dispose,openRun(id){if(/^[a-f0-9]{24}$/.test(id)&&chooseCurrent)return chooseCurrent(id);return false;}};window.addEventListener('pagehide',dispose);
})();
