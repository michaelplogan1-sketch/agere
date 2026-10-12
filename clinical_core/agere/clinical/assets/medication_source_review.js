// Keep original documentary results; open complete received medication accounts.
const renderPortfolioBeforeMedicationReview=renderEvidencePortfolio;
renderEvidencePortfolio=function(box,portfolio){renderPortfolioBeforeMedicationReview(box,portfolio);addMedicationSourceReview(box,portfolio.id)};
function addMedicationSourceReview(box,portfolioId){
 const area=N('details'),body=N('div');area.append(N('summary','Review received medication evidence and pharmacy options'),body);box.append(area);let loaded=false;
 function accounts(parent,rows){for(const r of rows){const d=N('details');d.append(N('summary',r.source_table+' · '+(r.fields.medication_name||r.fields.medication_name_reported||r.fields.name||r.record_id)+(r.field_path.length?' · item '+(r.field_path[1]+1):'')),N('p','Source date: '+(r.source_date||'unknown')+' · received '+(r.available_date||'unknown')+' · '+r.source_system,'meta'),N('p',r.meaning),details('Literal received fields',r.fields),details('Exact source fields and hashes',r.evidence));parent.append(d)}}
 function render(parent,r){parent.append(N('h3','Medication evidence for '+r.patient_id),N('p',r.medication_records+' medication records · '+r.nested_medication_items+' separately exposed report items · '+r.narrative_records+' narrative/context records · '+r.received_records_outside_review+' other received records outside this review','scope-notice'),N('p','Cutoff '+r.as_of+'. Current regimen, medication ingestion and original question resolution are not established.','meta'));
  if(r.current===false){parent.append(N('p',r.stale_reason,'missing'));return}
  for(const q of r.questions){parent.append(N('h4',q.title),N('p',q.purpose),N('p','Original state: '+q.status+' · unchanged','meta'));for(const b of q.binding_coverage){const d=N('details');d.append(N('summary',b.label+' · '+human(b.coverage_status)),N('p',Object.entries(b.category_counts).map(([k,n])=>k+': '+n).join(' · ')),details('Exact concept, relation, categories and predicate',b.source_contract));parent.append(d)}}
  for(const x of r.additional_unmapped_native_records)parent.append(N('p',x.source_table+' · '+x.reason,'scope-notice'));
  const meds=N('details');meds.open=true;meds.append(N('summary','Dated medication accounts'));accounts(meds,r.medication_accounts);parent.append(meds);
  const narrative=N('details');narrative.append(N('summary','Received narrative/context records — meaning unassessed'));accounts(narrative,r.narrative_accounts);parent.append(narrative,details('Explicit source connections and unresolved links',{links:r.source_links,issues:r.source_link_issues}));
  parent.append(N('h4','Before a medication decision'));for(const q of r.review_questions)parent.append(N('p',q));
  const ref=N('a',r.reference.title);ref.href=r.reference.url;ref.target='_blank';ref.rel='noopener noreferrer';parent.append(ref,N('p',r.reference.scope,'meta'));for(const l of r.limits)parent.append(N('p',l,'meta'));
  addCapabilityMatches(parent,{kind:'medication_source_review',id:r.id,finding_ids:[]},{patient_id:r.patient_id,findings:[]});
  // Known documentary scope only; no patient preferences or coverage are inferred.
  const match=parent.lastElementChild;match.querySelector('[aria-label="Needs to match (comma separated)"]').value='medication_review';match.querySelector('[aria-label="Decision date"]').value=r.as_of;
  addMedicationInterpretation(parent,r);
  parent.append(details('Immutable review receipt',r));
 }
 area.ontoggle=async()=>{if(!area.open||loaded)return;loaded=true;try{const c=await workspaceFetch('/api/stack/shared-information/choices',{portfolio_id:portfolioId}),patients=[...new Set(c.questions.map(q=>q.patient_id))].sort(),sel=careSelect(body,'Patient for medication evidence review',patients.map(p=>[p,p]),patients[0]),button=N('button','Prepare received medication review'),history=N('button','Load saved medication reviews'),out=N('div');body.append(button,history,out);
  button.onclick=async()=>{button.disabled=true;try{const r=await workspaceFetch('/api/stack/medication-source-review/prepare',{portfolio_id:portfolioId,patient_id:sel.value});out.replaceChildren();render(out,r)}catch(e){out.replaceChildren(N('p',e.message,'missing'))}finally{button.disabled=false}};
  history.onclick=async()=>{history.disabled=true;try{const d=await workspaceFetch('/api/stack/medication-source-review/list',{portfolio_id:portfolioId,patient_id:sel.value});out.replaceChildren();for(const r of d.reviews){const card=N('article');render(card,r);out.append(card)}if(!d.reviews.length)out.append(N('p','No saved review for this patient and question portfolio.'))}catch(e){out.replaceChildren(N('p',e.message,'missing'))}finally{history.disabled=false}};
 }catch(e){body.replaceChildren(N('p',e.message,'missing'));loaded=false}};
}

function addMedicationTaskReview(card,task){
 const origin=task.handoff?.origin;if(origin?.kind!=='medication_source_review')return;
 const r=origin.review,area=N('details'),body=N('div');area.append(N('summary','Medication source packet in this handoff'),body);card.append(area);let loaded=false;
 area.ontoggle=()=>{if(!area.open||loaded)return;loaded=true;
  body.append(N('h4','Medication evidence for '+r.patient_id),N('p',r.medication_records+' medication records · '+r.nested_medication_items+' separately displayed report items · '+r.narrative_records+' narrative/context records','scope-notice'),N('p',task.dependencies_current?'Source and service dependencies were current when this queue was loaded. Reload the queue after changes.':'This is a historical handoff; source or service dependencies changed. Refresh the match before further work.',task.dependencies_current?'meta':'missing'),N('p','Cutoff '+r.as_of+'. This packet does not establish present use, a treatment indication, consent, entitlement or clinician review.'));
  for(const q of r.questions){body.append(N('h4',q.title),N('p',q.purpose),N('p','Original documentary state: '+q.status));for(const b of q.binding_coverage)body.append(N('p',b.label+' · '+human(b.coverage_status),'meta'))}
  for(const check of r.review_questions)body.append(N('p',check));
  for(const [label,rows] of [['Medication accounts',r.medication_accounts],['Narrative/context accounts — meaning unassessed',r.narrative_accounts]]){const section=N('details');section.append(N('summary',label));for(const a of rows){const d=N('details');d.append(N('summary',a.source_table+' · '+(a.fields.medication_name||a.fields.medication_name_reported||a.fields.name||a.record_id)),N('p','Source date '+(a.source_date||'unknown')+' · received '+a.available_date),N('p',a.meaning),details('Literal fields',a.fields),details('Exact source provenance',a.evidence));section.append(d)}body.append(section)}
  addMedicationInterpretation(body,r,task.id);
  body.append(details('Explicit connections and missing source links',{links:r.source_links,issues:r.source_link_issues}));for(const l of r.limits)body.append(N('p',l,'meta'));
 };
}
