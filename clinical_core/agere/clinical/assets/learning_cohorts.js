const learningCohorts=semanticFetch('/api/stack/learning-cohorts');
function cohortModels(c){return {...(c.models?.models||{}),...(c.windowed_reader?.status==='complete_shadow_only'?{windowed_minilm:c.windowed_reader}:{})}}
function cohortDiagnostics(card,cohort_id,kind){
 const open=N('button','Inspect '+human(kind)+' development disagreements'),area=N('div');let offset=0,outcome='disagreement';
 async function load(){
  try{const r=await semanticFetch('/api/stack/learning-cohorts/diagnostics?id='+cohort_id+'&kind='+kind+'&outcome='+outcome+'&offset='+offset);area.replaceChildren(N('h3','Where the fitted reader and teacher differ'),N('p',r.disagreement_rows+' disagreements among '+r.validation_rows+' development validation targets. Neither interpretation is verified clinical truth.','missing'));
   const select=N('select');select.setAttribute('aria-label',kind+' development result filter');for(const value of ['disagreement','agreement','all'])select.add(new Option(human(value),value));select.value=outcome;select.onchange=()=>{outcome=select.value;offset=0;load()};area.append(select);
   const refresh=N('button','Refresh acquisition status');refresh.onclick=load;area.append(refresh);
   for(const row of r.rows){const item=N('div',undefined,'source');item.append(N('h3',row.target.concept),N('p',row.target.patient_id+' · '+row.target.field+' · retained development validation partition','meta'),N('p','Teacher: '+Object.keys(SEMANTIC_AXES).map(a=>human(a)+': '+human(row.teacher_labels[a])).join(' · ')),N('p','Fitted reader: '+Object.keys(SEMANTIC_AXES).map(a=>human(a)+': '+human(row.model_labels[a])).join(' · ')),N('p',row.passes_development_threshold?'Passes the development confidence threshold; automatic acceptance remains disabled.':'No local acceptance is supported for this prediction.','missing'));
    const inspect=N('button','Inspect exact source and teacher proposal');inspect.onclick=async()=>{inspect.disabled=true;try{const text=N('blockquote'),chars=Array.from(row.context.text);text.append(document.createTextNode(chars.slice(0,row.context.target_start).join('')),N('mark',row.context.target_quote),document.createTextNode(chars.slice(row.context.target_end).join('')));item.append(text);const saved=await semanticFetch('/api/stack/semantic?id='+row.teacher_reading_id);renderSemantic(item,saved)}catch(e){item.append(N('p',String(e),'missing'));inspect.disabled=false}};item.append(inspect);area.append(item)}
   const previous=N('button','Previous development results'),next=N('button','Next development results');previous.disabled=offset===0;next.disabled=offset+r.limit>=r.total;previous.onclick=()=>{offset=Math.max(0,offset-r.limit);load()};next.onclick=()=>{offset+=r.limit;load()};area.append(N('p',r.total+' matching results · no new inference or review submitted.','meta'),previous,next);
  }catch(e){area.textContent=String(e)}
 }
 open.onclick=()=>{open.disabled=true;load()};card.append(open,area);
}
function learningReaders(box,target){
 semanticControl(box,target);
 learningCohorts.then(r=>{
  const available=r.cohorts.filter(c=>c.status==='current'&&Object.keys(cohortModels(c)).length);if(!available.length)return;
  const area=N('div',undefined,'source');area.append(N('h3','Current-source fitted readers'),N('p','Optional machine-imitation heads. Their calibration and release status remain visible; no automatic acceptance or clinical authority.','missing'));
  const choose=N('select');choose.setAttribute('aria-label','Cohort shadow reader');
  for(const c of available)for(const kind of Object.keys(cohortModels(c))){const option=N('option',human(kind)+' · cohort '+c.id.slice(0,8)+' · '+c.target_count+' source targets');option.value=c.id+':'+kind;choose.append(option)}
  const run=N('button','Read with fitted cohort model'),message=N('p','','meta');
  run.onclick=async()=>{run.disabled=true;try{const [cohort_id,kind]=choose.value.split(':');message.textContent='Checking the frozen source, supervision and model…';const result=await semanticFetch('/api/stack/learning-cohorts/read',{cohort_id,kind,target});const inspected=await semanticFetch('/api/stack/semantic?id='+result.id);renderSemantic(area,{...inspected,cache_hit:result.cache_hit,new_paid_api_calls:0});message.textContent='Saved shadow interpretation. Source review is still required.'}catch(e){message.textContent=String(e)}finally{run.disabled=false}};
  area.append(choose,run,message);box.append(area);
 }).catch(e=>box.append(N('p','Cohort readers unavailable: '+String(e),'missing')));
}
learningCohorts.then(r=>{
 const box=$('learning-cohorts');if(!r.cohorts.length){box.append(N('p','No additional development cohort registered.'));return}
 for(const c of r.cohorts){const card=N('div',undefined,'source');card.append(N('h3','Development cohort '+c.id.slice(0,8)));if(c.status!=='current'){card.append(N('p','Stale: '+c.reason,'missing'));box.append(card);continue}
  card.append(N('p',c.target_count+' exact targets · '+c.counts.train+' fitting / '+c.counts.calibration+' calibration / '+c.counts.validation+' development validation'),N('p',c.inherited_targets+' targets inherit previous partitions · '+c.conflicting_targets_excluded+' targets excluded for conflicting inherited components','meta'),N('p',c.acquisition.completed+' teacher readings complete · '+c.acquisition.failed+' failures · '+c.acquisition.pending+' pending'));
  cohortSpanInventory(card,c.id);
  if(c.sampling_coverage){const s=c.sampling_coverage;card.append(N('p',s.sampled_fields+' sampled fields span '+s.sampled_categories+' source categories. Fitted targets cover '+s.fitted_target_categories+' categories and '+s.fitted_target_concepts+' concepts; '+s.unmarked_fields+' fields originally had no marked target; their new acquisition appears above.','missing'),details('Sample breadth versus fitted target coverage',s))}
  for(const [kind,m] of Object.entries(cohortModels(c))){card.append(N('h3',human(kind)+' head'),N('p','Teacher agreement: '+(100*m.validation.exact_match).toFixed(1)+'% on '+m.validation.rows+' development rows · accepted '+m.validation.selected_rows+' · '+human(m.release.status)),N('p','Agreement with unreviewed machine targets is not clinical accuracy.','missing'),details(kind+' calibration, class coverage and release evidence',m));cohortDiagnostics(card,c.id,kind)}
  if(c.models)for(const [kind,error] of Object.entries(c.models.failures||{}))card.append(N('p',(kind==='minilm'?'Fixed-length MiniLM':human(kind))+' fitting failed: '+error,'missing'));
  if(c.windowed_reader&&['failed','stale','running'].includes(c.windowed_reader.status))card.append(N('p','Windowed reader: '+human(c.windowed_reader.status)+' '+(c.windowed_reader.error||''),'missing'));
  card.append(N('p',c.design.structural_minimums_met?'Design sample minimums met; model qualification remains separate.':c.design.recommended_next_step,'meta'),details('Cohort source, execution and limitations',c));box.append(card);
 }
}).catch(e=>{$('learning-cohorts').textContent=String(e)});

function cohortSpanInventory(card,cohort_id){
 const show=N('button','Inspect unmarked source acquisition'),area=N('div');let offset=0;
 async function inspectField(row,box){
  try{const r=await semanticFetch('/api/stack/cohort-spans/item?cohort_id='+cohort_id+'&item_id='+row.id);box.replaceChildren(N('h3',human(row.stream)+' · '+row.field),N('blockquote',r.text),N('p','Retained partition: '+human(r.partition)+'. This is development source acquisition, with no automatic training inclusion.','missing'),details('Verified record availability; other content is not submitted',r.source_availability));
   async function render(saved){
    const result=N('div');result.append(N('p',saved.scope_reason),N('p',saved.abstained?'The specialist abstained. This field remains counted in the inventory.':saved.proposals.length+' exact machine phrase proposals; no reviewed labels.','missing'));
    for(const p of saved.proposals){const item=N('div',undefined,'source');item.append(N('h3',p.concept),N('blockquote',p.quote),N('p',p.reason));const select=N('button','Open this source learning task');select.onclick=async()=>{select.disabled=true;try{const task=await semanticFetch('/api/stack/cohort-spans/select',{cohort_id,item_id:row.id,proposal_id:p.id});const verified=await semanticFetch('/api/stack/cohort-spans/task?cohort_id='+cohort_id+'&id='+task.id);item.append(N('p','Exact source task saved. '+human(verified.partition)+' assignment retained; no review or fit eligibility implied.','missing'));learningReaders(item,verified.target);for(const reading of verified.readings)renderSemantic(item,reading);item.append(details('Machine origin, source role and acquisition boundary',verified))}catch(e){item.append(N('p',String(e),'missing'));select.disabled=false}};item.append(select);result.append(item)}
    for(const message of saved.unresolved)result.append(N('p',message,'meta'));result.append(details('Saved proposal and usage receipt',saved));box.append(result);
   }
   if(r.saved)await render(r.saved);else if(r.attempted)box.append(N('p','A prior attempt exists. Pending or failed requests are not automatically retried.','missing'));else{const run=N('button','Suggest source phrases with Claude'),status=N('p','','meta');run.onclick=async()=>{run.disabled=true;status.textContent='Reviewing the complete selected field with its reported siblings and availability metadata…';try{await render(await semanticFetch('/api/stack/cohort-spans',{cohort_id,item_id:row.id}));status.textContent='Saved machine acquisition; source review remains separate.'}catch(e){status.textContent=String(e)}};box.append(N('p',r.request_bytes+' request bytes · one received field and reported siblings · no whole chart','meta'),run,status)}
  }catch(e){box.textContent=String(e)}
 }
 async function load(){
  area.textContent='Checking the acquisition inventory and exact source receipts…';
  try{const r=await semanticFetch('/api/stack/cohort-spans?cohort_id='+cohort_id+'&offset='+offset);area.replaceChildren(N('h3','Acquisition beyond the old concept vocabulary'),N('p',r.total+' originally unmarked fields across '+r.source_categories+' categories · '+r.proposals+' saved phrase proposals'),N('p',Object.entries(r.counts).map(([k,v])=>v+' '+human(k)).join(' · ')),N('p','Proposals, abstentions, exclusions and failures all stay visible. No clinician reviews or training labels are created.','missing'));
   const refresh=N('button','Refresh acquisition status');refresh.onclick=load;area.append(refresh);
   for(const row of r.rows){const item=N('div',undefined,'source');item.append(N('p',human(row.stream)+' · '+row.field),N('p',row.patient_id+' · '+human(row.status)+(row.partition?' · '+human(row.partition):''),'meta'));if(row.status==='excluded')item.append(details('Inherited source partition exclusion',row.exclusion));else{const inspect=N('button','Inspect received field and proposals'),content=N('div');inspect.onclick=()=>{inspect.disabled=true;inspectField(row,content)};item.append(inspect,content)}area.append(item)}
   const previous=N('button','Previous acquisition fields'),next=N('button','Next acquisition fields');previous.disabled=offset===0;next.disabled=offset+r.limit>=r.total;previous.onclick=()=>{offset=Math.max(0,offset-r.limit);load()};next.onclick=()=>{offset+=r.limit;load()};area.append(previous,next,details('Acquisition coverage and boundaries',r));
  }catch(e){area.textContent=String(e)}
 }
 show.onclick=()=>{show.disabled=true;load()};card.append(show,area);
}
