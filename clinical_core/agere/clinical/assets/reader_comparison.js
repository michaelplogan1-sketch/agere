// Shared by exact source controls in the question, graph and learning workspaces.
function addReaderRoleCoverage(box,cohortId){
 const check=N('button','Check labels these classifiers can produce','chip'),out=N('section');box.append(check,out);
 check.onclick=async()=>{check.disabled=true;try{const r=await semanticFetch('/api/stack/reader-comparison/coverage',{cohort_id:cohortId});out.replaceChildren(N('h4','Classifier coverage limits'));
  for(const [role,v] of Object.entries(r.roles)){out.append(N('p',role.replaceAll('_',' ')+': '+(v.full_label_contract?'all contract labels represented':'some contract labels unavailable')+'; '+(v.selective_threshold===null?'no qualified selective threshold':'development threshold only')+'.','missing'));for(const [axis,labels]of Object.entries(v.unavailable_labels))if(labels.length)out.append(N('p',axis+' cannot return: '+labels.map(x=>x.replaceAll('_',' ')).join(', '),'meta'))}
  out.append(N('p',r.routing_rule,'missing'),N('p',r.native_roles,'meta'),details('Verified class sets and model identities',r));
 }catch(e){out.replaceChildren(N('p',e.message,'missing'))}finally{check.disabled=false}};
}
function renderReaderComparison(box,r){
 const panel=N('article',undefined,'finding-card'),link=N('a','Reopen this saved reader comparison');link.href='/stack?reader-comparison='+encodeURIComponent(r.id);
 panel.append(N('h3','How did the local readers interpret this passage?'),N('p',r.request.target.patient_id+' · '+r.request.target.concept),N('blockquote',r.context.text),N('p','Exact target: '+r.context.target_quote,'meta'),link,N('p','Same received passage and target. Cohort classifiers use text; Kev and d1 also see the supplied source dates. Agreement does not establish correctness.','missing'));
 if(typeof openEvidenceThread==='function'){const source=N('button','Inspect original source evidence','chip');source.onclick=()=>openEvidenceThread(r.request.target.patient_id,r.request.target.record_id);panel.append(source)}
 const dates=N('button','Inspect dates inside this target','chip'),dateOut=N('section');panel.append(dates,dateOut);
 dates.onclick=async()=>{dates.disabled=true;try{const d=await semanticFetch('/api/stack/reader-comparison/dates',{target:r.request.target});if(d.source_hash!==r.source_hash||d.context_digest!==r.context_digest)throw new Error('Source context changed; reload the comparison.');dateOut.replaceChildren(N('h4','Literal dates in the marked target'),N('p',d.scope,'meta'));
  for(const item of d.dates)dateOut.append(N('p',item.date+' · '+item.relative_to_source.replaceAll('_',' ')+(item.days_relative_to_source===null?'':' ('+item.days_relative_to_source+' days)')+' · exact '+item.field+' characters '+item.start+'–'+item.end+'.'));
  if(!d.dates.length)dateOut.append(N('p','No full ISO date found inside this target. Temporal evidence elsewhere remains unassessed.','meta'));
  for(const item of d.invalid_literals)dateOut.append(N('p',item.quote+': invalid calendar date.','missing'));
  dateOut.append(N('p','Source date: '+(d.source_date||'unknown')+' · legacy packet reference date: '+(d.legacy_packet_reference_date||'not extracted')+'. Literal dates do not automatically establish event time or change model labels.','missing'),details('Exact date audit and limits',d));
 }catch(e){dateOut.replaceChildren(N('p',e.message,'missing'))}finally{dates.disabled=false}};
 const table=N('table');table.setAttribute('aria-label','Local statement reader comparison');table.style.width='100%';const head=N('tr');for(const title of ['Reader','Statement','Time','Relation'])head.append(N('th',title));table.append(head);
 for(const [role,v] of Object.entries({...r.baselines,...r.variants})){const row=N('tr');row.append(N('th',role.replaceAll('_',' ')));for(const axis of ['statement','time','relation'])row.append(N('td',v.labels?.[axis]?.replaceAll('_',' ')||(v.status==='failed'?'Failed':'Unavailable')));table.append(row)}
 const scroll=N('div');scroll.style.overflowX='auto';scroll.append(table);panel.append(scroll);
 for(const [axis,a] of Object.entries(r.stage_assessment.axes))panel.append(N('p',axis+': '+(a.disagreement?'readers disagree':'no disagreement among completed readers')+' · '+a.abstaining_roles.length+' abstaining reader(s).','meta'));
 for(const [role,v] of Object.entries(r.variants)){
  if(v.status!=='completed'){panel.append(N('p',role+': '+v.reason,'missing'));continue}
  const open=N('button','Open '+role.replaceAll('_',' ')+' interpretation','chip'),out=N('section');
  open.onclick=async()=>{open.disabled=true;try{const reading=await semanticFetch('/api/stack/semantic?id='+encodeURIComponent(v.reading_id));out.replaceChildren();renderSemantic(out,{...reading,cache_hit:v.cache_hit,new_paid_api_calls:0})}catch(e){out.replaceChildren(N('p',e.message,'missing'))}finally{open.disabled=false}};panel.append(open,out);
 }
 const overlap=Object.entries(r.training_overlap).filter(([_,o])=>o.same_patient||o.same_source||o.same_model_text);
 addReaderRoleCoverage(panel,r.request.cohort_id);
 panel.append(N('p',overlap.length?'Overlap with the cohort development data is present: '+overlap.map(([p,o])=>p+' ('+o.same_patient+' same-patient examples)').join('; ')+'. This is not independent evaluation.':'No same-patient, exact-record or exact-input overlap found in the cohort data. Near-duplicates and foundation-model exposure remain unassessed.','missing'));
 panel.append(N('p','Machine-supervised classifiers remain in shadow mode. Retrieval, planning, service matching and clinical accuracy are unassessed here. No review, work or action was created.','meta'),details('Exact model identities, role boundaries, usage and source receipt',r));box.replaceChildren(panel);
}
function addReaderComparisonControl(box,target,provider){
 if(typeof addDecisionStatementReview==='function')addDecisionStatementReview(box,target);
 const configure=N('button','Compare local readers on this passage','chip'),area=N('section');box.append(configure,area);
 configure.onclick=async()=>{configure.disabled=true;area.replaceChildren(N('p','Checking current model attachments…','meta'));try{
  const status=await semanticFetch('/api/stack/reader-comparison/availability',{});area.replaceChildren();
  if(!status.global_students_attached){for(const o of provider.options)if(o.value.startsWith('student_'))o.disabled=true;area.append(N('p','The older global student models are not attached to this warehouse. Current cohort models are available below.','meta'))}
  const available=status.cohorts.filter(c=>c.status==='available');
  if(!available.length){area.append(N('p','No current cohort classifier pair is available.','missing'),details('Model status',status));return}
  const label=N('label','Cohort models '),select=N('select');select.setAttribute('aria-label','Reader comparison cohort');for(const c of available)select.add(new Option('Current cohort '+c.cohort_id.slice(0,12),c.cohort_id));label.append(select);
  const nativeLabel=N('label',' Include pinned Kev and d1 experiments (slower)'),native=N('input');native.type='checkbox';native.setAttribute('aria-label','Include Kev and d1 in reader comparison');nativeLabel.prepend(native);
  const run=N('button','Run same-passage comparison'),out=N('section'),coverage=N('section');area.append(label,nativeLabel,run,out,N('p','Local inference only. Existing exact readings are reused. These are shadow proposals; inspect missing output classes and calibration before interpreting agreement.','missing'),coverage);
  function showCoverage(){coverage.replaceChildren();addReaderRoleCoverage(coverage,select.value)}select.onchange=showCoverage;showCoverage();
  run.onclick=async()=>{run.disabled=true;out.replaceChildren(N('p','Comparing the exact passage; native models may take longer to load…','meta'));try{const roles=['cohort_lexical','cohort_windowed_minilm'];if(native.checked)roles.push('kev','d1');const data=await semanticFetch('/api/stack/reader-comparison/prepare',{target,cohort_id:select.value,roles});renderReaderComparison(out,data.comparison)}catch(e){out.replaceChildren(N('p',e.message,'missing'))}finally{run.disabled=false}};
 }catch(e){area.replaceChildren(N('p',e.message,'missing'))}finally{configure.disabled=false}};
}
document.addEventListener('DOMContentLoaded',()=>{
 const id=new URLSearchParams(location.search).get('reader-comparison');if(!id||typeof workspaceView!=='function')return;
 savedCaseRestoreAllowed=false;workspaceView('investigate');$('answer-area').hidden=false;
 semanticFetch('/api/stack/reader-comparison/inspect',{id}).then(r=>renderReaderComparison($('results'),r)).catch(e=>$('results').replaceChildren(N('p',e.message,'missing')));
});
