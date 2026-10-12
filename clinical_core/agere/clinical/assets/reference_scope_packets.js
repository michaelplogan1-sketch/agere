function addReferencePacketWork(box,task,packet){
 const area=N('details'),body=N('div');area.append(N('summary','Expanded evidence linked to packet review'),body);box.append(area);let loaded=false;
 async function load(){
  const state=await workspaceFetch('/api/stack/reference-packet/list',{task_id:task.id});
  body.replaceChildren(N('p','Link a previously selected expanded source scope to an exact recorded internal review. This records evidence follow-through, not patient consent, a clinical decision or completed care.','meta'));
  for(const f of state.followups){
   const card=N('section');card.append(N('h4',f.target.question),N('p',(f.current?'Current':'Historical')+' expanded evidence · '+f.selected_record_ids.length+' selected records · '+f.unselected_record_ids.length+' reachable records excluded · '+f.principal.principal_id,'meta'),N('p',f.note));
   for(const reason of f.stale_reasons)card.append(N('p',reason,'missing'));
   for(const a of state.answers.filter(a=>a.request.followup_id===f.id)){const link=N('a','Inspect interpretation returned to packet and question ↗');link.href='/stack?case='+encodeURIComponent(a.request.answer_id);card.append(link,N('p',(a.current?'Current':'Historical')+' machine attachment · '+a.summary.findings+' findings. Requirement remains unresolved.','meta'));for(const reason of a.stale_reasons)card.append(N('p',reason,'missing'))}
   if(f.current){
    const open=N('button','Inspect expanded packet evidence'),out=N('div');
    async function attach(answer){await workspaceFetch('/api/stack/reference-packet/answer',{followup_id:f.id,answer_id:answer.id});await load()}
    open.onclick=async()=>{open.disabled=true;try{const c=await workspaceFetch('/api/stack/context?id='+encodeURIComponent(f.context_id));out.replaceChildren();addCaseSynthesis(out,c,attach)}catch(e){out.replaceChildren(N('p',e.message,'missing'))}finally{open.disabled=false}};
    const existing=N('details'),id=careField(existing,'Saved expanded packet interpretation ID',''),bind=N('button','Return saved interpretation to packet and question'),message=N('p','','meta');existing.prepend(N('summary','Attach an existing interpretation'));existing.append(bind,message);
    bind.onclick=async()=>{bind.disabled=true;try{await attach({id:id.value.trim()})}catch(e){message.textContent=e.message;bind.disabled=false}};
    card.append(open,out,existing);
   }
   card.append(details('Exact review, selection, owner and source dependencies',f));body.append(card);
  }
  if(!packet.prepare_available){body.append(N('p','A current completed packet and recorded internal review are required before expanded evidence can be linked.','missing'));return}
  const form=N('div',undefined,'care-grid'),target=careSelect(form,'Expanded-scope question',packet.available_targets.map((t,i)=>[String(i),t.question]),'0'),selections=careSelect(form,'Previously selected expanded evidence',[],''),note=careField(form,'Expanded follow-up note','','textarea'),credential=careField(form,'Expanded follow-up reviewer credential','','password');credential.autocomplete='off';
  const prepare=N('button','Link selected scope to this packet review'),status=N('p','','meta');prepare.disabled=true;body.append(form,prepare,status);let generation=0;
  async function choices(){const seq=++generation;prepare.disabled=true;selections.replaceChildren();try{const t=packet.available_targets[Number(target.value)];if(!t)return;const rows=await workspaceFetch('/api/stack/reference-scope/list',{target:t.target});if(seq!==generation)return;for(const s of rows.selections.filter(s=>s.current&&s.status==='source_context_prepared')){const option=N('option',s.selected_record_ids.length+' records · '+s.context_id.slice(0,12));option.value=s.id;selections.append(option)}prepare.disabled=!selections.options.length;status.textContent=prepare.disabled?'Prepare an explicit expanded scope from the original question first.':'Select the source scope and supply a scoped local reviewer credential.'}catch(e){if(seq===generation)status.textContent=e.message}}
  target.onchange=choices;
  prepare.onclick=async()=>{prepare.disabled=true;const token=credential.value.trim();credential.value='';try{if(!token)throw Error('Enter a scoped local reviewer credential.');const response=await fetch('/api/stack/reference-packet/prepare',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify({run_id:packet.current_run_id,review_revision:packet.current_review_revision,selection_id:selections.value,note:note.value,request_id:crypto.randomUUID()})});const result=await response.json();if(!response.ok)throw Error(result.error||'Expanded packet follow-up failed');await load()}catch(e){status.textContent=e.message;prepare.disabled=false}};
  await choices();
 }
 area.ontoggle=async()=>{if(!area.open||loaded)return;loaded=true;try{await load()}catch(e){body.replaceChildren(N('p',e.message,'missing'));loaded=false}};
}
