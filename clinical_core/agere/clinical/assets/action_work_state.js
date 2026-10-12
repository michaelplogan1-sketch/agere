// One operational view of independent evidence layers; current never means approved.
function addActionWorkState(box,task,actor,onRefresh){
 const area=N('details'),body=N('div');area.append(N('summary','Evidence and work status'),body);box.append(area);let loaded=false;
 area.ontoggle=async()=>{if(!area.open||loaded)return;loaded=true;try{const r=await workspaceFetch('/api/stack/work-state?id='+encodeURIComponent(task.id));body.replaceChildren(N('h4',r.packet_matches_recorded_dependencies?'Internal packet matches its recorded dependencies':'Internal evidence packet needs attention'),N('p','A current packet does not establish clinical approval, covered benefits or service delivery.','meta'));
 for(const [name,layer]of Object.entries(r.layers)){const row=N('div',undefined,'action-work-layer');row.append(N('h4',human(name)),N('p',human(layer.state),'pill'),N('p',layer.reason,'meta'));if(layer.counts_by_route)row.append(N('p',Object.entries(layer.counts_by_route).map(([state,n])=>n+' '+human(state)).join(' · ')+' (requirements counted per route)','meta'));body.append(row)}
 for(const step of r.next_steps)body.append(N('p',step));const refresh=N('button','Refresh matching evidence'),status=N('p','','meta');refresh.disabled=!r.local_matching_refresh_available;body.append(refresh,status);if(r.refresh_unavailable_reason)body.append(N('p',r.refresh_unavailable_reason,'missing'));
 refresh.onclick=async()=>{refresh.disabled=true;try{await workspaceFetch('/api/stack/work-state/refresh',{task_id:task.id,expected_revision:r.task_revision,actor:actor()});await onRefresh()}catch(e){status.textContent=e.message;refresh.disabled=false}};
 }catch(e){body.replaceChildren(N('p',e.message,'missing'));loaded=false}};
}
