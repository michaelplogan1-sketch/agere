/* Result presentation only. Values and source conclusions remain owned by the saved answer. */
const FIELD_ACRONYMS={id:'ID',ids:'IDs',pcp:'PCP',aco:'ACO',vbc:'VBC',oap:'OAP',gdmt:'GDMT',hfref:'HFrEF',ckd:'CKD',ef:'EF',egfr:'eGFR',mra:'MRA',rasi:'RASi',sglt2i:'SGLT2i',pmpm:'PMPM',ed:'ED',icu:'ICU',sql:'SQL',api:'API',usd:'USD'};
function fieldLabel(key,meta){
 if(meta?.label||meta?.display_name||meta?.title)return String(meta.label||meta.display_name||meta.title);
 const words=String(key).replace(/([a-z])([A-Z])/g,'$1 $2').replace(/[_-]/g,' ').split(/\s+/);
 return words.map((w,i)=>FIELD_ACRONYMS[w.toLowerCase()]||(i===0?w.charAt(0).toUpperCase()+w.slice(1):w)).join(' ');
}
function columnSpec(a){
 const supplied=a.table_columns||a.table_binding?.columns||[], metadata=a.column_metadata||a.field_metadata||{};
 const entries=Array.isArray(supplied)?supplied:Object.entries(supplied).map(([key,m])=>({key,...(typeof m==='object'?m:{label:m})}));
 const out=new Map();
 for(const item of entries){const key=typeof item==='string'?item:item.key||item.name||item.column;if(key)out.set(key,{...(metadata[key]||{}),...(typeof item==='object'?item:{})})}
 for(const key of cols(a.table||[]))if(!out.has(key))out.set(key,metadata[key]||{});
 return [...out].map(([key,meta])=>({key,meta,label:fieldLabel(key,meta)}));
}
function structuredValue(v){if(typeof v==='string'&&/^[\[{]/.test(v.trim())){try{return JSON.parse(v)}catch(_){}}return v}
function flagField(key,meta,rows){
 return meta?.type==='boolean'||meta?.data_type==='boolean'||/^(is_|has_|on_|was_|can_)/.test(key)&&rows.every(r=>r[key]==null||[0,1,true,false].includes(r[key]));
}
function displayValue(value,key,meta={},rows=[]){
 if(value==null)return '<span class="value-badge unknown">Unknown</span>';
 if([0,1,true,false].includes(value)&&(flagField(key,meta,rows)||typeof value==='boolean')){
  const set=value===true||value===1;
  return '<span class="value-badge '+(set?'set':'unset')+'" title="Original value: '+esc(String(value))+'">'+esc(set?(meta.true_label||'Flag set'):(meta.false_label||'Flag not set'))+' <small>('+esc(String(value))+')</small></span>';
 }
 const v=structuredValue(value);
 if(Array.isArray(v))return v.length?'<span class="value-badge">'+v.length+' '+(v.length===1?'entry':'entries')+'</span>':'<span class="value-empty">No entries returned</span>';
 if(v&&typeof v==='object'){const n=Object.keys(v).length;return '<span class="value-badge">'+n+' '+(n===1?'field':'fields')+'</span>'}
 if(isNum(v))return esc(String(v))+(meta.unit?' <span class="muted">'+esc(meta.unit)+'</span>':'');
 return esc(String(v).includes('_')?fieldLabel(v):String(v));
}
function recordName(row){const p=structuredValue(row.profile);return row.patient_name||row.display_name||p?.patient_name||p?.display_name||row.patient_id||row.name||row.id||''}
function recordDetail(value,key,meta,rows,depth=0){
 const v=structuredValue(value);
 if(v==null||typeof v!=='object')return displayValue(value,key,meta,rows);
 const entries=Array.isArray(v)?v.map((item,i)=>[String(i+1),item]):Object.entries(v);
 if(!entries.length)return '<span class="value-empty">No entries returned</span>';
 if(depth>3)return '<pre>'+esc(JSON.stringify(v,null,2))+'</pre>';
 return '<dl class="record-fields nested">'+entries.map(([k,val])=>'<div><dt>'+esc(Array.isArray(v)?'Entry '+k:fieldLabel(k))+'</dt><dd>'+recordDetail(val,k,{},[],depth+1)+'</dd></div>').join('')+'</dl>';
}
function rowDetails(row,spec,rows){return '<dl class="record-fields">'+spec.map(c=>'<div><dt>'+esc(c.label)+'<code>'+esc(c.key)+'</code></dt><dd>'+recordDetail(row[c.key],c.key,c.meta,rows)+'</dd></div>').join('')+'</dl>'}
function presentationTable(rows,a,opt={}){
 if(!rows.length)return '<p class="hint">No matching records. Change or clear the search.</p>';
 const spec=columnSpec({...a,table:a.table||rows}),all=opt.allColumns,defaultSpec=spec.filter(c=>!['profile','patient_name','display_name'].includes(c.key)&&rows.some(r=>r[c.key]==null||typeof structuredValue(r[c.key])!=='object')).slice(0,4);
 const selected=all?spec:defaultSpec, patient=spec.some(c=>c.key==='patient_id'),id='record-table-'+(++state.seq),page=Math.max(0,Math.min(opt.page||0,Math.ceil(rows.length/15)-1)),start=page*15;
 let h='<p class="data-reading-note">Unknown means no value was returned. Flags retain the calculation’s 0 / 1 values; a flag of 0 does not establish clinical absence. Expand a record for every field and original value.</p>';
 h+='<div class="tablewrap result-table'+(all?' all-columns':'')+'" tabindex="0" role="region" aria-label="Returned records'+(all?' · all columns':'')+'"><table><caption class="sr">'+rows.length+' matching records. '+(all?'All columns.':'Selected fields; expand each record for all fields.')+'</caption><thead><tr>'+(patient?'<th scope="col">Patient</th>':'')+selected.filter(c=>!patient||c.key!=='patient_id').map(c=>'<th scope="col" title="'+esc(c.key)+'">'+esc(c.label)+'</th>').join('')+'<th scope="col">Details</th></tr></thead><tbody>';
 rows.slice(start,start+15).forEach((r,i)=>{const rid=id+'-'+i;
  h+='<tr>'+(patient?'<th scope="row"><strong>'+esc(recordName(r))+'</strong><small class="record-id">'+esc(r.patient_id)+'</small></th>':'')+selected.filter(c=>!patient||c.key!=='patient_id').map(c=>'<td>'+displayValue(r[c.key],c.key,c.meta,a.table||rows)+'</td>').join('')+'<td><button class="btn small" type="button" data-record-toggle="'+rid+'" aria-expanded="false" aria-controls="'+rid+'">View details</button></td></tr><tr id="'+rid+'" class="record-expanded" hidden><td colspan="'+(selected.filter(c=>!patient||c.key!=='patient_id').length+(patient?2:1))+'"><h4>'+esc(recordName(r)||'Record '+(start+i+1))+' · all fields</h4>'+rowDetails(r,spec,a.table||rows)+'<details><summary>Original record · JSON</summary><pre>'+esc(JSON.stringify(r,null,2))+'</pre></details></td></tr>';
 });
 h+='</tbody></table></div><div class="result-pagination"><span>Showing '+(start+1)+'–'+Math.min(start+15,rows.length)+' of '+fmt(rows.length,0)+' records</span><div><button class="btn small" type="button" data-record-page="'+(page-1)+'"'+(page===0?' disabled':'')+'>Previous</button><button class="btn small" type="button" data-record-page="'+(page+1)+'"'+(start+15>=rows.length?' disabled':'')+'>Next</button></div></div>';
 return h;
}
function answerSummary(a,opt={}){
 const facts=Object.entries(a.facts||{}).filter(([k,v])=>typeof v==='string'&&v.trim()),rows=a.table||[],ids=a.patient_ids||[],unique=new Set(ids.length?ids:rows.map(r=>r.patient_id).filter(Boolean));
 const scalar=a.answer_type==='count'?a.count:a.answer_type==='value'?a.value:null;
 const count=scalar!=null&&isNum(scalar)?scalar:unique.size||rows.length;
 const unit=scalar!=null?(a.unit||(a.answer_type==='count'?'Count returned':'Computed value')):unique.size?'Patients in returned result':'Records returned';
 let h='';
 if(!opt.noMetric&&count!=null&&(rows.length||unique.size))h+='<section class="result-at-a-glance" aria-label="Result at a glance"><div class="result-number">'+esc(fmt(count,Number.isInteger(count)?0:4))+'</div><div><h3>'+esc(unit)+'</h3><p>'+esc(rows.length?fmt(rows.length,0)+' returned rows · '+columnSpec(a).length+' fields':'Native computed result')+'</p></div><button class="btn" type="button" data-open-answer-tab="data">Explore records <span aria-hidden="true">→</span></button></section>';
 if(facts.length)h+='<section class="answer-findings"><div class="section-label">KEY FINDINGS <span>From the saved answer</span></div><div class="finding-list">'+facts.slice(0,3).map(([k,v],i)=>'<article><span class="finding-index">'+String(i+1).padStart(2,'0')+'</span><div><h3>'+esc(fieldLabel(k))+'</h3><p>'+esc(v)+'</p></div></article>').join('')+'</div>'+(facts.length>3?'<details><summary>Read '+(facts.length-3)+' more findings</summary>'+facts.slice(3).map(([k,v])=>'<h4>'+esc(fieldLabel(k))+'</h4><p>'+esc(v)+'</p>').join('')+'</details>':'')+'</section>';
 return h;
}
function resultLimitations(a){
 const source=[...(a.missing||[]),...(a.limitations||[]),...(a.overall?.limitations||[]),...(a.warnings||[])],items=[...new Set(source.filter(x=>typeof x==='string'&&x.trim()))];
 if(!items.length&&a.verification!=='unverified')return '';
 return '<section class="result-limitations" role="note"><div class="section-label">'+(a.verification==='unverified'?'VERIFICATION INCOMPLETE':'LIMITS TO THIS ANSWER')+'</div>'+(a.verification==='unverified'?'<p class="verification-note">The saved answer has not passed complete verification. Review the evidence before using its conclusions.</p>':'')+'<ul>'+items.slice(0,2).map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>'+(items.length>2?'<details><summary>All limitations ('+items.length+')</summary><ul>'+items.slice(2).map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>':'')+'</section>';
}
function patientOverview(a){
 const rows=a.table||[];if(!rows.length)return '';
 return '<section class="record-preview"><div class="section-row"><div><h3>The people behind the result</h3><p>Preview of '+Math.min(3,rows.length)+' of '+fmt(rows.length,0)+' returned records. The data view includes every record and field.</p></div><button type="button" class="text-button" data-open-answer-tab="data">View all records →</button></div><div class="record-preview-grid">'+rows.slice(0,3).map(r=>'<article><span class="record-avatar" aria-hidden="true">'+esc(String(recordName(r)).split(/\s+/).map(s=>s[0]).slice(0,2).join(''))+'</span><button type="button" class="text-button" data-open-patient="'+esc(r.patient_id)+'">'+esc(recordName(r))+' ↗</button><span class="record-id">'+esc(r.patient_id)+'</span><span class="record-preview-note">Open patient dossier</span></article>').join('')+'</div></section>';
}
function sourceDetails(a){
 const ground=(a.grounding||[]),sources=(a.sources||[]);
 let h='<section class="method-intro"><h3>How to read this answer</h3><p>Results describe the returned data and the saved calculation. Review the definitions, evidence coverage and unresolved checks below.</p></section>';
 if(ground.length)h+='<details open><summary>Evidence used ('+ground.length+')</summary><ol class="source-list-text">'+ground.map(v=>'<li>'+esc(typeof v==='string'?v:JSON.stringify(v))+'</li>').join('')+'</ol></details>';
 if(sources.length)h+='<details open><summary>Sources ('+sources.length+')</summary><ul>'+sources.map(s=>'<li>'+esc(typeof s==='string'?s:s.title||s.label||JSON.stringify(s))+(s.url&&/^https?:\/\//.test(s.url)?' <a href="'+esc(s.url)+'" target="_blank" rel="noopener noreferrer">Open source ↗</a>':'')+'</li>').join('')+'</ul></details>';
 if(a.table_binding)h+='<details><summary>Returned table and native computation</summary><pre>'+esc(JSON.stringify(a.table_binding,null,2))+'</pre></details>';
 if(a.verification||a.investigation_review)h+='<details><summary>Verification record · '+esc(a.verification||a.investigation_review?.status||'not recorded')+'</summary><p>'+esc(a.investigation_review?.feedback||'')+'</p><pre>'+esc(JSON.stringify({verification:a.verification,review:a.investigation_review},null,2))+'</pre></details>';
 return h;
}

/* A decision brief reads only recorded results and available review-workflow metadata. */
function recordedActionGroups(a){
 const groups=new Map();
 for(const row of a.table||[]){const action=row?.suggested_action;if(typeof action!=='string'||!action.trim())continue;
  if(!groups.has(action))groups.set(action,{action,rows:[]});groups.get(action).rows.push(row);
 }
 return [...groups.values()];
}
function decisionState(t){
 const d=t.data||{},a=d.answer||{},c=d.action_context||{},groups=recordedActionGroups(a);
 const patient=a.answer_type==='patient_set'||!!a.patient_ids?.length||(a.table||[]).some(r=>r.patient_id!=null);
 const blocked=!!(t.archiveBlocked||c.stale||c.invalidated),uncertain=a.status!=='answered'||a.verification==='unverified'||!!a.missing?.length||!!c.source_review_required;
 return {a,c,groups,patient,blocked,uncertain,interrupted:d.verification_progress?.status==='interrupted',gaps:(a.missing||[]).filter(x=>typeof x==='string'&&x.trim())};
}
function briefIcon(kind){const paths={arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',evidence:'<path d="M14 3H5v18h14V8zM14 3v6h5M8 13h7M8 17h5"/>',options:'<path d="M4 7h16M8 3v8M4 17h16M16 13v8"/>',work:'<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16M15 4v16M6 9v3M12 8v7M18 11v4"/>',spark:'<path d="m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7z"/>'};return '<svg class="brief-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(paths[kind]||paths.arrow)+'</svg>'}
function decisionHero(t){
 const s=decisionState(t),a=s.a,rows=a.table||[],ids=new Set(a.patient_ids?.length?a.patient_ids:rows.map(r=>r.patient_id).filter(Boolean));
 const numeric=['count','patient_set'].includes(a.answer_type)?a.count:a.answer_type==='value'?a.value:null;
 const value=isNum(numeric)?numeric:ids.size||rows.length||null;
 const unit=isNum(numeric)?a.unit||(a.answer_type==='patient_set'?'patients selected':a.answer_type==='count'?'computed count':'computed value'):ids.size?'patients in this result':rows.length?'returned records':'No quantified result returned';
 const headline=String(a.headline||a.interpretation||'No narrative summary was returned.');
 const boundary=headline.search(/[.!?](?=\s+[A-Z0-9])/),intro=headline.length>290&&boundary>0?headline.slice(0,boundary+1):headline,rest=intro===headline?'':headline.slice(boundary+1).trim();
 const status=s.blocked?'Saved answer needs updating':s.interrupted?'Review paused · work saved':s.uncertain?'Review before action':s.groups.length?'Suggestions ready to inspect':'Choose a next step';
 return '<section class="decision-hero" aria-label="Decision brief"><div class="decision-story"><div class="brief-kicker">THE DECISION BRIEF <span>Evidence → understanding → next steps</span></div><h2>Your answer.<br><em>A way forward.</em></h2><div class="brief-answer"><span class="brief-answer-label">WHAT THE ANALYSIS FOUND</span><p>'+esc(intro)+'</p>'+(rest?'<details><summary>Read the complete answer</summary><p>'+esc(rest)+'</p></details>':'')+'</div></div><aside class="decision-compass"><div class="compass-top">'+briefIcon('spark')+'<span>YOUR NEXT MOVE</span></div><h3>'+esc(status)+'</h3><p>'+esc(s.blocked?'This saved result remains available. Start an updated analysis before reusing it for work.':s.interrupted?'Completed work remains available. See saved review progress below for unfinished checks and the recorded service dependency.':s.uncertain?'Inspect the findings and resolve the open questions before choosing a next action.':'Review the recorded suggestions and their prerequisites, then decide how to follow through.')+'</p><button class="btn primary" type="button" '+(s.blocked?'data-decision-draft="refresh"':s.interrupted?'data-open-answer-tab="overview"':'data-open-answer-tab="method"')+'>'+esc(s.blocked?'Draft updated question':s.interrupted?'Inspect saved progress':'Review the evidence')+' '+briefIcon('arrow')+'</button><div class="compass-metric"><strong>'+esc(value!=null?fmt(value,isNum(value)&&!Number.isInteger(value)?4:0):'—')+'</strong><div>'+esc(unit)+'<small>From the returned calculation</small></div></div><div class="compass-route" aria-hidden="true"><span></span><i></i><span></span><i></i><span></span></div><div class="compass-route-labels"><span>Evidence</span><span>Review</span><span>Next step</span></div></aside></section>';
}
function decisionActions(t){
 const s=decisionState(t),a=s.a,context=/^ANS-[0-9a-f]{24}$/.test(s.c.id||'')?s.c.id:null;
 const title=s.patient?'Interventions & next steps':'Actions & next steps';
 let h='<section class="decision-actions" id="decision-actions"><div class="decision-section-heading"><div><span class="brief-kicker">FROM INSIGHT TO ACTION</span><h3>'+title+'</h3></div><span class="decision-state-pill">'+(s.groups.length?'Recorded suggestions':'No '+(s.patient?'intervention':'action')+' plan returned')+'</span></div>';
 if(s.groups.length){h+='<p class="decision-section-note">These suggestions come directly from the returned records. Review who they concern, the evidence, and the prerequisites before choosing an action.</p><div class="suggestion-grid">'+s.groups.map((g,i)=>'<article class="suggestion-card"><span class="suggestion-mark">'+briefIcon('spark')+'</span><span class="suggestion-status">'+(s.uncertain?'Needs evidence review':'Suggested by the analysis')+'</span><h4>'+esc(fieldLabel(g.action))+'</h4><p>Recorded in '+fmt(g.rows.length,0)+' returned '+(g.rows.length===1?'row':'rows')+'. Suitability and execution prerequisites require review.</p><button type="button" class="btn" data-suggestion-index="'+i+'">Evaluate this option '+briefIcon('arrow')+'</button></article>').join('')+'</div>';
 }else{
  h+='<div class="action-gap"><span class="action-gap-icon">'+briefIcon('options')+'</span><div><h4>'+esc(s.uncertain?'Review comes first.':'The next decision is yours.')+'</h4><p>'+esc(s.patient?'This answer does not include a patient-specific intervention plan. Use its findings to review the evidence, then compare supported options.':'This answer does not include an action plan. Review the findings, ask for a supported comparison, or carry the analysis into a report.')+'</p></div></div>';
 }
 const programs=Object.entries(t.data?.review_programs||{});
 if(s.patient&&programs.length)h+='<div class="review-pathway-strip"><span>Available review pathways<small>Suitability unconfirmed</small></span><div>'+programs.map(([key,p])=>'<button type="button" data-review-workflows="'+esc(key)+'">'+esc(p.label||'Review workflow')+' <span aria-hidden="true">↗</span></button>').join('')+'</div></div>';
 h+='<div class="next-move-grid"><article class="next-move"><div class="move-top"><span>01 / UNDERSTAND</span>'+briefIcon('evidence')+'</div><h4>Resolve the open questions</h4><p>'+esc(s.gaps[0]||'Inspect the definitions, source coverage and recorded limitations behind this answer.')+'</p><button type="button" class="text-button" data-open-answer-tab="method">Inspect evidence '+briefIcon('arrow')+'</button></article><article class="next-move"><div class="move-top"><span>02 / DECIDE</span>'+briefIcon('options')+'</div><h4>'+esc(s.blocked?'Update the analysis first':'Compare supported options')+'</h4><p>'+esc(s.blocked?(s.patient?'Draft a new warehouse question using the original wording. This saved patient set cannot be reused.':'Draft a new warehouse question using the original wording. The saved scope cannot be reused.'):'Ask which options the evidence supports, who they concern, and what remains unknown. Your chosen scope stays visible.')+'</p><button type="button" class="text-button" data-decision-draft="'+(s.blocked?'refresh':'options')+'">'+(s.blocked?'Draft updated question':'Draft a follow-up')+' '+briefIcon('arrow')+'</button></article><article class="next-move"><div class="move-top"><span>03 / FOLLOW THROUGH</span>'+briefIcon('work')+'</div><h4>'+esc(s.patient?'Give the next step an owner':'Make the answer useful')+'</h4><p>'+esc(!s.patient?'Prepare a report or inspect follow-through in the saved workspace. A patient group has not been selected for program work.':s.blocked?'Review workflows are available to inspect. New work stays unavailable until the answer is updated.':'Explore available review workflows and their requirements. Record an owner and next step in the saved workspace.')+'</p>'+(context?(s.patient?'<button type="button" class="text-button" data-review-workflows>Explore review workflows '+briefIcon('arrow')+'</button>':'<a class="text-button" href="/action-center?context='+encodeURIComponent(context)+'#studio">Build a report '+briefIcon('arrow')+'</a>'):'<span class="move-unavailable">A saved answer is needed for review work.</span>')+'</article></div>';
 h+='<p class="decision-footnote">'+(s.patient?'Program availability does not establish patient eligibility. ':'')+'Buttons here open evidence, draft a question, or inspect workflows. They do not execute care or outreach.</p></section>';
 return h+'<dialog id="decision-dialog" class="decision-dialog" aria-labelledby="decision-dialog-title"><button type="button" class="decision-dialog-close" data-close-decision-dialog aria-label="Close next-step details">×</button><div id="decision-dialog-body"></div></dialog>';
}
function suggestionDetails(t,index){
 const g=recordedActionGroups(t.data?.answer||{})[index];if(!g)return '';
 return '<span class="brief-kicker">RECORDED SUGGESTION</span><h2 id="decision-dialog-title">'+esc(fieldLabel(g.action))+'</h2><p class="decision-dialog-intro">Recorded for '+g.rows.length+' returned '+(g.rows.length===1?'row':'rows')+'. Review the supporting observations before deciding whether this option applies.</p>'+g.rows.map(r=>'<article class="option-record"><h3>'+esc(recordName(r)||'Returned record')+'</h3><p>'+esc(r.reason_detail||'No per-record explanation was returned with this suggestion.')+'</p><details><summary>Inspect this source record</summary><pre>'+esc(JSON.stringify(r,null,2))+'</pre></details></article>').join('');
}
function workflowDetails(t,programs,selected){
 const s=decisionState(t),choice=selected&&programs?.[selected],entries=choice?[[selected,choice]]:Object.entries(programs||{}),context=s.c.id;
 return '<span class="brief-kicker">AVAILABLE REVIEW WORKFLOWS</span><h2 id="decision-dialog-title">'+(choice?esc(choice.label||fieldLabel(selected)):'Choose the work,<br><em>then check the fit.</em>')+'</h2><p class="decision-dialog-intro">These are the application’s available review workflows. Their availability does not mean they were recommended for this result.</p>'+(entries.length?'<div class="workflow-options">'+entries.map(([key,p])=>'<article><h3>'+esc(p.label||fieldLabel(key))+'</h3><ul>'+(p.requirements||[]).map(v=>'<li>'+esc(v)+'</li>').join('')+'</ul><span class="workflow-availability">'+esc(s.blocked?'Update this saved answer before creating work.':s.uncertain?'Resolve evidence and suitability requirements before program work.':'Check requirements in the review workspace.')+'</span></article>').join('')+'</div>':'<p class="hint">Workflow details are unavailable in this response. Open the saved workspace to inspect its current options.</p>')+(choice?'<button class="text-button all-review-workflows" type="button" data-review-workflows>View all review workflows →</button>':'')+'<a class="btn primary" href="/action-center?context='+encodeURIComponent(context)+'#cohort">Open saved review workspace '+briefIcon('arrow')+'</a>';
}
