"""HTML for the hosted server: sign-in, the workspace app, admin, account -- one design system (brand.py tokens).

The design follows the marketing site: paper sections alternating with dark forest sections, numbered mono eyebrows
("02 / INTELLIGENCE / ASK"), DM Sans for UI and IBM Plex Mono for labels, numbers and code, orange as the single accent,
light and dark variants that follow prefers-color-scheme. Pages carry their own CSS and JS (only the two web fonts load
from Google Fonts; system fallbacks otherwise). Every page loads /api/auth/me, keeps the session's CSRF token and sends it
on state-changing requests; a 401 returns to /login.

The workspace app (/app) is one page with hash routes: #/ lists the organization's workspaces and connects new ones;
#/w/<slug>/<tab> opens a workspace on Ask, Plan & act, Simulate, Create, Physician value or Data. It reads the workspace
JSON API (workspace_api.py) and the hosted value route (/api/workspaces/<slug>/value, server.py); the deliverable route is
feature-detected (a 404 shows a "coming online" state).
"""
from __future__ import annotations

import html as _html
import json
from pathlib import Path

from . import brand

NAME = brand.PRODUCT_NAME

DESIGN_CSS = brand.css_tokens() + r'''
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 "DM Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;
 -webkit-font-smoothing:antialiased}
.mono,code,pre,.eyebrow,.num,.pill,.step,.tab small,th{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
a{color:inherit;text-decoration-color:var(--orange);text-underline-offset:3px}
.wrap{max-width:1200px;margin:0 auto;padding:0 16px}
@media (min-width:720px){.wrap{padding:0 40px}}
.top{background:var(--forest);color:#eeeee8;border-bottom:1px solid rgba(238,238,232,.12)}
.top .wrap{display:flex;gap:18px;align-items:center;flex-wrap:wrap;min-height:60px}
.brand{font-weight:700;letter-spacing:.14em;text-transform:uppercase;text-decoration:none;font-size:15px}
.brand small{font:400 12px "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.08em;opacity:.65;margin-left:10px}
.top nav{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-left:auto;font-size:14px}
.top nav a{text-decoration:none;opacity:.85}.top nav a:hover,.top nav a.on{opacity:1;text-decoration:underline}
.top select{width:auto;background:transparent;color:inherit;border-color:rgba(238,238,232,.3);padding:4px 8px}
.top .pill{border-color:rgba(238,238,232,.3);color:#eeeee8}
.section{padding:56px 0}.section.tight{padding:32px 0}
.forest{background:var(--forest);color:#eeeee8}
.forest .muted{color:rgba(238,238,232,.68)}.forest .eyebrow{color:rgba(238,238,232,.7)}
.forest input,.forest select,.forest textarea{background:rgba(238,238,232,.06);color:#eeeee8;border-color:rgba(238,238,232,.25)}
.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0 0 14px}
.eyebrow b{color:var(--orange);font-weight:500}
h1,h2,h3{font-weight:500;letter-spacing:-.02em;margin:0}
h1{font-size:clamp(36px,6vw,64px);line-height:1.02}
h2{font-size:clamp(28px,4vw,44px);line-height:1.06}
h3{font-size:19px;letter-spacing:-.01em;margin:0 0 10px}
.lead{font-size:18px;max-width:640px;color:var(--muted);margin:14px 0 0}.forest .lead{color:rgba(238,238,232,.75)}
.muted{color:var(--muted);font-size:14px}.small{font-size:13px}
.grid{display:grid;gap:16px}.g2{grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}.g3{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:20px;min-width:0}
.forest .card{background:var(--forest-2);border-color:rgba(238,238,232,.14)}
label{display:block;font-size:13px;color:var(--muted);margin:12px 0 6px}
input,select,textarea{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:3px;background:var(--card);color:var(--ink);font:inherit}
textarea{resize:vertical}input:focus,select:focus,textarea:focus{outline:2px solid var(--orange);outline-offset:1px}
button,.btn{display:inline-flex;align-items:center;gap:8px;margin-top:14px;padding:11px 20px;border:1px solid var(--ink);border-radius:999px;background:var(--ink);
 color:var(--paper);font:500 15px "DM Sans",ui-sans-serif,system-ui,sans-serif;cursor:pointer;text-decoration:none}
button.accent{background:var(--orange);border-color:var(--orange);color:#fff}
button.ghost,.btn.ghost{background:transparent;color:inherit;border-color:currentColor}
.forest button:not(.accent):not(.ghost){background:#eeeee8;color:var(--forest);border-color:#eeeee8}
button:disabled{opacity:.45;cursor:default}button.chip{margin:6px 6px 0 0;padding:7px 12px;font-size:13px;border-color:var(--line);background:transparent;color:var(--ink);text-align:left}
.forest button.chip{color:#eeeee8;border-color:rgba(238,238,232,.25);background:transparent}
button.chip:hover{border-color:var(--orange)}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;border:1px solid var(--line)}
.pill.ready,.pill.ok{color:var(--ok);border-color:var(--ok)}.pill.failed,.pill.bad{color:var(--bad);border-color:var(--bad)}.pill.warn{color:var(--orange);border-color:var(--orange)}
.num{font-weight:500;letter-spacing:-.03em;font-size:clamp(44px,8vw,84px);line-height:1}
.kpi{font:500 28px "IBM Plex Mono",ui-monospace,monospace;letter-spacing:-.02em}
table{border-collapse:collapse;width:100%;font-size:14px}th{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:500}
td,th{border-bottom:1px solid var(--line);padding:9px 8px;text-align:left;vertical-align:top}.forest td,.forest th{border-color:rgba(238,238,232,.14)}
td.r,th.r{text-align:right}.tablewrap{overflow-x:auto;max-width:100%}
pre{white-space:pre-wrap;word-break:break-word;background:var(--paper-2);padding:14px;border-radius:3px;font-size:12px;max-height:360px;overflow:auto;margin:8px 0 0}
code{font-size:12px;word-break:break-word}
details summary{cursor:pointer;font-size:14px;color:var(--ink);text-decoration:underline;text-decoration-color:var(--orange);text-underline-offset:3px}
.err{color:var(--bad)}.ok{color:var(--ok)}
.tabs{display:flex;gap:0;flex-wrap:wrap;margin-top:34px;border-top:1px solid rgba(238,238,232,.18)}
.tab{flex:1 1 120px;padding:14px 14px 4px 0;text-decoration:none;color:rgba(238,238,232,.6);border-top:2px solid transparent;margin-top:-1px;min-width:110px}
.tab small{display:block;font-size:11px;letter-spacing:.1em;text-transform:uppercase}.tab span{font-size:16px}
.tab.on{color:#eeeee8;border-top-color:var(--orange)}.tab:hover{color:#eeeee8}
.trace{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));border:1px solid var(--line);border-radius:4px;overflow:hidden;margin:22px 0}
@media (max-width:760px){.trace{grid-template-columns:repeat(2,minmax(0,1fr))}}
.trace div{padding:12px 12px 14px;border-right:1px solid var(--line);min-width:0}.trace div:last-child{border-right:0}
.trace small{display:block;font:11px "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.1em;color:var(--muted);text-transform:uppercase}
.trace b{display:block;font-weight:500;margin:2px 0 4px}.trace span{font-size:12px;color:var(--muted);word-break:break-word}
.trace div.on{background:var(--paper-2)}.trace div.on small{color:var(--orange)}
.funnel{display:grid;gap:8px;margin:14px 0}.bar{display:grid;grid-template-columns:170px 1fr 60px;gap:12px;align-items:center;font-size:14px}
@media (max-width:560px){.bar{grid-template-columns:110px 1fr 46px}}
.bar i{display:block;height:12px;background:var(--orange);border-radius:2px;min-width:2px}.bar i.alt{background:var(--ink);opacity:.45}
.bar .n{font-family:"IBM Plex Mono",ui-monospace,monospace;text-align:right}
.steps{display:grid;gap:0;margin:10px 0}.stp{display:grid;grid-template-columns:22px 1fr;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}
.dot{width:12px;height:12px;border-radius:50%;margin-top:6px;border:2px solid var(--ok)}.dot.failed{border-color:var(--bad)}.dot.moved,.dot.proposed,.dot.awaiting_counterparty{border-color:var(--orange)}
.dot.done{background:var(--ok)}
.split{display:grid;gap:40px;grid-template-columns:1fr}@media (min-width:900px){.split{grid-template-columns:minmax(0,5fr) minmax(0,7fr)}}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.row>*{margin-top:0}
.ws-card{display:block;text-decoration:none}.ws-card:hover{border-color:var(--orange)}
.notice{border-left:3px solid var(--orange);padding:10px 14px;background:var(--paper-2);font-size:14px;margin:14px 0}
iframe.deliverable{width:100%;height:720px;border:1px solid var(--line);border-radius:4px;background:#fff}
.spin{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--orange);animation:p 1s infinite alternate}@keyframes p{to{opacity:.2}}
footer{padding:28px 0;font-size:12px}
'''

SESSION_JS = r'''
let ME=null;
const _esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const _fetch=window.fetch.bind(window);
window.fetch=async(url,opt={})=>{opt=opt||{};const m=(opt.method||'GET').toUpperCase();
 if(m!=='GET'&&ME&&ME.csrf){opt.headers=Object.assign({},opt.headers||{},{'X-CSRF-Token':ME.csrf})}
 const r=await _fetch(url,opt);if(r.status===401&&!String(url).includes('/api/auth/login'))location.href='/login';return r};
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}:{});
 const d=await r.json().catch(()=>({}));if(!r.ok){const e=Error(d.error||('HTTP '+r.status));e.status=r.status;e.body=d;throw e}return d}
async function loadMe(){ME=await api('/api/auth/me');const b=document.getElementById('orgbar');if(!b)return ME;
 const orgs=(ME.orgs||[]);const here=location.pathname;
 b.innerHTML='<div class="wrap"><a class="brand" href="/app">'+_esc(PRODUCT)+'<small>/ '+_esc(TAGLINE)+'</small></a>'+
 '<span class="pill">'+_esc(ME.org)+' · '+_esc(ME.role)+'</span>'+
 (orgs.length>1?'<select id="orgsel" aria-label="Organization">'+orgs.map(o=>'<option value="'+_esc(o.slug)+'"'+(o.slug===ME.org?' selected':'')+'>'+_esc(o.name||o.slug)+'</option>').join('')+'</select>':'')+
 '<nav><a href="/app"'+(here==='/app'||here==='/'?' class="on"':'')+'>Workspaces</a>'+(['admin','owner'].includes(ME.role)?'<a href="/admin"'+(here==='/admin'?' class="on"':'')+'>Admin</a>':'')+
 '<a href="/account"'+(here==='/account'?' class="on"':'')+'>'+_esc((ME.user||{}).email||'Account')+'</a><button class="ghost" id="logout" style="margin:0;padding:6px 14px">Sign out</button></nav></div>';
 document.getElementById('logout').onclick=async()=>{await api('/api/auth/logout',{});location.href='/login'};
 const s=document.getElementById('orgsel');if(s)s.onchange=async()=>{await api('/api/auth/switch',{org:s.value});location.reload()};return ME}
'''


def _brand_js():
    return f'const PRODUCT={json.dumps(NAME)},TAGLINE={json.dumps(brand.TAGLINE)};'


def _head(title):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><title>{_html.escape(title)}</title>'
            f'<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link rel="stylesheet" href="{brand.FONTS_URL}"><style>{DESIGN_CSS}</style></head>')


def _page(title, body, script=''):
    foot = (f'<footer class="muted mono"><div class="wrap">{_html.escape(NAME.upper())} / SYNTHETIC DATA ONLY.</div></footer>')
    return (f'{_head(title)}<body>{body}{foot}<script>{_brand_js()}{SESSION_JS}const esc=_esc;{script}</script></body></html>')


def login():
    body = f'''<main class="split" style="min-height:calc(100vh - 80px);gap:0">
<section class="forest section" style="display:flex;align-items:center"><div class="wrap" style="max-width:620px">
<p class="eyebrow">{_html.escape(NAME.upper())} / <b>FROM DATA TO WISDOM</b></p>
<h1>Messy data.<br>Better lives.</h1>
<p class="lead">Find the patient. Put care in motion. Follow through to more days at home.</p>
<div class="grid g2" style="margin-top:34px">
<div><p class="eyebrow">01 / DATA</p><p class="muted">Every record, connected into one patient story.</p></div>
<div><p class="eyebrow">02 / INTELLIGENCE</p><p class="muted">Checked answers with their definition and method.</p></div>
<div><p class="eyebrow">03 / KNOWLEDGE</p><p class="muted">The pathway that fits each person, with agents that enroll.</p></div>
<div><p class="eyebrow">04 / WISDOM</p><p class="muted">Titrate, monitor, learn from each response.</p></div></div></div></section>
<section class="section" style="display:flex;align-items:center"><div class="wrap" style="max-width:460px;width:100%">
<p class="eyebrow">SIGN IN</p><h2>Sign in to {_html.escape(NAME)}</h2>
<form id="f" style="margin-top:20px"><label for="email">Work email</label><input id="email" type="email" autocomplete="username" required>
<label for="password">Password</label><input id="password" type="password" autocomplete="current-password" required>
<button type="submit" class="accent">Sign in</button><p id="msg" class="err" role="alert"></p></form>
<p class="notice">This environment accepts synthetic data only. Do not upload patient information.</p></div></section></main>'''
    script = r'''document.getElementById('f').onsubmit=async e=>{e.preventDefault();const m=document.getElementById('msg');m.textContent='';
 try{await api('/api/auth/login',{email:document.getElementById('email').value,password:document.getElementById('password').value});location.href='/app'}
 catch(err){m.textContent=err.message}}'''
    return _page(f'{NAME} Sign In', body, script)


def _oap_terms():
    """{program id: ACCESS OAP terms} from the simulator configuration (read-only), for the Simulate view."""
    from pathlib import Path
    try:
        cfg = json.loads((Path(__file__).resolve().parents[1] / 'simulator' / 'config.json').read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for pid, p in (cfg.get('programs') or {}).items():
        o = (p or {}).get('oap') if isinstance(p, dict) else None
        if not o:
            continue
        v = lambda k: (o.get(k) or {}).get('value') if isinstance(o.get(k), dict) else o.get(k)
        out[pid] = {'track': o.get('track'), 'annual_allowed': v('annual_allowed'), 'withheld_share': v('withheld_share'),
                    'outcome_attainment_threshold': v('outcome_attainment_threshold'), 'p_target_met_if_completed': v('p_target_met_if_completed'),
                    'medicare_share': v('medicare_share'), 'source': (o.get('annual_allowed') or {}).get('source')}
    return out


def app():
    data = {'examples': brand.example_questions(), 'programs': brand.PROGRAMS, 'templates': brand.DELIVERABLE_TEMPLATES,
            'ask_title': brand.ASK_TITLE, 'oap': _oap_terms()}
    body = '<header class="top" id="orgbar"></header><main id="view"><section class="section"><div class="wrap muted">Loading…</div></section></main>'
    boot = 'const APP=' + json.dumps(data).replace('</', '<\\/') + ';\n'
    assets = Path(__file__).parents[1] / 'assets'
    presentation = ('const ResultView=(()=>{const state={seq:0};'
        'const isNum=v=>typeof v==="number"&&Number.isFinite(v);'
        'const cols=rows=>[...new Set(rows.flatMap(r=>Object.keys(r)))];\n'
        + (assets/'answer_presentation.js').read_text()
        + '\n' + (assets/'verification_progress.js').read_text()
        + '\nreturn {summary:answerSummary,table:presentationTable,limits:resultLimitations,sources:sourceDetails,columns:cols,progress:verificationProgressHTML};})();\n')
    page = _page(f'{NAME}', body, boot + presentation + APP_JS + '\nloadMe().then(route).catch(e=>{if(e.status!==401)document.getElementById("view").innerHTML=\'<p class="err wrap">\'+esc(e.message)+\'</p>\'});')
    return page.replace('<html lang="en">', '<html lang="en" data-theme="light">').replace('</head>',
        '<style>' + (assets/'answer_presentation.css').read_text() + (assets/'verification_progress.css').read_text() + HOSTED_ANSWER_CSS + '</style></head>')


HOSTED_ANSWER_CSS = r'''
.hosted-answer{--fg:#262633;--line:#e4e2e9;--muted:#625f6e;background:#fff;border-radius:16px;padding:28px;min-width:0}
.hosted-answer h2{font-size:24px;line-height:1.3;margin:0 0 14px}.hosted-answer .answer-question{font-size:14px;color:#62576e;border-left:3px solid #c6b7df;padding-left:14px;line-height:1.7}
.hosted-answer .answer-intro{font-size:16px;line-height:1.8;max-width:85ch}.hosted-answer .answer-status{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0 24px}.hosted-answer .num{font-size:48px;color:#583b87}
.hosted-answer .result-at-a-glance{margin-top:20px}.hosted-answer .answer-findings,.hosted-answer .result-limitations{margin-top:20px}
.hosted-answer .result-records{margin:24px 0}.hosted-answer .result-records>summary{font-size:16px;min-height:44px}.hosted-answer #hostedRecords{min-width:0}
.hosted-answer .result-search{margin-top:16px}.hosted-answer .column-toggle input{width:16px;margin:0}.hosted-answer [hidden]{display:none!important}
.hosted-answer .trace{margin:20px 0}.hosted-answer .method-disclosure{margin:24px 0}.hosted-answer .sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.hosted-answer details pre{max-height:420px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}.hosted-answer .card{background:#faf9fc}.hosted-answer .answer-state{margin:16px 0}
@media(max-width:650px){.hosted-answer{padding:18px}.hosted-answer h2{font-size:21px}.hosted-answer .result-table:not(.all-columns) tr:has(>td:first-child) td:first-child{display:table-cell}.hosted-answer .result-search button{min-height:44px}}
'''


APP_JS = r'''
const $=id=>document.getElementById(id);
const fmt=n=>n==null||n===''?'—':(typeof n==='number'?n.toLocaleString(undefined,{maximumFractionDigits:2}):esc(n));
const money=n=>n==null?'—':(Number(n)<0?'-$':'$')+Math.abs(Number(n)).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0});
const pct=x=>x==null?'—':(Math.round(x*1000)/10)+'%';
const label=s=>String(s||'').replace(/_/g,' ');
const clip=(s,n)=>{s=String(s??'');return s.length>n?s.slice(0,n-1)+'…':s};
let STATE={slug:null,ws:null,timer:null,conversationId:null,conversationWorkspace:null,answerContext:null};
const TABS=[['ask','02','Ask','Intelligence'],['act','03','Plan & act','Knowledge'],['simulate','04','Simulate','Wisdom'],
 ['create','05','Create','Make anything'],['value','06','Physician value','VBC 3.0'],['data','07','Data','Connect & map']];
window.addEventListener('hashchange',route);
function route(){clearTimeout(STATE.timer);const h=location.hash.replace(/^#\/?/,'').split('/');
 if(h[0]==='w'&&h[1])return openWorkspace(decodeURIComponent(h[1]),h[2]||'ask');return home()}

// --- workspace list ---------------------------------------------------------------------------------------------------
async function home(){
 $('view').innerHTML=`<section class="forest section"><div class="wrap"><p class="eyebrow">01 / ${esc(String(ME.org).toUpperCase())} / <b>WORKSPACES</b></p>
 <h1>From data<br>to wisdom.</h1><p class="lead">Each workspace is one connected warehouse: ask checked questions, put care plans in motion with agents, simulate the economics and see the value reach the practice.</p></div></section>
 <section class="section"><div class="wrap split"><div><p class="eyebrow">YOUR WORKSPACES</p><div id="list" class="grid">Loading…</div></div>
 <div class="card"><p class="eyebrow">CONNECT</p><h3>Connect a warehouse</h3>
 <label for="name">Customer name</label><input id="name" placeholder="Acme Health">
 <label for="source">Source</label><input id="source" placeholder="snowflake://DB/SCHEMA">
 <label for="credential">Saved credentials (optional)</label><input id="credential" placeholder="name saved by an admin">
 <label for="upfile">Or upload extracts (.zip of CSV/X12/HL7/FHIR files, or a .sqlite/.csv file)</label>
 <input id="upfile" type="file" accept=".zip,.csv,.sqlite,.sqlite3,.db,.parquet,.json,.jsonl">
 <label for="uptype">Uploaded zip contains</label><select id="uptype"><option value="native">Native healthcare feeds (X12, HL7, NCPDP, FHIR, EHR extracts)</option><option value="files">Plain tables (one file per table)</option></select>
 <p id="upmsg" class="muted" role="status"></p>
 <label for="asof">Data as of (optional)</label><input id="asof" placeholder="YYYY-MM-DD">
 <label for="raw">Raw tables</label><select id="raw"><option value="all">Keep every source table queryable</option><option value="unmapped">Only tables the model does not use</option><option value="none">None</option></select>
 <button id="create" class="accent">Connect and build</button><p id="cerr" class="err"></p>
 <details><summary>Supported sources</summary><div id="connectors" class="muted small"></div></details></div></div></section>`;
 const d=await api('/api/workspaces');
 $('list').innerHTML=d.workspaces.length?d.workspaces.map(w=>`<a class="card ws-card" href="#/w/${encodeURIComponent(w.slug)}/ask"><div class="row" style="justify-content:space-between"><h3 style="margin:0">${esc(w.name)}</h3><span class="pill ${esc(w.status)}">${esc(w.status)}</span></div>
  <p class="muted mono small" style="margin:8px 0 0">AS OF ${esc(w.as_of||'—')} · ${esc(String(w.source||'').split('/').filter(Boolean).slice(-2).join('/'))}</p></a>`).join(''):'<p class="muted">None yet. Connect a warehouse to begin.</p>';
 $('create').onclick=async()=>{$('cerr').textContent='';try{const w=await api('/api/workspaces',{name:$('name').value,source:$('source').value,credential:$('credential').value||null,as_of:$('asof').value,raw:$('raw').value});location.hash='#/w/'+w.slug+'/data'}catch(e){$('cerr').textContent=e.message}};
 $('upfile').onchange=upload;
 api('/api/workspaces/connectors').then(d=>$('connectors').innerHTML=d.connectors.map(c=>`<p><b>${esc(c.kind)}</b>: <code>${esc(c.spec)}</code>${c.env.length?'<br>'+esc(c.env.join(', ')):''}</p>`).join('')).catch(()=>{})}
function upload(e){const f=e.target.files[0];if(!f)return;const m=$('upmsg');const name=f.name.replace(/[^A-Za-z0-9._-]/g,'_');const x=new XMLHttpRequest();
 x.open('PUT','/api/uploads/'+encodeURIComponent(name));x.setRequestHeader('X-CSRF-Token',(ME||{}).csrf||'');
 x.upload.onprogress=ev=>{if(ev.lengthComputable)m.textContent='Uploading… '+Math.round(100*ev.loaded/ev.total)+'%'};
 x.onload=()=>{let d={};try{d=JSON.parse(x.responseText)}catch(_){}if(x.status!==200){m.textContent=d.error||('Upload failed ('+x.status+')');return}
  const s=d.sources||{};$('source').value=s[$('uptype').value]||s.files||s.sqlite||Object.values(s)[0]||'';if(!$('name').value)$('name').value=name.replace(/\.[^.]+$/,'');
  m.textContent='Uploaded '+name+(d.extracted_files?' ('+d.extracted_files+' files)':'')+'. Source filled in; press Connect and build.'};
 x.onerror=()=>{m.textContent='Upload failed'};m.textContent='Uploading…';x.send(f)}

// --- one workspace ----------------------------------------------------------------------------------------------------
async function openWorkspace(slug,tab){
 if(STATE.slug!==slug||!STATE.ws){STATE.slug=slug;STATE.ws=await api('/api/workspaces/'+encodeURIComponent(slug))}
 const w=STATE.ws;if(!TABS.some(t=>t[0]===tab))tab='ask';
 $('view').innerHTML=`<section class="forest section" style="padding-bottom:0"><div class="wrap"><p class="eyebrow">01 / WORKSPACE / <b>${esc(String(w.name).toUpperCase())}</b></p>
 <div class="row" style="justify-content:space-between;align-items:flex-end"><h1>${esc(w.name)}</h1><span class="pill ${esc(w.status)}">${esc(w.status)}</span></div>
 <p class="muted mono small" style="margin-top:12px">DATA AS OF ${esc(w.as_of||'—')} · ${fmt(Object.values(w.tables||{}).reduce((a,b)=>a+(+b||0),0))} MODELED ROWS · <a href="#/">ALL WORKSPACES</a></p>
 <nav class="tabs" aria-label="Workspace">${TABS.map(t=>`<a class="tab ${t[0]===tab?'on':''}" href="#/w/${encodeURIComponent(slug)}/${t[0]}"><small>${t[1]} / ${esc(t[3])}</small><span>${esc(t[2])}</span></a>`).join('')}</nav></div></section>
 <div id="pane"></div>`;
 ({ask:paneAsk,act:paneAct,simulate:paneSim,create:paneCreate,value:paneValue,data:paneData})[tab](w)}
function head(n,area,name,title,lead){return `<p class="eyebrow">${n} / ${esc(area)} / <b>${esc(name)}</b></p><h2>${title}</h2>${lead?`<p class="lead">${lead}</p>`:''}`}
const W=p=>'/api/workspaces/'+encodeURIComponent(STATE.slug)+p;

// --- Ask ----------------------------------------------------------------------------------------------------------------
function paneAsk(w){
 if(STATE.conversationWorkspace!==STATE.slug){STATE.conversationId=null;STATE.answerContext=null;STATE.conversationWorkspace=STATE.slug}
 const cats={};APP.examples.forEach(q=>(cats[q.category]=cats[q.category]||[]).push(q));
 $('pane').innerHTML=`<section class="section"><div class="wrap">${head('02','INTELLIGENCE','ASK',esc(APP.ask_title)+'.','Ask in plain language. Every answer carries its definition, its method and the layer that answered, and is checked before it is shown.')}
 <div class="card" style="margin-top:28px"><label for="q" class="eyebrow" style="margin:0 0 8px">WHAT WOULD YOU LIKE TO KNOW?</label><textarea id="q" rows="2" placeholder="Which heart failure patients are not on all four GDMT pillars?" ${w.status!=='ready'?'disabled':''}></textarea>
 <div class="row"><button id="ask" class="accent" ${w.status!=='ready'?'disabled':''}>Ask</button><button id="newQuestion" class="ghost">New conversation</button><span id="asking" class="muted"></span></div>
 <div style="margin-top:16px">${Object.entries(cats).map(([c,qs])=>`<p class="eyebrow" style="margin:14px 0 2px">${esc(c)}</p>${qs.map(q=>`<button class="chip" data-q="${esc(q.question)}">${esc(q.question)}</button>`).join('')}`).join('')}</div></div>
 <div id="ans"></div>
 <div style="margin-top:40px"><p class="eyebrow">RECENT QUESTIONS</p><div id="hist" class="muted">Loading…</div></div></div></section>`;
 document.querySelectorAll('button.chip[data-q]').forEach(b=>b.onclick=()=>{$('q').value=b.dataset.q;$('q').focus()});
 $('newQuestion').onclick=()=>{STATE.conversationId=null;STATE.answerContext=null;$('q').value='';$('ans').innerHTML='';$('q').focus()};$('ask').onclick=ask;$('q').onkeydown=e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey))ask()};loadHist()}
async function ask(){const q=$('q').value.trim();if(!q||$('ask').disabled)return;$('ask').disabled=true;$('newQuestion').disabled=true;
 $('asking').innerHTML='<span class="spin"></span> Connecting, defining, compiling, computing and checking…';$('ans').innerHTML='';
 const t0=Date.now();
 try{const body={question:q,conversation_id:STATE.conversationId,interaction_mode:'interactive'};if(STATE.answerContext){body.answer_context_id=STATE.answerContext;body.answer_scope='auto'}const d=await api(W('/ask'),body);rememberConversation(d);$('ans').innerHTML=showAnswer(d,q,(Date.now()-t0)/1000);bindAnswer(d);loadHist()}
 catch(e){$('ans').innerHTML=`<div class="notice err">${esc(e.message)}</div>`}finally{if($('ask'))$('ask').disabled=false;if($('newQuestion'))$('newQuestion').disabled=false;if($('asking'))$('asking').textContent=''}}
function contractOf(d){const c=Object.values(d.contracts||{}).find(Boolean);if(c)return c;const r=d.result||{};return (r.answer||{}).definition||(r.answer||{}).contract||null}
function definitionText(d,q){const ex=APP.examples.find(x=>x.question===q);const c=contractOf(d);
 if(c&&typeof c==='object'){const parts=['population','numerator','denominator','window','exclusions','grain'].filter(k=>c[k]).map(k=>`<b>${k}</b>: ${esc(typeof c[k]==='string'?c[k]:JSON.stringify(c[k]))}`);if(parts.length)return parts.join('<br>')}
 if(typeof c==='string')return esc(c);const a=(d.result||{}).answer||{};
 if(a.definition_text)return esc(a.definition_text);if(ex&&ex.definition)return esc(ex.definition)+' <span class="muted">(reference definition)</span>';
 return esc(d.headline||d.interpretation||'—')}
function methodOf(d){const sql=Object.values(d.runs||{}).map(r=>r&&r.sql).find(Boolean);if(sql)return {kind:'SQL',text:sql};
 const r=d.result||{};const a=r.answer||{};if(a.sql)return {kind:'SQL',text:a.sql};
 const g=r.graph;if(g&&(g.nodes||[]).length)return {kind:'Cohort-family / analysis spec',text:JSON.stringify(g,null,1)};
 if(d.plan)return {kind:'Routed plan',text:JSON.stringify(d.plan.summary,null,1)};return null}
function layerOf(d){const t=(d.result||{}).together||{};const dec=d.decision||t.decision||'';const route=d.route||'';
 if(route==='saved_cohort')return {name:'Native population calculation',detail:'Computed over the saved population; no language model call'};
 const spec=/special|bottled|cascade|cohort|engine|compiler|local|governed|metric/i.test(dec+' '+route+' '+(d.operations||[]).join(' '));
 return {name:d.pipeline==='plan'?'Deterministic router':(spec?'Specialist layer':(d.pipeline==='hybrid'||/unanim|major|analyst/i.test(dec)?'Analysts (independent, cross-checked)':'Full pipeline')),detail:[d.pipeline,route,dec].filter(Boolean).join(' · ')}}
function traceCells(d,q){const w=STATE.ws||{};const m=methodOf(d);const ans=d.answer;const cert=d.certified;
 return [['01','Connect',w.name||'',`as of ${w.as_of||'—'}`],['02','Define',contractOf(d)||APP.examples.find(x=>x.question===q)?'typed definition':'question read','population, window, grain'],
  ['03','Compile',m?m.kind:'—',(d.operations||[]).slice(0,3).join(', ')||d.route||''],['04','Compute',ans?(('count' in ans)?'count':('value' in ans)?'value':ans.patient_ids?ans.patient_ids.length+' patients':'table'):'no answer',d.pipeline||''],
  ['05','Check',cert?'certified':(d.confidence!=null?'confidence '+fmt(d.confidence):(d.decision||'—')),cert&&typeof cert==='object'?(cert.tier||cert.route||'')+(cert.error_bound!=null?' ± '+cert.error_bound:''):''],
  ['06','Save',d.action_context?'saved with evidence':'question history',d.cost_usd==null?'cost unknown':'$'+Number(d.cost_usd).toFixed(3)]]}
function answerRows(a,d){
 if(Array.isArray(a?.table))return a.table.some(r=>Array.isArray(r.keys)&&!('group' in r))?a.table.map(r=>Array.isArray(r.keys)&&!('group' in r)?{group:r.keys.map(v=>v==null?'Unknown':typeof v==='object'?JSON.stringify(v):String(v)).join(' · '),...r}:r):a.table;
 return (a?.patient_ids||[]).map(id=>({patient_id:id,...(d.customer_ids?.[id]?{customer_id:d.customer_ids[id]}:{})}));
}
function answerCSV(rows){const keys=ResultView.columns(rows),cell=v=>'"'+String(v==null?'':typeof v==='object'?JSON.stringify(v):v).replace(/"/g,'""')+'"';return [keys.map(cell).join(','),...rows.map(row=>keys.map(k=>cell(row[k])).join(','))].join('\r\n')}
function showAnswer(d,q,secs){const a=d.answer,rows=answerRows(a,d);let main='';
 if(d.clarification?.status==='pending')main=clarificationView(d.clarification);
 else if(!a)main=`<div class="notice">${esc(d.headline||d.interpretation||'More information is needed to answer this question.')}</div>${(d.decline?.readings||[]).length?'<p class="muted">Possible meanings:</p><ul>'+d.decline.readings.map(r=>'<li>'+esc(typeof r==='string'?r:JSON.stringify(r))+'</li>').join('')+'</ul>':''}`;
 else {
  if('count' in a||'value' in a){const v=a.count??a.value;main=`<div class="num">${v==null?'Unknown':esc(typeof v==='number'?v.toLocaleString():v)}</div><p class="muted">${esc(a.unit||('count' in a?'Count returned':'Computed value'))}</p>`}
  main+=ResultView.summary({...a,table:rows})+ResultView.limits(a);
  if(rows.length)main+=`<details class="result-records" id="hostedRecordSection"><summary>Explore all ${fmt(rows.length)} records</summary><div class="result-search"><input id="hostedRecordSearch" type="search" aria-label="Search returned records" placeholder="Search names, identifiers or any field"><label class="column-toggle"><input id="hostedAllColumns" type="checkbox">All columns</label><button class="chip" id="hostedCSV">Download all ${fmt(rows.length)} rows</button></div><p id="hostedRecordStatus" class="result-status" role="status"></p><div id="hostedRecords">${ResultView.table(rows,{...a,table:rows})}</div></details>`;
 }
 const m=methodOf(d),L=layerOf(d),status=d.status||d.decision||'Answer';
 return `<section class="card hosted-answer" style="margin-top:24px"><p class="answer-question">${esc(q)}</p><h2>What the answer shows</h2>${a&&d.headline?`<p class="answer-intro">${esc(d.headline)}</p>`:''}<div class="answer-status"><span class="pill">${esc(label(status))}</span>${a?.verification==='unverified'?'<span class="pill">Verification incomplete</span>':''}<span class="small muted">${d.cost_usd==null?'API cost unknown':'API estimate $'+Number(d.cost_usd).toFixed(4)} · ${secs.toFixed(1)} s${d.reopened?' to reopen · no analysis replay':''}</span></div>${answerState(d)}${main}
 <details class="method-disclosure"><summary>Method, sources &amp; scope</summary>${a?ResultView.sources(a):''}<div class="trace">${traceCells(d,q).map((c,i)=>`<div class="${i<4||d.answer?'on':''}"><small>${c[0]} / ${c[1]}</small><b>${esc(c[2])}</b><span>${esc(c[3])}</span></div>`).join('')}</div><div class="grid g3"><div><p class="eyebrow">DEFINITION &amp; SCOPE</p><p class="small">${definitionText(d,q)}</p></div><div><p class="eyebrow">EXECUTION</p><p class="small"><b>${esc(L.name)}</b><br>${esc(L.detail)}</p></div><div><p class="eyebrow">USAGE</p><p class="small">${d.new_paid_api_calls==null?'New model calls unknown':fmt(d.new_paid_api_calls)+' new model calls'}. Local compute and review costs are not included in the API estimate.</p></div></div>${m?`<details><summary>${esc(m.kind)}</summary><pre>${esc(m.text)}</pre></details>`:''}${d.certified?`<details><summary>Recorded certificate</summary><pre>${esc(JSON.stringify(d.certified,null,2))}</pre></details>`:''}</details>
 ${savedFollowup(d)}${reportView(d)}${d.plan?`<p><a class="btn ghost" href="#/w/${encodeURIComponent(STATE.slug)}/act">Open the plan in Plan &amp; act</a></p>`:''}<details><summary>Original response</summary><pre>${esc(JSON.stringify(Object.assign({},d,{plan:d.plan?'(see Plan & act)':undefined}),null,1))}</pre></details></section>`;
}
function bindAnswerRecords(d){const a=d.answer,rows=answerRows(a,d),box=$('hostedRecords');if(!box)return;let page=0;
 const render=()=>{const query=$('hostedRecordSearch').value.trim().toLocaleLowerCase(),found=query?rows.filter(row=>JSON.stringify(row).toLocaleLowerCase().includes(query)):rows;
  box.innerHTML=ResultView.table(found,{...a,table:rows},{page,allColumns:$('hostedAllColumns').checked});$('hostedRecordStatus').textContent=fmt(found.length)+' of '+fmt(rows.length)+' records shown. Download includes every record.'};
 $('hostedRecordSearch').oninput=()=>{page=0;render()};$('hostedAllColumns').onchange=render;
 box.onclick=e=>{const toggle=e.target.closest('[data-record-toggle]'),pager=e.target.closest('[data-record-page]');if(toggle){const el=$(toggle.dataset.recordToggle);el.hidden=!el.hidden;toggle.setAttribute('aria-expanded',String(!el.hidden));toggle.textContent=el.hidden?'View details':'Hide details'}if(pager){page=Number(pager.dataset.recordPage);render()}};
 document.querySelectorAll('#ans [data-open-answer-tab]').forEach(b=>b.onclick=()=>{const section=$('hostedRecordSection');section.open=true;section.scrollIntoView({block:'start',behavior:'smooth'});$('hostedRecordSearch').focus({preventScroll:true})});
 $('hostedCSV').onclick=()=>{const blob=new Blob([answerCSV(a.table||rows)],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='wollstonecraft-results.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};render();
}
function answerState(d){const c=d.saved_context;
 const warning=d.context_error||(c&&(c.stale||c.invalidated||c.source_review_required||((c.source_disposition||{}).status==='unavailable'))?'Saved evidence needs review. This is the original answer; data or verification has changed.':'');
 return (warning?`<div class="notice err">${esc(warning)}</div>`:'')+ResultView.progress(c?.verification_progress||d.verification_progress)+(d.action_context?`<p class="small">Saved answer <span class="mono">${esc(d.action_context.id)}</span> <button class="chip" data-reopen="${esc(d.action_context.id)}">Reopen evidence</button></p>`:'')+
 (c?`<details><summary>Saved scope and source status</summary><pre>${esc(JSON.stringify({question:c.original_question,source:c.source,stale:c.stale,invalidated:c.invalidated,source_disposition:c.source_disposition,dependency_validation:c.dependency_validation,input_patient_ids:c.input_patient_ids,population_scopes:c.population_scopes},null,2))}</pre></details>`:'')}
function rememberConversation(d){if(d.conversation)STATE.conversationId=d.conversation.id;if(d.action_context)STATE.answerContext=d.action_context.id;else if(d.clarification)STATE.answerContext=null}
function clarificationView(c){return `<div class="notice"><p>${esc(c.reason)}</p><p class="small">Your choice clarifies the request; source evidence remains unchanged.</p>${c.options.map(o=>`<button class="chip" data-intent-option="${esc(o.id)}" title="${esc(o.meaning)}">${esc(o.label)}</button>`).join('')}<label for="intentText">Clarify your intent</label><input id="intentText" placeholder="Or describe what you mean"><button id="sendIntent" class="chip">Continue this analysis</button><p id="intentMessage" class="small"></p></div>`}
function bindClarification(d){const c=d.clarification;if(!c||c.status!=='pending')return;
 const send=async(reply)=>{const controls=[...document.querySelectorAll('#ans [data-intent-option],#sendIntent,#ask,#newQuestion')];controls.forEach(b=>b.disabled=true);const t0=Date.now();try{const out=await api(W('/ask'),{conversation_id:c.conversation_id,clarification_response:{id:c.id,...reply},interaction_mode:'interactive'});rememberConversation(out);$('ans').innerHTML=showAnswer(out,out.question||c.original_question,(Date.now()-t0)/1000);bindAnswer(out);loadHist()}catch(e){$('intentMessage').textContent=e.message}finally{controls.forEach(b=>b.disabled=false)}};
 document.querySelectorAll('#ans [data-intent-option]').forEach(b=>b.onclick=()=>send({option_id:b.dataset.intentOption}));$('sendIntent').onclick=()=>{const text=$('intentText').value.trim();if(text)send({text})}}
async function openConversation(id){try{const d=await api(W('/conversations/'+encodeURIComponent(id)));STATE.conversationId=id;STATE.answerContext=null;const last=d.turns[d.turns.length-1],a=last.answer_summary||{};if(a.action_context){await reopenAnswer(a.action_context.id);return}$('q').value=last.question;const out={...a,answer:null,question:last.question,conversation:{id},cost_usd:null};$('ans').innerHTML=showAnswer(out,last.question,0);bindAnswer(out)}catch(e){$('ans').innerHTML=`<div class="notice err">${esc(e.message)}</div>`}}
function reportView(d){if(!d.action_context)return '';const blocked=d.context_error||(d.saved_context&&(d.saved_context.stale||d.saved_context.invalidated||d.saved_context.source_review_required));
 return `<div class="card" style="margin-top:16px"><h3>Report from this evidence</h3><p class="small muted">Uses the saved answer, its sources and limitations. No new analysis or model request.</p>${ME.role!=='viewer'?`<button id="createSavedReport" class="chip" ${blocked?'disabled':''}>Create evidence brief</button>`:''}<button id="listSavedReports" class="chip">Saved reports</button><p id="reportMessage" class="small"></p><div id="boundReports"></div><div id="boundReportPreview"></div>${(d.workflow||[]).length?'<details><summary>Requested work</summary><pre>'+esc(JSON.stringify(d.workflow,null,2))+'</pre></details>':''}</div>`}
function bindReports(d){if(!d.action_context)return;const cid=d.action_context.id,base=W('/answers/'+encodeURIComponent(cid)),msg=$('reportMessage'),list=$('boundReports'),preview=$('boundReportPreview');if(!list)return;
 const show=out=>{preview.innerHTML=`<h3>${esc(out.title||'Saved report')}</h3><p class="small">${esc(out.status)} · ${esc(out.generation||'')} · ${out.new_paid_api_calls==null?'model calls unknown':fmt(out.new_paid_api_calls)+' new model calls'}</p>${out.html?'<iframe class="deliverable" sandbox="allow-scripts" title="Saved evidence report"></iframe>':'<p>No report is ready. Its original result has been retained.</p>'}`;if(out.html)preview.querySelector('iframe').srcdoc=out.html};
 const load=async()=>{try{const out=await api(base+'/deliverables');list.innerHTML=out.deliverables.map(r=>`<button class="chip" data-bound-report="${esc(r.id)}">${esc(r.title||r.id)} · ${esc(r.status)}</button>`).join('')||'<p class="small muted">No report has been requested for this answer.</p>';list.querySelectorAll('[data-bound-report]').forEach(b=>b.onclick=async()=>{try{show(await api(base+'/deliverables/'+encodeURIComponent(b.dataset.boundReport)))}catch(e){msg.textContent=e.message}})}catch(e){msg.textContent=e.message}};
 $('listSavedReports').onclick=load;const create=$('createSavedReport');if(create)create.onclick=async()=>{create.disabled=true;msg.textContent='Rendering the saved evidence…';try{const out=await api(base+'/report',{template:'brief',request:'Create an evidence review brief of this saved answer, preserving its scope, sources and limitations.'});show(out);msg.textContent='Evidence brief saved. No new model request.';await load()}catch(e){msg.textContent=e.message}finally{create.disabled=false}}}
function savedFollowup(d){const ref=d.action_context,c=d.saved_context||ref;if(!ref||!c)return '';
 const scopes=[...(c.result_scope_available?[{id:'result',label:'Patients in this answer'}]:[]),...(c.input_patients!=null?[{id:'input',label:'Original input population'}]:[]),...(c.population_scopes||[]).map(s=>({id:'population:'+s.id,label:s.label+' · '+s.patients+' patients'}))];
 if(!scopes.length)return '';const blocked=c.stale||c.invalidated||c.source_review_required||(c.source_disposition||{}).status==='unavailable';
 return `<div class="card" style="margin-top:16px"><label for="savedq">Ask about this saved population</label><p class="small muted">Uses the same people and original evidence. List patients, count them, group by practice or payer, or exclude recorded deaths.</p><select id="savedScope" aria-label="Saved population">${scopes.map(s=>`<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('')}</select><input id="savedq" placeholder="Group them by practice" ${blocked?'disabled':''}><button id="savedFollowup" class="chip" ${blocked?'disabled':''}>Apply to saved population</button><span id="savedMsg" class="small muted">${blocked?'Refresh the source-bound answer before changing its population.':''}</span></div>`}
function bindAnswer(d){bindAnswerRecords(d);bindClarification(d);bindReports(d);document.querySelectorAll('#ans [data-reopen]').forEach(b=>b.onclick=()=>reopenAnswer(b.dataset.reopen));const b=$('savedFollowup');if(b)b.onclick=async()=>{const q=$('savedq').value.trim();if(!q)return;b.disabled=true;const t0=Date.now();try{const out=await api(W('/answers/'+encodeURIComponent(d.action_context.id)+'/followup'),{question:q,scope:$('savedScope').value});if(out.status==='clarify'){$('savedMsg').textContent=out.headline+' '+(out.suggestions||[]).join(' · ');return}rememberConversation(out);$('ans').innerHTML=showAnswer(out,q,(Date.now()-t0)/1000);bindAnswer(out);loadHist()}catch(e){$('savedMsg').textContent=e.message}finally{b.disabled=false}}}
async function reopenAnswer(id){const t0=Date.now();try{const d=await api(W('/answers/'+encodeURIComponent(id)));STATE.answerContext=id;$('q').value=d.question;$('ans').innerHTML=showAnswer(d,d.question,(Date.now()-t0)/1000);bindAnswer(d)}catch(e){$('ans').innerHTML=`<div class="notice err">${esc(e.message)}</div>`}}
async function loadHist(){try{const d=await api(W('/questions'));$('hist').innerHTML=d.questions.slice(0,10).map(r=>`<div class="row" style="padding:8px 0;border-bottom:1px solid var(--line)"><span style="flex:1;min-width:200px">${esc(clip(r.question,180))}</span><b class="mono">${esc(clip(r.answer?(r.answer.count??r.answer.value??(r.answer.patient_ids?r.answer.patient_ids.length+' patients':'table')):'no confident answer',60))}</b>${r.conversation?`<button class="chip" data-conversation="${esc(r.conversation.id)}">Open conversation</button>`:r.action_context?`<button class="chip" data-saved-answer="${esc(r.action_context.id)}">Open answer</button>`:''}</div>`).join('')||'None yet.';document.querySelectorAll('#hist [data-conversation]').forEach(b=>b.onclick=()=>openConversation(b.dataset.conversation));document.querySelectorAll('#hist [data-saved-answer]').forEach(b=>b.onclick=()=>reopenAnswer(b.dataset.savedAnswer))}catch(e){$('hist').textContent=e.message}}

// --- Plan & act ---------------------------------------------------------------------------------------------------------
function paneAct(w){
 $('pane').innerHTML=`<section class="section"><div class="wrap">${head('03','KNOWLEDGE','PLAN & ACT','Don’t stop<br>at a referral.','Route a cohort to the program that fits, then let agents reach out, enroll, schedule and follow through. Every step advances only on a receipt; exceptions come back to a person.')}
 <div class="card" style="margin-top:28px"><div class="grid g3">
 <div><label for="pk">Program</label><select id="pk">${APP.programs.map(p=>`<option value="${esc(p.kind)}">${esc(p.label)}</option>`).join('')}</select><p class="muted small" id="pkd"></p></div>
 <div><label for="pq">Cohort question (optional)</label><input id="pq" placeholder="Leave blank to use the program's own gap definition"></div>
 <div><label for="pthrough">Receipts through (optional)</label><input id="pthrough" placeholder="YYYY-MM-DD"></div></div>
 <div class="row"><button id="runplan" class="accent">Run the plan</button><span id="planmsg" class="muted"></span></div></div>
 <div id="planout"></div><div style="margin-top:36px"><p class="eyebrow">SAVED DECISIONS</p><div id="decs" class="muted">Loading…</div></div></div></section>`;
 const desc=()=>{const p=APP.programs.find(x=>x.kind===$('pk').value);$('pkd').textContent=p?p.detail:''};$('pk').onchange=desc;desc();
 $('runplan').onclick=runPlan;loadDecisions()}
async function runPlan(){const kind=$('pk').value,q=$('pq').value.trim(),th=$('pthrough').value.trim();$('runplan').disabled=true;
 $('planmsg').innerHTML='<span class="spin"></span> Routing the cohort and running the agents…';
 try{const body={program_kind:kind};if(q)body.question=q;if(th)body.through=th;const d=await api(W('/plan'),body);renderPlan(d.plan,d.headline);loadDecisions()}
 catch(e){$('planout').innerHTML=`<div class="notice err">${esc(e.message)}</div>`}finally{$('runplan').disabled=false;$('planmsg').textContent=''}}
async function loadDecisions(){try{const d=await api(W('/decisions'));$('decs').innerHTML=d.decisions.length?`<div class="tablewrap"><table><tr><th>Decision</th><th>Program</th><th>As of</th><th class="r">Patients</th><th class="r">Work items</th><th></th></tr>${d.decisions.slice(0,20).map(x=>`<tr><td class="mono">${esc(String(x.id).slice(0,12))}</td><td>${esc(label(x.program_kind))}</td><td class="mono">${esc(x.as_of)}</td><td class="r mono">${fmt(x.patients)}</td><td class="r mono">${fmt(x.work_items)}</td><td><button class="ghost chip" data-dec="${esc(x.id)}">Open</button></td></tr>`).join('')}</table></div>`:'No plans yet.';
 document.querySelectorAll('[data-dec]').forEach(b=>b.onclick=async()=>{const x=await api(W('/decisions/'+encodeURIComponent(b.dataset.dec)));renderPlan({program_kind:x.decision.program_kind,as_of:x.decision.as_of,summary:{patients:x.decision.patients,by_route:x.decision.routes||{}},execution:Object.assign({decision_id:x.decision.id,follow_on:x.follow_on},x)},'Saved decision '+String(x.decision.id).slice(0,12))})}catch(e){$('decs').textContent=e.message}}
function funnelRows(s){const o=s.outcomes||{},f=Object.fromEntries((s.funnel||[]).map(x=>[x.stage,x.patients]));
 return [['Routed to a program',f.routed],['Reached by the agent',o.reached??f.reached],['Declined',o.declined,1],['Denied (eligibility, coverage, or appeal upheld)',o.denied,1],
  ['Prior auth requested',o.prior_auth_requested,1],['Appeal overturned',o.appeal_overturned,1],['Enrolled',o.enrolled??f.enrolled],['First visit attended',o.first_visit_attended??f.first_visit_attended],['Completed',o.completed??f.completed]].filter(r=>r[1]!=null)}
function renderPlan(plan,headline){const ex=plan.execution||{},s=ex.summary||{};const rows=funnelRows(s);const max=Math.max(1,...rows.map(r=>r[1]||0));
 const comp=ex.completion||{},fol=ex.follow_on||null,kind=plan.program_kind;
 $('planout').innerHTML=`<section class="card" style="margin-top:24px"><p class="eyebrow">DECISION ${esc(String(ex.decision_id||'').slice(0,12))} ${ex.replay?'· <b>REPLAY</b> (idempotent: nothing changed)':''}</p>
 <h3>${esc(headline||'')}</h3>
 <div class="grid g3" style="margin:18px 0"><div><p class="eyebrow">PATIENTS</p><div class="kpi">${fmt((plan.summary||{}).patients)}</div><p class="muted small">${esc(Object.entries((plan.summary||{}).by_route||{}).map(([k,v])=>label(k)+' '+v).join(' · '))}</p></div>
 <div><p class="eyebrow">WORK ITEMS · RECEIPTS</p><div class="kpi">${fmt(ex.work_items??s.actions)} · ${fmt(s.receipts)}</div><p class="muted small">steps done without a receipt: ${fmt(s.steps_done_without_receipt)}</p></div>
 <div><p class="eyebrow">CLOSED LOOP</p><div class="kpi">${comp.evaluated?fmt(comp.closed)+' / '+fmt(comp.evaluated):'—'}</div><p class="muted small">${comp.closure_rate!=null?'closure rate '+pct(comp.closure_rate):'evaluated against workspace data'}</p></div></div>
 <p class="eyebrow">AGENT FUNNEL</p><div class="funnel">${rows.map(r=>`<div class="bar"><span>${esc(r[0])}</span><i class="${r[2]?'alt':''}" style="width:${Math.max(1,Math.round(100*(r[1]||0)/max))}%"></i><span class="n">${fmt(r[1])}</span></div>`).join('')}</div>
 ${fol?followOnBlock(fol):''}
 <div class="grid g2" style="margin-top:22px"><div><p class="eyebrow">EXCEPTIONS THAT NEED A PERSON</p><div id="exc" class="muted">Loading…</div></div>
 <div><p class="eyebrow">PATIENT JOURNEYS</p><div id="jour" class="muted">Loading…</div></div></div>
 <div id="journey"></div></section>`;
 loadWork(kind)}
function followOnBlock(f){const bp=f.by_program||{};const names={gdmt_titration_pathway:['06 / WISDOM / TITRATE','A care plan that keeps moving.'],hf_remote_monitoring_home_labs:['07 / WISDOM / MONITOR','Bring care home. Keep the connection.']};
 return `<div class="grid g2" style="margin-top:22px">${Object.entries(bp).map(([k,v])=>`<div class="card" style="background:var(--paper-2)"><p class="eyebrow">${esc((names[k]||[k])[0])}</p><h3>${esc((names[k]||['',label(k)])[1])}</h3>
 <p class="muted small">${fmt(v.actions)} patients · ${esc(Object.entries(v.states||{}).map(([s,n])=>label(s)+' '+n).join(' · '))}</p>
 <p class="mono small" style="margin:0">${esc(Object.entries(v.milestones||{}).map(([s,n])=>label(s)+' '+n).join(' · '))}</p></div>`).join('')}</div>
 <p class="muted small">Follow-on work starts after PharmD enrollment: ${fmt(f.followups_open)} follow-ups open · ${fmt(f.receipts)} receipts.</p>`}
async function loadWork(kind){try{const lane=encodeURIComponent(kind);
 const [fu,pi]=await Promise.all([api(W('/work?program_kind='+lane+'&kind=followup&state=open&limit=60')),api(W('/work?program_kind='+lane+'&limit=5000'))]);
 const owners={team:'Care team',care_team:'Care team',practice:'Attributed practice',vendor_slot:'Program'};
 $('exc').innerHTML=fu.items.length?`<div class="tablewrap"><table><tr><th>Patient</th><th>Exception</th><th>Owner</th><th>Due</th></tr>${fu.items.slice(0,25).map(i=>`<tr><td class="mono"><a href="#" data-pat="${esc(i.patient_id)}">${esc(String(i.patient_id).slice(0,16))}</a></td><td>${esc(label(i.type))}<br><span class="muted small">${esc(i.reason||label(i.trigger))}</span></td><td class="small">${esc(owners[(i.owner||{}).kind]||(i.owner||{}).kind||'')}</td><td class="mono small">${esc(i.due||'')}</td></tr>`).join('')}</table></div><p class="muted small">${fmt(fu.total)} open.</p>`:'<p class="muted">No open exceptions.</p>';
 const plans=pi.items.filter(i=>i.kind==='plan_item');
 $('jour').innerHTML=plans.length?`<div class="tablewrap"><table><tr><th>Patient</th><th>Program</th><th>State</th><th>Last receipt</th></tr>${plans.slice(0,25).map(i=>`<tr><td class="mono"><a href="#" data-item="${esc(i.id)}">${esc(String(i.patient_id).slice(0,16))}</a></td><td class="small">${esc(label(i.program_id))}</td><td><span class="pill ${/completed|enrolled|attended/.test(i.state)?'ok':(/declin|denied|unreach|drop/.test(i.state)?'bad':'warn')}">${esc(label(i.state))}</span></td><td class="small mono">${esc(i.last_receipt?label(i.last_receipt.outcome)+' · '+String(i.last_receipt.at).slice(0,10):'—')}</td></tr>`).join('')}</table></div><p class="muted small">Showing ${Math.min(25,plans.length)} of ${fmt(plans.length)} plan items${pi.counts&&pi.counts.follow_on?' · follow-on: '+esc(Object.entries(pi.counts.follow_on).map(([k,v])=>label(k)+' '+v).join(', ')):''}.</p>`:'<p class="muted">No work items yet.</p>';
 document.querySelectorAll('[data-item]').forEach(a=>a.onclick=e=>{e.preventDefault();journey(a.dataset.item)});
 document.querySelectorAll('[data-pat]').forEach(a=>a.onclick=async e=>{e.preventDefault();const it=plans.find(p=>p.patient_id===a.dataset.pat);if(it)journey(it.id)})}
 catch(e){$('exc').textContent=e.message;$('jour').textContent=''}}
async function journey(id){const d=await api(W('/work/'+encodeURIComponent(id)));const p=d.item;
 const all=await api(W('/work?patient_id='+encodeURIComponent(p.patient_id)));const fol=all.items.filter(i=>i.kind==='follow_on');
 const fdet=await Promise.all(fol.map(f=>api(W('/work/'+encodeURIComponent(f.id))).catch(()=>null)));
 const stepList=a=>`<div class="steps">${a.steps.map(s=>{const r=s.receipt;return `<div class="stp"><span class="dot ${esc(s.status)}"></span><div><b>${esc(label(s.step))}</b>${s.attempt>1?' <span class="muted mono small">attempt '+s.attempt+'</span>':''} — ${r?esc(label(r.outcome)):esc(label(s.status))}
  <div class="muted small">${r?esc(r.reason||'')+' · '+esc(r.counterparty||r.counterparty_id)+' · <span class="mono">'+esc(String(r.at).replace('T',' ').slice(0,16))+' · '+esc(r.receipt_id)+'</span>':esc(s.note||'')}</div>
  ${r&&r.detail&&s.step==='dose_step_proposal'?'<div class="small mono">'+esc((r.detail.proposal||{}).agent||'')+' '+esc((r.detail.proposal||{}).from||'start')+' → '+esc((r.detail.proposal||{}).to||'')+'</div>':''}
  ${r&&r.detail&&s.step==='safety_check'?'<div class="small mono">'+esc(Object.entries(r.detail.measures||{}).map(([k,v])=>label(k)+' '+v.value+' ('+v.date+')').join(' · '))+(r.detail.holds&&r.detail.holds.length?'<br>holds: '+esc(r.detail.holds.map(h=>h.pillar+': '+h.reason).join('; ')):'')+'</div>':''}
  ${r&&r.detail&&s.step==='readings_window'?'<div class="small mono">'+esc(`${r.detail.transmission_days}/${r.detail.days} days · weight Δ7d ${r.detail.weight_change_kg_7d} kg · SBP ${r.detail.systolic_bp_min}–${r.detail.systolic_bp_max} · HR ${r.detail.heart_rate_min}–${r.detail.heart_rate_max}`)+'</div>':''}
  </div></div>`}).join('')}</div>`;
 const fus=(d.followup_items||[]).concat(...fdet.filter(Boolean).map(x=>x.followup_items||[]));
 $('journey').innerHTML=`<div class="card" style="margin-top:24px"><p class="eyebrow">PATIENT JOURNEY / <b>${esc(p.patient_id)}</b></p><h3>${esc(label(p.action.program))} — ${esc(label(p.state))}</h3>
 <p class="muted small">${esc(p.action.agent||'')} · ${esc(p.action.vendor||'')} · cycle ${fmt(p.cycle)} · revision ${fmt(p.revision)}${p.closure?' · closure: '+esc(p.closure.status)+' ('+esc(p.closure.source)+')':''}</p>
 ${(p.action.barriers||[]).length?'<p class="small">Barriers: '+esc(p.action.barriers.map(b=>b.pillar+' '+label(b.barrier_type)).join('; '))+'</p>':''}
 ${stepList(p.action)}
 ${fdet.filter(Boolean).map(x=>`<p class="eyebrow" style="margin-top:22px">FOLLOW-ON / <b>${esc(label(x.item.action.program))}</b></p><p class="muted small">${esc(x.item.action.agent||'')} · started ${esc(x.item.action.start)} · ${esc(label(x.item.state))}</p>${stepList(x.item.action)}`).join('')}
 ${fus.length?`<p class="eyebrow" style="margin-top:22px">FOLLOW-UP WORK</p><ul>${fus.map(f=>`<li>${esc(label(f.type))} — ${esc(label(f.state))} <span class="muted small">${esc(f.reason||'')} · due ${esc(f.due||'')}</span></li>`).join('')}</ul>`:''}
 <p class="muted small">Events: ${esc((d.events||[]).map(e=>e.action).join(' → '))}</p></div>`;
 $('journey').scrollIntoView({behavior:'smooth',block:'start'})}

// --- Simulate -----------------------------------------------------------------------------------------------------------
function paneSim(w){
 $('pane').innerHTML=`<section class="section"><div class="wrap">${head('04','WISDOM','SIMULATE','Test the plan<br>before you act.','Run the routed plan against a simulated copy of this population, beside each alternative and doing nothing. Intervals carry parameter uncertainty; every parameter shows its source.')}
 <div class="card" style="margin-top:28px"><div class="grid g3">
 <div><label for="sk">Program</label><select id="sk">${APP.programs.filter(p=>p.kind!=='coverage_check').map(p=>`<option value="${esc(p.kind)}">${esc(p.label)}</option>`).join('')}</select></div>
 <div><label for="sh">Horizon (months)</label><input id="sh" type="number" min="1" max="60" value="12"></div>
 <div><label for="si">Iterations</label><input id="si" type="number" min="20" max="2000" value="200"></div></div>
 <div class="row"><button id="runsim" class="accent">Run the simulation</button><span id="simmsg" class="muted"></span></div></div><div id="simout"></div></div></section>`;
 $('runsim').onclick=runSim}
const SIMCOLS=[['admissions','Admissions',v=>fmt(Math.round(v*10)/10)],['medical_cost','Medical cost',money],['admissions_avoided','Admissions avoided',v=>fmt(Math.round(v*10)/10)],
 ['ed_visits_avoided','ED visits avoided',v=>fmt(Math.round(v*10)/10)],['net_savings','Net savings',money],['program_cost','Program cost',money],['pharmacy_cost_added','Added pharmacy cost',money],
 ['shared_savings_change','Shared-savings change',money],['p_net_savings_positive','P(net > 0)',pct]];
function interval(v,f){if(v==null)return '—';if(typeof v!=='object')return f(v);const m=v.mean??v.p50??v.value;const lo=v.p05,hi=v.p95;
 return `<span class="mono">${f(m)}</span>${lo!=null&&hi!=null?`<br><span class="muted small mono" style="white-space:nowrap">${f(lo)} – ${f(hi)}</span>`:''}`}
async function runSim(){$('runsim').disabled=true;$('simmsg').innerHTML='<span class="spin"></span> Simulating the population…';
 try{const d=await api(W('/simulate'),{program_kind:$('sk').value,horizon_months:+$('sh').value||12,iterations:+$('si').value||200});renderSim(d)}
 catch(e){$('simout').innerHTML=`<div class="notice err">${esc(e.message)}</div>`}finally{$('runsim').disabled=false;$('simmsg').textContent=''}}
function oapBlock(d,rows){const terms=APP.oap||{};const out=[];
 rows.forEach(r=>(r.programs||[]).forEach(pid=>{const t=terms[pid];if(!t||!r.funnel)return;const f=r.funnel,en=f.enrolled||0,co=f.completed||0;
  const share=en?co*(t.p_target_met_if_completed||0)/en:0,paid=(1-t.withheld_share)+t.withheld_share*Math.min(1,share/t.outcome_attainment_threshold);
  out.push(`<tr><td>${esc(r.label)}</td><td class="r mono">${fmt(Math.round(en*10)/10)}</td><td class="r mono">${fmt(Math.round(co*10)/10)}</td><td class="r mono">${pct(share)}</td><td class="r mono">${pct(paid)}</td><td class="r mono">${money(en*t.annual_allowed*paid)}</td></tr>`)}));
 if(!out.length)return '';const t=Object.values(terms)[0];
 return `<p class="eyebrow" style="margin-top:26px">ACCESS OUTCOME-ALIGNED PAYMENT (OAP) ECONOMICS</p><div class="tablewrap"><table><tr><th>Arm</th><th class="r">Enrolled</th><th class="r">Completed</th><th class="r">Targets met / enrolled</th><th class="r">Share of OAP paid</th><th class="r">OAP allowed (est.)</th></tr>${out.join('')}</table></div>
 <p class="muted small">$${fmt(t.annual_allowed)} allowed per beneficiary per year; ${pct(t.withheld_share)} withheld and reconciled against the ${pct(t.outcome_attainment_threshold)} outcome attainment threshold; targets met by ${pct(t.p_target_met_if_completed)} of completers (assumption). OAP payments are inside program cost above; source: ${esc(t.source||'simulator config')}.</p>`}
function renderSim(d){const rows=(d.summary_table||[]);const cols=SIMCOLS.filter(c=>rows.some(r=>r[c[0]]!=null));
 $('simout').innerHTML=`<section class="card" style="margin-top:24px"><p class="eyebrow">SIMULATION ${esc(d.id||'')} · ${esc(d.interval||'')}</p>
 <div class="grid g3" style="margin:12px 0 18px"><div><p class="eyebrow">COHORT</p><div class="kpi">${fmt((d.cohort||{}).patients)}</div><p class="muted small">${esc((d.cohort||{}).definition||'')}</p></div>
 <div><p class="eyebrow">HORIZON</p><div class="kpi">${fmt(d.horizon_months)} mo</div><p class="muted small">${esc(d.as_of||'')} → ${esc(d.horizon_end||'')} · ${fmt(d.iterations)} draws</p></div>
 <div><p class="eyebrow">CONTRACT</p><div class="kpi">${esc((d.contract||{}).program||'—')}</div><p class="muted small">${d.contract?'MSR '+pct(d.contract.msr)+' · sharing '+pct(d.contract.sharing_rate)+' · '+fmt(d.contract.population)+' attributed':''}</p></div></div>
 <div class="tablewrap"><table><tr><th>Arm</th>${cols.map(c=>`<th class="r">${esc(c[1])}</th>`).join('')}<th class="r">Enrolled → completed</th></tr>${rows.map(r=>`<tr><td><b>${esc(r.label||r.arm)}</b></td>${cols.map(c=>`<td class="r">${interval(r[c[0]],c[2])}</td>`).join('')}<td class="r mono">${r.funnel?fmt(Math.round(r.funnel.enrolled*10)/10)+' → '+fmt(Math.round(r.funnel.completed*10)/10):'—'}</td></tr>`).join('')}</table></div>
 <p class="muted small">Mean, with the 90% interval beneath.</p>
 ${oapBlock(d,rows)}
 ${d.narrative?`<p style="margin-top:18px">${esc(d.narrative)}</p>`:''}
 <details><summary>Parameters and sources</summary><div class="tablewrap"><table><tr><th>Parameter</th><th class="r">Value</th><th>Kind</th><th>Source</th></tr>${(d.parameters||[]).map(p=>`<tr><td class="mono small">${esc(p.id)}</td><td class="r mono">${fmt(p.value)}${p.range?'<br><span class="muted small">'+esc(p.range.join(' – '))+'</span>':''}</td><td class="small">${esc(p.kind||'')}</td><td class="small">${esc(p.source||p.why||p.outcome||'')}</td></tr>`).join('')}</table></div></details>
 <details><summary>Calibration and assumptions</summary><pre>${esc(JSON.stringify({calibration:d.calibration,assumptions:d.assumptions},null,1).slice(0,30000))}</pre></details></section>`}

// --- Create -------------------------------------------------------------------------------------------------------------
async function paneCreate(w){
 $('pane').innerHTML=`<section class="section"><div class="wrap">${head('05','KNOWLEDGE','CREATE','Make me anything.','Describe what you need: a war-room view, pharmacist worklist cards, a board one-pager. The composer runs the questions, plans and simulations it needs and returns a verified, self-contained page.')}
 <div id="cr" style="margin-top:28px" class="muted">Checking the composer…</div></div></section>`;
 let online=true,saved=[];try{saved=(await api(W('/deliverables'))).deliverables||[]}catch(e){if(e.status===404&&/route/i.test(e.message))online=false}
 if(!online){$('cr').innerHTML=`<div class="card"><p class="eyebrow">COMING ONLINE</p><h3>The composer is being connected to this workspace.</h3><p class="muted">Ask, Plan & act and Simulate work now. Create will open here as soon as the deliverable service is live.</p></div>`;return}
 $('cr').innerHTML=`<div class="card"><label for="dreq" class="eyebrow" style="margin:0 0 8px">WHAT SHOULD WE MAKE?</label><textarea id="dreq" rows="3" placeholder="A one-page HF war room for the CMO: who needs GDMT, where the plan stands, and the savings range"></textarea>
 <label for="daud">Audience (optional)</label><input id="daud" placeholder="CMO, CFO, PharmD team…">
 <div class="row"><button id="dgo" class="accent">Create</button><span id="dmsg" class="muted"></span></div>
 <p class="eyebrow" style="margin:16px 0 2px">OR START FROM A REFERENCE EXAMPLE</p>${APP.templates.map(t=>`<button class="chip" data-t="${esc(t.template)}">${esc(t.label)}</button>`).join('')}</div>
 <div id="dout"></div><div style="margin-top:30px"><p class="eyebrow">SAVED</p><div id="dsaved" class="muted">${saved.length?'':'None yet.'}</div></div>`;
 const list=s=>{$('dsaved').innerHTML=s.length?s.slice(0,20).map(x=>`<div class="row" style="padding:6px 0;border-bottom:1px solid var(--line)"><a href="#" data-d="${esc(x.id)}" style="flex:1">${esc(x.title||x.request||x.template||x.id)}</a><span class="pill ${x.status==='delivered'?'ok':'warn'}">${esc(x.status||'')}</span></div>`).join(''):'None yet.';
  document.querySelectorAll('[data-d]').forEach(a=>a.onclick=async e=>{e.preventDefault();showDeliverable(await api(W('/deliverables/'+encodeURIComponent(a.dataset.d))))})};list(saved);
 const go=async body=>{$('dgo').disabled=true;$('dmsg').innerHTML='<span class="spin"></span> Composing and verifying…';
  try{const d=await api(W('/deliverable'),body);showDeliverable(d)}catch(e){if(e.status===422&&e.body)showDeliverable(e.body);else if(e.status===404)$('dout').innerHTML='<div class="notice">The composer is coming online for this workspace.</div>';else $('dout').innerHTML=`<div class="notice err">${esc(e.message)}</div>`}
  finally{$('dgo').disabled=false;$('dmsg').textContent='';api(W('/deliverables')).then(x=>list(x.deliverables||[])).catch(()=>{})}};
 $('dgo').onclick=()=>{const r=$('dreq').value.trim();if(r)go({request:r,audience:$('daud').value.trim()||null})};
 document.querySelectorAll('[data-t]').forEach(b=>b.onclick=()=>go({template:b.dataset.t}))}
function showDeliverable(d){const ver=d.verification||{};
 $('dout').innerHTML=`<section class="card" style="margin-top:24px"><p class="eyebrow">${esc(d.status||'')} · ${esc(d.id||'')}</p>${d.title?`<h3>${esc(d.title)}</h3>`:''}
 ${d.html?'<iframe class="deliverable" sandbox="allow-scripts" title="Deliverable" id="dframe"></iframe>':'<p class="muted">No page was produced.</p>'}
 <div class="row"><button class="ghost" id="ddl" ${d.html?'':'disabled'}>Download HTML</button><span class="muted small">${(d.costs||{}).total_usd!=null?'cost $'+Number(d.costs.total_usd).toFixed(3):''}</span></div>
 <details><summary>Verification</summary><pre>${esc(JSON.stringify(ver,null,1))}</pre></details></section>`;
 if(d.html){$('dframe').srcdoc=d.html;$('ddl').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([d.html],{type:'text/html'}));a.download=(d.id||'deliverable')+'.html';a.click()}}}

// --- Physician value ----------------------------------------------------------------------------------------------------
async function paneValue(w){
 $('pane').innerHTML=`<section class="forest section"><div class="wrap">${head('06','VBC 3.0','PHYSICIAN VALUE','What I earned.<br>Why I earned it.<br>When I get paid.','Completed, receipted care steps build into rewards for the attributed physician and care team, with milestone bonuses for seeing the pathway through.')}
 <p class="notice" style="background:rgba(238,238,232,.06);color:#eeeee8">Illustrative amounts. Rates are configurable (config/agere/physician_value.json); they are not contracted terms and nothing here is a payment.</p>
 <div id="vk" class="grid g3" style="margin-top:20px"></div></div></section>
 <section class="section"><div class="wrap"><div class="row"><label for="vnpi" style="margin:0">Physician (NPI)</label><select id="vnpi" style="max-width:280px"><option value="">All physicians</option></select></div><div id="vout" class="muted" style="margin-top:20px">Loading…</div></div></section>`;
 const load=async npi=>{try{const d=await api(W('/value'+(npi?'?npi='+encodeURIComponent(npi):'')));renderValue(d,npi)}catch(e){$('vout').innerHTML=`<div class="notice err">${esc(e.message)}</div>`}};
 $('vnpi').onchange=()=>load($('vnpi').value);load('')}
function renderValue(d,npi){const t=d.totals||{};
 $('vk').innerHTML=[['EXAMPLE REWARDS',money(t.earned)],['ACTION REWARDS',money(t.action_rewards)],['MILESTONE BONUSES',money(t.milestone_bonuses)],['PATIENTS · PHYSICIANS',fmt(t.patients)+' · '+fmt(t.physicians)]].map(k=>`<div class="card"><p class="eyebrow">${k[0]}</p><div class="kpi">${k[1]}</div></div>`).join('');
 if(!npi&&$('vnpi').options.length<=1)(d.by_physician||[]).forEach(p=>{const o=document.createElement('option');o.value=p.npi;o.textContent='NPI '+p.npi+' — '+money(p.earned);$('vnpi').appendChild(o)});
 if(!t.credits){$('vout').innerHTML='<p class="muted">No receipted care steps yet. Run a plan in Plan & act; credits appear as agents and the care team complete steps.</p>';return}
 const r=d.rates||{};
 $('vout').innerHTML=`<div class="grid g2"><div><p class="eyebrow">WHAT I EARNED</p><div class="tablewrap"><table><tr><th>Step</th><th class="r">Count</th><th class="r">Amount</th></tr>${(d.by_step||[]).map(s=>`<tr><td>${esc(s.label)}</td><td class="r mono">${fmt(s.count)}</td><td class="r mono">${money(s.amount)}</td></tr>`).join('')}</table></div></div>
 <div><p class="eyebrow">WHEN I GET PAID</p><div class="tablewrap"><table><tr><th>Scheduled</th><th class="r">Amount</th></tr>${(d.schedule||[]).map(s=>`<tr><td class="mono">${esc(s.pays_on)}</td><td class="r mono">${money(s.amount)}</td></tr>`).join('')}</table></div><p class="muted small">${esc((r.pay_schedule||{}).label||'')}</p></div></div>
 <p class="eyebrow" style="margin-top:30px">WHY I EARNED IT</p><div class="tablewrap"><table><tr><th>Earned</th><th>Patient</th><th>Step</th><th>Why</th><th class="r">Amount</th><th>Pays</th><th>Receipt</th></tr>${(d.credits||[]).slice(0,20).map(c=>`<tr><td class="mono small">${esc(c.earned_on)}</td><td class="mono small">${esc(String(c.patient_id).slice(0,16))}</td><td>${esc(c.label)}${c.kind==='bonus'?' <span class="pill warn">bonus</span>':''}</td><td class="small">${esc(c.why)}</td><td class="r mono">${money(c.amount)}</td><td class="mono small">${esc(c.pays_on)}</td><td class="mono small">${esc(c.receipt_id)}</td></tr>`).join('')}</table></div>
 ${npi?'':`<p class="eyebrow" style="margin-top:30px">BY PHYSICIAN</p><div class="tablewrap"><table><tr><th>NPI</th><th class="r">Patients</th><th class="r">Actions</th><th class="r">Bonuses</th><th class="r">Total</th><th>Next payment</th></tr>${(d.by_physician||[]).slice(0,25).map(p=>`<tr><td class="mono">${esc(p.npi)}</td><td class="r mono">${fmt(p.patients)}</td><td class="r mono">${money(p.action_rewards)}</td><td class="r mono">${money(p.milestone_bonuses)}</td><td class="r mono"><b>${money(p.earned)}</b></td><td class="mono small">${esc((p.schedule[0]||{}).pays_on||'')} ${p.schedule[0]?money(p.schedule[0].amount):''}</td></tr>`).join('')}</table></div>`}
 <details style="margin-top:16px"><summary>Rates in use (illustrative)</summary><div class="tablewrap"><table><tr><th>#</th><th>Step</th><th>Credited when</th><th class="r">Amount</th></tr>${(r.steps||[]).map(s=>`<tr><td class="mono">${esc(s.order)}</td><td>${esc(s.label)}</td><td class="small">${esc(s.why)}</td><td class="r mono">${money(s.amount)}</td></tr>`).join('')}${(r.bonuses||[]).map(b=>`<tr><td></td><td>${esc(b.label)}</td><td class="small">${esc(b.after_steps)} steps complete for a patient</td><td class="r mono">${money(b.amount)}</td></tr>`).join('')}</table></div></details>
 <p class="muted small">${esc((d.limits||[]).join(' '))}</p>`}

// --- Data (connect, map, build) -----------------------------------------------------------------------------------------
const STEPS=['profile','propose','approve','build','raw','verify','docs'];
async function paneData(w){w=STATE.ws=await api(W(''));const steps=STEPS.map(s=>`<span class="pill ${w.steps&&w.steps[s]?'ok':''}">${s}</span>`).join(' ');
 const tbl=o=>o?Object.entries(o).filter(([,n])=>n).map(([t,n])=>`<tr><td class="mono small">${esc(t)}</td><td class="r mono">${Number(n).toLocaleString()}</td></tr>`).join(''):'';
 const failed=((w.steps_detail||{}).verify||{}).failed_checks||[];
 $('pane').innerHTML=`<section class="section"><div class="wrap">${head('07','DATA','CONNECT & MAP','Find the person<br>inside the records.','Every source mapped into one patient story. Answers work now; each mapping decision here sharpens the next rebuild.')}
 <div class="card" style="margin-top:28px"><p class="muted mono small">${esc(w.source)} · DATA AS OF ${esc(w.as_of||'—')}</p><div style="margin-top:10px">${steps}</div>${w.error?`<p class="err">${esc(w.error)}</p>`:''}
 <div class="row"><button class="ghost" id="rebuild" ${w.building?'disabled':''}>Rebuild with my decisions</button><button class="ghost" id="rerun" ${w.building?'disabled':''}>Re-profile from scratch</button>${w.building?'<span class="muted"><span class="spin"></span> building…</span>':''}</div></div>
 <div class="grid g2" style="margin-top:24px"><div class="card"><p class="eyebrow">MAPPING REVIEW</p><div id="review" class="muted">Loading…</div></div>
 <div class="card"><p class="eyebrow">CHECKS</p><p>${w.verification===true?'<span class="pill ok">all checks passed</span>':w.verification===false?'Checks to look at: '+esc(failed.join(', ')||'see detail'):'Not run yet.'}</p>
 <details><summary>Check detail</summary><pre>${esc(JSON.stringify(w.verification_detail,null,1))}</pre></details></div></div>
 <div class="grid g2" style="margin-top:24px"><div class="card"><p class="eyebrow">MODELED TABLES</p><div class="tablewrap"><table>${tbl(w.tables)||'<tr><td class="muted">—</td></tr>'}</table></div></div>
 <div class="card"><p class="eyebrow">RAW SOURCE TABLES (QUERYABLE AS-IS)</p><div class="tablewrap" style="max-height:420px;overflow:auto"><table>${tbl(w.raw_tables)||'<tr><td class="muted">—</td></tr>'}</table></div></div></div></div></section>`;
 $('rebuild').onclick=async()=>{await api(W('/rebuild'),{});paneData()};$('rerun').onclick=async()=>{await api(W('/run'),{});paneData()};
 loadReview();if(w.building||!['ready','failed'].includes(w.status))STATE.timer=setTimeout(()=>{if(location.hash.endsWith('/data'))paneData()},4000)}
async function loadReview(){const d=await api(W('/review'));const open=d.items.filter(i=>!i.decided);
 $('review').innerHTML=open.length?`<div class="tablewrap"><table><tr><th>Item</th><th>Why</th><th>Decision</th></tr>${open.slice(0,40).map(i=>`<tr><td class="mono small">${esc(i.id)}</td><td class="small">${esc(i.reason)}${(i.candidates||[]).length?'<br><span class="muted">options: '+esc(i.candidates.map(c=>c.column||c.table).join(', '))+'</span>':''}</td>
 <td><button class="chip" data-i="${esc(i.id)}" data-d="accept" ${i.current?'':'disabled'}>Accept</button><button class="chip" data-i="${esc(i.id)}" data-d="reject">Reject</button>
 ${(i.candidates||[]).length?`<select data-i="${esc(i.id)}" data-k="${i.kind==='table'?'source_table':'source'}"><option value="">Use instead…</option>${i.candidates.map(c=>`<option>${esc(c.column||c.table)}</option>`).join('')}</select>`:''}</td></tr>`).join('')}</table></div>`:'<p class="muted">Nothing open.</p>';
 document.querySelectorAll('#review button').forEach(b=>b.onclick=async()=>{await api(W('/decide'),{item:b.dataset.i,decision:b.dataset.d});loadReview()});
 document.querySelectorAll('#review select').forEach(s=>s.onchange=async()=>{if(!s.value)return;await api(W('/decide'),{item:s.dataset.i,decision:{[s.dataset.k]:s.value}});loadReview()})}
'''


def admin():
    body = '''<header class="top" id="orgbar"></header><section class="forest section"><div class="wrap"><p class="eyebrow">ADMIN / <b>ORGANIZATION</b></p>
<h2>Members, keys and credentials.</h2><p class="lead">Who can read, ask and act in this organization; API keys for automation; encrypted warehouse credentials; usage and audit.</p></div></section>
<section class="section"><div class="wrap grid g2">
<div class="card"><p class="eyebrow">MEMBERS</p><div id="members" class="muted">Loading…</div>
<div class="grid" style="grid-template-columns:2fr 1fr;gap:8px;align-items:end"><div><label for="mem">Email</label><input id="mem" type="email"></div>
<div><label for="role">Role</label><select id="role"><option>analyst</option><option>viewer</option><option>admin</option><option>owner</option></select></div></div>
<button id="addmem">Add member</button><p id="memmsg" class="muted"></p></div>
<div class="card"><p class="eyebrow">API KEYS</p><div id="keys" class="muted">Loading…</div>
<div class="grid" style="grid-template-columns:2fr 1fr;gap:8px;align-items:end"><div><label for="kname">Name</label><input id="kname" placeholder="ETL job"></div>
<div><label for="krole">Role</label><select id="krole"><option>analyst</option><option>viewer</option><option>admin</option></select></div></div>
<button id="addkey">Create key</button><p id="keymsg" class="muted"></p></div>
<div class="card"><p class="eyebrow">WAREHOUSE CREDENTIALS</p><p class="muted">Encrypted at rest. Workspaces refer to them by name; values are never shown again.</p>
<div id="creds" class="muted">Loading…</div>
<label for="cname">Name</label><input id="cname" placeholder="acme-snowflake">
<label for="ckind">Kind</label><select id="ckind"><option>snowflake</option><option>bigquery</option><option>databricks</option><option>postgres</option></select>
<label for="cvals">Values (one KEY=value per line)</label><textarea id="cvals" rows="5" placeholder="SNOWFLAKE_ACCOUNT=...&#10;SNOWFLAKE_USER=...&#10;SNOWFLAKE_PASSWORD=...&#10;SNOWFLAKE_WAREHOUSE=...&#10;SNOWFLAKE_ROLE=..."></textarea>
<button id="savecred">Save credentials</button><p id="credmsg" class="muted"></p></div>
<div class="card"><p class="eyebrow">USAGE</p><div id="usage" class="muted">Loading…</div></div>
</div><div class="wrap" style="margin-top:16px"><div class="card"><p class="eyebrow">AUDIT LOG</p><div id="audit" class="muted tablewrap">Loading…</div></div></div></section>'''
    script = r'''
const tbl=(rows,cols)=>rows&&rows.length?'<div class="tablewrap"><table><tr>'+cols.map(c=>'<th>'+esc(c[1])+'</th>').join('')+'</tr>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+esc(typeof c[0]==='function'?c[0](r):r[c[0]])+'</td>').join('')+'</tr>').join('')+'</table></div>':'<p class="muted">None yet.</p>';
async function refresh(){
 const m=await api('/api/admin/members');document.getElementById('members').innerHTML=tbl(m.members,[['email','Email'],['role','Role'],[r=>r.disabled?'disabled':'active','Status']]);
 const k=await api('/api/admin/keys');document.getElementById('keys').innerHTML=tbl(k.keys,[['name','Name'],['role','Role'],['prefix','Key'],['created_at','Created'],['last_used_at','Last used'],[r=>r.revoked?'revoked':'active','Status']]);
 const c=await api('/api/admin/credentials');document.getElementById('creds').innerHTML=tbl(c.credentials,[['name','Name'],['kind','Kind'],['updated_at','Updated']]);
 const u=(await api('/api/admin/usage')).usage||{};const bw=Object.entries(u.by_workspace||{}).map(([w,v])=>Object.assign({workspace:w},typeof v==='object'?v:{value:v}));
 const cost=u.cost_usd==null?'total unknown ($'+Number(u.known_cost_usd||0).toFixed(2)+' known; '+Number(u.unknown_cost_records||0)+' unknown records)':'$'+Number(u.cost_usd).toFixed(2);
 document.getElementById('usage').innerHTML='<div class="kpi">'+esc(u.records||0)+'</div><p class="muted">questions · '+cost+' model estimate · '+Math.round(u.seconds||0)+' s answering</p>'+(bw.length?tbl(bw,Object.keys(bw[0]).map(x=>[x,x])):'');
 const when=s=>s?new Date(s*1000).toLocaleString():'';
 const a=await api('/api/admin/audit');document.getElementById('audit').innerHTML=tbl((a.audit||[]).slice(0,100),[[r=>when(r.at),'When'],['user','User'],['action','Action'],[r=>JSON.stringify(r.detail||''),'Detail'],['ip','IP']]);}
document.getElementById('addmem').onclick=async()=>{const m=document.getElementById('memmsg');try{const d=await api('/api/admin/members',{email:document.getElementById('mem').value,role:document.getElementById('role').value});
 m.innerHTML='Added '+esc(d.email)+(d.temporary_password?'. Temporary password (shown once): <code>'+esc(d.temporary_password)+'</code>':'');refresh()}catch(e){m.textContent=e.message}};
document.getElementById('addkey').onclick=async()=>{const m=document.getElementById('keymsg');try{const d=await api('/api/admin/keys',{name:document.getElementById('kname').value,role:document.getElementById('krole').value});
 m.innerHTML='New key (shown once): <code>'+esc(d.key)+'</code>';refresh()}catch(e){m.textContent=e.message}};
document.getElementById('savecred').onclick=async()=>{const m=document.getElementById('credmsg');const vals={};document.getElementById('cvals').value.split('\n').forEach(l=>{const i=l.indexOf('=');if(i>0)vals[l.slice(0,i).trim()]=l.slice(i+1).trim()});
 try{await api('/api/admin/credentials',{name:document.getElementById('cname').value,kind:document.getElementById('ckind').value,values:vals});document.getElementById('cvals').value='';m.textContent='Saved.';refresh()}catch(e){m.textContent=e.message}};
loadMe().then(refresh).catch(e=>document.body.insertAdjacentHTML('beforeend','<p class="err wrap">'+esc(e.message)+'</p>'));'''
    return _page(f'{NAME} Admin', body, script)


def account():
    body = '''<header class="top" id="orgbar"></header><section class="section"><div class="wrap" style="max-width:520px"><p class="eyebrow">ACCOUNT / <b>PASSWORD</b></p>
<h2>Change password.</h2><div class="card" style="margin-top:24px">
<label for="cur">Current password</label><input id="cur" type="password" autocomplete="current-password">
<label for="new">New password (12+ characters)</label><input id="new" type="password" autocomplete="new-password">
<button id="go" class="accent">Change password</button><p id="msg" class="muted" role="status"></p></div></div></section>'''
    script = r'''document.getElementById('go').onclick=async()=>{const m=document.getElementById('msg');
 try{await api('/api/account/password',{current:document.getElementById('cur').value,new:document.getElementById('new').value});m.textContent='Password changed.'}
 catch(e){m.textContent=e.message}};loadMe();'''
    return _page(f'{NAME} Account', body, script)
