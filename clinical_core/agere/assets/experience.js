/* Visible activity is a projection of the existing request journal, not a timer-driven story. */
const workStages=[['understand','⌁','Understand'],['compute','∑','Calculate'],['verify','✓','Check'],['reason','✳','Reason'],['respond','↗','Answer']];
let syncConversationNavigation=()=>{};
function progressKey(){return crypto.randomUUID().replaceAll('-','')}
function workingHTML(t){
 return '<div class="working-state" aria-live="off" data-work-key="'+esc(t.progressKey||'')+'"><div class="working-eyebrow"><span class="live-dot"></span>FOLLOWING THE EVIDENCE<span class="working-clock" data-work-clock aria-hidden="true">0:00</span></div><div class="working-title" role="status" aria-live="polite" data-work-title>Preparing your question</div><p class="working-note">The stack chooses an approach, works with the source data, and checks the result. Recorded steps appear below.</p><div class="evidence-path" aria-label="Recorded analysis activity">'+workStages.map(([id,symbol,label])=>'<div class="path-node" data-work-stage="'+id+'" data-reached="false"><div class="node-symbol" aria-hidden="true">'+symbol+'</div>'+label+'</div>').join('')+'</div><div class="work-counts"><span><b data-work-reads>0</b> <span data-work-read-label>queries finished</span></span><span><b data-work-checks>0</b> source checks finished</span><span><b data-work-models>0</b> model attempts recorded</span></div><ol class="work-events" data-work-events aria-label="Recent recorded steps"></ol><p class="work-unavailable" data-work-note>Waiting for the first recorded activity…</p></div>';
}
function paintWork(t){
 const root=document.querySelector('[data-work-key="'+t.progressKey+'"]');if(!root)return;
 const p=t.progress||{updates:[],sequence:-1},updates=p.updates||[],active=new Map(),reached=new Set();
 for(const e of updates){reached.add(e.group);if(e.key){if(e.state==='running')active.set(e.key,e);else active.delete(e.key)}}
 const running=[...active.values()],current=running.at(-1)||updates.at(-1);
 const title=root.querySelector('[data-work-title]'),label=current?.label||'Preparing your question';if(title.textContent!==label)title.textContent=label;
 const elapsed=Math.max(0,Math.floor((performance.now()-(t.startedAt||performance.now()))/1000));root.querySelector('[data-work-clock]').textContent=Math.floor(elapsed/60)+':'+String(elapsed%60).padStart(2,'0');
 root.querySelectorAll('[data-work-stage]').forEach(node=>{const id=node.dataset.workStage;node.dataset.reached=String(reached.has(id));node.dataset.active=String(running.some(e=>e.group===id));const last=updates.filter(e=>e.group===id).at(-1);node.dataset.state=last?.state||'waiting'});
 root.querySelector('[data-work-reads]').textContent=updates.filter(e=>e.kind==='component_finished'&&['investigator_sql','governed_sql'].includes(e.component)&&e.state==='completed').length;
 root.querySelector('[data-work-read-label]').textContent=root.querySelector('[data-work-reads]').textContent==='1'?'query finished':'queries finished';
 root.querySelector('[data-work-checks]').textContent=updates.filter(e=>e.component==='claim_local_source_review'&&e.kind==='component_finished'&&e.state==='completed').length;
 root.querySelector('[data-work-models]').textContent=updates.filter(e=>e.kind==='reserved').length;
 if(root.dataset.workSequence!==String(p.sequence)){
  root.querySelector('[data-work-events]').innerHTML=updates.filter(e=>e.state!=='running').slice(-4).map(e=>'<li><span class="event-mark" aria-hidden="true">'+(e.state==='failed'?'!':'✓')+'</span>'+esc(e.label)+'<time>'+esc(new Date(e.at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}))+'</time></li>').join('');root.dataset.workSequence=String(p.sequence);
 }
 const note=root.querySelector('[data-work-note]');note.hidden=false;
 note.textContent=p.unavailable?'Activity updates are unavailable. The analysis request is still open; it has not been resent.':p.status==='waiting'?'Waiting for the first recorded activity…':p.status==='complete'?'The analysis returned. Preparing the answer view…':p.status==='failed'?'The request stopped. Waiting for its saved response…':'Only observed steps are shown. A model reservation does not yet establish a returned response.';
}
function beginWork(t){
 t.progress={sequence:-1,updates:[],status:'waiting'};t.startedAt=performance.now();let stopped=false,timer;
 async function poll(){
  if(stopped||!t.pending)return;
  try{const r=await fetch('/api/stack/analysis-progress?key='+encodeURIComponent(t.progressKey)+'&after='+t.progress.sequence,{cache:'no-store'});if(!r.ok)throw Error('unavailable');const d=await r.json();if(stopped)return;
   const old=t.progress.updates;t.progress={...d,updates:[...old,...(d.updates||[]).filter(e=>e.sequence>t.progress.sequence)]};
  }catch(_){t.progress.unavailable=true}
  paintWork(t);if(!stopped&&t.pending)timer=setTimeout(poll,1200);
 }
 poll();const clock=setInterval(()=>{if(!stopped&&t.pending)paintWork(t)},1000);
 return()=>{stopped=true;clearTimeout(timer);clearInterval(clock)};
}
function recordedWork(t){
 const updates=t.progress?.updates||[];if(!updates.length)return '';
 const queries=updates.filter(e=>e.kind==='component_finished'&&['investigator_sql','governed_sql'].includes(e.component)&&e.state==='completed').length;
 const checks=updates.filter(e=>e.kind==='component_finished'&&e.component==='claim_local_source_review'&&e.state==='completed').length;
 const models=updates.filter(e=>e.kind==='reserved').length;
 return '<details class="recorded-work"><summary>See the work behind this answer</summary><p>'+queries+' source '+(queries===1?'query':'queries')+' finished · '+checks+' source checks finished · '+models+' model attempts recorded</p><ul>'+updates.filter(e=>e.state!=='running').slice(-4).map(e=>'<li>'+esc(e.label)+'</li>').join('')+'</ul><p>Steps shown here belong to this request. The method view keeps the answer’s evidence and definitions.</p></details>';
}
function completeWork(t){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),3000);
 fetch('/api/stack/analysis-progress?key='+encodeURIComponent(t.progressKey)+'&after='+(t.progress?.sequence??-1),{cache:'no-store',signal:controller.signal}).then(r=>r.ok?r.json():null).then(d=>{if(!d)return;const old=t.progress||{updates:[],sequence:-1};t.progress={...d,updates:[...old.updates,...(d.updates||[]).filter(e=>e.sequence>old.sequence)]};if(state.turns[state.active]===t){const box=document.querySelector('[data-recorded-work]');if(box)box.innerHTML=recordedWork(t)}}).catch(()=>{}).finally(()=>clearTimeout(timer));
}
function transcriptHTML(t,i){const a=t.data?.answer||{},headline=a.headline||t.summary||t.error||'This answer is saved in the conversation.';
 return '<article class="transcript-turn"><div class="user-message">'+esc(t.question)+'</div><div class="assistant-summary"><p>'+esc(typeof headline==='string'?headline:'The answer is available.')+'</p><button class="text-button" type="button" data-turn="'+i+'">Open this answer <span aria-hidden="true">↗</span></button></div></article>';
}
function paintTranscript(i){
 $('#transcript-before').innerHTML=state.turns.slice(0,i).map((t,n)=>transcriptHTML(t,n)).join('');
 $('#transcript-after').innerHTML=state.turns.slice(i+1).map((t,n)=>transcriptHTML(t,n+i+1)).join('');
 document.body.classList.remove('nav-open');$('#history-button').setAttribute('aria-expanded','false');syncConversationNavigation();
}
function setupExperience(){
 const compact=matchMedia('(max-width:800px)'),navigation=document.querySelector('.app-sidebar');
 const sync=()=>{navigation.inert=compact.matches&&!document.body.classList.contains('nav-open');navigation.setAttribute('aria-hidden',String(navigation.inert))};
 syncConversationNavigation=sync;
 const toggle=on=>{document.body.classList.toggle('nav-open',on);$('#history-button').setAttribute('aria-expanded',String(on));sync();if(on)$('#close-history').focus();else $('#history-button').focus()};
 compact.addEventListener('change',sync);sync();
 $('#history-button').onclick=()=>toggle(!document.body.classList.contains('nav-open'));$('#close-history').onclick=()=>toggle(false);
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('nav-open'))toggle(false)});
 $('#right').addEventListener('toggle',()=>{if($('#right').open&&!$('#right').dataset.loaded){$('#right').dataset.loaded='true';loadBriefing()}});
 fetchJSON('/api/stack/source-atlas').then(d=>{sourceAtlas=d;$('#live-records').textContent=fmt(d.records,0);$('#live-patients').textContent=fmt(d.patients,0);$('#live-sources').textContent=fmt(d.source_categories,0);$('#connection-status').dataset.state='ready';$('#connection-status').innerHTML='<i></i>Warehouse connected'}).catch(()=>{$('#connection-status').textContent='Connection not confirmed'});
}
