// Explicit expansion leaves original contexts and clinical uncertainty intact.
function addReferenceScope(box,target,initialRoots=[]){
 const area=N('details'),body=N('div');area.append(N('summary','Inspect and expand exact source references'),body);box.append(area);let loaded=false;
 async function load(){
  const state=await workspaceFetch('/api/stack/reference-scope/list',{target});
  body.replaceChildren(N('p','Inspect the records named by these sources before expanding the question’s evidence. Exact links do not prove clinical relationships, completed care or consent. Earlier selections stay in history.','meta'));
  const roots=careField(body,'Starting received record IDs',initialRoots.join(', ')),preview=N('button','Inspect referenced records'),out=N('div'),status=N('p','','meta');body.append(preview,status,out);
  function render(p){
   out.replaceChildren(N('h4',p.records.length+' reachable received records'),N('p',p.added_record_ids.length+' additional records beyond the historical typed scope. '+p.reference_component.issues.length+' unresolved reference issues. Nothing has been added to the evidence yet.','meta'));
   const selected=new Set(p.root_record_ids);
   for(const r of p.records){const label=N('label',undefined,'meta'),check=document.createElement('input');check.type='checkbox';check.style.width='auto';check.style.margin='0 8px 0 0';label.style.display='flex';label.style.alignItems='center';check.checked=r.is_root;check.disabled=r.is_root;
    check.onchange=()=>{if(check.checked)selected.add(r.record_id);else selected.delete(r.record_id)};
    label.append(check,document.createTextNode(' '+human(r.source_table)+' · source '+(r.source_date||'unknown')+' · received '+r.available_date+' · '+(r.is_root?'original root':r.was_in_historical_typed_scope?'previously reachable':'additional')+' · '+r.record_id));out.append(label);}
   for(const issue of p.reference_component.issues)out.append(N('p',human(issue.reason)+' · '+issue.source_id+' · '+issue.field+' → '+String(issue.target_id),'missing'));
   const all=N('button','Select all reachable records'),save=N('button','Prepare this explicit evidence scope'),message=N('p','','meta');out.append(all,save,message,details('Exact reference edges and original scope comparison',p));
   all.onclick=()=>{for(const id of p.reference_component.record_ids)selected.add(id);for(const check of out.querySelectorAll('input[type=checkbox]'))check.checked=true;message.textContent='All '+selected.size+' reachable records selected. Review the scope before preparing it.'};
   save.onclick=async()=>{save.disabled=true;try{await workspaceFetch('/api/stack/reference-scope/select',{preview_id:p.id,record_ids:[...selected]});await load()}catch(e){message.textContent=e.message;save.disabled=false}};
  }
  preview.onclick=async()=>{preview.disabled=true;try{render(await workspaceFetch('/api/stack/reference-scope/preview',{target,record_ids:careList(roots.value)}));status.textContent='Reference preview saved. Select the records to include.'}catch(e){status.textContent=e.message}finally{preview.disabled=false}};
  for(const s of state.selections){const card=N('section');card.append(N('h4',(s.current?'Current':'Historical')+' selected reference scope'),N('p',s.selected_record_ids.length+' selected records · '+s.unselected_record_ids.length+' reachable records not selected · '+human(s.status)+'. Original question remains unresolved.','meta'));
   for(const reason of s.stale_reasons)card.append(N('p',reason,'missing'));
   if(s.structured_transport){card.append(N('p','Lossless structured-field transport · '+s.structured_transport.original_units+' original units → '+s.structured_transport.transport_units+' transport units. All source values and narrative units preserved.','meta'),details('Exact structured paths and reconstruction manifest',s.structured_transport))}
   else if(s.current&&s.status==='source_context_prepared'){const split=N('button','Prepare lossless structured-field transport'),message=N('p','','meta');card.append(split,message);split.onclick=async()=>{split.disabled=true;try{await workspaceFetch('/api/stack/reference-scope/transport',{selection_id:s.id});await load()}catch(e){message.textContent=e.message;split.disabled=false}}}
   const answers=N('div');
   function showAnswers(rows){answers.replaceChildren();for(const a of rows){const link=N('a','Inspect interpretation returned to this question ↗');link.href='/stack?case='+encodeURIComponent(a.request.answer_id);answers.append(N('p',(a.current?'Current':'Historical')+' machine answer · '+a.summary.findings+' findings · '+a.summary.exact_citations+' exact citations.','meta'),link);for(const reason of a.stale_reasons)answers.append(N('p',reason,'missing'));}}
   showAnswers(state.answers.filter(a=>a.request.selection_id===s.id));
   if(s.current&&s.status==='source_context_prepared'){const open=N('button','Inspect this expanded evidence'),result=N('div');
    async function attach(answer){await workspaceFetch('/api/stack/reference-scope/answer',{selection_id:s.id,answer_id:answer.id});const fresh=await workspaceFetch('/api/stack/reference-scope/list',{target});showAnswers(fresh.answers.filter(a=>a.request.selection_id===s.id));}
    open.onclick=async()=>{open.disabled=true;try{const c=await workspaceFetch('/api/stack/context?id='+encodeURIComponent(s.context_id));result.replaceChildren();addCaseSynthesis(result,c,attach)}catch(e){result.replaceChildren(N('p',e.message,'missing'))}finally{open.disabled=false}};
    const saved=N('details'),form=N('div'),id=careField(form,'Saved expanded-scope interpretation ID',''),bind=N('button','Return expanded-scope interpretation'),message=N('p','','meta');saved.append(N('summary','Attach a saved interpretation for this expanded scope'),form,bind,message);
    bind.onclick=async()=>{bind.disabled=true;try{await attach({id:id.value.trim()});message.textContent='Machine interpretation returned to the original question. No clinical requirement resolved.'}catch(e){message.textContent=e.message}finally{bind.disabled=false}};
    card.append(open,result,saved);
   }
   card.append(answers,details('Selection, context, omissions and unresolved references',s));body.append(card);
  }
  if(state.previews.length)body.append(details('Saved reference preview history',state.previews));
 }
 area.ontoggle=async()=>{if(!area.open||loaded)return;loaded=true;try{await load()}catch(e){body.replaceChildren(N('p',e.message,'missing'));loaded=false}};
}
