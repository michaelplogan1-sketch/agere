"""The artifact runtime: what every deliverable page carries so its numbers come from data and resolve to receipts.

wrap(body_html, bundle, title) produces one self-contained HTML document:
  * <script id="agere-data" type="application/json"> = the verified bundle (results + receipts), injected here, never
    written by a model;
  * a small runtime (window.AGERE): get(path), fmt(value, kind), bind(root) fills every element carrying
    data-bind="R-xxxxxxxxxx:path.to.value" (+ optional data-fmt) and marks it with its receipt, receipts() renders the
    receipt list (tool, definition, SQL fingerprint, as_of, data fingerprint), on(...) helpers for interactive pages;
  * light/dark tokens (prefers-color-scheme + a toggle, data-theme on <html>) and print rules.
No external network: no remote scripts, styles, fonts or images; write-back uses relative same-origin /api paths only.
"""
from __future__ import annotations

import html as htmlmod
import json

CSS = r"""
:root{--bg:#f7f7f4;--panel:#ffffff;--ink:#1d2327;--muted:#5d666d;--line:#dfe2e4;--accent:#0f766e;--accent-2:#b45309;
--good:#15803d;--bad:#b91c1c;--warn:#a16207;--chip:#eef2f1;--shadow:0 1px 2px rgba(0,0,0,.06),0 4px 14px rgba(0,0,0,.05);
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111416;--panel:#1a1f22;--ink:#e6e9ea;--muted:#9aa4aa;
--line:#2c3439;--accent:#2dd4bf;--accent-2:#f59e0b;--good:#4ade80;--bad:#f87171;--warn:#facc15;--chip:#232a2e;--shadow:none}}
:root[data-theme="dark"]{--bg:#111416;--panel:#1a1f22;--ink:#e6e9ea;--muted:#9aa4aa;--line:#2c3439;--accent:#2dd4bf;--accent-2:#f59e0b;
--good:#4ade80;--bad:#f87171;--warn:#facc15;--chip:#232a2e;--shadow:none}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--sans)}
a{color:var(--accent)}.agere-wrap{max-width:1180px;margin:0 auto;padding:20px 16px 48px}
.agere-top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px;color:var(--muted);font-size:12px}
.agere-theme{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:3px 8px;cursor:pointer;font-size:12px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;box-shadow:var(--shadow)}
.muted{color:var(--muted)}.mono{font-family:var(--mono)}.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 8px;font-size:12px}
[data-receipt]{text-decoration:underline dotted var(--muted);text-underline-offset:3px;cursor:help}
.agere-receipts{margin-top:28px;font-size:12px;color:var(--muted)}.agere-receipts summary{cursor:pointer;font-weight:600}
.agere-receipts table{border-collapse:collapse;width:100%;margin-top:8px}.agere-receipts td,.agere-receipts th{border-top:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}
.agere-receipts code{font-family:var(--mono);font-size:11px;word-break:break-all}
@media print{.agere-theme,.no-print{display:none!important}body{background:#fff;color:#000}.panel{box-shadow:none;break-inside:avoid}
.agere-receipts details{display:block}.agere-receipts details>*{display:block}}
"""

JS = r"""
(function(){
  var raw = document.getElementById('agere-data');
  var B = raw ? JSON.parse(raw.textContent) : {results:{}, receipts:{}, meta:{}};
  function get(path){
    var m = /^(R-[0-9a-f]{10})(?::(.*))?$/.exec(String(path).trim());
    if(!m || !(m[1] in B.results)) throw new Error('unresolved data path ' + path);
    var cur = B.results[m[1]], parts = (m[2] || '').split('.').filter(function(p){return p !== ''});
    for (var i = 0; i < parts.length; i++){ if (cur == null) throw new Error('unresolved data path ' + path); cur = cur[parts[i]]; }
    if (cur === undefined) throw new Error('unresolved data path ' + path);
    return cur;
  }
  function n(v){return typeof v === 'number' && isFinite(v)}
  function grp(x, d){return x.toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d})}
  function fmt(v, kind){
    if (v === null || v === undefined) return '—';
    if (!n(v)) return String(v);
    var s = v < 0 ? '-' : '', a = Math.abs(v);
    switch(kind || 'auto'){
      case 'int': return s + grp(Math.round(a), 0);
      case 'num1': return s + grp(a, 1);
      case 'num2': return s + grp(a, 2);
      case 'usd0': return s + '$' + grp(Math.round(a), 0);
      case 'usd': return s + '$' + grp(a, 2);
      case 'usdk': return a >= 1e6 ? s + '$' + grp(a / 1e6, 2) + 'M' : a >= 1e3 ? s + '$' + grp(a / 1e3, 1) + 'K' : s + '$' + grp(Math.round(a), 0);
      case 'pct0': return s + grp(a * 100, 0) + '%';
      case 'pct1': return s + grp(a * 100, 1) + '%';
      case 'pp': return s + grp(a * 100, 1) + ' pts';
      default: return Number.isInteger(v) ? s + grp(a, 0) : s + grp(a, Math.abs(v) < 10 ? 2 : 0);
    }
  }
  function recLabel(rid){
    var r = B.receipts[rid] || {};
    return rid + ' · ' + (r.tool || '') + (r.as_of ? ' · as of ' + r.as_of : '') + (r.sql_fingerprint ? ' · ' + r.sql_fingerprint : '') +
      (r.definition ? '\n' + String(r.definition).slice(0, 400) : '');
  }
  function mark(el, path){
    var rid = String(path).split(':')[0];
    el.setAttribute('data-receipt', rid); el.title = recLabel(rid);
  }
  function bind(root){
    (root || document).querySelectorAll('[data-bind]').forEach(function(el){
      var p = el.getAttribute('data-bind');
      try { el.textContent = fmt(get(p), el.getAttribute('data-fmt')); mark(el, p); }
      catch(e){ el.textContent = '—'; el.setAttribute('data-unresolved', p); }
    });
  }
  function esc(s){return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
  function receipts(el){
    el = el || document.getElementById('agere-receipts'); if(!el) return;
    var rows = Object.keys(B.receipts).map(function(k){var r = B.receipts[k];
      return '<tr id="receipt-' + esc(k) + '"><td><code>' + esc(k) + '</code></td><td>' + esc(r.tool) + '</td><td><code>' + esc(JSON.stringify(r.args)) + '</code></td><td>' +
        esc(String(r.definition || '').slice(0, 600)) + (r.sql_fingerprint ? '<br><code>' + esc(r.sql_fingerprint) + '</code>' : '') + '</td><td>' + esc(r.as_of || '') +
        '</td><td><code>' + esc(String(r.data_fingerprint || '').slice(0, 24)) + '</code></td></tr>'}).join('');
    el.innerHTML = '<details><summary>Saved evidence receipts (' + Object.keys(B.receipts).length +
      ')</summary><table><tr><th>receipt</th><th>tool</th><th>arguments</th><th>definition / SQL fingerprint</th><th>as of</th><th>data</th></tr>' + rows + '</table></details>';
  }
  function theme(){
    var btn = document.querySelector('.agere-theme'); if(!btn) return;
    btn.addEventListener('click', function(){
      var cur = document.documentElement.getAttribute('data-theme');
      var dark = cur ? cur === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
      document.documentElement.setAttribute('data-theme', dark ? 'light' : 'dark');
    });
  }
  function quantile(sorted, q){ if(!sorted.length) return null; var i = (sorted.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo); }
  function stats(arr){ var s = arr.slice().sort(function(a,b){return a-b}), m = 0; for (var i = 0; i < s.length; i++) m += s[i];
    return {mean: s.length ? m / s.length : null, p05: quantile(s, .05), p50: quantile(s, .5), p95: quantile(s, .95), sorted: s}; }
  window.AGERE = {data: B, get: get, fmt: fmt, bind: bind, mark: mark, receipts: receipts, esc: esc, quantile: quantile, stats: stats,
    receipt: function(rid){return B.receipts[rid]}};
  document.addEventListener('DOMContentLoaded', function(){ bind(); receipts(); theme(); });
})();
"""


# Frozen artifacts must also be embedded in an opaque-origin sandboxed iframe.
# Direct HTML serving additionally needs the HTTP CSP sandbox directive; a
# meta CSP cannot enforce sandboxing.
FROZEN_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
              "img-src data:; connect-src 'none'; frame-src 'none'; worker-src 'none'; "
              "object-src 'none'; form-action 'none'; base-uri 'none'")


def wrap(body_html, bundle, title, *, subtitle=None, extra_js=''):
    """The full document. body_html is the page body (may contain its own <style> and <script> blocks)."""
    payload = bundle.to_json() if hasattr(bundle, 'to_json') else bundle
    data = json.dumps(payload, default=str, separators=(',', ':'))
    data = data.replace('</', '<\\/')  # never close the data block early
    sub = f'<span>{htmlmod.escape(subtitle)}</span>' if subtitle else '<span></span>'
    policy = (f'<meta http-equiv="Content-Security-Policy" content="{htmlmod.escape(FROZEN_CSP, quote=True)}">'
              if payload.get('meta', {}).get('frozen_evidence') else '')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            + policy +
            f'<title>{htmlmod.escape(title)}</title><style>{CSS}</style>'
            f'<script id="agere-data" type="application/json">{data}</script><script>{JS}</script></head><body><div class="agere-wrap">'
            f'<div class="agere-top no-print">{sub}<button class="agere-theme" type="button">Light / dark</button></div>'
            f'{body_html}<section id="agere-receipts" class="agere-receipts"></section></div>'
            + (f'<script>{extra_js}</script>' if extra_js else '') + '</body></html>')


def split(doc):
    """(data json text, rest of the document with the data block emptied) from a wrapped document."""
    start = doc.find('<script id="agere-data" type="application/json">')
    if start < 0:
        return None, doc
    s = start + len('<script id="agere-data" type="application/json">')
    end = doc.find('</script>', s)
    return doc[s:end], doc[:s] + doc[end:]


def runtime_doc():
    """What a composing model is told about the runtime (kept short; it is in the cached system prompt)."""
    return """RUNTIME (already on the page; do not re-implement):
- The bundle is embedded as JSON; window.AGERE.data = {meta, results: {receipt_id: data}, receipts: {receipt_id: receipt}}.
- AGERE.get('R-xxxxxxxxxx:path.to.value') returns a value (dot path; list indexes are numbers). Throws if it does not resolve.
- Static numbers: write <span data-bind="R-xxxxxxxxxx:path" data-fmt="int|num1|num2|usd0|usd|usdk|pct0|pct1|pp"></span>; the runtime
  fills it from data and marks it with its receipt. pct formats expect a fraction (0.353 -> 35.3%).
- AGERE.fmt(value, kind) formats a number the same way inside your own JS. AGERE.mark(el, path) marks an element with a receipt.
- AGERE.stats(array) -> {mean, p05, p50, p95, sorted}; AGERE.quantile(sortedArray, q); AGERE.esc(text) HTML-escapes.
- CSS tokens: var(--bg) --panel --ink --muted --line --accent --accent-2 --good --bad --warn --chip --shadow --mono --sans;
  classes .panel .muted .mono .chip. Light/dark and print are handled; use the tokens so both themes work.
- A receipts table is rendered automatically at the bottom; the theme toggle is in the top bar.
- Write-back (only when a page records a human decision): fetch('/api/workspaces/' + AGERE.data.meta.workspace + '/deliverable/decision',
  {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({item_id, decision, note, deliverable_id: AGERE.data.meta.deliverable_id})})
  and fall back to keeping the decision in the page (and offering a JSON download) when the request fails (file:// viewing)."""
