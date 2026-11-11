/* Presentation and navigation over the existing warehouse and saved-answer APIs. */
const uiPaths={
 compass:'<circle cx="12" cy="12" r="9"/><path d="m16 8-3 5-5 3 3-5 5-3z"/>',
 folder:'<path d="M3 7V5h6l2 2h10v13H3z"/>',
 board:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16M6 8v5M12 8v8M18 8v3"/>',
 database:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
 activity:'<path d="M3 17V7m6 10V4m6 13v-7m6 7V2M2 21h20"/>',
 search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
 users:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M17 5a3 3 0 0 1 0 6M21 21v-3a6 6 0 0 0-3-5"/>',
 cost:'<path d="M12 2v20M17 6H9a4 4 0 0 0 0 8h6a3 3 0 0 1 0 6H6"/>',
 clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/>',
 curve:'<path d="M3 3v18h18M6 7h4v4h4v5h6"/>',
 check:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6"/>',
 file:'<path d="M14 3H5v18h14V8zM14 3v6h5M8 13h8M8 17h5"/>'
};
function uiIcon(name){return '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">'+(uiPaths[name]||uiPaths.file)+'</svg>'}
function hydrateUI(){document.querySelectorAll('[data-ui-icon]').forEach(e=>e.innerHTML=uiIcon(e.dataset.uiIcon))}
const ideas=[
 {topic:'Costs',title:'Review population performance',detail:'Choose contract inputs, compare cost and quality, and prepare review work.',question:'Show contract performance',icon:'check',color:'mint'},
 {topic:'Costs',title:'Where is the money going?',detail:'Compare allowed cost per member, by coverage.',question:'What was allowed PMPM by coverage in 2029?',icon:'cost',color:'mint'},
 {topic:'Patients',title:'Find the right population',detail:'Combine conditions, exclusions, and care teams.',question:'Count patients with diabetes and without CKD by PCP',icon:'users',color:'blue'},
 {topic:'Operations',title:'Understand missed visits',detail:'Explore appointment patterns across practices.',question:'Count appointments where kept = 0 by practice',icon:'clock',color:'peach'},
 {topic:'Outcomes',title:'Look beyond a single number',detail:'Explore readmissions over time, by coverage.',question:'Show a Kaplan-Meier readmission curve after discharge during 2029, grouped by coverage, out to 90 days.',icon:'curve',color:'lavender'},
 {topic:'Costs',title:'Investigate claim denials',detail:'Compare denied claims across payers.',question:'Count claims where ever_denied = 1 in 2029 by payer',icon:'file',color:'rose'},
 {topic:'Operations',title:'Turn a finding into a next step',detail:'Build a capacity-limited review worklist.',question:'Which patients should care managers call if we have 10 slots?',icon:'board',color:'sand'}
];
let savedAnalyses=[],sourceAtlas=null,sourceError=null,currentPage='explore',activeContext=null,activeTab='overview',lastDialogFocus=null,conversationEpoch=0;
function showPage(name,focus=false){
 if(!['explore','saved','data'].includes(name))name='explore';
 currentPage=name;
 ['explore','saved','data'].forEach(n=>$('#page-'+n).hidden=n!==name);
 document.querySelectorAll('.app-nav [data-page]').forEach(e=>{if(e.dataset.page===name)e.setAttribute('aria-current','page');else e.removeAttribute('aria-current')});
 $('#page-name').textContent={explore:'Ask your data',saved:'Saved answers',data:'Sources'}[name];
 if(name==='saved')renderSaved();if(name==='data')loadSources();
 if(focus){$('#main').focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'})}
}
function setResultMode(on){document.body.classList.toggle('has-result',on);$('#answer').hidden=!on;showPage('explore')}
function renderIdeas(topic='All topics'){
 $('#topic-filters').innerHTML=['All topics','Patients','Costs','Operations','Outcomes'].map(t=>'<button type="button" data-topic="'+esc(t)+'" aria-pressed="'+(topic===t)+'">'+esc(t)+'</button>').join('');
 $('#examples').innerHTML=ideas.map((x,i)=>(topic==='All topics'?i<3:topic===x.topic)?'<button type="button" class="question-card" data-idea="'+i+'" data-color="'+x.color+'"><span class="tile-icon">'+uiIcon(x.icon)+'</span><span class="card-arrow" aria-hidden="true">↗</span><strong>'+esc(x.title)+'</strong><span class="question-detail">'+esc(x.detail)+'</span></button>':'').join('');
}
function draftQuestion(q){if(state.busy)return;newConversation();$('#q').value=q;$('#q').focus();$('#q').scrollIntoView({block:'center',behavior:'smooth'})}
function shortDate(s){return s?new Date(s).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'}):'Saved answer'}
async function fetchJSON(url){const r=await fetch(url);let d;try{d=await r.json()}catch(_){throw Error('Could not load this view. Please try again.')}if(!r.ok||d.error)throw Error(d.error||'This view is unavailable.');return d}
async function loadSaved(){
 try{const d=await fetchJSON('/api/stack/action-contexts');savedAnalyses=d.contexts||[];$('#saved-count').textContent=savedAnalyses.length||'';
 $('#recent-analyses').innerHTML=savedAnalyses.slice(0,3).map(c=>'<a class="recent-row" href="/ask?context='+encodeURIComponent(c.id)+'">'+uiIcon('file')+'<div><strong>'+esc(c.question)+'</strong><small>'+shortDate(c.created_at)+' · '+(c.result_scope_available===false?'Analytical summary':c.patients==null?'Open to inspect saved scope':fmt(c.patients)+' patients in scope')+'</small></div><span aria-hidden="true">↗</span></a>').join('')||'<p class="hint">Your first analysis will appear here after you ask a question.</p>';renderSaved()}
 catch(e){$('#recent-analyses').innerHTML='<p class="hint">Saved analyses couldn’t load. <button class="text-button" data-reload-saved>Retry</button></p>';$('#saved-list').innerHTML='<div class="empty-library">'+esc(e.message)+' <button class="text-button" data-reload-saved>Retry</button></div>'}
}
function renderSaved(){
 const term=$('#saved-search').value.toLowerCase(),rows=savedAnalyses.filter(x=>x.question.toLowerCase().includes(term));
 $('#saved-caption').textContent=rows.length+(rows.length===1?' analysis':' analyses')+(savedAnalyses.length===100?' · showing the 100 most recent':'');
 $('#saved-list').innerHTML=rows.map(c=>'<article class="analysis-row">'+uiIcon('file')+'<div><h2><a href="/ask?context='+encodeURIComponent(c.id)+'">'+esc(c.question)+'</a></h2><p>'+shortDate(c.created_at)+' · '+(c.result_scope_available===false?'Analytical summary':c.patients==null?'Open to inspect saved scope':fmt(c.patients)+' patients in scope')+'</p></div><a class="btn small" href="/action-center?context='+encodeURIComponent(c.id)+'">Action workspace ↗</a></article>').join('')||'<div class="empty-library">'+(term?'No saved questions match your search.':'Ask your first question to start your analysis library.')+'</div>';
}
async function loadSources(){
 if(!sourceAtlas&&!sourceError){
  $('#source-list').innerHTML='<p class="hint">Reading the source inventory…</p>';
  try{sourceAtlas=await fetchJSON('/api/stack/source-atlas')}
  catch(e){sourceError=e.message}
 }
 if(sourceError){$('#source-list').innerHTML='<p class="hint">'+esc(sourceError)+' <button class="text-button" data-reload-sources>Retry</button></p>';return}
 $('#warehouse-stats').innerHTML=[[sourceAtlas.patients,'Patients'],[sourceAtlas.source_categories,'Source categories'],[sourceAtlas.records,'Received records']].map(([n,l])=>'<div><strong>'+fmt(n,0)+'</strong><span>'+l+'</span></div>').join('');
 const term=$('#source-search').value.toLowerCase(),rows=(sourceAtlas.sources||[]).filter(s=>[s.stream,label(s.stream),s.domain].join(' ').toLowerCase().includes(term));
 $('#source-caption').textContent=rows.length+(rows.length===1?' source':' sources')+' · data as of '+shortDate(sourceAtlas.snapshot?.as_of);
 $('#source-list').innerHTML=rows.map(s=>'<div class="source-card"><strong>'+esc(label(s.stream))+'</strong><p>'+fmt(s.records,0)+' records · '+fmt(s.patients,0)+' patients</p><button class="btn small" data-source-question="'+esc(s.stream)+'">Ask about this source ↗</button></div>').join('')||'<p class="hint">No sources match. Try another name.</p>';
}
function syncScope(t){
 const retained=(t?.pending||t?.error)?t.scopeBefore:null;
 const d=t?.data||{},c=retained?.context||d.action_context||t?.action_context;
 activeContext=c&&/^ANS-[0-9a-f]{24}$/.test(c.id)?c:null;
 if(activeContext)history.replaceState(null,'','/ask?context='+encodeURIComponent(activeContext.id)+(demo.on?'&demo=1':''));
 const option=$('#question-scope').querySelector('[value="saved"]');
 const a=d.answer||{},summaryWithoutCohort=c?.result_scope_available===false||(!(a.patient_ids||[]).length&&(a.analysis_population_count!=null||a.answer_type==='value'));
 option.disabled=!activeContext||!!t?.archiveBlocked||summaryWithoutCohort;
 option.textContent=activeContext?(summaryWithoutCohort?'Analytical summary · no selected patients':'Follow up · '+fmt(c.patients??a.patient_ids?.length??0)+' saved patients'):'Follow up on this result';
 const input=$('#question-scope').querySelector('[value="input"]');
 input.disabled=!activeContext||!!t?.archiveBlocked||c?.input_patients==null;
 input.textContent=c?.input_patients!=null?'Original input · '+fmt(c.input_patients)+' patients':'Original input unavailable';
 const auto=$('#question-scope').querySelector('[value="auto"]');auto.disabled=!activeContext||!!t?.archiveBlocked;
 $('#question-scope').querySelectorAll('[data-population-scope]').forEach(o=>o.remove());
 for(const population of c?.population_scopes||[]){const o=new Option(population.label+' · '+fmt(population.patients)+' patients','population:'+population.id);o.dataset.populationScope=population.id;o.disabled=!!t?.archiveBlocked;$('#question-scope').append(o)}
 $('#population-scopes').hidden=!activeContext||!!t?.archiveBlocked||!c?.population_scope_discovery_available;
 $('#question-scope').value=option.disabled?(auto.disabled?'warehouse':'auto'):'saved';
 if(retained?.value==='input'&&!input.disabled)$('#question-scope').value='input';
 if(retained?.value&&[...$('#question-scope').options].some(o=>o.value===retained.value&&!o.disabled))$('#question-scope').value=retained.value;
 updateScopeNote();
}
function updateScopeNote(){
 const scope=$('#question-scope').value;
 $('#scope-note').textContent=scope==='input'?'Uses the original question’s saved input patients. These are not necessarily patients selected by its answer, and this choice does not authorize work.':scope==='saved'?'This follow-up keeps the exact saved patient set. Choose “Entire warehouse” to start a broader question.':scope==='auto'?'Ask about this analysis. For a patient follow-up, choose the population behind the count you mean.':scope.startsWith('population:')?'Follows this source-defined population, keeping its original period and filters.':'Use your own words. Add a date range, comparison, or filter.';
}
async function openSavedContext(id){
 const epoch=++conversationEpoch;
 setResultMode(true);$('#answer').innerHTML='<div class="working-state"><div class="working-title"><span class="working-ring"></span>Opening saved analysis…</div></div>';
 try{
  const d=await fetchJSON('/api/stack/action-context?context='+encodeURIComponent(id));if(epoch!==conversationEpoch)return;
  const c=d.context;state.cid=null;store.set('agere-conversation',null);
  const t={question:c.original_question||c.question,resolved:c.question,archive:true,archiveBlocked:c.stale||c.invalidated,data:{workflow:d.workflow?.steps||[],answer:c.answer,graph:c.graph,results:c.results,planner_receipt:c.planner_receipt,planning_economics:c.planning_economics,action_context:{id:c.id,patients:c.patient_ids.length,input_patients:c.input_patients,result_scope_available:c.result_scope_available,population_scopes:c.population_scopes,population_scope_discovery_available:c.population_scope_discovery_available},new_paid_api_calls:c.planner_receipt?.new_paid_api_calls}};
  t.archiveNotice=c.invalidated?'This saved answer was invalidated. Its history is available, but it cannot be used for new work or scoped follow-ups.':c.stale?'The warehouse has changed since this answer. Start a new analysis to use current data.':null;
  state.turns=[t];renderTurn(0);
 }catch(e){if(epoch!==conversationEpoch)return;$('#answer').innerHTML='<h2 class="headline">This analysis couldn’t open.</h2><p>'+esc(e.message)+'</p><button class="btn" data-page="saved">Back to saved analyses</button>'}
}
function showAnswerTab(name){
 if(!['overview','data','method'].includes(name))return;activeTab=name;
 document.querySelectorAll('[data-answer-tab]').forEach(b=>{const selected=b.dataset.answerTab===name;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1});
 document.querySelectorAll('[data-answer-panel]').forEach(p=>p.hidden=p.dataset.answerPanel!==name);
}
function dataTables(a){
 if(a.answer_type==='composite')return(a.sections||[]).flatMap(s=>dataTables(s.answer).map(t=>({...t,title:s.title+' · '+t.title})));
 if(a.table?.length)return[{title:'Returned rows',rows:a.table,columns:a.table_columns}];
 if(a.patient_ids?.length)return[{title:'Patients in the answer',rows:a.patient_ids.map(patient_id=>({patient_id}))}];
 if(a.answer_type==='count'||a.answer_type==='value')return[{title:'Computed value',rows:[{[a.answer_type]:a.count??a.value,unit:a.unit||''}]}];
 return[];
}
function renderResultData(term=''){
 const a=state.turns[state.active]?.data?.answer;if(!a||!$('#result-data'))return;
 const tables=dataTables(a);let total=0,shown=0;
 $('#result-data').innerHTML=tables.map(t=>{total+=t.rows.length;const rows=t.rows.filter(r=>!term||Object.values(r).some(v=>String(cell(v)).toLowerCase().includes(term.toLowerCase())));shown+=rows.length;return '<h3>'+esc(t.title)+'</h3>'+(rows.length?tableHTML(rows,{name:'agere-result',cols:t.columns}):'<p class="hint">No matching rows.</p>')}).join('')||'<p class="hint">This answer has no tabular result. See the overview and method for the returned findings.</p>';
 $('#result-count').textContent=shown+' of '+total+' rows';
}
function openAppDialog(id){lastDialogFocus=document.activeElement;$('#'+id).showModal()}
function setupExplorer(){
 hydrateUI();renderIdeas();loadSaved();setupExperience();
 document.addEventListener('click',e=>{
  const b=e.target.closest('[data-page],[data-topic],[data-idea],[data-close-dialog],[data-answer-tab],[data-reload-saved],[data-reload-sources],[data-source-question]');
  if(!b)return;
  if(b.dataset.page){showPage(b.dataset.page,true);history.replaceState(null,'','/ask'+(b.dataset.page==='explore'?'':'#'+b.dataset.page))}
  if(b.dataset.topic)renderIdeas(b.dataset.topic);
  if(b.dataset.idea!=null)draftQuestion(ideas[Number(b.dataset.idea)].question);
  if(b.dataset.closeDialog)$('#'+b.dataset.closeDialog).close();
  if(b.dataset.answerTab)showAnswerTab(b.dataset.answerTab);
  if(b.hasAttribute('data-reload-saved'))loadSaved();
  if(b.hasAttribute('data-reload-sources')){sourceError=null;loadSources()}
  if(b.dataset.sourceQuestion)draftQuestion('How many received records are in the '+b.dataset.sourceQuestion+' source?');
 });
 document.addEventListener('input',e=>{if(e.target.id==='result-search')renderResultData(e.target.value)});
 document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();if(!state.busy){showPage('explore');$('#q').focus()}}
  const tab=e.target.closest('[data-answer-tab]');
  if(tab&&['ArrowRight','ArrowLeft','Home','End'].includes(e.key)){e.preventDefault();const tabs=['overview','data','method'],i=tabs.indexOf(tab.dataset.answerTab),n=e.key==='Home'?0:e.key==='End'?2:(i+(e.key==='ArrowRight'?1:2))%3;showAnswerTab(tabs[n]);document.querySelector('[data-answer-tab="'+tabs[n]+'"]').focus()}
 });
 $('#q').addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();$('#askf').requestSubmit()}});
 $('#saved-search').addEventListener('input',renderSaved);$('#source-search').addEventListener('input',()=>loadSources());
 $('#question-scope').addEventListener('change',updateScopeNote);
 $('#population-scopes').addEventListener('click',async()=>{
  if(!activeContext||state.busy)return;
  const button=$('#population-scopes'),parent=activeContext.id,epoch=conversationEpoch,t=state.turns[state.active];
  state.busy=true;button.disabled=true;button.textContent='Tracing populations…';$('#askb').disabled=true;$('#q').readOnly=true;
  try{
   const response=await fetch('/api/stack/action-populations',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({context_id:parent})});
   const prepared=await response.json();if(!response.ok||prepared.error)throw Error(prepared.error||'The populations could not be opened.');
   if(epoch!==conversationEpoch||activeContext?.id!==parent)return;
   t.data.action_context=prepared.action_context;syncScope(t);
   $('#scope-note').textContent=prepared.population_scopes.length?'Choose the population behind your count, then ask to list, group or compare those patients.':'No exact patient population could be established for these results.';
  }catch(error){if(epoch===conversationEpoch)$('#scope-note').textContent=error.message}
  finally{state.busy=false;button.disabled=false;button.textContent='Explore patient populations';$('#askb').disabled=false;$('#q').readOnly=false}
 });
 $('#help-button').onclick=()=>openAppDialog('help-dialog');$('#usage-button').onclick=()=>openAppDialog('usage-dialog');$('#footer-usage').onclick=()=>openAppDialog('usage-dialog');
 document.querySelectorAll('.app-dialog').forEach(d=>d.addEventListener('close',()=>lastDialogFocus?.focus()));
 const params=new URLSearchParams(location.search);
 if(params.get('context'))openSavedContext(params.get('context'));else{loadConversation();showPage(location.hash.slice(1)||'explore')}
}
