"""Offline, source-grounded review of a saved integrated synthetic stack run."""
import json
from pathlib import Path


def write_report(run, path):
    if run.get("schema_version") != "agere-integrated-stack/1" or run.get("synthetic") is not True:
        raise ValueError("Expected integrated synthetic run")
    data = json.dumps(run, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c")
    html = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agere · Evidence to action</title>
<style>
:root{color-scheme:dark;--bg:#101917;--panel:#18241f;--line:#33453b;--text:#eff4e9;--muted:#b2c2b5;--accent:#cff39b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,sans-serif}
header,main{max-width:1500px;margin:auto;padding:32px}header{border-bottom:1px solid var(--line)}
.brand{font-size:15px;letter-spacing:.18em;color:var(--accent)}h1{font:400 clamp(32px,4vw,58px)/1.12 Georgia,serif;margin:22px 0 14px;max-width:880px}
h2{font-size:20px;font-weight:500}h3{font-size:16px;margin-bottom:6px}p{max-width:900px}.muted,small{color:var(--muted)}
.metrics,.stages{display:flex;flex-wrap:wrap;gap:12px;margin:24px 0}.metric,.stage{padding:15px 20px;background:var(--panel);border:1px solid var(--line);border-radius:8px}.metric strong{display:block;font-size:27px;font-weight:500;color:var(--accent)}
.layout{display:grid;grid-template-columns:minmax(280px,380px) minmax(0,1fr);gap:24px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:22px}
input,select,button{font:inherit;color:var(--text);background:var(--bg);border:1px solid var(--line);padding:10px;border-radius:6px}input,select{width:100%;margin:5px 0}button{cursor:pointer}button:hover,button:focus-visible{border-color:var(--accent)}
.case{display:block;text-align:left;width:100%;margin:8px 0;padding:13px}.case[aria-pressed=true]{border-color:var(--accent);background:#263629}.case span{display:block;font-size:13px;color:var(--muted)}
.pill{display:inline-block;padding:3px 9px;border:1px solid var(--line);border-radius:30px;font-size:12px;margin-right:8px;color:var(--accent)}
.evidence{border-left:2px solid var(--accent);padding:12px 18px;margin:16px 0;background:var(--bg);overflow-wrap:anywhere}blockquote{margin:8px 0;white-space:pre-wrap}.actions{padding:16px;background:#25342b;border-radius:6px}
details{margin:15px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:450px;overflow:auto}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid var(--line);padding:12px;vertical-align:top}th{color:var(--muted);font-size:13px}.scroll{overflow:auto}.count{color:var(--accent)}
@media(max-width:850px){header,main{padding:20px}.layout{grid-template-columns:1fr}.metrics{gap:8px}.metric{flex:1;padding:12px}.cases{max-height:330px;overflow:auto}}
</style><header><div class="brand">AGERE / CONNECTED STACK</div>
<h1>Every insight starts with<br>evidence you can follow.</h1>
<p class="muted">Synthetic research workspace. Inspect what the records establish, what remains unknown, and which next step could resolve it. No automatic treatment decisions or outreach.</p>
<div id="metrics" class="metrics"></div><div id="stages" class="stages"></div></header>
<main><div class="layout"><aside class="panel"><h2>Evidence review</h2>
<label for="search">Patient or practice</label><input id="search" placeholder="Filter cases…">
<label for="state">Evidence state</label><select id="state"><option value="">All states</option></select>
<p id="count" class="muted"></p><div id="cases" class="cases"></div><button id="more">Show more</button></aside>
<section id="detail" class="panel" aria-live="polite"></section></div>
<section class="panel" style="margin-top:24px"><h2>Population review opportunities</h2><p class="muted">Counts describe the supplied synthetic cohort. They do not establish care gaps, causality or provider performance.</p><div class="scroll"><table><thead><tr><th>Practice / subject</th><th>Finding</th><th>Cases / assessed</th><th>Next step</th></tr></thead><tbody id="cards"></tbody></table></div></section>
<section class="panel" style="margin-top:24px"><h2>What is connected—and what is still research</h2><div class="scroll"><table><thead><tr><th>Component</th><th>Status</th><th>Purpose</th></tr></thead><tbody id="components"></tbody></table></div><details><summary>Source intake and compute receipt</summary><pre id="receipt"></pre></details></section>
</main><script id="data" type="application/json">__DATA__</script><script>
const data=JSON.parse(document.getElementById('data').textContent);let selected=null,limit=30;
const $=id=>document.getElementById(id), label=s=>String(s).replaceAll('_',' ');
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
for(const [name,value] of [['Patients',data.summary.patients],['Evidence assessments',data.summary.patient_subject_pairs],['Dependency cache hits',data.compute.dependency_cache_hits],['External model calls',data.compute.external_model_calls]]){const n=el('div',undefined,'metric');n.append(el('strong',Number(value).toLocaleString()),el('span',name));$('metrics').append(n)}
for(const name of ['Versioned sources','Evidence + observability','Bounded reasoning','Review + learning'])$('stages').append(el('span',name,'stage'));
for(const state of Object.keys(data.summary.evidence_states)){const o=el('option',label(state));o.value=state;$('state').append(o)}
function render(){const q=$('search').value.toLowerCase(),s=$('state').value;const rows=data.cases.filter(c=>(!s||c.state===s)&&[c.patient_id,c.practice].some(x=>x.toLowerCase().includes(q)));$('count').textContent=`${rows.length} cases · showing ${Math.min(limit,rows.length)}`;$('cases').replaceChildren();for(const c of rows.slice(0,limit)){const b=el('button',c.patient_id+' · '+c.subject,'case');b.setAttribute('aria-pressed',String(c.case_id===selected));b.append(el('span',c.practice+' / '+label(c.state)));b.onclick=()=>{show(c);render()};$('cases').append(b)}$('more').hidden=rows.length<=limit}
function show(c){selected=c.case_id;const d=$('detail');d.replaceChildren(el('span','SYNTHETIC PATIENT','pill'),el('span',label(c.state),'pill'),el('h2',c.patient_id+' · '+c.subject),el('p',c.practice+' · evidence cutoff '+c.as_of,'muted'));
const action=el('div',undefined,'actions');action.append(el('h3','Next useful step'),el('p',c.next_action),el('p',label(c.route.operation)+': '+c.route.reason));d.append(action);
d.append(el('h3','What remains unresolved'));for(const u of c.unknowns)d.append(el('p',u,'muted'));
if(c.documents.length){d.append(el('h3','Linked documents'));for(const doc of c.documents)d.append(el('p',`${doc.record_id}: ${doc.processing_status} · ${doc.pages} extracted pages · arrival ${doc.available_date||'unknown'}`))}
d.append(el('h3','Evidence and original sources'));if(!c.evidence.length)d.append(el('p','No supporting assertion was extracted for this subject. This does not establish that care was absent.','muted'));
for(const a of c.evidence){const n=el('div',undefined,'evidence');n.append(el('strong',label(a.kind)),el('blockquote',a.reference.quote||JSON.stringify(a.detail)),el('small',`${a.source_origin.source_system} / ${a.source_origin.record_id}${a.source_origin.page?' / page '+a.source_origin.page:''} · event ${a.event_date} · arrival ${a.available_date||'unknown'}`));const detail=el('details');detail.append(el('summary','Exact reference and source revision'),el('pre',JSON.stringify({reference:a.reference,origin:a.source_origin,assertion_id:a.assertion_id},null,2)));n.append(detail);d.append(n)}
const decision=el('details');decision.append(el('summary','Conditional reasoning and missing information'),el('pre',JSON.stringify({analysis:c.conditional_analysis,information_needs:c.information_needs,lineage:c.lineage},null,2)));d.append(decision)}
$('search').oninput=$('state').onchange=()=>{limit=30;render()};$('more').onclick=()=>{limit+=30;render()};
for(const c of data.insight_cards.slice(0,60)){const r=el('tr');r.append(el('td',c.practice+' / '+c.subject),el('td',label(c.finding)),el('td',c.patient_subject_pairs+' / '+c.denominator,'count'),el('td',c.next_action));$('cards').append(r)}
for(const c of data.capabilities){const r=el('tr');r.append(el('td',c.component),el('td',label(c.status)),el('td',c.purpose));$('components').append(r)}
$('receipt').textContent=JSON.stringify({run_id:data.run_id,namespace:data.namespace,compute:data.compute,intake:data.intake},null,2);if(data.cases.length)show(data.cases[0]);else $('detail').append(el('p','No eligible synthetic evidence assessments in this snapshot.'));render();
</script></html>'''
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html.replace("__DATA__", data))
