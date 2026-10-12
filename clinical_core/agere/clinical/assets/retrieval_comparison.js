// Compare model roles against the exact displayed question and scope.
const retrievalBeforeComparison=retrieval;
retrieval=function(d){
 retrievalBeforeComparison(d);
 if(!d.comparison_request)return;
 const article=$('results').lastElementChild,button=N('button','Compare retrieval with and without reranking'),out=N('section');
 article.append(button,out);
 button.onclick=async()=>{button.disabled=true;out.replaceChildren(N('p','Comparing the same local candidates…','meta'));try{
  const response=await workspaceFetch('/api/stack/retrieval-comparison/prepare',d.comparison_request);
  renderRetrievalComparison(out,response.comparison);
 }catch(e){out.replaceChildren(N('p',e.message,'missing'))}finally{button.disabled=false}};
};
function renderRetrievalComparison(out,r){
  const link=N('a','Reopen this saved comparison');link.href='/stack?retrieval-comparison='+encodeURIComponent(r.id);
  out.replaceChildren(N('h3','What changed after reranking?'),N('p',r.request.question),N('p',r.candidates.length+' identical candidates · '+r.changes.entered_selected.length+' entered the selected evidence · '+r.changes.left_selected.length+' left it.','meta'),N('p','These are ranking differences, not measured accuracy gains. Clinical meaning, completeness and model superiority remain unassessed.','missing'),link);
  if(r.request.patient){const inventory=N('button','Inspect received categories outside this selection'),coverage=N('section');out.append(inventory,coverage);
   inventory.onclick=async()=>{inventory.disabled=true;try{const c=await workspaceFetch('/api/stack/retrieval-comparison/coverage',{id:r.id});coverage.replaceChildren(N('h4','What else was received?'),N('p',c.received_records+' records in '+c.categories.length+' categories; '+c.categories_outside_candidates+' categories are absent from the candidate set.','meta'),N('p',c.inventory_scope+' Category presence does not establish relevance; these counts do not measure recall.','missing'));
    const select=careSelect(coverage,'Received category to inspect',c.categories.map(x=>[x.source_category,human(x.source_category)+' · '+x.received_records+' received / '+x.candidates+' candidate / '+x.reranked_selected+' selected records']),c.categories[0]?.source_category||''),open=N('button','Open received category');
    open.onclick=async()=>{$('atlas-patient').value=c.patient_id;workspaceView('atlas');await loadAtlas();await openSource(select.value,0)};
    coverage.append(open,details('Exact category counts and scope limits',c));
    addRetrievalSourcePlan(coverage,r,select);
   }catch(e){coverage.replaceChildren(N('p',e.message,'missing'))}finally{inventory.disabled=false}};
  }
  const rows=r.candidates.filter(c=>c.rrf_selected_rank||c.reranked_selected_rank).sort((a,b)=>(a.reranked_selected_rank||99)-(b.reranked_selected_rank||99));
  for(const c of rows){const box=N('article',undefined,'finding-card'),s=c.source,open=N('button','Inspect this source');
   box.append(N('h4',s.patient_id+' · '+s.source_table+' / '+s.field),N('p','Before reranking: '+(c.rrf_selected_rank||'not selected')+' · After reranking: '+(c.reranked_selected_rank||'not selected'),'pill'),N('blockquote',c.text));
   open.onclick=()=>openEvidenceThread(s.patient_id,s.record_id);box.append(open,details('Exact source and both rank positions',c));out.append(box);
  }
  if(!rows.length)out.append(N('p','No candidates were selected. This does not establish absence of care.'));
  out.append(N('p','Reader, classifier, planner and service matching were not run in this comparison.','meta'),details('Saved paired comparison and role boundaries',r));
}
const retrievalComparisonId=new URLSearchParams(location.search).get('retrieval-comparison');
if(retrievalComparisonId){savedCaseRestoreAllowed=false;workspaceView('investigate');workspaceFetch('/api/stack/retrieval-comparison/inspect',{id:retrievalComparisonId}).then(r=>{renderRetrievalComparison($('results'),r);$('answer-area').hidden=false}).catch(e=>{$('results').replaceChildren(N('p',e.message,'missing'));$('answer-area').hidden=false})}

function addRetrievalSourcePlan(box,comparison,category,initialCategories=[]){
 const area=N('section',undefined,'finding-card'),selected=new Set(initialCategories),chips=N('div'),output=N('div');let revision=0;
 area.append(N('h4','Bring received categories into this question'),N('p','Choose categories to include alongside the selected retrieval records. The patient and date window stay bound to this saved question. Whole records will be prepared for review; relevance and clinical completeness remain unassessed.','meta'));
 const baseline=careSelect(area,'Retrieval selection to retain',[['bge_reranked','After reranking'],['rrf_baseline','Before reranking']],'bge_reranked'),add=N('button','Add category to source plan'),preview=N('button','Preview source plan');
 function refresh(){revision++;output.replaceChildren();chips.replaceChildren();for(const c of selected){const b=N('button','Remove '+human(c),'chip');b.onclick=()=>{selected.delete(c);refresh()};chips.append(b)}if(!selected.size)chips.append(N('p','No category additions yet. You can preview the baseline records alone.','meta'))}
 add.onclick=()=>{if(category.value)selected.add(category.value);refresh()};baseline.onchange=refresh;
 preview.onclick=async()=>{preview.disabled=true;const current=revision,request={comparison_id:comparison.id,baseline:baseline.value,categories:[...selected].sort()};output.replaceChildren(N('p','Checking the exact received records and date scope…','meta'));try{
  const p=await workspaceFetch('/api/stack/retrieval-source-plan/preview',request);if(current!==revision)return;renderRetrievalSourcePreview(output,p);
  const prepare=N('button','Prepare these records for review'),prepared=N('section');prepare.disabled=!p.can_prepare;output.append(prepare,prepared);
  prepare.onclick=async()=>{prepare.disabled=true;try{const result=await workspaceFetch('/api/stack/retrieval-source-plan/prepare',{request:p.request,preview_id:p.id});renderRetrievalSourcePlan(prepared,result)}catch(e){prepared.replaceChildren(N('p',e.message,'missing'))}finally{prepare.disabled=!p.can_prepare}};
 }catch(e){if(current===revision)output.replaceChildren(N('p',e.message,'missing'))}finally{preview.disabled=false}};
 area.append(add,chips,preview,output);box.append(area);refresh();
}
function renderRetrievalSourcePreview(box,p){
 if(p.schema==='decision-reanswer-plan-scope/1'){
  box.replaceChildren(N('h4','Current evidence for the saved question'),N('p',p.patient_id+' · '+p.original_question),N('p',p.selected_record_ids.length+' selected records · '+p.excluded_records+' outside this selection · '+p.date_exclusions.length+' excluded or unknown dates','meta'),N('p',p.review_question,'scope-notice'),details('Original window, changes and unresolved references',p));return;
 }
 box.replaceChildren(N('h4','Declared evidence scope'),N('p',p.patient_id+' · '+p.original_question),N('p',p.baseline_record_ids.length+' baseline records + '+p.added_record_ids.length+' added records = '+p.selected_record_ids.length+' selected; '+p.excluded_records+' received records remain outside this answer.','meta'),N('p',p.representation,'scope-notice'));
 for(const c of p.requested_categories)box.append(N('p',human(c.source_category)+': '+c.eligible_records+' eligible / '+c.received_records+' received · '+c.outside_window_records+' outside the date window · '+c.unknown_date_records+' with unknown dates.'));
 box.append(N('p',p.review_question,'meta'));
 for(const b of p.blockers)box.append(N('p',b,'missing'));
 box.append(details('Exact selected records, selection reasons and exclusions',p));
}
function renderRetrievalSourcePlan(box,result,onComplete=null){
 const panel=N('article',undefined,'source-plan-panel');box.replaceChildren(panel);box=panel;
 const p=result.plan,link=N('a','Reopen this saved source plan');link.href='/stack?retrieval-source-plan='+encodeURIComponent(p.id);
 renderRetrievalSourcePreview(box,p.preview);box.append(link,N('p',p.groups.length+' explicit review group'+(p.groups.length===1?'':'s')+' cover all '+p.preview.selected_record_ids.length+' selected records. No model was called to prepare them.','meta'));
 const savedAnswers=N('section');box.append(savedAnswers);const refreshAnswers=()=>loadSourcePlanAnswers(savedAnswers,p.id,onComplete);refreshAnswers();
 if(p.groups.length>1)box.append(N('p','Group answers remain separate until you select them for a combined excerpt review. Source coverage and clinical correctness are separate.','scope-notice'));
 for(const [i,c] of result.contexts.entries()){const g=p.groups[i],part=N('section',undefined,'finding-card');part.append(N('h4','Review group '+g.group_number+' · '+g.record_ids.length+' records'),N('p',c.budget.selected_units+' / '+c.budget.candidate_units+' source units fit · '+c.budget.used+' / '+c.budget.maximum+' bytes · '+c.budget.omitted_units+' omitted units.','meta'));
  if(g.request_fit_error)part.append(N('p',g.request_fit_error,'missing'));
  if(g.structured_transport_applied)part.append(N('p','Large structured units were partitioned by exact field paths with lossless reconstruction checked. Fragments remain parts of the same received record.','scope-notice'));
  if(g.reference_tables_applied)part.append(N('p','Repeated source relationships use reversible reference tables. Every relationship and original source unit is preserved; historical requests retain their original format.','meta'));
  part.append(details('Exact group records and bounded context',{group:g,context:c}));box.append(part);addCaseSynthesis(part,c,async answer=>{await workspaceFetch('/api/stack/source-plan-answers/attach',{plan_id:p.id,answer_id:answer.id});await refreshAnswers()});
 }
 box.append(details('Saved source plan and all review groups',result));
}
const retrievalSourcePlanId=new URLSearchParams(location.search).get('retrieval-source-plan');
if(retrievalSourcePlanId){savedCaseRestoreAllowed=false;workspaceView('investigate');workspaceFetch('/api/stack/retrieval-source-plan/inspect',{id:retrievalSourcePlanId}).then(r=>{renderRetrievalSourcePlan($('results'),r);$('answer-area').hidden=false}).catch(e=>{$('results').replaceChildren(N('p',e.message,'missing'));$('answer-area').hidden=false})}

async function loadSourcePlanAnswers(box,planId,onComplete=null){
 box.replaceChildren(N('p','Loading saved group answers…','meta'));
 try{const r=await workspaceFetch('/api/stack/source-plan-answers/listing',{plan_id:planId});
 box.replaceChildren(N('h3','Answers attached to the original question'),N('p',r.answered_groups+' / '+r.total_groups+' groups have saved answers. Missing groups remain unresolved.','scope-notice'));
 const choices=[],output=N('section');
 for(const g of r.groups){const row=N('section'),select=N('select');select.setAttribute('aria-label','Saved answer for group '+g.group_number);row.append(N('h4','Group '+g.group_number+' · '+human(g.status)));
  if(g.attachments.length!==1)select.add(new Option('Select a saved answer',''));
  for(const a of g.attachments)select.add(new Option(a.answer_id.slice(0,12)+' · '+a.source_units+' source units · '+a.model_unresolved_units+' unresolved',a.id));
  select.disabled=!g.attachments.length;choices.push(select);row.append(select);
  if(g.attachments.length)row.append(details('Verified group attachments',g.attachments));box.append(row);
 }
 const save=N('button','Save these group answers together','primary');save.disabled=choices.some(s=>!s.value);choices.forEach(s=>s.onchange=()=>{save.disabled=choices.some(c=>!c.value)});
 save.onclick=async()=>{save.disabled=true;try{const snapshot=await workspaceFetch('/api/stack/source-plan-answers/prepare',{plan_id:planId,attachment_ids:choices.map(s=>s.value)});renderSourcePlanAnswerSnapshot(output,snapshot,onComplete)}catch(e){output.replaceChildren(N('p',e.message,'missing'))}finally{save.disabled=choices.some(s=>!s.value)}};
 box.append(save);for(const s of r.snapshots){const a=N('a','Reopen saved answer selection '+s.id.slice(0,12));a.href='/stack?source-plan-answer='+s.id;if(onComplete)a.onclick=async e=>{e.preventDefault();try{renderSourcePlanAnswerSnapshot(output,await workspaceFetch('/api/stack/source-plan-answers/inspect',{id:s.id}),onComplete)}catch(err){output.replaceChildren(N('p',err.message,'missing'))}};const line=N('p');line.append(a);box.append(line)}box.append(output);
 }catch(e){box.replaceChildren(N('p',e.message,'missing'))}
}

function renderSourcePlanAnswerSnapshot(box,r,onComplete=null){
 const s=r.snapshot,link=N('a','Reopen this saved answer selection');link.href='/stack?source-plan-answer='+s.id;
 box.replaceChildren(N('h2',s.original_question),N('p',s.patient_id+' · '+s.groups.length+' saved group answers · '+s.selected_records+' selected records · '+s.source_units+' accounted units','meta'),link,
  N('p',s.excluded_records+' received records remain outside this plan. '+s.model_unresolved_units+' submitted units remain unresolved by the model. Neither group completion nor an excerpt review establishes clinical correctness.','scope-notice'));
 const parent=N('a','Return to the source plan');parent.href='/stack?retrieval-source-plan='+s.plan_id;const parentLine=N('p');parentLine.append(parent);box.append(parentLine);
 const continueButton=N('button','Continue this complete question','primary'),completeStatus=N('p','Use every saved group answer in the shared decision flow. Unassessed relationships and exclusions remain visible. No new model call.','meta');box.append(continueButton,completeStatus);
 continueButton.onclick=async()=>{continueButton.disabled=true;try{const answer=await workspaceFetch('/api/stack/source-plan-answers/compose',{snapshot_id:s.id});if(onComplete)await onComplete(answer);else location.href='/stack?case='+answer.id}catch(e){completeStatus.textContent=e.message;continueButton.disabled=false}};
 if(s.groups.length>1){const review=N('section');box.append(review);addSourcePlanReconciliation(review,s.id)}
 for(const [i,a] of r.answers.entries()){const group=N('details'),content=N('section');group.append(N('summary','Group '+s.groups[i].group_number+' · saved source interpretation'),content);box.append(group);renderCaseSynthesis(content,a)}
 box.append(details('Immutable answer selection and coverage',s));
}

function addSourcePlanReconciliation(box,snapshotId){
 const button=N('button','Review relationships across groups · Claude API','primary'),status=N('p','Checking all saved claims and exact excerpts…','meta'),output=N('section');button.disabled=true;
 box.append(N('h3','Across the review groups'),N('p','Every original source-pass claim and its cited excerpts are reviewed together. Uncited text and prior group conclusions remain in the original answers. This creates a machine proposal.','scope-notice'),button,status,output);
 workspaceFetch('/api/stack/source-plan-reconciliation/preview',{snapshot_id:snapshotId}).then(p=>{status.textContent=p.input_claims+' original claims · '+p.cited_excerpts+' exact excerpts · '+p.request_bytes+' request bytes · at most one new inference call.';box.insertBefore(details('Cross-group review scope',p),button);button.disabled=!!p.saved_failure;if(p.saved_failure){status.textContent+=' Saved review failed validation: '+p.saved_failure.error+'. It will not be resent.';addSourcePlanRecovery(output,snapshotId)}if(p.saved_answer_available){button.textContent='Reopen saved cross-group review';workspaceFetch('/api/stack/source-plan-reconciliation/inspect',{id:p.id}).then(r=>renderSourcePlanReconciliation(output,r)).catch(e=>status.textContent=e.message)}}).catch(e=>status.textContent='Cross-group review remains unresolved: '+e.message);
 button.onclick=async()=>{button.disabled=true;status.textContent='Reviewing saved claims against their exact source excerpts…';try{const r=await workspaceFetch('/api/stack/source-plan-reconciliation/reconcile',{snapshot_id:snapshotId});renderSourcePlanReconciliation(output,r);status.textContent=r.cache_hit?'Reopened the saved review without new inference.':'Saved a cross-group machine review; clinical correctness remains unvalidated.'}catch(e){status.textContent=e.message;button.disabled=true;addSourcePlanRecovery(output,snapshotId)}finally{if(output.querySelector('h3'))button.disabled=false}};
}

function renderSourcePlanReconciliation(box,r){
 box.replaceChildren(N('h3','Saved cross-group interpretation'),N('p',r.coverage.accounted_claims+' / '+r.coverage.input_claims+' source-pass claims accounted for. This does not resolve every clinical question or expand source coverage.','scope-notice'));
 if(r.quarantined_claim_reviews?.length)box.append(N('p',r.quarantined_claim_reviews.length+' claim review remains system-unassessed because its model explanation used an ambiguous source reference. No reference was guessed. The failed response is preserved; recovery made no new provider call.','missing'),details('Original quarantined claim reviews',r.quarantined_claim_reviews));
 for(const f of r.result.findings){const card=N('article',undefined,'finding-card');card.append(N('span',human(f.kind),'pill'),N('p',f.text));for(const c of f.citations){const b=N('button','Inspect cited source ↗','chip');b.onclick=()=>openEvidenceThread(r.patient_id,c.source.record_id);card.append(N('blockquote',c.quote),b)}for(const text of f.unresolved)card.append(N('p',text,'missing'));box.append(card)}
 for(const q of r.result.unanswered)box.append(N('p',q,'missing'));
 const questions=N('details');questions.append(N('summary','Proposed evidence-review questions · no task assigned'));for(const q of r.result.review_steps)questions.append(N('p',q.question));box.append(questions,details('Every original claim, reference identity and usage receipt',r));
}

const sourcePlanAnswerId=new URLSearchParams(location.search).get('source-plan-answer');
if(sourcePlanAnswerId){savedCaseRestoreAllowed=false;workspaceView('investigate');workspaceFetch('/api/stack/source-plan-answers/inspect',{id:sourcePlanAnswerId}).then(r=>{renderSourcePlanAnswerSnapshot($('results'),r);$('answer-area').hidden=false}).catch(e=>{$('results').replaceChildren(N('p',e.message,'missing'));$('answer-area').hidden=false})}

async function addSourcePlanRecovery(box,snapshotId){
 try{const p=await workspaceFetch('/api/stack/source-plan-recovery/preview',{snapshot_id:snapshotId});
  const b=N('button',p.saved_answer_available?'Reopen locally recovered review':'Recover valid content from saved response'),status=N('p',p.quarantined_claim_reviews+' ambiguous claim review will remain unresolved. No new API call.','scope-notice'),output=N('section');box.append(b,status,output);
  const run=async()=>{b.disabled=true;try{const r=await workspaceFetch('/api/stack/source-plan-recovery/recover',{snapshot_id:snapshotId});renderSourcePlanReconciliation(output,r);status.textContent='Recovered valid content locally; '+r.coverage.system_unassessed_claim_reviews+' claim review remains unresolved.'}catch(e){status.textContent=e.message}finally{b.disabled=false}};b.onclick=run;if(p.saved_answer_available)await run();
 }catch(e){box.append(N('p','Automatic recovery unavailable: '+e.message,'missing'))}
}
