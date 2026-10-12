// Isolated source mapping uses the same documentary-state contracts as the stack.
$('portable-intake-open').onclick=async()=>{
 const box=$('portable-intake-content');box.replaceChildren(N('p','Opening synthetic intake…'));
 try{
  const example=await workspaceFetch('/api/stack/portable-intake/example',{});
  const form=N('div'),output=N('div'),history=N('div'),status=N('p','','meta');
  const source=N('textarea'),mapping=N('textarea'),cutoff=N('input'),contract=N('select');
  source.value=JSON.stringify(example.package,null,2);mapping.value=JSON.stringify(example.mapping,null,2);
  source.rows=10;mapping.rows=12;source.maxLength=2000000;mapping.maxLength=50000;
  source.style.width=mapping.style.width='100%';cutoff.type='date';cutoff.value=example.cutoff;
  contract.add(new Option('Renal evidence and referral attendance','renal_review'));
  contract.add(new Option('Reported affordability and assistance evidence','affordability'));
  for(const [label,input] of [['Synthetic received source package',source],['Explicit field mapping',mapping],['Observation cutoff',cutoff],['Documentary question',contract]]){
   const l=N('label',label);input.setAttribute('aria-label',label);l.append(input);form.append(l);
  }
  const preview=N('button','Preview mapping and questions'),save=N('button','Save intake review','primary');
  form.append(N('p','Example: four independently identified synthetic patients. The initial map omits the sample date. All original rows remain available for inspection. Mapping names and meanings require review.','scope-notice'),preview,save,status);
  box.replaceChildren(form,output,history);
  const request=()=>({package:JSON.parse(source.value),mapping:JSON.parse(mapping.value),cutoff:cutoff.value,contract:contract.value});
  function render(r){
   output.replaceChildren(N('h3','Source intake → documentary questions'),N('p',r.summary.roster_patients+' roster patients · '+r.summary.received_records+' received records · '+r.summary.visible_records+' visible records · '+r.summary.patients_without_visible_records+' patients without visible records','meta'),N('p',r.summary.requirement_mapping_gaps+' requirement mapping gaps · '+r.summary.source_mapping_issues+' source issues · '+r.summary.unresolved_work_items+' unresolved review items','scope-notice'));
   if(r.current===false)output.append(N('p','Saved under earlier code. Re-run explicitly to produce a current version.','missing'));
   const table=N('table'),head=N('tr');['Patient','Requirement','Documentary state','Result'].forEach(t=>head.append(N('th',t)));table.append(head);
   for(const p of r.patients)for(const q of p.evaluation.requirements){const row=N('tr');[p.patient_id,q.label,human(q.state.status),human(q.result)].forEach(t=>row.append(N('td',t)));table.append(row)}
   output.append(table);
   const work=N('details');work.append(N('summary','Mapping and evidence review work ('+r.work_items.length+')'));
   for(const q of r.work_items)work.append(details(q.patient_id+' · '+q.label+' · unresolved',q));output.append(work);
   output.append(details('Exact original source, mappings, clocks and evidence',r),N('p',r.limits.join(' '),'meta'));
  }
  async function refresh(){
   const list=await workspaceFetch('/api/stack/portable-intake/list',{});history.replaceChildren(N('h3','Saved source reviews'));
   const before=N('select'),after=N('select');before.setAttribute('aria-label','Before mapping review');after.setAttribute('aria-label','After mapping review');
   for(const r of list.reviews){
    const label=r.source_id+' · '+r.mapping_version+' · '+human(r.contract)+' · '+r.id.slice(0,8)+(r.current?'':' · historical code');
    before.add(new Option(label,r.id));after.add(new Option(label,r.id));
    const b=N('button','Open '+label);b.onclick=async()=>{try{const saved=await workspaceFetch('/api/stack/portable-intake/inspect',{id:r.id});render(saved);source.value=JSON.stringify(saved.request.package,null,2);mapping.value=JSON.stringify(saved.request.mapping,null,2);cutoff.value=saved.request.cutoff;contract.value=saved.request.contract;status.textContent='Saved review reopened; edits create a new version.'}catch(e){status.textContent=e.message}};history.append(b);
   }
   const compare=N('button','Compare mapping versions'),result=N('div');compare.disabled=list.reviews.length<2;
   compare.onclick=async()=>{try{const d=await workspaceFetch('/api/stack/portable-intake/compare',{before_id:before.value,after_id:after.value});result.replaceChildren(N('p',d.changed_requirements.length+' changed documentary requirements across the same '+d.roster_patients+' patients. '+d.meaning),details('Changed requirements and preserved versions',d))}catch(e){result.replaceChildren(N('p',e.message,'missing'))}};
   history.append(before,after,compare,result);
  }
  for(const [button,route] of [[preview,'preview'],[save,'save']])button.onclick=async()=>{preview.disabled=save.disabled=true;status.textContent='Checking identity, timing, mappings and all roster patients…';try{const r=await workspaceFetch('/api/stack/portable-intake/'+route,request());render(r);status.textContent=route==='save'?'Immutable intake review saved. No active records or care tasks changed.':'Preview complete. Source mapping and clinical meaning remain unvalidated.';if(route==='save')await refresh()}catch(e){status.textContent=e.message}finally{preview.disabled=save.disabled=false}};
  await refresh();
 }catch(e){box.replaceChildren(N('p',e.message,'missing'))}
};
