/* JARVIS V2 chrome. Existing graph, voice, model and Eyes controllers own their state. */
(() => {
  'use strict';
  const SETTINGS=Object.freeze({preferenceKey:'jarvis.preferences.v2',pinKey:'jarvis.pins.v2',historyKey:'jarvis.notifications.v2',
    insightMs:8000,hoverMs:240,statusMs:5000,bootMs:2400,bootReducedMs:0,placementGap:16,placementStep:24,
    paletteLimit:60,historyLimit:40,soundMs:90,soundGain:.018,soundHz:620,monoPlaces:0,sheetDismissPx:80,clockMs:1000,clockMinutes:1440,percent:100,operationBytes:12});
  const $=id=>document.getElementById(id),create=(tag,className,text)=>{const e=document.createElement(tag);if(className)e.className=className;if(text!==undefined)e.textContent=text;return e;};
  const load=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key)||'null')||fallback;}catch{return fallback;}};
  const store=(key,value)=>{try{localStorage.setItem(key,JSON.stringify(value));}catch{}};
  const preferences=load(SETTINGS.preferenceKey,{theme:'dark',motion:'system',sounds:false,wake:false});
  const pins=load(SETTINGS.pinKey,{}),history=load(SETTINGS.historyKey,[]);
  let binding=null,currentWorkspace='galaxy',currentNote=null,previewNote=null,lastAnswer=null,lastQuestion='',selectedPalette=0,paletteItems=[],hoverTimer=null,insightTimer=null,insightText='',noteRequest=0,approval=null,workTimer=null;
  let systemState=null,watchState=null,lastFocusElement=null,serverAvailable=true,backendAvailable=true;
  const noteKey=node=>node.group+'/'+node.label;
  const notes=()=>binding?.graph.nodes||[];
  const endpoint=value=>typeof value==='object'?value.id:value;
  const noteURL=node=>'/note?id='+node.id+(node.stable_id?'&stable='+encodeURIComponent(node.stable_id):'');
  const links=node=>(binding?.graph.links||[]).filter(l=>endpoint(l.source)===node.id||endpoint(l.target)===node.id);
  const related=node=>links(node).map(l=>notes().find(n=>n.id===(endpoint(l.source)===node.id?endpoint(l.target):endpoint(l.source)))).filter(Boolean);
  const label=node=>node.group==='captures'?'Captures':node.group;
  const color=node=>window.JarvisGalaxy?.categoryColor(node.group)||getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
  const announce=text=>{$('live-region').textContent=text;};
  const request=async(path,body)=>{const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.answer||data.text||'The local service is unavailable.');return data;};
  function operationKey(){const bytes=new Uint8Array(SETTINGS.operationBytes);crypto.getRandomValues(bytes);return Array.from(bytes,value=>value.toString(16).padStart(2,'0')).join('');}
  function sound(){if(!preferences.sounds)return;try{const Audio=window.AudioContext||window.webkitAudioContext,ctx=new Audio(),osc=ctx.createOscillator(),gain=ctx.createGain();osc.frequency.value=SETTINGS.soundHz;gain.gain.setValueAtTime(SETTINGS.soundGain,ctx.currentTime);gain.gain.exponentialRampToValueAtTime(.0001,ctx.currentTime+SETTINGS.soundMs/1000);osc.connect(gain).connect(ctx.destination);osc.start();osc.stop(ctx.currentTime+SETTINGS.soundMs/1000);osc.onended=()=>ctx.close();}catch{}}
  function applyPreferences(){
    const light=preferences.theme==='light'||(preferences.theme==='system'&&matchMedia('(prefers-color-scheme: light)').matches);
    document.documentElement.dataset.theme=light?'light':'dark';
    document.documentElement.dataset.motion=preferences.motion==='reduced'?'reduced':'system';
    store(SETTINGS.preferenceKey,preferences);
    window.JarvisVoice?.setWakeWord?.(preferences.wake===true);
    window.dispatchEvent(new CustomEvent('jarvis:theme',{detail:{theme:light?'light':'dark'}}));
    window.JarvisGalaxy?.wake();
  }
  function archiveInsight(){if(!insightText)return;history.unshift({text:insightText,time:Date.now()});history.splice(SETTINGS.historyLimit);store(SETTINGS.historyKey,history);insightText='';}
  function hideInsight(){clearTimeout(insightTimer);$('insight').hidden=true;archiveInsight();}
  function insight(text){hideInsight();insightText=text;$('insight').textContent=text;$('insight').hidden=false;insightTimer=setTimeout(hideInsight,SETTINGS.insightMs);}
  $('insight').onmouseenter=()=>clearTimeout(insightTimer);$('insight').onmouseleave=()=>{insightTimer=setTimeout(hideInsight,SETTINGS.insightMs);};
  function pin(node){const key=noteKey(node);if(pins[key])delete pins[key];else pins[key]=true;store(SETTINGS.pinKey,pins);$('preview-pin').setAttribute('aria-pressed',String(Boolean(pins[key])));window.JarvisGalaxy?.refresh();announce((pins[key]?'Pinned ':'Unpinned ')+node.label);}
  const relativeDate=stamp=>stamp?new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric'}).format(new Date(stamp*1000)):'Date unavailable';
  function metadata(node){const groups=new Set(related(node).map(n=>n.group));return links(node).length+' links · '+groups.size+' constellations · '+relativeDate(node.edited_at);}
  function rectOverlap(a,b){return a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;}
  function placePreview(){
    if(!previewNote||$('preview').hidden)return;
    const point=window.JarvisGalaxy?.project(previewNote);if(!point)return;
    const stage=$('stage').getBoundingClientRect(),card=$('preview'),r=card.getBoundingClientRect(),gap=SETTINGS.placementGap;
    const obstacles=['#chat-form','#jarvis-orb-host','#groups','#detail','#answer-card'].map(q=>document.querySelector(q)).filter(e=>e&&!e.hidden&&e.getBoundingClientRect().width).map(e=>e.getBoundingClientRect());
    const candidates=[[point.x+gap,point.y-r.height/2],[point.x-r.width-gap,point.y-r.height/2],[point.x-r.width/2,point.y-r.height-gap],[point.x-r.width/2,point.y+gap]];
    for(let y=gap;y<stage.height-r.height;y+=SETTINGS.placementStep)candidates.push([stage.width-r.width-gap,y]);
    let best=null,bestScore=Infinity;
    for(const candidate of candidates){const x=Math.max(gap,Math.min(stage.width-r.width-gap,candidate[0])),y=Math.max(gap,Math.min(stage.height-r.height-gap,candidate[1]));
      const box={left:x+stage.left,top:y+stage.top,right:x+stage.left+r.width,bottom:y+stage.top+r.height};
      const score=obstacles.reduce((sum,o)=>sum+(rectOverlap(box,o)?stage.width*stage.height:0),0)+Math.hypot(x-point.x,y-point.y);
      if(score<bestScore){bestScore=score;best={x,y};}
    }
    if(best){card.style.left=best.x+'px';card.style.top=best.y+'px';}
    drawLeader();
  }
  function drawLeader(){
    const svg=$('card-leaders');svg.replaceChildren();
    const node=!$('preview').hidden?previewNote:!$('detail').hidden?binding?.selected():null;if(!node)return;
    const point=window.JarvisGalaxy?.project(node);if(!point)return;
    const stage=$('stage').getBoundingClientRect(),box=(!$('preview').hidden?$('preview'):$('detail')).getBoundingClientRect();
    const line=document.createElementNS('http://www.w3.org/2000/svg','line');
    line.setAttribute('x1',point.x);line.setAttribute('y1',point.y);line.setAttribute('x2',Math.max(box.left-stage.left,Math.min(box.right-stage.left,point.x)));line.setAttribute('y2',Math.max(box.top-stage.top,Math.min(box.bottom-stage.top,point.y)));svg.append(line);
  }
  function showPreview(node){
    if(currentWorkspace!=='galaxy'||!node||!$('detail').hidden)return;
    previewNote=node;$('preview-group').textContent=label(node);$('preview-group').style.color=color(node);$('preview-title').textContent=node.label;
    $('preview-excerpt').textContent=node.excerpt.replace(/^# .*\n?/,'');$('preview-meta').textContent=metadata(node);$('preview-pin').setAttribute('aria-pressed',String(Boolean(pins[noteKey(node)])));$('preview').hidden=false;placePreview();
  }
  function hidePreview(){clearTimeout(hoverTimer);previewNote=null;$('preview').hidden=true;drawLeader();}
  $('preview-pin').onclick=()=>{if(previewNote)pin(previewNote);};
  $('preview-open').onclick=()=>{const node=previewNote;hidePreview();if(node){binding?.select(node);$('detail').focus();}};
  $('preview-ask').onclick=()=>{if(previewNote)askAbout(previewNote);};
  $('preview-close').onclick=hidePreview;
  function askAbout(node){setWorkspace('galaxy');$('question').value='What should I know about '+node.label+'?';$('question').focus();}
  function rebuildTree(){
    const tree=$('galaxy-tree');tree.replaceChildren();
    for(const group of [...new Set(notes().map(n=>n.group))]){
      const item=create('div');item.setAttribute('role','treeitem');item.setAttribute('aria-expanded','false');item.tabIndex=0;item.textContent=group==='captures'?'Captures':group;
      const children=create('div');children.setAttribute('role','group');children.hidden=true;
      item.onclick=()=>{children.hidden=!children.hidden;item.setAttribute('aria-expanded',String(!children.hidden));};
      item.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();item.click();}};
      notes().filter(n=>n.group===group).forEach(node=>{const entry=create('div');entry.setAttribute('role','treeitem');entry.setAttribute('aria-label',node.label);const b=create('button',null,node.label+' · '+links(node).length+' links');b.onclick=e=>{e.stopPropagation();setWorkspace('galaxy');binding.select(node);};entry.append(b);const neighbors=create('div');neighbors.setAttribute('role','group');related(node).forEach(linked=>{const link=create('button',null,'Linked note: '+linked.label);link.setAttribute('role','treeitem');link.onclick=()=>{setWorkspace('galaxy');binding.select(linked);};neighbors.append(link);});entry.append(neighbors);children.append(entry);});
      tree.append(item,children);
    }
  }
  function openPalette(query=''){
    lastFocusElement=document.activeElement;$('palette-input').dataset.mention=String(query.startsWith('@'));if(!$('command-palette').open)$('command-palette').showModal();$('palette-input').value=query;selectedPalette=0;renderPalette();$('palette-input').focus();
  }
  function closePalette(){$('command-palette').close();delete $('palette-input').dataset.mention;lastFocusElement?.focus?.();}
  function actions(){return [
    ['Knowledge Galaxy','Workspace',()=>setWorkspace('galaxy')],['Notes','Workspace',()=>setWorkspace('notes')],['Work / Mission Control','Workspace',()=>setWorkspace('work')],
    ['Settings','Workspace',()=>setWorkspace('settings')],['Jarvis Brain','Workspace',()=>setWorkspace('brain')],['Library','Workspace',()=>setWorkspace('library')],
    ['JARVIS Vault','Workspace',()=>setWorkspace('vault')],['Models and integrations','Workspace',()=>setWorkspace('integrations')],['Voice','Workspace',()=>setWorkspace('voice')],
    ['Agents','Connection required',()=>setWorkspace('agents')],['Automations','Connection required',()=>setWorkspace('automations')],['Trading','Connection required',()=>setWorkspace('trading')],
    ['Agency','Connection required',()=>setWorkspace('agency')],['Studio','Connection required',()=>setWorkspace('studio')],
    ['Toggle Eyes','Camera permission',()=>$('eyes').click()],['Watch screen / Stop watching','Screen permission',()=>$('screen').click()],
    ['Use notes instead of screen','Action',()=>$('screen-notes').click()],['Toggle microphone','Microphone permission',()=>$('mic').click()],
    ['Stop speaking','Action',()=>$('stop-speech').click()],['Choose model','Action',()=>$('brain-chip').click()],['Fit galaxy','Action',()=>binding?.fit()],
    ['Toggle light / dark theme','Appearance',()=>{preferences.theme=document.documentElement.dataset.theme==='light'?'dark':'light';applyPreferences();}],
    ['Notification history','Workspace',()=>setWorkspace('history')],['Keyboard shortcuts','Help',()=>setWorkspace('help')]
  ];}
  function renderPalette(){
    const query=$('palette-input').value.toLowerCase().replace(/^[\/@]/,'').trim(),entries=[],mentioning=$('palette-input').dataset.mention==='true';
    for(const node of notes())if(!query||node.label.toLowerCase().includes(query))entries.push({title:node.label,meta:label(node),run:()=>{if(mentioning){$('question').value+='@'+node.label+' ';$('question').focus();}else{setWorkspace('galaxy');binding?.select(node);$('detail').focus();}}});
    if(!mentioning)for(const entry of actions())if(!query||entry[0].toLowerCase().includes(query))entries.push({title:entry[0],meta:entry[1],run:entry[2]});
    for(const group of [...new Set(notes().map(n=>n.group))])if(query&&group.toLowerCase().includes(query))entries.unshift({title:'Go to '+group,meta:'Constellation',run:()=>{setWorkspace('galaxy');binding?.filter(group);}});
    paletteItems=entries.slice(0,SETTINGS.paletteLimit);selectedPalette=Math.max(0,Math.min(selectedPalette,paletteItems.length-1));$('palette-results').replaceChildren();
    if(!paletteItems.length)$('palette-results').append(create('p','answer-meta','No matching notes or commands.'));
    paletteItems.forEach((item,index)=>{const button=create('button','palette-result');button.id='palette-option-'+index;button.setAttribute('role','option');button.setAttribute('aria-selected',String(index===selectedPalette));button.append(create('span',null,item.title),create('small',null,item.meta));button.onclick=()=>{closePalette();item.run();sound();};$('palette-results').append(button);});
    $('palette-input').setAttribute('aria-activedescendant','palette-option-'+selectedPalette);
  }
  $('command-launch').onclick=()=>openPalette();$('palette-close').onclick=closePalette;$('palette-input').oninput=()=>{selectedPalette=0;renderPalette();};
  $('palette-input').onkeydown=e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();selectedPalette=(selectedPalette+(e.key==='ArrowDown'?1:-1)+paletteItems.length)%Math.max(1,paletteItems.length);renderPalette();$('palette-option-'+selectedPalette)?.scrollIntoView({block:'nearest'});}if(e.key==='Enter'){e.preventDefault();const item=paletteItems[selectedPalette];if(item){closePalette();item.run();}}};
  $('command-palette').addEventListener('click',e=>{if(e.target===$('command-palette'))closePalette();});
  document.querySelectorAll('[data-workspace]').forEach(button=>button.onclick=()=>setWorkspace(button.dataset.workspace));
  $('command-model').onclick=()=>$('brain-chip').click();$('computer-control').onclick=()=>setWorkspace('computer');
  $('answer-close').onclick=()=>setBriefing(false);
  $('answer-read').onclick=()=>{$('full-answer-text').textContent=$('answer').textContent;$('full-answer').showModal();};
  $('full-answer-close').onclick=()=>$('full-answer').close();
  $('answer-galaxy').onclick=()=>{setWorkspace('galaxy');const ids=lastAnswer?.nodes||[];if(ids.length){binding?.select(notes().find(n=>n.id===ids[0]));insight(ids.length+' cited '+(ids.length===1?'note':'notes')+' shown in your galaxy.');}};
  $('detail-edit').onclick=()=>{const node=binding?.selected();if(node)openNote(node,true);};
  $('detail-ask').onclick=()=>{const node=binding?.selected();if(node)askAbout(node);};
  $('detail-open').onclick=()=>{const node=binding?.selected();if(node)openNote(node);};
  function workspaceFrame(title,description){const view=$('workspace-view');view.replaceChildren();const body=create('div','workspace-body'),head=create('div','workspace-header'),titles=create('div');titles.append(create('h1',null,title),create('p',null,description));const back=create('button','control','Back to galaxy');back.onclick=()=>setWorkspace('galaxy');head.append(titles,back);body.append(head);view.append(body);return body;}
  function setWorkspace(name){
    window.JarvisWorkflows?.dispose();
    window.JarvisLocalServices?.dispose();
    window.JarvisBrainServices?.dispose();
    currentWorkspace=name;document.body.dataset.workspace=name;window.JarvisGalaxy?.setVisible(name==='galaxy');hidePreview();hideInsight();clearInterval(workTimer);workTimer=null;
    for(const selector of ['.sidebar','#graph','.scene-controls','#jarvis-orb-host','#detail','#chat','#galaxy-tree']){const element=document.querySelector(selector);if(element){element.inert=name!=='galaxy';if(name==='galaxy')element.removeAttribute('aria-hidden');else element.setAttribute('aria-hidden','true');}}
    $('workspace-view').hidden=name==='galaxy';$('breadcrumb').textContent='/ '+({galaxy:'Knowledge Galaxy',brain:'Jarvis Brain',work:'Mission Control',integrations:'Integrations'}[name]||name.charAt(0).toUpperCase()+name.slice(1));
    document.querySelectorAll('[data-workspace]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.workspace===name)));
    if(name==='galaxy'){window.JarvisGalaxy?.wake();return;}
    if(['notes','library'].includes(name)){renderNotes(name);return;}
    if(name==='brain'){window.JarvisBrainServices?.mountBrain(workspaceFrame('Jarvis Brain','Your shared Obsidian memory, searched locally.'));return;}
    if(['agents','studio'].includes(name)){window.JarvisWorkflows?.mount(workspaceFrame('Agent workflows','Real execution, saved progress and verified findings.'));return;}
    if(name==='work'){renderWork();workTimer=setInterval(updateWork,SETTINGS.statusMs);return;}
    if(name==='settings'||name==='voice'){renderSettings(name);return;}
    if(name==='integrations'){renderIntegrations();return;}
    if(name==='vault'){renderVault();return;}
    if(name==='history'){renderHistory();return;}
    if(name==='help'){renderHelp();return;}
    renderUnavailable(name);
  }
  function row(parent,title,description,control){const r=create('div','list-row'),copy=create('div');copy.append(create('strong',null,title));if(description)copy.append(create('p',null,description));r.append(copy);if(control)r.append(control);parent.append(r);return r;}
  function button(text,handler){const b=create('button','control',text);b.onclick=handler;return b;}
  function renderNotes(kind='notes'){
    const body=workspaceFrame(kind==='brain'?'Project Brain':kind==='library'?'Library':'Notes','Your Markdown files. Changes stay in the same local collection.');
    window.JarvisUltra?.mountNotes(body);
    const layout=create('div','notes-layout'),list=create('nav','notes-list'),reader=create('article','note-reader'),backlinks=create('aside','backlinks');list.setAttribute('aria-label','All notes');
    notes().filter(n=>!n.capture_hub).forEach(node=>list.append(button(node.label,()=>readInto(node,reader,backlinks))));
    layout.append(list,reader,backlinks);body.append(layout);
    const node=currentNote||binding?.selected()||notes().find(n=>!n.capture_hub);if(node)readInto(node,reader,backlinks);else reader.append(create('p',null,'No notes are indexed.'));
  }
  async function readInto(node,reader,backlinks,editing=false){
    currentNote=node;const sequence=++noteRequest;reader.replaceChildren(create('p','answer-meta','Opening note…'));backlinks.replaceChildren();
    try{const data=await request(noteURL(node));if(sequence!==noteRequest)return;
      reader.replaceChildren(create('div','eyebrow',label(node)),create('h1',null,data.title),create('div','note-metadata',metadata(node)));
      const content=create('div','note-content');reader.append(content);
      if(editing){const editor=create('textarea','note-editor');editor.value=data.text;editor.setAttribute('aria-label','Edit '+data.title);content.append(editor,button('Review changes',()=>approveEdit(node,data,editor.value,()=>readInto(node,reader,backlinks))),button('Cancel',()=>readInto(node,reader,backlinks)));}
      else{renderMarkdown(content,data.text,(line,checked)=>{const lines=data.text.split('\n');lines[line]=lines[line].replace(/^(\s*[-*]\s+\[)[ xX](\])/,(_,a,b)=>a+(checked?'x':' ')+b);approveEdit(node,data,lines.join('\n'),()=>readInto(node,reader,backlinks));});
        const controls=create('div','card-actions');controls.append(button('Edit note',()=>readInto(node,reader,backlinks,true)),button('Ask JARVIS',()=>askAbout(node)),button('Show in galaxy',()=>{setWorkspace('galaxy');binding?.select(node);}));reader.append(controls);}
      const obsidian=window.JarvisBrainServices?.obsidianLink(data.obsidian_uri);if(obsidian)reader.append(obsidian);
      backlinks.append(create('div','eyebrow','Linked notes'));related(node).forEach(n=>backlinks.append(button(n.label,()=>readInto(n,reader,backlinks))));
      if(!related(node).length)backlinks.append(create('p','answer-meta','No indexed links.'));
      backlinks.append(neighborhood(node));announce('Opened '+data.title);
    }catch(error){if(sequence===noteRequest)reader.replaceChildren(create('p','answer-meta',error.message));}
  }
  function openNote(node,editing=false){currentNote=node;setWorkspace('notes');if(editing){const reader=$('workspace-view').querySelector('.note-reader'),backlinks=$('workspace-view').querySelector('.backlinks');readInto(node,reader,backlinks,true);}}
  function renderMarkdown(container,text,onCheckbox){
    const lines=text.split('\n');lines.forEach((line,index)=>{
      if(index===0&&/^# /.test(line))return;
      const check=line.match(/^\s*[-*]\s+\[([ xX])\]\s*(.*)$/);
      if(check){const label=create('label'),input=create('input');input.type='checkbox';input.checked=check[1].toLowerCase()==='x';input.onchange=()=>{input.checked=!input.checked;onCheckbox?.(index,!input.checked);};label.append(input,document.createTextNode(check[2]));const p=create('p');p.append(label);container.append(p);return;}
      const heading=line.match(/^(#{1,4})\s+(.*)/);const element=create(heading?'h2':/^[-*]\s/.test(line)?'p':'p',null,heading?heading[2]:line.replace(/^[-*]\s/,'• '));
      container.append(element);
    });
  }
  function neighborhood(node){
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.classList.add('neighborhood');svg.setAttribute('viewBox','0 0 240 168');svg.setAttribute('role','img');svg.setAttribute('aria-label',node.label+' and '+related(node).length+' linked notes');
    const neighbors=related(node),cx=120,cy=84,rx=82,ry=54;
    neighbors.forEach((n,index)=>{const angle=index/Math.max(1,neighbors.length)*Math.PI*2,x=cx+Math.cos(angle)*rx,y=cy+Math.sin(angle)*ry,line=document.createElementNS(ns,'line');for(const [k,v]of Object.entries({x1:cx,y1:cy,x2:x,y2:y}))line.setAttribute(k,v);svg.append(line);
      const a=document.createElementNS(ns,'a');a.setAttribute('href','#');a.setAttribute('aria-label',n.label);a.onclick=e=>{e.preventDefault();openNote(n);};const dot=document.createElementNS(ns,'circle');dot.setAttribute('cx',x);dot.setAttribute('cy',y);dot.setAttribute('r','3');dot.style.fill=color(n);const title=document.createElementNS(ns,'title');title.textContent=n.label;a.append(dot,title);svg.append(a);});
    const core=document.createElementNS(ns,'circle');core.setAttribute('cx',cx);core.setAttribute('cy',cy);core.setAttribute('r','5');svg.append(core);return svg;
  }
  async function approveEdit(node,data,text,done){
    if(approval?.dataset.executing==='true'){announce('The approved save is still being verified.');return;}
    if(approval)approval.remove();const operation_session=operationKey(),card=create('section','approval-card material');card.id='approval-card';card.setAttribute('role','dialog');card.setAttribute('aria-label','Approve note edit');
    card.append(create('div','eyebrow','Confirm'),create('h2',null,'Save changes?'),create('p',null,node.label+' · This replaces the current Markdown content. Approval expires in five minutes. The file revision is checked before writing.'));
    const preview=create('details','workflow-output');preview.append(create('summary',null,'Review exact contents'),create('h3',null,'Before'),create('pre',null,data.text),create('h3',null,'After'),create('pre',null,text));card.append(preview);
    const actions=create('div','card-actions'),confirm=create('button','primary-button','Save changes'),cancel=button('Not now',()=>{card.remove();approval=null;window.JarvisOrb?.setStatus(null);done?.();});
    let proposed=null;confirm.disabled=true;
    confirm.onclick=async()=>{confirm.disabled=true;cancel.disabled=true;card.dataset.executing='true';window.JarvisOrb?.setStatus('EXECUTING',{progress:null,message:'Saving note'});
      try{if(!proposed)throw Error('The exact edit is not ready for approval.');const response=await request('/approvals/decide',{id:proposed.id,fingerprint:proposed.fingerprint,decision:'approve'}),result=response.result;if(result?.verified!==true)throw Error('The saved result was not verified.');
        binding?.replaceGraph(result.graph);card.dataset.executing='false';window.JarvisOrb?.setStatus(null);card.replaceChildren(create('div','eyebrow','Verified'),create('h2',null,'Changes saved'),create('p',null,'Saved to your existing Markdown file and indexed for recall.'),button('Done',()=>{card.remove();approval=null;window.JarvisOrb?.setStatus(null);done?.();}));sound();announce('Saved and verified '+node.label);
      }catch(error){card.dataset.executing='false';card.append(create('p','answer-meta',error.message));confirm.disabled=false;cancel.disabled=false;window.JarvisOrb?.setStatus('ERROR',{message:error.message});}
    };
    actions.append(confirm,cancel);card.append(actions);$('stage').append(card);approval=card;$('jarvis-orb-host').inert=false;$('jarvis-orb-host').removeAttribute('aria-hidden');window.JarvisOrb?.setStatus('AWAITING APPROVAL',{target:'#approval-card'});confirm.focus();
    try{proposed=await request('/approvals/propose',{id:node.id,text,revision:data.revision,request_key:operation_session});if(approval===card&&card.isConnected){confirm.disabled=proposed.status!=='pending';card.append(create('p','answer-meta','This edit is queued in Mission Control. “Not now” leaves it there for later review.'));}}
    catch(error){if(approval===card&&card.isConnected)card.append(create('p','answer-meta',error.message));}
  }
  function renderWork(){const body=workspaceFrame('Mission Control','Live state from your local JARVIS services.');window.JarvisWorkflows?.mount(body);window.JarvisLocalServices?.mountApprovals(body);window.JarvisLocalServices?.mountPreferenceApprovals(body);row(body,'Eyes','Local posture tracking; camera stays off until you enable it.',button('Toggle Eyes',()=>$('eyes').click()));row(body,'Screen watch','One Entire Screen share for watching and questions.',button('Toggle watch',()=>$('screen').click()));row(body,'Current activity','',create('output','service-state'));body.lastChild.lastChild.id='work-activity';row(body,'Watch state','',create('output','service-state'));body.lastChild.lastChild.id='work-watch';updateWork();}
  async function updateWork(){try{watchState=await request('/watch');if($('work-watch'))$('work-watch').textContent=watchState.active?'Watching':'Off';if($('work-activity'))$('work-activity').textContent=$('voice-status').textContent||'Ready';}catch{if($('work-activity'))$('work-activity').textContent='Local server unavailable';}}
  function settingSelect(parent,title,description,key,options){const select=create('select');select.setAttribute('aria-label',title);options.forEach(([value,text])=>{const option=create('option',null,text);option.value=value;select.append(option);});select.value=preferences[key]||options[0][0];select.onchange=()=>{preferences[key]=select.value;applyPreferences();};row(parent,title,description,select);}
  function renderSettings(kind){
    const body=workspaceFrame(kind==='voice'?'Voice':'Settings','Local controls, shared memory, and saved preferences.');
    window.JarvisLocalServices?.mountHealth(body);
    window.JarvisBrainServices?.mountOperations(body);
    if(kind!=='voice'){row(body,'Obsidian brain','Choose your shared memory folder and control local retrieval.',button('Open Jarvis Brain',()=>setWorkspace('brain')));window.JarvisBrainServices?.mountExport(body);}
    if(kind!=='voice')window.JarvisUltra?.mountSettings(body);
    if(kind!=='voice'){settingSelect(body,'Appearance','Dark observatory or warm paper.','theme',[['dark','Dark'],['light','Light'],['system','System']]);settingSelect(body,'Motion','System accessibility settings are always respected.','motion',[['system','Follow system'],['reduced','Reduced motion']]);
      const sounds=button(preferences.sounds?'On':'Off',()=>{preferences.sounds=!preferences.sounds;store(SETTINGS.preferenceKey,preferences);sounds.textContent=preferences.sounds?'On':'Off';sound();});row(body,'Interface sounds','Quiet, optional, and off by default.',sounds);
      row(body,'Build','V'+(systemState?.version||'2.0')+' · Local Python / Three.js viewer',button('Keyboard shortcuts',()=>setWorkspace('help')));
      const measured=window.JarvisGalaxy?.performance;const performance=create('output','service-state',measured?`${measured.nodes} notes · ${measured.links} links · ${measured.drawCalls} draw calls · ${measured.idle?'idle rendering paused':'active'} · hover ${measured.hoverMs.toFixed(2)}ms`:'Open the galaxy to collect rendering measurements.');performance.id='perf-readout';row(body,'Measured rendering','Measurements describe this device and session; no hardware certification is implied.',performance);
    }
    row(body,'Microphone','Only the explicit microphone control enables listening.',button($('mic').getAttribute('aria-pressed')==='true'?'Turn off':'Enable',()=>$('mic').click()));
    row(body,'Desktop voice shortcuts','Hold Control–Shift–Space to talk from any app. Option–Escape cancels; Option–Command–M mutes. Desktop Island and background-update controls are in the ✦ menu.');
    const wake=button(preferences.wake?'On':'Off',()=>{preferences.wake=!preferences.wake;applyPreferences();wake.textContent=preferences.wake?'On':'Off';});
    row(body,'Wake phrase · Jarvis','Optional, off by default. Applies only after you enable Mic. Hold Space, stop, and relief remain immediate.',wake);
    row(body,'Browser speech output','British English voice preference when available. This control stops browser speech.',button('Stop speaking',()=>$('stop-speech').click()));
    const devices=create('select');devices.setAttribute('aria-label','Microphone device');devices.append(new Option('System default',''));devices.onchange=()=>window.JarvisVoice?.setInputDevice?.(devices.value);row(body,'Input device','Native speech uses the selected device when available.',devices);
    request('/voice/devices').then(data=>{for(const device of data.devices||[])devices.append(new Option(device.name,device.id));devices.value=window.JarvisVoice?.inputDevice?.()||'';}).catch(()=>{});
    row(body,'Model',$('brain-chip').textContent,button('Choose model',()=>$('brain-chip').click()));
    row(body,'Camera tools','Existing permission and calibration controls remain in their controllers.',button('Open Mission Control',()=>setWorkspace('work')));
  }
  function renderIntegrations(){const body=workspaceFrame('Integrations','The same inference backends and runtime model chooser.');request('/model').then(data=>{for(const choice of data.choices||[]){const control=button(choice.available?'Choose':'Details',()=>binding?.ask('switch to '+choice.name));control.setAttribute('aria-label','Choose '+choice.label);row(body,choice.label,choice.reason||choice.backend,control);}}).catch(error=>body.append(create('p',null,error.message)));}
  function renderVault(){const body=workspaceFrame('JARVIS Vault','Your current knowledge is stored as local Markdown.');row(body,'Local note collection',notes().filter(n=>!n.capture_hub).length+' indexed files. Browser access stays on this Mac.',button('Browse notes',()=>setWorkspace('notes')));row(body,'Dedicated Vault service','No encrypted Vault service is present in V1.',create('span','service-state','Not connected'));row(body,'Keys and configuration','Credentials remain outside the browser. Enter keys only in the existing local configuration file.',button('Model integrations',()=>setWorkspace('integrations')));}
  function renderUnavailable(name){
    const title=({computer:'Computer control',agency:'Agency',studio:'Studio',trading:'Trading',agents:'Agents',automations:'Automations'})[name]||name;
    const body=workspaceFrame(title,'Connection required');
    body.append(create('p','note-content','Your current V1 does not include a '+title.toLowerCase()+' service. This workspace is reserved for that connection; no remote action or execution is available.'));
    row(body,'Connection','Supply the existing service module and its real state/action contract to wire this workspace.',create('span','service-state','Not connected'));
    row(body,'Available now','Notes, Eyes, screen watch, and model integrations remain usable.',button('Open Mission Control',()=>setWorkspace('work')));
  }
  function renderHistory(){const body=workspaceFrame('Activity history','Saved local task and approval states.');window.JarvisBrainServices?.mountActivity(body);body.append(create('h2',null,'Browser insights'));if(!history.length)body.append(create('p','note-content','No archived browser insights yet.'));history.forEach(item=>row(body,item.text,new Date(item.time).toLocaleString()));}
  function renderHelp(){const body=workspaceFrame('Keyboard shortcuts','Operate JARVIS without leaving the keyboard.');[['⌘ K / Ctrl K','Search notes, workspaces and actions'],['F','Toggle full view'],['Shift + F','Fit the galaxy'],['Esc','Close the current surface or selection'],['Arrow keys','Walk a selected note’s links'],['Tab','Cycle constellations when the galaxy has focus'],['Space, held','Push to talk outside text fields'],['Shift + drag / right drag','Pan the galaxy'],['Scroll / pinch','Zoom toward the pointer']].forEach(([key,text])=>row(body,key,text));}
  function boot(launch){
    const key='jarvis.boot.hud';try{if(sessionStorage.getItem(key))return;sessionStorage.setItem(key,'1');}catch{}
    const reduced=preferences.motion==='reduced'||matchMedia('(prefers-reduced-motion: reduce)').matches;if(reduced)return;
    const duration=SETTINGS.bootMs,layer=create('div','boot-layer');layer.setAttribute('aria-hidden','true');
    layer.append(create('span',null,'INITIATING J.A.R.V.I.S.'),create('span','boot-progress'));$('stage').append(layer);document.body.classList.add('is-booting');
    window.JarvisGalaxy?.boot?.(duration);
    const done=()=>{clearTimeout(timer);layer.remove();document.body.classList.remove('is-booting');window.JarvisGalaxy?.boot?.(0);document.removeEventListener('keydown',done);document.removeEventListener('pointerdown',done);};
    document.addEventListener('keydown',done,{once:true});document.addEventListener('pointerdown',done,{once:true});const timer=setTimeout(done,duration);
  }
  function connectionState(){const connected=serverAvailable&&backendAvailable;$('system-status').textContent=connected?'ONLINE':'OFFLINE';$('system-dot').dataset.status=connected?'online':'offline';window.JarvisOrb?.setConnected(connected);}
  async function refreshSystem(){try{const previousLaunch=systemState?.launch;systemState=await request('/system');if(binding&&(systemState.notes!==notes().filter(n=>!n.capture_hub).length||(previousLaunch&&previousLaunch!==systemState.launch))){const response=await fetch('/graph-data.js',{cache:'no-store'}),match=(await response.text()).match(/^\s*const GRAPH = ([\s\S]*);\s*$/);if(!response.ok||!match)throw Error('The note index is unavailable.');const data=JSON.parse(match[1]);if(!Array.isArray(data.nodes)||!Array.isArray(data.links))throw Error('Invalid note index.');binding.replaceGraph(data);}serverAvailable=true;$('build-version').textContent='V'+systemState.version;connectionState();if(systemState.launch&&binding)boot(systemState.launch);}catch{serverAvailable=false;connectionState();}}
  document.addEventListener('keydown',e=>{
    if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openPalette();return;}
    if(e.key==='Escape'){
      if(approval){e.preventDefault();e.stopImmediatePropagation();if(approval.dataset.executing==='true'){announce('The approved save is still being verified.');return;}approval.remove();approval=null;window.JarvisOrb?.setStatus(null);return;}
      if(!$('preview').hidden){hidePreview();return;}
      if(currentWorkspace!=='galaxy'){setWorkspace('galaxy');return;}
    }
    if(e.target===$('question')&&e.key==='/'&&!$('question').value){e.preventDefault();openPalette('/');}
    if(e.target===$('question')&&e.key==='@'){e.preventDefault();openMention();}
  });
  function openMention(){
    openPalette('@');
  }
  $('palette-results').addEventListener('click',()=>{delete $('palette-input').dataset.mention;});
  window.addEventListener('jarvis:selection',event=>{
    hidePreview();const node=event.detail?.node;if(!node){selectNote(null);drawLeader();return;}$('detail-meta').textContent=metadata(node);
    selectNote(node,event.detail?.source!=='pointer');
    {const serial=++noteRequest;request(noteURL(node)).then(data=>{if(serial!==noteRequest||binding?.selected()?.id!==node.id)return;$('detail-excerpt').replaceChildren();renderMarkdown($('detail-excerpt'),data.text,(line,checked)=>{const lines=data.text.split('\n');lines[line]=lines[line].replace(/^(\s*[-*]\s+\[)[ xX](\])/,(_,a,b)=>a+(checked?'x':' ')+b);approveEdit(node,data,lines.join('\n'),()=>binding?.select(notes().find(n=>n.id===node.id)||node));});}).catch(()=>{});}
    announce('Selected '+node.label+', '+links(node).length+' linked notes.');drawLeader();
  });
  // Galaxy labels and highlighted real links provide hover feedback without an extra card.
  $('preview').onmouseenter=()=>clearTimeout(hoverTimer);$('preview').onmouseleave=()=>{if(previewNote&&!pins[noteKey(previewNote)])hoverTimer=setTimeout(hidePreview,SETTINGS.hoverMs);};
  window.addEventListener('jarvis:camera',()=>{placePreview();drawLeader();});
  window.addEventListener('jarvis:interaction',()=>{document.querySelector('.scene-foot').dataset.dismissed='true';});
  window.addEventListener('resize',placePreview);
  for(const id of ['detail','preview','answer-card']){
    const sheet=$(id);let startY=null;
    sheet.addEventListener('pointerdown',event=>{if(matchMedia('(max-width:700px)').matches&&!event.target.closest('button,input,textarea,select,a'))startY=event.clientY;});
    sheet.addEventListener('pointerup',event=>{if(startY!==null&&event.clientY-startY>SETTINGS.sheetDismissPx){if(id==='detail')binding?.clear();else if(id==='preview')hidePreview();else sheet.dataset.idle='true';}startY=null;});
    sheet.addEventListener('pointercancel',()=>{startY=null;});
  }
  new MutationObserver(()=>{const text=$('brain-chip').textContent.replace(/\s*▾$/,'');$('command-model').textContent=text.replace(/^.*?·\s*/,'');window.JarvisOrb?.setModel(text);}).observe($('brain-chip'),{childList:true,characterData:true,subtree:true});
  window.JarvisUI={
    operationKey,
    connect(api){binding=api;rebuildTree();applyPreferences();refreshSystem();},
    async refreshGraph(){const data=await request('/notes/graph');binding?.replaceGraph(data);rebuildTree();refreshSystem();},
    sourceLabel(id){return notes().find(node=>node.id===id)?.label||'Note '+id;},
    openSource(id,stable){const node=notes().find(node=>stable?node.stable_id===stable:node.id===id);if(!node)return false;openNote(node);return true;},
    async openBrainSource(id,stable){await this.refreshGraph();return this.openSource(id,stable);},
    openWorkRun(id){setWorkspace('work');return window.JarvisWorkflows?.openRun?.(id);},
    isPinned(node){return Boolean(pins[noteKey(node)]);},
    openPalette,openMention,openNote,setWorkspace,insight,selectNote,setBriefing,toggleFullView,
    onBackend(available){backendAvailable=Boolean(available);connectionState();},
    onQuestion(question){setBriefing(true);lastQuestion=question;$('answer-question').textContent=question;$('answer-question').hidden=false;$('answer-label').textContent='JARVIS / '+question;$('answer-card').dataset.idle='false';$('answer-galaxy').hidden=true;$('answer-read').hidden=true;hidePreview();},
    onAnswer(data){lastAnswer=data;$('answer-galaxy').hidden=!data.nodes?.length;$('answer-read').hidden=false;announce(data.answer);},
    onGraph(){rebuildTree();window.JarvisGalaxy?.refresh();},
    get preferences(){return {...preferences};},
    get activeWorkspace(){return currentWorkspace;}
  };
  function setBriefing(open){$('answer-card').dataset.idle=String(!open);$('briefing-toggle').setAttribute('aria-expanded',String(open));}
  function selectNote(node,expand=false){const pill=$('selected-note');pill.hidden=!node;if(!node){pill.setAttribute('aria-expanded','false');return;}$('selected-title').textContent=node.label;pill.style.setProperty('--selection-color',color(node));pill.setAttribute('aria-expanded',String(Boolean(expand)));}
  function toggleFullView(force){const full=typeof force==='boolean'?force:!document.body.classList.contains('is-full-view');document.body.classList.toggle('is-full-view',full);$('full-view').setAttribute('aria-pressed',String(full));$('full-view-exit').hidden=!full;window.JarvisGalaxy?.wake();}
  const exit=create('button','full-view-exit','Exit full view · F');exit.id='full-view-exit';exit.hidden=true;exit.onclick=()=>toggleFullView(false);document.body.append(exit);
  $('full-view').onclick=()=>toggleFullView();
  document.addEventListener('keydown',event=>{if(event.defaultPrevented||event.metaKey||event.ctrlKey||event.altKey||event.shiftKey||event.target.closest?.('input,textarea,select,[contenteditable="true"],dialog'))return;if(event.key.toLowerCase()==='f'){event.preventDefault();toggleFullView();}if(event.key==='Escape'&&document.body.classList.contains('is-full-view')){event.preventDefault();toggleFullView(false);}});
  $('selected-note').onclick=()=>{const node=binding?.selected();if(!node)return;const expand=$('detail').hidden;$('detail').hidden=!expand;selectNote(node,expand);};
  $('briefing-toggle').onclick=()=>setBriefing($('answer-card').dataset.idle==='true');
  $('attach-note').onclick=openMention;
  $('rail-eyes').onclick=()=>$('eyes').click();$('rail-screen').onclick=()=>$('screen').click();
  function updateClock(){const date=new Date();$('deck-clock').textContent=date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',hour12:false});$('deck-clock').dateTime=date.toISOString();const fraction=(date.getHours()*60+date.getMinutes())/SETTINGS.clockMinutes;$('clock-progress').style.strokeDashoffset=String(SETTINGS.percent*(1-fraction));}
  for(const [tool,rail]of [['eyes','rail-eyes'],['screen','rail-screen']])new MutationObserver(()=>$(rail).setAttribute('aria-pressed',$(tool).getAttribute('aria-pressed')||'false')).observe($(tool),{attributes:true,attributeFilter:['aria-pressed']});
  updateClock();setInterval(updateClock,SETTINGS.clockMs);document.body.dataset.workspace='galaxy';
  refreshSystem();setInterval(refreshSystem,SETTINGS.statusMs);applyPreferences();
  const openLinkedWorkspace=()=>{if(window.location.hash==='#approvals')setWorkspace('work');};
  window.addEventListener('hashchange',openLinkedWorkspace);openLinkedWorkspace();
})();
