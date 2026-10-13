"""Agere workspace (/ask): conversation thread, answer card with the right visual per answer type, briefing and economics.

One self-contained HTML page (no external assets; charts are hand-written SVG). It reads only existing routes:
/api/stack/compose (with conversation_id), /api/stack/conversation, /api/stack/briefing, /api/stack/economics and
/api/stack/correct, /api/stack/learning-loop (read-only loop summary pill), plus /api/stack/briefing-feedback (opened / asked follow-up / dismissed per briefing card,
the briefing ranker's implicit feedback). /ask?demo=1 adds the scripted buyer walkthrough in DEMO_STEPS; every step asks a real question.
"""
import json

# Scripted buyer walkthrough for /ask?demo=1. Edit freely: kind is "briefing" (asks the top briefing card's follow-up),
# "ask" (a standalone question) or "followup" (a follow-up resolved against the previous turn). "focus" scrolls the
# answer card to a section (settlement, allocation, worklist, reasons, effect, curve).
DEMO_STEPS = [
    {'id': 'briefing', 'kind': 'briefing', 'label': 'Morning briefing',
     'note': 'Agere scans the data release with no question asked and ranks what stands out. The top card\'s follow-up is asked for real.'},
    {'id': 'settlement', 'kind': 'ask', 'label': 'VBC settlement risk', 'focus': 'settlement',
     'question': 'What would our shared savings be with a benchmark of $90 PMPM and a sharing rate of 50%?',
     'note': 'Exact contract arithmetic on the supplied terms, then 4,000 bootstrap draws through the same arithmetic: chance of earning savings, owing a loss, value at risk.'},
    {'id': 'allocation', 'kind': 'ask', 'label': 'Fair allocation', 'focus': 'allocation',
     'question': 'What would our shared savings be with a benchmark of $90 PMPM and a sharing rate of 50%, by practice?',
     'note': 'Shapley values split the pooled settlement by each practice\'s marginal contribution, compared with a pro-rata split by member-months.'},
    {'id': 'drivers', 'kind': 'ask', 'label': 'Which practices drove it',
     'question': 'What is the allowed PMPM by practice?',
     'note': 'Completion-adjusted allowed cost per member per month. Click a bar to drill into that practice in the same conversation.'},
    {'id': 'worklist', 'kind': 'ask', 'label': 'Who to call this week', 'focus': 'worklist',
     'question': 'who should our care managers call this week? we have 40 slots',
     'note': 'An optimised 40-slot worklist: expected value, engagement probability, minutes and a certificate that the selection is optimal for the stated objective.'},
    {'id': 'why', 'kind': 'ask', 'label': 'Why: reasons and drivers', 'focus': 'reasons',
     'question': 'What drives emergency department visits in our population?',
     'note': 'Each worklist row carries its own reason chips (previous step). This asks what drives ED use across the whole population.'},
    {'id': 'effect', 'kind': 'ask', 'label': 'Did care management work?', 'focus': 'effect',
     'question': 'Did care management reduce ED visits?',
     'note': 'Matched difference-in-differences with placebo, pre-trend and synthetic-control checks. Warnings and the inference status are shown, not hidden.'},
    {'id': 'curve', 'kind': 'ask', 'label': 'Readmission curve by payer', 'focus': 'curve',
     'question': 'What does the readmission curve look like by payer?',
     'note': 'Kaplan-Meier time to readmission after discharge, with censoring and a log-rank comparison across payers.'},
    {'id': 'notes', 'kind': 'ask', 'label': 'EF < 40 from notes',
     'question': 'How many heart failure patients have an EF under 40 documented in notes?',
     'note': 'Ejection fraction lives in free text. Agere reads it from note-extracted facts and states the definition it used.'},
    {'id': 'drill', 'kind': 'followup', 'label': 'Drill down: list them',
     'question': 'list them',
     'note': 'A follow-up in the same conversation. The console shows the standalone question it was answered as.'},
]

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agere Workspace</title>
<style>
:root{--bg:#f6f6f4;--fg:#1c1917;--muted:#57534e;--line:#e2e0dc;--card:#fff;--soft:#f1f0ec;--accent:#0f766e;--accent-fg:#fff;--warn:#92400e;--warn-bg:#fef3c7;
--bad:#b91c1c;--bad-bg:#fee2e2;--good:#166534;--good-bg:#dcfce7;--focus:#2563eb;
--s1:#0f766e;--s2:#b45309;--s3:#4f46e5;--s4:#be123c;--s5:#0369a1;--s6:#4d7c0f;--grid:#e7e5e4;--band:rgba(15,118,110,.10)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0e0d0c;--fg:#f5f5f4;--muted:#b5afa9;--line:#34302c;--card:#1a1816;--soft:#24211e;--accent:#2dd4bf;--accent-fg:#042f2e;
--warn:#fcd34d;--warn-bg:#3b2a07;--bad:#fca5a5;--bad-bg:#3f1414;--good:#86efac;--good-bg:#0f2e1a;--focus:#93c5fd;
--s1:#2dd4bf;--s2:#fbbf24;--s3:#a5b4fc;--s4:#fb7185;--s5:#7dd3fc;--s6:#a3e635;--grid:#2c2926;--band:rgba(45,212,191,.12)}}
:root[data-theme="dark"]{--bg:#0e0d0c;--fg:#f5f5f4;--muted:#b5afa9;--line:#34302c;--card:#1a1816;--soft:#24211e;--accent:#2dd4bf;--accent-fg:#042f2e;
--warn:#fcd34d;--warn-bg:#3b2a07;--bad:#fca5a5;--bad-bg:#3f1414;--good:#86efac;--good-bg:#0f2e1a;--focus:#93c5fd;
--s1:#2dd4bf;--s2:#fbbf24;--s3:#a5b4fc;--s4:#fb7185;--s5:#7dd3fc;--s6:#a3e635;--grid:#2c2926;--band:rgba(45,212,191,.12)}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;overflow-x:hidden}
a{color:var(--accent)}button{font:inherit;color:inherit}
:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
header.top{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--card)}
header.top h1{font-size:18px;margin:0;letter-spacing:.2px}header.top .tag{color:var(--muted);font-size:13px}
.econ{margin-left:auto;display:flex;flex-wrap:wrap;gap:6px;font-size:13px}
.pill{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--line);border-radius:999px;padding:2px 10px;background:var(--soft);white-space:nowrap}
.pill b{font-variant-numeric:tabular-nums}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr) 320px;gap:16px;padding:16px;max-width:1600px;margin:0 auto}
@media (max-width:1180px){.layout{grid-template-columns:240px minmax(0,1fr)}.right{grid-column:1/-1}}
@media (max-width:800px){.layout{grid-template-columns:minmax(0,1fr);padding:12px 16px}.left{order:2}.center{order:1}.right{order:3}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.panel>h2:not(.headline){font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 8px}
.btn{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:6px 12px;cursor:pointer;min-height:36px}
.btn:hover{border-color:var(--accent)}.btn.primary{background:var(--accent);color:var(--accent-fg);border-color:var(--accent);font-weight:600}
.btn:disabled{opacity:.55;cursor:default}.btn.small{padding:3px 9px;font-size:13px;min-height:30px}
.thread{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.thread button{display:block;width:100%;text-align:left;border:1px solid var(--line);background:var(--soft);border-radius:8px;padding:8px 10px;cursor:pointer}
.thread button[aria-current="true"]{border-color:var(--accent);box-shadow:inset 3px 0 0 var(--accent)}
.thread .tq{font-weight:600;font-size:14px;overflow-wrap:anywhere}.thread .ta{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle;background:var(--muted)}
.dot.answered{background:var(--good)}.dot.partial{background:var(--warn)}.dot.error,.dot.unsupported{background:var(--bad)}.dot.pending{background:var(--focus)}
.hint{color:var(--muted);font-size:13px;margin:8px 0 0}
form.askf{display:flex;gap:8px}form.askf input{flex:1;min-width:0;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg);font-size:15px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:4px 10px;font-size:13px;cursor:pointer;color:var(--accent)}
.chip:hover{border-color:var(--accent)}
.answer{margin-top:14px}.answer .q{font-weight:600;color:var(--muted);font-size:14px;overflow-wrap:anywhere}
.resolved{font-size:13px;color:var(--muted);border-left:3px solid var(--accent);padding-left:8px;margin:6px 0;overflow-wrap:anywhere}
.headline{font-size:19px;line-height:1.4;font-weight:650;margin:8px 0;overflow-wrap:anywhere}.headline.long{font-size:16px;font-weight:600}
.badges{display:flex;flex-wrap:wrap;gap:6px;font-size:12px;color:var(--muted)}
.badge{border:1px solid var(--line);border-radius:6px;padding:1px 8px;background:var(--soft)}
.badge.answered{color:var(--good);background:var(--good-bg);border-color:transparent}.badge.partial{color:var(--warn);background:var(--warn-bg);border-color:transparent}
.badge.unsupported,.badge.error{color:var(--bad);background:var(--bad-bg);border-color:transparent}
.alert{border-radius:10px;padding:10px 12px;margin:12px 0;font-size:14px}.alert ul{margin:4px 0 0;padding-left:18px}
.alert.warn{background:var(--warn-bg);color:var(--fg);border-left:4px solid var(--warn)}.alert.bad{background:var(--bad-bg);border-left:4px solid var(--bad)}
.alert h3{margin:0;font-size:14px}
.sec{margin-top:16px}.sec>h3{font-size:14px;margin:0 0 8px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
.kpi{background:var(--soft);border-radius:10px;padding:8px 10px}.kpi .l{font-size:12px;color:var(--muted)}.kpi .v{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.big{font-size:44px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1}
.chart{width:100%;height:auto;display:block;overflow:visible}
.chart text{fill:var(--muted);font-size:11px}.chart .axis{stroke:var(--line)}.chart .gridl{stroke:var(--grid)}
.chart [data-ask],.chart [data-pick]{cursor:pointer}.chart [data-ask]:hover rect,.chart [data-ask]:focus rect{opacity:.8}
.chart [data-ask]:focus-visible{outline:none}.chart [data-ask]:focus-visible rect{stroke:var(--focus);stroke-width:3}
.legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;color:var(--muted);margin:4px 0}
.legend button{border:0;background:none;cursor:pointer;padding:2px 0;color:var(--muted)}.legend button[aria-pressed="false"]{opacity:.45;text-decoration:line-through}
.sw{display:inline-block;width:12px;height:3px;vertical-align:middle;margin-right:5px;border-radius:2px}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;margin-top:6px;max-height:520px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{position:sticky;top:0;background:var(--soft);color:var(--muted);font-weight:600;white-space:nowrap}td{white-space:nowrap}td.wrap{white-space:normal;min-width:200px;max-width:360px}details.why{border:0;margin:2px 0 0;padding:0}details.why summary{padding:0;font-size:12px}td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr[data-ask],tr[data-pick]{cursor:pointer}tr[data-ask]:hover td,tr[data-pick]:hover td{background:var(--soft)}
.rchip{display:inline-block;border-radius:6px;padding:1px 7px;margin:1px 3px 1px 0;font-size:12px;border:1px solid var(--line);background:var(--soft);white-space:normal}
.rchip.utilization{border-color:var(--s4)}.rchip.care_gap{border-color:var(--s3)}.rchip.rising_risk{border-color:var(--s2)}.rchip.social_need{border-color:var(--s5)}
.tools{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px}
details{margin-top:8px;border-top:1px solid var(--line);padding-top:6px}summary{cursor:pointer;color:var(--muted);font-size:13px;padding:4px 0}
details pre{white-space:pre-wrap;font-size:12px;color:var(--muted);margin:4px 0;overflow-wrap:anywhere}
.pids{display:flex;flex-wrap:wrap;gap:4px;font-family:ui-monospace,Menlo,monospace;font-size:12px}.pids span{background:var(--soft);border-radius:4px;padding:1px 6px}
.probbar{display:flex;height:28px;border-radius:8px;overflow:hidden;border:1px solid var(--line)}
.probbar div{display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;color:#fff;min-width:0;overflow:hidden;white-space:nowrap}
.probbar .earn{background:#166534}.probbar .corr{background:#57534e}.probbar .owe{background:#b91c1c}
.brief{display:flex;flex-direction:column;gap:8px}
.brief button{text-align:left;border:1px solid var(--line);background:var(--soft);border-radius:10px;padding:10px;cursor:pointer;width:100%}
.brief .bcard{position:relative}.brief button.x{position:absolute;top:4px;right:4px;width:auto;padding:2px 8px;border:none;background:none;color:var(--muted);font-size:16px}
.brief details{font-size:12px;color:var(--muted);margin:2px 6px}.brief .xp{color:var(--accent)}
.brief button:hover{border-color:var(--accent)}.brief .k{font-size:12px;color:var(--muted)}.brief .h{font-size:14px;font-weight:600;margin:2px 0;overflow-wrap:anywhere}.brief .f{font-size:13px;color:var(--accent);overflow-wrap:anywhere}
.right.flash .panel{box-shadow:0 0 0 3px var(--accent)}
.demo{background:var(--card);border-bottom:1px solid var(--line);padding:10px 16px}
.demo ol{display:flex;flex-wrap:wrap;gap:6px;list-style:none;margin:8px 0 0;padding:0;counter-reset:s}
.demo li button{border:1px solid var(--line);background:var(--soft);border-radius:999px;padding:4px 10px;font-size:13px;cursor:pointer}
.demo li button[aria-current="step"]{border-color:var(--accent);background:var(--accent);color:var(--accent-fg)}
.demo li button.done{color:var(--muted)}
@media (max-width:600px){.demo ol{flex-wrap:nowrap;overflow-x:auto;padding-bottom:4px}.demo li button{white-space:nowrap}}
.demonote{border:1px dashed var(--accent);border-radius:10px;padding:8px 12px;margin-top:12px;font-size:14px}
.flag textarea{width:100%;min-height:64px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);padding:8px;font:inherit}
.muted{color:var(--muted)}.warnc{color:var(--warn)}.badc{color:var(--bad)}.goodc{color:var(--good)}
.skeleton{color:var(--muted);padding:18px 0}
</style></head><body>
<a class="sr" href="#q">Skip to question</a>
<header class="top"><h1>Agere</h1><span class="tag">Population analytics workspace · synthetic data</span>
<div class="econ" id="econ" aria-label="Question economics"></div></header>
<div class="demo" id="demo" hidden></div>
<div class="layout">
<nav class="left" aria-label="Conversation">
 <div class="panel"><h2>Conversation</h2>
  <ul class="thread" id="thread"></ul>
  <p class="hint" id="threadhint">Follow-ups like “now by practice”, “only Medicare”, “what about 2029?”, “list them” or “why?” build on the previous answer.</p>
  <div class="tools"><button class="btn small" id="newconv" type="button">New conversation</button></div>
 </div>
</nav>
<main class="center" id="main">
 <div class="panel">
  <form class="askf" id="askf"><label class="sr" for="q">Ask a question</label>
   <input id="q" placeholder="Ask about the population, care, cost or data" autocomplete="off"><button class="btn primary" id="askb">Ask</button></form>
  <div class="chips" id="examples" aria-label="Example questions"></div>
 </div>
 <div id="demonote"></div>
 <div id="answer" class="panel answer" aria-live="polite"><p class="muted" style="margin:0">Ask a question, pick a briefing card, or start the demo with <a href="?demo=1">?demo=1</a>.</p></div>
</main>
<aside class="right" id="right" aria-label="Briefing">
 <div class="panel"><h2>Briefing</h2><p class="hint" style="margin:0 0 8px">Ranked findings computed locally, without a question. Select one to ask its follow-up.</p>
  <div class="brief" id="brief">Loading…</div></div>
</aside>
</div>
<script id="demo-script" type="application/json">__DEMO_STEPS__</script>
<script>
"use strict";
const DEMO=JSON.parse(document.getElementById('demo-script').textContent);
const EXAMPLES=["What is our allowed PMPM by payer?","Which CKD patients have a declining eGFR?","Are any of our rates out of control?",
 "who should our care managers call this week? we have 40 slots","Did care management reduce ED visits?","What does the readmission curve look like by payer?"];
const ROUTES={local_compiler:'Agere compiler',local_unsupported:'Agere compiler (unsupported)',local_model_rewrite_compiled:'Local rewrite → compiler',
 hosted_verified_sql:'Hosted models, cross-verified',learned_verified_sql:'Learned (previously verified)',hosted_missing_data:'Hosted: data not available',frontier_escalation:'Frontier model'};
const $=(s,el)=>(el||document).querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const isNum=v=>typeof v==='number'&&isFinite(v);
const fmt=(v,d)=>{if(!isNum(v))return v==null?'—':String(v);const a=Math.abs(v);d=d==null?(a>=1000?0:a>=10?1:a>=1?2:3):d;return v.toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:0})};
const usdK=v=>!isNum(v)?'—':Math.abs(v)>=1e6?(v<0?'−':'')+'$'+fmt(Math.abs(v)/1e6,1)+'M':Math.abs(v)>=1e3?(v<0?'−':'')+'$'+fmt(Math.abs(v)/1e3,0)+'k':(v<0?'−$':'$')+fmt(Math.abs(v),0);
const usd=v=>isNum(v)?(v<0?'−$':'$')+Math.abs(v).toLocaleString(undefined,{maximumFractionDigits:0}):'—';
const pct=(v,d)=>isNum(v)?(100*v).toFixed(d==null?1:d)+'%':'—';
const label=k=>String(k).replace(/_/g,' ');
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
function cols(rows,skip){const seen=[];rows.slice(0,50).forEach(r=>Object.keys(r||{}).forEach(k=>{if(!seen.includes(k)&&!(skip||[]).includes(k))seen.push(k)}));return seen}
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
const GROUP_KEYS=['group','segment','practice','pcp','payer','coverage_type','facility','age_band','sex','label','factor','month','quarter','year'];
const METRICS=['allowed_pmpm_completed','oe_ratio','ed_per_1000_shrunk','shrunk_rate','rate','readmission_rate','risk_ratio','probability','mean_risk_score','value','change','members','patient_count','patients','total_cents','count'];
function pickMetric(rows){const f=rows[0]||{};const m=METRICS.find(k=>isNum(f[k]));if(m)return m;
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
function vizTrajectory(a){const rows=a.table||[];if(!rows.length||!('patient_id' in rows[0]))return null;const metric=['probability','change','slope_per_year'].find(k=>isNum(rows[0][k]));
 let h='<section class="sec"><div class="kpis"><div class="kpi"><div class="l">Patients flagged</div><div class="v">'+fmt(a.count!=null?a.count:rows.length)+'</div></div></div>';
 if(metric){const top=rows.slice().sort((x,y)=>Math.abs(y[metric])-Math.abs(x[metric])).slice(0,15);h+='<h3 style="margin-top:12px">Largest '+esc(label(metric))+' (top '+top.length+')</h3>'+
  hbars(top.map(r=>({label:r.patient_id,value:r[metric]})),{fmt:v=>metric==='probability'?pct(v,0):fmt(v),title:label(metric)+' by patient'})}
 return h+tableHTML(rows,{name:'trajectory-patients'})+'</section>'}
function vizTable(a){const rows=a.table||[];if(!rows.length)return '';const f=rows[0];const key=GROUP_KEYS.find(k=>k in f&&typeof f[k]!=='object')||Object.keys(f).find(k=>typeof f[k]==='string');
 const metric=pickMetric(rows);let h='<section class="sec">';
 if(key&&metric&&rows.length>1&&rows.length<=60){const drillable=['group','segment','practice','pcp','payer','coverage_type','facility','month','year','quarter'].includes(key);
  if(rows.every(r=>isTime(r[key]))){const cats=rows.map(r=>String(r[key]));h+='<h3>'+esc(label(metric))+' by '+esc(label(key))+'</h3>'+lines([{name:label(metric),pts:rows.map((r,i)=>[i,r[metric]])}],{title:label(metric)+' over time',cats,ylabel:label(metric)})}
  else{const sorted=rows.slice().sort((x,y)=>(y[metric]||0)-(x[metric]||0));h+='<h3>'+esc(label(metric))+' by '+esc(label(key))+'</h3>'+hbars(sorted.slice(0,30).map(r=>({label:r[key],value:r[metric],ask:drillable?drill(r[key]):null})),{title:label(metric)+' by '+label(key),ref:/ratio/.test(metric)?1:null,refLabel:'no difference (1.0)'})+
   (drillable?'<p class="hint">Select a bar to ask a follow-up about that '+esc(label(key))+'.</p>':'')}}
 h+=tableHTML(rows,{name:'agere-answer',ask:key&&['group','segment','practice','pcp','payer','coverage_type','facility'].includes(key)?(r=>drill(r[key])):null});
 return h+'</section>'}
function vizScalar(a){if(a.answer_type==='count'||a.answer_type==='value'){const v=a.answer_type==='count'?a.count:a.value;if(v==null||typeof v==='string')return '';
  return '<section class="sec"><div class="big">'+esc(fmt(v))+(a.unit?esc(a.unit):'')+'</div><div class="muted">'+(a.answer_type==='count'?'count':'value')+(a.unknown_count?' · '+fmt(a.unknown_count)+' patients could not be evaluated':'')+'</div></section>'}
 if(a.answer_type==='patient_set'){const ids=a.patient_ids||[];const rows=ids.map(id=>({patient_id:id}));
  return '<section class="sec"><div class="big">'+fmt(a.count!=null?a.count:ids.length)+'</div><div class="muted">patients'+(a.unknown_count?' · '+fmt(a.unknown_count)+' could not be evaluated':'')+'</div>'+
   (ids.length?'<details open><summary>'+ids.length+' patient IDs</summary><div class="pids">'+ids.slice(0,300).map(i=>'<span>'+esc(i)+'</span>').join('')+'</div>'+(ids.length>300?'<p class="hint">First 300 shown; the CSV has all.</p>':'')+csvButton(rows,'patients')+'</details>':'')+'</section>'}
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
function visual(a){const v=visual0(a);return v==null?v:v+calNote(a)}
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
function visual0(a){if(!a||a.answer_type==='unsupported')return '';const o=a.overall||{};
  if(o.contract_strategy)return vizContract(a);
  if(o.knowledge)return vizKnowledge(a);
  if(o.early_signal)return vizEarlySignal(a);
  if(o.claims_review)return vizClaims(a);
  if(o.risk_adjustment)return vizRisk(a);
  if(o.profile)return vizProfile(a);
  if(o.scheduling)return vizScheduling(a);
  if(o.provider_network)return vizNetwork(a);
  if(o.matrix&&o.states)return vizPathway(a);
 if(o.network&&o.data_gaps)return vizMarket(a);
 if(o.briefing)return vizBriefing(a);
 if(o.demand)return vizDemand(a);if(o.curves)return vizSurvival(a);if(o.charts||(o.tests_examined!=null&&o.look_elsewhere_bits!=null))return vizMonitor(a);if(o.did)return vizEffect(a);if(o.portfolio&&o.by_group)return vizOutreach(a);
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
function badges(t){const d=t.data||{},a=d.answer||{},pr=d.planner_receipt||{},econ=d.planning_economics||{};const st=t.error?'error':(a.status||'');
 const b=['<span class="badge '+esc(st)+'">'+esc(label(st||'unknown'))+'</span>',
  '<span class="badge">Route: '+esc(ROUTES[pr.route]||pr.route||d.route||'—')+'</span>','<span class="badge">Paid API calls: '+fmt(d.new_paid_api_calls||0)+'</span>',
  '<span class="badge">Cost: $'+(econ.known_api_estimate_usd||0).toFixed(4)+'</span>','<span class="badge">Latency: '+(t.seconds!=null?t.seconds.toFixed(2)+' s':'—')+'</span>'];
 if(d.cache_hit)b.push('<span class="badge">Reused cached result</span>');return '<div class="badges">'+b.join('')+'</div>'}
function resolvedLine(t){const c=(t.data||{}).conversation;const r=c?c.resolved_question:t.resolved;if(!r||r===t.question)return '';
 return '<div class="resolved">Answered as: <b>'+esc(r)+'</b>'+(c&&c.method==='hosted'?' (follow-up rewritten by a hosted model, then planned normally)':'')+'</div>'}
function headlineHTML(a){let hl=String(a.headline||a.status||'');const cut=hl.indexOf(' Warnings (');if(cut>0&&(a.warnings||[]).length)hl=hl.slice(0,cut);
 return '<h2 class="headline'+(hl.length>200?' long':'')+'">'+esc(hl)+'</h2>'}
function warnBox(a){const w=(a.warnings||[]),m=(a.missing||[]);let h='';
 if(w.length)h+='<div class="alert warn" role="note"><h3>Warnings ('+w.length+')</h3><ul>'+w.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></div>';
 if(m.length)h+='<div class="alert '+(a.status==='unsupported'?'bad':'warn')+'" role="note"><h3>Not covered</h3><ul>'+m.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></div>';return h}
function renderTurn(i){const t=state.turns[i];const box=$('#answer');state.active=i;renderThread();if(!t){box.innerHTML='';return}
 if(t.pending){box.innerHTML='<div class="q">'+esc(t.question)+'</div><p class="skeleton">Planning and computing…</p>';return}
 if(t.error){box.innerHTML='<div class="q">'+esc(t.question)+'</div>'+resolvedLine(t)+'<div class="headline badc">Not answered</div>'+badges(t)+'<div class="alert bad">'+esc(t.error)+'</div>'+
   '<p class="hint">Rephrase as a complete question, or start a new conversation.</p>';return}
 if(!t.data){box.innerHTML='<div class="q">'+esc(t.question)+'</div>'+resolvedLine(t)+'<div class="headline">'+esc(t.summary||'')+'</div><p class="hint">Earlier turn: ask it again to see the full answer.</p>'+
   '<div class="tools"><button type="button" class="btn small" data-ask-standalone="'+esc(t.resolved||t.question)+'">Ask again</button></div>';return}
 const a=t.data.answer||{};state.csv={};
 let h='<div class="q">'+esc(t.question)+'</div>'+resolvedLine(t)+headlineHTML(a)+badges(t)+warnBox(a);
 try{h+=visual(a)}catch(err){h+='<div class="alert warn">Visual unavailable: '+esc(err.message)+'</div>'+tableHTML(a.table||[],{name:'agere-answer'})}
 const sg=suggestions(a);if(sg.length)h+='<div class="chips" aria-label="Follow-ups">'+sg.map(s=>'<button type="button" class="chip" data-ask="'+esc(s)+'">'+esc(s)+'</button>').join('')+'</div>';
 h+=details(a);
 h+='<div class="sec flag" data-flag><button type="button" class="btn small" data-flagopen="1" style="color:var(--bad)">Flag as wrong</button></div>';
 box.innerHTML=h;if(t.focus){const el=box.querySelector('[data-focus="'+t.focus+'"]');if(el)el.scrollIntoView({behavior:'smooth',block:'start'})}}
function renderThread(){const ul=$('#thread');ul.innerHTML=state.turns.map((t,i)=>{const a=(t.data||{}).answer||{};const st=t.pending?'pending':t.error?'error':(a.status||t.status||'');
 const r=((t.data||{}).conversation||{}).resolved_question||t.resolved;
 return '<li><button type="button" data-turn="'+i+'"'+(i===state.active?' aria-current="true"':'')+'><div class="tq"><span class="dot '+esc(st)+'" aria-hidden="true"></span>'+esc(t.question)+'</div>'+
  (r&&r!==t.question?'<div class="ta">→ '+esc(r)+'</div>':'')+'<div class="ta">'+esc(t.pending?'working…':t.error?'not answered':String(a.headline||t.summary||'').slice(0,110))+'</div></button></li>'}).join('')||'<li class="hint">No questions yet.</li>';
 $('#threadhint').hidden=state.turns.length>0}

/* ---------- asking ---------- */
async function ask(question,opt){opt=opt||{};question=String(question||'').trim();if(!question||state.busy)return null;state.busy=true;$('#askb').disabled=true;
 if(!state.cid){state.cid=newId();store.set('agere-conversation',state.cid)}
 const t={question,pending:true,focus:opt.focus};state.turns.push(t);renderTurn(state.turns.length-1);const top=$('#answer').getBoundingClientRect().top;if(top<0||top>window.innerHeight-80)$('#answer').scrollIntoView({block:'start'});const t0=performance.now();
 try{const r=await fetch('/api/stack/compose',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({question,conversation_id:state.cid})});
  let d;try{d=await r.json()}catch(_){d={error:'HTTP '+r.status}}t.seconds=(performance.now()-t0)/1000;t.pending=false;
  if(d.error||!r.ok){t.error=d.error||('HTTP '+r.status)}else{t.data=d;t.resolved=(d.conversation||{}).resolved_question}}
 catch(err){t.pending=false;t.error=String(err)}finally{state.busy=false;$('#askb').disabled=false}
 state.outFilter=null;renderTurn(state.turns.indexOf(t));loadEcon();return t}
function loadConversation(){if(!state.cid)return;fetch('/api/stack/conversation?id='+encodeURIComponent(state.cid)).then(r=>r.ok?r.json():null).then(d=>{if(!d||state.turns.length)return;
 state.turns=(d.turns||[]).map(x=>({question:x.question,resolved:x.resolved_question,error:x.error||null,summary:(x.answer_summary||{}).headline||'',status:(x.answer_summary||{}).status}));
 if(state.turns.length)renderTurn(state.turns.length-1)}).catch(()=>{})}
function newConversation(){state.cid=null;store.set('agere-conversation',null);state.turns=[];state.active=-1;renderThread();
 $('#answer').innerHTML='<p class="muted" style="margin:0">New conversation. Ask a question or pick a briefing card.</p>';$('#q').focus()}
function loadEcon(){fetch('/api/stack/economics').then(r=>r.ok?r.json():null).then(e=>{if(!e)return;
 $('#econ').innerHTML='<span class="pill" title="Questions answered by this installation"><b>'+fmt(e.questions||0)+'</b> questions</span>'+
  '<span class="pill" title="Answered without any paid model call"><b>'+(e.share_without_api_calls!=null?Math.round(100*e.share_without_api_calls)+'%':'—')+'</b> without paid calls</span>'+
  '<span class="pill" title="Known hosted model spend"><b>$'+(e.api_usd||0).toFixed(2)+'</b> spend</span><span id="loop"></span>';loadLoop()}).catch(()=>{})}
function loadLoop(){fetch('/api/stack/learning-loop').then(r=>r.ok?r.json():null).then(l=>{if(!l||l.status!=='ok'||!$('#loop'))return;const g=l.gaps||{},rt=l.retrain||{};
 $('#loop').innerHTML='<span class="pill" title="'+esc('Learning loop: '+(l.detail||'')+(rt.reason?'. Retrain: '+rt.reason:''))+'"><b>'+fmt(g.actionable_clusters||0)+'</b> gap clusters · <b>'+fmt((l.regressions||{}).cases||0)+'</b> regressions · <b>'+fmt((l.proposals||{}).count||0)+'</b> proposals'+(rt.recommend_retrain?' · retrain due':'')+'</span>'}).catch(()=>{})}
function briefFeedback(card,event){if(!card)return;const body={card_id:card,event};if(state.briefingId)body.briefing_id=state.briefingId;
 fetch('/api/stack/briefing-feedback',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}).catch(()=>{})}
function loadBriefing(){return fetch('/api/stack/briefing').then(r=>r.ok?r.json():Promise.reject(r.status)).then(d=>{const cards=(d.cards||[]).slice();state.briefing=cards;state.briefingId=d.briefing_id||null;
 $('#brief').innerHTML=cards.slice(0,10).map((c,i)=>'<div class="bcard"><button type="button" data-ask-standalone="'+esc(c.follow_up)+'" data-brief-card="'+esc(c.card_id||'')+'"><div class="k">#'+(i+1)+(c.explore?' · <span class="xp" title="Explore slot: shown so the ranker can learn">explore</span>':'')+' · '+esc(label(c.kind))+' · '+esc(label(c.topic||''))+(c.patients?' · '+fmt(c.patients)+' patients':'')+'</div>'+
  '<div class="h">'+esc(c.headline)+'</div><div class="f">Ask: '+esc(c.follow_up)+' →</div></button>'+
  (c.why_text?'<details data-brief-open="'+esc(c.card_id||'')+'"><summary>Why #'+(i+1)+'</summary>'+esc(c.why_text)+'</details>':'')+
  (c.card_id?'<button type="button" class="x" data-brief-dismiss="'+esc(c.card_id)+'" aria-label="Dismiss this card" title="Not useful">×</button>':'')+'</div>').join('')||'<p class="hint">No significant findings.</p>';
 $('#brief').querySelectorAll('details[data-brief-open]').forEach(x=>x.addEventListener('toggle',()=>{if(x.open&&!x.dataset.sent){x.dataset.sent='1';briefFeedback(x.dataset.briefOpen,'opened')}}));return cards})
 .catch(()=>{$('#brief').innerHTML='<p class="hint">Briefing unavailable.</p>';return []})}

/* ---------- demo ---------- */
const demo={on:/[?&]demo=1\b/.test(location.search),i:0,done:new Set()};
function renderDemo(){if(!demo.on)return;const el=$('#demo');el.hidden=false;
 el.innerHTML='<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center"><b>Buyer walkthrough</b><span class="muted" style="font-size:13px">Every step asks a real question.</span>'+
  '<button type="button" class="btn primary small" id="demonext"'+(demo.i>=DEMO.length?' disabled':'')+'>'+(demo.i>=DEMO.length?'Walkthrough complete':'Run step '+(demo.i+1)+': '+esc(DEMO[demo.i].label))+'</button>'+
  '<button type="button" class="btn small" id="demoreset">Restart</button></div><ol>'+DEMO.map((s,i)=>'<li><button type="button" data-demo="'+i+'"'+(i===demo.i?' aria-current="step"':'')+(demo.done.has(i)?' class="done"':'')+'>'+(i+1)+'. '+esc(s.label)+(demo.done.has(i)?' ✓':'')+'</button></li>').join('')+'</ol>'}
async function runStep(i){const s=DEMO[i];if(!s||state.busy)return;if(i===0){newConversation()}demo.i=i;renderDemo();
 $('#demonote').innerHTML='<div class="demonote"><b>Step '+(i+1)+' of '+DEMO.length+' · '+esc(s.label)+'.</b> '+esc(s.note||'')+'</div>';
 let q=s.question;if(s.kind==='briefing'){const cards=state.briefing||await loadBriefing();const r=$('#right');r.classList.add('flash');r.scrollIntoView({behavior:'smooth',block:'nearest'});setTimeout(()=>r.classList.remove('flash'),2200);
  q=cards.length?cards[0].follow_up:null;if(!q){demo.done.add(i);demo.i=i+1;renderDemo();return}}
 await ask(q,{focus:s.focus});demo.done.add(i);demo.i=i+1;renderDemo()}

/* ---------- events (delegated; buttons and role=button elements are keyboard reachable) ---------- */
function activate(el){if(el.dataset.csv){downloadCSV(el.dataset.csv);return}
 if(el.dataset.briefDismiss){briefFeedback(el.dataset.briefDismiss,'dismissed');const b=el.closest('.bcard');if(b)b.remove();return}
 if(el.dataset.askStandalone!=null){if(el.dataset.briefCard)briefFeedback(el.dataset.briefCard,'asked_follow_up');ask(el.dataset.askStandalone);return}
 if(el.dataset.ask!=null){ask(el.dataset.ask);return}
 if(el.dataset.turn!=null){renderTurn(+el.dataset.turn);return}
 if(el.dataset.demo!=null){runStep(+el.dataset.demo);return}
 if(el.id==='demonext'){runStep(demo.i);return}if(el.id==='demoreset'){demo.i=0;demo.done.clear();renderDemo();return}
 if(el.dataset.clearfilter){state.outFilter=null;refreshWorklist();return}
 if(el.dataset.series!=null){const key=el.closest('[data-legend]').dataset.legend,st=state[key];if(!st)return;const s=st.series[+el.dataset.series];s.hidden=!s.hidden;el.setAttribute('aria-pressed',String(!s.hidden));
  const box=document.querySelector('[data-chart="'+key+'"]');if(box)box.innerHTML=st.draw?st.draw(st):survChart(st);return}
 if(el.dataset.pick!=null){const mon=el.closest('#answer')&&document.querySelector('[data-monsel]');
  if(mon&&el.tagName==='TR'){if(+el.dataset.pick>=0){mon.value=el.dataset.pick;mon.dispatchEvent(new Event('change',{bubbles:true}));mon.scrollIntoView({behavior:'smooth',block:'center'})}return}
  state.outFilter=el.dataset.pick;refreshWorklist();return}
 if(el.dataset.flagopen){const box=el.closest('[data-flag]');box.innerHTML='<label for="flagnote"><b>What is wrong with this answer?</b></label><textarea id="flagnote"></textarea><div class="tools"><button type="button" class="btn small primary" data-flagsend="1">Record correction</button><button type="button" class="btn small" data-flagcancel="1">Cancel</button></div>';$('#flagnote').focus();return}
 if(el.dataset.flagcancel){el.closest('[data-flag]').innerHTML='<button type="button" class="btn small" data-flagopen="1" style="color:var(--bad)">Flag as wrong</button>';return}
 if(el.dataset.flagsend){const box=el.closest('[data-flag]'),note=$('#flagnote').value.trim();if(!note){$('#flagnote').focus();return}const t=state.turns[state.active]||{};
  const question=((t.data||{}).conversation||{}).resolved_question||t.question;el.disabled=true;
  fetch('/api/stack/correct',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({question,note})}).then(r=>r.json()).then(d=>{
   box.innerHTML='<p class="'+(d.error?'badc':'goodc')+'" role="status">'+esc(d.error||'Recorded: this answer will not be reused and becomes a regression case.')+'</p>'}).catch(e=>{box.innerHTML='<p class="badc">'+esc(e)+'</p>'})}}
function refreshWorklist(){const t=state.turns[state.active];const box=document.querySelector('[data-worklist]');if(t&&t.data&&box){box.innerHTML=worklist(t.data.answer);box.scrollIntoView({behavior:'smooth',block:'nearest'})}}
const ACT='[data-csv],[data-ask],[data-ask-standalone],[data-turn],[data-demo],#demonext,#demoreset,[data-clearfilter],[data-series],[data-pick],[data-flagopen],[data-flagcancel],[data-flagsend],[data-brief-dismiss]';
document.addEventListener('click',e=>{const el=e.target.closest(ACT);if(el){e.preventDefault();activate(el)}});
document.addEventListener('keydown',e=>{if(e.key!=='Enter'&&e.key!==' ')return;const el=e.target.closest(ACT);if(!el||el.tagName==='BUTTON')return;if(e.target.tagName==='TEXTAREA'||e.target.tagName==='INPUT')return;e.preventDefault();activate(el)});
document.addEventListener('change',e=>{const d=e.target.closest('[data-demsel]');if(!d)return;const st=state[d.dataset.demsel];const box=document.querySelector('[data-chart="'+d.dataset.demsel+'"]');if(st&&box)box.innerHTML=fanChart(st.series[+d.value],st.o)});
document.addEventListener('change',e=>{const s=e.target.closest('[data-monsel]');if(!s)return;const st=state[s.dataset.monsel];const box=document.querySelector('[data-chart="'+s.dataset.monsel+'"]');if(st&&box)box.innerHTML=controlChart(st.charts[+s.value])});
$('#askf').addEventListener('submit',e=>{e.preventDefault();const v=$('#q').value;$('#q').value='';ask(v)});
$('#newconv').addEventListener('click',newConversation);
$('#examples').innerHTML=EXAMPLES.map(x=>'<button type="button" class="chip" data-ask-standalone="'+esc(x)+'">'+esc(x)+'</button>').join('');
let rz,lastW=window.innerWidth;window.addEventListener('resize',()=>{if(window.innerWidth===lastW)return;lastW=window.innerWidth;clearTimeout(rz);rz=setTimeout(()=>{const t=state.turns[state.active];if(t&&t.data){const f=t.focus;t.focus=null;renderTurn(state.active);t.focus=f}},250)});
renderThread();loadEcon();loadBriefing();loadConversation();renderDemo();
</script></body></html>"""


def page():
    return PAGE.replace('__DEMO_STEPS__', json.dumps(DEMO_STEPS).replace('</', '<\\/'))
