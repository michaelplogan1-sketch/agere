function addQuestionFollowup(box,context,origin,selectionKind,selectionId){
 const state=N('div'),saved=N('button','Attach saved follow-up interpretation');box.append(saved,state);
 const attach=async answer=>{const r=await workspaceFetch('/api/stack/evidence-needs/answer',{...origin,selection_kind:selectionKind,selection_id:selectionId,answer_id:answer.id});state.replaceChildren(N('p','Saved follow-up attached to the original question · '+r.summary.findings+' findings · '+r.summary.exact_citations+' exact citations. The requirement remains unresolved.','scope-notice'),details('Exact question and answer dependencies',r));return r};
 saved.onclick=async()=>{saved.disabled=true;try{const plan=await workspaceFetch('/api/stack/context/plan',{context_id:context.id});if(!plan.saved_answer_id)throw Error('This selected context has no complete saved interpretation yet.');await attach({id:plan.saved_answer_id})}catch(e){state.replaceChildren(N('p',e.message,'missing'))}finally{saved.disabled=false}};
 addCaseSynthesis(box,context,attach);
}

function addQuestionAnswers(box,receipt,need){
 if(need.id==='explicit')return;
 const area=N('details',undefined,'question-answers'),body=N('div'),load=N('button','Load follow-up interpretations'),status=N('p','','meta'),result=N('div');area.append(N('summary','Follow-up interpretations for this question'),body);body.append(load,status,result);box.append(area);
 load.onclick=async()=>{load.disabled=true;try{const r=await workspaceFetch('/api/stack/evidence-needs/answers?'+new URLSearchParams({receipt_id:receipt.id,need_id:need.id}));result.replaceChildren();
  for(const a of r.answers){const card=N('section'),open=N('button','Open follow-up answer ↗'),answer=N('div');card.append(N('p',(a.current?'Current source dependencies':'Stale source dependencies')+' · '+a.summary.findings+' findings · '+a.summary.exact_citations+' exact citations','meta'),N('p','Machine evidence for review. The original question remains unresolved.','scope-notice'));
   for(const reason of a.stale_reasons)card.append(N('p',reason,'missing'));
   open.disabled=!a.current;open.onclick=async()=>{open.disabled=true;try{renderCaseSynthesis(answer,await workspaceFetch('/api/stack/case-synthesis?id='+encodeURIComponent(a.answer_id)))}catch(e){answer.replaceChildren(N('p',e.message,'missing'))}finally{open.disabled=!a.current}};
   card.append(open,answer,details('Immutable follow-up attachment',a));result.append(card);
  }status.textContent=r.answers.length?r.answers.length+' saved follow-up interpretations. None automatically closes this requirement.':'No follow-up interpretation is attached to this question.';
 }catch(e){status.textContent=e.message}finally{load.disabled=false}};
}
