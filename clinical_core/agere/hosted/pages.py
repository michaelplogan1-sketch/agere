"""HTML for the hosted server: sign-in, the workspace app (the workspace UI with the organization bar), admin, account.

Pages are self-contained (no external assets) so the Content-Security-Policy can stay 'self'. Every page loads
/api/auth/me, keeps the session's CSRF token and sends it on state-changing requests; a 401 returns to /login.
"""
from __future__ import annotations

BASE_CSS = r'''
:root{--bg:#f6f8f9;--panel:#fff;--ink:#14212b;--muted:#5a6976;--line:#dbe2e7;--accent:#0c6b73;--bad:#a33;--ok:#1d7a3a}
@media (prefers-color-scheme:dark){:root{--bg:#0e1519;--panel:#151f25;--ink:#e4ecf0;--muted:#97a8b3;--line:#29363e;--accent:#4fb8c0;--bad:#f07070;--ok:#6cc47c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
a{color:var(--accent)}.wrap{max-width:1100px;margin:0 auto;padding:24px 16px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px}
h1{font-size:22px;margin:0 0 6px}h2{font-size:16px;margin:0 0 10px}label{display:block;font-size:13px;color:var(--muted);margin-top:10px}
input,select,textarea{width:100%;padding:9px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:inherit}
button{margin-top:12px;padding:9px 16px;border:0;border-radius:6px;background:var(--accent);color:#fff;font:inherit;cursor:pointer}
button.secondary{background:transparent;color:var(--accent);border:1px solid var(--accent)}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
.muted{color:var(--muted);font-size:13px}.err{color:var(--bad)}.ok{color:var(--ok)}code{font-size:12px;word-break:break-all}
.bar{display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
.bar .sp{flex:1}.pill{font-size:12px;border:1px solid var(--line);border-radius:10px;padding:1px 8px}
'''

SESSION_JS = r'''
let ME=null;
const _esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const _fetch=window.fetch.bind(window);
window.fetch=async(url,opt={})=>{opt=opt||{};const m=(opt.method||'GET').toUpperCase();
 if(m!=='GET'&&ME&&ME.csrf){opt.headers=Object.assign({},opt.headers||{},{'X-CSRF-Token':ME.csrf})}
 const r=await _fetch(url,opt);if(r.status===401&&!String(url).includes('/api/auth/login'))location.href='/login';return r};
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}:{});
 const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||r.status);return d}
async function loadMe(){ME=await api('/api/auth/me');const b=document.getElementById('orgbar');if(!b)return ME;
 const orgs=(ME.orgs||[]);b.innerHTML='<b>Agere</b><span class="pill">'+_esc(ME.org)+' · '+_esc(ME.role)+'</span>'+
 (orgs.length>1?'<select id="orgsel">'+orgs.map(o=>'<option value="'+_esc(o.slug)+'"'+(o.slug===ME.org?' selected':'')+'>'+_esc(o.name||o.slug)+'</option>').join('')+'</select>':'')+
 '<span class="sp"></span><a href="/app">Workspaces</a>'+(['admin','owner'].includes(ME.role)?'<a href="/admin">Admin</a>':'')+
 '<a href="/account">'+_esc((ME.user||{}).email||'Account')+'</a><button class="secondary" id="logout" style="margin:0">Sign out</button>';
 document.getElementById('logout').onclick=async()=>{await api('/api/auth/logout',{});location.href='/login'};
 const s=document.getElementById('orgsel');if(s)s.onchange=async()=>{await api('/api/auth/switch',{org:s.value});location.reload()};return ME}
'''


def _page(title, body, script=''):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><style>{BASE_CSS}</style></head><body>{body}<script>{SESSION_JS}const esc=_esc;{script}</script></body></html>')


def login():
    body = '''<div class="wrap" style="max-width:420px;margin-top:8vh"><div class="panel"><h1>Sign in to Agere</h1>
<p class="muted">Healthcare data analytics: connect your data, ask questions, get checked answers.</p>
<form id="f"><label for="email">Work email</label><input id="email" type="email" autocomplete="username" required>
<label for="password">Password</label><input id="password" type="password" autocomplete="current-password" required>
<button type="submit">Sign in</button><p id="msg" class="err" role="alert"></p></form></div>
<p class="muted">This environment accepts synthetic data only. Do not upload patient information.</p></div>'''
    script = r'''document.getElementById('f').onsubmit=async e=>{e.preventDefault();const m=document.getElementById('msg');m.textContent='';
 try{await api('/api/auth/login',{email:document.getElementById('email').value,password:document.getElementById('password').value});location.href='/app'}
 catch(err){m.textContent=err.message}}'''
    return _page('Agere Sign In', body, script)


def app():
    from ..workspace_api import page as workspace_page
    html = workspace_page()
    html = html.replace('<a href="/ask">Ask on the current warehouse</a>', '')
    html = html.replace('<body>', '<body><div class="bar" id="orgbar"></div>', 1)
    bar_css = ('<style>.bar{display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--line);'
               'background:var(--panel)}.bar .sp{flex:1}.bar a{color:var(--accent)}.bar button{margin:0}.pill{font-size:12px;border:1px solid var(--line);'
               'border-radius:10px;padding:1px 8px}</style>')
    html = html.replace('</head>', bar_css + '</head>', 1)
    # the session helper (CSRF on writes, 401 -> sign-in) runs before the workspace UI script
    html = html.replace('<script>', '<script>' + SESSION_JS + '\nloadMe().catch(()=>location.href="/login");\n', 1)
    html = html.replace('<label>Source</label><input id="source" placeholder="snowflake://DB/SCHEMA">',
                        '<label>Source</label><input id="source" placeholder="snowflake://DB/SCHEMA">'
                        '<label>Saved credentials (optional)</label><input id="credential" placeholder="name saved by an admin">', 1)
    html = html.replace("source:$('source').value,as_of:", "source:$('source').value,credential:($('credential')||{}).value||null,as_of:", 1)
    upload = ('<label for="upfile">Or upload extracts (.zip of CSV/X12/HL7/FHIR files, or a .sqlite/.csv file)</label>'
              '<input id="upfile" type="file" accept=".zip,.csv,.sqlite,.sqlite3,.db,.parquet,.json,.jsonl">'
              '<label for="uptype">Uploaded zip contains</label><select id="uptype"><option value="native">Native healthcare feeds (X12, HL7, NCPDP, FHIR, EHR extracts)</option>'
              '<option value="files">Plain tables (one file per table)</option></select>'
              '<p id="upmsg" class="muted" role="status"></p>')
    html = html.replace('<label>Data as of (optional)</label>', upload + '<label>Data as of (optional)</label>', 1)
    upload_js = r'''
document.getElementById('upfile').onchange=e=>{const f=e.target.files[0];if(!f)return;const m=document.getElementById('upmsg');
 const name=f.name.replace(/[^A-Za-z0-9._-]/g,'_');const x=new XMLHttpRequest();x.open('PUT','/api/uploads/'+encodeURIComponent(name));
 x.setRequestHeader('X-CSRF-Token',(ME||{}).csrf||'');x.upload.onprogress=ev=>{if(ev.lengthComputable)m.textContent='Uploading… '+Math.round(100*ev.loaded/ev.total)+'%'};
 x.onload=()=>{let d={};try{d=JSON.parse(x.responseText)}catch(_){}if(x.status!==200){m.textContent=d.error||('Upload failed ('+x.status+')');return}
  const s=d.sources||{};const pick=s[document.getElementById('uptype').value]||s.files||s.sqlite||Object.values(s)[0];
  document.getElementById('source').value=pick||'';if(!document.getElementById('name').value)document.getElementById('name').value=name.replace(/\.[^.]+$/,'');
  m.textContent='Uploaded '+name+(d.extracted_files?(' ('+d.extracted_files+' files)'):'')+'. Source filled in; press Connect and build.'};
 x.onerror=()=>{m.textContent='Upload failed'};m.textContent='Uploading…';x.send(f)};
'''
    html = html.replace('</script></body>', upload_js + '</script></body>', 1)
    return html


def admin():
    body = '''<div class="bar" id="orgbar"></div><div class="wrap">
<div class="panel"><h2>Members</h2><div id="members" class="muted">Loading…</div>
<div style="display:grid;grid-template-columns:2fr 1fr auto;gap:8px;align-items:end"><div><label for="mem">Email</label><input id="mem" type="email"></div>
<div><label for="role">Role</label><select id="role"><option>analyst</option><option>viewer</option><option>admin</option><option>owner</option></select></div>
<button id="addmem">Add member</button></div><p id="memmsg" class="muted"></p></div>
<div class="panel"><h2>API keys</h2><div id="keys" class="muted">Loading…</div>
<div style="display:grid;grid-template-columns:2fr 1fr auto;gap:8px;align-items:end"><div><label for="kname">Name</label><input id="kname" placeholder="ETL job"></div>
<div><label for="krole">Role</label><select id="krole"><option>analyst</option><option>viewer</option><option>admin</option></select></div>
<button id="addkey">Create key</button></div><p id="keymsg" class="muted"></p></div>
<div class="panel"><h2>Warehouse credentials</h2><p class="muted">Encrypted at rest. Workspaces refer to them by name; values are never shown again.</p>
<div id="creds" class="muted">Loading…</div>
<label for="cname">Name</label><input id="cname" placeholder="acme-snowflake">
<label for="ckind">Kind</label><select id="ckind"><option>snowflake</option><option>bigquery</option><option>databricks</option><option>postgres</option></select>
<label for="cvals">Values (one KEY=value per line)</label><textarea id="cvals" rows="5" placeholder="SNOWFLAKE_ACCOUNT=...&#10;SNOWFLAKE_USER=...&#10;SNOWFLAKE_PASSWORD=...&#10;SNOWFLAKE_WAREHOUSE=...&#10;SNOWFLAKE_ROLE=..."></textarea>
<button id="savecred">Save credentials</button><p id="credmsg" class="muted"></p></div>
<div class="panel"><h2>Usage</h2><div id="usage" class="muted">Loading…</div></div>
<div class="panel"><h2>Audit log</h2><div id="audit" class="muted">Loading…</div></div></div>'''
    script = r'''
const tbl=(rows,cols)=>rows&&rows.length?'<table><tr>'+cols.map(c=>'<th>'+esc(c[1])+'</th>').join('')+'</tr>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+esc(typeof c[0]==='function'?c[0](r):r[c[0]])+'</td>').join('')+'</tr>').join('')+'</table>':'<p class="muted">None yet.</p>';
async function refresh(){
 const m=await api('/api/admin/members');document.getElementById('members').innerHTML=tbl(m.members,[['email','Email'],['role','Role'],[r=>r.disabled?'disabled':'active','Status']]);
 const k=await api('/api/admin/keys');document.getElementById('keys').innerHTML=tbl(k.keys,[['name','Name'],['role','Role'],['prefix','Key'],['created_at','Created'],['last_used_at','Last used'],[r=>r.revoked?'revoked':'active','Status']]);
 const c=await api('/api/admin/credentials');document.getElementById('creds').innerHTML=tbl(c.credentials,[['name','Name'],['kind','Kind'],['updated_at','Updated']]);
 const u=(await api('/api/admin/usage')).usage||{};const bw=Object.entries(u.by_workspace||{}).map(([w,v])=>Object.assign({workspace:w},typeof v==='object'?v:{value:v}));
 document.getElementById('usage').innerHTML='<p>'+esc(u.records||0)+' questions · $'+Number(u.cost_usd||0).toFixed(2)+' model cost · '+Math.round(u.seconds||0)+' s answering</p>'+(bw.length?tbl(bw,Object.keys(bw[0]).map(x=>[x,x])):'');
 const when=s=>s?new Date(s*1000).toLocaleString():'';
 const a=await api('/api/admin/audit');document.getElementById('audit').innerHTML=tbl((a.audit||[]).slice(0,100),[[r=>when(r.at),'When'],['user','User'],['action','Action'],[r=>JSON.stringify(r.detail||''),'Detail'],['ip','IP']]);}
document.getElementById('addmem').onclick=async()=>{const m=document.getElementById('memmsg');try{const d=await api('/api/admin/members',{email:document.getElementById('mem').value,role:document.getElementById('role').value});
 m.innerHTML='Added '+esc(d.email)+(d.temporary_password?'. Temporary password (shown once): <code>'+esc(d.temporary_password)+'</code>':'');refresh()}catch(e){m.textContent=e.message}};
document.getElementById('addkey').onclick=async()=>{const m=document.getElementById('keymsg');try{const d=await api('/api/admin/keys',{name:document.getElementById('kname').value,role:document.getElementById('krole').value});
 m.innerHTML='New key (shown once): <code>'+esc(d.key)+'</code>';refresh()}catch(e){m.textContent=e.message}};
document.getElementById('savecred').onclick=async()=>{const m=document.getElementById('credmsg');const vals={};document.getElementById('cvals').value.split('\n').forEach(l=>{const i=l.indexOf('=');if(i>0)vals[l.slice(0,i).trim()]=l.slice(i+1).trim()});
 try{await api('/api/admin/credentials',{name:document.getElementById('cname').value,kind:document.getElementById('ckind').value,values:vals});document.getElementById('cvals').value='';m.textContent='Saved.';refresh()}catch(e){m.textContent=e.message}};
loadMe().then(refresh).catch(e=>document.body.insertAdjacentHTML('beforeend','<p class="err wrap">'+esc(e.message)+'</p>'));'''
    return _page('Agere Admin', body, script)


def account():
    body = '''<div class="bar" id="orgbar"></div><div class="wrap" style="max-width:480px"><div class="panel"><h2>Change password</h2>
<label for="cur">Current password</label><input id="cur" type="password" autocomplete="current-password">
<label for="new">New password (12+ characters)</label><input id="new" type="password" autocomplete="new-password">
<button id="go">Change password</button><p id="msg" class="muted" role="status"></p></div></div>'''
    script = r'''document.getElementById('go').onclick=async()=>{const m=document.getElementById('msg');
 try{await api('/api/account/password',{current:document.getElementById('cur').value,new:document.getElementById('new').value});m.textContent='Password changed.'}
 catch(e){m.textContent=e.message}};loadMe();'''
    return _page('Agere Account', body, script)
