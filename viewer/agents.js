// A small, equal-footed constellation. Read existing activity; never run agent work.
(() => {
  'use strict';
  const SETTINGS=Object.freeze({pollMs:750,discoveryMs:4000,hiddenPollMs:3000,disabledPollMs:5000,
    fetchTimeoutMs:6000,terminalMs:12000,timeoutGraceMs:10000,secondsToMs:1000,elapsedDigits:1,
    recentEvents:3,maxCrewSlots:16,taskChars:240});
  const IDENTITIES=Object.freeze([
    {slot:'worker-1',name:'Scout',tone:'cyan',glyph:'<path d="m28 15 12 13-12 13-12-13Z"/><path d="m28 20 6 8-6 8-6-8Z"/><path d="M28 9v4m0 30v4M9 28h4m30 0h4"/>'},
    {slot:'worker-2',name:'Weave',tone:'violet',glyph:'<ellipse cx="28" cy="28" rx="15" ry="6"/><ellipse cx="28" cy="28" rx="15" ry="6" transform="rotate(60 28 28)"/><ellipse cx="28" cy="28" rx="15" ry="6" transform="rotate(120 28 28)"/><circle cx="28" cy="28" r="2"/>'},
    {slot:'worker-3',name:'Prism',tone:'amber',glyph:'<path d="m28 12 14 8v16l-14 8-14-8V20Z"/><path d="m14 20 14 8 14-8M28 28v16m0-32v16L14 36m14-8 14 8"/>'}
  ]);
  const ACTIVE=new Set(['running','retrying']);
  const TERMINAL=new Set(['complete','failed','cancelled','interrupted']);
  const STATES={idle:'Idle',running:'Working',retrying:'Retrying',queued:'Queued',complete:'Done',failed:'Check',cancelled:'Stopped',interrupted:'Interrupted',skipped:'Unused'};
  const PHASES={classifying:'Preparing',planning:'Preparing',answering:'Working',checking:'Checking',drafting:'Drafting',sharing:'Sharing findings',reviewing:'Reviewing together',reporting:'Combining findings',recovering:'Recovering'};
  const EVENTS={planned:'Work assigned',drafts_ready:'Findings ready',peer_review_started:'Reviewing shared findings',peer_review_complete:'Shared review complete',reported:'Findings combined',resumed:'Saved work resumed',checkpoint_saved:'Progress saved'};
  const root=document.getElementById('agent-monitor'),crew=document.getElementById('agent-crew');
  if(!root||!crew)return;
  const rows=document.getElementById('agent-rows'),status=document.getElementById('agent-summary'),meta=document.getElementById('agent-limits'),
    members=document.getElementById('crew-members'),crewStatus=document.getElementById('crew-summary'),activity=document.getElementById('crew-activity');
  const eventList=part(root,'ol','agent-events');eventList.ariaLabel='Recent agent activity';eventList.hidden=true;
  const cards=IDENTITIES.map(identity=>{
    const card=part(members,'article','crew-card');card.dataset.slot=identity.slot;card.dataset.tone=identity.tone;
    const avatar=part(card,'div','crew-avatar');avatar.innerHTML='<svg viewBox="0 0 56 56" aria-hidden="true"><circle class="crew-halo" cx="28" cy="28" r="23"/><circle class="crew-orbit" cx="28" cy="28" r="23"/><g class="crew-symbol">'+identity.glyph+'</g><circle class="crew-spark" cx="28" cy="5" r="2"/></svg>';
    const name=part(card,'h3','crew-name',identity.name),state=part(card,'p','crew-state','Idle');
    return {card,avatar,name,state};
  });
  const links=[...crew.querySelectorAll('.crew-link')];
  let timer=null,destroyed=false,last=null,workflow=null,workflowHealthy=true,nextDiscovery=0;
  function part(parent,tag,className,text){const element=document.createElement(tag);element.className=className;if(text!==undefined)element.textContent=text;parent.append(element);return element;}
  function cell(row,text){part(row,'td','',String(text));}
  function elapsed(value){return Number.isFinite(value)?value.toFixed(SETTINGS.elapsedDigits)+'s':'—';}
  function publicEvent(event){
    if(!event||!Object.hasOwn(EVENTS,event.kind))return null;
    if(event.slot!==null&&event.slot!==undefined&&event.slot!=='boss'){
      const number=Number(String(event.slot).split('-')[1]);
      if(!Number.isInteger(number)||number<1||number>SETTINGS.maxCrewSlots||event.slot!==`worker-${number}`)return null;
    }return EVENTS[event.kind];
  }
  function currentWorkflow(){
    if(!workflow)return null;
    if(!TERMINAL.has(workflow.status))return workflow;
    const ended=(workflow.created_at+workflow.elapsed_s)*SETTINGS.secondsToMs;
    return Date.now()-ended<SETTINGS.terminalMs?workflow:null;
  }
  function renderConstellation(){
    const run=currentWorkflow(),fromWorkflow=Boolean(run),data=fromWorkflow?run:last;
    crew.hidden=!(fromWorkflow||last?.enabled&&last?.crew_enabled);
    if(crew.hidden)return;
    const healthy=fromWorkflow?workflowHealthy:Boolean(last?.connected);
    crew.dataset.connected=String(healthy);
    const isActive=fromWorkflow?!TERMINAL.has(run.status):Boolean(data?.active);
    const agents=(isActive||fromWorkflow)&&Array.isArray(data?.agents)?data.agents:[];
    const selected=IDENTITIES.map(identity=>agents.filter(job=>job.slot===identity.slot).at(-1));
    const working=selected.filter(job=>ACTIVE.has(job?.status)).length;
    const engaged=selected.map(job=>isActive&&(ACTIVE.has(job?.status)||job?.status==='complete'));
    const reviewer=fromWorkflow?agents.find(job=>job.slot==='reviewer'&&ACTIVE.has(job.status)):
      agents.find(job=>['brain','answer'].includes(job.role)&&ACTIVE.has(job.status));
    const peerReview=!fromWorkflow&&selected.some(job=>ACTIVE.has(job?.status)&&job.phase==='reviewing');
    const combining=Boolean(isActive&&reviewer&&selected.some(job=>job?.status==='complete'));
    const count=engaged.filter(Boolean).length;
    crew.dataset.mode=healthy?(count===3?'trio':count===2?'pair':working?'solo':combining?'combining':'idle'):'offline';
    crew.dataset.active=String(isActive&&healthy);
    for(const [index,view] of cards.entries()){
      const job=selected[index];
      const state=job&&Object.hasOwn(STATES,job.status)?job.status:'idle';
      view.card.dataset.status=state;view.card.dataset.phase=combining?'combining':peerReview?'reviewing':'';
      view.state.textContent=healthy?(ACTIVE.has(state)&&job?.current_tool?'Using a tool':peerReview&&ACTIVE.has(state)?'Reviewing':STATES[state]):'Offline';
      view.card.title=IDENTITIES[index].name+' · '+view.state.textContent+(fromWorkflow&&job?.task?' · '+String(job.task).slice(0,SETTINGS.taskChars):'')+(job?' · '+elapsed(job.elapsed_s):'');
    }
    for(const link of links){
      const [a,b]=link.dataset.pair.split('-').map(Number);
      link.dataset.active=String(Boolean(healthy&&isActive&&engaged[a]&&engaged[b]));
      link.dataset.phase=combining?'merge':'shared';
    }
    crewStatus.textContent=!healthy?'Offline':isActive?(working?working+' working':combining?'Combining':'Preparing'):fromWorkflow?run.status==='complete'?'Complete':'Stopped':'Ready';
    activity.textContent=!healthy?'Activity connection interrupted':!isActive?(fromWorkflow?run.status==='complete'?'Findings ready in Work':'Saved work available in Work':'Ready for your next task'):
      combining?'Combining their findings':peerReview?'Reviewing shared findings':count===3?'All three · one shared task':count===2?'Two agents · one shared task':working?'One agent · focused work':fromWorkflow?'Finding sources & assigning work':'Preparing your request';
    crew.dataset.source=fromWorkflow?'workflow':'chat';
  }
  function renderLegacy(data){
    last={...data,connected:true};root.hidden=!data.enabled;
    if(!data.enabled){renderConstellation();return;}
    const run=currentWorkflow();
    if(run){renderWorkflowTable(run);renderConstellation();return;}
    status.textContent=data.active?'Working · '+elapsed(data.elapsed_s):data.agents.length?'Last request · '+elapsed(data.elapsed_s):'Ready';
    meta.textContent=`Up to ${data.limits.max_workers} agents · ${data.limits.worker_timeout_s}s per agent`;
    rows.replaceChildren();
    for(const agent of data.agents){
      const row=document.createElement('tr');row.dataset.status=agent.status;
      const identity=IDENTITIES.find(item=>item.slot===agent.slot),label=identity?.name||({dispatch:'Quick answer',brain:'Preparation',answer:'Final review',vision:'Vision'}[agent.role]||'Agent');
      const phase=Object.hasOwn(PHASES,agent.phase)?PHASES[agent.phase]:null;
      for(const value of [label,agent.model,agent.effort||'—',phase?`${agent.status} · ${phase}`:agent.status,agent.current_tool||'—',elapsed(agent.elapsed_s),agent.retries,agent.total_tokens===null?'Unavailable':agent.total_tokens.toLocaleString()])cell(row,value);
      rows.append(row);
    }
    const events=(data.events||[]).filter(event=>publicEvent(event)).slice(-SETTINGS.recentEvents);
    eventList.replaceChildren();eventList.hidden=!events.length;
    for(const event of events)part(eventList,'li','agent-event',(Number.isFinite(event.elapsed_s)?elapsed(event.elapsed_s)+' · ':'')+publicEvent(event));
    renderConstellation();
  }
  function renderWorkflowTable(run){
    root.hidden=false;status.textContent=(TERMINAL.has(run.status)?'Saved workflow':'Workflow active')+' · '+elapsed(run.elapsed_s);
    meta.textContent='Three agent signals · shared findings · '+run.tool_calls+' tool calls';
    rows.replaceChildren();
    for(const agent of run.agents){
      const row=document.createElement('tr');row.dataset.status=agent.status;
      const label=IDENTITIES.find(item=>item.slot===agent.slot)?.name||'Final review';
      for(const value of [label,agent.model,agent.effort,STATES[agent.status]||'Idle','—',elapsed(agent.elapsed_s),Math.max(0,agent.attempts-1),agent.usage_reported?agent.tokens.toLocaleString():'Unavailable'])cell(row,value);
      rows.append(row);
    }
    eventList.hidden=true;eventList.replaceChildren();
  }
  function acceptWorkflow(run){
    if(destroyed||!run||!Array.isArray(run.agents)||typeof run.id!=='string'||typeof run.status!=='string')return;
    if(workflow&&!TERMINAL.has(workflow.status)&&run.id!==workflow.id&&run.created_at<workflow.created_at)return;
    workflow=run;workflowHealthy=true;
    if(currentWorkflow())renderWorkflowTable(run);
    renderConstellation();
  }
  async function read(path){const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(SETTINGS.fetchTimeoutMs)});if(!response.ok)throw Error('Activity unavailable');return response.json();}
  async function refreshWorkflow(){
    if(workflow&&!TERMINAL.has(workflow.status)){
      try{acceptWorkflow(await read('/workflows/run?id='+encodeURIComponent(workflow.id)));}
      catch{workflowHealthy=false;renderConstellation();}
      return;
    }
    if(Date.now()<nextDiscovery){renderConstellation();return;}
    nextDiscovery=Date.now()+SETTINGS.discoveryMs;
    try{
      const data=await read('/workflows');
      if(destroyed)return;
      if(data.enabled&&data.available&&Array.isArray(data.runs)){
        const candidate=data.runs.find(run=>run.id===data.active)||data.runs[0];
        if(candidate)acceptWorkflow(candidate);
      }
    }catch{/* Ordinary chat remains visible if workflow integration is disabled. */}
  }
  async function refresh(){
    await Promise.allSettled([refreshWorkflow(),(async()=>{
      try{
        const data=await read('/agents');if(destroyed)return;
        if(typeof data.enabled!=='boolean'||!Array.isArray(data.agents)||!data.limits)throw Error('Invalid activity');
        window.JarvisGalaxy?.setSpeechBusy?.(data.speech_busy===true);
        renderLegacy(data);
      }catch{
        window.JarvisGalaxy?.setSpeechBusy?.(false);
        if(last)last.connected=false;
        if(!currentWorkflow()){status.textContent='Activity connection interrupted';renderConstellation();}
      }
    })()]);
    if(!destroyed)timer=setTimeout(refresh,document.hidden?SETTINGS.hiddenPollMs:last?.enabled?SETTINGS.pollMs:SETTINGS.disabledPollMs);
  }
  function timeoutMs(fallback){return last?.routing_enabled?Math.max(fallback,last.limits.request_timeout_s*SETTINGS.secondsToMs+SETTINGS.timeoutGraceMs):fallback;}
  function destroy(){destroyed=true;clearTimeout(timer);window.removeEventListener('jarvis:workflow-state',onWorkflow);}
  function onWorkflow(event){if(!destroyed)acceptWorkflow(event.detail);}
  document.getElementById('crew-open-work').onclick=()=>window.JarvisUI?.setWorkspace('work');
  window.addEventListener('jarvis:workflow-state',onWorkflow);
  window.addEventListener('pagehide',destroy,{once:true});
  window.JarvisAgents={timeoutMs,destroy};
  refresh();
})();
