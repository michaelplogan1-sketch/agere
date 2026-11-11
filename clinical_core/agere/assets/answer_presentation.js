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
function answerSummary(a){
 const facts=Object.entries(a.facts||{}).filter(([k,v])=>typeof v==='string'&&v.trim()),rows=a.table||[],ids=a.patient_ids||[],unique=new Set(ids.length?ids:rows.map(r=>r.patient_id).filter(Boolean));
 const scalar=a.answer_type==='count'?a.count:a.answer_type==='value'?a.value:null;
 const count=scalar!=null&&isNum(scalar)?scalar:unique.size||rows.length;
 const unit=scalar!=null?(a.unit||(a.answer_type==='count'?'Count returned':'Computed value')):unique.size?'Patients in returned result':'Records returned';
 let h='';
 if(count!=null&&(rows.length||unique.size))h+='<section class="result-at-a-glance" aria-label="Result at a glance"><div class="result-number">'+esc(fmt(count,Number.isInteger(count)?0:4))+'</div><div><h3>'+esc(unit)+'</h3><p>'+esc(rows.length?fmt(rows.length,0)+' returned rows · '+columnSpec(a).length+' fields':'Native computed result')+'</p></div><button class="btn" type="button" data-open-answer-tab="data">Explore records <span aria-hidden="true">→</span></button></section>';
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
 return '<section class="record-preview"><div class="section-row"><div><h3>The people behind the result</h3><p>Preview of '+Math.min(3,rows.length)+' of '+fmt(rows.length,0)+' returned records. The data view includes every record and field.</p></div><button type="button" class="text-button" data-open-answer-tab="data">View all records →</button></div><div class="record-preview-grid">'+rows.slice(0,3).map(r=>'<article><span class="record-avatar" aria-hidden="true">'+esc(String(recordName(r)).split(/\s+/).map(s=>s[0]).slice(0,2).join(''))+'</span><strong>'+esc(recordName(r))+'</strong><span class="record-id">'+esc(r.patient_id)+'</span><span class="record-preview-note">Source details available</span></article>').join('')+'</div></section>';
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
