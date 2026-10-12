// Dated source reports are context for review, not current goals or consent.
function showPatientContext(box, receipt) {
 const c=receipt.context||receipt;
 box.append(N('h4','Patient priorities and practical access'),N('p',c.reports.length+' received reports through '+c.as_of+'. Current goals, service preferences and consent still require confirmation.','scope-notice'));
 if(c.literal_differences.length)box.append(N('p',c.literal_differences.length+' fields have different values across dated reports. Review the accounts; no contradiction was automatically resolved.','missing'));
 if(!c.reports.length)box.append(N('p','No reports from these source categories were received by the selected date. Missing reports do not establish no barriers or no support.','meta'));
 for(const r of c.reports){const d=N('details');d.append(N('summary',human(r.source_table)+' · '+(r.source_date||'Source date unknown')+' · '+(r.respondent||'Respondent not supplied')),
 N('p','Received '+r.available_date+' · '+r.record_id,'meta'));
 if(r.superseded_by.length)d.append(N('p','Received successor: '+r.superseded_by.join(', '),'missing'));
 if(r.temporal_scope!=='dated_report_not_current_confirmation')d.append(N('p',human(r.temporal_scope),'missing'));
 for(const [key,value]of Object.entries(r.fields))d.append(N('p',human(key)+': '+(value===null?'Not supplied (null)':String(value))));
 if(r.unrepresented_fields.length)d.append(N('p','Fields not projected: '+r.unrepresented_fields.join(', '),'missing'));
 box.append(d)}
 box.append(details('Exact patient context, source fingerprints and limits',receipt));
}
function addPatientContextPicker(box,patient,asof){
 const area=N('details'),body=N('div');area.append(N('summary','Inspect patient priorities and practical access'),body);box.append(area);
 const load=N('button','Load patient and support reports'),attach=N('button','Attach these reports to the service review'),detach=N('button','Detach patient context'),output=N('div'),status=N('p','','meta');
 body.append(N('p','Review dated patient, support and access accounts. Keep manually entered preferences separate from source evidence; confirm the patient’s current goals and wishes.','meta'),load,output,attach,detach,status);
 attach.disabled=true;detach.disabled=true;let context=null,binding=null,version=0;
 const clear=()=>{binding=null;detach.disabled=true;};
 asof.addEventListener('input',()=>{version++;clear();context=null;attach.disabled=true;output.replaceChildren();status.textContent='Decision date changed. Reload reports before attaching them.'});
 load.onclick=async()=>{const token=++version;load.disabled=true;clear();attach.disabled=true;try{const c=await workspaceFetch('/api/stack/patient-context',{patient_id:patient,as_of:asof.value});if(token!==version||c.as_of!==asof.value)return;context=c;output.replaceChildren();showPatientContext(output,c);attach.disabled=false;status.textContent='Reports loaded. No source has been attached to a service match yet.'}catch(e){status.textContent=e.message}finally{load.disabled=false}};
 attach.onclick=()=>{if(!context||context.as_of!==asof.value){status.textContent='Load reports for this decision date again.';return}binding=context.id;detach.disabled=false;status.textContent='Exact dated reports attached to future matches. Current goals, consent and service preference remain unconfirmed.'};
 detach.onclick=()=>{version++;clear();status.textContent='Patient context detached from future matches. Saved matches keep their original source bindings.'};
 return ()=>{if(binding&&context.as_of!==asof.value)throw Error('Patient-context decision date changed. Reload or detach the reports.');return binding?{patient_context_id:binding}:{}};
}
function showPatientOptionChecks(box,receipt,option){
 const checks=receipt.option_review_checks[option.id]||[];const d=N('details');d.append(N('summary','Confirm patient fit and practical access ('+checks.length+')'));
 for(const check of checks)d.append(N('p',check.review_question),N('p',check.record_ids.length?'Dated accounts: '+check.record_ids.join(', '):'No confirming source supplied for this check.','meta'));
 box.append(d);
}
function addPatientTaskContext(box,task){
 const area=N('details'),body=N('div');area.append(N('summary','Inspect patient context carried into work'),body);box.append(area);let loaded=false;
 area.ontoggle=async()=>{if(!area.open||loaded)return;loaded=true;try{const r=await workspaceFetch('/api/stack/patient-context/task?id='+encodeURIComponent(task.id));body.replaceChildren();if(!r.patient_context){body.append(N('p','This task has no bound patient/support source context. Manually entered preferences remain a separate report.','meta'));return}
 if(r.stale_reason)body.append(N('p',r.stale_reason,'missing'));showPatientContext(body,r.patient_context);showPatientOptionChecks(body,r.patient_context,{id:task.option_id});
 body.append(N('p',r.handoff_context_current?'The current internal handoff carries these exact reports and review questions.':'No current handoff attachment exists for these reports. Prepare a fresh packet after resolving changed dependencies.','meta'));
 }catch(e){body.replaceChildren(N('p',e.message,'missing'));loaded=false}};
}
