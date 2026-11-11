// Uses the existing source-learning workbench and exact saved observation IDs.
let typedOffset=0;
async function loadTypedLearning(){
 const list=$('typed-list'),status=$('typed-status');status.textContent='Loading source readings…';
 try{
  const r=await semanticFetch('/api/stack/typed-learning?offset='+typedOffset);
  list.replaceChildren();status.textContent=r.total+' source-bound readings available for review';
  for(const row of r.rows){
   const item=N('div',undefined,'typed-item'),button=N('button','Review source');
   item.append(N('p',row.question),N('p',row.patient_id+' · '+(row.review_status?human(row.review_status):'Not reviewed'),'meta'));
   button.onclick=()=>openTypedReading(row.observation_id);item.append(button);list.append(item);
  }
  if(!r.total)list.append(N('p','New chart readings will appear here when an analysis uses them.'));
  $('typed-previous').disabled=typedOffset===0;$('typed-next').disabled=r.next_offset===null;
  $('typed-next').onclick=()=>{typedOffset=r.next_offset;loadTypedLearning()};
 }catch(e){status.textContent='Source readings could not be loaded. '+String(e)}
}
async function openTypedReading(id){
 const box=$('typed-detail');box.replaceChildren(N('p','Opening the original document…'));
 try{
  const r=await semanticFetch('/api/stack/typed-learning/item?id='+encodeURIComponent(id));
  box.replaceChildren(N('h3',r.state.subject),N('p',r.patient_id+' · source date '+r.state.evidence[0].date+' · analysis through '+r.state.as_of,'meta'),N('blockquote',r.source_text));
  box.append(N('p','Recorded model reading: '+(r.state.options[r.machine_label]||'Unresolved')));
  const form=N('form'),actor=N('input'),rationale=N('textarea'),status=N('select'),answer=N('select'),use=N('input'),save=N('button','Save review'),message=N('p','','meta');
  actor.required=true;actor.placeholder='Your name or initials';actor.setAttribute('aria-label','Reviewer');
  rationale.required=true;rationale.placeholder='Explain what the document supports and why';rationale.setAttribute('aria-label','Reason for this review');
  for(const [value,label] of [['corrected','Correct the reading'],['accepted','Confirm the recorded reading'],['unresolved','Keep unresolved'],['rejected','Reject this example']])status.add(new Option(label,value));
  status.setAttribute('aria-label','Review disposition');
  for(const [value,meaning] of Object.entries(r.state.options))answer.add(new Option(meaning,value));
  answer.value=r.review?.label||r.machine_label||'unknown';answer.setAttribute('aria-label','Supported answer');
  use.type='checkbox';use.id='typed-use-training';
  const label=N('label');label.append(use,document.createTextNode(' Include this reviewed example in a future development training set'));
  const update=()=>{const canLabel=['accepted','corrected'].includes(status.value);answer.disabled=!canLabel;use.disabled=!canLabel;if(!canLabel)use.checked=false};status.onchange=update;update();
  let revision=r.revision,requestId=crypto.randomUUID();
  form.onsubmit=async e=>{
   e.preventDefault();save.disabled=true;
   try{
    const data=await semanticFetch('/api/stack/typed-learning/review',{observation_id:id,expected_binding:r.binding,expected_revision:revision,request_id:requestId,
     actor:actor.value,status:status.value,label:answer.disabled?null:answer.value,rationale:rationale.value,use_for_development:use.checked});
    revision=data.revision;requestId=crypto.randomUUID();message.textContent='Review saved. '+(data.training_eligible?'Included when the next development dataset is prepared.':'Excluded from development training.');loadTypedLearning();
   }catch(e){message.textContent=String(e)}finally{save.disabled=false}
  };
  form.append(actor,status,answer,rationale,label,save,message);box.append(form,N('p','This records a source review. It does not retrain a model, alter patient records or change the saved answer automatically.','meta'));
  box.scrollIntoView({behavior:'smooth',block:'start'});
 }catch(e){box.replaceChildren(N('p','Could not open this source reading. '+String(e)))}
}
$('typed-previous').onclick=()=>{typedOffset=Math.max(0,typedOffset-24);loadTypedLearning()};
loadTypedLearning();
