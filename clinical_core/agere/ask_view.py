"""Plain question console: ask Agere in English, see the answer, how it was computed and what it cost."""

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agere · Ask</title>
<style>
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#57534e;--line:#e7e5e4;--card:#fff;--accent:#0f766e;--warn:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#0c0a09;--fg:#f5f5f4;--muted:#a8a29e;--line:#292524;--card:#1c1917;--accent:#2dd4bf;--warn:#fbbf24}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 16px 64px}h1{font-size:20px;margin:0 0 4px}p.sub{color:var(--muted);margin:0 0 18px}
form{display:flex;gap:8px}input{flex:1;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg);font-size:15px}
button{padding:12px 18px;border:0;border-radius:10px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer}button:disabled{opacity:.5}
.examples{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 22px}.examples a{font-size:13px;color:var(--accent);border:1px solid var(--line);border-radius:999px;padding:4px 10px;cursor:pointer;text-decoration:none}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-top:14px}
.headline{font-size:17px;font-weight:600}.meta{color:var(--muted);font-size:13px;margin-top:6px}.meta b{color:var(--fg)}
.warn{color:var(--warn)}table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px;display:block;overflow-x:auto}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--muted);font-weight:600}
details{margin-top:10px}summary{cursor:pointer;color:var(--muted);font-size:13px}pre{white-space:pre-wrap;font-size:12px;color:var(--muted)}
</style></head><body><main>
<h1>Agere</h1><p class="sub">Ask about the population, care, cost or data. Agere plans the question itself; a hosted model is used only when it must be.</p>
<p class="meta" id="econ"></p><form id="f"><input id="q" placeholder="e.g. Which CKD patients have a declining eGFR?" autocomplete="off"><button id="b">Ask</button></form>
<div class="examples" id="ex"></div><div id="out"></div>
<h1 style="margin-top:28px">Briefing</h1><p class="sub">What stands out in the data right now, computed locally without a question.</p><div id="brief" class="meta">Loading…</div>
<script>
const EX=["How many patients have diabetes with A1c over 9?","Which CKD patients have a declining eGFR?","What is our allowed PMPM by payer?",
"Which PCPs have the highest emergency department utilization, adjusted for case mix?","Why did ED visits go up from 2027 to 2028?",
"Where is heart failure concentrated?","What drives emergency department visits in our population?","What is the A1c poor control rate by practice?"];
const ex=document.getElementById('ex');EX.forEach(t=>{const a=document.createElement('a');a.textContent=t;a.onclick=()=>{q.value=t;ask()};ex.appendChild(a)});
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function table(rows){if(!rows||!rows.length)return'';const cols=Object.keys(rows[0]).filter(k=>!['readmissions','patients_with_outcome','pids'].includes(k));
 return '<table><tr>'+cols.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr>'+rows.slice(0,50).map(r=>'<tr>'+cols.map(c=>'<td>'+esc(typeof r[c]==='object'?JSON.stringify(r[c]):r[c])+'</td>').join('')+'</tr>').join('')+'</table>'}
let ask=async function(e){if(e)e.preventDefault();const question=q.value.trim();if(!question)return;b.disabled=true;
 const out=document.getElementById('out');out.innerHTML='<div class="card meta">Working…</div>';const t0=performance.now();
 try{const r=await fetch('/api/stack/compose',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({question})});const d=await r.json();
  if(d.error){out.innerHTML='<div class="card"><div class="headline warn">Not answered locally</div><div class="meta">'+esc(d.error)+'</div></div>';return}
  const a=d.answer||{},pr=d.planner_receipt||{},econ=d.planning_economics||{};
  const route={local_compiler:'Agere compiler',local_unsupported:'Agere compiler (unsupported)',local_model_rewrite_compiled:'Local model rewrite → Agere compiler',hosted_verified_sql:'Hosted models, cross-verified',frontier_escalation:'Frontier model'}[pr.route]||pr.route;
  let html='<div class="card"><div class="headline">'+esc(a.headline||a.status)+'</div><div class="meta">Route <b>'+esc(route)+'</b> · API calls <b>'+(d.new_paid_api_calls||0)+'</b> · cost <b>$'+(econ.known_api_estimate_usd||0).toFixed(4)+'</b> · '+((performance.now()-t0)/1000).toFixed(2)+' s'+(pr.rewrite?' · rewrite: “'+esc(pr.rewrite)+'”':'')+'</div>';
  if(a.patient_ids&&a.patient_ids.length&&!a.table)html+='<details><summary>'+a.patient_ids.length+' patient IDs</summary><pre>'+esc(a.patient_ids.join(', '))+'</pre></details>';
  html+=table(a.table);
  if(a.concepts&&a.concepts.length)html+='<details><summary>Definitions</summary><pre>'+esc(JSON.stringify(a.concepts,null,1))+'</pre></details>';
  if(a.standards&&a.standards.length)html+='<details><summary>Standards</summary><pre>'+esc(a.standards.map(x=>x.definition+': '+x.standard+' ('+x.alignment+')\n  Agere: '+x.agere_default+'\n  '+x.sources.join('\n  ')).join('\n\n'))+'</pre></details>';
  if(a.method)html+='<details><summary>Method</summary><pre>'+esc(a.method)+'</pre></details>';
  if(a.query)html+='<details><summary>Query</summary><pre>'+esc(a.query)+'</pre></details>';
  if(a.missing&&a.missing.length)html+='<details open><summary>Not covered</summary><pre>'+esc(a.missing.join('\n'))+'</pre></details>';
  out.innerHTML=html+'</div>'}catch(err){out.innerHTML='<div class="card warn">'+esc(err)+'</div>'}finally{b.disabled=false}}
document.getElementById('f').onsubmit=ask;
const loadEcon=()=>fetch('/api/stack/economics').then(r=>r.json()).then(e=>{if(!e.questions)return;document.getElementById('econ').innerHTML=
 '<b>'+e.questions+'</b> questions answered here · <b>'+Math.round(100*(e.share_without_api_calls||0))+'%</b> with no model call · API spend <b>$'+(e.api_usd||0).toFixed(4)+'</b>'}).catch(()=>{});
loadEcon();const _ask=ask;ask=async(e)=>{await _ask(e);loadEcon()};document.getElementById('f').onsubmit=ask;
fetch('/api/stack/briefing').then(r=>r.json()).then(d=>{const el=document.getElementById('brief');
 el.innerHTML=(d.cards||[]).slice(0,10).map(c=>'<div class="card"><div class="meta"><b>'+esc(c.kind)+'</b> · '+esc(c.topic)+'</div><div class="headline" style="font-size:15px">'+esc(c.headline)+'</div><div class="meta"><a style="color:var(--accent);cursor:pointer" data-q="'+esc(c.follow_up)+'">'+esc(c.follow_up)+' →</a></div></div>').join('')||'No significant findings.';
 el.querySelectorAll('a[data-q]').forEach(a=>a.onclick=()=>{q.value=a.dataset.q;window.scrollTo(0,0);ask()})}).catch(()=>{document.getElementById('brief').textContent='Briefing unavailable.'});
</script></main></body></html>"""


def page():
    return PAGE
