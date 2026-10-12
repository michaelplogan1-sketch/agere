function addNeedCandidates(box,receipt,need){
 if(need.id==='explicit')return;
 const area=N('details'),body=N('div'),query=N('textarea'),search=N('button','Find candidate sources locally'),status=N('p','','meta'),result=N('div');
 area.append(N('summary','Discover evidence for this question'),body);box.append(area);
 query.value=need.question;query.maxLength=1500;const label=N('label','Discovery query');label.append(query);query.setAttribute('aria-label','Discovery query for '+need.question);
 body.append(N('p','Search received records for this patient. Ranked candidates do not become exact question bindings or clinical conclusions.','meta'),label,search,status,result);
 search.onclick=async()=>{search.disabled=true;status.textContent='Searching bounded local passage candidates…';try{
  const r=await workspaceFetch('/api/stack/evidence-needs/candidates',{receipt_id:receipt.id,need_id:need.id,query:query.value});
  result.replaceChildren(N('p',r.summary.candidate_records+' candidate records · '+r.summary.outside_original_scope+' outside the original answer · '+r.summary.received_records_not_returned+' received records not returned. Retrieval completeness remains unassessed.','scope-notice'));
  addEvidenceTiming(result,{kind:'candidates',id:r.id});
  addQuestionLearning(result,r);
  const choices=[];
  for(const candidate of r.candidates){const card=N('section'),choose=N('input');choose.type='checkbox';choose.setAttribute('aria-label','Select candidate '+candidate.record_id);const label=N('label',human(candidate.source_table)+' · '+candidate.record_id);label.prepend(choose);card.append(label,N('p',human(candidate.original_scope_status)+' · source available '+(candidate.available_date||'unknown'),'meta'));
   for(const p of candidate.passages)card.append(N('blockquote',p.text),N('small','Rank '+p.rank+' · '+p.source.field+' · characters '+p.source.start+'–'+p.source.end,'meta'));
   const inspect=N('button','Inspect candidate source ↗');inspect.onclick=()=>openEvidenceThread(r.binding.patient_id,candidate.record_id);card.append(inspect);result.append(card);choices.push({id:candidate.record_id,choose});
  }
  const prepare=N('button','Prepare selected candidate evidence'),scope=N('div');prepare.disabled=!r.candidates.length;
  prepare.onclick=async()=>{prepare.disabled=true;try{
   const ids=choices.filter(v=>v.choose.checked).map(v=>v.id);if(!ids.length)throw Error('Select at least one candidate record.');
   const selected=await workspaceFetch('/api/stack/evidence-needs/select-candidates',{candidate_id:r.id,record_ids:ids}),context=await workspaceFetch('/api/stack/context?id='+encodeURIComponent(selected.context_id));
   scope.replaceChildren(N('p',selected.selection.retrieved_record_ids.length+' selected roots expand to '+selected.selection.connected_record_ids.length+' connected records. '+selected.record_scope.excluded_records+' other received records remain outside this context. The original question remains unresolved.','scope-notice'),details('Candidate selection, typed links and original requirement',selected));addQuestionFollowup(scope,context,{receipt_id:receipt.id,need_id:need.id},'candidates',selected.id);
  }catch(e){scope.replaceChildren(N('p',e.message,'missing'))}finally{prepare.disabled=!r.candidates.length}};
  result.append(prepare,scope,details('Discovery scope and provenance',r));status.textContent=r.cache_hit?'Reused the candidate search for these exact inputs.':'Candidate discovery complete. Select records to inspect further.';
 }catch(e){status.textContent=e.message}finally{search.disabled=false}};
}
