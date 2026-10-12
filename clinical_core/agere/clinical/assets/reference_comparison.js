function renderReferenceComparison(box,r){
 const status=r.comparison.status;
 if(r.current===false){box.append(N('h4','Historical comparison · source dependencies are stale'));for(const message of r.stale_reasons)box.append(N('p',message,'missing'));}
 box.append(N('h4',status==='null_value_present'?'A reported null is present':r.comparison.literal_equal?'Selected literal values match exactly':'Selected literal values differ'));
 box.append(N('p','Local comparison · no normalization or unit conversion · '+(r.same_source_location?'both selectors refer to the same source location':'two selected source locations'),'meta'));
 for(const side of ['left','right']){const x=r[side],value=x.value;box.append(N('p',x.selector.reference_name+' · '+x.selector.field+' · '+human(x.selector.scope)+' · '+x.type,'meta'));if(x.type==='string')box.append(N('blockquote',value.length<=1000?value:'Full value retained in the receipt; '+value.length+' characters.'));else box.append(N('pre',JSON.stringify(value)));}
 if(r.comparison.text?.first_difference_windows.length)box.append(details('Exact first-difference windows',r.comparison.text));
 box.append(N('p','This does not establish authorship, independent corroboration, patient identity or clinical equivalence. The original question remains unresolved.','scope-notice'),details('Original question, exact values and comparison receipt',r));
}

function addReferenceComparisonControls(box,reference,choices,receipt,need){
 const area=N('section',undefined,'literal-comparison'),prepare=N('button','Compare selected source values locally'),saved=N('button','Load saved local comparisons'),body=N('div'),history=N('div');
 area.append(prepare,saved,body,history);box.append(area);
 saved.onclick=async()=>{saved.disabled=true;try{const r=await workspaceFetch('/api/stack/evidence-needs/comparisons',{receipt_id:receipt.id,need_id:need.id});history.replaceChildren();for(const row of r.comparisons){const card=N('section');renderReferenceComparison(card,row);history.append(card)}if(!r.comparisons.length)history.append(N('p','No local comparison is saved for this question.','meta'));}catch(e){history.replaceChildren(N('p',e.message,'missing'))}finally{saved.disabled=false}};
 prepare.onclick=async()=>{prepare.disabled=true;try{
  const names=choices.filter(x=>x.check.checked).map(x=>x.name);if(names.length!==2)throw Error('Select exactly two located references to compare.');
  const p=await workspaceFetch('/api/stack/evidence-needs/comparison-preview',{reference_id:reference.id,reference_names:names}),selectors=[];body.replaceChildren(N('p','Choose the exact received fields. Submitted unit text can be compared separately from the full received field.','meta'));
  for(const [i,source] of p.sources.entries()){
   const label=N('label',source.reference_name+' · '+source.record_id),select=N('select');select.setAttribute('aria-label',(i?'Right':'Left')+' source value for local comparison');select.add(new Option('Choose a source field',''));
   for(const f of source.fields){if(f.comparable)select.add(new Option(f.field+' · '+f.type,JSON.stringify({reference_name:source.reference_name,scope:'received_field',field:f.field})));}
   if(source.source_unit_text_available)select.add(new Option('Submitted unit text only',JSON.stringify({reference_name:source.reference_name,scope:'source_unit',field:source.source_unit_field})));
   if(source.default_field&&source.fields.some(f=>f.comparable&&f.field===source.default_field))select.value=JSON.stringify({reference_name:source.reference_name,scope:'received_field',field:source.default_field});
   label.append(select);body.append(label);selectors.push(select);
  }
  const compare=N('button','Run literal comparison'),result=N('div');body.append(compare,result,details('Available received fields and exact source identities',p));
  compare.onclick=async()=>{compare.disabled=true;try{if(selectors.some(s=>!s.value))throw Error('Choose one exact field on each side.');const r=await workspaceFetch('/api/stack/evidence-needs/compare',{reference_id:reference.id,left:JSON.parse(selectors[0].value),right:JSON.parse(selectors[1].value)});result.replaceChildren();renderReferenceComparison(result,r);}catch(e){result.replaceChildren(N('p',e.message,'missing'))}finally{compare.disabled=false}};
 }catch(e){body.replaceChildren(N('p',e.message,'missing'))}finally{prepare.disabled=false}};
}
