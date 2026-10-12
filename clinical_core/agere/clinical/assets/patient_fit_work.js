// Received-source progress stays separate from current patient confirmation.
function addPatientFitWork(box,task){
 const area=N('details'),body=N('div');area.append(N('summary','Track patient fit and access questions'),body);box.append(area);let loaded=false;
 async function load(){
  const state=await workspaceFetch('/api/stack/patient-fit?id='+encodeURIComponent(task.id));
  body.replaceChildren(N('p','Each question keeps its service, owner, source date and exact received records. Historical evidence cannot confirm the patient’s current goals, wishes or consent.','scope-notice'));
  if(state.unavailable_reason)body.append(N('p',state.unavailable_reason,'missing'));
  if(!state.current_manifest_id&&state.prepare_available){
   const start=N('button','Track these patient-fit questions'),status=N('p','','meta');body.append(start,status);
   start.onclick=async()=>{start.disabled=true;try{await workspaceFetch('/api/stack/patient-fit/prepare',{task_id:task.id,expected_revision:task.revision});await load()}catch(e){status.textContent=e.message;start.disabled=false}};
  }
  for(const set of state.sets){
   const group=N('section');group.append(N('h4',(set.current?'Current':'Historical')+' questions · '+set.manifest.service.name),N('p','Work owner: '+set.manifest.owner.id+' · source cutoff '+set.manifest.as_of,'meta'));
   if(set.stale_reason)group.append(N('p',set.stale_reason,'missing'));
   for(const q of set.questions){
    const card=N('article',undefined,'finding-card');card.append(N('h4',q.review_question),N('p',human(q.work_state)+' · current confirmation unresolved','pill'));
    const answerBox=N('div'),loadAnswers=N('button','Load interpretations returned to this question');
    async function refreshAnswers(){const r=await workspaceFetch('/api/stack/patient-fit/answers',{target:q.target});answerBox.replaceChildren();
     if(!r.answers.length)answerBox.append(N('p','No saved interpretation is attached to this question.','meta'));
     for(const a of r.answers){const block=N('div'),link=N('a','Inspect the saved interpretation ↗');link.href='/stack?case='+encodeURIComponent(a.answer_id);
      block.append(N('p',(a.current?'Current':'Historical')+' machine interpretation · '+a.summary.findings+' findings · '+a.summary.exact_citations+' exact citations. Current patient confirmation remains unresolved.','meta'),link);
      for(const reason of a.stale_reasons)block.append(N('p',reason,'missing'));block.append(details('Exact question, source selection and answer',a));answerBox.append(block);}}
    loadAnswers.onclick=async()=>{loadAnswers.disabled=true;try{await refreshAnswers()}catch(e){answerBox.replaceChildren(N('p',e.message,'missing'))}finally{loadAnswers.disabled=false}};
    const selected=new Set(),sources=N('div');
    for(const r of q.source_reports){const label=N('label',undefined,'meta'),check=document.createElement('input');check.type='checkbox';check.style.width='auto';check.style.margin='0 8px 0 0';label.style.display='flex';label.style.alignItems='center';check.disabled=!q.current;check.onchange=()=>{if(check.checked)selected.add(r.record_id);else selected.delete(r.record_id)};
     label.append(check,document.createTextNode(' '+human(r.source_table)+' · '+(r.source_date||'date unknown')+' · '+r.record_id));sources.append(label,details('Exact dated account '+r.record_id,r));}
    card.append(sources);
    if(!q.record_ids.length)card.append(N('p','No confirming source is attached to this question. Current confirmation is still required; no contact has been sent.','missing'));
    if(q.current&&q.record_ids.length){const prepare=N('button','Prepare selected evidence for this question'),status=N('p','','meta');card.append(prepare,status);
     prepare.onclick=async()=>{prepare.disabled=true;try{if(!selected.size)throw Error('Select the exact dated reports to review.');await workspaceFetch('/api/stack/readiness/context',{target:q.target,record_ids:[...selected]});await load()}catch(e){status.textContent=e.message;prepare.disabled=false}};
    }
    for(const a of q.acquisitions){const receipt=N('div');receipt.append(N('p',(a.current?'Current':'Historical')+' evidence preparation · '+a.acquisition.record_scope.record_ids.length+' connected records. Patient confirmation remains unresolved.','meta'));
     for(const reason of a.stale_reasons)receipt.append(N('p',reason,'missing'));
     if(a.current){const open=N('button','Inspect bounded evidence for this question'),output=N('div');
      async function attach(answer){await workspaceFetch('/api/stack/patient-fit/answer',{target:q.target,acquisition_id:a.id,answer_id:answer.id});await refreshAnswers()}
      open.onclick=async()=>{open.disabled=true;try{const c=await workspaceFetch('/api/stack/context?id='+encodeURIComponent(a.acquisition.context_id));output.replaceChildren();addCaseSynthesis(output,c,attach)}catch(e){output.replaceChildren(N('p',e.message,'missing'))}finally{open.disabled=false}};
      const saved=N('details'),form=N('div'),id=careField(form,'Existing saved interpretation ID',''),bind=N('button','Return saved interpretation to this question'),status=N('p','','meta');saved.append(N('summary','Attach an existing interpretation for these exact sources'),form,bind,status);
      bind.onclick=async()=>{bind.disabled=true;try{await attach({id:id.value.trim()});status.textContent='Machine interpretation attached for review. Current patient confirmation remains unresolved.'}catch(e){status.textContent=e.message}finally{bind.disabled=false}};receipt.append(open,output,saved)}
     receipt.append(details('Exact question and evidence preparation receipt',a));card.append(receipt);
    }
    card.append(loadAnswers,answerBox);if(q.current&&q.record_ids.length)addReferenceScope(card,q.target,q.record_ids);group.append(card);
   }
   group.append(details('Original question set and dependencies',set.manifest));body.append(group);
  }
  body.append(N('p','These questions are also available in the existing evidence follow-up workflow after a scoped internal packet review. Preparing evidence here records no reviewer decision.','meta'));
 }
 area.ontoggle=async()=>{if(!area.open||loaded)return;loaded=true;try{await load()}catch(e){body.replaceChildren(N('p',e.message,'missing'));loaded=false}};
}
