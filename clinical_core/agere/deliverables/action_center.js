'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = value => String(value || '').replaceAll('_', ' ');
const key = new URLSearchParams(location.search).get('context');
const PAGE_SIZE = 15;
let selectedTemplate = 'command';
const states = {needs_review:'Ready to review', in_review:'In progress', blocked:'Needs attention', review_complete:'Review complete', cancelled:'Cancelled'};
const stateHints = {needs_review:'Ready for a first look', in_review:'Someone is on it', blocked:'Waiting on a next step', review_complete:'Review wrapped up'};
const programNames = {evidence_review:'Evidence review', pharmd:'PharmD review', rpm:'Remote monitoring', custom:'Custom follow-up'};
const icons = {
  overview:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  people:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6M21 21v-3a6 6 0 0 0-4-5.65"/>',
  board:'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 3v18M15 3v18M5.5 7h1M11.5 7h1M17.5 7h1M5.5 10h1M11.5 10h1"/>',
  spark:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3zM20 2v4M18 4h4"/>',
  pill:'<path d="M5 19a6 6 0 0 1 0-8l6-6a5.66 5.66 0 0 1 8 8l-6 6a6 6 0 0 1-8 0zM8 8l8 8"/>',
  pulse:'<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  arrow:'<path d="M5 12h14m-6-6 6 6-6 6"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3zM8 12l3 3 5-6"/>',
  refresh:'<path d="M20 10a8 8 0 1 0-1 8M20 4v6h-6"/>',
  download:'<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  file:'<path d="M14 3H5v18h14V8l-5-5zM14 3v6h5M8 13h8M8 17h5"/>',
  layers:'<path d="m12 3 10 5-10 5L2 8l10-5zM2 12l10 5 10-5M2 16l10 5 10-5"/>',
  check:'<path d="m5 12 4 4L19 6"/>'
};
function icon(name) { return '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">' + (icons[name] || icons.file) + '</svg>'; }
function hydrateIcons(root=document) { root.querySelectorAll('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon)); }
const num = n => Number(n || 0).toLocaleString();
const date = value => value ? new Date(value).toLocaleDateString(undefined, {month:'short',day:'numeric',year:'numeric',timeZone:'UTC'}) : 'Date unavailable';
const initials = owner => String(owner || '').trim().split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase();
let data = null, selected = new Set(), busy = false, currentView = 'overview', pageIndex = 0, intent = null, toastTimer, lastDialogTrigger;
const viewNames = {overview:'Overview',cohort:'Your cohort',work:'Work board',studio:'Creative studio'};

async function api(url, body) {
  const response = await fetch(url, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : undefined);
  let result;
  try { result = await response.json(); } catch (_) { throw Error('Could not read the server response. Try again.'); }
  if (!response.ok || result.error) throw Error(result.error || 'The request could not be completed.');
  return result;
}
function notify(message, error=false) {
  clearTimeout(toastTimer);
  $('status').textContent = message;
  $('status').classList.toggle('error', error);
  $('status').hidden = false;
  toastTimer = setTimeout(() => $('status').hidden = true, error ? 12000 : 5500);
}
async function run(fn) {
  if (busy) { notify('Finishing the current request. Your selections are still here.'); return; }
  busy = true;
  try { await fn(); } catch (error) { notify(error.message, true); }
  finally { busy = false; }
}
function canCreate(program = intent || $('program').value) {
  if (!data || data.context.supports_patient_work === false || data.context.stale || data.context.invalidated) return false;
  if (!canChangeWork(program)) return false;
  const answer = data.context.answer;
  return program === 'evidence_review'
    ? ['answered','partial','low_confidence'].includes(answer.status)
    : answer.status === 'answered' && !answer.missing?.length;
}
function canChangeWork(program) {
  return !data.context.stale && !data.context.invalidated && data.context.source_disposition?.status !== 'unavailable'
    && (!data.context.source_review_required || program === 'evidence_review');
}
function generationLabel(d) {
  if (d.generation === 'saved_report') return 'Saved evidence report';
  return d.mockup || d.mode === 'reference_example' ? 'Local template' : d.generation === 'frontier' || d.mode === 'composed' ? 'Frontier generated' : 'Generation not recorded';
}
function costLabel(d) {
  if (d.generation === 'saved_report') return 'Rendered locally; no new model calls';
  if (d.mockup || d.mode === 'reference_example') return 'No model/API calls';
  const costs = d.costs || {};
  return costs.total_usd == null
    ? 'API cost incomplete; known estimate '+(costs.known_api_estimate_usd == null ? 'unavailable' : '$'+Number(costs.known_api_estimate_usd).toFixed(4))
    : 'API estimate $'+Number(costs.total_usd).toFixed(4);
}
function updateGeneration() {
  const frontier = $('generation').value === 'frontier';
  $('generation').querySelector('[value="frontier"]').disabled = !data.allow_frontier;
  $('compose').disabled = !canChangeWork('custom') || (frontier && !data.allow_frontier);
  $('compose').innerHTML = icon('spark')+(frontier ? 'Generate custom view' : 'Build local template');
  $('generation-note').textContent = frontier
    ? (data.allow_frontier ? 'A frontier model designs this view from your saved answer and work. Usage and verification results are recorded.' : 'Frontier generation is disabled on this server. You can choose a local template.')
    : 'Uses a fixed layout. Your brief is saved as design intent; a model will not customize it.';
}
function go(view, focus=true) {
  if (!viewNames[view] || !data) return;
  currentView = view;
  for (const name of Object.keys(viewNames)) $('view-'+name).hidden = name !== view;
  document.querySelectorAll('.workspace-nav [data-view]').forEach(button => {
    if (button.dataset.view === view) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
  });
  $('view-name').textContent = viewNames[view];
  history.replaceState(null, '', location.pathname + location.search + (view === 'overview' ? '' : '#'+view));
  if (view === 'cohort') showPatients();
  if (view === 'work') showWork();
  if (focus) { window.scrollTo({top:0,behavior:'instant'}); $('main').focus({preventScroll:true}); }
}
function openDialog(id) {
  lastDialogTrigger = document.activeElement;
  $(id).showModal();
}
function closeDialog(id) { $(id).close(); }
for (const dialog of document.querySelectorAll('dialog')) {
  dialog.addEventListener('close', () => {
    if (lastDialogTrigger?.isConnected && !lastDialogTrigger.closest('[hidden]')) lastDialogTrigger.focus();
  });
}
function evidence() {
  const c = data.context;
  $('evidence-summary').innerHTML = '<div class="evidence-summary"><h3>'+esc(c.question)+'</h3><p>'+esc(c.answer.headline || '')+'</p><dl><dt>Snapshot</dt><dd>'+esc(date(c.source.as_of))+'</dd><dt>Answer status</dt><dd>'+esc(pretty(c.answer.status))+'</dd><dt>Patient scope</dt><dd>'+num(c.patient_rows.length)+' patients in this saved answer</dd><dt>Known API cost</dt><dd>'+(c.planning_economics?.known_api_estimate_usd == null ? 'Unavailable' : '$'+Number(c.planning_economics.known_api_estimate_usd).toFixed(4))+'</dd></dl></div>';
  $('evidence').textContent = JSON.stringify({answer:c.answer,graph:c.graph,results:c.results,source:c.source,planner_receipt:c.planner_receipt,planning_economics:c.planning_economics}, null, 2);
  openDialog('evidence-dialog');
}
function start(program) {
  intent = program;
  $('program').value = program;
  showIntent();
  if (selected.size) openReview(); else go('cohort');
}
function showIntent() {
  $('intent-banner').hidden = !intent;
  if (intent) {
    $('intent-title').textContent = 'Prepare ' + programNames[intent];
    $('intent-icon').innerHTML = icon(intent === 'pharmd' ? 'pill' : intent === 'rpm' ? 'pulse' : 'file');
  }
  updateSelection();
}
function patientName(pid) {
  const profile = data.context.patient_profiles?.patients?.[pid];
  return profile?.patient_id === pid && typeof profile.display_name === 'string' && profile.display_name.trim()
    ? profile.display_name : pid;
}
function detailValue(value) {
  if (value == null) return '<span class="muted">Not available</span>';
  let structured = value;
  if (typeof value === 'string') {
    try { const parsed = JSON.parse(value); if (parsed && typeof parsed === 'object') structured = parsed; } catch (_) {}
  }
  if (typeof structured === 'object') {
    const label = Array.isArray(structured) ? 'Inspect '+structured.length+' source record'+(structured.length===1?'':'s') : 'Inspect source details';
    return '<details><summary>'+label+'</summary><pre>'+esc(JSON.stringify(structured,null,2))+'</pre></details>';
  }
  return esc(value);
}
function detailFields(entries) {
  return '<dl class="detail-fields">'+entries.map(([k,v])=>'<div><dt>'+esc(pretty(k))+'</dt><dd>'+detailValue(v)+'</dd></div>').join('')+'</dl>';
}
function filteredPatients() {
  const q = $('search').value.trim().toLowerCase(), filter = $('cohort-filter').value;
  const assigned = new Set(data.items.filter(i => i.state !== 'cancelled').map(i => i.patient_id));
  return data.context.patient_rows.filter(p => {
    if (q && !(patientName(p.patient_id)+' '+JSON.stringify(p)).toLowerCase().includes(q)) return false;
    if (filter === 'assigned') return assigned.has(p.patient_id);
    if (filter === 'unassigned') return !assigned.has(p.patient_id);
    if (filter === 'details') return p.evidence_rows.length > 0;
    if (filter === 'selected') return selected.has(p.patient_id);
    return true;
  });
}
function showPatients() {
  if (!data) return;
  const rows = filteredPatients(), pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  pageIndex = Math.min(pageIndex, pages-1);
  const visible = rows.slice(pageIndex*PAGE_SIZE,(pageIndex+1)*PAGE_SIZE);
  $('patients').innerHTML = visible.map(p => {
    const work = data.items.filter(i => i.patient_id === p.patient_id && i.state !== 'cancelled');
    const name = patientName(p.patient_id), label = name === p.patient_id ? name : name+' · '+p.patient_id;
    return '<tr class="'+(selected.has(p.patient_id)?'is-selected':'')+'"><td class="check-cell"><input type="checkbox" data-patient="'+esc(p.patient_id)+'" aria-label="Select '+esc(label)+'" '+(selected.has(p.patient_id)?'checked':'')+'></td><td><button class="patient-button" data-inspect="'+esc(p.patient_id)+'"><span class="patient-avatar">'+icon('people')+'</span><span class="patient-label">'+esc(name)+(name===p.patient_id?'':'<small>'+esc(p.patient_id)+'</small>')+'</span></button></td><td><span class="badge '+(p.evidence_rows.length?'':'peach')+'">'+(p.evidence_rows.length ? num(p.evidence_rows.length)+' returned detail'+(p.evidence_rows.length===1?'':'s') : 'Identifier only')+'</span></td><td>'+(work.length ? '<span class="badge">'+work.length+' review'+(work.length===1?'':'s')+'</span>' : '<span class="muted">Not started</span>')+'</td><td><button class="row-arrow" data-inspect="'+esc(p.patient_id)+'" aria-label="Open '+esc(label)+' details">'+icon('arrow')+'</button></td></tr>';
  }).join('') || '<tr><td colspan="5"><div class="empty-state"><h3>No patients in this view.</h3><p>Try a different search or filter. Your selections are preserved.</p></div></td></tr>';
  $('cohort-results').textContent = num(rows.length)+' matching · '+num(data.context.patient_rows.length)+' in saved answer';
  $('page-caption').textContent = rows.length ? (pageIndex*PAGE_SIZE+1)+'–'+Math.min((pageIndex+1)*PAGE_SIZE,rows.length)+' of '+num(rows.length) : '0 patients';
  $('previous-page').disabled = pageIndex === 0;
  $('next-page').disabled = pageIndex >= pages-1;
  $('select-page').checked = visible.length > 0 && visible.every(p => selected.has(p.patient_id));
  $('select-page').indeterminate = visible.some(p => selected.has(p.patient_id)) && !$('select-page').checked;
  $('select-page').disabled = visible.length === 0;
  $('select-all').disabled = rows.length === 0;
  updateSelection();
}
function updateSelection() {
  $('selection').textContent = selected.size ? num(selected.size)+' patient'+(selected.size===1?'':'s')+' selected' : 'No patients selected';
  $('selection-hint').textContent = data.context.supports_patient_work === false ? 'This summary is for analysis. Ask which patients should receive follow-up first.' : !canCreate() ? 'This answer needs updating before review work can be created.' : intent ? programNames[intent]+' · ready when you are' : 'Select patients to prepare their next step.';
  $('continue').innerHTML = (intent ? 'Continue with selection' : 'Choose next step') + ' '+icon('arrow');
  $('continue').disabled = !selected.size || !canCreate();
  $('clear').disabled = !selected.size;
}
function renderOverview() {
  const c = data.context;
  const active = data.items.filter(i => i.state !== 'cancelled');
  const assigned = new Set(active.map(i=>i.patient_id)).size;
  $('question').textContent = c.question;
  $('headline').textContent = c.answer.headline || '';
  $('snapshot').textContent = 'Snapshot · '+date(c.source.as_of);
  $('cohort-total').textContent = num(c.patient_rows.length);
  $('coverage-ring').style.setProperty('--coverage', (c.patient_rows.length ? assigned/c.patient_rows.length*100 : 0)+'%');
  $('coverage-caption').textContent = num(assigned)+' with review work · '+num(c.patient_rows.length)+' total';
  $('overview-work-caption').textContent = active.length ? num(active.length)+' review task'+(active.length===1?'':'s')+' across '+num(assigned)+' patient'+(assigned===1?'':'s')+'. Counts reflect staff review status.' : 'Pick a direction above to start your first staff review.';
  $('overview-work').innerHTML = Object.keys(stateHints).map(state => '<button class="progress-cell" data-state-view="'+state+'"><span class="progress-top"><span><i class="state-dot '+state+'"></i>'+states[state]+'</span>'+icon('arrow')+'</span><strong>'+num(data.counts[state])+'</strong><small>'+stateHints[state]+'</small></button>').join('');
  const steps=data.workflow?.steps||[], host=$('requested-workflow');
  host.hidden=!steps.length;
  host.innerHTML='<div class="section-heading"><div><h2>Your requested next steps</h2><p>The same answer, carried through.</p></div></div><div class="launch-grid">'+steps.map((s,i)=>'<article class="launch-card"><span class="eyebrow">'+esc(s.kind)+' / '+esc(pretty(s.status))+'</span><strong>'+esc(s.title)+'</strong><p>'+esc(s.reason||'')+'</p>'+
    (s.selection_summary?'<p><small>'+num(s.selection_summary.selected)+' selected for work · '+num(s.selection_summary.not_selected)+' outside the work criterion · '+num(s.selection_summary.unknown)+' unresolved</small></p>':'')+
    (s.context_id&&s.context_id!==c.id?'<a class="btn" href="/action-center?context='+esc(s.context_id)+'">Open this output’s workspace ↗</a>':'')+
    ((!s.context_id||s.context_id===c.id)&&s.kind==='deliverable'&&s.deliverable_id?'<button class="btn" data-workflow-studio>Open saved deliverable ↗</button>':'')+
    ((!s.context_id||s.context_id===c.id)&&s.kind==='deliverable'&&s.status==='blocked'&&(s.deliverable_id||s.render_error)?'<button class="btn" data-workflow-retry="'+i+'">Retry generation ↗</button>':'')+
    (s.attempts?.length?'<p><small>'+s.attempts.length+' generation attempts retained</small></p>':'')+
    ((!s.context_id||s.context_id===c.id)&&s.kind==='simulation'&&s.status==='ready'?'<button class="btn" data-workflow-board>Run simulated agents ↗</button>':'')+
    (s.kind==='monitor'&&s.mode==='work'&&s.observations?.length?'<p><strong>'+num(s.observations.at(-1).measurement.completed)+' / '+num(s.observations.at(-1).measurement.total)+' simulated journeys complete</strong><br>Simulated agent journeys needing attention: '+num(s.observations.at(-1).measurement.needs_attention)+'</p>':'')+
    (s.kind==='monitor'&&s.observations?'<p><small>'+esc(s.scope_label||s.scope)+' · Manual checks · '+s.observations.length+' recorded observation(s)</small></p><details><summary>Baseline &amp; observations</summary><pre>'+esc(JSON.stringify(s.observations.map(o=>({at:o.at,as_of:o.source.as_of,answer:o.measurement})),null,2))+'</pre></details>'+((!s.context_id||s.context_id===c.id)?'<button class="btn" data-monitor-check="'+i+'">'+(s.mode==='work'?'Check work progress':'Check current snapshot')+' ↗</button>':''):'')+'</article>').join('')+'</div>';
  host.querySelectorAll('[data-workflow-studio]').forEach(b=>b.onclick=()=>go('studio'));
  host.querySelectorAll('[data-workflow-board]').forEach(b=>b.onclick=()=>go('work'));
  host.querySelectorAll('[data-workflow-retry]').forEach(b=>b.onclick=()=>run(async()=>{
    const index=Number(b.dataset.workflowRetry);b.disabled=true;b.textContent='Generating…';
    try {
      await api('/api/stack/action-workflow/retry',{context_id:c.id,index,attempt:(steps[index].attempts?.length||1)+1});
      await refresh();await loadDeliverables();notify('Generation attempt saved. Earlier attempts and costs are retained.');
    } finally { if(b.isConnected){b.disabled=false;b.textContent='Retry generation ↗';} }
  }));
  host.querySelectorAll('[data-monitor-check]').forEach(b=>b.onclick=()=>run(async()=>{const index=Number(b.dataset.monitorCheck),work=steps[index]?.mode==='work';await api('/api/stack/action-monitor',{context_id:c.id,index});await refresh();notify(work?'Simulated work progress checked.':'Monitor checked against the current warehouse.');}));
}
function taskCard(item) {
  return '<button class="task-card" data-task="'+esc(item.id)+'" aria-label="Open review for '+esc(item.patient_id)+'"><span class="task-top"><strong>'+esc(item.patient_id)+'</strong>'+icon('arrow')+'</span><span class="task-program">'+esc(programNames[item.program] || item.program)+'</span><p>'+esc(item.simulation ? 'Demo: '+pretty(item.simulation.status)+' · '+(item.simulation.next_step?pretty(item.simulation.next_step):item.simulation.follow_up||'Journey complete') : item.note || 'No notes yet. Open this review to add the next step.')+'</p><span class="task-owner"><span class="owner-avatar">'+esc(initials(item.owner))+'</span>'+esc(item.owner)+'</span></button>';
}
function showWork() {
  if (!data) return;
  const program = $('work-program').value, filter = $('work-filter').value;
  const columns = filter ? [filter] : Object.keys(stateHints);
  $('work').classList.toggle('is-filtered', !!filter);
  $('work').innerHTML = columns.map(state => {
    const items = data.items.filter(i => i.state === state && (!program || i.program === program));
    return '<section class="board-column '+state+'" aria-label="'+states[state]+'"><div class="column-header"><span><i class="state-dot '+state+'"></i>'+states[state]+'</span><span class="column-count">'+items.length+'</span></div>'+ (items.map(taskCard).join('') || '<div class="lane-empty">'+(state==='needs_review'?'Your next review starts here.':state==='in_review'?'Ready when your team is.':state==='blocked'?'Nothing waiting here.':state==='review_complete'?'Completed reviews will land here.':'No cancelled reviews.')+'</div>')+'</section>';
  }).join('');
}
function render() {
  const c = data.context;
  $('back-to-analysis').href='/ask?context='+encodeURIComponent(c.id);
  let resume = $('resume-investigation');
  if (!resume) {
    resume = document.createElement('button'); resume.id = 'resume-investigation'; resume.className = 'btn';
    $('back-to-analysis').parentElement.append(resume);
  }
  resume.hidden = !c.investigation?.draft_resume_available || !data.allow_frontier || !canChangeWork('custom');
  resume.textContent = 'Continue analysis';
  resume.onclick = () => run(async () => {
    resume.disabled = true; resume.textContent = 'Reviewing saved work…';
    try {
      const result = await api('/api/stack/investigation/resume', {context_id:c.id});
      if (!result.action_context?.id) throw Error('The analysis did not produce a saved result.');
      location.href = '/action-center?context='+encodeURIComponent(result.action_context.id);
    } finally { resume.disabled = false; resume.textContent = 'Continue analysis'; }
  });
  $('center').hidden = false;
  $('loading').hidden = true;
  $('nav-cohort').textContent = num(c.patient_rows.length);
  $('nav-work').textContent = num(data.items.filter(i=>i.state!=='cancelled').length);
  const warnings = [];
  if (c.invalidated) warnings.push('This answer uses an older or invalidated planning definition. Its history is preserved; refresh the answer before new work or demo actions.');
  if (c.stale) warnings.push('Source data has changed. This saved answer is still visible; ask again before changing work.');
  if (c.source_review_required) warnings.push('This saved answer relies on a suspended source reading. Its original findings remain inspectable; open source-evidence review or ask again before preparing program work. The answer has not been recalculated.');
  if (c.source_disposition?.status === 'unavailable') warnings.push('Source-reading status is unavailable. Restore source verification before changing work. The saved evidence remains inspectable.');
  if (c.answer.status !== 'answered' || c.answer.missing?.length) warnings.push('This answer has unresolved evidence. You can prepare source-evidence review; clinical program work requires a complete answer.');
  const gaps = c.answer.missing || [];
  const gapDetails = gaps.length ? '<details><summary>Original analytical gaps and limitations ('+gaps.length+')</summary><ul>'+gaps.map(gap=>'<li>'+esc(gap)+'</li>').join('')+'</ul></details>' : '';
  if (c.supports_patient_work === false) warnings.push('This is an analytical summary. Build a view or monitor it; ask for a specific patient cohort before preparing follow-up work.');
  const accepted = data.independent_review?.accepted === true && !c.source_review_required && c.source_disposition?.status !== 'unavailable';
  if (accepted) {
    $('scope-warning').innerHTML = '<strong>Independent review accepted by '+esc(data.independent_review.reviewer)+'.</strong> No additional clinician review of these accepted findings is requested.'+
      (warnings.length?'<details><summary>Saved machine assessment, recorded before independent review</summary><p>'+esc(warnings.join(' '))+'</p></details>':'')+gapDetails;
  } else $('scope-warning').innerHTML = esc(warnings.join(' '))+gapDetails;
  $('scope-warning').hidden = !accepted && !warnings.length && !gaps.length;
  $('studio-review-note').textContent = accepted ? 'Independent review of this saved analysis is recorded. Source gaps and action prerequisites remain explicit.' : 'Both remain synthetic and require interpretation review.';
  $('cohort-description').textContent = num(c.patient_rows.length)+' patients from the saved answer. Select who to work with, then choose their next step.';
  if (!$('program').options.length) {
    const options = Object.entries(data.programs).map(([id,p])=>'<option value="'+esc(id)+'">'+esc(p.label)+'</option>').join('');
    $('program').innerHTML = options;
    $('program').value = (c.source_review_required || c.answer.status !== 'answered' || c.answer.missing?.length) && data.programs.evidence_review ? 'evidence_review' : 'custom';
    $('work-program').insertAdjacentHTML('beforeend',options);
    $('work-filter').insertAdjacentHTML('beforeend',Object.keys(stateHints).map(s=>'<option value="'+s+'">'+states[s]+' only</option>').join(''));
  }
  $('studio-patients').textContent = num(c.patient_rows.length);
  $('studio-tasks').textContent = num(data.items.length);
  updateGeneration();
  renderOverview(); showPatients(); showWork(); showIntent();
}
async function refresh() {
  data = await api('/api/stack/action-context?context='+encodeURIComponent(key));
  render();
}
function program() {
  const p = data.programs[$('program').value];
  $('requirements').innerHTML = p.requirements.map(r => '<li>'+esc(r)+'</li>').join('');
  $('create').disabled = !canCreate($('program').value);
}
function openReview() {
  if (!selected.size || !canCreate()) return;
  $('review-selection').textContent = num(selected.size)+' selected patient'+(selected.size===1?'':'s')+' · saved evidence attached';
  if (intent) $('program').value = intent;
  program();
  $('review-error').textContent = '';
  openDialog('review-dialog');
}
function patientDetails(pid) {
  const patient = data.context.patient_rows.find(p => p.patient_id === pid);
  if (!patient) return;
  $('detail-kicker').textContent = 'PATIENT / SAVED ANSWER';
  $('detail-title').textContent = patientName(pid);
  const evidenceRows = patient.evidence_rows || [];
  const interpreted = new Set(data.context.answer.table_binding?.interpreted_columns || []);
  const details = evidenceRows.length ? evidenceRows.map((row,index) => {
    const entries = Object.entries(row), prose = entries.filter(([k])=>interpreted.has(k));
    return (prose.length ? '<h3 class="detail-section">Proposed interpretation</h3>'+detailFields(prose) : '')+
      '<h3 class="detail-section">Returned evidence'+(evidenceRows.length>1?' · '+(index+1):'')+'</h3>'+detailFields(entries.filter(([k])=>!interpreted.has(k)));
  }).join('') : '<div class="patient-empty">This answer returned a patient identifier only. Clinical details and program suitability have not been assessed.<br><br>Ask for those details before making a clinical decision.</div>';
  const tasks = data.items.filter(i=>i.patient_id===pid);
  $('detail-content').innerHTML = '<p class="muted">'+esc(pid)+' · Snapshot '+esc(date(data.context.source.as_of))+'</p><p class="muted">Answer status: '+esc(pretty(data.context.answer.status))+(data.context.answer.verification?' · '+esc(pretty(data.context.answer.verification)):'')+'</p>'+details+(tasks.length?'<h3 style="font-size:16px;margin-top:25px">Review work</h3>'+tasks.map(taskCard).join(''):'')+'<div class="sheet-actions"><button class="button primary" data-select-detail="'+esc(pid)+'">'+(selected.has(pid)?'Remove from selection':'Add to selection')+'</button></div>';
  if (!$('detail-dialog').open) openDialog('detail-dialog');
}
function editWork(id) {
  const item = data.items.find(i=>i.id===id);
  if (!item) return;
  $('detail-kicker').textContent = 'REVIEW WORK / '+(programNames[item.program] || item.program).toUpperCase();
  $('detail-title').textContent = item.patient_id;
  $('detail-content').innerHTML = '<form id="edit-work-form"><p class="muted">Keep the next step, owner, and progress together.</p><label for="task-state">Staff review status</label><select id="task-state" '+(!canChangeWork(item.program)?'disabled':'')+'>'+data.states.map(s=>'<option value="'+esc(s)+'" '+(s===item.state?'selected':'')+'>'+esc(states[s]||pretty(s))+'</option>').join('')+'</select><label for="task-owner">Who’s taking the lead?</label><input id="task-owner" required maxlength="120" value="'+esc(item.owner)+'"><label for="task-note">What’s the next step?</label><textarea id="task-note" maxlength="2000">'+esc(item.note)+'</textarea><details class="requirements-box"><summary>Review requirements</summary><ul>'+item.requirements.map(r=>'<li>'+esc(r)+'</li>').join('')+'</ul></details><button type="button" class="text-button" data-inspect="'+esc(item.patient_id)+'">Open patient evidence '+icon('arrow')+'</button><p class="cost-note">Updates the staff review status. Simulated agent outcomes remain in the journey history. No outreach, enrollment, or care delivery occurs.</p><p id="work-error" class="form-error" role="alert"></p><div class="sheet-actions"><button type="button" class="button" data-close="detail-dialog">Cancel</button><button id="save-work" class="button primary" '+(!canChangeWork(item.program)?'disabled':'')+'>Save progress '+icon('check')+'</button></div></form>';
  const sim = item.simulation;
  const demo = document.createElement('section');
  demo.className = 'requirements-box';
  demo.innerHTML = '<h3>Try the demo agent</h3><p class="muted">Scripted synthetic journey. No contact, enrollment or care occurs.</p>'+
    (sim ? '<span class="badge">Simulated · '+esc(pretty(sim.status))+'</span><ol>'+sim.receipts.map(r=>'<li><strong>'+esc(pretty(r.step))+' → '+esc(pretty(r.outcome))+'</strong><p>'+esc(r.reason)+'</p></li>').join('')+'</ol>'+(sim.follow_up?'<p class="warning">'+esc(sim.follow_up)+'</p>':'') :
      '<label for="demo-scenario">Demo scenario</label><select id="demo-scenario"><option value="happy_path">Successful journey</option><option value="records_needed">Missing records</option><option value="patient_declines">Patient declines</option></select>')+
    (!sim || sim.status==='running' ? '<button type="button" id="demo-advance" class="button primary" '+(!canChangeWork('custom')||!['needs_review','in_review'].includes(item.state)?'disabled':'')+'>'+(sim?'Simulate '+esc(pretty(sim.next_step)):'Start demo agent')+' →</button>' : '<p>Demo finished. Its history stays attached to this review.</p>');
  if (item.program !== 'evidence_review') $('detail-content').append(demo);
  if ($('demo-advance')) $('demo-advance').onclick = () => run(async () => {
    $('demo-advance').disabled = true;
    try {
      await api('/api/stack/action-simulate',{context_id:key,item_id:id,revision:item.revision,scenario:sim?.scenario||$('demo-scenario').value});
      await refresh(); editWork(id); notify('Simulated step saved. No external action occurred.');
    } finally { if ($('demo-advance')) $('demo-advance').disabled = !canChangeWork('custom') || !['needs_review','in_review'].includes(item.state); }
  });
  $('edit-work-form').onsubmit = event => {
    event.preventDefault();
    run(async () => {
      $('save-work').disabled = true;
      try {
        await api('/api/stack/action-work/update',{context_id:key,item_id:id,revision:item.revision,state:$('task-state').value,owner:$('task-owner').value,note:$('task-note').value});
        await refresh();
        closeDialog('detail-dialog');
        notify('Progress saved. Your work board is up to date.');
      } catch (error) { $('work-error').textContent = error.message; }
      finally { if ($('save-work')) $('save-work').disabled = !canChangeWork(item.program); }
    });
  };
  if (!$('detail-dialog').open) openDialog('detail-dialog');
}
async function loadDeliverables() {
  const d = await api('/api/stack/action-deliverables?context='+encodeURIComponent(key));
  $('deliverables').innerHTML = (d.deliverables||[]).map(x => '<button class="creation-card" data-open="'+esc(x.id)+'" '+(x.status!=='delivered'?'disabled':'')+'>'+icon('file')+'<strong>'+esc(x.title||x.id)+'</strong><span>'+esc(generationLabel(x))+' · '+esc(costLabel(x))+'</span><span>'+esc(pretty(x.status))+' '+(x.status==='delivered'?'· Open view →':'· Not available to open')+'</span>'+(x.failure?'<small>'+esc(x.failure)+'</small>':'')+'</button>').join('') || '<div class="empty-state">'+icon('spark')+'<h3>Your next idea belongs here.</h3><p>Build an interactive view or a team briefing from the saved answer. It will stay in this workspace.</p></div>';
}
function preview(d) {
  $('cost').textContent = generationLabel(d)+' · '+costLabel(d)+'. Local compute and review are not priced.';
  if (d.status !== 'delivered' || !d.html) {
    const details = (d.verification?.violations || []).slice(0,3).map(v=>v.detail||v.kind).join('; ');
    throw Error(d.failure || 'This view did not pass verification and was not delivered.'+(details?' '+details:''));
  }
  const frame = document.createElement('iframe');
  frame.title = d.title || 'Custom deliverable';
  frame.sandbox = 'allow-scripts';
  const exports = d.generation === 'saved_report' ? [['pdf','Download complete PDF'],['json','Download complete evidence'],['csv','Download full worklist'],['html','Download interactive report']].filter(([format]) => d.formats?.[format]?.filename === 'artifact.'+format) : [];
  const hiddenExports = exports.filter(([format])=>format!=='html').map(([format])=>format==='pdf'?'.toolbar a[download]':format==='json'?'.toolbar #json':'.toolbar #csv');
  const exportStyle = hiddenExports.length ? '<style>'+hiddenExports.join(',')+'{display:none!important}</style>' : '';
  frame.srcdoc = d.html.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; script-src &#39;unsafe-inline&#39;; style-src &#39;unsafe-inline&#39;; img-src data:; connect-src &#39;none&#39;; form-action &#39;none&#39;; base-uri &#39;none&#39;">'+exportStyle);
  $('preview').innerHTML = '<div class="preview-heading"><h2>'+esc(d.title||'Your custom view')+'</h2><button class="text-button" id="close-preview">Close preview ×</button></div>';
  if (exports.length) {
    const controls = document.createElement('div');
    controls.className = 'report-downloads';
    for (const [format,label] of exports) {
      const link = document.createElement('a');
      link.className = 'text-button'; link.textContent = label; link.download = 'Agere-report.'+format;
      link.href = '/api/stack/action-deliverable?'+new URLSearchParams({context:key,id:d.id,format});
      controls.append(link,document.createTextNode(' '));
    }
    $('preview').append(controls);
  }
  $('preview').append(frame);
  $('close-preview').onclick = () => $('preview').replaceChildren();
  $('cost').textContent += d.mockup ? ' Fixed synthetic layout.' : data.independent_review?.accepted ? ' Binding and technical presentation checks passed. Independent review of this saved analysis is recorded.' : ' This view preserves the saved analysis; unresolved items still require review.';
  if (data.context.stale || data.context.invalidated) $('cost').textContent += ' Historical artifact: the source answer is stale or invalidated. Re-run the analysis before creating new work.';
  $('preview').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
}
const presets = {
  command:'Build an interactive command center for this saved cohort. Show patient filters, the status and owner of each review, missing evidence, and clear next steps. Keep review completion distinct from enrollment or care delivered. Use only the attached answer and saved work.',
  brief:'Create a clear care-team briefing from this saved answer and review work. Summarize what we know, what is missing, who owns each next step, and which reviews need attention. Include patient-level evidence where it was returned; do not invent clinical details or eligibility.',
  explore:'Build an interactive evidence explorer for this exact saved answer. Let the team search patients, inspect the returned details, and see unknowns alongside existing review work. Make the source snapshot visible. Do not infer eligibility or outcomes.'
};
document.addEventListener('click', event => {
  const close = event.target.closest('[data-close]'); if (close) { closeDialog(close.dataset.close); return; }
  const nav = event.target.closest('[data-view]'); if (nav) { go(nav.dataset.view); return; }
  const startButton = event.target.closest('[data-start]'); if (startButton) { start(startButton.dataset.start); return; }
  const state = event.target.closest('[data-state-view]'); if (state) { $('work-filter').value=state.dataset.stateView; go('work'); return; }
  const patient = event.target.closest('[data-inspect]'); if (patient) { patientDetails(patient.dataset.inspect); return; }
  const task = event.target.closest('[data-task]'); if (task) { editWork(task.dataset.task); return; }
  const selectDetail = event.target.closest('[data-select-detail]'); if (selectDetail) {
    const pid=selectDetail.dataset.selectDetail; selected.has(pid)?selected.delete(pid):selected.add(pid);
    closeDialog('detail-dialog'); go('cohort'); return;
  }
  const preset = event.target.closest('[data-preset]'); if (preset) {
    selectedTemplate = preset.dataset.preset;
    $('brief').value = presets[selectedTemplate];
    document.querySelectorAll('[data-preset]').forEach(b=>b.setAttribute('aria-pressed',String(b===preset)));
    $('brief').focus(); return;
  }
  const saved = event.target.closest('[data-open]'); if (saved) run(async () => preview(await api('/api/stack/action-deliverable?context='+encodeURIComponent(key)+'&id='+encodeURIComponent(saved.dataset.open))));
});
$('show-evidence').onclick = evidence;
$('cohort-evidence').onclick = evidence;
$('explore-cohort').onclick = () => go('cohort');
$('search').oninput = () => { pageIndex=0; showPatients(); };
$('cohort-filter').onchange = () => { pageIndex=0; showPatients(); };
$('patients').onchange = event => { const pid=event.target.dataset.patient; if (!pid) return; event.target.checked?selected.add(pid):selected.delete(pid); showPatients(); };
$('select-page').onchange = event => { const checked=event.target.checked; filteredPatients().slice(pageIndex*PAGE_SIZE,(pageIndex+1)*PAGE_SIZE).forEach(p=>checked?selected.add(p.patient_id):selected.delete(p.patient_id)); showPatients(); };
$('select-all').onclick = () => { filteredPatients().forEach(p=>selected.add(p.patient_id)); showPatients(); };
$('previous-page').onclick = () => { pageIndex--; showPatients(); };
$('next-page').onclick = () => { pageIndex++; showPatients(); };
$('clear').onclick = () => { selected.clear(); showPatients(); };
$('cancel-intent').onclick = () => {
  intent=null;
  if (data.context.answer.status !== 'answered' || data.context.answer.missing?.length) $('program').value='evidence_review';
  showIntent(); updateSelection();
};
$('continue').onclick = openReview;
$('program').onchange = program;
$('work-filter').onchange = showWork;
$('work-program').onchange = showWork;
$('refresh').onclick = () => run(async () => { await refresh(); notify('Work board refreshed.'); });
$('review-form').onsubmit = event => {
  event.preventDefault();
  run(async () => {
    $('create').disabled = true;
    $('review-error').textContent = '';
    try {
      const ids = [...selected];
      await api('/api/stack/action-work/create',{context_id:key,program:$('program').value,patient_ids:ids,owner:$('owner').value,note:$('note').value});
      selected.clear(); intent=null;
      await refresh();
      closeDialog('review-dialog');
      $('work-filter').value=''; $('work-program').value='';
      go('work');
      notify('Review work saved for your selection. Existing progress was preserved.');
    } catch (error) { $('review-error').textContent = error.message; }
    finally { $('create').disabled = !canCreate($('program').value); }
  });
};
$('compose').onclick = () => run(async () => {
  if (!$('brief').value.trim()) $('brief').value = presets[selectedTemplate];
  $('compose').disabled = true;
  $('build-progress').hidden = false;
  $('compose-error').hidden = true;
  const frontier = $('generation').value === 'frontier';
  $('generation-progress').textContent = frontier ? 'Designing from your saved evidence, then checking data bindings and numbers. This may take a few minutes.' : 'Building a local template from your saved evidence.';
  try {
    const request = {context_id:key,request:$('brief').value};
    if (!frontier) request.template = selectedTemplate;
    const d = await api(frontier ? '/api/stack/action-deliverable' : '/api/stack/action-mockup', request);
    await loadDeliverables(); preview(d); notify('Your view is ready and saved in this workspace.');
  } catch (error) {
    $('compose-error').textContent = error.message;
    $('compose-error').hidden = false;
    throw error;
  } finally { updateGeneration(); $('build-progress').hidden = true; }
});
$('generation').onchange = updateGeneration;
$('export').onclick = () => {
  const cols=['patient_id','program','owner','state','note','revision'];
  const cell=value=>{let text=String(value??'');if(/^[=+@\-\t\r]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';};
  const csv=[cols,...data.items.map(i=>cols.map(k=>i[k]))].map(row=>row.map(cell).join(',')).join('\r\n');
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'})), link=document.createElement('a');
  link.href=url;link.download='agere-review-work.csv';link.click();URL.revokeObjectURL(url);
};
async function init() {
  hydrateIcons();
  try {
    if (key) { await refresh(); go(viewNames[location.hash.slice(1)]?location.hash.slice(1):'overview',false); await loadDeliverables(); return; }
    const result = await api('/api/stack/action-contexts');
    $('saved').hidden=false; $('loading').hidden=true;
    document.querySelector('.workspace-nav').hidden=true;document.querySelector('.side-caption').hidden=true;
    $('view-name').textContent='Saved workspaces';
    $('saved').innerHTML='<div class="section-kicker">SYNTHETIC WORK &amp; DELIVERABLES</div><div class="page-heading"><div><h1>Action workspaces<span class="copper">.</span></h1><p>Choose a saved answer to explore its cohort, review work, and simulated activity.</p></div></div><div class="saved-grid">'+((result.contexts||[]).map(c=>'<a class="saved-card" href="/action-center?context='+encodeURIComponent(c.id)+'"><h2>'+esc(c.question)+'</h2><p>'+num(c.patients)+' patients · '+esc(date(c.created_at))+icon('arrow')+'</p></a>').join('')||'<div class="empty-state"><h3>Start with a question.</h3><p>Your saved answer will become a workspace for exploring evidence and preparing next steps.</p><a class="button primary" href="/ask">Ask Agere →</a></div>')+'</div>';
  } catch (error) {
    $('loading').hidden=true;
    $('saved').hidden=false;
    $('saved').innerHTML='<div class="empty-state"><h2>This workspace couldn’t open.</h2><p>'+esc(error.message)+'</p><a class="button" href="/action-center">Back to saved workspaces</a></div>';
    notify(error.message,true);
  }
}
init();
