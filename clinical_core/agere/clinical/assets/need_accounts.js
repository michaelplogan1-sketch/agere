function addNeedAccounts(box,receipt,need){
 if(need.id==='explicit')return;
 const area=N('details'),body=N('div'),load=N('button','Inspect related financial accounts'),status=N('p','','meta'),result=N('div');
 area.append(N('summary','Follow related financial accounts'),body);box.append(area);
 body.append(N('p','Follow registered account links from this answer’s source records. Balances and payment reports do not establish hardship or savings.','meta'),load,status,result);
 const amount=(value,currency)=>Number.isInteger(value)?(value/100).toFixed(2)+' '+(currency||'currency unknown'):'not established';
 load.onclick=async()=>{load.disabled=true;status.textContent='Checking received account records locally…';try{
  const r=await workspaceFetch('/api/stack/evidence-needs/accounts',{receipt_id:receipt.id,need_id:need.id});
  result.replaceChildren(N('p',r.matched_accounts?r.matched_accounts+' accounts linked to the original answer sources. The question remains unresolved.':'The original answer sources have no registered account binding. This does not establish absent financial needs.','scope-notice'));
  for(const a of r.accounts){const card=N('section',undefined,'need-account'),prepare=N('button','Prepare this account’s records'),scope=N('div');
   card.append(N('h4',a.account_id),N('p',human(a.status)+' · '+a.source_count+' received records · '+a.outside_original_scope+' outside the original answer','meta'),N('p','Selected reported balance: '+amount(a.snapshot_amounts?.balance_cents,a.currency)),N('p',a.next_step,'meta'));
   for(const e of a.equations)card.append(N('p',human(e.id)+' · '+human(e.status),'meta'));
   for(const c of a.cash_comparisons)card.append(N('p',human(c.stream)+' · '+human(c.status),'meta'));
   const sources=N('details');sources.append(N('summary','Account sources and timing'));
   for(const s of a.source_records){const row=N('p',human(s.source_table)+' · '+s.record_id+' · available '+(s.arrival_date||'unknown')+' · '+(s.original_scope?'original answer':'outside original answer')+' '),inspect=N('button','Inspect account source ↗');inspect.onclick=()=>openEvidenceThread(r.binding.patient_id,s.record_id);row.append(inspect);sources.append(row)}
   prepare.onclick=async()=>{prepare.disabled=true;try{
    const selected=await workspaceFetch('/api/stack/evidence-needs/account-context',{account_receipt_id:r.id,account_id:a.account_id}),context=await workspaceFetch('/api/stack/context?id='+encodeURIComponent(selected.context_id));
    scope.replaceChildren(N('p',selected.record_scope.record_ids.length+' received account records and binding parents selected. '+selected.record_scope.excluded_records+' other patient records excluded. External capture and the original question remain unverified.','scope-notice'),details('Exact account evidence context',selected));addQuestionFollowup(scope,context,{receipt_id:receipt.id,need_id:need.id},'account',selected.id);
   }catch(e){scope.replaceChildren(N('p',e.message,'missing'))}finally{prepare.disabled=false}};
   card.append(sources,prepare,scope,details('Account amounts, reconciliation and provenance',a));result.append(card);
  }
  result.append(details('Account follow-through receipt',r));status.textContent=r.cache_hit?'Reused this exact account evidence receipt.':'Account evidence checked locally. No paid model calls.';
 }catch(e){status.textContent=e.message}finally{load.disabled=false}};
}
