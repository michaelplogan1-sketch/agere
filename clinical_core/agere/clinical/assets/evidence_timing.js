function addEvidenceTiming(box,request){
 const area=N('details',undefined,'evidence-timing'),body=N('div'),load=N('button','Inspect source timing'),status=N('p','','meta'),result=N('div');
 area.append(N('summary','Source timing and dated selection'),body);body.append(N('p','Inspect source, clinical and arrival clocks separately. A calendar difference alone does not establish a contradiction.','meta'),load,status,result);box.append(area);
 load.onclick=async()=>{load.disabled=true;status.textContent='Inspecting the selected source clocks locally…';try{
  const r=await workspaceFetch('/api/stack/evidence-timing',request);
  result.replaceChildren(N('p',r.summary.records+' records inspected · '+r.summary.calendar_basis_unresolved+' separate date/timestamp relationships unresolved · '+r.summary.explicit_offset_calendar_rollovers+' explicitly represented offset rollovers. Original interpretations and account balances are unchanged.','scope-notice'));
  const sources=N('details');sources.append(N('summary','Inspect dates and timestamp provenance'));
  for(const row of r.records){const record=N('section');record.append(N('h4',human(row.source_table)+' · '+row.record_id),N('p','Source date '+(row.basis_dates.source_event||'unknown')+' · explicit clinical date '+(row.basis_dates.clinical||'unknown')+' · arrival '+(row.basis_dates.arrival||'unknown'),'meta'),N('p','Timestamp '+(row.raw_clock_fields.event_time?.value||'not received')+' · represented date '+(row.source_timestamp.represented_date||'unknown')+' · UTC date '+(row.source_timestamp.utc_date||'unknown'),'meta'),N('p',human(row.source_date_timestamp_relation)+' · separate date timezone '+human(row.date_timezone_binding),'meta'),details('Exact clock fields and existing clock decisions',row));sources.append(record)}
  const label=N('label','Date basis '),basis=N('select'),startLabel=N('label','From date '),start=N('input'),endLabel=N('label','Through date '),end=N('input'),check=N('button','Check date window'),windowBox=N('div');
  basis.setAttribute('aria-label','Timing date basis');[['source_event','Source event date'],['clinical','Explicit clinical date'],['arrival','Arrival date']].forEach(([v,s])=>basis.add(new Option(s,v)));
  start.type=end.type='date';start.setAttribute('aria-label','Timing window start');end.setAttribute('aria-label','Timing window end');start.max=end.max=r.binding.snapshot.as_of;
  label.append(basis);startLabel.append(start);endLabel.append(end);
  check.onclick=async()=>{check.disabled=true;try{
   const w=await workspaceFetch('/api/stack/evidence-timing/window',{timing_id:r.id,window:{basis:basis.value,start:start.value,end:end.value}});
   windowBox.replaceChildren(N('p',w.counts.within+' records in the date window · '+w.counts.outside+' outside · '+w.counts.unresolved+' with unresolved dates. These counts describe this selected evidence, not the whole chart.','scope-notice'));
   const unknown=N('input'),unknownLabel=N('label','Keep records with unresolved dates '),prepare=N('button','Prepare dated evidence'),scope=N('div');unknown.type='checkbox';unknown.checked=true;unknown.setAttribute('aria-label','Keep records with unresolved dates');unknownLabel.prepend(unknown);
   prepare.onclick=async()=>{prepare.disabled=true;try{
    const selected=await workspaceFetch('/api/stack/evidence-timing/select',{window_id:w.id,include_unresolved:unknown.checked}),context=await workspaceFetch('/api/stack/context?id='+encodeURIComponent(selected.context_id));
    scope.replaceChildren(N('p',selected.selection.retrieved_record_ids.length+' dated or explicitly retained roots · '+selected.linked_records_outside_selected_roots.length+' additional linked records. Linked evidence can fall outside the chosen window. Original questions remain unresolved.','scope-notice'),details('Selected roots, linked evidence and exclusions',selected));if(request.kind==='candidates'){const parent=await workspaceFetch('/api/stack/evidence-needs/candidates?id='+encodeURIComponent(request.id));addQuestionFollowup(scope,context,{receipt_id:parent.binding.receipt_id,need_id:parent.binding.need_id},'candidate_window',selected.id)}else addCaseSynthesis(scope,context);
   }catch(e){scope.replaceChildren(N('p',e.message,'missing'))}finally{prepare.disabled=false}};
   windowBox.append(unknownLabel,prepare,scope,details('Date membership and unresolved sources',w));
  }catch(e){windowBox.replaceChildren(N('p',e.message,'missing'))}finally{check.disabled=false}};
  result.append(sources,label,startLabel,endLabel,check,windowBox,details('Timing receipt and source dependencies',r));status.textContent=r.cache_hit?'Reused exact source-timing diagnostics.':'Source clocks inspected locally. No dates were rewritten.';
 }catch(e){status.textContent=e.message}finally{load.disabled=false}};
}
