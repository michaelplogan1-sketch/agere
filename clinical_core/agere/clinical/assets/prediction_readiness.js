function addPredictionReadiness(parent, adapter, datasetIdentity) {
 const button=N('button','Inspect protected outcome-model build','chip'), box=N('section',undefined,'prediction-readiness');
 button.onclick=async()=>{button.disabled=true;box.replaceChildren(N('p','Checking source and model-build dependencies…','meta'));
  try {
   const status=await semanticFetch('/api/stack/prediction-qualification');
   const rows=status.reports.filter(r=>r.task?.adapter===adapter && (r.dataset_identity===datasetIdentity || !r.current));box.replaceChildren();
   if(!rows.length)box.append(N('p','No saved qualification report is attached for this task. No fitted or validated predictor is implied.','missing'));
   for(const r of rows){const card=N('article',undefined,'runtime-card');card.append(N('h3',r.task.title));
    if(!r.current){card.append(N('p','Qualification unavailable: source or build dependencies changed.','warn'),N('p',r.reason,'meta'));box.append(card);continue}
    card.append(N('h4',r.status==='not_fitted'?'No predictor fitted':'Research comparison only'),N('p','Saved build population: '+r.counts.patients_assessed.toLocaleString()+' patients. '+r.source_qualification.timing_eligible+' timing-eligible observations; '+r.counts.eligible_rows+' remain after development protections.'));
    const labels=r.source_qualification.timing_eligible_label_counts;
    card.append(N('p','Timing-eligible recorded outcomes: '+(labels['1']||0)+' positive / '+(labels['0']||0)+' zero. A recorded zero does not establish absence of care.','meta'));
    const table=N('table'), head=N('tr');for(const label of ['Development partition','Recorded class','Observed','Required minimum','Shortfall']){const th=N('th',label);th.scope='col';head.append(th)}table.append(head);
    for(const row of r.class_deficits){const tr=N('tr');for(const value of [row.partition.replaceAll('_',' '),row.recorded_class,row.observed,row.minimum,row.deficit])tr.append(N('td',String(value)));table.append(tr)}card.append(table);
    for(const reason of r.readiness_gaps)card.append(N('p',reason,'missing'));
    for(const [kind,examples] of Object.entries(r.source_qualification.examples)){const group=N('details');group.append(N('summary',kind.replaceAll('_',' ')+' · received-evidence examples'));
     for(const e of examples){const item=N('div',undefined,'source');item.append(N('p',e.patient_id+' · '+(e.timing_eligible?'timing eligible':e.reasons.join(', '))));const inspect=N('button','Inspect source qualification for '+e.patient_id,'chip');
      inspect.onclick=async()=>{inspect.disabled=true;try{const route=adapter==='receipt_day'?'/api/stack/decision-landmarks':'/api/stack/baseline';item.append(details('Received evidence, dates and exact source dependencies',await semanticFetch(route+'?id='+encodeURIComponent(e.baseline_receipt_id))))}catch(error){item.append(N('p',String(error),'warn'))}finally{inspect.disabled=false}};item.append(inspect);group.append(item)}card.append(group)}
    card.append(N('p',r.limits.join(' '),'meta'),details('Build task, source snapshot, metrics and verification receipt',r));box.append(card)
   }
  }catch(error){box.replaceChildren(N('p',String(error),'warn'))}finally{button.disabled=false}
 };parent.append(button,box);
}
