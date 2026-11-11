"""Agere workspace (/ask): conversation thread, answer card with the right visual per answer type, briefing and economics.

One self-contained HTML page (no external assets; charts are hand-written SVG). It reads only existing routes:
/api/stack/compose (with conversation_id), /api/stack/conversation, /api/stack/briefing, /api/stack/economics and
/api/stack/correct, /api/stack/learning-loop (read-only loop summary pill), plus /api/stack/briefing-feedback (opened / asked follow-up / dismissed per briefing card,
the briefing ranker's implicit feedback). /ask?demo=1 adds the scripted buyer walkthrough in DEMO_STEPS; it follows one saved contract analysis.
"""
import json
from pathlib import Path

# The first synthetic release follows one versioned input and one saved answer.
# Navigating buyer/evidence views never starts another analytical request.
DEMO_STEPS = [
    {'id': 'input', 'kind': 'input', 'label': 'Choose contract inputs',
     'note': 'Choose the versioned synthetic terms in the conversation, or open an existing contract answer. No payer terms or attribution are guessed.'},
    {'id': 'provider', 'kind': 'view', 'label': 'Population health view', 'section': 'provider',
     'note': 'Native cost, member-months and approximate quality share one attributed population and period. Review needs are separate from clinical eligibility.'},
    {'id': 'payer', 'kind': 'view', 'label': 'Payer view', 'section': 'payer',
     'note': 'Inspect the other buyer view of the same calculation. Scenario variance is not realized savings, cash or payment entitlement.'},
    {'id': 'drivers', 'kind': 'view', 'label': 'Trace the drivers', 'section': 'practices',
     'note': 'Open practice contributions, monthly exposure and the complete member list. These describe recorded data; they do not establish causation.'},
    {'id': 'inputs', 'kind': 'view', 'label': 'Inspect and revise assumptions', 'section': 'assumptions',
     'note': 'The editor saves a versioned change only when you submit it. Recalculation checks current inputs; older answers and work retain their original bindings.'},
    {'id': 'work', 'kind': 'work', 'label': 'Continue into saved work',
     'note': 'Open the saved answer’s action workspace for evidence and internal review follow-up. No outreach or care is executed by this walkthrough.'},
]

PAGE = r"""__EXPLORE_SHELL__
<script id="demo-script" type="application/json">__DEMO_STEPS__</script>
<script>
"use strict";
const DEMO=JSON.parse(document.getElementById('demo-script').textContent);
const EXAMPLES=["How many ED visits among patients over 65 in 2029 by payer?","Count patients with diabetes and without CKD by PCP",
 "Average length of stay for admissions in 2029 by practice","Count claims where ever_denied = 1 in 2029 by payer",
 "Count appointments where kept = 0 by PCP","Count patients where coverage = 'medicare' by practice"];
const ROUTES={shared_planner:'Shared request planner',local_compiler:'Agere compiler',local_unsupported:'Agere compiler (unsupported)',local_requires_input:'Agere compiler (needs your inputs)',local_model_rewrite_compiled:'Local rewrite → compiler',
 hosted_verified_sql:'Hosted models, cross-verified',learned_verified_sql:'Learned (previously verified)',hosted_missing_data:'Hosted: data not available',frontier_escalation:'Frontier model',
 semantic_cache:'Verified semantic reuse',exact_question_cache:'Exact question reuse',cohort_family:'Cohort SQL family',dq_family:'Data-quality SQL family',vbc_family:'VBC SQL family',
 metric_router:'Governed metric',operator_router:'Analytical operator',bottled_sql:'Local SQL model',economical_composition_local_execution:'Specialist composition'};
const $=(s,el)=>(el||document).querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const isNum=v=>typeof v==='number'&&isFinite(v);
const fmt=(v,d)=>{if(!isNum(v))return v==null?'—':String(v);const a=Math.abs(v);d=d==null?(a>=1000?0:a>=10?1:a>=1?2:3):d;return v.toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:0})};
const usdK=v=>!isNum(v)?'—':Math.abs(v)>=1e6?(v<0?'−':'')+'$'+fmt(Math.abs(v)/1e6,1)+'M':Math.abs(v)>=1e3?(v<0?'−':'')+'$'+fmt(Math.abs(v)/1e3,0)+'k':(v<0?'−$':'$')+fmt(Math.abs(v),0);
const usd=v=>isNum(v)?(v<0?'−$':'$')+Math.abs(v).toLocaleString(undefined,{maximumFractionDigits:0}):'—';
const pct=(v,d)=>isNum(v)?(100*v).toFixed(d==null?1:d)+'%':'—';
const label=k=>fieldLabel(k);
const store={get(k){try{return sessionStorage.getItem(k)}catch(_){return null}},set(k,v){try{v==null?sessionStorage.removeItem(k):sessionStorage.setItem(k,v)}catch(_){}}};
const newId=()=>(window.crypto&&crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random().toString(16).slice(2)).replace(/[^A-Za-z0-9_-]/g,'');
const state={cid:store.get('agere-conversation'),turns:[],active:-1,busy:false,csv:{},seq:0,briefing:null,outFilter:null};

/* ---------- SVG charts (hand-written; colours from CSS tokens) ---------- */
function CW(){const b=document.getElementById('answer');const w=b?b.clientWidth-26:640;return Math.max(300,Math.min(760,Math.round(w||640)))}
const SER=['var(--s1)','var(--s2)','var(--s3)','var(--s4)','var(--s5)','var(--s6)'];
function ticks(lo,hi,n){if(!(hi>lo)){hi=lo+1}const span=hi-lo,step0=Math.pow(10,Math.floor(Math.log10(span/n))),err=span/n/step0;
 const step=step0*(err>=7.5?10:err>=3.5?5:err>=1.5?2:1);const out=[];for(let v=Math.ceil(lo/step)*step;v<=hi+step*1e-9;v+=step)out.push(+v.toFixed(10));return out}
function svgOpen(w,h,title){const id='t'+(++state.seq);return '<svg class="chart" viewBox="0 0 '+w+' '+h+'" role="img" aria-labelledby="'+id+'"><title id="'+id+'">'+esc(title)+'</title>'}
/* horizontal bars; rows: {label, value, ask?, pick?, note?, value2?}; two series when value2 present */
function hbars(rows,opt){opt=opt||{};const W=CW(),lh=opt.value2!=null?30:24,padL=Math.min(Math.round(W*.36),10+7*Math.max(4,...rows.map(r=>String(r.label).length))),padR=70,top=8;
 const H=top+rows.length*lh+26;const vals=rows.flatMap(r=>[r.value,r.value2]).filter(isNum);let lo=Math.min(0,...vals),hi=Math.max(0,...vals);if(hi===lo)hi=lo+1;
 const x=v=>padL+(v-lo)/(hi-lo)*(W-padL-padR);let s=svgOpen(W,H,opt.title||'Bar chart');
 ticks(lo,hi,W<480?3:5).forEach(t=>{s+='<line class="gridl" x1="'+x(t)+'" x2="'+x(t)+'" y1="'+top+'" y2="'+(H-22)+'"/><text x="'+x(t)+'" y="'+(H-8)+'" text-anchor="middle">'+esc((opt.tick||fmt)(t))+'</text>'});
 rows.forEach((r,i)=>{const y=top+i*lh,act=r.ask?' data-ask="'+esc(r.ask)+'" tabindex="0" role="button" aria-label="'+esc(r.label+': '+(opt.fmt||fmt)(r.value)+'. Ask: '+r.ask)+'"':r.pick!=null?' data-pick="'+esc(r.pick)+'" tabindex="0" role="button" aria-label="'+esc(r.label+': '+(opt.fmt||fmt)(r.value))+'"':'';
  s+='<g'+act+'><text x="'+(padL-6)+'" y="'+(y+lh/2+4)+'" text-anchor="end" style="fill:var(--fg)">'+esc((n=>String(r.label).length>n?String(r.label).slice(0,n-1)+'…':r.label)(Math.max(6,Math.floor((padL-12)/6.6))))+'</text>';
  const bh=opt.value2!=null?(lh-8)/2:lh-8;
  if(isNum(r.value))s+='<rect x="'+Math.min(x(0),x(r.value))+'" y="'+(y+3)+'" width="'+Math.max(1,Math.abs(x(r.value)-x(0)))+'" height="'+bh+'" rx="3" style="fill:'+(r.color||SER[0])+'"/>';
  if(opt.value2!=null&&isNum(r.value2))s+='<rect x="'+Math.min(x(0),x(r.value2))+'" y="'+(y+4+bh)+'" width="'+Math.max(1,Math.abs(x(r.value2)-x(0)))+'" height="'+bh+'" rx="3" style="fill:'+SER[1]+'"/>';
  s+='<text x="'+(Math.max(x(0),x(isNum(r.value)?r.value:0))+4)+'" y="'+(y+3+bh-2)+'" style="fill:var(--fg)">'+esc((opt.fmt||fmt)(r.value))+'</text>';
  if(opt.value2!=null&&isNum(r.value2))s+='<text x="'+(Math.max(x(0),x(r.value2))+4)+'" y="'+(y+4+2*bh-2)+'">'+esc((opt.fmt||fmt)(r.value2))+'</text>';
  s+='<rect x="0" y="'+y+'" width="'+W+'" height="'+lh+'" style="fill:transparent"/></g>'});
 s+='<line class="axis" x1="'+x(0)+'" x2="'+x(0)+'" y1="'+top+'" y2="'+(H-22)+'"/>';
 if(isNum(opt.ref))s+='<line x1="'+x(opt.ref)+'" x2="'+x(opt.ref)+'" y1="'+top+'" y2="'+(H-22)+'" style="stroke:var(--fg);stroke-dasharray:4 3"/><text x="'+x(opt.ref)+'" y="'+(top+8)+'" dx="4">'+esc(opt.refLabel||'')+'</text>';
 s+='</svg>';
 const leg=opt.value2!=null?'<div class="legend"><span><span class="sw" style="background:'+SER[0]+'"></span>'+esc(opt.name1||'')+'</span><span><span class="sw" style="background:'+SER[1]+'"></span>'+esc(opt.name2||'')+'</span></div>':'';
 return leg+s}
/* line/step chart; series: {name, pts:[[x,y]], step?, dash?}; x numeric or category index */
function lines(series,opt){opt=opt||{};const W=CW(),H=opt.h||(W<480?230:280),L=58,R=16,T=12,B=40;const all=series.flatMap(s=>s.pts).filter(p=>isNum(p[1]));if(!all.length)return '<p class="hint">No plottable points.</p>';
 const xs=all.map(p=>p[0]),ys=all.map(p=>p[1]).concat(opt.extraY||[]);let x0=Math.min(...xs),x1=Math.max(...xs);if(x1===x0)x1=x0+1;
 let y0=opt.y0!=null?opt.y0:Math.min(...ys),y1=opt.y1!=null?opt.y1:Math.max(...ys);if(y1===y0){y1=y0+1}const pad=(y1-y0)*.06;if(opt.y0==null)y0-=pad;if(opt.y1==null)y1+=pad;
 const X=v=>L+(v-x0)/(x1-x0)*(W-L-R),Y=v=>T+(1-(v-y0)/(y1-y0))*(H-T-B);let s=svgOpen(W,H,opt.title||'Line chart');
 ticks(y0,y1,5).forEach(t=>{if(t<y0-1e-9||t>y1+1e-9)return;s+='<line class="gridl" x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(t)+'" y2="'+Y(t)+'"/><text x="'+(L-6)+'" y="'+(Y(t)+4)+'" text-anchor="end">'+esc((opt.ytick||fmt)(t))+'</text>'});
 const xt=opt.cats?opt.cats.map((c,i)=>[i,c]).filter((_,i,a)=>i%Math.ceil(a.length/(W<480?4:8))===0):ticks(x0,x1,6).map(t=>[t,(opt.xtick||fmt)(t)]);
 xt.forEach(([v,t])=>{s+='<text x="'+X(v)+'" y="'+(H-B+16)+'" text-anchor="middle">'+esc(t)+'</text>'});
 s+='<line class="axis" x1="'+L+'" x2="'+(W-R)+'" y1="'+(H-B)+'" y2="'+(H-B)+'"/>';
 if(opt.xlabel)s+='<text x="'+((L+W-R)/2)+'" y="'+(H-4)+'" text-anchor="middle">'+esc(opt.xlabel)+'</text>';
 if(opt.ylabel)s+='<text x="12" y="'+((T+H-B)/2)+'" text-anchor="middle" transform="rotate(-90 12 '+((T+H-B)/2)+')">'+esc(opt.ylabel)+'</text>';
 if(opt.under)s+=opt.under(X,Y);
 series.forEach((se,i)=>{if(se.hidden)return;let d='',prev=null;se.pts.forEach(p=>{if(!isNum(p[1])){prev=null;return}
   if(prev===null)d+='M'+X(p[0])+' '+Y(p[1]);else if(se.step)d+='H'+X(p[0])+'V'+Y(p[1]);else d+='L'+X(p[0])+' '+Y(p[1]);prev=p});
  s+='<path d="'+d+'" fill="none" style="stroke:'+(se.color||SER[i%SER.length])+';stroke-width:2.2'+(se.dash?';stroke-dasharray:5 4':'')+'"/>'});
 if(opt.over)s+=opt.over(X,Y);return s+'</svg>'}
function legend(series,key){return '<div class="legend" data-legend="'+key+'">'+series.map((s,i)=>'<button type="button" aria-pressed="'+(!s.hidden)+'" data-series="'+i+'"><span class="sw" style="background:'+(s.color||SER[i%SER.length])+'"></span>'+esc(s.name)+'</button>').join('')+'</div>'}
/* interval display: rows {label, est, lo, hi, note, main} on a shared axis with a zero line */
function intervals(rows,opt){opt=opt||{};const W=CW(),lh=40,L=Math.min(Math.round(W*.42),12+7*Math.max(...rows.map(r=>r.label.length))),R=24,T=8,H=T+rows.length*lh+28;
 const vs=rows.flatMap(r=>[r.est,r.lo,r.hi]).filter(isNum).concat([0]);let lo=Math.min(...vs),hi=Math.max(...vs);const p=(hi-lo)*.08||1;lo-=p;hi+=p;
 const X=v=>L+(v-lo)/(hi-lo)*(W-L-R);let s=svgOpen(W,H,opt.title||'Estimates with 95% intervals');
 ticks(lo,hi,W<480?3:5).forEach(t=>{s+='<line class="gridl" x1="'+X(t)+'" x2="'+X(t)+'" y1="'+T+'" y2="'+(H-24)+'"/><text x="'+X(t)+'" y="'+(H-8)+'" text-anchor="middle">'+esc((opt.tick||fmt)(t))+'</text>'});
 s+='<line x1="'+X(0)+'" x2="'+X(0)+'" y1="'+T+'" y2="'+(H-24)+'" style="stroke:var(--muted);stroke-dasharray:3 3"/>';
 rows.forEach((r,i)=>{const y=T+i*lh+lh/2,c=r.main?'var(--s1)':'var(--s3)';const fit=r.label.length*6.6<=L-10;s+='<text x="'+(fit?L-8:4)+'" y="'+(fit?y+4:y-11)+'" text-anchor="'+(fit?'end':'start')+'" style="fill:var(--fg)'+(r.main?';font-weight:700':'')+'">'+esc(r.label)+'</text>';
  if(isNum(r.lo)&&isNum(r.hi))s+='<line x1="'+X(r.lo)+'" x2="'+X(r.hi)+'" y1="'+y+'" y2="'+y+'" style="stroke:'+c+';stroke-width:'+(r.main?4:2.5)+';stroke-linecap:round"/>';
  if(isNum(r.est))s+='<circle cx="'+X(r.est)+'" cy="'+y+'" r="'+(r.main?7:5)+'" style="fill:'+c+';stroke:var(--card);stroke-width:2"/>';
  if(r.note){const nx=isNum(r.est)?X(r.est):X(0);s+='<text x="'+nx+'" y="'+(fit?y-10:y+18)+'" text-anchor="'+(nx>W*.75?'end':nx<L+40?'start':'middle')+'">'+esc(r.note)+'</text>'}});
 return s+'</svg>'}

/* ---------- tables + CSV ---------- */
function cols(rows,skip){const seen=[];rows.forEach(r=>Object.keys(r||{}).forEach(k=>{if(!seen.includes(k)&&!(skip||[]).includes(k))seen.push(k)}));return seen}
function cell(v){if(v==null)return '';if(Array.isArray(v))return v.map(x=>typeof x==='object'?JSON.stringify(x):x).join(', ');if(typeof v==='object')return JSON.stringify(v);return v}
function tableHTML(rows,opt){opt=opt||{};if(!rows||!rows.length)return '';const id='csv'+(++state.seq);state.csv[id]={rows,name:opt.name||'agere-table'};
 const cs=opt.cols||cols(rows,opt.skip);const lim=opt.limit||200;
 let h='<div class="tools"><button type="button" class="btn small" data-csv="'+id+'">Download CSV ('+rows.length+' rows)</button>'+(opt.caption?'<span class="muted" style="font-size:13px">'+opt.caption+'</span>':'')+'</div>';
 h+='<div class="tablewrap" tabindex="0" role="region" aria-label="'+esc(opt.name||'table')+'"><table><thead><tr>'+cs.map(c=>'<th scope="col">'+esc(opt.head&&opt.head[c]||label(c))+'</th>').join('')+'</tr></thead><tbody>';
 rows.slice(0,lim).forEach((r,i)=>{const a=opt.ask?opt.ask(r,i):null,p=opt.pick?opt.pick(r,i):null;
  h+='<tr'+(a?' data-ask="'+esc(a)+'" tabindex="0" title="Ask: '+esc(a)+'"':p!=null?' data-pick="'+esc(p)+'" tabindex="0"':'')+'>'+cs.map(c=>{const v=r[c];
   if(opt.render&&opt.render[c])return '<td class="wrap">'+opt.render[c](v,r)+'</td>';const long=typeof v==='string'&&v.length>40;return '<td'+(isNum(v)?' class="n"':long?' class="wrap"':'')+'>'+esc(isNum(v)?fmt(v,Math.abs(v)<1&&v!==0?4:2):cell(v))+'</td>'}).join('')+'</tr>'});
 h+='</tbody></table></div>';if(rows.length>lim)h+='<p class="hint">Showing '+lim+' of '+rows.length+' rows; the CSV has all of them.</p>';return h}
function csvButton(rows,name){const id='csv'+(++state.seq);state.csv[id]={rows,name};return '<div class="tools"><button type="button" class="btn small" data-csv="'+id+'">Download CSV ('+rows.length+' rows)</button></div>'}
function downloadCSV(id){const t=state.csv[id];if(!t)return;const cs=cols(t.rows);const q=v=>{const s=String(cell(v));return /[",\n\r]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s};
 const text=[cs.map(q).join(',')].concat(t.rows.map(r=>cs.map(c=>q(r[c])).join(','))).join('\r\n');
 const url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=t.name.replace(/[^A-Za-z0-9_-]+/g,'-').slice(0,60)+'.csv';
 document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}

/* ---------- drill-down wording ---------- */
const PAYER=/^(medicare|medicaid|commercial|uninsured)$/i;
function drill(g){g=String(g);if(PAYER.test(g))return 'only '+g[0].toUpperCase()+g.slice(1).toLowerCase();if(/^\d{4}$/.test(g))return 'in '+g;return 'show the patients in '+g}
const GROUP_KEYS=['group','segment','practice','pcp','payer','coverage_type','facility','organization_id','drg_title','age_band','sex','label','factor','month','quarter','year'];
/* observed allowed PMPM leads; the completed (IBNR) figure only when the question asks for completion */
const COMPLETION=/complet|ibnr|run[- ]?out|incurred but not|lag[- ]adjust/i;
const METRICS=['allowed_pmpm','allowed_pmpm_completed','oe_ratio','ed_per_1000_shrunk','shrunk_rate','rate','readmission_rate','risk_ratio','probability','mean_risk_score','value','change','members','patient_count','patients','total_cents','count'];
function pickMetric(rows,question){const f=rows[0]||{};const order=COMPLETION.test(question||'')?['allowed_pmpm_completed',...METRICS]:METRICS;const m=order.find(k=>isNum(f[k]));if(m)return m;
 const meas=Object.keys(f).filter(k=>isNum(f[k])&&/avg|average|mean|median|rate|pct|percent|share|per_1000|days|usd|dollars|_cents|ratio/.test(k));return meas.length?meas[meas.length-1]:Object.keys(f).find(k=>isNum(f[k]))}
const isTime=v=>/^\d{4}(-\d{2}|-Q[1-4]|Q[1-4])?$/.test(String(v));

/* ---------- answer-type visuals ---------- */
function vizSurvival(a){const o=a.overall||{},curves=o.curves||{};const names=Object.keys(curves);if(!names.length)return '';
 const series=names.map(n=>({name:n,step:true,pts:[[0,0]].concat((curves[n]||[]).map(p=>[p.day,isNum(p.survival)?1-p.survival:null]))}));
 const key='surv'+(++state.seq);state[key]={series,ev:o.event_label,tz:o.time_zero};
 let h='<section class="sec" data-focus="curve"><h3>Cumulative incidence of '+esc(o.event_label||'the event')+' after '+esc(o.time_zero||'time zero')+'</h3>'+legend(series,key)+
  '<div data-chart="'+key+'">'+survChart(state[key])+'</div>';
 const lr=o.logrank;h+='<div class="kpis" style="margin-top:8px">'+[['Units',fmt(o.units)],['Patients',fmt(o.patients)],['Events',fmt(o.events)],
  ['Log-rank p',lr&&isNum(lr.p_value)?(lr.p_value<.001?'<0.001':lr.p_value.toFixed(3)):'—']].map(k=>'<div class="kpi"><div class="l">'+k[0]+'</div><div class="v">'+k[1]+'</div></div>').join('')+'</div>';
 if(o.excluded&&Object.keys(o.excluded).length)h+='<p class="hint">Excluded: '+esc(Object.entries(o.excluded).map(([k,v])=>label(k)+' '+v).join('; '))+'</p>';
 if(o.groups&&o.groups.length)h+=tableHTML(o.groups,{name:'survival-groups',skip:['censoring'],caption:'Per group: units, events, censoring, median time'});
 if(o.cox&&o.cox.coefficients)h+=tableHTML(o.cox.coefficients,{name:'cox-model',caption:'Cox model coefficients'});
 return h+'</section>'}
function survChart(st){return lines(st.series,{title:'Cumulative incidence by day',y0:0,ytick:v=>pct(v,0),xlabel:'days after '+(st.tz||'time zero'),ylabel:'cumulative incidence'})}
function vizMonitor(a){const charts=(a.overall||{}).charts||[];let h='';
 if(charts.length){const key='mon'+(++state.seq);state[key]={charts,i:0};
  h+='<section class="sec" data-focus="monitor"><h3>Control chart</h3><label class="sr" for="'+key+'s">Chart</label><select id="'+key+'s" data-monsel="'+key+'" class="btn small" style="max-width:100%">'+
   charts.map((c,i)=>'<option value="'+i+'">'+esc((c.status==='rate_signal'?'● ':c.status==='data_feed_anomaly'?'◆ ':'')+(c.group==='all'?'Overall':c.group)+' — '+c.label+' ('+label(c.status)+')')+'</option>').join('')+'</select>'+
   '<div data-chart="'+key+'">'+controlChart(charts[0])+'</div><div class="legend"><span><span class="sw" style="background:var(--s1)"></span>observed</span><span><span class="sw" style="background:var(--band);height:10px"></span>3-sigma limits</span><span><span class="sw" style="background:var(--bad);height:8px;width:8px;border-radius:50%"></span>rate signal</span><span><span class="sw" style="background:var(--warn);height:8px;width:8px"></span>data-feed anomaly</span></div></section>'}
 const rows=a.table||[];if(rows.length){const isStatus='status' in rows[0]&&'series' in rows[0]&&!('kind' in rows[0]);
  h+='<section class="sec"><h3>'+(isStatus?'Every chart examined':'Findings')+'</h3>'+tableHTML(rows,{name:'monitor-findings',skip:['echo'],
   ask:r=>r.follow_up||null,pick:(r,i)=>isStatus&&charts.length?charts.findIndex(c=>c.series===r.series&&c.group===r.group&&c.group_by===r.group_by):null,
   caption:isStatus?'Select a row to show its chart.':'Select a finding to ask its follow-up.'})+'</section>'}
 return h}
function vizDemand(a){const o=a.overall||{},all=(o.demand||[]);const ok=all.filter(s=>s.status==='forecast');let h='';
 if(ok.length){const key='dem'+(++state.seq);state[key]={series:ok,o};
  h+='<section class="sec" data-focus="forecast"><h3>'+esc(label(o.measure||'volume'))+' per '+esc(o.frequency||'period')+': history and forecast</h3>'+
   (ok.length>1?'<label class="sr" for="'+key+'s">Series</label><select id="'+key+'s" data-demsel="'+key+'" class="btn small" style="max-width:100%">'+ok.map((s,i)=>'<option value="'+i+'">'+esc(s.group==='all'?'Overall':s.group)+'</option>').join('')+'</select>':'')+
   '<div data-chart="'+key+'">'+fanChart(ok[0],o)+'</div><div class="legend"><span><span class="sw" style="background:var(--s1)"></span>observed (fitted regime)</span><span><span class="sw" style="background:var(--muted)"></span>observed (not fitted)</span>'+
   '<span><span class="sw" style="background:var(--s2)"></span>forecast</span><span><span class="sw" style="background:var(--band);height:10px"></span>80% / 95% interval</span><span><span class="sw" style="background:var(--warn);height:8px;width:8px"></span>gap or spike excluded</span></div>';
  const s=ok[0],bt=s.backtest||{},c80=(bt.coverage||{})['80']||{},c95=(bt.coverage||{})['95']||{};
  h+='<div class="kpis" style="margin-top:8px">'+[['Model',label(s.model||'')],['Backtest MASE',fmt(bt.selected_mase)],[label(bt.baseline||'naive')+' MASE',fmt(bt.baseline_mase)],
   ['Skill vs baseline',isNum(bt.skill_vs_baseline)?pct(bt.skill_vs_baseline,0):'—'],['80% coverage',isNum(c80.conformal_sequential)?pct(c80.conformal_sequential,0)+' of '+fmt(c80.evaluated):'too few'],
   ['95% coverage',isNum(c95.conformal_sequential)?pct(c95.conformal_sequential,0)+' of '+fmt(c95.evaluated):'too few'],['Fitted '+(o.frequency||'period')+'s',fmt((s.regime||{}).periods)+' from '+((s.regime||{}).start||'—')]]
   .map(k=>'<div class="kpi"><div class="l">'+esc(k[0])+'</div><div class="v" style="font-size:16px">'+esc(k[1])+'</div></div>').join('')+'</div>';
  const issues=all.flatMap(x=>(x.data_issues||[]).map(d=>({group:x.group,kind:d.kind,periods:d.periods,detail:d.detail})));
  if(issues.length)h+='<div class="alert warn"><h3>Data checks ('+issues.length+')</h3><ul>'+issues.slice(0,8).map(d=>'<li>'+esc((d.group==='all'?'':d.group+': ')+d.kind.replace('_',' ')+' '+d.periods+' — '+d.detail)+'</li>').join('')+'</ul></div>';
  h+='</section>'}
 const bad=all.filter(s=>s.status!=='forecast');if(bad.length)h+='<div class="alert bad"><h3>Not forecast</h3><ul>'+bad.map(s=>'<li>'+esc((s.group==='all'?'Overall':s.group)+': '+(s.reason||''))+'</li>').join('')+'</ul></div>';
 if((a.table||[]).length)h+='<section class="sec"><h3>Forecast</h3>'+tableHTML(a.table,{name:'demand-forecast'})+'</section>';
 if((o.capacity||[]).length)h+='<section class="sec"><h3>Capacity</h3>'+tableHTML(o.capacity,{name:'demand-capacity'})+'</section>';
 return h}
function fanChart(s,o){const hist=(s.history||[]).slice(-48),fc=s.forecast||[];const cats=hist.map(p=>p.period).concat(fc.map(p=>p.period)),n=hist.length;
 const used=hist.map((p,i)=>[i,p.status==='used'||p.status==='spike'?p.value:null]),other=hist.map((p,i)=>[i,p.status==='before_regime'?p.value:null]);
 const pt=[[n-1,n?hist[n-1].value:null]].concat(fc.map((p,i)=>[n+i,p.point]));
 const S=[{name:'other',pts:other,color:'var(--muted)'},{name:'observed',pts:used,color:'var(--s1)'},{name:'forecast',pts:pt,color:'var(--s2)',dash:true}];
 const band=(X,Y,lo,hi,op)=>{if(!fc.length)return '';let up='',dn='';fc.forEach((p,i)=>{up+=(up?'L':'M')+X(n+i)+' '+Y(p[hi])});for(let i=fc.length-1;i>=0;i--)dn+='L'+X(n+i)+' '+Y(fc[i][lo]);
  return '<path d="'+up+dn+'Z" style="fill:var(--band);opacity:'+op+';stroke:none"/>'};
 const rs=hist.findIndex(p=>p.period===(s.regime||{}).start);
 return lines(S,{title:(s.group==='all'?'Overall':s.group)+' '+(o.measure||'')+' forecast',cats,y0:0,extraY:fc.map(p=>p.hi95).concat(hist.map(p=>p.value)),ytick:v=>fmt(v),ylabel:label(o.measure||''),
  under:(X,Y)=>band(X,Y,'lo95','hi95',1)+band(X,Y,'lo80','hi80',1)+(rs>0?'<line x1="'+X(rs)+'" x2="'+X(rs)+'" y1="'+Y(0)+'" y2="12" style="stroke:var(--muted);stroke-dasharray:3 3"/><text x="'+(X(rs)+4)+'" y="22">fitted from here</text>':''),
  over:(X,Y)=>hist.map((p,i)=>p.status==='gap'||p.status==='spike'?'<rect x="'+(X(i)-3.5)+'" y="'+(Y(p.value)-3.5)+'" width="7" height="7" style="fill:var(--warn)"><title>'+esc(p.period+': '+fmt(p.value)+' ('+p.status+', excluded)')+'</title></rect>':'').join('')+
   fc.map((p,i)=>'<circle cx="'+X(n+i)+'" cy="'+Y(p.point)+'" r="3" style="fill:var(--s2)"><title>'+esc(p.period+': '+fmt(p.point)+' (80% '+fmt(p.lo80)+'–'+fmt(p.hi80)+', 95% '+fmt(p.lo95)+'–'+fmt(p.hi95)+')'+(p.in_progress?' — in progress':''))+'</title></circle>').join('')})}
function monthIn(m,list){return (list||[]).some(s=>{const p=String(s).split('..');return p.length>1?m>=p[0]&&m<=p[1]:m===p[0]})}
function controlChart(c){const pts=c.points||[];const cats=pts.map(p=>p.month);const S=[{name:'observed',pts:pts.map((p,i)=>[i,p.value])}];
 const ys=pts.flatMap(p=>[p.lcl,p.ucl]).filter(isNum);
 return lines(S,{title:(c.group==='all'?'Overall':c.group)+' '+c.label,cats,extraY:ys.concat([c.center]),ytick:v=>fmt(v),ylabel:c.label.length>28?c.label.slice(0,27)+'…':c.label,
  under:(X,Y)=>{let up='',dn='';pts.forEach((p,i)=>{if(isNum(p.ucl)){up+=(up?'L':'M')+X(i)+' '+Y(p.ucl)}});for(let i=pts.length-1;i>=0;i--){const p=pts[i];if(isNum(p.lcl))dn+='L'+X(i)+' '+Y(p.lcl)}
   return (up?'<path d="'+up+dn+'Z" style="fill:var(--band);stroke:none"/>':'')+(isNum(c.center)?'<line x1="'+X(0)+'" x2="'+X(pts.length-1)+'" y1="'+Y(c.center)+'" y2="'+Y(c.center)+'" style="stroke:var(--muted);stroke-dasharray:4 3"/>':'')},
  over:(X,Y)=>pts.map((p,i)=>{if(!isNum(p.value))return '';const r=monthIn(p.month,c.rate_signal_months),d=!r&&monthIn(p.month,c.data_signal_months);
   return r?'<circle cx="'+X(i)+'" cy="'+Y(p.value)+'" r="4.5" style="fill:var(--bad)"><title>'+esc(p.month+': '+fmt(p.value)+' (rate signal)')+'</title></circle>':d?'<rect x="'+(X(i)-4)+'" y="'+(Y(p.value)-4)+'" width="8" height="8" style="fill:var(--warn)"><title>'+esc(p.month+': '+fmt(p.value)+' (data-feed anomaly)')+'</title></rect>':'<circle cx="'+X(i)+'" cy="'+Y(p.value)+'" r="2.5" style="fill:var(--s1)"><title>'+esc(p.month+': '+fmt(p.value))+'</title></circle>'}).join('')})}
function vizEffect(a){const o=a.overall||{},did=o.did||{},u=(o.outcome||{}).unit||'';const inf=o.inference||'';
 const cls=inf==='supported_under_assumptions'?'answered':inf==='limited'?'partial':'unsupported';let h='<section class="sec" data-focus="effect"><h3>Estimated effect</h3>';
 h+='<div class="kpis"><div class="kpi"><div class="l">Effect ('+esc(u)+')</div><div class="v">'+fmt(did.effect,4)+'</div></div><div class="kpi"><div class="l">95% interval</div><div class="v">'+(did.ci95?fmt(did.ci95[0],4)+' to '+fmt(did.ci95[1],4):'—')+
  '</div></div><div class="kpi"><div class="l">Inference</div><div class="v"><span class="badge '+cls+'" style="font-size:14px">'+esc(label(inf||'unknown'))+'</span></div></div><div class="kpi"><div class="l">Matched exposed / comparison</div><div class="v">'+
  fmt((o.sample||{}).matched_exposed)+' / '+fmt((o.sample||{}).comparison_patients)+'</div></div></div>';
 const rows=[{label:'Matched difference-in-differences',est:did.effect,lo:(did.ci95||[])[0],hi:(did.ci95||[])[1],main:true}];
 if(o.placebo&&isNum(o.placebo.placebo_effect))rows.push({label:'Placebo (should be ~0)',est:o.placebo.placebo_effect,lo:(o.placebo.ci95||[])[0],hi:(o.placebo.ci95||[])[1],note:label(o.placebo.status||'')});
 if(o.pre_trend&&isNum(o.pre_trend.difference_in_pre_trend))rows.push({label:'Pre-trend difference (should be ~0)',est:o.pre_trend.difference_in_pre_trend,lo:(o.pre_trend.ci95||[])[0],hi:(o.pre_trend.ci95||[])[1],note:label(o.pre_trend.status||'')});
 if(o.synthetic_control&&isNum(o.synthetic_control.effect))rows.push({label:'Synthetic control (no interval)',est:o.synthetic_control.effect,note:isNum(o.synthetic_control.placebo_p_value)?'placebo p '+o.synthetic_control.placebo_p_value:''});
 h+=intervals(rows,{title:'Effect estimate and checks with 95% intervals',tick:v=>fmt(v,3)})+'<p class="hint">Dots are estimates, bars 95% intervals; the dashed line is no effect.</p>';
 const sc=o.synthetic_control;if(sc&&sc.series&&sc.series.length){const cats=sc.series.map(p=>p.month);const S=[{name:'Exposed',pts:sc.series.map((p,i)=>[i,p.exposed])},{name:'Synthetic control',pts:sc.series.map((p,i)=>[i,p.synthetic]),dash:true}];
  h+='<h3 style="margin-top:14px">Synthetic control series</h3>'+legend(S,'x')+lines(S,{title:'Exposed versus synthetic control by month',cats,ylabel:u.length>26?'outcome':u})}
 if(o.balance&&o.balance.length)h+='<h3 style="margin-top:14px">Covariate balance</h3>'+tableHTML(o.balance,{name:'balance',caption:'Standardised mean differences before and after matching'});
 if(a.assumptions&&a.assumptions.length)h+='<details><summary>Assumptions ('+a.assumptions.length+')</summary><ul>'+a.assumptions.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 return h+'</section>'}
function vizVBC(a){const r=(a.table||[])[0]||{};const sr=r.settlement_risk,fa=r.fair_allocation;if(!sr&&!fa)return null;let h='';
 const pay=isNum(r.payment_to_aco)?r.payment_to_aco:isNum(r.payment_from_aco)?-r.payment_from_aco:null;
 h+='<section class="sec" data-focus="settlement"><h3>Settlement</h3><div class="kpis">'+[['Payment to (−from) ACO',usd(pay)],['Outcome',label(r.outcome||'—')],['Savings vs benchmark',pct(r.savings_pct)],
  ['Actual vs benchmark PMPM','$'+fmt(r.actual_pmpm_completed,2)+' / $'+fmt(r.benchmark_pmpm,2)],['Member-months',fmt(r.member_months)]].map(k=>'<div class="kpi"><div class="l">'+k[0]+'</div><div class="v">'+esc(k[1])+'</div></div>').join('')+'</div>';
 if(sr){const e=sr.p_earn_savings||0,c=sr.p_within_corridor||0,o=sr.p_owe_loss||0;
  h+='<h3 style="margin-top:14px">Settlement risk ('+fmt(sr.draws)+' bootstrap draws)</h3><div class="probbar" role="img" aria-label="'+esc('Chance of earning savings '+pct(e,0)+', within corridor '+pct(c,0)+', owing a loss '+pct(o,0))+'">'+
   (e?'<div class="earn" style="width:'+100*e+'%">'+(e>.08?'earn '+pct(e,0):'')+'</div>':'')+(c?'<div class="corr" style="width:'+100*c+'%">'+(c>.08?'corridor '+pct(c,0):'')+'</div>':'')+(o?'<div class="owe" style="width:'+100*o+'%">'+(o>.08?'owe '+pct(o,0):'')+'</div>':'')+'</div>';
  h+='<p class="hint">Earn savings '+pct(e,1)+' · within corridor '+pct(c,1)+' · owe a loss '+pct(o,1)+'</p>';
  h+=intervals([{label:'Net payment (5th–95th pct)',est:sr.net_payment_p50,lo:sr.net_payment_p5,hi:sr.net_payment_p95,main:true,note:'median '+usd(sr.net_payment_p50)},
   {label:'Expected net payment',est:sr.expected_net_payment}],{title:'Distribution of the net settlement payment',tick:usdK});
  h+='<div class="kpis">'+[['95% value at risk',usd(sr.value_at_risk_95)],['Actual PMPM 5th–95th','$'+fmt(sr.actual_pmpm_p5,2)+' – $'+fmt(sr.actual_pmpm_p95,2)],
   ['Benchmark PMPM to clear MSR at median','$'+fmt(sr.benchmark_pmpm_needed_for_median_to_clear_msr,2)],['Top 1% members share of cost',pct(sr.top_1pct_members_share_of_cost)]].map(k=>'<div class="kpi"><div class="l">'+k[0]+'</div><div class="v">'+esc(k[1])+'</div></div>').join('')+'</div>'}
 h+='</section>';
 if(fa&&fa.allocation){h+='<section class="sec" data-focus="allocation"><h3>Fair allocation by '+esc(label(fa.group_by))+' — pooled '+usd(fa.pooled_settlement)+'</h3>'+
  hbars(fa.allocation.map(g=>({label:g.group,value:g.shapley_share,value2:g.pro_rata_share,ask:drill(g.group)})),{value2:1,name1:'Shapley (marginal contribution)',name2:'Pro rata by member-months',fmt:usd,tick:usdK,title:'Shapley versus pro-rata settlement share by group'})+
  '<p class="hint">Select a bar to drill into that group.</p>'+tableHTML(fa.allocation,{name:'fair-allocation',ask:g=>drill(g.group)})+'</section>'}
 h+=tableHTML(a.table.map(x=>{const y={...x};delete y.settlement_risk;delete y.fair_allocation;return y}),{name:'settlement',caption:'Settlement row (terms shown as JSON)'});
 return h}
function vizOutreach(a){const o=a.overall||{},p=o.portfolio||{},labels=o.reason_labels||{};let h='<section class="sec" data-focus="worklist"><h3>Portfolio</h3><div class="kpis">'+
  [['Selected / candidates',fmt(a.count)+' / '+fmt(o.candidates)],['Expected value',usd(p.expected_value)+(isNum(p.sd)?' ± '+usd(p.sd):'')],['80% interval',p.interval_80?usd(p.interval_80[0])+' – '+usd(p.interval_80[1]):'—'],
   ['Expected to engage',fmt(p.expected_engaged_patients)],['Nurse time',(o.capacity&&isNum(o.capacity.minutes_used))?fmt(o.capacity.minutes_used/60,1)+' h':'—'],
   ['Value lost per slot cut',p.value_lost_per_unit_cut&&isNum(p.value_lost_per_unit_cut.usd_per_unit)?usd(p.value_lost_per_unit_cut.usd_per_unit):'—']].map(k=>'<div class="kpi"><div class="l">'+k[0]+'</div><div class="v">'+esc(k[1])+'</div></div>').join('')+'</div>';
 if(o.optimization)h+='<p class="hint">Optimiser: '+esc(o.optimization.method||'')+' — '+esc(o.optimization.certificate||o.optimization.guarantee||'')+'</p>';
 if(o.by_group&&o.by_group.length)h+='<h3>Expected value by practice</h3>'+hbars(o.by_group.map(g=>({label:g.group+' ('+g.patients+')',value:g.expected_value,pick:g.group})),{fmt:usd,tick:usdK,title:'Expected value of selected patients by practice'})+
  '<p class="hint">Select a practice to filter the worklist below.</p>';
 h+='<div data-worklist>'+worklist(a)+'</div></section>';
 if(Object.keys(labels).length)h+='<section class="sec" data-focus="reasons"><h3>Reason codes on this worklist</h3>'+Object.entries(labels).map(([c,l])=>'<span class="rchip '+esc(l.category)+'" title="'+esc(c)+'">'+esc(l.label)+'</span>').join('')+'</section>';
 if(o.learning_loop)h+='<p class="hint">Learning loop: '+esc(label(o.learning_loop.status||''))+'. '+esc(o.learning_loop.detail||'')+'</p>';
 return h}
function worklist(a){const labels=((a.overall||{}).reason_labels)||{};let rows=a.table||[];const f=state.outFilter;if(f)rows=rows.filter(r=>r.practice===f||r.pcp===f);
 return (f?'<div class="tools"><span class="badge">Showing '+esc(f)+' · '+rows.length+' patients</span><button type="button" class="btn small" data-clearfilter="1">Show all</button></div>':'')+
  tableHTML(rows,{name:'outreach-worklist',cols:['priority','patient_id','top_reasons','expected_value','expected_value_interval_90','engagement_probability','minutes','practice','pcp','suggested_action'],
   head:{expected_value:'expected value',expected_value_interval_90:'90% conformal',engagement_probability:'P(engage)',top_reasons:'reasons',reason_detail:'why (evidence)'},
   render:{expected_value:v=>usd(v),expected_value_interval_90:v=>Array.isArray(v)?usd(v[0])+' – '+usd(v[1]):'—',engagement_probability:v=>pct(v,0),
    top_reasons:(v,r)=>(v||[]).map(c=>{const l=labels[c]||{};return '<span class="rchip '+esc(l.category||'')+'" title="'+esc(c)+'">'+esc(l.label||c)+'</span>'}).join('')+
     (r.reason_detail?'<details class="why"><summary>Evidence</summary><span class="muted" style="font-size:12px">'+esc(r.reason_detail)+'</span></details>':''),
    suggested_action:v=>'<span style="font-size:12px">'+esc(v)+'</span>'}})}
function vizTrajectory(a){const rows=a.table||[];if(!rows.length||!('patient_id' in rows[0]))return null;
 const metric=['probability','change','slope_per_year'].find(k=>isNum(rows[0][k]));let h='';
 if(metric){const top=rows.slice().sort((x,y)=>Math.abs(y[metric])-Math.abs(x[metric])).slice(0,15);h+='<section class="sec"><h3>Largest '+esc(label(metric))+' (top '+top.length+')</h3>'+hbars(top.map(r=>({label:r.patient_id,value:r[metric]})),{fmt:v=>metric==='probability'?pct(v,0):fmt(v),title:label(metric)+' by patient'})+'</section>'}
 return h+patientOverview(a)}
function vizTable(a){const rows=a.table||[];if(!rows.length)return '';const columns=[...new Set(rows.flatMap(r=>Object.keys(r)))];const key=GROUP_KEYS.find(k=>columns.includes(k)&&rows.some(r=>r[k]!=null&&typeof r[k]!=='object'))||columns.find(k=>rows.some(r=>typeof r[k]==='string'));
 const metric=pickMetric(rows,a.question||a.interpretation);let h='<section class="sec">';
 if(key&&metric&&rows.length>1&&rows.length<=60){const drillable=['group','segment','practice','pcp','payer','coverage_type','facility','organization_id','month','year','quarter'].includes(key);
  if(rows.every(r=>isTime(r[key]))){const cats=rows.map(r=>String(r[key]));h+='<h3>'+esc(label(metric))+' by '+esc(label(key))+'</h3>'+lines([{name:label(metric),pts:rows.map((r,i)=>[i,r[metric]])}],{title:label(metric)+' over time',cats,ylabel:label(metric)})}
  else{const sorted=rows.slice().sort((x,y)=>(y[metric]||0)-(x[metric]||0));h+='<h3>'+esc(label(metric))+' by '+esc(label(key))+'</h3>'+hbars(sorted.slice(0,30).map(r=>({label:r[key]??'Not recorded',value:r[metric],ask:drillable&&r[key]!=null?drill(r[key]):null})),{title:label(metric)+' by '+label(key),ref:/ratio/.test(metric)?1:null,refLabel:'no difference (1.0)'})+
   (drillable?'<p class="hint">Select a bar to ask a follow-up about that '+esc(label(key))+'.</p>':'')}}
 h+='<details class="overview-records"><summary>Explore returned data ('+rows.length.toLocaleString()+' rows)</summary>'+tableHTML(rows,{name:'agere-answer',cols:a.table_columns,ask:key&&['group','segment','practice','pcp','payer','coverage_type','facility'].includes(key)?(r=>drill(r[key])):null})+'</details>';
 return h+'</section>'}
function vizScalar(a){if(a.answer_type==='count'||a.answer_type==='value'){const v=a.answer_type==='count'?a.count:a.value;if(v==null||typeof v==='string')return '';
  return '<section class="sec"><div class="big">'+esc(fmt(v))+(a.unit?esc(a.unit):'')+'</div><div class="muted">'+(a.answer_type==='count'?'count':'value')+(a.unknown_count?' · '+fmt(a.unknown_count)+' patients could not be evaluated':'')+'</div></section>'}
 if(a.answer_type==='patient_set'){const ids=a.patient_ids||[];const rows=ids.map(id=>({patient_id:id}));
  return '<section class="sec"><div class="big">'+fmt(a.count!=null?a.count:ids.length)+'</div><div class="muted">patients'+(a.unknown_count?' · '+fmt(a.unknown_count)+' could not be evaluated':'')+'</div>'+
   (ids.length?'<details'+(ids.length<=12?' open':'')+'><summary>'+ids.length+' patient IDs</summary><div class="pids">'+ids.slice(0,300).map(i=>'<span>'+esc(i)+'</span>').join('')+'</div>'+(ids.length>300?'<p class="hint">First 300 shown; the CSV has all.</p>':'')+csvButton(rows,'patients')+'</details>':'')+'</section>'}
 return ''}
function sparkline(vals,dir,title){const v=(vals||[]).filter(isNum);if(v.length<2)return '';const w=96,h=24,lo=Math.min(...v),hi=Math.max(...v),sp=hi-lo||1;
 const X=i=>2+i*(w-6)/(v.length-1),Y=x=>h-3-(x-lo)/sp*(h-6);const d=v.map((x,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(x).toFixed(1)).join('');
 return '<svg class="spark" width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'" role="img" aria-label="'+esc(title)+'"><title>'+esc(title)+'</title><path d="'+d+'" style="fill:none;stroke:var(--s1);stroke-width:1.5"/>'+
  '<circle cx="'+X(v.length-1).toFixed(1)+'" cy="'+Y(v[v.length-1]).toFixed(1)+'" r="2.8" style="fill:var('+(dir==='worse'?'--bad':'--good')+')"/></svg>'}
function mval(v,m){if(!isNum(v))return '—';if(['a1c_poor','bp_control','denial_rate','no_show_rate'].includes(m))return pct(v,1);if(m==='pmpm')return usd(v);return fmt(v,1)}
function vizMarket(a){const o=a.overall||{},rows=a.table||[];let h='<section class="sec" data-focus="market"><h3>Market view by '+esc(o.unit||'unit')+'</h3><div class="kpis">'+
  [['Tickers examined',fmt(o.tests_examined)],['Salient moves',fmt(rows.filter(r=>r.salient).length)],['Look-elsewhere cost',fmt(o.look_elsewhere_bits)+' bits'],['Members',fmt(o.members_in_scope)]]
  .map(k=>'<div class="kpi"><div class="l">'+k[0]+'</div><div class="v">'+k[1]+'</div></div>').join('')+'</div>';
 const st=Object.values(o.network||{}).map(n=>n.statement).filter(Boolean);if(st.length)h+='<ul class="hint">'+st.map(s=>'<li>'+esc(s)+'</li>').join('')+'</ul>';
 if(rows.length){h+='<div class="tools"><span class="muted" style="font-size:13px">Ranked by surprise × impact. Select a ticker to ask its follow-up.</span></div><div class="tablewrap" tabindex="0" role="region" aria-label="ticker board"><table><thead><tr>'+
   ['Ticker','Metric','Month','Trend','Last','Δ prior','Δ baseline','z','β','Surprise','Move'].map(c=>'<th scope="col">'+c+'</th>').join('')+'</tr></thead><tbody>';
  rows.slice(0,60).forEach(r=>{const chg=isNum(r.change_prior_pct)?(r.change_prior_pct>0?'+':'')+pct(r.change_prior_pct,0):'—',cb=isNum(r.change_baseline_pct)?(r.change_baseline_pct>0?'+':'')+pct(r.change_baseline_pct,0):'—';
   h+='<tr'+(r.follow_up?' data-ask="'+esc(r.follow_up)+'" tabindex="0" title="Ask: '+esc(r.follow_up)+'"':'')+'><td><b>'+esc(r.unit)+'</b>'+(r.low_volume?' <span class="muted" title="low volume: shrunk toward the network, not headlined">(low vol)</span>':'')+'</td><td>'+esc(label(r.metric))+'</td><td>'+esc(r.month)+'</td>'+
    '<td>'+sparkline(r.spark,r.direction,r.unit+' '+label(r.metric)+' (shrunk monthly values)')+'</td><td class="n">'+mval(r.last,r.metric)+'</td><td class="n" style="color:var('+(r.direction==='worse'?'--bad':'--good')+')">'+chg+'</td><td class="n">'+cb+'</td>'+
    '<td class="n">'+fmt(r.z,2)+'</td><td class="n">'+(isNum(r.beta)?fmt(r.beta,2):'—')+'</td><td class="n">'+fmt(r.surprise_bits,1)+' bits</td><td>'+esc(r.move_type||'')+'</td></tr>'});
  h+='</tbody></table></div>'+csvButton(rows.map(r=>{const c=Object.assign({},r);delete c.spark;return c}),'market-tickers')}
 const gaps=Object.entries(o.data_gaps||{}).flatMap(([m,g])=>(g||[]).filter(x=>x.reason!=='no_data').map(x=>label(m)+' '+x.months+' ('+label(x.reason)+')'));
 if(gaps.length)h+='<p class="hint">Data gaps excluded (not read as moves): '+esc(gaps.join('; '))+'</p>';
 const sk=Object.entries(o.skipped_metrics||{});if(sk.length)h+='<p class="hint">Not ranked: '+esc(sk.map(([m,w])=>label(m)+' ('+w+')').join('; '))+'</p>';
 const sec=(o.sectors||[]).filter(s=>(s.sectors||[]).length);if(sec.length)h+='<details><summary>Co-moving sectors</summary><ul>'+sec.map(s=>'<li><b>'+esc(label(s.metric))+'</b> (network factor '+pct(s.network_factor_share,0)+'): '+
  esc(s.sectors.map(g=>g.units.join(', ')+' (r̄ '+fmt(g.mean_correlation,2)+')').join('; '))+'</li>').join('')+'</ul></details>';
 return h+'</section>'}
/* care pathway: transition-probability heatmap (rows = from, columns = to) + reach/conformance/leakage KPIs + path variants */
const PW={emergency:'ED',inpatient:'Inpatient',observation:'Obs',urgent_care:'Urgent',primary_care:'PCP',specialty:'Specialty',telehealth:'Tele',
 post_acute:'Post-acute',outside:'Outside',other:'Other',discharge:'Discharge',referral:'Referral',diagnosis:'Diagnosis',end:'No contact'};
const pw=s=>PW[s]||label(s);
function pathMatrix(m,start){const st=m.states||[],P=m.probabilities||[],N=m.counts||[];const rows=st.filter((s,i)=>s!=='end'&&(N[i]||[]).some(x=>x>0));
 const W=CW(),L=78,T=58,cw=Math.max(26,Math.min(52,Math.floor((W-L-8)/st.length))),ch=24,H=T+rows.length*ch+8;let s=svgOpen(L+cw*st.length+8,H,'Transition probabilities from each state (row) to the next (column)');
 st.forEach((c,j)=>{s+='<text transform="translate('+(L+j*cw+cw/2)+','+(T-6)+') rotate(-40)" style="fill:var(--fg)">'+esc(pw(c))+'</text>'});
 rows.forEach((r,k)=>{const i=st.indexOf(r),y=T+k*ch,n=(N[i]||[]).reduce((x,y)=>x+y,0);
  s+='<text x="'+(L-6)+'" y="'+(y+ch/2+4)+'" text-anchor="end" style="fill:var(--fg);font-weight:'+(r===start?700:400)+'">'+esc(pw(r))+'</text>';
  st.forEach((c,j)=>{const p=(P[i]||[])[j]||0,x=L+j*cw;s+='<g><title>'+esc(pw(r)+' → '+pw(c)+': '+pct(p)+' ('+((N[i]||[])[j]||0)+' of '+n+')')+'</title>'+
   '<rect x="'+x+'" y="'+y+'" width="'+(cw-2)+'" height="'+(ch-2)+'" rx="3" style="fill:var(--s1);fill-opacity:'+(p>0?(.08+.92*p).toFixed(3):0)+';stroke:var(--line)"/>'+
   (p>=.005?'<text x="'+(x+cw/2-1)+'" y="'+(y+ch/2+4)+'" text-anchor="middle" style="fill:'+(p>.55?'var(--card)':'var(--fg)')+';font-size:11px">'+Math.round(100*p)+'</text>':'')+'</g>'})});
 return s+'</svg>'}
function vizPathway(a){const o=a.overall||{},p=o.parameters||{},start=p.start||'';let h='<section class="sec" data-focus="flow"><h3>Care pathway'+(start?' after '+esc(pw(start)):'')+
 ' · '+esc(fmt(o.transitions))+' transitions, '+esc(fmt(o.patients))+' patients</h3>';const k=[];const c=o.conformance;
 if(c&&c.occurrences)k.push([pw(c.from)+' → '+c.to.map(pw).join('/')+' ≤'+c.within_days+'d',pct(c.share)+' <span class="muted" style="font-size:12px">'+pct(c.ci95_low)+'–'+pct(c.ci95_high)+'</span>']);
 Object.entries(o.reach||{}).forEach(([t,rs])=>{const r=(rs||[])[c?0:rs.length-1];if(r&&r.anchors)k.push([pw(t)+' within '+r.days+'d',pct(r.share)+' <span class="muted" style="font-size:12px">'+pct(r.ci95_low)+'–'+pct(r.ci95_high)+'</span>'])});
 const lk=o.leakage||{};if(lk.transitions)k.push(['Leakage to outside',pct(lk.share,2)]);if(o.chain)k.push(['Expected steps in care',fmt(o.chain.expected_steps)]);
 if(k.length)h+='<div class="kpis">'+k.map(x=>'<div class="kpi"><div class="l">'+esc(x[0])+'</div><div class="v">'+x[1]+'</div></div>').join('')+'</div>';
 if(o.matrix)h+='<p class="hint">Row = current step, column = next step; cells are % of the row ('+esc(fmt(p.window_days))+'-day window; “No contact” = nothing further recorded).</p>'+pathMatrix(o.matrix,start);
 (o.acute_data_gaps||[]).forEach(g=>{h+='<p class="hint">ED/inpatient records absent '+esc(g.start)+'..'+esc(g.end)+' (feed defect): windows overlapping it are excluded.</p>'});
 if(o.variants&&o.variants.length)h+=tableHTML(o.variants.map(v=>({path:v.states.map(pw).join(' → '),count:v.count,share:v.share,ci95_low:v.ci95_low,ci95_high:v.ci95_high})),{name:'pathway-variants',caption:'Most common paths'});
 if(o.groups&&o.groups.tests&&o.groups.tests.length)h+=tableHTML(o.groups.tests,{name:'pathway-group-tests',caption:'Do next steps differ by '+label(o.groups.by)+'? (permutation test)'});
 return h+'</section>'}
/* fair profiling: caterpillar of shrunk rates with 95% intervals, raw (adjusted) rate as a hollow dot, network line */
function vizProfile(a){const o=a.overall||{},pr=o.profile||{},rows=(a.table||[]).slice(0,40);if(!rows.length)return '';const c=pr.counts||{};
 const share=/share/.test(pr.display_unit||''),cost=/\$/.test(pr.display_unit||''),f=v=>share?pct(v,1):cost?'$'+fmt(v,2):fmt(v,0);
 let h='<section class="sec" data-focus="profile"><h3>'+esc(label(o.unit==='practice'?'practices':'PCPs'))+' on '+esc(pr.metric_label||label(o.metric||''))+'</h3><div class="kpis">'+
  [['Truly high',fmt(c.truly_high)],['Truly low',fmt(c.truly_low)],['Indistinguishable',fmt(c.indistinguishable)],['Network',f(pr.network_rate)],
   ['Median shrinkage',isNum(pr.shrinkage_median)?pct(pr.shrinkage_median,0):'—']].map(k=>'<div class="kpi"><div class="l">'+k[0]+'</div><div class="v">'+esc(k[1])+'</div></div>').join('')+'</div>';
 h+=profChart(rows,pr.network_rate,o.threshold,f)+'<div class="legend"><span><span class="sw" style="background:var(--bad)"></span>truly high</span><span><span class="sw" style="background:var(--good)"></span>truly low</span>'+
  '<span><span class="sw" style="background:var(--s3)"></span>indistinguishable</span><span>○ raw (case-mix adjusted)</span><span>┊ network</span></div>';
 if((a.table||[]).length>rows.length)h+='<p class="hint">Chart shows the first '+rows.length+' of '+a.table.length+' units by shrunk rate; the table has all.</p>';
 return h+tableHTML(a.table,{name:'profile-units',ask:r=>'is '+r.group+' really an outlier?',cols:['group','flag','posterior_rate','ci95_low','ci95_high','raw_rate','members','p_above_network','rank','rank_ci95','credibility_z','credibility_rate','observed','expected'],
  head:{posterior_rate:'shrunk rate',p_above_network:'P(above network)',credibility_z:'credibility Z',rank_ci95:'rank 95%'},render:{p_above_network:v=>pct(v,0),flag:v=>esc(label(v||''))}})+'</section>'}
function profChart(rows,net,thr,f){const W=CW(),lh=22,L=Math.min(Math.round(W*.36),12+7*Math.max(...rows.map(r=>String(r.group).length))),R=20,T=8,H=T+rows.length*lh+28;
 const vs=rows.flatMap(r=>[r.ci95_low,r.ci95_high,r.adjusted_raw_rate]).concat([net,thr]).filter(isNum);let lo=Math.min(0,...vs),hi=Math.max(...vs);const pd=(hi-lo)*.04||1;hi+=pd;
 const X=v=>L+(v-lo)/(hi-lo)*(W-L-R);let s=svgOpen(W,H,'Shrunk rate with 95% interval per unit');
 ticks(lo,hi,W<480?3:5).forEach(t=>{s+='<line class="gridl" x1="'+X(t)+'" x2="'+X(t)+'" y1="'+T+'" y2="'+(H-24)+'"/><text x="'+X(t)+'" y="'+(H-8)+'" text-anchor="middle">'+esc(f(t))+'</text>'});
 if(isNum(net))s+='<line x1="'+X(net)+'" x2="'+X(net)+'" y1="'+T+'" y2="'+(H-24)+'" style="stroke:var(--fg);stroke-dasharray:4 3"/>';
 if(isNum(thr))s+='<line x1="'+X(thr)+'" x2="'+X(thr)+'" y1="'+T+'" y2="'+(H-24)+'" style="stroke:var(--warn);stroke-dasharray:2 2"/>';
 rows.forEach((r,i)=>{const y=T+i*lh+lh/2,c=r.flag==='truly_high'?'var(--bad)':r.flag==='truly_low'?'var(--good)':'var(--s3)',q='is '+r.group+' really an outlier?';
  s+='<g data-ask="'+esc(q)+'" tabindex="0" role="button" aria-label="'+esc(r.group+': '+f(r.posterior_rate)+' ('+f(r.ci95_low)+' to '+f(r.ci95_high)+'), '+label(r.flag))+'">';
  s+='<text x="'+(L-6)+'" y="'+(y+4)+'" text-anchor="end" style="fill:var(--fg)">'+esc(r.group)+'</text>';
  if(isNum(r.ci95_low)&&isNum(r.ci95_high))s+='<line x1="'+X(r.ci95_low)+'" x2="'+X(r.ci95_high)+'" y1="'+y+'" y2="'+y+'" style="stroke:'+c+';stroke-width:3;stroke-linecap:round"/>';
  if(isNum(r.adjusted_raw_rate))s+='<circle cx="'+X(Math.min(r.adjusted_raw_rate,hi))+'" cy="'+y+'" r="3.5" style="fill:none;stroke:var(--muted);stroke-width:1.5"/>';
  if(isNum(r.posterior_rate))s+='<circle cx="'+X(r.posterior_rate)+'" cy="'+y+'" r="5" style="fill:'+c+';stroke:var(--card);stroke-width:1.5"><title>'+esc(r.group+': shrunk '+f(r.posterior_rate)+', raw '+f(r.raw_rate)+', '+r.members+' members')+'</title></circle>';
  s+='</g>'});
 return s+'</svg>'}
/* scheduling / yield: expected net value vs extra bookings per clinic session (recommended level marked) + ranked upcoming appointments */
function vizScheduling(a){const o=(a.overall||{}).scheduling||{},m=o.model||{},me=m.model||{},be=m.baseline||{},rem=o.reminders||{},S=(o.sessions||[]).slice(0,6);
 let h='<section class="sec" data-focus="yield"><h3>Scheduling'+(o.practice?' · '+esc(o.practice):'')+'</h3><div class="kpis">'+
  [['No-show AUC (model / practice rate)',isNum(me.auc)?fmt(me.auc,3)+' / '+fmt(be.auc,3):'—'],['Brier (model / practice rate)',isNum(me.brier)?fmt(me.brier,4)+' / '+fmt(be.brier,4):'—'],
   ['Held-out no-show rate',isNum(me.observed_rate)?pct(me.observed_rate,1):'—'],['Upcoming in window',fmt((o.counts||{}).in_horizon)],
   ['Expected no-shows',isNum(rem.expected_no_shows)?fmt(rem.expected_no_shows,1):'—']].map(k=>'<div class="kpi"><div class="l">'+esc(k[0])+'</div><div class="v">'+esc(k[1])+'</div></div>').join('')+'</div>';
 if(S.length){h+='<p class="hint">Expected net value of a typical session (served visits × value − patients beyond capacity × overflow cost) as extra bookings are added; ● = recommended level.</p>'+yieldChart(S)+
  '<div class="legend">'+S.map((s,i)=>'<span><span class="sw" style="background:'+SER[i%SER.length]+'"></span>'+esc(s.practice)+' (cap '+s.capacity+', +'+s.recommended_extra+')</span>').join('')+'</div>';
  h+=tableHTML(o.sessions.map(s=>{const c=Object.assign({},s);delete c.curve;return c}),{name:'scheduling-sessions',caption:'Recommended extra bookings per typical session',
   cols:['practice','capacity','mean_no_show_probability','recommended_extra','p_overflow','expected_overflow','expected_idle_without','expected_idle_with','expected_value_gain'],
   head:{mean_no_show_probability:'P(no-show)',p_overflow:'P(overflow)'},render:{mean_no_show_probability:v=>pct(v,1),p_overflow:v=>pct(v,0)}})}
 const ap=o.appointments||[];if(ap.length)h+=tableHTML(ap,{name:'scheduling-appointments',caption:'Upcoming appointments ranked by no-show risk'+(rem.selected?' (top '+rem.selected+' = reminder calls; expected '+fmt(rem.expected_averted,2)+' no-shows averted vs '+fmt(rem.random_choice_averted,2)+' at random)':''),
  cols:['rank','scheduled_date','practice','appointment_id','patient_id','purpose','lead_days','p_no_show','remind','reasons'],head:{p_no_show:'P(no-show)'},
  render:{p_no_show:v=>'<span style="display:inline-block;height:8px;width:'+Math.round(60*Math.min(1,v||0))+'px;background:var(--s4);border-radius:2px;margin-right:6px"></span>'+pct(v,0),
   remind:v=>v?'call':'',reasons:v=>esc((v||[]).join('; '))}});
 if((o.access||[]).length)h+=tableHTML(o.access,{name:'scheduling-access',caption:'Access: booking lead time (booked → scheduled), last test window'});
 const d=o.data_defects||[];if(d.length)h+='<details><summary>Data limits ('+d.length+')</summary><ul>'+d.map(x=>'<li>'+esc(x.statement)+'</li>').join('')+'</ul></details>';
 if((o.assumptions||[]).length)h+='<p class="hint"><b>Assumptions.</b> '+esc(o.assumptions.join(' '))+'</p>';
 return h+'</section>'}
/* queueing / staffing: servers (or beds) needed per hour of week for a wait or blocking target + the Erlang table behind the peak */
function vizQueueing(a){const o=(a.overall||{}).queueing||{},st=o.staffing||{},sp=o.single_point||{},inp=o.inputs||{},rows=a.table||[],blk=o.objective==='blocking',scope=(a.overall||{}).input_scope;
 const rate=v=>isNum(v)&&v>0&&v<.001?'<0.001':fmt(v);
 let h='<section class="sec" data-focus="staffing"><h3>'+(blk?'Bed capacity':'Staffing')+' · '+esc(o.source||'')+'</h3>';
 if(scope){const w=scope.window||{},population=scope.population==='selected'?fmt(scope.patient_count,0)+' selected patients':'All received patients';
  h+='<p class="hint"><b>Analysis scope.</b> '+esc(population)+' · arrivals '+esc(w.start||'start of available history')+' to '+esc(w.end||scope.as_of||'end of available history')+' (inclusive)'+(scope.as_of?' · snapshot '+esc(scope.as_of):'')+(scope.organization_id?' · organization '+esc(scope.organization_id):'')+'.</p>'}
 if(o.status==='insufficient_data'){
  h+='<p class="alert warn"><b>Capacity remains unknown.</b> '+esc(o.reason||'Required inputs are unavailable.')+'. Completed demand observations are retained below.</p>';
  h+='<div class="kpis">'+[['Observed arrivals',fmt(inp.arrivals_used,0)],['Baseline arrivals / week',rate(inp.weekly_arrivals_base)],['Scaled arrivals / week',rate(inp.weekly_arrivals_scaled)],['Required servers','Unknown'],['Capacity gap','Unknown']].map(k=>'<div class="kpi"><div class="l">'+esc(k[0])+'</div><div class="v">'+esc(k[1])+'</div></div>').join('')+'</div>';
  h+='<p class="hint">Demand scaling is arithmetic, not a forecast or a staffing estimate. '+(inp.all_arrivals_timed===false?'Hour-of-day peaks are not fully observed. ':'')+'Service duration is needed for a capacity scenario; an approved service target is needed to interpret that scenario, and current staffing is needed for a capacity gap. Explicit assumptions remain assumptions.</p>';
  if(rows.length)h+=tableHTML(rows,{name:'queueing-demand-observations',caption:'Completed demand observations · staffing unresolved',cols:['source','arrivals_used','rate_window_start_date','rate_window_end_date','weekly_arrivals_base','arrival_scale_applied','weekly_arrivals_scaled','mean_service_minutes','required_servers','capacity_gap_servers']});
  if((a.warnings||[]).length)h+='<details><summary>Source and assumption limits</summary><ul>'+a.warnings.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
  if(rows.length&&rows[0].configured_parameters_json)h+='<details><summary>Configured engine inputs · service targets are not approved</summary><pre>'+esc(rows[0].configured_parameters_json)+'</pre></details>';
  return h+'</section>'}
 h+='<div class="kpis">'+
  [['Peak '+(blk?'beds':'servers'),fmt(st.peak_servers)+(st.peak_hour?' ('+st.peak_hour+')':'')],[(blk?'Bed':'Server')+'-hours / week',fmt(st.total_server_hours_per_week)],
   ['Arrivals / week',rate(inp.weekly_arrivals_planned)],['Observed arrivals',fmt(inp.arrivals_used,0)],['Mean service (min)',isNum(inp.mean_service_minutes)?fmt(inp.mean_service_minutes,1):'—'],
   [blk?'Blocking at mean load':'P(wait) at peak',blk?(isNum(sp.erlang_b_blocking_probability)?pct(sp.erlang_b_blocking_probability,2):'—'):((sp.erlang_c||{}).p_wait!=null?pct(sp.erlang_c.p_wait,0):'—')]
  ].map(k=>'<div class="kpi"><div class="l">'+esc(k[0])+'</div><div class="v">'+esc(k[1])+'</div></div>').join('')+'</div>';
 if(rows.length){const W=CW(),L=36,R=8,T=8,B=26,H=170,m=Math.max(1,...rows.map(r=>r.servers||0)),bw=(W-L-R)/rows.length;
  let s='<svg viewBox="0 0 '+W+' '+H+'" width="100%" role="img" aria-label="Servers needed by hour of week">';
  rows.forEach((r,i)=>{const v=r.servers||0,y=T+(H-T-B)*(1-v/m);s+='<rect x="'+(L+i*bw)+'" y="'+y+'" width="'+Math.max(1,bw-0.6)+'" height="'+(H-B-y)+'" style="fill:var(--s1)"><title>'+esc(r.day+' '+String(r.hour).padStart(2,'0')+':00 · '+v)+'</title></rect>'});
  ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach((d,i)=>{s+='<text x="'+(L+i*24*bw+12*bw)+'" y="'+(H-8)+'" text-anchor="middle">'+d+'</text>'});
  s+='<text x="4" y="'+(T+8)+'">'+m+'</text><text x="4" y="'+(H-B)+'">0</text></svg>';h+='<p class="hint">Minimum '+(blk?'beds':'servers')+' per hour block (Monday 00:00 first) meeting the target.</p>'+s}
 if(rows.length)h+=tableHTML(rows,{name:'queueing-staffing',caption:'Staffing by hour of week',cols:['day','hour','arrival_rate_per_hour','offered_load','servers',blk?'blocking_probability':'p_wait_gt_t','utilization'],
  head:{arrival_rate_per_hour:'Arrivals/h',offered_load:'Load',p_wait_gt_t:'P(wait>t)',blocking_probability:'P(block)'},render:{utilization:v=>isNum(v)?pct(v,0):'',p_wait_gt_t:v=>isNum(v)?pct(v,1):'',blocking_probability:v=>isNum(v)?pct(v,2):''}});
 if((a.warnings||[]).length)h+='<details open><summary>Assumption warnings ('+a.warnings.length+')</summary><ul>'+a.warnings.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 if((a.assumptions||[]).length)h+='<p class="hint"><b>Assumptions.</b> '+esc(a.assumptions.join(' '))+'</p>';
 return h+'</section>'}
/* uplift: who benefits (predicted vs observed benefit by decile) + the capacity-constrained targeting table */
function vizUplift(a){const o=(a.overall||{}).uplift||{},ate=o.ate||{},ra=o.rate_autoc||{},qi=o.qini||{},tg=o.targeting||{},cal=o.calibration||[],het=o.heterogeneity||'not_assessable';
 const cls=het==='detected'?'answered':het==='reversed'?'unsupported':'partial';
 const ci=x=>x&&x.ci95?fmt(x.ci95[0],4)+' to '+fmt(x.ci95[1],4):'—';
 let h='<section class="sec" data-focus="uplift"><h3>Who benefits · '+esc(label(((o.exposure||{}).program)||'program'))+'</h3><div class="kpis">'+
  [['Average benefit per member',isNum(ate.estimate)?fmt(ate.estimate,4):'—'],['95% interval',ci(ate)],['RATE (AUTOC)',isNum(ra.estimate)?fmt(ra.estimate,4):'—'],['RATE 95% interval',ci(ra)],
   ['Qini',isNum(qi.estimate)?fmt(qi.estimate,4):'—'],['Heterogeneity','<span class="badge '+cls+'" style="font-size:14px">'+esc(label(het))+'</span>',true],
   ['Expected averted (list / random)',isNum(tg.expected_events_averted)?fmt(tg.expected_events_averted,1)+' / '+fmt(tg.random_selection_expected_events_averted,1):'—']
  ].map(k=>'<div class="kpi"><div class="l">'+esc(k[0])+'</div><div class="v">'+(k[2]?k[1]:esc(k[1]))+'</div></div>').join('')+'</div>';
 if(cal.length){const W=CW(),L=46,R=10,T=10,B=34,H=210,vals=cal.flatMap(c=>[c.predicted_benefit,c.observed_benefit]).filter(isNum),lo=Math.min(0,...vals),hi=Math.max(0,...vals),sp=(hi-lo)||1,
   Y=v=>T+(1-(v-lo)/sp)*(H-T-B),bw=(W-L-R)/cal.length;let g='<svg viewBox="0 0 '+W+' '+H+'" width="100%" role="img" aria-label="Predicted and observed benefit by decile">';
  ticks(lo,hi,4).forEach(t=>{g+='<line class="gridl" x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(t)+'" y2="'+Y(t)+'"/><text x="'+(L-6)+'" y="'+(Y(t)+4)+'" text-anchor="end">'+esc(fmt(t,3))+'</text>'});
  cal.forEach((c,i)=>{const x=L+i*bw;[[c.predicted_benefit,'var(--s1)',0],[c.observed_benefit,'var(--s4)',1]].forEach(([v,col,k])=>{if(!isNum(v))return;const y0=Y(0),y1=Y(v);
    g+='<rect x="'+(x+bw*(.12+.38*k))+'" y="'+Math.min(y0,y1)+'" width="'+(bw*.36)+'" height="'+Math.max(1,Math.abs(y1-y0))+'" style="fill:'+col+'"><title>'+esc('Decile '+c.decile+(k?' observed ':' predicted ')+fmt(v,4))+'</title></rect>'});
   g+='<text x="'+(x+bw/2)+'" y="'+(H-14)+'" text-anchor="middle">'+c.decile+'</text>'});
  g+='<text x="'+((L+W-R)/2)+'" y="'+(H-2)+'" text-anchor="middle">Decile of predicted benefit (1 = most)</text></svg>';
  h+='<p class="hint">Predicted vs observed benefit (reduction in the outcome) by decile of predicted benefit, held-out cross-fitted predictions; bars should fall from left to right if the ranking works.</p>'+g+
   '<div class="legend"><span><span class="sw" style="background:var(--s1)"></span>predicted</span><span><span class="sw" style="background:var(--s4)"></span>observed (doubly robust)</span></div>'}
 if((a.table||[]).length)h+=tableHTML(a.table,{name:'uplift-targeting',caption:'Recommended members to enrol (predicted benefit, expected value, main drivers)'+(tg.capacity?' within '+tg.capacity.slots+' slots':''),
  cols:['priority','patient_id','predicted_uplift','baseline_rate','expected_value_usd','minutes','practice','reason_detail'],head:{predicted_uplift:'Predicted benefit',baseline_rate:'Baseline rate',expected_value_usd:'Expected value ($)',reason_detail:'Drivers'}});
 if((o.segments||[]).length)h+=tableHTML(o.segments,{name:'uplift-deciles',caption:'Deciles of predicted benefit: who they are'});
 if((a.warnings||[]).length)h+='<details open><summary>Warnings ('+a.warnings.length+')</summary><ul>'+a.warnings.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 if((a.assumptions||[]).length)h+='<p class="hint"><b>Assumptions.</b> '+esc(a.assumptions.join(' '))+'</p>';
 return h+'</section>'}
/* program_effect via synthetic DiD or Callaway-Sant'Anna: estimate with interval, cohort / event-time effects, checks */
function vizEffectPanel(a){const o=a.overall||{},sd=o.sdid,cs=o.callaway_santanna,u=(o.outcome||{}).unit||'',inf=o.inference||'';
 const cls=inf==='supported_under_assumptions'?'answered':inf==='limited'?'partial':'unsupported',r0=(a.table||[])[0]||{};
 let h='<section class="sec" data-focus="effect"><h3>Estimated effect · '+esc(label(o.estimation_method||''))+'</h3><div class="kpis">'+
  '<div class="kpi"><div class="l">Effect ('+esc(u)+')</div><div class="v">'+fmt(r0.estimate,4)+'</div></div><div class="kpi"><div class="l">95% interval</div><div class="v">'+(r0.ci95?fmt(r0.ci95[0],4)+' to '+fmt(r0.ci95[1],4):'—')+
  '</div></div><div class="kpi"><div class="l">Inference</div><div class="v"><span class="badge '+cls+'" style="font-size:14px">'+esc(label(inf||'unknown'))+'</span></div></div><div class="kpi"><div class="l">Exposed / comparison</div><div class="v">'+fmt(r0.exposed)+' / '+fmt(r0.comparison)+'</div></div></div>';
 const rows=(a.table||[]).filter(r=>isNum(r.estimate)&&r.ci95).map(r=>({label:label(r.analysis)+(r.cohort?' '+r.cohort:''),est:r.estimate,lo:r.ci95[0],hi:r.ci95[1],main:r.analysis===(a.table[0]||{}).analysis&&!r.cohort}));
 if(rows.length)h+=intervals(rows,{title:'Effect estimates with 95% intervals',tick:v=>fmt(v,3)})+'<p class="hint">Dots are estimates, bars 95% intervals; the dashed line is no effect.</p>';
 if(cs&&(cs.dynamic||[]).length){const S=[{name:'ATT by event time',pts:cs.dynamic.map(d=>[d.event_time,d.att])},{name:'uniform 95% lower',pts:cs.dynamic.map(d=>[d.event_time,(d.uniform_ci95||d.ci95||[])[0]]),dash:true},{name:'uniform 95% upper',pts:cs.dynamic.map(d=>[d.event_time,(d.uniform_ci95||d.ci95||[])[1]]),dash:true}];
  h+='<h3 style="margin-top:14px">Event study</h3>'+legend(S,'x')+lines(S,{title:'ATT by months since adoption',xlabel:'months since first qualifying record',ylabel:u.length>26?'effect':u})+
   '<p class="hint">Pre-trend check: '+esc(label((cs.pretrend||{}).status||'n/a'))+'. '+esc((cs.pretrend||{}).detail||'')+'</p>'}
 if(cs&&(cs.by_cohort||[]).length)h+=tableHTML(cs.by_cohort.map(c=>({cohort:c.group_label,att:c.att,ci_low:(c.ci95||[])[0],ci_high:(c.ci95||[])[1],units:c.units,post_periods:c.post_periods})),{name:'cs-cohorts',caption:'Effect by adoption-month cohort'});
 if(sd&&sd.unit_weights)h+=tableHTML(Object.entries(sd.unit_weights).map(([k,v])=>({practice_series:k,unit_weight:v})),{name:'sdid-unit-weights',caption:'Synthetic comparison: weights on unexposed practice series (pre-period RMSE '+fmt(sd.pre_fit_rmse,4)+')'});
 if(o.method_selection)h+='<p class="hint">Method: '+esc(label(o.estimation_method||''))+' ('+esc(o.method_selection)+').</p>';
 if((a.warnings||[]).length)h+='<details open><summary>Warnings ('+a.warnings.length+')</summary><ul>'+a.warnings.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 if((a.assumptions||[]).length)h+='<details><summary>Assumptions ('+a.assumptions.length+')</summary><ul>'+a.assumptions.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 return h+'</section>'}
/* demand hierarchy: coherence and reconciliation facts + the nodes table */
function vizReconcile(a){const rc=(a.overall||{}).reconciliation;if(!rc)return '';const rr=rc.reconciliation||{},co=rc.coherence||{},bt=rc.backtest||{};
 let h='<section class="sec" data-focus="reconciliation"><h3>Hierarchical reconciliation</h3><div class="kpis">'+
  [['Method',label(rc.applied?(rr.method||rc.method_requested):'not applied')],['Nodes / levels',fmt(rc.nodes)+' / '+fmt(rc.levels)],['Coherent',co.coherent==null?'—':(co.coherent?'children sum to parents':'no')],
   ['Gap before / after',isNum(co.base_max_abs_gap)?fmt(co.base_max_abs_gap,2)+' / '+fmt(co.max_abs_gap,2):'—'],['Shrinkage',isNum(rr.shrinkage_lambda)?fmt(rr.shrinkage_lambda,2):'—']].map(k=>'<div class="kpi"><div class="l">'+esc(k[0])+'</div><div class="v" style="font-size:16px">'+esc(k[1])+'</div></div>').join('')+'</div>';
 if(rc.applied===false&&rc.reason)h+='<p class="hint">'+esc(rc.reason)+'</p>';
 const lv=bt.by_level;if(lv)h+=tableHTML(Object.entries(lv).map(([k,v])=>({level:k,base_mase:v.base_mase,reconciled_mase:v.reconciled_mase})),{name:'reconcile-mase',caption:'Rolling-origin MASE by hierarchy level: unreconciled vs reconciled'});
 if((a.table||[]).length)h+=tableHTML(a.table,{name:'demand-hierarchy',caption:'Forecast by node'});
 return h+'</section>'}
/* risk-adjusted CUSUM / VLAD: path per unit and the signal table */
function vizRA(a){const o=(a.overall||{}).risk_adjusted||{},p=o.parameters||{},us=(o.units||[]).slice(0,6),cus=o.check==='ra_cusum',lim=o.control_limits||{};
 let h='<section class="sec" data-focus="ra"><h3>'+(cus?'Risk-adjusted CUSUM':'VLAD')+' · '+esc(label(p.outcome||''))+' by '+esc(p.unit||'')+'</h3><div class="kpis">'+
  [['Units charted',fmt((o.summary||{}).units_charted)],['Units with signals',fmt((o.summary||{}).units_with_signals)],['Cases',fmt((o.summary||{}).cases)],['Network rate',isNum((o.summary||{}).network_rate)?pct(o.summary.network_rate,1):'—'],
   [cus?'Control limit h':'Funnel limits',cus?fmt(lim.h,2):'95% / 99.8%']].map(k=>'<div class="kpi"><div class="l">'+esc(k[0])+'</div><div class="v">'+esc(k[1])+'</div></div>').join('')+'</div>';
 const S=us.map(u=>({name:u.unit,pts:((cus?(u.cusum_up||{}).path:(u.vlad||{}).path)||[]).map(q=>[q[0],q[2]])})).filter(s=>s.pts.length);
 if(S.length)h+=legend(S,'x')+lines(S,{title:cus?'CUSUM statistic by case number':'Cumulative expected minus observed by case number',xlabel:'cases in date order',ylabel:cus?'CUSUM':'expected - observed',extraY:cus&&isNum(lim.h)?[lim.h]:[],
  under:cus&&isNum(lim.h)?(X,Y)=>'<line x1="40" x2="9999" y1="'+Y(lim.h)+'" y2="'+Y(lim.h)+'" style="stroke:var(--bad);stroke-dasharray:4 3"/>':null});
 if((a.table||[]).length)h+=tableHTML(a.table,{name:'ra-units',caption:'Charted units (observed vs expected, signals)'});
 if((o.signals||[]).length)h+=tableHTML(o.signals.slice(0,50),{name:'ra-signals',caption:'Signals'});
 if((a.assumptions||[]).length)h+='<p class="hint"><b>Assumptions.</b> '+esc((a.assumptions||[]).join(' '))+'</p>';
 return h+'</section>'}
function yieldChart(S){const W=CW(),L=52,R=14,T=10,B=34,H=230;const kmax=Math.max(...S.map(s=>s.curve.length-1));
 const gain=s=>s.curve.map(r=>r.expected_value-s.curve[0].expected_value);const vs=S.flatMap(gain);let lo=Math.min(0,...vs),hi=Math.max(0.05,...vs);const pd=(hi-lo)*.06||.1;lo-=pd;hi+=pd;
 const X=k=>L+k/(kmax||1)*(W-L-R),Y=v=>T+(hi-v)/(hi-lo)*(H-T-B);let s=svgOpen(W,H,'Expected net value versus extra bookings per session');
 ticks(lo,hi,4).forEach(t=>{s+='<line class="gridl" x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(t)+'" y2="'+Y(t)+'"/><text x="'+(L-6)+'" y="'+(Y(t)+4)+'" text-anchor="end">'+esc(fmt(t,2))+'</text>'});
 for(let k=0;k<=kmax;k+=Math.max(1,Math.ceil(kmax/10)))s+='<text x="'+X(k)+'" y="'+(H-16)+'" text-anchor="middle">+'+k+'</text>';
 s+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(0)+'" y2="'+Y(0)+'" style="stroke:var(--fg);stroke-dasharray:4 3"/>';
 s+='<text x="'+((L+W-R)/2)+'" y="'+(H-2)+'" text-anchor="middle" style="fill:var(--muted)">extra bookings beyond capacity (gain vs no overbooking)</text>';
 S.forEach((ss,i)=>{const g=gain(ss),c=SER[i%SER.length];s+='<path d="'+g.map((v,k)=>(k?'L':'M')+X(k).toFixed(1)+' '+Y(v).toFixed(1)).join('')+'" style="fill:none;stroke:'+c+';stroke-width:2"/>';
  const k=ss.recommended_extra;s+='<circle cx="'+X(k)+'" cy="'+Y(g[k])+'" r="5" style="fill:'+c+';stroke:var(--card);stroke-width:1.5"><title>'+esc(ss.practice+': +'+k+' extra, gain '+fmt(g[k],3)+', P(overflow) '+pct(ss.p_overflow,0))+'</title></circle>'});
 return s+'</svg>'}
/* knowledge: cited passages from the VBC knowledge base (public sources, summarised) */
function kbLink(c){const u=String(c.url||'');return /^https?:\/\//i.test(u)?'<a href="'+esc(u)+'" target="_blank" rel="noopener noreferrer">'+esc(c.title||u)+'</a>':esc(c.title||'')}
function kbFacts(f){const ks=Object.keys(f||{}).slice(0,6);if(!ks.length)return '';return '<ul class="hint">'+ks.map(k=>{const v=f[k];
 return '<li><b>'+esc(label(k))+'</b>: '+esc(typeof v==='object'&&v!==null?JSON.stringify(v).slice(0,240):String(v).slice(0,240))+'</li>'}).join('')+'</ul>'}
function vizKnowledge(a){const k=(a.overall||{}).knowledge||{},rows=a.table||[];if(!rows.length)return '';
 let h='<section class="sec" data-focus="knowledge"><h3>Cited passages</h3><p class="hint">'+esc(fmt(rows.length))+' of '+esc(fmt((k.corpus||{}).entries))+
  ' knowledge entries · retrieval '+esc(k.retrieval||'')+(k.synthesis?' · synthesised by '+esc(k.synthesis.model||'model'):' · extractive (no model wrote the text)')+'</p>';
 rows.forEach(r=>{h+='<article class="sec"><h4>'+esc(r.n+'. '+(r.title||r.id))+'</h4><div class="hint">'+esc(label(r.topic||''))+' · confidence '+esc(r.confidence||'')+
  ' · verified '+esc(r.last_verified||'')+'</div><p>'+esc(r.summary||'')+'</p>'+kbFacts(r.facts);
  (r.contradictions||[]).slice(0,3).forEach(c=>{h+='<p class="hint"><b>Sources disagree:</b> '+esc(c.claim||'')+' — '+esc(c.note||'')+'</p>'});
  h+='<ol class="hint">'+(r.citations||[]).map(c=>'<li>'+kbLink(c)+' · '+esc(c.publisher||'')+(c.date?' · '+esc(c.date):'')+(c.quote?' — “'+esc(c.quote)+'”':'')+'</li>').join('')+'</ol></article>'});
 const s=k.sources||[];if(s.length)h+='<h4>Sources</h4><ol class="hint">'+s.map(c=>'<li value="'+esc(c.n)+'">'+kbLink(c)+' · '+esc(c.publisher||'')+(c.date?' · '+esc(c.date):'')+'</li>').join('')+'</ol>';
 return h+'</section>'}
function vizBriefing(a){const rows=a.table||[],r=(a.overall||{}).briefing||{};let h='<section class="sec" data-focus="briefing"><h3>Briefing, ranked</h3>'+
 '<p class="hint">'+esc(r.model||'')+(r.pairs?' · '+fmt(r.pairs)+' preference pairs from '+fmt(r.events||0)+' feedback events':'')+'</p><ol>';
 rows.forEach(c=>{h+='<li><b>'+esc(label(c.kind||''))+'</b>'+(c.explore?' <span class="xp">explore</span>':'')+': '+esc(c.headline||'')+
  '<div class="hint">'+esc(c.why||'')+'</div>'+(c.follow_up?'<button type="button" class="btn small" data-ask-standalone="'+esc(c.follow_up)+'" data-brief-card="'+esc(c.card_id||'')+'">Ask: '+esc(c.follow_up)+'</button>':'')+'</li>'});
 return h+'</ol>'+csvButton(rows,'briefing')+'</section>'}
function vizClaims(a){const o=a.overall||{},c=o.claims_review||{},d=o.denials||{},u=o.underpayment||{},rows=a.table||[];
 const exp=rows.reduce((t,r)=>t+(isNum(r.expected_dollars)?r.expected_dollars:0),0);
 let h='<section class="sec" data-focus="claims"><h3>Claims review — '+fmt(c.claims)+' claims, '+fmt(c.providers)+' providers, '+fmt((c.payers||[]).length)+' payers</h3><div class="kpis">'+
  [['Review flags',fmt(rows.length)],['Expected $ at stake',usdK(exp)],['First-pass denial rate',isNum(d.first_pass_denial_rate)?pct(d.first_pass_denial_rate,1):'—'],
   ['Overturn rate',isNum(d.overturn_rate)?pct(d.overturn_rate,0):'—'],['Written off',isNum(d.written_off)?usdK(d.written_off):'—'],
   ['Underpaid claims',isNum(u.flagged)?fmt(u.flagged)+' of '+fmt(u.claims_compared):'—']].map(k=>'<div class="kpi"><div class="l">'+k[0]+'</div><div class="v">'+esc(k[1])+'</div></div>').join('')+'</div>';
 const leak=(c.leakage_pareto||[]).map(x=>({label:x.leak,value:x.dollars,cum:x.cumulative_share}));
 const den=(d.pareto||[]).map(x=>({label:x.reason,value:x.denials,cum:x.cumulative_share}));
 if(leak.length)h+='<h3 style="margin-top:12px">Where the money leaks (Pareto)</h3>'+paretoChart(leak.slice(0,12),usdK);
 if(den.length)h+='<h3 style="margin-top:12px">Denial reasons (Pareto)</h3>'+paretoChart(den.slice(0,12),v=>fmt(v));
 if(leak.length||den.length)h+='<div class="legend"><span><span class="sw" style="background:var(--s1)"></span>amount</span><span><span class="sw" style="background:var(--warn);height:3px"></span>cumulative share</span></div>';
 h+='</section>';
 if(rows.length)h+='<section class="sec"><h3>Review flags, ranked by expected dollars</h3>'+tableHTML(rows,{name:'claims-review-flags',ask:r=>r.follow_up||null,
  cols:['rank','category','subject','reason','dollars_at_stake','probability','expected_dollars','q_value'],head:{dollars_at_stake:'$ at stake',expected_dollars:'expected $',q_value:'q (BH)'},
  render:{dollars_at_stake:v=>esc(usd(v)),expected_dollars:v=>esc(usd(v)),probability:v=>esc(pct(v,0)),category:v=>esc(label(v||''))},
  caption:'Review prompts, not findings of fraud. Select a flag to ask its follow-up.'})+'</section>';
 else h+='<p class="hint">No review flags at the stated false-discovery rate.</p>';
 if(d.table&&d.table.length)h+='<details><summary>Denials by '+esc(label(d.by||'payer'))+' ('+d.table.length+')</summary>'+tableHTML(d.table,{name:'claims-denials',
  cols:['group','claims','first_pass_denial_rate','denial_rate_shrunk','overturn_rate','median_days_to_resolution','recovered','written_off','pending'],
  render:{first_pass_denial_rate:v=>esc(pct(v,1)),denial_rate_shrunk:v=>esc(pct(v,1)),overturn_rate:v=>esc(pct(v,0)),recovered:v=>esc(usd(v)),written_off:v=>esc(usd(v)),pending:v=>esc(usd(v))}})+'</details>';
 if(o.providers&&(o.providers.units||[]).length)h+='<details><summary>Provider peer profiles ('+o.providers.units.length+')</summary>'+tableHTML(o.providers.units,{name:'claims-providers'})+'</details>';
 if(u.cells&&u.cells.length)h+='<details><summary>Expected payment by payer × service × setting ('+u.cells.length+')</summary>'+tableHTML(u.cells,{name:'claims-underpayment-cells'})+'</details>';
 if(o.benford&&o.benford.length)h+='<details><summary>Benford tests ('+o.benford.length+' groups)</summary>'+tableHTML(o.benford,{name:'claims-benford'})+'</details>';
 if((c.notes||[]).length)h+='<p class="hint">'+c.notes.map(esc).join(' ')+'</p>';
 return h}
/* Pareto: bars (left axis) largest first plus cumulative share line (right axis, 0-100%); rows {label,value,cum} */
function paretoChart(rows,f){const W=CW(),T=10,B=74,L=58,R=40,H=T+190+B;const n=rows.length,hi=Math.max(...rows.map(r=>r.value).filter(isNum),1);
 const bw=(W-L-R)/n,X=i=>L+i*bw+bw/2,Y=v=>T+190-(v/hi)*190,Yc=q=>T+190-q*190;let s=svgOpen(W,H,'Pareto chart: amount per category with cumulative share');
 ticks(0,hi,4).forEach(t=>{s+='<line class="gridl" x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(t)+'" y2="'+Y(t)+'"/><text x="'+(L-6)+'" y="'+(Y(t)+4)+'" text-anchor="end">'+esc(f(t))+'</text>'});
 [0,.5,1].forEach(q=>{s+='<text x="'+(W-R+6)+'" y="'+(Yc(q)+4)+'">'+(q*100)+'%</text>'});
 rows.forEach((r,i)=>{const x=X(i),w=Math.max(4,bw*.7),lab=String(r.label);
  s+='<rect x="'+(x-w/2)+'" y="'+Y(r.value)+'" width="'+w+'" height="'+Math.max(1,T+190-Y(r.value))+'" rx="2" style="fill:var(--s1)"><title>'+esc(lab+': '+f(r.value)+(isNum(r.cum)?' (cumulative '+pct(r.cum,0)+')':''))+'</title></rect>';
  s+='<text transform="translate('+x+','+(T+198)+') rotate(35)" style="fill:var(--fg)">'+esc(lab.length>26?lab.slice(0,25)+'…':lab)+'</text>'});
 const pts=rows.map((r,i)=>isNum(r.cum)?X(i)+','+Yc(r.cum):null).filter(Boolean);
 if(pts.length>1)s+='<polyline points="'+pts.join(' ')+'" style="fill:none;stroke:var(--warn);stroke-width:2"/>';
 rows.forEach((r,i)=>{if(isNum(r.cum))s+='<circle cx="'+X(i)+'" cy="'+Yc(r.cum)+'" r="3" style="fill:var(--warn)"/>'});
 s+='<line class="axis" x1="'+L+'" x2="'+(W-R)+'" y1="'+(T+190)+'" y2="'+(T+190)+'"/>';
 return s+'</svg>'}
function calNote(a){const c=((a&&a.overall)||{}).calibration;if(!c||!c.statement)return '';
 return '<p class="hint" data-calibration="1"><b>Calibration.</b> '+esc(c.statement)+(c.method?'<details class="why"><summary>Method</summary><span class="muted" style="font-size:12px">'+esc(c.method)+'</span></details>':'')+'</p>'}
function visual(a){
 if(a.answer_type==='composite')return (a.sections||[]).map(s=>'<section class="sec"><h3>'+esc(s.title)+'</h3>'+headlineHTML(s.answer)+warnBox(s.answer)+(visual0(s.answer)||'')+calNote(s.answer)+(s.action_context?'<p><a href="'+esc(s.action_context.url)+'">Open this output’s workspace ↗</a></p>':'')+'</section>').join('');
 const v=visual0(a);return v==null?v:v+calNote(a)}
/* early signal: learned pre-event template per channel (whitened units, months before the event) + patients ranked by matched-filter SNR */
function vizEarlySignal(a){const o=a.overall||{},es=o.early_signal||{},ev=es.evaluation||{},rows=(a.table||[]).slice(0,20);
 const kp=(l,v)=>'<div class="kpi"><div class="l">'+l+'</div><div class="v">'+esc(v)+'</div></div>',pc=v=>isNum(v)?pct(v,1):'—';
 let h='<section class="sec" data-focus="signal"><h3>Pre-event signature of '+esc(es.outcome_label||'the event')+'</h3><div class="kpis">'+
  kp('Flagged',fmt(es.flagged)+' of '+fmt(es.patients_scored))+kp('SNR threshold',isNum(es.threshold_snr)?fmt(es.threshold_snr,2):'—')+kp('Template events',fmt(es.training_events))+
  kp('Background samples',fmt(es.background_samples))+'</div>';
 const tp=(es.templates||[]).filter(t=>t.energy>0).slice(0,6);
 if(tp.length){const series=tp.map(t=>({name:t.label,pts:t.template.map((v,i)=>[t.months_before_event[i],v])}));const key='sig'+(++state.seq);state[key]={series,draw:sigTemplate};
  h+=legend(series,key)+'<div data-chart="'+key+'">'+sigTemplate(state[key])+'</div>'}
 else h+='<p class="hint">No channel carries a template above its noise (all cells shrunk to zero).</p>';
 if(rows.length)h+='<h3 style="margin-top:12px">Patients ranked by matched-filter SNR</h3>'+sigChart(rows,es.threshold_snr);
 if(ev.status==='evaluated'){const m=ev.matched_filter||{},b=ev.baseline||{},mv=ev.baseline_at_matched_volume||{};
  h+='<h3 style="margin-top:12px">Time-split check (cutoff '+esc(ev.cutoff)+', labels '+esc(ev.label_months)+')</h3><div class="kpis">'+
   kp('Precision',pc(m.precision)+' vs '+pc(b.precision))+kp('Recall',pc(m.recall)+' vs '+pc(b.recall))+kp('AUC',(isNum(m.auc)?fmt(m.auc,2):'—')+' vs '+(isNum(b.auc)?fmt(b.auc,2):'—'))+
   kp('Median lead (months)',(m.median_lead_months!=null?fmt(m.median_lead_months,1):'—'))+kp('Events',fmt(ev.positives)+' in '+fmt(ev.patient_months)+' patient-months')+'</div>'+
   '<p class="hint">Matched filter vs baseline ('+esc(b.rule||'')+'). Baseline at the same alert volume: precision '+pc(mv.precision)+'. False-alarm rate on later non-event months: '+pc(ev.null_false_alarm_rate_test)+'.</p>'}
 else if(ev.reason)h+='<p class="hint">Not evaluated: '+esc(ev.reason)+'</p>';
 (es.notes||[]).forEach(n=>{h+='<p class="hint">'+esc(n)+'</p>'});
 return h+tableHTML(a.table,{name:'early-signal-patients',cols:['patient_id','snr','false_alarm_probability','alert','aligned_lead_months','driving_channels','acute_visits_last_3_months','event_in_window'],
  head:{snr:'SNR',false_alarm_probability:'false-alarm prob.',aligned_lead_months:'lead (months)',acute_visits_last_3_months:'acute visits 3 mo'},render:{false_alarm_probability:v=>isNum(v)?esc(v<0.001?'<0.001':v.toFixed(3)):''}})+'</section>'}
function sigTemplate(st){const xs=st.series.flatMap(s=>s.pts.map(p=>p[0]));return lines(st.series,{title:'Learned template: whitened deviation by month before the event',xlabel:'months before the event',
 ylabel:'template (SD units)',extraY:[0],xtick:v=>String(Math.round(v)),under:(X,Y)=>'<line x1="'+X(Math.min(...xs))+'" x2="'+X(Math.max(...xs))+'" y1="'+Y(0)+'" y2="'+Y(0)+'" style="stroke:var(--muted);stroke-dasharray:3 3"/>'})}
function sigChart(rows,thr){const W=CW(),lh=22,L=Math.min(Math.round(W*.36),12+7*Math.max(...rows.map(r=>String(r.patient_id).length))),R=70,T=8,H=T+rows.length*lh+28;
 const vs=rows.map(r=>r.snr).concat([thr,0]).filter(isNum);const lo=Math.min(...vs),hi=Math.max(...vs)*1.05||1;const X=v=>L+(v-lo)/(hi-lo||1)*(W-L-R);
 let s=svgOpen(W,H,'Matched-filter SNR per patient with the false-alarm threshold');
 ticks(lo,hi,W<480?3:5).forEach(t=>{s+='<line class="gridl" x1="'+X(t)+'" x2="'+X(t)+'" y1="'+T+'" y2="'+(H-24)+'"/><text x="'+X(t)+'" y="'+(H-8)+'" text-anchor="middle">'+esc(fmt(t,1))+'</text>'});
 if(isNum(thr))s+='<line x1="'+X(thr)+'" x2="'+X(thr)+'" y1="'+T+'" y2="'+(H-24)+'" style="stroke:var(--warn);stroke-dasharray:4 3"/>';
 rows.forEach((r,i)=>{const y=T+i*lh,c=r.alert?'var(--bad)':'var(--s3)',f=isNum(r.false_alarm_probability)?(r.false_alarm_probability<0.001?'<0.001':r.false_alarm_probability.toFixed(3)):'';
  s+='<g tabindex="0" aria-label="'+esc(r.patient_id+': SNR '+fmt(r.snr,2)+', false-alarm probability '+f)+'"><text x="'+(L-6)+'" y="'+(y+lh/2+4)+'" text-anchor="end" style="fill:var(--fg)">'+esc(r.patient_id)+'</text>';
  if(isNum(r.snr))s+='<rect x="'+Math.min(X(0),X(r.snr))+'" y="'+(y+4)+'" width="'+Math.max(1,Math.abs(X(r.snr)-X(0)))+'" height="'+(lh-8)+'" rx="3" style="fill:'+c+'"><title>'+esc((r.driving_channels||[]).join(', '))+'</title></rect>';
  s+='<text x="'+(Math.max(X(0),X(isNum(r.snr)?r.snr:0))+4)+'" y="'+(y+lh/2+4)+'">'+esc(fmt(r.snr,2)+' · p='+f)+'</text></g>'});
 return s+'</svg><div class="legend"><span><span class="sw" style="background:var(--bad)"></span>above threshold</span><span><span class="sw" style="background:var(--s3)"></span>below</span><span>┊ threshold at the false-alarm rate</span></div>'}
/* risk adjustment: KPI tiles, payment-RAF distribution (histogram), RAF by practice, then the gap / suspect / patient table */
function vizRisk(a){const o=a.overall||{},ra=o.risk_adjustment||{},p=o.population||{},pay=ra.payment||{},rows=a.table||[];
 const kp=(l,v)=>'<div class="kpi"><div class="l">'+l+'</div><div class="v">'+esc(v)+'</div></div>',r=p.payment_raf||{},rc=p.recapture||{},cc=p.coding_completeness||{},ds=p.dollars_at_stake||{};
 const blend=Object.entries(pay.blend||{}).map(([m,w])=>Math.round(w*100)+'% '+m).join(' + ');
 let h='<section class="sec" data-focus="risk"><h3>CMS-HCC risk adjustment — '+fmt(p.patients_in_scope)+' patients, collection year '+esc((ra.period||{}).collection_year)+', PY'+esc(pay.payment_year)+' ('+esc(blend)+')</h3><div class="kpis">'+
  kp('Mean payment RAF',isNum(r.mean)?fmt(r.mean,3):'—')+kp('Raw V24 / V28',(isNum((p.raw||{}).V24&&p.raw.V24.mean)?fmt(p.raw.V24.mean,3):'—')+' / '+(isNum((p.raw||{}).V28&&p.raw.V28.mean)?fmt(p.raw.V28.mean,3):'—'))+
  kp('Coding completeness',isNum(cc.rate)?pct(cc.rate,1):'—')+kp('Recapture (prior year)',isNum(rc.recapture_rate)?pct(rc.recapture_rate,1):'—')+
  kp('Open recapture gaps',fmt(rc.open_gaps_current_year)+' · '+usdK(ds.recapture||0)+'/yr')+kp('Suspect prompts',fmt((p.suspects||[]).reduce((t,s)=>t+s.patients,0))+' · '+usdK(ds.suspects||0)+'/yr')+'</div>';
 const hist=p.histogram||[];if(hist.length){const W=CW(),L=46,R=12,T=10,B=40,H=200,n=hist.length,hi=Math.max(1,...hist.map(b=>b.patients)),bw=(W-L-R)/n,Y=v=>T+(H-T-B)*(1-v/hi);
  let s=svgOpen(W,H,'Distribution of payment RAF');ticks(0,hi,4).forEach(t=>{s+='<line class="gridl" x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(t)+'" y2="'+Y(t)+'"/><text x="'+(L-6)+'" y="'+(Y(t)+4)+'" text-anchor="end">'+esc(fmt(t))+'</text>'});
  hist.forEach((b,i)=>{const x=L+i*bw,lab=fmt(b.from,2)+(b.to==null?'+':'–'+fmt(b.to,2));s+='<rect x="'+(x+bw*.1)+'" y="'+Y(b.patients)+'" width="'+(bw*.8)+'" height="'+Math.max(1,H-B-Y(b.patients))+'" rx="2" style="fill:var(--s1)"><title>'+esc(lab+': '+b.patients+' patients')+'</title></rect>'+
   '<text x="'+(x+bw/2)+'" y="'+(H-B+14)+'" text-anchor="middle">'+esc(lab)+'</text>'});
  h+='<h3 style="margin-top:12px">Payment RAF distribution</h3>'+s+'</svg>'}
 const by=(p.by||{}).practice||[];if(by.length)h+='<details open><summary>RAF by practice ('+by.length+')</summary>'+tableHTML(by,{name:'risk-by-practice',
  cols:['group','patients','mean_payment_raf','median_payment_raf','mean_raw_v24','mean_raw_v28','open_recapture_gaps','suspects','dollars_at_stake'],render:{dollars_at_stake:v=>esc(usd(v))}})+'</details>';
 const sus=p.suspects||[];if(sus.length)h+='<details><summary>Suspect categories ('+sus.length+')</summary>'+tableHTML(sus,{name:'risk-suspects',render:{annual_value_if_documented:v=>esc(usd(v))}})+'</details>';
 h+='</section>';
 if(rows.length)h+='<section class="sec"><h3>Gaps and patients</h3>'+tableHTML(rows,{name:'risk-adjustment',render:{annual_value:v=>esc(usd(v)),annual_value_if_documented:v=>esc(usd(v)),dollars_at_stake:v=>esc(usd(v))},
  caption:'Suspects are documentation review prompts, not diagnoses. '+esc(pay.base_rate_note||'')})+'</section>';
 if((ra.data_gaps||[]).length)h+='<details><summary>Data limits ('+ra.data_gaps.length+')</summary><ul>'+ra.data_gaps.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 if((ra.assumptions||[]).length)h+='<p class="hint"><b>Assumptions.</b> '+esc(ra.assumptions.join(' '))+'</p>';
 return h}
/* provider network: KPI tiles, community-grouped circular graph (deterministic, no force layout), then focus tables */
function netGraph(v){const ns=(v||{}).nodes||[],es=(v||{}).edges||[];if(ns.length<2)return '';
 const W=CW(),H=Math.min(560,Math.max(320,Math.round(W*.72))),cx=W/2,cy=H/2,R=Math.min(W,H)/2-46;
 const groups={};ns.forEach(n=>{const k=n.community==null?'isolated':String(n.community);(groups[k]=groups[k]||[]).push(n)});
 const keys=Object.keys(groups).sort((a,b)=>a==='isolated'?1:b==='isolated'?-1:(groups[b].length-groups[a].length)||(+a-+b));
 const gap=2,slots=ns.length+gap*keys.length,pos={},maxPr=Math.max(...ns.map(n=>n.pagerank||0),1e-9),maxW=Math.max(...es.map(e=>e[2]),1);let i=0,labels='';
 keys.forEach(k=>{const g=groups[k].slice().sort((a,b)=>(b.pagerank-a.pagerank)||(a.id<b.id?-1:1)),a0=i;
  g.forEach(n=>{const t=2*Math.PI*i/slots-Math.PI/2;pos[n.id]=[cx+R*Math.cos(t),cy+R*Math.sin(t)];i++});
  const tm=2*Math.PI*((a0+i-1)/2)/slots-Math.PI/2,lx=cx+(R+26)*Math.cos(tm),ly=cy+(R+26)*Math.sin(tm);
  labels+='<text x="'+lx.toFixed(1)+'" y="'+(ly+4).toFixed(1)+'" text-anchor="'+(Math.cos(tm)>.3?'start':Math.cos(tm)<-.3?'end':'middle')+'" style="fill:var(--fg);font-weight:600">'+esc(k==='isolated'?'isolated':'C'+k)+' ('+g.length+')</text>';i+=gap});
 let s=svgOpen(W,H,'Provider patient-sharing network grouped by community');
 es.forEach(e=>{const na=ns[e[0]]||{},nb=ns[e[1]]||{},a=pos[na.id],b=pos[nb.id],w=e[2];if(!a||!b)return;const ca=na.community,cb=nb.community;
  s+='<path d="M'+a[0].toFixed(1)+','+a[1].toFixed(1)+' Q'+cx.toFixed(1)+','+cy.toFixed(1)+' '+b[0].toFixed(1)+','+b[1].toFixed(1)+'" style="fill:none;stroke:'+(ca===cb&&ca!=null?SER[ca%SER.length]:'var(--muted)')+
   ';stroke-opacity:'+(0.12+0.55*w/maxW).toFixed(2)+';stroke-width:'+(0.6+2.4*w/maxW).toFixed(2)+'"><title>'+esc(na.id+' – '+nb.id+': '+w+' shared patients')+'</title></path>'});
 ns.forEach(n=>{const p=pos[n.id],r=2.5+7*Math.sqrt((n.pagerank||0)/maxPr),c=n.community==null?'var(--muted)':SER[n.community%SER.length];
  s+='<circle cx="'+p[0].toFixed(1)+'" cy="'+p[1].toFixed(1)+'" r="'+r.toFixed(1)+'" style="fill:'+c+(n.in_network===false?';stroke:var(--bad);stroke-width:2':'')+'"><title>'+
   esc(n.id+' ('+n.specialty+') · '+(n.organization||'')+' · '+n.patients+' patients, degree '+n.degree+', PageRank '+fmt(n.pagerank,4)+', betweenness '+fmt(n.betweenness,4)+(n.in_network===false?' · OUTSIDE network':''))+'</title></circle>'});
 return s+labels+'</svg><div class="legend"><span>node size = PageRank</span><span>colour = community</span><span>curve = shared patients (darker = more)</span><span><span class="sw" style="background:var(--bad);height:3px"></span>outside network</span>'+
  '<span>showing '+fmt(v.shown_nodes)+' nodes, '+fmt(v.shown_edges)+' edges</span></div>'}
function spreadChart(sp){const pts=sp.over_time||[];if(pts.length<2)return '';const W=CW(),H=220,L=46,R=12,T=10,B=34,n=pts.length,hi=Math.max(1,...pts.map(p=>Math.max(p.p90,p.expected_pcps,p.degree_seeds_expected_pcps)));
 const X=i=>L+(W-L-R)*i/(n-1),Y=v=>T+(H-T-B)*(1-v/hi);let s=svgOpen(W,H,'Expected PCPs adopting over diffusion rounds (model)');
 ticks(0,hi,4).forEach(t=>{s+='<line class="gridl" x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(t)+'" y2="'+Y(t)+'"/><text x="'+(L-6)+'" y="'+(Y(t)+4)+'" text-anchor="end">'+esc(fmt(t))+'</text>'});
 s+='<path d="'+pts.map((p,i)=>(i?'L':'M')+X(i).toFixed(1)+','+Y(p.p90).toFixed(1)).join('')+pts.slice().reverse().map((p,j)=>'L'+X(n-1-j).toFixed(1)+','+Y(p.p10).toFixed(1)).join('')+'Z" style="fill:var(--band)"/>';
 s+='<path d="'+pts.map((p,i)=>(i?'L':'M')+X(i).toFixed(1)+','+Y(p.degree_seeds_expected_pcps).toFixed(1)).join('')+'" style="fill:none;stroke:var(--muted);stroke-dasharray:4 3;stroke-width:1.5"/>';
 s+='<path d="'+pts.map((p,i)=>(i?'L':'M')+X(i).toFixed(1)+','+Y(p.expected_pcps).toFixed(1)).join('')+'" style="fill:none;stroke:var(--s1);stroke-width:2"/>';
 pts.forEach((p,i)=>{s+='<circle cx="'+X(i)+'" cy="'+Y(p.expected_pcps)+'" r="3" style="fill:var(--s1)"><title>'+esc('round '+p.step+': '+fmt(p.expected_pcps)+' PCPs ('+fmt(p.p10)+'–'+fmt(p.p90)+'), ~'+fmt(p.expected_attributed_patients)+' attributed patients')+'</title></circle><text x="'+X(i)+'" y="'+(H-B+14)+'" text-anchor="middle">'+p.step+'</text>'});
 return s+'</svg><div class="legend"><span><span class="sw" style="background:var(--s1)"></span>greedy seeds (expected)</span><span><span class="sw" style="background:var(--band);height:10px"></span>10th–90th percentile</span><span>┄ top-degree seeds</span><span>x = diffusion round</span></div>'}
function vizNetwork(a){const o=(a.overall||{}).provider_network||{},g=o.graph||{},p=o.parameters||{},rows=a.table||[],lk=(o.leakage||{}).overall||{},co=o.cohesion||{},cc=o.concentration||{};
 const kp=(l,v)=>'<div class="kpi"><div class="l">'+esc(l)+'</div><div class="v">'+esc(v)+'</div></div>';
 let h='<section class="sec" data-focus="network"><h3>Provider network — '+fmt(g.providers)+' '+esc(p.level||'')+'s, '+fmt(g.patients)+' patients, '+esc((o.period||{}).start||'')+' to '+esc((o.period||{}).end||'')+'</h3><div class="kpis">'+
  kp('Significant edges',fmt(g.edges)+' of '+fmt(g.candidate_edges))+kp('Communities',fmt((o.communities||[]).length)+' · Q '+fmt(g.modularity,2))+
  kp('Leakage (visits)',isNum(lk.leakage_share)?pct(lk.leakage_share,1):'—')+kp('Leaked claim dollars',usdK(lk.outside_dollars))+
  kp('Care density (mean)',isNum(co.mean)?fmt(co.mean,1):'—')+kp('Referral HHI (mean)',isNum(cc.mean_hhi_organizations)?fmt(cc.mean_hhi_organizations,0):'—')+'</div>';
 h+=netGraph(o.visual)+'</section>';
 const sp=o.spread;if(sp&&sp.status==='model')h+='<section class="sec"><h3>Spread model (not an observation) — '+esc(sp.model.replace(/_/g,' '))+' over the '+esc(sp.graph.replace(/_/g,' '))+' graph</h3>'+spreadChart(sp)+
  tableHTML(sp.seeds||[],{name:'network-seeds',caption:'Seeds chosen by '+esc(sp.seed_strategy)+' influence maximisation; '+esc(fmt(sp.runs))+' Monte Carlo runs, seed '+esc(sp.random_seed)})+'</section>';
 const st=o.steering;if(st)h+='<section class="sec"><h3>Steering model — '+esc(st.service_line)+' to '+esc(st.target||'no target')+'</h3><div class="kpis">'+kp('Pool visits',fmt(st.pool_visits))+kp('Recovered visits',fmt(st.recovered_visits))+
  kp('Recovered dollars',usdK(st.recovered_dollars))+kp('Target volume change',isNum(st.target_volume_increase)?'+'+pct(st.target_volume_increase,0):'—')+'</div></section>';
 if(rows.length)h+='<section class="sec"><h3>'+esc(label(p.focus||'all'))+'</h3>'+tableHTML(rows,{name:'network-'+(p.focus||'all'),render:{claim_dollars:v=>esc(usd(v)),outside_dollars:v=>esc(usd(v)),pool_dollars:v=>esc(usd(v)),recovered_dollars:v=>esc(usd(v))}})+'</section>';
 const by=(o.leakage||{}).by||[];if(p.focus!=='leakage'&&by.length)h+='<details><summary>Leakage by '+esc((o.leakage||{}).group_by||'')+' ('+by.length+')</summary>'+tableHTML(by,{name:'network-leakage',render:{claim_dollars:v=>esc(usd(v)),outside_dollars:v=>esc(usd(v))}})+'</details>';
 if(p.focus!=='communities'&&(o.communities||[]).length)h+='<details><summary>Communities ('+o.communities.length+')</summary>'+tableHTML(o.communities,{name:'network-communities'})+'</details>';
 if(p.focus!=='usage'&&(o.usage||[]).length)h+='<details><summary>Organisations patients use ('+o.usage.length+')</summary>'+tableHTML(o.usage,{name:'network-usage',render:{claim_dollars:v=>esc(usd(v))}})+'</details>';
 if((o.notes||[]).length)h+='<ul class="hint">'+o.notes.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>';
 if((o.data_gaps||[]).length)h+='<details><summary>Data limits ('+o.data_gaps.length+')</summary><ul>'+o.data_gaps.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 if((o.assumptions||[]).length)h+='<p class="hint"><b>Assumptions.</b> '+esc(o.assumptions.join(' '))+'</p>';
 return h}
/* contract strategy: KPI tiles, net-value distribution per option (step histograms), 5th-95th intervals, ranking flips,
   Nash negotiation frontier (MSR x sharing rate: break-even and equal-value curves, bargaining bounds, Nash point) */
function vizContractPerformance(a){const c=a.overall.contract_performance,s=c.summary||{},q=c.quality||{},qo=q.overall||{},terms=c.contract||{};
 const money=v=>v==null?'Unresolved':usd(v/100),kp=(l,v)=>'<div class="kpi"><div class="l">'+esc(l)+'</div><div class="v">'+esc(v)+'</div></div>';
 let h='<section class="sec" data-focus="contract"><h3>'+esc(terms.label||'Contract performance')+'</h3><p class="hint">Authored synthetic terms · '+esc(terms.id)+' · version '+esc(terms.version)+' · '+esc((c.period||{}).start)+' to '+esc((c.period||{}).end)+'. Both views use the same saved cohort and calculations.</p>';
 h+='<div class="kpis">'+kp('Attributed members',fmt(s.members))+kp('Recorded member-months',fmt(s.recorded_member_months))+kp('Observed allowed cost',money(s.observed_allowed_cents))+kp('Authored benchmark',money(s.benchmark_cents))+kp('Approximate quality',qo.denominator==null?'Unresolved':fmt(qo.numerator)+' / '+fmt(qo.denominator))+'</div>';
 h+='<details open data-contract-section="provider"><summary>Provider / population health view</summary><p>Illustrative provider share: <strong>'+esc(money(s.illustrative_provider_share_cents))+'</strong>. Quality target '+(s.approximate_quality_target_met==null?'is unresolved':s.approximate_quality_target_met?'is met':'is not met')+' under the authored demo rule. Review observed cost and quality evidence before choosing internal follow-up.</p></details>';
 h+='<details data-contract-section="payer"><summary>Payer view</summary><p>Illustrative retained positive benchmark variance: <strong>'+esc(money(s.illustrative_payer_retained_positive_variance_cents))+'</strong>. This is scenario arithmetic, not realized savings, cash or settlement. Nonmetric claims and claims without the same attributed member-month remain outside the ledger.</p></details>';
 h+='<details data-contract-section="monthly"><summary>Monthly cost and exposure</summary>'+tableHTML((c.monthly||[]).map(r=>({'Month':r.month,'Member-months':r.recorded_member_months,'Allowed cost':money(r.observed_allowed_cents),'Benchmark':money(r.benchmark_cents)})),{name:'contract-monthly'})+'</details>';
 h+='<details data-contract-section="practices"><summary>Practice contributions · descriptive drivers</summary>'+tableHTML((c.practice_drivers||[]).map(r=>({'Practice':r.practice_id,'Member-months':r.recorded_member_months,'Allowed cost':money(r.observed_allowed_cents),'Benchmark variance':money(r.observed_variance_cents)})),{name:'contract-practices'})+'</details>';
 h+='<details data-contract-section="members"><summary>Members and internal review needs</summary>'+tableHTML(a.table||[],{name:'contract-members'})+'</details>';
 if(c.editing_available)h+='<details data-contract-section="assumptions"><summary>Revise demo assumptions</summary><div data-contract-editor="'+esc(terms.id)+'" data-binding="'+esc(c.contract_digest)+'"><p class="hint">Save a versioned authored assumption and recalculate this report. Earlier answers and work remain in history; affected work needs a current binding. These inputs are not a payer offer or care instruction.</p><div class="contract-input-grid"><label>Benchmark per member-month ($)<input data-contract-benchmark value="'+esc(terms.benchmark_pmpm_cents/100)+'" inputmode="decimal"></label><label>Sharing rate (%)<input data-contract-sharing value="'+esc(terms.sharing_rate_bps/100)+'" inputmode="decimal"></label><label>Approximate quality target (%)<input data-contract-quality value="'+esc(terms.quality.target_bps/100)+'" inputmode="decimal"></label><label>Reason for the change<input data-contract-reason></label></div><div class="tools"><button type="button" class="btn primary" data-contract-save>Save assumptions and recalculate</button><button type="button" class="btn" data-contract-restore="base">Restore packaged demo inputs</button></div><p data-contract-message role="status"></p></div></details>';
 h+='<p class="hint">Quality uses the registered approximation. Clinical suitability, complete capture and payment entitlement are unestablished. Save or reopen the evidence and internal review work from this answer.</p></section>';return h;}
async function saveContractInput(el){const box=el.closest('[data-contract-editor]'),message=box.querySelector('[data-contract-message]');
 const scaled=selector=>{const text=box.querySelector(selector).value.trim(),m=/^(\d+)(?:\.(\d{1,2}))?$/.exec(text);if(!m)throw Error('Use a nonnegative amount with at most two decimal places.');const n=Number(m[1])*100+Number((m[2]||'').padEnd(2,'0'));if(!Number.isSafeInteger(n))throw Error('This value cannot be represented exactly by this input.');return n};
 try{const reason=box.querySelector('[data-contract-reason]').value.trim();if(!reason)throw Error('Enter a reason so the change remains traceable.');const restore=el.dataset.contractRestore||'',changes=restore?{}:{benchmark_pmpm_cents:scaled('[data-contract-benchmark]'),sharing_rate_bps:scaled('[data-contract-sharing]'),quality_target_bps:scaled('[data-contract-quality]')};el.disabled=true;message.textContent='Saving the versioned demo input…';
 const response=await fetch('/api/stack/contract-input/save',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({contract_id:box.dataset.contractEditor,expected_digest:box.dataset.binding,changes,restore_revision:restore,reason})});const data=await response.json();if(!response.ok||data.error)throw Error(data.error||'The input could not be saved.');message.textContent='Input saved. Recalculating from the current version…';await ask(data.question,{standalone:true});
 }catch(error){message.textContent=String(error.message||error);el.disabled=false}}
function vizContract(a){const cs=(a.overall||{}).contract_strategy||{},rows=a.table||[],p=cs.parameters||{},n=cs.negotiation,sim=cs.simulation||{};
 const kp=(l,v)=>'<div class="kpi"><div class="l">'+l+'</div><div class="v">'+esc(v)+'</div></div>',b=rows[0]||{},lam=cs.risk_aversion,cf=cs.confidence;
 let h='<section class="sec" data-focus="contract"><h3>Contract strategy — benchmark $'+fmt(p.benchmark_pmpm,2)+' PMPM, base cost $'+fmt(sim.base_pmpm,2)+' PMPM, '+
  fmt(sim.members)+' members, '+fmt(sim.draws)+' draws</h3>';
 if(rows.length)h+='<div class="kpis">'+kp(rows.length>1?'Best risk-adjusted':'Contract',b.option||'—')+kp('Expected net',usd(b.expected_net))+
  kp('Upside call / downside put',usdK(b.upside_value)+' / '+usdK(-b.downside_cost))+kp(pct(cf,0)+' CVaR loss',usd(b.cvar))+kp('P(net loss)',pct(b.p_net_loss,0))+
  kp('Risk-adjusted (λ '+fmt(lam,2)+')',usd(b.risk_adjusted_value))+'</div>';
 const d=cs.distribution;if(d&&d.edges&&rows.length){const e=d.edges,mid=e.slice(0,-1).map((v,i)=>(v+e[i+1])/2),names=rows.map(r=>r.option);
  const S=names.map((nm,i)=>({name:nm,color:SER[i%SER.length],step:true,pts:[[e[0],0]].concat((d.shares[nm]||[]).map((y,k)=>[e[k+1],y])).concat([[e[e.length-1],0]])}));
  h+='<h3 style="margin-top:14px">Distribution of net value per option</h3>'+lines(S,{title:'Share of simulated outcomes by net settlement value',y0:0,ytick:v=>pct(v,0),xtick:usdK,
   xlabel:'net value to us (payout − care-management spend)',ylabel:'share of draws',under:(X,Y)=>'<line x1="'+X(0)+'" x2="'+X(0)+'" y1="'+Y(0)+'" y2="12" style="stroke:var(--muted);stroke-dasharray:3 3"/>'})+
   '<div class="legend">'+S.map(s=>'<span><span class="sw" style="background:'+s.color+'"></span>'+esc(s.name)+'</span>').join('')+'<span>┊ zero</span></div>';
  h+=intervals(rows.map((r,i)=>({label:r.option,est:r.expected_net,lo:r.net_p5,hi:r.net_p95,main:i===0,note:'E '+usdK(r.expected_net)})),{title:'Expected net value with the 5th to 95th percentile range',tick:usdK})}
 const fl=cs.ranking_flips||[];if(fl.length)h+='<h4>Where the ranking flips</h4><ul class="hint">'+fl.slice(0,6).map(f=>'<li>'+esc(f.challenger)+' overtakes '+esc(f.leader)+
  (isNum(f.flip_benchmark_pmpm)?' at a $'+fmt(f.flip_benchmark_pmpm,2)+' PMPM benchmark':'')+(isNum(f.flip_trend_pct)?(isNum(f.flip_benchmark_pmpm)?' or':' at')+' a '+pct(f.flip_trend_pct,1)+' cost trend':'')+
  (!isNum(f.flip_benchmark_pmpm)&&!isNum(f.flip_trend_pct)?' nowhere within benchmark ±25% and trend ±15 points':'')+'</li>').join('')+'</ul>';
 h+='</section>';
 if(n){const fr=(n.frontier||[]).filter(r=>isNum(r.break_even_sharing_rate)||isNum(r.sharing_rate_for_nash_value)),bd=n.bounds||{},ns=n.nash;
  h+='<section class="sec" data-focus="negotiation"><h3>Negotiation — Nash bargaining over sharing rate × MSR</h3><div class="kpis">'+
   kp('Nash solution',ns?pct(ns.sharing_rate,0)+' sharing · '+pct(ns.msr,1)+' MSR':'no agreement zone')+kp('Our value at Nash',ns?usd(ns.aco_value):'—')+
   kp('Payer retains',ns?usd(ns.payer_value):'—')+kp('Break-even sharing',isNum(n.break_even_sharing_rate)?pct(n.break_even_sharing_rate,1)+' @ '+pct(n.break_even_at_msr,1)+' MSR':'—')+'</div>';
  if(fr.length){const S=[{name:'break-even (expected net = 0)',color:'var(--s4)',pts:fr.map(r=>[r.msr,r.break_even_sharing_rate])}];
   if(ns)S.push({name:'equal value to us as the Nash terms',color:'var(--s1)',pts:fr.map(r=>[r.msr,r.sharing_rate_for_nash_value])});
   const ys=[bd.sharing_rate_min,bd.sharing_rate_max].concat(ns?[ns.sharing_rate]:[]).filter(isNum);
   h+=lines(S,{title:'Sharing rate needed at each MSR',extraY:ys,ytick:v=>pct(v,0),xtick:v=>pct(v,1),xlabel:'minimum savings rate (MSR)',ylabel:'sharing rate',
    under:(X,Y)=>'<rect x="'+X(bd.msr_min)+'" y="'+Y(bd.sharing_rate_max)+'" width="'+Math.max(1,X(bd.msr_max)-X(bd.msr_min))+'" height="'+Math.max(1,Y(bd.sharing_rate_min)-Y(bd.sharing_rate_max))+'" style="fill:var(--band)"><title>'+esc('Bargaining bounds: '+n.typical_range_label)+'</title></rect>',
    over:(X,Y)=>ns?'<circle cx="'+X(ns.msr)+'" cy="'+Y(ns.sharing_rate)+'" r="6" style="fill:var(--s2);stroke:var(--card);stroke-width:2"><title>'+esc('Nash: '+pct(ns.sharing_rate,0)+' sharing, '+pct(ns.msr,1)+' MSR')+'</title></circle>':''})+
    '<div class="legend">'+S.map(s=>'<span><span class="sw" style="background:'+s.color+'"></span>'+esc(s.name)+'</span>').join('')+
    '<span><span class="sw" style="background:var(--s2)"></span>Nash solution</span><span><span class="sw" style="background:var(--band);height:10px"></span>bargaining bounds</span></div>'}
  const prov=n.bounds_provenance||{},typ=Object.entries(prov).filter(([,v])=>v!=='stated').map(([k])=>label(k));
  h+='<p class="hint">Bounds: sharing '+pct(bd.sharing_rate_min,0)+'–'+pct(bd.sharing_rate_max,0)+', MSR '+pct(bd.msr_min,1)+'–'+pct(bd.msr_max,1)+
   (typ.length?'; '+esc(typ.join(', '))+' from the '+esc(n.typical_range_label):' (all stated)')+'. Payer reservation '+usd(n.payer_reservation)+' — '+esc(n.payer_reservation_provenance)+'.</p>';
  if((n.surface||[]).length)h+='<details><summary>Utilities along the Nash MSR ('+n.surface.length+')</summary>'+tableHTML(n.surface,{name:'nash-surface'})+'</details>';
  h+='</section>'}
 if(rows.length)h+='<section class="sec"><h3>Options</h3>'+tableHTML(rows,{name:'contract-options',render:Object.fromEntries(['expected_payout','expected_net','sd','value_at_risk','cvar','upside_value',
  'downside_cost','net_p5','net_p50','net_p95','risk_adjusted_value','delta_trend_per_point','vega_per_10pct_volatility'].map(k=>[k,v=>esc(usd(v))])),
  caption:'Greeks: delta per $1 PMPM of benchmark, per +1 point of cost trend, vega per +10% cost volatility (finite differences, common random numbers).'})+'</section>';
 if((cs.assumptions||[]).length)h+='<p class="hint"><b>Assumptions.</b> '+esc(cs.assumptions.join(' '))+'</p>';
 const src=cs.sources||[];if(src.length)h+='<details><summary>Program sources ('+src.length+')</summary><ol class="hint">'+src.map(c=>'<li>'+kbLink(c)+(c.section?' · '+esc(c.section):'')+'</li>').join('')+'</ol></details>';
 return h}
/* target trial emulation: protocol table, effect intervals (ITT, per-protocol, negative control), weighted risk curves,
   propensity overlap (mirrored histogram), covariate balance (Love plot), eligibility attrition */
function vizTargetTrial(a){const t=(a.overall||{}).target_trial||{},an=t.analyses||{},itt=an.intention_to_treat||{},pp=an.per_protocol||{},nc=an.negative_control||{};
 const L=t.arm_labels||['treatment','comparator'],hr=itt.scale==='hazard_ratio',kp=(l,v)=>'<div class="kpi"><div class="l">'+l+'</div><div class="v">'+v+'</div></div>';
 const inf=t.inference||'',cls=inf==='supported_under_assumptions'?'answered':inf==='limited'?'partial':'unsupported',n=t.sample||{},arms=t.arms||[];
 const est=v=>!isNum(v)?'—':hr?fmt(v,3):(v>0?'+':'')+fmt(v,3),ci=x=>x&&isNum(x[0])?est(x[0])+' to '+est(x[1]):'—';
 let h='<section class="sec" data-focus="trial"><h3>Target trial emulation — '+esc(L[0])+' vs '+esc(L[1])+'</h3><p class="hint">'+esc(t.outcome_label||'')+'; new users, active comparator, '+esc((t.parameters||{}).adjustment||'overlap')+' propensity weighting.</p><div class="kpis">'+
  kp('ITT '+(hr?'hazard ratio':'difference in change'),esc(est(itt.estimate)))+kp('95% interval',esc(ci(itt.ci95)))+kp('Per-protocol',esc(est(pp.estimate)))+
  kp('E-value (CI)',esc(isNum(itt.e_value)?fmt(itt.e_value,2)+(isNum(itt.e_value_ci)?' ('+fmt(itt.e_value_ci,2)+')':''):'—'))+
  kp('New users',esc(fmt(n[arms[0]])+' / '+fmt(n[arms[1]])))+kp('Inference','<span class="badge '+cls+'" style="font-size:14px">'+esc(label(inf||'unknown'))+'</span>')+'</div>';
 const rows=[];const tr=v=>!isNum(v)?null:hr?Math.log(Math.max(v,1e-6)):v;
 [['Intention-to-treat',itt,true],['Per-protocol',pp,false],['Negative control (should be null)',nc,false]].forEach(([nm,x,m])=>{if(x&&x.status==='estimated')rows.push({label:nm,est:tr(x.estimate),lo:tr((x.ci95||[])[0]),hi:tr((x.ci95||[])[1]),main:m,note:est(x.estimate)})});
 if(rows.length)h+=intervals(rows,{title:'Effect estimates with 95% bootstrap intervals',tick:v=>hr?fmt(Math.exp(v),2):fmt(v,2)})+'<p class="hint">Dots are estimates, bars 95% intervals; the dashed line is no difference'+(hr?' (hazard ratio 1, log scale)':'')+'.</p>';
 const cv=itt.curves;if(cv&&cv.treated){const S=[{name:L[0],pts:cv.treated.map(p=>[p[0],1-p[1]]),step:true},{name:L[1],pts:cv.comparator.map(p=>[p[0],1-p[1]]),step:true,dash:true}];
  h+='<h3 style="margin-top:14px">Weighted cumulative incidence</h3>'+legend(S,'tt')+lines(S,{title:'Weighted Kaplan-Meier cumulative incidence by arm',y0:0,ytick:v=>pct(v,0),xlabel:'days after time zero',ylabel:'cumulative incidence'})}
 const ps=(t.propensity||{}).histogram;if(ps&&ps.edges)h+='<h3 style="margin-top:14px">Propensity-score overlap</h3>'+psMirror(ps,L)+'<p class="hint">'+esc(fmt(100*((t.propensity||{})['share_outside_0.05_0.95']||0),1))+'% of patients outside 0.05-0.95; effective sample size '+
  esc(Object.entries((t.propensity||{}).effective_sample_size||{}).map(([k,v])=>label(k)+' '+fmt(v,0)).join(', '))+'.</p>';
 const bal=t.balance||[];if(bal.length)h+='<h3 style="margin-top:14px">Covariate balance</h3>'+lovePlot(bal)+'<p class="hint">Hollow = before weighting, filled = after; the band is |SMD| &lt; 0.1.</p>';
 h+='</section><section class="sec"><h3>Target trial protocol and its emulation</h3>'+tableHTML(t.protocol||[],{name:'target-trial-protocol'});
 const f=t.eligibility_funnel||{};const fr=Object.entries(f).filter(([k,v])=>k!=='eligible'&&isNum(v)).map(([k,v])=>({label:label(k),value:v}));
 if(fr.length)h+='<h4>Eligibility</h4>'+hbars(fr.concat([{label:'eligible',value:f.eligible,color:'var(--s2)'}]),{title:'Eligibility counts'});
 h+='<details><summary>Analyses ('+Object.keys(an).length+')</summary>'+tableHTML(Object.entries(an).map(([k,v])=>Object.assign({analysis:k},Object.fromEntries(Object.entries(v).filter(([kk])=>kk!=='curves')))),{name:'target-trial-analyses'})+'</details>';
 if(a.assumptions&&a.assumptions.length)h+='<details><summary>Assumptions ('+a.assumptions.length+')</summary><ul>'+a.assumptions.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 return h+'</section>'}
function psMirror(ps,L){const W=CW(),H=200,P=34,mid=H/2,e=ps.edges,n=e.length-1,mx=Math.max(1,...ps.treated,...ps.comparator),bw=(W-2*P)/n;let s=svgOpen(W,H,'Propensity score distribution by arm');
 for(let i=0;i<n;i++){const x=P+i*bw,u=(ps.treated[i]||0)/mx*(mid-18),d=(ps.comparator[i]||0)/mx*(mid-18);
  s+='<rect x="'+(x+1)+'" y="'+(mid-u)+'" width="'+Math.max(1,bw-2)+'" height="'+u+'" style="fill:var(--s1)"><title>'+esc(L[0]+': '+ps.treated[i])+'</title></rect>';
  s+='<rect x="'+(x+1)+'" y="'+mid+'" width="'+Math.max(1,bw-2)+'" height="'+d+'" style="fill:var(--s3)"><title>'+esc(L[1]+': '+ps.comparator[i])+'</title></rect>'}
 s+='<line class="axis" x1="'+P+'" x2="'+(W-P)+'" y1="'+mid+'" y2="'+mid+'"/>';[0,0.25,0.5,0.75,1].forEach(v=>{s+='<text x="'+(P+v*(W-2*P))+'" y="'+(H-4)+'" text-anchor="middle">'+v+'</text>'});
 return s+'</svg><div class="legend"><span><span class="sw" style="background:var(--s1)"></span>'+esc(L[0])+' (above the axis)</span><span><span class="sw" style="background:var(--s3)"></span>'+esc(L[1])+' (below)</span><span>x = propensity score</span></div>'}
function lovePlot(bal){const W=CW(),lh=20,Lp=Math.min(Math.round(W*.42),12+6.6*Math.max(...bal.map(b=>String(b.covariate).length))),R=20,T=8,H=T+bal.length*lh+26;
 const vs=bal.flatMap(b=>[b.smd_before,b.smd_after]).filter(isNum).map(Math.abs).concat([0.25]);const hi=Math.max(...vs)*1.08,X=v=>Lp+v/hi*(W-Lp-R);
 let s=svgOpen(W,H,'Absolute standardised mean differences before and after weighting');s+='<rect x="'+X(0)+'" y="'+T+'" width="'+(X(0.1)-X(0))+'" height="'+(H-T-24)+'" style="fill:var(--band)"/>';
 ticks(0,hi,4).forEach(v=>{s+='<text x="'+X(v)+'" y="'+(H-8)+'" text-anchor="middle">'+fmt(v,2)+'</text>'});
 bal.forEach((b,i)=>{const y=T+i*lh+lh/2;s+='<text x="'+(Lp-6)+'" y="'+(y+4)+'" text-anchor="end" style="fill:var(--fg)">'+esc(label(b.covariate))+'</text><line class="gridl" x1="'+Lp+'" x2="'+(W-R)+'" y1="'+y+'" y2="'+y+'"/>';
  if(isNum(b.smd_before))s+='<circle cx="'+X(Math.abs(b.smd_before))+'" cy="'+y+'" r="5" style="fill:none;stroke:var(--s4);stroke-width:2"><title>before '+fmt(b.smd_before,3)+'</title></circle>';
  if(isNum(b.smd_after))s+='<circle cx="'+X(Math.abs(b.smd_after))+'" cy="'+y+'" r="4.5" style="fill:var(--s1)"><title>after '+fmt(b.smd_after,3)+'</title></circle>'});
 return s+'</svg>'}
/* lines of therapy: KPI tiles, Sankey of regimens across lines, per-line table, persistence curves, switching matrix */
function vizLines(a){const o=(a.overall||{}).line_of_therapy||{},c=o.counts||{},ls=o.lines||[],per=o.persistence||[],sq=o.sequences||[];const kp=(l,v)=>'<div class="kpi"><div class="l">'+l+'</div><div class="v">'+esc(v)+'</div></div>';
 let h='<section class="sec" data-focus="lines"><h3>Lines of therapy — '+esc(o.area_label||(o.parameters||{}).components||'')+'</h3><div class="kpis">'+kp('New users lined up',fmt(c.analysed_new_users))+
  kp('Reached line 2',ls[1]?fmt(ls[1].patients)+' ('+pct(ls[1].share_of_new_users,0)+')':'0')+kp('Top first line',(ls[0]||{}).top_regimen||'—')+
  kp('1-year persistence',per[0]&&isNum(per[0].persistent_day_365)?pct(per[0].persistent_day_365,0)+' '+per[0].component:'—')+kp('Prevalent excluded',fmt(c.excluded_possible_prevalent_users||0))+'</div>';
 if((o.sankey||[]).length)h+='<h3 style="margin-top:14px">Treatment sequences</h3>'+sankey(o.sankey);
 if(ls.length)h+=tableHTML(ls,{name:'lines-of-therapy',caption:'Per line: patients, how they arrived (switch / add-on), Kaplan-Meier time on treatment and time to next treatment'});
 const P=per.filter(r=>(r.curve||[]).length).slice(0,5);if(P.length){const S=P.map((r,i)=>({name:label(r.component),color:SER[i%SER.length],step:true,pts:[[0,1]].concat(r.curve.map(p=>[p.day,p.survival]))}));
  h+='<h3 style="margin-top:14px">Persistence on first-line components</h3>'+legend(S,'lot')+lines(S,{title:'Share still on the first-line component (Kaplan-Meier)',y0:0,y1:1,ytick:v=>pct(v,0),xlabel:'days since first fill',ylabel:'still on therapy'})}
 if((o.switch_matrix||[]).length)h+='<h4>Switches between components</h4>'+tableHTML(o.switch_matrix,{name:'switch-matrix'});
 if(sq.length)h+='<details><summary>Sequences ('+sq.length+')</summary>'+tableHTML(sq,{name:'sequences'})+'</details>';
 const r=o.rules||{};h+='<p class="hint"><b>Rules.</b> gap '+esc(r.gap_days)+' days ends an episode; components within '+esc(r.regimen_window_days)+' days form a regimen; a component ending within '+esc(r.switch_window_days)+
  ' days of a new start is a switch, otherwise an add-on'+(r.add_on_new_line?' (new line)':' (same line)')+'; washout '+esc(r.washout_days)+' days; '+esc(r.fill_definition||'')+'.</p>';
 return h+'</section>'}
function sankey(links){const W=CW(),H=Math.max(220,24*Math.min(14,links.length)+40),P=8,nw=12;const col=n=>n==='discontinued'?3:(+((/^L(\d)/.exec(n)||[])[1])||1)-1;
 const nodes={};links.forEach(l=>{[l.source,l.target].forEach(n=>{nodes[n]=nodes[n]||{name:n,col:col(n),in:0,out:0}});nodes[l.source].out+=l.patients;nodes[l.target].in+=l.patients});
 const cols=[0,1,2,3].map(k=>Object.values(nodes).filter(n=>n.col===k).sort((a,b)=>Math.max(b.in,b.out)-Math.max(a.in,a.out))),used=cols.filter(c=>c.length);
 const tot=Math.max(...cols.map(c=>c.reduce((s,n)=>s+Math.max(n.in,n.out),0))),gap=8,maxN=Math.max(...cols.map(c=>c.length)),k=(H-2*P-gap*(maxN-1))/Math.max(1,tot);
 const xs=k2=>P+k2*((W-2*P-nw-150)/Math.max(1,used.length-1));let s=svgOpen(W,H,'Treatment sequences across lines of therapy');
 cols.forEach((c,ci)=>{let y=P;const ui=used.indexOf(c);c.forEach(n=>{n.x=xs(Math.max(0,ui));n.y=y;n.h=Math.max(2,Math.max(n.in,n.out)*k);n.oy=n.y;n.iy=n.y;y+=n.h+gap})});
 links.slice().sort((a,b)=>b.patients-a.patients).forEach((l,i)=>{const a=nodes[l.source],b=nodes[l.target],w=Math.max(1,l.patients*k),y0=a.oy+w/2,y1=b.iy+w/2;a.oy+=w;b.iy+=w;
  const x0=a.x+nw,x1=b.x,mx=(x0+x1)/2;s+='<path d="M'+x0+' '+y0+'C'+mx+' '+y0+' '+mx+' '+y1+' '+x1+' '+y1+'" fill="none" style="stroke:'+(l.target==='discontinued'?'var(--muted)':SER[i%SER.length])+';stroke-opacity:.45;stroke-width:'+w+'"><title>'+esc(l.source+' → '+l.target+': '+l.patients)+'</title></path>'});
 Object.values(nodes).forEach(n=>{s+='<rect x="'+n.x+'" y="'+n.y+'" width="'+nw+'" height="'+n.h+'" style="fill:var(--fg)"/><text x="'+(n.x+nw+4)+'" y="'+(n.y+Math.min(n.h,14)/2+4)+'" style="fill:var(--fg);paint-order:stroke;stroke:var(--card);stroke-width:4px;stroke-linejoin:round">'+esc((n.name.length>34?n.name.slice(0,33)+'…':n.name)+' ('+Math.max(n.in,n.out)+')')+'</text>'});
 return s+'</svg>'}
/* trial feasibility: attrition funnel, criterion impact, sites, diversity vs source population, expected enrolment */
function vizFunnel(a){const o=(a.overall||{}).trial_feasibility||{},f=o.funnel||[],fc=o.forecast||{},kp=(l,v)=>'<div class="kpi"><div class="l">'+l+'</div><div class="v">'+esc(v)+'</div></div>';
 let h='<section class="sec" data-focus="funnel"><h3>Trial feasibility'+((o.parameters||{}).trial?' — '+esc(o.parameters.trial):'')+'</h3><div class="kpis">'+kp('Eligible',fmt(o.eligible)+' of '+fmt(o.population))+
  kp('Removes most alone',((o.criterion_impact||[])[0]||{}).criterion||'—')+kp('Newly eligible / month',isNum(fc.expected_per_month)?fmt(fc.expected_per_month,1)+' ('+fmt((fc.ci95_per_month||[])[0],1)+'–'+fmt((fc.ci95_per_month||[])[1],1)+')':'—')+
  (isNum(fc.expected_enrolled_per_month)?kp('Enrolled / month',fmt(fc.expected_enrolled_per_month,1)):'')+'</div>';
 if(f.length)h+=funnelChart(f);
 const im=(o.criterion_impact||[]).filter(r=>r.fails_overall);if(im.length)h+='<h4>Patients each criterion removes on its own</h4>'+hbars(im.map(r=>({label:r.criterion,value:r.removes_alone,value2:r.fails_overall})),{title:'Removed alone vs failing overall',value2:1,name1:'removes alone (would join if dropped)',name2:'fails overall'});
 h+='</section>';
 const st=(o.sites||[]).filter(r=>r.eligible);if(st.length)h+='<section class="sec"><h3>Sites (PCP practices)</h3>'+hbars(st.slice(0,12).map(r=>({label:r.site,value:r.eligible,note:fmt(r.eligible_per_1000,1)+' per 1,000'})),{title:'Eligible patients by practice'})+
  ((o.pcps||[]).length?'<details><summary>Investigators (PCPs)</summary>'+tableHTML(o.pcps,{name:'eligible-by-pcp'})+'</details>':'')+'</section>';
 const dv=o.diversity||[];if(dv.length){h+='<section class="sec"><h3>Diversity of the eligible population</h3>';
  ['race','ethnicity','sex','age_band','language'].forEach(d=>{const rows=dv.filter(r=>r.dimension===d&&(r.source_patients||r.goal_share!=null));if(!rows.length)return;
   h+='<h4>'+esc(label(d))+'</h4>'+hbars(rows.slice(0,8).map(r=>({label:r.group,value:r.eligible_share,value2:r.source_share})),{title:'Eligible vs source population share by '+label(d),value2:1,fmt:v=>pct(v,0),tick:v=>pct(v,0),name1:'eligible',name2:'source population'});
   const g=rows.filter(r=>r.goal_share!=null);if(g.length)h+='<p class="hint">Goals: '+g.map(r=>esc(r.group)+' '+pct(r.goal_share,0)+' — '+(r.goal_met?'met':'not met')+' ('+pct(r.eligible_share,0)+')').join('; ')+'</p>'});
  h+='</section>'}
 const hs=fc.history||[];if(hs.length)h+='<section class="sec"><h3>Newly eligible patients per month</h3>'+lines([{name:'newly eligible',pts:hs.map((p,i)=>[i,p.newly_eligible])},{name:'expected rate',pts:hs.map((p,i)=>[i,fc.expected_per_month]),dash:true}],
  {title:'Patients who became eligible each month',cats:hs.map(p=>p.month),y0:0,ylabel:'patients'})+'<p class="hint">'+esc(fc.basis||'')+'; next 12 months ≈ '+fmt(fc.expected_next_12_months,1)+'.</p></section>';
 return h}
function funnelChart(f){const W=CW(),lh=34,T=6,H=T+f.length*lh+6,mx=Math.max(1,...f.map(r=>r.entering)),cx=W*0.36,half=W*0.3;let s=svgOpen(W,H,'Eligibility attrition funnel');
 f.forEach((r,i)=>{const y=T+i*lh,w=Math.max(2,r.remaining/mx*2*half),rm=r.removed||0;s+='<rect x="'+(cx-w/2)+'" y="'+y+'" width="'+w+'" height="'+(lh-8)+'" rx="3" style="fill:'+(r.kind==='not applied'?'var(--band)':'var(--s1)')+'"><title>'+esc(r.criterion+': '+r.remaining+' remain')+'</title></rect>';
  s+='<text x="'+cx+'" y="'+(y+lh/2)+'" text-anchor="middle" style="fill:var(--card);font-weight:700">'+fmt(r.remaining)+'</text>';
  s+='<text x="'+(cx+half+10)+'" y="'+(y+lh/2-3)+'" style="fill:var(--fg)">'+esc((r.criterion.length>44?r.criterion.slice(0,43)+'…':r.criterion))+'</text>';
  if(i)s+='<text x="'+(cx+half+10)+'" y="'+(y+lh/2+11)+'">'+(rm?'−'+fmt(rm)+(r.removed_missing_data?' ('+fmt(r.removed_missing_data)+' no data)':''):r.kind==='not applied'?'not applied':'none removed')+'</text>'});
 return s+'</svg>'}
/* quality & safety: loop closure (status bars per item type, time-to-closure curves), diagnostic delay (delay histogram, prompts), risk-standardized outcomes (funnel) */
const kpiT=(l,v)=>'<div class="kpi"><div class="l">'+esc(l)+'</div><div class="v">'+esc(v)+'</div></div>';
const LOOP_ST=[['closed_on_time','var(--good)','closed on time'],['closed_late','var(--s2)','closed late'],['declined','var(--muted)','declined'],['open_overdue','var(--bad)','open past due'],['pending','var(--s3)','not yet due']];
function loopBars(rows){if(!rows.length)return '';const W=CW(),lh=26,L=Math.min(Math.round(W*.34),12+7*Math.max(...rows.map(r=>label(r.group).length))),R=56,T=8,H=T+rows.length*lh+24;
 let s=svgOpen(W,H,'Loop status by item type (share of items)');const X=v=>L+v*(W-L-R);
 [0,.25,.5,.75,1].forEach(t=>{s+='<line class="gridl" x1="'+X(t)+'" x2="'+X(t)+'" y1="'+T+'" y2="'+(H-20)+'"/><text x="'+X(t)+'" y="'+(H-6)+'" text-anchor="middle">'+pct(t,0)+'</text>'});
 rows.forEach((r,i)=>{const y=T+i*lh;let x0=0;s+='<text x="'+(L-6)+'" y="'+(y+lh/2+4)+'" text-anchor="end" style="fill:var(--fg)">'+esc(label(r.group))+'</text>';
  LOOP_ST.forEach(([k,c,n])=>{const v=r.items?(r[k]||0)/r.items:0;if(v<=0)return;s+='<rect x="'+X(x0).toFixed(1)+'" y="'+(y+4)+'" width="'+Math.max(1,X(x0+v)-X(x0)).toFixed(1)+'" height="'+(lh-8)+'" style="fill:'+c+'"><title>'+esc(label(r.group)+': '+n+' '+r[k]+' of '+r.items)+'</title></rect>';x0+=v});
  s+='<text x="'+(W-R+4)+'" y="'+(y+lh/2+4)+'">'+esc(isNum(r.close_rate)?pct(r.close_rate,0):'—')+'</text>'});
 return s+'</svg><div class="legend">'+LOOP_ST.map(([k,c,n])=>'<span><span class="sw" style="background:'+c+'"></span>'+n+'</span>').join('')+'<span>right: close rate (on time / due)</span></div>'}
function vizLoops(a){const o=(a.overall||{}).loop_closure||{},c=o.status_counts||{},p=o.parameters||{},bt=o.by_type||[],due=(o.items_total||0)-(c.pending||0);
 const crit=((o.by_tier||{}).critical||{}).open_overdue||0;
 let h='<section class="sec" data-focus="loops"><h3>Closing the loop — '+fmt(o.items_total)+' items opened '+esc((o.period||{}).start||'')+' to '+esc((o.period||{}).end||'')+'</h3><div class="kpis">'+
  kpiT('Open past due',fmt(c.open_overdue||0))+kpiT('Critical open',fmt(crit))+kpiT('Close rate (on time)',due?pct((c.closed_on_time||0)/due,1):'—')+
  kpiT('Closed late',fmt(c.closed_late||0))+kpiT('Declined',fmt(c.declined||0))+kpiT('Not yet due',fmt(c.pending||0))+'</div>'+loopBars(bt);
 const tc=o.time_to_closure||{},names=Object.keys(tc);
 if(names.length){const series=names.map(n=>({name:label(n)+' (median '+(isNum(tc[n].median_days)?fmt(tc[n].median_days,0)+'d':'—')+')',step:true,pts:tc[n].points||[]}));
  h+='<h3 style="margin-top:12px">Time to closure (Kaplan-Meier, open items censored at the snapshot)</h3>'+legend(series,'loopkm')+
   lines(series,{title:'Share of items closed by days since the item opened',y0:0,y1:1,ytick:v=>pct(v,0),xlabel:'days since result / order / referral',ylabel:'closed'})}
 h+='</section>';
 const rows=a.table||[];if(rows.length)h+='<section class="sec"><h3>Open loops past due ('+fmt(o.open_loops_total)+')</h3>'+tableHTML(rows,{name:'open-loops',
  cols:['tier','item_type','item','patient_id','item_date','due_date','days_overdue','owner','practice','activity','expected_action'],render:{tier:v=>'<span class="'+(v==='critical'?'bad':'')+'">'+esc(v)+'</span>',item_type:v=>esc(label(v))}})+'</section>';
 const g=o.by_group||[];if(g.length)h+='<details open><summary>Close rates by '+esc(label(o.group_by||'group'))+' ('+g.length+')</summary>'+tableHTML(g,{name:'loop-close-rates',
  cols:['group','items','due','closed_on_time','closed_late','open_overdue','critical_open','pending','close_rate','close_rate_ci95','median_days_to_close'],render:{close_rate:v=>esc(pct(v,1)),close_rate_ci95:v=>esc(v?pct(v[0],0)+'–'+pct(v[1],0):'—')}})+'</details>';
 const ev=o.closure_evidence||[];if(ev.length)h+='<details><summary>Closure evidence types ('+ev.length+')</summary>'+tableHTML(ev,{name:'loop-evidence'})+'</details>';
 if((o.data_gaps||[]).length)h+='<details><summary>Data limits ('+o.data_gaps.length+')</summary><ul>'+o.data_gaps.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 if((o.assumptions||[]).length)h+='<p class="hint"><b>Assumptions.</b> '+esc(o.assumptions.join(' '))+'</p>';
 return h}
function delayHist(bins){if(!bins||!bins.length)return '';const W=CW(),L=46,R=12,T=10,B=40,H=200,n=bins.length,hi=Math.max(1,...bins.map(b=>b.patients)),bw=(W-L-R)/n,Y=v=>T+(H-T-B)*(1-v/hi);
 let s=svgOpen(W,H,'Diagnostic delay distribution (days from criterion to diagnosis)');ticks(0,hi,4).forEach(t=>{s+='<line class="gridl" x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(t)+'" y2="'+Y(t)+'"/><text x="'+(L-6)+'" y="'+(Y(t)+4)+'" text-anchor="end">'+esc(fmt(t))+'</text>'});
 bins.forEach((b,i)=>{const x=L+i*bw,lab=b.to==null?'>'+(b.from-1)+'d':b.from+'–'+b.to+'d',c=b.from>180?'var(--bad)':b.from>90?'var(--s2)':'var(--s1)';
  s+='<rect x="'+(x+bw*.1).toFixed(1)+'" y="'+Y(b.patients).toFixed(1)+'" width="'+(bw*.8).toFixed(1)+'" height="'+Math.max(1,H-B-Y(b.patients)).toFixed(1)+'" rx="2" style="fill:'+c+'"><title>'+esc(lab+': '+b.patients+' patients')+'</title></rect><text x="'+(x+bw/2).toFixed(1)+'" y="'+(H-B+14)+'" text-anchor="middle">'+esc(lab)+'</text>'});
 return s+'<text x="'+((L+W-R)/2)+'" y="'+(H-6)+'" text-anchor="middle">days from first meeting the criterion to the recorded diagnosis</text></svg>'}
function vizDelay(a){const o=(a.overall||{}).diagnostic_delay||{},p=o.parameters||{},lb=o.lookback,ud=o.undiagnosed,lf=o.look_forward,t=o.target_label||label(p.target||'');
 let h='<section class="sec" data-focus="delay"><h3>Diagnostic delay — '+esc(t)+' ('+esc(label(p.mode||''))+'). Review prompts, not diagnoses.</h3>';
 if(lf){h+='<div class="kpis">'+kpiT('ED treat-and-release visits',fmt(lf.index_visits))+kpiT('With a related symptom code',fmt(lf.symptom_index_visits))+
   kpiT('Admitted with '+t+' within '+p.horizon_days+'d',fmt(lf.returns_with_target))+kpiT('Rate after symptom visits',isNum(lf.symptom_rate)?pct(lf.symptom_rate,2):'—')+
   kpiT('Rate after other ED visits',isNum(lf.other_rate)?pct(lf.other_rate,2):'—')+kpiT('Risk ratio',isNum(lf.risk_ratio)?fmt(lf.risk_ratio,2):'—')+'</div>';
  if(isNum(lf.symptom_rate)||isNum(lf.other_rate))h+=hbars([{label:'symptom-coded ED visits',value:lf.symptom_rate},{label:'other ED visits',value:lf.other_rate}],{title:'Share admitted with the target diagnosis',fmt:v=>pct(v,2),tick:v=>pct(v,1)})}
 if(lb){const d=lb.delay||{};h+='<div class="kpis">'+kpiT('Incident diagnoses',fmt(lb.incident))+kpiT('Prevalent (excluded)',fmt(lb.prevalent_excluded))+
   kpiT('Criterion met before diagnosis',fmt(lb.with_prior_criterion))+kpiT('Median delay',isNum(d.median_days)?fmt(d.median_days,0)+' days':'—')+
   kpiT('Delayed > 180 days',isNum(d.share_over_180)?pct(d.share_over_180,0):'—')+kpiT('Missed-opportunity visits',fmt(lb.missed_opportunity_visits))+'</div>'+delayHist(d.histogram);
  const g=lb.by_group||[];if(g.length)h+='<details><summary>Delay by '+esc(label(p.group_by||'group'))+' ('+g.length+')</summary>'+tableHTML(g,{name:'delay-by-group'})+'</details>';
  const cs=o.lookback_cases||[];if(cs.length)h+='<details><summary>Incident cases ('+cs.length+' shown)</summary>'+tableHTML(cs,{name:'delay-cases',cols:['patient_id','diagnosis_date','criterion_date','delay_days','criterion','missed_opportunity_visits','related_visits_in_lookback','practice','pcp','diagnosis_source']})+'</details>'}
 if(ud){h+='<div class="kpis">'+kpiT('Criterion met, no diagnosis',fmt(ud.criterion_met))+kpiT('Seen since',fmt(ud.with_visit_since))+kpiT('One value never repeated',fmt(ud.unconfirmed_signal))+'</div>';
  const g=ud.by_group||[];if(g.length)h+=hbars(g.slice(0,12).map(r=>({label:r.group,value:r.criterion_met})),{title:'Patients meeting the criterion without a diagnosis'})}
 h+='</section>';const rows=a.table||[];
 if(rows.length)h+='<section class="sec"><h3>'+(lf?'ED visits later admitted with '+esc(t):ud?'Review prompts':'Cases')+'</h3>'+tableHTML(rows,{name:'diagnostic-delay',caption:'Each row is a review prompt for a clinician, never a diagnosis.'})+'</section>';
 if((o.data_gaps||[]).length)h+='<details><summary>Data limits ('+o.data_gaps.length+')</summary><ul>'+o.data_gaps.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 if((o.assumptions||[]).length)h+='<p class="hint"><b>Assumptions.</b> '+esc(o.assumptions.join(' '))+'</p>';
 return h}
function funnelChart(f,net){const lim=(f||{}).limits||[],pts=(f||{}).points||[];if(!lim.length||!pts.length)return '';const W=CW(),H=W<480?240:300,L=52,R=16,T=12,B=42;
 const ex=lim.map(r=>r.expected),lx=v=>Math.log10(v),x0=lx(Math.min(...ex)),x1=lx(Math.max(...ex));
 const ys=pts.map(p=>p.oe_ratio).filter(isNum).concat(lim.map(r=>r['upper_0.998']).filter(isNum),[0]);let y1=Math.min(Math.max(2,...ys),6);
 const X=v=>L+(lx(v)-x0)/((x1-x0)||1)*(W-L-R),Y=v=>T+(1-Math.min(v,y1)/y1)*(H-T-B);let s=svgOpen(W,H,'Funnel plot: observed / expected by expected events');
 ticks(0,y1,5).forEach(t=>{s+='<line class="gridl" x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(t)+'" y2="'+Y(t)+'"/><text x="'+(L-6)+'" y="'+(Y(t)+4)+'" text-anchor="end">'+esc(fmt(t,2))+'</text>'});
 [Math.min(...ex),Math.sqrt(Math.min(...ex)*Math.max(...ex)),Math.max(...ex)].forEach(v=>{s+='<text x="'+X(v).toFixed(1)+'" y="'+(H-B+16)+'" text-anchor="middle">'+esc(fmt(v,1))+'</text>'});
 [['0.95','var(--s2)','4 3'],['0.998','var(--bad)','2 3']].forEach(([lv,c,d])=>['lower_','upper_'].forEach(k=>{s+='<path d="'+lim.map((r,i)=>(i?'L':'M')+X(r.expected).toFixed(1)+','+Y(r[k+lv]).toFixed(1)).join('')+'" style="fill:none;stroke:'+c+';stroke-dasharray:'+d+';stroke-width:1.5"/>'}));
 s+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(1)+'" y2="'+Y(1)+'" style="stroke:var(--fg);stroke-dasharray:4 3"/>';
 pts.forEach(p=>{if(!isNum(p.oe_ratio))return;const c=p.outside_998?'var(--bad)':p.outside_95?'var(--s2)':'var(--s1)';
  s+='<circle cx="'+X(p.expected).toFixed(1)+'" cy="'+Y(p.oe_ratio).toFixed(1)+'" r="5" style="fill:'+c+';stroke:var(--card);stroke-width:1.5"><title>'+esc(p.unit+': O/E '+fmt(p.oe_ratio,2)+' on '+fmt(p.expected,1)+' expected')+'</title></circle>'});
 return s+'<text x="'+((L+W-R)/2)+'" y="'+(H-6)+'" text-anchor="middle">expected events (log scale)</text></svg><div class="legend"><span>┄ O/E = 1 (network)</span><span><span class="sw" style="background:var(--s2);height:3px"></span>95% limits</span>'+
  '<span><span class="sw" style="background:var(--bad);height:3px"></span>99.8% limits</span><span>dots: units (orange/red = outside the limits)</span></div>'}
function vizFunnel(a){const o=(a.overall||{}).risk_outcomes||{},p=o.parameters||{},m=o.model||{},c=o.counts||{},rows=a.table||[],los=p.outcome==='length_of_stay',f=v=>los?fmt(v,2)+' d':pct(v,1);
 let h='<section class="sec" data-focus="funnel"><h3>Risk-standardized '+esc(o.outcome_label||label(p.outcome||''))+(los?'':' ('+esc(p.window_days)+'-day)')+' by '+esc(p.unit||'unit')+'</h3><div class="kpis">'+
  kpiT('Network rate',isNum(o.network_rate)?f(o.network_rate):'—')+kpiT('Index cases',fmt(m.cases))+kpiT(los?'Bed days':'Events',fmt(m.events))+kpiT('Worse than network',fmt(c.worse_than_network||0))+
  kpiT('Better than network',fmt(c.better_than_network||0))+kpiT('Model c-statistic',isNum(m.c_statistic)?fmt(m.c_statistic,2):'—')+'</div>'+funnelChart(o.funnel,o.network_rate);
 const iv=rows.filter(r=>isNum(r.rsr)).slice(0,25).map(r=>({label:String(r.unit),est:r.rsr,lo:(r.rsr_ci95||[])[0],hi:(r.rsr_ci95||[])[1],main:r.flag!=='no_different',note:r.flag==='no_different'?'':label(r.flag)}));
 if(iv.length)h+='<h3 style="margin-top:12px">Risk-standardized rate with 95% interval (network '+esc(isNum(o.network_rate)?f(o.network_rate):'—')+')</h3>'+intervals(iv,{title:'Risk-standardized rate per unit',tick:f});
 h+='</section>';
 if(rows.length)h+='<section class="sec"><h3>Units</h3>'+tableHTML(rows,{name:'risk-standardized-outcomes',cols:['unit','flag','index_cases','observed','expected','oe_ratio','srr','srr_ci95','rsr','rsr_ci95','p_worse_than_network','rank','small_unit'],
  head:{srr:'SRR (shrunk O/E)',rsr:'risk-standardized rate',p_worse_than_network:'P(worse)'},render:{flag:v=>esc(label(v||'')),rsr:v=>esc(isNum(v)?f(v):'—'),rsr_ci95:v=>esc(v?f(v[0])+'–'+f(v[1]):'—'),srr_ci95:v=>esc(v?fmt(v[0],2)+'–'+fmt(v[1],2):'—')}})+'</section>';
 if((m.coefficients||[]).length)h+='<details><summary>Case-mix model ('+esc(m.family||'')+', '+fmt(m.coefficients.length)+' terms)</summary>'+tableHTML(m.coefficients,{name:'case-mix-model'})+'</details>';
 if((o.notes||[]).length)h+='<ul class="hint">'+o.notes.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>';
 if((o.data_gaps||[]).length)h+='<details><summary>Data limits ('+o.data_gaps.length+')</summary><ul>'+o.data_gaps.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 return h}
function vizMonitorComparison(a){const c=a.overall.monitor_comparison,rows=Array.isArray(c.rows)?c.rows:(a.table||[]),charts=(c.registered_chart||{}).chart_status||[],base=c.baseline||{};
 const months=new Set(rows.map(r=>r.month)),flags=rows.filter(r=>r.stable_family_flag===true).length,patterns=charts.filter(r=>r.status==='rate_signal'),missing=rows.filter(r=>r.rate==null).length;
 const kp=(name,value)=>'<div class="kpi"><div class="l">'+esc(name)+'</div><div class="v">'+esc(value)+'</div></div>';
 let h='<section class="sec" data-focus="monitor-comparison"><h3>Rate changes and source evidence</h3><p class="hint">Complete native comparison. Model verification and clinical significance remain separate.</p><div class="kpis">'+kp('Months examined',months.size)+kp('Corrected stable-rate flags',flags)+kp('Charts with exploratory patterns',patterns.length)+kp('Months with unknown rates',missing)+'</div>';
 h+='<p>The stable-rate comparison tests each month against the remaining reporting period. Bonferroni covers '+esc(base.family_tests)+' tests. Registered run-pattern rules can flag a chart even when no single point crosses its limits.</p>';
 if(patterns.length)h+='<details open><summary>Registered exploratory patterns</summary>'+tableHTML(patterns.map(r=>({'Series':r.series,'Group':r.group,'Rules':(r.rate_rules||[]).join(', '),'Months':(r.rate_signal_months||[]).join(', ')})),{name:'registered-rate-patterns'})+'</details>';
 h+='<details open><summary>Monthly rates and baseline comparison</summary>'+tableHTML(rows.map(r=>({'Month':r.month,'Series':r.series,'Group':r.group,'Events':r.numerator,'Member-months':r.exposure,'Rate per 1,000':r.rate,'Adjusted p':r.stable_bonferroni_p,'Corrected flag':r.stable_family_flag,'Source records':r.received_source_records})),{name:'native-monitor-comparison'})+'</details>';
 h+='<details><summary>Assumptions and source limits</summary><ul>'+(base.assumptions||[]).map(x=>'<li>'+esc(x)+'</li>').join('')+'<li>'+esc(c.source_limitation||'')+'</li><li>'+esc((c.registered_chart||{}).multiplicity||'')+'</li></ul></details></section>';return h;}
function visual0(a){if(!a||a.answer_type==='unsupported')return '';const o=a.overall||{};
 if(o.monitor_comparison)return vizMonitorComparison(a);
 if(a.answer_type==='table'&&a.table_columns)return '<p class="hint">'+esc(a.interpretation||'')+'</p>'+vizTable(a);
  if(o.loop_closure)return vizLoops(a);
  if(o.diagnostic_delay)return vizDelay(a);
  if(o.risk_outcomes)return vizFunnel(a);
  if(o.target_trial)return vizTargetTrial(a);
  if(o.line_of_therapy)return vizLines(a);
  if(o.trial_feasibility)return vizFunnel(a);
  if(o.contract_performance)return vizContractPerformance(a);
  if(o.contract_strategy)return vizContract(a);
  if(o.knowledge)return vizKnowledge(a);
  if(o.early_signal)return vizEarlySignal(a);
  if(o.claims_review)return vizClaims(a);
  if(o.risk_adjustment)return vizRisk(a);
  if(o.profile)return vizProfile(a);
  if(o.scheduling)return vizScheduling(a);
  if(o.queueing)return vizQueueing(a);
  if(o.uplift)return vizUplift(a);
  if(o.risk_adjusted)return vizRA(a);
  if((o.sdid||o.callaway_santanna)&&!o.did)return vizEffectPanel(a);
  if(o.provider_network)return vizNetwork(a);
  if(o.matrix&&o.states)return vizPathway(a);
 if(o.network&&o.data_gaps)return vizMarket(a);
 if(o.briefing)return vizBriefing(a);
 if(o.demand)return vizDemand(a)+vizReconcile(a);if(o.curves)return vizSurvival(a);if(o.charts||(o.tests_examined!=null&&o.look_elsewhere_bits!=null))return vizMonitor(a);if(o.did)return vizEffect(a);if(o.portfolio&&o.by_group)return vizOutreach(a);
 const v=vizVBC(a);if(v)return v;if(a.answer_type==='table'){const t=vizTrajectory(a);if(t)return t;return vizTable(a)}return vizScalar(a)}

/* ---------- answer card ---------- */
function suggestions(a){if(!a)return [];if(a.answer_type==='count')return ['list them','by practice','by payer'];if(a.answer_type==='patient_set')return ['how many','by practice'];
 if(a.answer_type==='value')return ['by practice','by payer'];return []}
function details(a){let h='';
 if(a.computed&&String(a.headline||'').indexOf('Computed as')<0)h+='<div class="sec"><b>Computed as:</b> '+esc(a.computed)+'</div>';
 if(a.concepts&&a.concepts.length)h+='<details><summary>Definitions ('+a.concepts.length+')</summary><ul>'+a.concepts.map(c=>'<li><b>'+esc(c.label||c.concept||'')+'</b>'+(c.concept&&c.label?' <span class="muted">('+esc(c.concept)+')</span>':'')+
  ((c.included_labels||c.included_series||[]).length?' — '+esc((c.included_labels||c.included_series).slice(0,12).join(', '))+((c.included_labels||c.included_series).length>12?' …':''):'')+'</li>').join('')+'</ul></details>';
 if(a.standards&&a.standards.length)h+='<details><summary>Standards ('+a.standards.length+')</summary><ul>'+a.standards.map(s=>'<li><b>'+esc(label(s.definition))+'</b>: '+esc(s.standard)+' <span class="badge">'+esc(s.alignment)+'</span><br><span class="muted">Agere: '+esc(s.agere_default)+'</span>'+
  (s.sources||[]).map(u=>'<br><a href="'+esc(u)+'" target="_blank" rel="noopener noreferrer">'+esc(u)+'</a>').join('')+'</li>').join('')+'</ul></details>';
 if(a.benchmark&&a.benchmark.length)h+='<div class="sec"><b>Benchmark reference:</b><ul style="margin:4px 0">'+a.benchmark.map(x=>'<li>'+esc(x.label)+' — '+esc(x.display)+((x.sources&&x.sources[0])?' <a target="_blank" rel="noopener noreferrer" href="'+esc(x.sources[0].url)+'">source</a>':'')+'</li>').join('')+
  '</ul><span class="hint">Reference only; the computed answer is unchanged. Populations and definitions differ.</span></div>';
 if(a.method)h+='<details><summary>Method</summary><pre>'+esc(a.method)+'</pre></details>';
 const lim=(a.limitations||[]).concat(((a.overall||{}).limitations)||[]);if(lim.length)h+='<details><summary>Limits ('+lim.length+')</summary><ul>'+lim.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></details>';
 if(a.query)h+='<details><summary>Query</summary><pre>'+esc(a.query)+'</pre></details>';
 return h}
function executionInfo(d){d=d||{};const pr=d.planner_receipt||{},econ=d.planning_economics||{},tg=d.together||{},analyst=tg.analyst||{},hybrid=tg.hybrid||{};
 const cr=d.cost_receipt,authoritative=!!(cr&&cr.version==='cost-receipt/1');
 const calls=authoritative?(isNum(cr.paid_calls)?cr.paid_calls:null):isNum(d.new_paid_api_calls)?d.new_paid_api_calls:isNum(pr.new_paid_api_calls)?pr.new_paid_api_calls:null;
 const total=authoritative?(cr.ledger_consistent===true&&cr.accounting_complete!==false&&cr.usd_known===true&&isNum(cr.usd)?cr.usd:null):econ.failed_or_unpriced_api_cost?null:isNum(econ.new_api_estimate_usd)?econ.new_api_estimate_usd:null;
 const known=authoritative?(isNum((d.cost_telemetry||{}).api_usd)?d.cost_telemetry.api_usd:null):isNum(econ.known_api_estimate_usd)?econ.known_api_estimate_usd:null;
 const cost=total!=null?'$'+total.toFixed(4):'Unknown'+(known>0?' (known portion $'+known.toFixed(4)+')':'');
 const models=new Set();function model(v){if(typeof v==='string'&&v)models.add(v)}
 function walk(r){if(!r||typeof r!=='object')return;model(r.model);(r.stages||[]).forEach(walk);if(r.receipt)walk(r.receipt)}
 (pr.steps||[]).forEach(walk);if(pr.model)model(pr.model);
 if(analyst.state&&!String(analyst.state).startsWith('skipped')){model(analyst.model);Object.values(hybrid.models||{}).forEach(model)}
 if(tg.reconcile)model(tg.reconcile.model);
 if(authoritative)Object.entries(cr.calls_by_model||{}).filter(([_,n])=>isNum(n)&&n>0).forEach(([name])=>model(name));
 const raw=pr.route||d.route||'',route=tg.winner==='analyst'?'Executed analyst SQL':ROUTES[raw]||raw||'Not recorded';
 const graph=d.graph||{},nodes=Array.isArray(graph.nodes)?graph.nodes:[],operations=[...new Set(nodes.map(n=>n.operation).filter(Boolean))];
 return {route,calls,total,known,cost,scope:typeof econ.scope==='string'?econ.scope:'',models:[...models],operations,reason:tg.reason||pr.escalation_reason||'',
  analystState:analyst.state||tg.analyst_state||'',decision:hybrid.decision||tg.decision||'',cached:!!(d.cache_hit||pr.cache_hit)}}
function executionDetails(d){const x=executionInfo(d),rows=[['Answer route',x.route],['Executed operators',x.operations.join(', ')||'Not recorded'],
 ['Models recorded',x.models.join(', ')||'None recorded'],['New paid API calls',x.calls==null?'Unknown':String(x.calls)],['This step · API cost estimate',x.cost]];
 if(x.scope)rows.push(['Cost coverage',x.scope]);
 if(x.analystState)rows.push(['Analyst dispatch',label(x.analystState)]);if(x.decision)rows.push(['Selection / repair',label(x.decision)]);
 if(x.cached)rows.push(['Reuse','Cached result reused']);if(x.reason)rows.push(['Route explanation',x.reason]);
 return '<details data-execution><summary>How Agere answered</summary><div class="tablewrap"><table><tbody>'+rows.map(([k,v])=>'<tr><th scope="row">'+esc(k)+'</th><td class="wrap">'+esc(v)+'</td></tr>').join('')+
 '</tbody></table></div><p class="hint">This estimate covers the recorded step, not necessarily the full history of this answer. Earlier attempts and reused work may have separate costs. Subscription figures are API equivalents, not cash charges. Local compute, setup and review are excluded. Unknown cost is not zero. Execution checks do not establish clinical accuracy.</p></details>'}
function badges(t){const d=t.data||{},a=d.answer||{},x=executionInfo(d);const st=t.error?'error':(a.status||'');
 const b=['<span class="badge '+esc(st)+'">'+esc(label(st||'unknown'))+'</span>',
  '<span class="badge">Route: '+esc(x.route)+'</span>','<span class="badge">Paid API calls: '+(x.calls==null?'Unknown':fmt(x.calls))+'</span>',
  '<span class="badge">API estimate: '+esc(x.cost)+'</span>','<span class="badge">Latency: '+(t.seconds!=null?t.seconds.toFixed(2)+' s':'—')+'</span>'];
 if(d.cache_hit)b.push('<span class="badge">Reused cached result</span>');return '<div class="badges">'+b.join('')+'</div>'}
function resolvedLine(t){const c=(t.data||{}).conversation;const r=c?c.resolved_question:t.resolved;if(!r||r===t.question)return '';
 return '<div class="resolved">Answered as: <b>'+esc(r)+'</b>'+(c&&c.method==='hosted'?' (follow-up rewritten by a hosted model, then planned normally)':'')+'</div>'}
function headlineHTML(a){let hl=String(a.headline||a.status||'');const cut=hl.indexOf(' Warnings (');if(cut>0&&(a.warnings||[]).length)hl=hl.slice(0,cut);
 const boundary=hl.search(/[.!?](?=\s+[A-Z0-9])/),short=hl.length>280&&boundary>0?hl.slice(0,boundary+1):hl,remaining=short===hl?'':hl.slice(boundary+1).trim();
 return '<div class="answer-narrative"><h2 class="headline">What the answer shows</h2><p>'+esc(short)+'</p>'+(remaining?'<details><summary>Read the full saved summary</summary><p>'+esc(remaining)+'</p></details>':'')+'</div>'}
function warnBox(a){const w=(a.warnings||[]),m=(a.missing||[]);let h='';
 if(a.status==='clarify'&&(a.readings||[]).length&&typeof a.readings[0]==='string')h+='<div class="alert warn" role="note"><h3>Which do you mean?</h3><p>Agere\'s independent analysts found more than one defensible reading of this question. Ask again with the one you mean:</p><ul>'+a.readings.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></div>';
 if(w.length)h+='<div class="alert warn" role="note"><h3>Warnings ('+w.length+')</h3><ul>'+w.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></div>';
 if(m.length)h+='<div class="alert '+(a.status==='unsupported'?'bad':'warn')+'" role="note"><h3>Not covered</h3><ul>'+m.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></div>';return h}
function readingsBox(a,q){const r=a.readings||[];if(r.length<2||typeof r[0]==='string')return '';const show=v=>typeof v==='number'?fmt(v):v&&v.patients!=null?fmt(v.patients)+' patients':v&&v.rows?v.rows.length+' rows':esc(JSON.stringify(v));let h='<div class="sec" data-readings><h3>Two defensible readings</h3>'+(a.clarifying_question?'<p class="hint">'+esc(a.clarifying_question)+'</p>':'')+'<div class="readings">';r.forEach((x,i)=>{h+='<div class="reading'+(x.default?' default':'')+'"><div class="muted">'+(x.default?'Shown (default)':'Alternative')+'</div>'+'<div class="val">'+show(x.result)+'</div><p>'+esc(x.definition||'')+'</p>'+(x.query?'<details><summary>Query</summary><pre>'+esc(x.query)+'</pre></details>':'')+(q&&x.definition?'<button type="button" class="btn small" data-ask="'+esc(q+' (definition: '+x.definition+')')+'">Use this reading</button>':'')+'</div>'});return h+'</div></div>'}
function actionLink(c,blocked=false){if(!c||!/^ANS-[0-9a-f]{24}$/.test(c.id||''))return '';return '<section class="next-step"><div><h3>'+esc(blocked?'Review this saved answer':'Choose your next step')+'</h3><p>'+esc(blocked?'This answer is unavailable for follow-ups. Inspect the saved evidence or start a new question.':'Ask a follow-up below, or open the workspace to prepare a report or synthetic review work.')+'</p></div><a class="btn primary" href="/action-center?context='+encodeURIComponent(c.id)+'">Open action workspace ↗</a></section>'}
function clarificationCard(c){if(!c)return '';const pending=c.status==='pending',tid='intent-'+c.id;
 const message={complete:'Your response was saved. The continued analysis appears below.',running:'Your response was saved and the analysis is continuing.',failed:'The continuation stopped. Completed work and the failed attempt are saved. Start a new analysis to try again.',pending:'Completed work is saved. Choose what you mean to continue the affected analysis.'}[c.status]||'This clarification is unavailable.';
 return '<section class="sec clarification-card" data-clarification="'+esc(c.id)+'"><h3>'+esc(c.question)+'</h3><p>'+esc(c.reason)+'</p><p class="hint" role="status">'+esc(message)+'</p>'+
 (!pending?'':(c.options||[]).map(o=>'<p><button type="button" class="btn" data-clarification-option="'+esc(o.id)+'">'+esc(o.label)+'</button> <span class="hint">'+esc(o.meaning)+'</span></p>').join('')+'<label for="'+esc(tid)+'">Or describe what you mean</label><textarea id="'+esc(tid)+'"></textarea><button type="button" class="btn primary" data-clarification-text="1">Continue this analysis</button>')+'</section>'}
function requestPlan(d){const c=d.request_contract||(d.planner_receipt||{}).request_contract;if(!c)return '';
 const r=c.request||{},seen=new Set(),items=(c.bindings||[]).filter(b=>{const k=b.kind+':'+b.quote;if(seen.has(k)||b.path==='where'&&b.quote===c.original_question)return false;seen.add(k);return true});
 const interpretation=((d.planner_receipt||{}).interpretation||{});
 const review=interpretation.review==='fully_consumed_grammar'?'Fully parsed request':interpretation.review==='checked_hosted_interpretation'?'Model interpretation checked against the schema and compiled query':'Candidate interpretation · review required';
 return '<details class="sec" open><summary><strong>How I interpreted your question</strong></summary><p class="hint">'+esc(review)+(c.population?' · limited to '+c.population.length+' saved patients':'')+'</p><ul>'+items.map(b=>'<li>'+esc(b.quote)+' <span class="hint">— '+esc(b.disposition==='unresolved'?'unresolved':b.kind)+'</span></li>').join('')+'</ul><details><summary>Inspect computation</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+esc(c.query)+'</pre><p class="hint">Request '+esc(c.id.slice(0,12))+' · source '+esc((c.source_identity||{}).source_digest||'')+'</p></details></details>'}
function renderTurn(i){const t=state.turns[i];const box=$('#answer');state.active=i;renderThread();paintTranscript(i);if(!t){box.innerHTML='';setResultMode(false);return}
 setResultMode(true);syncScope(t);
 if(t.pending){box.innerHTML='<div class="q">'+esc(t.question)+'</div>'+workingHTML(t);paintWork(t);return}
 if(t.error){const saved=t.action_context&&/^ANS-[0-9a-f]{24}$/.test(t.action_context.id||'');box.innerHTML='<div class="q">'+esc(t.question)+'</div>'+resolvedLine(t)+'<h2 class="headline">'+(saved?'Investigation interrupted. Work saved.':'This question needs another look.')+'</h2><div class="alert bad">'+esc(t.error)+'</div>'+(saved?'<p class="hint">The unfinished draft and its source evidence are saved for continuation. The answer still needs verification.</p><a class="btn primary" href="/action-center?context='+encodeURIComponent(t.action_context.id)+'">Open saved investigation ↗</a>':'<p class="hint">Your earlier answers are still in the conversation. Edit the question or start a new analysis.</p><button class="btn" data-edit-question="'+esc(t.question)+'">Edit question</button>');return}
 if(!t.data){box.innerHTML='<div class="q">'+esc(t.question)+'</div>'+resolvedLine(t)+'<h2 class="headline">'+esc(t.summary||'Saved result')+'</h2>'+actionLink(t.action_context)+'<div class="tools">'+(t.action_context?'<a class="btn primary" href="/ask?context='+encodeURIComponent(t.action_context.id)+'">Open full analysis ↗</a>':'')+'<button type="button" class="btn small" data-ask-standalone="'+esc(t.resolved||t.question)+'">Ask again</button></div>';return}
 if(t.data.clarification){box.innerHTML='<div class="q">'+esc(t.data.clarification.original_question||t.question)+'</div>'+clarificationCard(t.data.clarification)+'<div data-recorded-work>'+recordedWork(t)+'</div>';return}
 const a=t.data.answer||{};state.csv={};const x=executionInfo(t.data),status={answered:'Answer ready',partial:'Partial answer',not_understood:'Needs clarification',unsupported:'Not supported',low_confidence:'Needs review'}[a.status]||label(a.status||'Unknown status');
 let h='<div class="answer-topline"><span class="eyebrow">'+(t.archive?'SAVED ANALYSIS':'ANALYSIS RESULT')+'</span><button class="text-button" data-edit-question="'+esc(t.question)+'">Edit question ↗</button></div><div class="q">'+esc(t.question)+'</div>'+resolvedLine(t)+
 '<div class="brief-status-row"><span class="badge '+esc(a.status)+'">'+esc(status)+'</span>'+(a.verification==='unverified'?'<span class="brief-verification">Verification incomplete</span>':'')+'<button type="button" class="text-button" data-open-answer-tab="method">Analysis details & usage ↗</button></div>'+
 (t.archiveNotice?'<div class="archive-message" role="note">'+briefIcon('evidence')+'<span>'+esc(t.archiveNotice)+'</span></div>':'')+decisionHero(t)+(a.status==='clarify'?warnBox(a):'')+readingsBox(a,t.resolved||t.question);
 h+='<div class="answer-tabs" role="tablist" aria-label="Answer views">'+['overview','data','method'].map((n,j)=>'<button type="button" id="tab-'+n+'" role="tab" aria-controls="result-'+n+'" aria-selected="'+(j===0)+'" tabindex="'+(j===0?0:-1)+'" data-answer-tab="'+n+'">'+({overview:'Decision brief',data:'Records & data',method:'Evidence & method'}[n])+'</button>').join('')+'</div><section class="answer-panel" id="result-overview" data-answer-panel="overview" role="tabpanel" aria-labelledby="tab-overview">';
 h+=decisionActions(t)+answerSummary(a,{noMetric:true})+resultLimitations(a);
 try{h+=visual(a)||'<p class="hint">See the method and sources for the available evidence.</p>'}catch(err){h+='<div class="alert warn">Visual unavailable: '+esc(err.message)+'</div>'+tableHTML(a.table||[],{name:'agere-answer'})}
 h+='</section><section class="answer-panel" id="result-data-panel" data-answer-panel="data" role="tabpanel" aria-labelledby="tab-data" hidden><div class="data-view-heading"><div><h3>Explore the returned data</h3><p>Search records, open details, or download the original values.</p></div><div id="result-downloads"></div></div><div class="result-search"><label class="sr" for="result-search">Search returned rows</label><input id="result-search" type="search" placeholder="Search returned rows"><span id="result-count" class="result-status" role="status"></span><label class="column-toggle"><input type="checkbox" id="result-all-columns"> Show all columns</label></div><div id="result-data"></div></section><section class="answer-panel" id="result-method" data-answer-panel="method" role="tabpanel" aria-labelledby="tab-method" hidden>'+sourceDetails(a)+requestPlan(t.data)+executionDetails(t.data)+(t.seconds!=null?'<p class="hint">Recorded latency: '+esc(t.seconds.toFixed(1))+' seconds</p>':'')+(x.scope?'<p class="hint" data-cost-scope>'+esc(x.scope)+'</p>':'')+details(a)+'</section>';
 const sg=activeContext&&!t.archiveBlocked&&activeContext.result_scope_available!==false?suggestions(a):[];if(sg.length)h+='<div class="chips" aria-label="Follow-ups">'+sg.map(v=>'<button type="button" class="chip" data-ask="'+esc(v)+'">'+esc(v)+' ↗</button>').join('')+'</div>';
 h+=(t.data.workflow?.length?'<section class="answer-workflow"><h3>Your requested workflow</h3>'+t.data.workflow.map(s=>'<p><strong>'+esc(label(s.kind))+' · '+esc(label(s.status))+'</strong> — '+esc(s.title)+(s.reason?'<small>'+esc(s.reason)+'</small>':'')+'</p>').join('')+'</section>':'')+actionLink(t.data.action_context,t.archiveBlocked)+'<div class="sec flag" data-flag>'+(t.data.learning?.components?.component_learning&&t.data.action_context?.id?'<button type="button" class="btn small" data-answercorrect="1">Mark answer correct</button> ':'')+'<button type="button" class="btn small" data-flagopen="1">Flag as wrong</button></div>';
 box.innerHTML=h+'<div data-recorded-work>'+recordedWork(t)+'</div>';$('#tab-data').setAttribute('aria-controls','result-data-panel');renderResultData();showAnswerTab(activeTab);
 if(t.focus){const el=box.querySelector('[data-focus="'+t.focus+'"]');if(el)el.scrollIntoView({behavior:'smooth',block:'start'})}}
function renderThread(){const ul=$('#thread');ul.innerHTML=state.turns.map((t,i)=>{const a=(t.data||{}).answer||{};const st=t.pending?'pending':t.error?'error':(a.status||t.status||'');
 const r=((t.data||{}).conversation||{}).resolved_question||t.resolved;
 return '<li><button type="button" data-turn="'+i+'"'+(i===state.active?' aria-current="true"':'')+'><div class="tq"><span class="dot '+esc(st)+'" aria-hidden="true"></span>'+esc(t.question)+'</div>'+
  (r&&r!==t.question?'<div class="ta">→ '+esc(r)+'</div>':'')+'<div class="ta">'+esc(t.pending?'working…':t.error?'not answered':String(a.headline||t.summary||'').slice(0,110))+'</div></button></li>'}).join('')||'<li class="hint">No questions yet.</li>';
 $('#threadhint').hidden=state.turns.length>0}

/* ---------- asking ---------- */
async function ask(question,opt){opt=opt||{};question=String(question||'').trim();if(!question||state.busy)return null;
 const selectedScope=$('#question-scope').value;
 const parent=opt.standalone||opt.clarification?null:(['saved','input','auto'].includes(selectedScope)||selectedScope.startsWith('population:')?activeContext:null);
 state.busy=true;$('#askb').disabled=true;$('#newconv').disabled=true;$('#q').readOnly=true;activeTab='overview';
 if(!state.cid){state.cid=newId();store.set('agere-conversation',state.cid)}
 const t={question,pending:true,progressKey:progressKey(),focus:opt.focus,scopeBefore:parent?{context:parent,value:selectedScope}:null};state.turns.push(t);renderTurn(state.turns.length-1);const t0=performance.now();const stopWork=beginWork(t);
 try{const r=await fetch('/api/stack/compose',{method:'POST',headers:{'content-type':'application/json','X-Agere-Response-View':'answer','X-Agere-Progress-Key':t.progressKey},body:JSON.stringify(opt.clarification?{conversation_id:state.cid,clarification_response:opt.clarification,interaction_mode:$('#interaction-mode').value}:{question,delivery_generation:$('#delivery-generation').value,conversation_id:state.cid,interaction_mode:$('#interaction-mode').value,...(parent?{answer_context_id:parent.id,...(selectedScope!=='saved'?{answer_scope:selectedScope}:{})}:{}),...(!parent?{scope:'warehouse'}:{})})});
  let d;try{d=await r.json()}catch(_){d={error:'HTTP '+r.status}}t.seconds=(performance.now()-t0)/1000;t.pending=false;
  if(d.error||!r.ok){t.error=d.error||('HTTP '+r.status);t.action_context=d.action_context}else{t.data=d;t.resolved=(d.conversation||{}).resolved_question;if(opt.clarification)state.turns.forEach(old=>{if(old.data?.clarification?.id===opt.clarification.id)old.data.clarification.status='complete'})}}
 catch(err){t.pending=false;t.error=String(err)}finally{stopWork();state.busy=false;$('#askb').disabled=false;$('#newconv').disabled=false;$('#q').readOnly=false}
 if(opt.clarification)try{const r=await fetch('/api/stack/conversation?id='+encodeURIComponent(state.cid));if(r.ok){const d=await r.json();for(const item of d.turns||[]){const c=item.answer_summary?.clarification;if(c)for(const old of state.turns)if(old.data?.clarification?.id===c.id)old.data.clarification=c}}}catch(_){}
 state.outFilter=null;renderTurn(state.turns.indexOf(t));completeWork(t);syncContractDemo();loadEcon();loadSaved();document.dispatchEvent(new Event('agere-activity-changed'));return t}
function loadConversation(){if(!state.cid)return;const epoch=conversationEpoch;fetch('/api/stack/conversation?id='+encodeURIComponent(state.cid)).then(r=>r.ok?r.json():null).then(d=>{if(!d||state.turns.length||epoch!==conversationEpoch)return;
 state.turns=(d.turns||[]).map(x=>({question:x.question,resolved:x.resolved_question,error:x.error||null,summary:(x.answer_summary||{}).headline||'',status:(x.answer_summary||{}).status,action_context:(x.answer_summary||{}).action_context,...(x.answer_summary?.clarification?{data:{answer:x.answer_summary,clarification:x.answer_summary.clarification}}:{})}));
 renderThread();const pending=state.turns.map((t,i)=>t.data?.clarification?.status==='pending'?i:-1).filter(i=>i>=0);if(pending.length)renderTurn(pending[pending.length-1])}).catch(()=>{})}
function newConversation(){if(state.busy)return;conversationEpoch++;state.cid=null;store.set('agere-conversation',null);state.turns=[];state.active=-1;activeContext=null;activeTab='overview';renderThread();paintTranscript(-1);syncScope(null);setResultMode(false);$('#answer').innerHTML='';$('#q').value='';history.replaceState(null,'','/ask'+(demo.on?'?demo=1':''));$('#q').focus()}
function loadEcon(){fetch('/api/stack/economics').then(r=>r.ok?r.json():null).then(e=>{if(!e)return;
 $('#econ').innerHTML='<span class="pill" title="Questions answered by this installation"><b>'+fmt(e.questions||0)+'</b> questions</span>'+
  '<span class="pill" title="Answered without any paid model call"><b>'+(e.share_without_api_calls!=null?Math.round(100*e.share_without_api_calls)+'%':'—')+'</b> without paid calls</span>'+
  '<span class="pill" title="Known API-equivalent usage estimates; subscription allocation and local compute are excluded"><b>$'+(e.api_usd||0).toFixed(2)+'</b> known usage estimate</span>'+(e.unknown_usage_questions?'<span class="pill">'+fmt(e.unknown_usage_questions)+' unpriced question(s)</span>':'')+'<span id="loop"></span>';loadLoop()}).catch(()=>{})}
function loadLoop(){Promise.all(['/api/stack/learning','/api/stack/learning-loop'].map(url=>fetch(url).then(r=>r.ok?r.json():null))).then(([learning,loop])=>{
 if(!$('#loop'))return;const shapes=learning?.reusable_structures||{},stored=Object.values(shapes).reduce((n,s)=>n+(s.active||0),0),revoked=Object.values(shapes).reduce((n,s)=>n+(s.revoked||0),0);
 let html=learning?'<span class="pill" title="'+esc(learning.learning_mode||'Checked structures remain source- and policy-bound; counts are not a quality score.')+'"><b>'+fmt(stored)+'</b> saved plan structures · <b>'+fmt(learning.corrections||0)+'</b> corrections · <b>'+fmt(revoked)+'</b> suspended</span>':'';
 if(learning?.procedural_memory?.revisions){const m=learning.procedural_memory;html+='<span class="pill" title="'+esc(m.qualification)+'"><b>'+fmt(m.revisions)+'</b> lesson revisions · <b>'+fmt(m.states?.quarantined||0)+'</b> quarantined</span>'}
 const hc=learning?.healthcare_domains;if(hc)html+='<span class="pill" title="Questions identify knowledge gaps. Checked facts apply only to their source version and scope."><b>'+fmt(hc.questions)+'</b> healthcare questions · <b>'+fmt(hc.verified_rules)+'</b> checked facts · <b>'+fmt(hc.inactive_rules)+'</b> suspended facts</span>';
 const fin=learning?.healthcare_economics;if(fin)html+='<span class="pill" title="Questions are captured automatically. Rule lessons apply only to their recorded program, year and source version."><b>'+fmt(fin.questions)+'</b> economics questions · <b>'+fmt(fin.verified_rules)+'</b> checked rules · <b>'+fmt(fin.inactive_rules)+'</b> suspended rules</span>';
 if(loop?.status==='ok'){const g=loop.gaps||{};html+='<span class="pill" title="Offline development report; no automatic model promotion"><b>'+fmt(g.actionable_clusters||0)+'</b> gap clusters · <b>'+fmt((loop.regressions||{}).cases||0)+'</b> regressions</span>'}
 $('#loop').innerHTML=html}).catch(()=>{})}
function briefFeedback(card,event){if(!card)return;const body={card_id:card,event};if(state.briefingId)body.briefing_id=state.briefingId;
 fetch('/api/stack/briefing-feedback',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}).catch(()=>{})}
function loadBriefing(){return fetch('/api/stack/briefing').then(r=>r.ok?r.json():Promise.reject(r.status)).then(d=>{
 const cards=d.cards||[];state.briefing=cards;const failures=(d.checks||[]).filter(c=>c.status==='unavailable');
 $('#brief-status').textContent=cards.length+' review opportunities · '+(d.as_of||'Current snapshot')+' · '+(failures.length?failures.length+' checks unavailable':'5 checks completed')+' · No model calls';
 $('#brief').innerHTML=cards.map((c,i)=>'<article class="chief-card"><div class="chief-card-top"><span>'+String(i+1).padStart(2,'0')+' / '+esc(label(c.topic))+'</span><span class="signal-badge">OBSERVED</span></div><h3>'+esc(c.title)+'</h3><div class="signal-value">'+(c.unit==='allowed cents'?new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:0}).format(c.value/100):fmt(c.value,0))+'<small>'+esc(c.unit==='allowed cents'?'allowed dollars':c.unit)+'</small></div><p>'+esc(c.why)+'</p><a class="chief-investigate" href="/ask?context='+encodeURIComponent(c.context_id)+'">'+esc(c.action)+' <span>↗</span></a><div class="chief-pathways"><button data-brief-path="deliverable" data-card="'+esc(c.card_id)+'">Build a view</button><button data-brief-path="simulation" data-card="'+esc(c.card_id)+'"'+(!c.patients?' disabled title="No patient worklist in this answer"':'')+'>Simulate work</button><button data-brief-path="monitor" data-card="'+esc(c.card_id)+'">Monitor</button></div><details><summary>Definition &amp; evidence</summary><p>'+esc(c.follow_up)+'</p><pre>'+esc(JSON.stringify(c.evidence.rows,null,2))+'</pre></details></article>').join('')||'<p class="hint">No review opportunities found by the completed checks. Ask your own question below.</p>';
 if(failures.length)$('#brief').insertAdjacentHTML('beforeend','<details class="brief-check-errors"><summary>Inspect unavailable checks</summary>'+failures.map(f=>'<p>'+esc(f.domain)+': '+esc(f.reason)+'</p>').join('')+'</details>');
 $('#brief').querySelectorAll('[data-brief-path]').forEach(b=>b.onclick=async()=>{b.disabled=true;const old=b.textContent;b.textContent='Preparing…';try{const r=await fetch('/api/stack/briefing-pathway',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({card_id:b.dataset.card,pathway:b.dataset.briefPath})});const v=await r.json();if(!r.ok)throw Error(v.error||'Unable to prepare this pathway');location.href=v.action_context.url}catch(e){$('#brief-status').textContent=e.message;b.textContent=old;b.disabled=false}});return cards;
 }).catch(()=>{$('#brief-status').textContent='The briefing could not load. Your question workspace is still available.';$('#brief').innerHTML='<button class="btn" id="retry-brief">Retry briefing</button>';$('#retry-brief').onclick=loadBriefing;return []})}

/* ---------- one saved contract workflow ---------- */
const demo={on:/[?&]demo=1\b/.test(location.search),i:0,done:new Set()};
function renderDemo(){if(!demo.on)return;const el=$('#demo');el.hidden=false;
 el.innerHTML='<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center"><b>Population contract walkthrough</b><span class="muted" style="font-size:13px">One analysis. Two buyer views. Saved follow-up.</span>'+
  '<button type="button" class="btn primary small" id="demonext"'+(demo.i>=DEMO.length?' disabled':'')+'>'+(demo.i>=DEMO.length?'Walkthrough complete':'Step '+(demo.i+1)+': '+esc(DEMO[demo.i].label))+'</button>'+
  '<button type="button" class="btn small" id="demoreset">Restart walkthrough</button></div><ol>'+DEMO.map((s,i)=>'<li><button type="button" data-demo="'+i+'"'+(i===demo.i?' aria-current="step"':'')+(demo.done.has(i)?' class="done"':'')+'>'+(i+1)+'. '+esc(s.label)+(demo.done.has(i)?' ✓':'')+'</button></li>').join('')+'</ol>'}
function demoContract(){const t=state.turns[state.active];return t&&!t.archiveBlocked&&t.data?.answer?.overall?.contract_performance?t:null}
function syncContractDemo(){if(demo.on&&demoContract()){demo.done.add(0);if(demo.i===0)demo.i=1;renderDemo()}}
async function runStep(i){const s=DEMO[i];if(!s||state.busy)return;demo.i=i;renderDemo();
 const note=$('#demonote');note.innerHTML='<div class="demonote" role="status"><b>Step '+(i+1)+' of '+DEMO.length+' · '+esc(s.label)+'.</b> '+esc(s.note||'')+'</div>';
 if(s.kind==='input'){
  if(demoContract()){syncContractDemo();return}
  const t=state.turns[state.active];
  if(!t?.data?.clarification||t.data.clarification.status!=='pending')await ask('Show contract performance',{standalone:true});
  syncContractDemo();return;
 }
 const t=demoContract();
 if(!t){note.insertAdjacentHTML('beforeend','<p class="hint" role="status">Choose contract inputs above or reopen a current contract answer before continuing. An unresolved or stale answer cannot complete this step.</p>');return}
 if(s.kind==='work'){
  const c=t.data.action_context||t.action_context;
  if(!c||!/^ANS-[0-9a-f]{24}$/.test(c.id||'')){note.insertAdjacentHTML('beforeend','<p role="status">This answer has no saved work context yet.</p>');return}
  note.insertAdjacentHTML('beforeend','<p><a class="btn primary" href="/action-center?context='+encodeURIComponent(c.id)+'">Open this answer’s review workspace ↗</a></p>');
 }else{
  const target=$('[data-contract-section="'+s.section+'"]');
  if(!target){note.insertAdjacentHTML('beforeend','<p role="status">This section is unavailable in the current answer. Reopen its full overview; no new calculation has been started.</p>');return}
  target.open=true;
  if(s.id==='drivers')for(const name of ['monthly','members']){const other=$('[data-contract-section="'+name+'"]');if(other)other.open=true}
  target.scrollIntoView({behavior:'smooth',block:'center'});
 }
 demo.done.add(i);const next=DEMO.findIndex((_,index)=>!demo.done.has(index));demo.i=next<0?DEMO.length:next;renderDemo();
}

__EXPLORE_JS__

/* ---------- events (delegated; buttons and role=button elements are keyboard reachable) ---------- */
function activate(el){if(el.dataset.csv){downloadCSV(el.dataset.csv);return}
 if(el.hasAttribute('data-contract-save')||el.hasAttribute('data-contract-restore')){saveContractInput(el);return}
 if(el.dataset.clarificationOption!=null||el.dataset.clarificationText!=null){const card=el.closest('[data-clarification]');if(!card)return;
  const response=el.dataset.clarificationOption!=null?{id:card.dataset.clarification,option_id:el.dataset.clarificationOption}:{id:card.dataset.clarification,text:card.querySelector('textarea').value.trim()};
  if(response.text===''){card.querySelector('textarea').focus();return}ask(el.dataset.clarificationOption!=null?el.textContent:'Clarification: '+response.text,{clarification:response});return}
 if(el.dataset.briefDismiss){briefFeedback(el.dataset.briefDismiss,'dismissed');const b=el.closest('.bcard');if(b)b.remove();return}
 if(el.dataset.askStandalone!=null){if(el.dataset.briefCard)briefFeedback(el.dataset.briefCard,'asked_follow_up');ask(el.dataset.askStandalone,{standalone:true});return}
 if(el.dataset.ask!=null){if(activeContext&&$('#question-scope').value!=='input')$('#question-scope').value='saved';ask(el.dataset.ask);return}
 if(el.dataset.editQuestion!=null){$('#q').value=el.dataset.editQuestion;$('#question-scope').value='warehouse';updateScopeNote();$('#q').focus();$('#q').scrollIntoView({block:'center',behavior:'smooth'});return}
 if(el.dataset.turn!=null){renderTurn(+el.dataset.turn);return}
 if(el.dataset.demo!=null){runStep(+el.dataset.demo);return}
 if(el.id==='demonext'){runStep(demo.i);return}if(el.id==='demoreset'){demo.i=0;demo.done.clear();renderDemo();return}
 if(el.dataset.clearfilter){state.outFilter=null;refreshWorklist();return}
 if(el.dataset.series!=null){const key=el.closest('[data-legend]').dataset.legend,st=state[key];if(!st)return;const s=st.series[+el.dataset.series];s.hidden=!s.hidden;el.setAttribute('aria-pressed',String(!s.hidden));
  const box=document.querySelector('[data-chart="'+key+'"]');if(box)box.innerHTML=st.draw?st.draw(st):survChart(st);return}
 if(el.dataset.pick!=null){const mon=el.closest('#answer')&&document.querySelector('[data-monsel]');
  if(mon&&el.tagName==='TR'){if(+el.dataset.pick>=0){mon.value=el.dataset.pick;mon.dispatchEvent(new Event('change',{bubbles:true}));mon.scrollIntoView({behavior:'smooth',block:'center'})}return}
  state.outFilter=el.dataset.pick;refreshWorklist();return}
 if(el.dataset.answercorrect){const t=state.turns[state.active]||{},box=el.closest('[data-flag]');el.disabled=true;
  fetch('/api/stack/correct',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({context_id:t.data.action_context.id,question:t.data.conversation?.resolved_question||t.question,accepted:true,note:'User marked this saved answer correct'})}).then(r=>r.json()).then(d=>{box.innerHTML='<p role="status" class="'+(d.error?'badc':'goodc')+'">'+esc(d.error||d.effect)+'</p>'}).catch(e=>{el.disabled=false;box.insertAdjacentHTML('beforeend','<p class="badc">'+esc(e)+'</p>')});return}
 if(el.dataset.flagopen){const box=el.closest('[data-flag]');box.innerHTML='<label for="flagnote"><b>What is wrong with this answer?</b></label><textarea id="flagnote"></textarea><div class="tools"><button type="button" class="btn small primary" data-flagsend="1">Record correction</button><button type="button" class="btn small" data-flagcancel="1">Cancel</button></div>';$('#flagnote').focus();
  const t=state.turns[state.active]||{},question=((t.data||{}).conversation||{}).resolved_question||t.question;
  fetch('/api/stack/healthcare-learning?question='+encodeURIComponent(question||'')).then(r=>r.ok?r.json():null).then(d=>{
   if(!box.querySelector('#flagnote'))return;
   if(!d?.rule_options?.length){if(d?.source_scope?.unresolved_scope?.length){const hint=document.createElement('p');hint.className='hint';hint.textContent='To check a cited fact, the question needs a covered model performance year, a clear Star Ratings or measurement year, or the applicable reference version. Your correction can still be recorded for review.';box.insertBefore(hint,box.querySelector('.tools'))}return}box._economicsRules=d.rule_options;
   const label=document.createElement('label');label.textContent='Optional: rule this correction refers to';
   const select=document.createElement('select');select.dataset.flagrule='1';select.setAttribute('aria-label','Cited healthcare fact or program rule');
   select.add(new Option('Keep as feedback for review',''));
   d.rule_options.forEach((r,i)=>select.add(new Option(r.label+' · '+(r.display_value??(typeof r.value==='number'?(r.value*100).toFixed(1)+'%':String(r.value))),String(i))));
   label.append(select);box.insertBefore(label,box.querySelector('.tools'));
   const note=document.createElement('p');note.className='hint';note.textContent='A selected rule is checked against its cited specification. Your explanation remains feedback to investigate.';label.append(note);
   const cite=document.createElement('p');label.append(cite);select.onchange=()=>{cite.replaceChildren();const rule=box._economicsRules[select.value];if(!rule)return;for(const src of rule.sources){const a=document.createElement('a');a.href=src.url;a.textContent=src.title;a.target='_blank';a.rel='noopener';cite.append(a,document.createTextNode(' '))}};
  }).catch(()=>{});return}
 if(el.dataset.flagcancel){el.closest('[data-flag]').innerHTML='<button type="button" class="btn small" data-flagopen="1" style="color:var(--bad)">Flag as wrong</button>';return}
 if(el.dataset.flagsend){const box=el.closest('[data-flag]'),note=$('#flagnote').value.trim();if(!note){$('#flagnote').focus();return}const t=state.turns[state.active]||{};
  const question=((t.data||{}).conversation||{}).resolved_question||t.question;el.disabled=true;
  const selected=box.querySelector('[data-flagrule]'),chosen=selected&&selected.value!==''?box._economicsRules?.[+selected.value]:null;
  const rule=chosen?(chosen.entry_id?{scope:chosen.scope,entry_id:chosen.entry_id,field:chosen.field,value:chosen.value,source_digest:chosen.source_digest}:{program:chosen.program,parameter:chosen.parameter,value:chosen.value,source_digest:chosen.source_digest}):null;
  fetch('/api/stack/correct',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({question,note,...(t.data?.learning?.components?.component_learning&&t.data.action_context?.id?{context_id:t.data.action_context.id}:{}),...(rule?{rule}:{})})}).then(r=>r.json()).then(d=>{
   document.dispatchEvent(new Event('agere-activity-changed'));loadLoop();box.innerHTML='<p class="'+(d.error||d.rule_verification?.status==='rejected'?'badc':'goodc')+'" role="status">'+esc(d.error||d.effect||'Correction recorded; related answers require revalidation.')+'</p>'}).catch(e=>{box.innerHTML='<p class="badc">'+esc(e)+'</p>'})}}
function refreshWorklist(){const t=state.turns[state.active];const box=document.querySelector('[data-worklist]');if(t&&t.data&&box){box.innerHTML=worklist(t.data.answer);box.scrollIntoView({behavior:'smooth',block:'nearest'})}}
const ACT='[data-contract-save],[data-contract-restore],[data-clarification-option],[data-clarification-text],[data-edit-question],[data-csv],[data-ask],[data-ask-standalone],[data-turn],[data-demo],#demonext,#demoreset,[data-clearfilter],[data-series],[data-pick],[data-answercorrect],[data-flagopen],[data-flagcancel],[data-flagsend],[data-brief-dismiss]';
document.addEventListener('click',e=>{const el=e.target.closest(ACT);if(el){e.preventDefault();activate(el)}});
document.addEventListener('keydown',e=>{if(e.key!=='Enter'&&e.key!==' ')return;const el=e.target.closest(ACT);if(!el||el.tagName==='BUTTON')return;if(e.target.tagName==='TEXTAREA'||e.target.tagName==='INPUT')return;e.preventDefault();activate(el)});
document.addEventListener('change',e=>{const d=e.target.closest('[data-demsel]');if(!d)return;const st=state[d.dataset.demsel];const box=document.querySelector('[data-chart="'+d.dataset.demsel+'"]');if(st&&box)box.innerHTML=fanChart(st.series[+d.value],st.o)});
document.addEventListener('change',e=>{const s=e.target.closest('[data-monsel]');if(!s)return;const st=state[s.dataset.monsel];const box=document.querySelector('[data-chart="'+s.dataset.monsel+'"]');if(st&&box)box.innerHTML=controlChart(st.charts[+s.value])});
$('#askf').addEventListener('submit',e=>{e.preventDefault();if(state.busy)return;const v=$('#q').value;if(v.trim()){$('#q').value='';ask(v)}});
$('#newconv').addEventListener('click',newConversation);

renderThread();loadEcon();setupExplorer();renderDemo();
</script></body></html>"""


def page():
    assets = Path(__file__).parent / 'assets'
    shell = (assets / 'explore.html').read_text().replace('/* EXPLORE_CSS */',
        (assets / 'answer.css').read_text() + '\n' + (assets / 'explore.css').read_text() + '\n' + (assets / 'answer_presentation.css').read_text())
    return (PAGE.replace('__EXPLORE_SHELL__', shell)
            .replace('__EXPLORE_JS__', (assets / 'answer_presentation.js').read_text() + '\n' + (assets / 'explore.js').read_text() + '\n' +
                     (assets / 'experience.js').read_text())
            .replace('__DEMO_STEPS__', json.dumps(DEMO_STEPS).replace('</', '<\\/')))
