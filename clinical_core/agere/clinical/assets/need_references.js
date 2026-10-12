function addNeedReferences(box,receipt,need){
 if(need.id==='explicit')return;
 const area=N('details',undefined,'question-references'),body=N('div'),load=N('button','Locate exact question references'),result=N('div');
 area.append(N('summary','Exact source units named in this question'),body);body.append(N('p','Pass-qualified unit names and complete record IDs locate sources. This does not endorse the question’s premise or resolve its meaning.','meta'),load,result);box.append(area);
 load.onclick=async()=>{load.disabled=true;try{
  const r=await workspaceFetch('/api/stack/evidence-needs/references',{receipt_id:receipt.id,need_id:need.id});result.replaceChildren(N('p',r.references.length+' exact references located · '+r.unresolved_mentions.length+' reference mentions remain unresolved.','meta'));
  for(const m of r.unresolved_mentions)result.append(N('p',m.mention+' · '+human(m.reason),'missing'));
  const choices=[];
  for(const ref of r.references){const label=N('label'),check=N('input');check.type='checkbox';check.setAttribute('aria-label','Select exact reference '+ref.name);label.append(check,N('span',ref.name+' → '+ref.record_id+(ref.field?' · '+ref.field:'')+' · '+human(ref.kind)));choices.push({check,name:ref.name});result.append(label);if(ref.source_unit)result.append(details('Inspect exact unit '+ref.name,ref.source_unit));}
  addReferenceComparisonControls(result,r,choices,receipt,need);
  if(r.references.length){const select=N('button','Prepare selected reference evidence'),scope=N('div');result.append(select,scope);select.onclick=async()=>{select.disabled=true;try{
   const names=choices.filter(x=>x.check.checked).map(x=>x.name);if(!names.length)throw Error('Select at least one exact reference.');
   const p=await workspaceFetch('/api/stack/evidence-needs/reference-context',{reference_id:r.id,reference_names:names}),c=await workspaceFetch('/api/stack/context?id='+encodeURIComponent(p.context_id));
   scope.replaceChildren(N('p',p.record_scope.record_ids.length+' received records selected through exact references and typed links. '+p.unresolved_mentions.length+' ambiguous mentions and '+p.unselected_reference_names.length+' unselected references remain visible. The question remains unresolved.','scope-notice'),details('Exact question, selected references and linked source scope',p));
   addQuestionFollowup(scope,c,{receipt_id:receipt.id,need_id:need.id},'exact_references',p.id);
  }catch(e){scope.replaceChildren(N('p',e.message,'missing'))}finally{select.disabled=false}};}
  result.append(details('Immutable reference lookup and original question',r));
 }catch(e){result.replaceChildren(N('p',e.message,'missing'))}finally{load.disabled=false}};
}
