"""Customer onboarding API and page (/workspaces): connect a warehouse, watch it map and build, review, and ask.

Routes (JSON; handled before the service's global stack lock because builds take minutes):
  GET  /api/workspaces                      list
  GET  /api/workspaces/connectors           supported source specs and the environment variables each needs
  GET  /api/workspaces/<slug>               summary + step detail + verification
  GET  /api/workspaces/<slug>/review        open mapping questions
  GET  /api/workspaces/<slug>/questions     question history
  POST /api/workspaces                      {name, source, as_of?, raw?}  -> create and start the build in the background
  POST /api/workspaces/<slug>/run           re-profile and rebuild from scratch (background)
  POST /api/workspaces/<slug>/decide        {item, decision}  decision: accept | reject | {source} | {source_table}
  POST /api/workspaces/<slug>/rebuild       re-apply the mapping with decisions (background)
  POST /api/workspaces/<slug>/ask           {question, answer_type?}  -> the hybrid core's answer over this workspace
  POST /api/workspaces/<slug>/batch         {questions: [{id, question, answer_type?}]} -> answered in the background into answers-<time>.jsonl
"""
from __future__ import annotations

import json
import threading

from .onboarding import workspace as wsmod

_JOBS = {}
_JOBS_LOCK = threading.Lock()

CONNECTORS = [
    {'kind': 'snowflake', 'spec': 'snowflake://DATABASE/SCHEMA',
     'env': ['SNOWFLAKE_ACCOUNT', 'SNOWFLAKE_USER', 'SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH', 'SNOWFLAKE_WAREHOUSE', 'SNOWFLAKE_ROLE (SELECT only)']},
    {'kind': 'bigquery', 'spec': 'bigquery://project/dataset', 'env': ['GOOGLE_APPLICATION_CREDENTIALS or application default credentials (dataViewer + jobUser)']},
    {'kind': 'databricks', 'spec': 'databricks://catalog/schema', 'env': ['DATABRICKS_SERVER_HOSTNAME', 'DATABRICKS_HTTP_PATH', 'DATABRICKS_TOKEN']},
    {'kind': 'postgres', 'spec': 'postgresql://user@host:5432/db (read-only role)', 'env': ['PGPASSWORD or a .pgpass entry']},
    {'kind': 'files', 'spec': 'files:///path/to/extracts (CSV, TSV, JSON, JSONL, Parquet; a folder of part files = one table)', 'env': []},
    {'kind': 'sqlite / duckdb', 'spec': 'sqlite:///path/db.sqlite or duckdb:///path/db.duckdb', 'env': []},
]


def _background(slug, fn):
    with _JOBS_LOCK:
        job = _JOBS.get(slug)
        if job is not None and job.is_alive():
            raise RuntimeError(f'Workspace {slug} is already building')

        def run():
            try:
                fn()
            except Exception:  # noqa: BLE001 - the workspace records its own failure state
                pass
        t = threading.Thread(target=run, name=f'workspace-{slug}', daemon=True)
        _JOBS[slug] = t
        t.start()


def _detail(ws):
    s = ws.state
    out = ws.summary()
    out['steps_detail'] = s['steps']
    out['building'] = bool(_JOBS.get(ws.slug) and _JOBS[ws.slug].is_alive())
    v = ws.dir / 'verification.json'
    out['verification_detail'] = json.loads(v.read_text()) if v.is_file() else None
    return out


def handle(method, path, body=None):
    """-> (http status, payload). Raises nothing; errors come back as {'error': ...}."""
    parts = [p for p in path.split('/') if p][2:]  # after api/workspaces
    try:
        if method == 'GET':
            if not parts:
                return 200, {'workspaces': wsmod.list_workspaces()}
            if parts == ['connectors']:
                return 200, {'connectors': CONNECTORS}
            ws = wsmod.Workspace(parts[0])
            if len(parts) == 1:
                return 200, _detail(ws)
            if parts[1] == 'review':
                return 200, {'items': ws.review_items()}
            if parts[1] == 'questions':
                f = ws.dir / 'questions.jsonl'
                rows = [json.loads(l) for l in f.read_text().splitlines()] if f.is_file() else []
                return 200, {'questions': rows[-100:][::-1]}
            return 404, {'error': 'Unknown workspace route'}
        body = body or {}
        if not parts:
            if not body.get('name') or not body.get('source'):
                raise ValueError('name and source are required')
            ws = wsmod.Workspace.create(body['name'], body['source'], as_of=body.get('as_of') or None, raw=body.get('raw') or 'all')
            _background(ws.slug, ws.run)
            return 202, _detail(ws)
        ws = wsmod.Workspace(parts[0])
        action = parts[1] if len(parts) > 1 else None
        if action == 'run':
            _background(ws.slug, ws.run)
            return 202, _detail(ws)
        if action == 'rebuild':
            _background(ws.slug, ws.rebuild)
            return 202, _detail(ws)
        if action == 'decide':
            return 200, {'items': ws.decide(body['item'], body['decision'], reviewer='person:' + str(body.get('reviewer') or 'reviewer'))}
        if action == 'batch':
            qs = body.get('questions')
            if not isinstance(qs, list) or not qs or len(qs) > 500:
                raise ValueError('questions must be a list of 1-500 {id, question, answer_type?}')
            qs = [{'id': str(q.get('id') or f'Q{i + 1}'), 'question': str(q['question']), 'answer_type': q.get('answer_type')}
                  for i, q in enumerate(qs)]
            _background(ws.slug + ':batch', lambda: wsmod.ask_many(ws, qs))
            return 202, {'accepted': len(qs), 'results': 'GET /api/workspaces/' + ws.slug + '/questions'}
        if action == 'ask':
            q = str(body.get('question') or '').strip()
            if not q:
                raise ValueError('question is required')
            out = ws.ask(q, answer_type=body.get('answer_type') or None)
            return 200, out
        return 404, {'error': 'Unknown workspace action'}
    except FileNotFoundError as exc:
        return 404, {'error': str(exc)}
    except (ValueError, KeyError, FileExistsError, RuntimeError) as exc:
        return 400, {'error': f'{type(exc).__name__}: {exc}'[:600]}


def page():
    return PAGE


PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agere Workspaces</title><style>
:root{--bg:#f7f8fa;--panel:#fff;--ink:#16202c;--muted:#5d6b7a;--line:#dfe4ea;--accent:#0f6e6e;--warn:#9a5b00;--bad:#a33;--ok:#1d7a3a}
@media (prefers-color-scheme:dark){:root{--bg:#0f1419;--panel:#171e26;--ink:#e6edf3;--muted:#93a1b0;--line:#2a3440;--accent:#4fb3b3;--warn:#e0a84f;--bad:#f07070;--ok:#5cc47e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
header h1{margin:0;font-size:20px}header a{color:var(--accent)}main{display:grid;grid-template-columns:300px 1fr;gap:20px;padding:20px 24px;max-width:1400px}
@media (max-width:860px){main{grid-template-columns:1fr;padding:16px}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
h2{font-size:16px;margin:0 0 10px}h3{font-size:14px;margin:14px 0 6px}label{display:block;font-size:13px;color:var(--muted);margin-top:8px}
input,select,textarea{width:100%;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:inherit}
button{margin-top:10px;padding:8px 14px;border:0;border-radius:6px;background:var(--accent);color:#fff;font:inherit;cursor:pointer}
button.secondary{background:transparent;color:var(--accent);border:1px solid var(--accent)}button:disabled{opacity:.5;cursor:default}
.ws{padding:8px;border-radius:6px;cursor:pointer;border:1px solid transparent}.ws:hover,.ws.on{border-color:var(--line);background:var(--bg)}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;border:1px solid var(--line);margin-right:4px}
.ready{color:var(--ok);border-color:var(--ok)}.failed{color:var(--bad);border-color:var(--bad)}.building,.profiling,.mapping,.verifying{color:var(--warn);border-color:var(--warn)}
.steps{display:flex;gap:6px;flex-wrap:wrap}.step{font-size:12px;padding:3px 8px;border-radius:4px;background:var(--bg);border:1px solid var(--line)}.step.done{border-color:var(--ok);color:var(--ok)}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}
.muted{color:var(--muted);font-size:13px}pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);padding:10px;border-radius:6px;font-size:12px;max-height:320px;overflow:auto}
.answer{font-size:28px;font-weight:600;margin:6px 0}.err{color:var(--bad)}details summary{cursor:pointer;color:var(--accent);font-size:13px}
</style></head><body>
<header><h1>Agere Workspaces</h1><span class="muted">Connect a warehouse, map it automatically, ask questions the same day.</span><a href="/ask">Ask on the current warehouse</a></header>
<main><aside>
<div class="panel"><h2>Connect a warehouse</h2>
<label>Customer name</label><input id="name" placeholder="Acme Health">
<label>Source</label><input id="source" placeholder="snowflake://DB/SCHEMA">
<label>Data as of (optional)</label><input id="asof" placeholder="YYYY-MM-DD">
<label>Raw tables</label><select id="raw"><option value="all">Keep every source table queryable</option><option value="unmapped">Only tables the model doesn't use</option><option value="none">None</option></select>
<button id="create">Connect and build</button><div id="cerr" class="err"></div>
<details><summary>Supported sources</summary><div id="connectors" class="muted"></div></details></div>
<div class="panel"><h2>Workspaces</h2><div id="list" class="muted">None yet.</div></div>
</aside><section id="detail"><div class="panel muted">Pick or connect a workspace.</div></section></main>
<script>
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let current=null,timer=null;
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}:{});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);return d}
const STEPS=['profile','propose','approve','build','raw','verify','docs'];
async function loadList(){const d=await api('/api/workspaces');$('list').innerHTML=d.workspaces.length?d.workspaces.map(w=>`<div class="ws ${w.slug===current?'on':''}" data-s="${esc(w.slug)}"><b>${esc(w.name)}</b><br><span class="pill ${esc(w.status)}">${esc(w.status)}</span><span class="muted">${esc(w.source)}</span></div>`).join(''):'None yet.';
 document.querySelectorAll('.ws').forEach(e=>e.onclick=()=>open(e.dataset.s))}
async function open(slug){current=slug;clearTimeout(timer);loadList();const w=await api('/api/workspaces/'+slug);render(w);if(w.building||!['ready','failed'].includes(w.status))timer=setTimeout(()=>open(slug),3000)}
function render(w){const steps=STEPS.map(s=>`<span class="step ${w.steps[s]?'done':''}">${s}</span>`).join('');
 const tables=w.tables?Object.entries(w.tables).filter(([,n])=>n).map(([t,n])=>`<tr><td>${esc(t)}</td><td>${Number(n).toLocaleString()}</td></tr>`).join(''):'';
 const raw=w.raw_tables?Object.entries(w.raw_tables).map(([t,n])=>`<tr><td>${esc(t)}</td><td>${Number(n).toLocaleString()}</td></tr>`).join(''):'';
 const failed=(w.steps_detail?.verify?.failed_checks)||[];
 $('detail').innerHTML=`<div class="panel"><h2>${esc(w.name)} <span class="pill ${esc(w.status)}">${esc(w.status)}</span></h2>
 <div class="muted">${esc(w.source)} · data as of ${esc(w.as_of||'—')}</div><div class="steps" style="margin-top:8px">${steps}</div>
 ${w.error?`<p class="err">${esc(w.error)}</p>`:''}
 <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="secondary" id="rebuild" ${w.building?'disabled':''}>Rebuild with my decisions</button><button class="secondary" id="rerun" ${w.building?'disabled':''}>Re-profile from scratch</button></div></div>
 <div class="panel"><h2>Ask</h2><textarea id="q" rows="2" placeholder="How many members with diabetes had an A1c over 9 last year?" ${w.status!=='ready'?'disabled':''}></textarea>
 <button id="ask" ${w.status!=='ready'?'disabled':''}>Ask</button><div id="ans"></div><h3>Recent questions</h3><div id="hist" class="muted"></div></div>
 <div class="panel"><h2>Mapping review</h2><div class="muted">Answers work now; each decision here sharpens the next rebuild.</div><div id="review"></div></div>
 <div class="panel"><h2>What was built</h2><h3>Modeled tables</h3><table>${tables||'<tr><td class="muted">—</td></tr>'}</table>
 <h3>Raw source tables (queryable as-is)</h3><table>${raw||'<tr><td class="muted">—</td></tr>'}</table>
 <h3>Checks</h3><div class="muted">${w.verification===true?'All checks passed.':w.verification===false?'Checks to look at: '+esc(failed.join(', ')||'see detail'):'Not run yet.'}</div>
 <details><summary>Check detail</summary><pre>${esc(JSON.stringify(w.verification_detail,null,1))}</pre></details></div>`;
 $('rebuild').onclick=async()=>{await api('/api/workspaces/'+w.slug+'/rebuild',{});open(w.slug)};
 $('rerun').onclick=async()=>{await api('/api/workspaces/'+w.slug+'/run',{});open(w.slug)};
 $('ask').onclick=()=>ask(w.slug);loadReview(w.slug);loadHist(w.slug)}
function showAnswer(d){if(!d.answer)return `<p class="muted">No confident answer (${esc(d.interpretation)}). The analysts disagreed or the question needs a clearer definition.</p>`;
 const a=d.answer;let main='';if('count' in a||'value' in a)main=`<div class="answer">${esc((a.count??a.value).toLocaleString?.()??a.count??a.value)}</div>`;
 else if(a.patient_ids)main=`<div class="answer">${a.patient_ids.length.toLocaleString()} patients</div><pre>${esc(a.patient_ids.slice(0,200).join(', '))}</pre>`;
 else if(a.table)main=`<table>${a.table.map(r=>`<tr><td>${esc(r.keys.join(' · '))}</td><td>${esc(r.value)}</td></tr>`).join('')}</table>`;
 const c=Object.values(d.contracts||{}).find(Boolean)||{},sql=Object.values(d.runs||{}).map(r=>r.sql).find(Boolean);
 return main+`<div class="muted">${esc(d.interpretation)} · ${d.cost_usd==null?'cost unknown':'$'+Number(d.cost_usd).toFixed(3)}</div>
 <details><summary>Definition used</summary><pre>${esc(JSON.stringify({population:c.population,numerator:c.numerator,denominator:c.denominator,window:c.window,exclusions:c.exclusions,grain:c.grain},null,1))}</pre></details>
 <details><summary>SQL</summary><pre>${esc(sql||'')}</pre></details>`}
async function ask(slug){const q=$('q').value.trim();if(!q)return;$('ask').disabled=true;$('ans').innerHTML='<p class="muted">Three analysts are working independently; the system runs and checks their SQL…</p>';
 try{const d=await api('/api/workspaces/'+slug+'/ask',{question:q});$('ans').innerHTML=showAnswer(d);loadHist(slug)}catch(e){$('ans').innerHTML=`<p class="err">${esc(e.message)}</p>`}finally{$('ask').disabled=false}}
async function loadHist(slug){const d=await api('/api/workspaces/'+slug+'/questions');$('hist').innerHTML=d.questions.slice(0,8).map(r=>`<div>${esc(r.question)} — <b>${esc(r.answer?(r.answer.count??r.answer.value??(r.answer.patient_ids?r.answer.patient_ids.length+' patients':'table')):'no confident answer')}</b></div>`).join('')||'None yet.'}
async function loadReview(slug){const d=await api('/api/workspaces/'+slug+'/review');const open=d.items.filter(i=>!i.decided);
 $('review').innerHTML=open.length?`<table><tr><th>Item</th><th>Why</th><th>Decision</th></tr>${open.slice(0,40).map(i=>`<tr><td>${esc(i.id)}</td><td>${esc(i.reason)}${i.candidates?.length?'<br><span class="muted">options: '+esc(i.candidates.map(c=>c.column||c.table).join(', '))+'</span>':''}</td>
 <td><button class="secondary" data-i="${esc(i.id)}" data-d="accept" ${i.current?'':'disabled'}>Accept</button> <button class="secondary" data-i="${esc(i.id)}" data-d="reject">Reject</button>
 ${i.candidates?.length?`<select data-i="${esc(i.id)}" data-k="${i.kind==='table'?'source_table':'source'}"><option value="">Use instead…</option>${i.candidates.map(c=>`<option>${esc(c.column||c.table)}</option>`).join('')}</select>`:''}</td></tr>`).join('')}</table>`:'<p class="muted">Nothing open.</p>';
 document.querySelectorAll('#review button').forEach(b=>b.onclick=async()=>{await api('/api/workspaces/'+slug+'/decide',{item:b.dataset.i,decision:b.dataset.d});loadReview(slug)});
 document.querySelectorAll('#review select').forEach(s=>s.onchange=async()=>{if(!s.value)return;await api('/api/workspaces/'+slug+'/decide',{item:s.dataset.i,decision:{[s.dataset.k]:s.value}});loadReview(slug)})}
$('create').onclick=async()=>{$('cerr').textContent='';try{const w=await api('/api/workspaces',{name:$('name').value,source:$('source').value,as_of:$('asof').value,raw:$('raw').value});open(w.slug)}catch(e){$('cerr').textContent=e.message}};
api('/api/workspaces/connectors').then(d=>$('connectors').innerHTML=d.connectors.map(c=>`<p><b>${esc(c.kind)}</b>: <code>${esc(c.spec)}</code>${c.env.length?'<br>'+esc(c.env.join(', ')):''}</p>`).join(''));
loadList();
</script></body></html>'''
