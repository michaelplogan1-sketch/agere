// Appointment navigation remains an evidence review, without inferred care need.
function renderAppointmentNavigation(box,r){
 box.replaceChildren(N('h3','Navigation review for '+r.patient_id),N('p',human(r.appointment.status)+' · source cutoff '+r.as_of,'pill'),N('p','This is a historical appointment account. It does not explain a no-show or establish a current need, consent, benefit or priority.','missing'));
 for(const c of r.responsibilities)box.append(N('h4',c.label),N('p',c.question));
 box.append(details('Exact appointment history and original work questions',r));
 addCapabilityMatches(box,{kind:'appointment_navigation_review',id:r.id,finding_ids:[]},{patient_id:r.patient_id,findings:[]},{need_tags:['care_navigation'],as_of:r.as_of,rationale:'Review the exact received appointment history and determine what must be confirmed before any navigation. No cause, current care need, clinical priority or entitlement is inferred.'});
}
function addAppointmentTaskReview(card,task){
 if(task.handoff?.origin?.kind!=='appointment_navigation_review')return;
 const button=N('button','Review navigation responsibilities'),box=N('section');card.append(button,box);
 button.onclick=async()=>{button.disabled=true;try{const r=await workspaceFetch('/api/stack/appointment-navigation/task',{task_id:task.id});box.replaceChildren(N('h4','Proposed navigation responsibilities'),N('p','Proposals do not reserve services, assign clinical staff or resolve any question.','missing'));
 const fields=N('div'),bindings=[];for(const c of r.responsibilities){const row=N('article',undefined,'finding-card');row.append(N('h4',c.label),N('p',c.question));const select=careSelect(row,'Proposed service for '+c.label,[['','Unassigned'],...r.options.map(o=>[o.id,o.capability.name+' · '+human(o.status)])]),why=careField(row,'Reason for '+c.label,'','textarea');fields.append(row);bindings.push({key:c.key,select,why})}
 const note=careField(fields,'Navigation coordination note','','textarea'),preview=N('button','Preview navigation proposal'),save=N('button','Save navigation proposal'),out=N('div');save.disabled=true;let candidate=null;const request=()=>({task_id:r.task.id,expected_revision:r.task.revision,component_bindings:bindings.filter(b=>b.select.value).map(b=>({component_key:b.key,option_id:b.select.value,rationale:b.why.value})),coordination_note:note.value});
 const show=p=>{out.replaceChildren(appointmentTable(['Responsibility','State','Proposed owner'],p.components.map(c=>[c.label,human(c.state),c.proposed_service?.capability.owner.id||'Unassigned'])),N('p','All four questions remain unresolved. No responsibility has been accepted.','missing'),details('Source, coverage, patient context and participation packet',p))};
 fields.oninput=()=>{candidate=null;save.disabled=true};fields.onchange=fields.oninput;
 preview.onclick=async()=>{preview.disabled=true;try{candidate=await workspaceFetch('/api/stack/appointment-navigation/preview',request());show(candidate);save.disabled=false}catch(e){out.replaceChildren(N('p',e.message,'missing'))}finally{preview.disabled=false}};
 save.onclick=async()=>{save.disabled=true;try{const p=await workspaceFetch('/api/stack/appointment-navigation/save',{proposal:request(),preview_id:candidate.id});show(p);out.prepend(N('p','Navigation proposal saved with exact task revision and source dependencies.','scope-notice'))}catch(e){out.replaceChildren(N('p',e.message,'missing'))}};
 box.append(fields,preview,save,out);for(const p of r.proposals){const saved=N('details');saved.append(N('summary',(p.current?'Current':'Historical')+' navigation proposal'),N('p',p.stale_reason||'Exact task and source dependencies are current.','meta'),appointmentTable(['Responsibility','State'],p.components.map(c=>[c.label,human(c.state)])),details('Saved proposal',p));box.append(saved)}
 box.append(details('Received appointment history and review questions',r.source_review));
 }catch(e){box.replaceChildren(N('p',e.message,'missing'))}finally{button.disabled=false}};
}
