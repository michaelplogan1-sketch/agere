"""Reference deliverables: deterministic, zero-model-cost renderers that double as few-shot examples for the composer.

Each example names the tool calls it needs and renders a page body that reads every number from the bundle (data-bind
spans for static numbers, AGERE.get + the shared browser libraries for live computation). They show the composing model
what "chains the stack" means in practice:

  war_room              CFO contract war room: sliders re-run the simulator's draws through the contract arithmetic in the
                        browser (savings distribution, VaR / CVaR, probability of earning, upside call / downside put,
                        ACCESS OAP attainment) and a Nash bargaining panel (walk-away and target terms).
  pharmacist_cards      one decision card per barrier-review patient: missing pillars, the documented barrier with its source
                        sentence and record, agents still possible, and a one-tap decision written back to the work store.
  self_running_program  the program as an agent timeline: every patient's journey through the action backbone with
                        counterparty receipts, a replayable live funnel, what the agents do next week, and only the
                        exceptions the agents escalate to a person.
"""
from __future__ import annotations

import html as htmlmod
import json

from . import finance

EXAMPLES = {}


def example(name, title, needs, audience, description):
    def deco(fn):
        EXAMPLES[name] = {'name': name, 'title': title, 'needs': needs, 'audience': audience, 'description': description, 'render': fn}
        return fn
    return deco


def _e(s):
    return htmlmod.escape(str(s if s is not None else ''))


def _program_label(kind):
    return {'pharmacy_gdmt': 'Heart failure GDMT (PharmD)', 'virtual_pt': 'ED low back pain to physical therapy'}.get(kind, kind)


# ------------------------------------------------------------------------------------------------------------ war room
@example('war_room', 'Contract war room', lambda p: [('what_if', {'program_kind': p['program_kind']}),
                                                       ('simulate', {'program_kind': p['program_kind'], 'iterations': 1000})],
         'CFO', 'Interactive contract what-if and payer negotiation for one program')
def war_room(bundle, rids, params):
    rid, sim = rids[0], rids[1]
    d = bundle.results[rid]
    has_oap = bool(d['arm'].get('oap'))
    kind = params['program_kind']
    b = lambda path, fmt: f'<span data-bind="{rid}:{path}" data-fmt="{fmt}"></span>'
    oap_slider = ('<label>ACCESS OAP outcome attainment <output id="o-attainment"></output>'
                  '<input id="s-attainment" type="range" min="0" max="1" step="0.01"></label>') if has_oap else ''
    nash_html = (f'<p>Target (Nash): sharing <b id="n-s">{b("initial_nash.nash.sharing_rate", "pct0")}</b> at MSR <b id="n-m">{b("initial_nash.nash.msr", "pct1")}</b></p>'
                 f'<p>Our value there <b id="n-ua">{b("initial_nash.nash.aco_value", "usd0")}</b>; payer keeps <b id="n-up">{b("initial_nash.nash.payer_value", "usd0")}</b></p>'
                 if (d.get('initial_nash') or {}).get('nash') else
                 '<p>Target (Nash): <b id="n-s">no agreement zone</b> <span id="n-m"></span></p><p>At these settings no sharing rate and MSR leave both sides '
                 'better off than walking away. <span id="n-ua"></span><span id="n-up"></span></p>')
    oap_toggle = ('<label class="tog"><input id="s-aco" type="checkbox" checked> ACO is the ACCESS participant (receives OAP)</label>') if has_oap else ''
    body = f"""
<style>
.wr-head h1{{margin:0 0 4px;font-size:26px}}.wr-grid{{display:grid;grid-template-columns:320px 1fr;gap:16px;margin-top:14px}}
@media (max-width:860px){{.wr-grid{{grid-template-columns:1fr}}}}
.ctl label{{display:block;font-size:13px;color:var(--muted);margin:10px 0}}.ctl input[type=range]{{width:100%;accent-color:var(--accent)}}
.ctl output{{float:right;color:var(--ink);font-family:var(--mono)}}.tog{{display:flex!important;gap:6px;align-items:center}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}@media (max-width:860px){{.tiles{{grid-template-columns:repeat(2,1fr)}}}}
.tile{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px}}.tile .k{{font-size:12px;color:var(--muted)}}
.tile .v{{font-size:20px;font-weight:650;font-family:var(--mono)}}.tile .s{{font-size:12px;color:var(--muted)}}
.good{{color:var(--good)}}.bad{{color:var(--bad)}}svg text{{fill:var(--muted);font-size:11px}}
.nash{{display:grid;grid-template-columns:1fr 280px;gap:14px;margin-top:16px}}@media (max-width:860px){{.nash{{grid-template-columns:1fr}}}}
.cell{{stroke:var(--panel);stroke-width:1}}.mk{{stroke:var(--ink);stroke-width:2;fill:none}}
table.arms{{border-collapse:collapse;width:100%;font-size:13px}}table.arms td,table.arms th{{border-top:1px solid var(--line);padding:5px;text-align:right}}
table.arms td:first-child,table.arms th:first-child{{text-align:left}}
</style>
<div class="wr-head"><h1>Contract war room: {_e(_program_label(kind))}</h1>
<div class="muted">{_e(d['terms_label'])} contract on <span data-bind="{rid}:population.members" data-fmt="int"></span> attributed members
(<span data-bind="{rid}:population.member_months" data-fmt="int"></span> member-months). Routed arm: {_e(d['arm']['label'])},
<span data-bind="{rid}:arm.patients" data-fmt="int"></span> patients. Every slider re-runs
<span data-bind="{rid}:iterations" data-fmt="int"></span> simulator draws through the exact settlement arithmetic in this page.</div></div>
<div class="wr-grid">
 <div class="panel ctl">
  <label>Program scale (share of the routed cohort reached) <output id="o-scale"></output><input id="s-scale" type="range" min="0.25" max="2" step="0.05"></label>
  <label>Benchmark factor (x projected do-nothing PMPM <span data-bind="{rid}:population.projected_do_nothing_pmpm" data-fmt="usd"></span>)
   <output id="o-bench"></output><input id="s-bench" type="range" min="0.94" max="1.08" step="0.005"></label>
  {oap_slider}
  <label>Program cost multiplier <output id="o-cost"></output><input id="s-cost" type="range" min="0.5" max="2" step="0.05"></label>
  <label>Cost trend vs projection <output id="o-trend"></output><input id="s-trend" type="range" min="-0.05" max="0.08" step="0.005"></label>
  <label>Risk aversion (lambda on CVaR) <output id="o-lambda"></output><input id="s-lambda" type="range" min="0" max="2" step="0.1"></label>
  {oap_toggle}
  <p class="muted" style="font-size:12px">Population cost dispersion: member bootstrap of <span data-bind="{rid}:population.bootstrap_members" data-fmt="int"></span>
  members, trend SD <span data-bind="{rid}:population.trend_sd" data-fmt="pct1"></span>, risk-score drift SD
  <span data-bind="{rid}:population.risk_drift_sd" data-fmt="pct1"></span>. MSR <span data-bind="{rid}:terms.msr" data-fmt="pct1"></span>,
  sharing <span data-bind="{rid}:terms.sharing_rate" data-fmt="pct0"></span>.</p>
 </div>
 <div>
  <div class="tiles">
   <div class="tile"><div class="k">Expected settlement change</div><div class="v" id="t-delta">{b('initial.expected_settlement_change', 'usd0')}</div><div class="s">with program minus do nothing</div></div>
   <div class="tile"><div class="k">Chance of earning shared savings</div><div class="v" id="t-pearn">{b('initial.p_earn_with_program', 'pct1')}</div><div class="s">do nothing <span id="t-pearn0">{b('initial.p_earn_do_nothing', 'pct1')}</span></div></div>
   <div class="tile"><div class="k">ACO net position (mean)</div><div class="v" id="t-net">{b('initial.aco_net_mean', 'usd0')}</div><div class="s" id="t-band">5th-95th percentile band {b('initial.aco_net_p05', 'usd0')} to {b('initial.aco_net_p95', 'usd0')}</div></div>
   <div class="tile"><div class="k">VaR / CVaR (<span data-bind="{rid}:defaults.confidence" data-fmt="pct0"></span>)</div><div class="v" id="t-var">{b('initial.var', 'usd0')}</div><div class="s">CVaR <span id="t-cvar">{b('initial.cvar', 'usd0')}</span></div></div>
   <div class="tile"><div class="k">Upside call value</div><div class="v" id="t-call">{b('initial.upside_call_value', 'usd0')}</div><div class="s">E[max(settlement, 0)]</div></div>
   <div class="tile"><div class="k">Downside put cost</div><div class="v" id="t-put">{b('initial.downside_put_cost', 'usd0')}</div><div class="s">E[max(-settlement, 0)]</div></div>
   <div class="tile"><div class="k">Program operating cost</div><div class="v" id="t-op">{b('initial.operating_cost_mean', 'usd0')}</div><div class="s">OAP payments <span id="t-oap">{b('initial.oap_payments_mean', 'usd0')}</span></div></div>
   <div class="tile"><div class="k">Risk-adjusted value</div><div class="v" id="t-rav">{b('initial.risk_adjusted_value', 'usd0')}</div><div class="s">E[net] - lambda x CVaR</div></div>
  </div>
  <div class="panel" style="margin-top:12px"><div class="muted" style="font-size:13px">Distribution of the ACO net position across draws</div><svg id="hist" viewBox="0 0 760 210" width="100%" role="img" aria-label="histogram"></svg></div>
 </div>
</div>
<div class="nash">
 <div class="panel"><h3 style="margin:0 0 6px">Payer negotiation: Nash bargaining over sharing rate x MSR</h3>
  <div class="muted" style="font-size:13px">{_e(d['negotiation_bounds_label'])}. Cells: Nash product of the two sides' gains (darker = better joint deal); blank = no agreement.</div>
  <svg id="nash" viewBox="0 0 640 260" width="100%" role="img" aria-label="Nash surface"></svg></div>
 <div class="panel"><h3 style="margin:0 0 6px">Walk-away and target</h3>
  {nash_html}
  <p>Walk away below sharing <b id="n-w">{b('initial_nash.walk_away_sharing_rate', 'pct0')}</b> (break-even at MSR <span id="n-wm">{b('initial_nash.walk_away_at_msr', 'pct1')}</span>)</p>
  <p class="muted" style="font-size:12px">Payer expected gross savings <span id="n-g">{b('initial_nash.payer_expected_gross_savings', 'usd0')}</span>. Recomputed live from the same draws when you move a slider.</p>
 </div>
</div>
<div class="panel" style="margin-top:16px"><h3 style="margin:0 0 6px">Simulated arms vs doing nothing (static, 5th-95th percentile intervals)</h3><div id="arms"></div></div>
<script>{finance.JS_EVAL}</script>
<script>
(function(){{
  var D = AGERE.get('{rid}'), SIM = AGERE.get('{sim}'), S = Object.assign({{}}, D.defaults), F = AGERE.fmt;
  function $(id){{return document.getElementById(id)}}
  var map = [['s-scale','scale','o-scale','num2'],['s-bench','benchmark_factor','o-bench','num2'],['s-attainment','attainment','o-attainment','pct0'],
             ['s-cost','cost_multiplier','o-cost','num2'],['s-trend','trend','o-trend','pct1'],['s-lambda','risk_aversion','o-lambda','num1']];
  map.forEach(function(m){{ var el = $(m[0]); if(!el) return; el.value = S[m[1]]; $(m[2]).textContent = F(S[m[1]], m[3]);
    el.addEventListener('input', function(){{ S[m[1]] = parseFloat(el.value); $(m[2]).textContent = F(S[m[1]], m[3]); schedule(); }}); }});
  if ($('s-aco')) $('s-aco').addEventListener('change', function(){{ S.aco_is_access_participant = $('s-aco').checked; schedule(); }});
  var timer = null; function schedule(){{ clearTimeout(timer); timer = setTimeout(update, 60); }}
  function setv(id, v, kind, cls){{ var el = $(id); el.textContent = F(v, kind); el.removeAttribute('data-bind');
    if (cls) {{ el.classList.toggle('good', v > 0); el.classList.toggle('bad', v < 0); }} }}
  function hist(net){{
    var svg = $('hist'), W = 760, H = 210, pad = 30, s = net.slice().sort(function(a,b){{return a-b}});
    var lo = AGERE.quantile(s, .01), hi = AGERE.quantile(s, .99); if (hi <= lo) hi = lo + 1;
    var bins = 40, c = new Array(bins).fill(0); net.forEach(function(x){{ var i = Math.floor((x - lo) / (hi - lo) * bins); if (i >= 0 && i < bins) c[i]++; }});
    var mx = Math.max.apply(null, c) || 1, bw = (W - 2 * pad) / bins, out = '';
    c.forEach(function(v, i){{ var x0 = lo + (hi - lo) * i / bins, h = (H - 2 * pad) * v / mx;
      out += '<rect x="' + (pad + i * bw) + '" y="' + (H - pad - h) + '" width="' + (bw - 1) + '" height="' + h + '" fill="' + (x0 < 0 ? 'var(--bad)' : 'var(--accent)') + '" opacity="0.8"></rect>'; }});
    var zx = pad + (0 - lo) / (hi - lo) * (W - 2 * pad);
    if (zx > pad && zx < W - pad) out += '<line x1="' + zx + '" x2="' + zx + '" y1="' + pad + '" y2="' + (H - pad) + '" stroke="var(--ink)" stroke-dasharray="3 3"></line>';
    out += '<text x="' + pad + '" y="' + (H - 8) + '">' + F(lo, 'usdk') + '</text><text x="' + (W - pad) + '" y="' + (H - 8) + '" text-anchor="end">' + F(hi, 'usdk') + '</text>';
    svg.innerHTML = out;
  }}
  function nashDraw(n){{
    var svg = $('nash'), W = 640, H = 260, pl = 46, pb = 30, g = n.grid; if (!g.length) {{ svg.innerHTML = ''; return; }}
    var ss = Array.from(new Set(g.map(function(r){{return r.sharing_rate}}))).sort(function(a,b){{return a-b}});
    var ms = Array.from(new Set(g.map(function(r){{return r.msr}}))).sort(function(a,b){{return a-b}});
    var mx = Math.max.apply(null, g.map(function(r){{return r.nash_product || 0}})) || 1, cw = (W - pl - 10) / ss.length, ch = (H - pb - 10) / ms.length, out = '';
    g.forEach(function(r){{ var i = ss.indexOf(r.sharing_rate), j = ms.indexOf(r.msr), a = r.nash_product ? 0.15 + 0.85 * r.nash_product / mx : 0;
      out += '<rect class="cell" x="' + (pl + i * cw) + '" y="' + (10 + (ms.length - 1 - j) * ch) + '" width="' + cw + '" height="' + ch + '" fill="var(--accent)" fill-opacity="' + a.toFixed(3) + '"><title>sharing ' + F(r.sharing_rate, 'pct0') + ', MSR ' + F(r.msr, 'pct1') + ': ours ' + F(r.aco_value, 'usd0') + ', payer ' + F(r.payer_value, 'usd0') + '</title></rect>'; }});
    if (n.nash){{ var i = ss.indexOf(n.nash.sharing_rate), j = ms.indexOf(n.nash.msr);
      out += '<rect class="mk" x="' + (pl + i * cw) + '" y="' + (10 + (ms.length - 1 - j) * ch) + '" width="' + cw + '" height="' + ch + '"></rect>'; }}
    if (n.walk_away_sharing_rate !== null && n.walk_away_sharing_rate >= ss[0]){{ var wx = pl + (n.walk_away_sharing_rate - ss[0]) / (ss[ss.length - 1] - ss[0] || 1) * (W - pl - 10 - cw);
      out += '<line x1="' + wx + '" x2="' + wx + '" y1="10" y2="' + (H - pb) + '" stroke="var(--bad)" stroke-dasharray="4 3"></line>'; }}
    out += '<text x="' + pl + '" y="' + (H - 10) + '">sharing ' + F(ss[0], 'pct0') + '</text><text x="' + (W - 10) + '" y="' + (H - 10) + '" text-anchor="end">' + F(ss[ss.length - 1], 'pct0') + '</text>';
    out += '<text x="4" y="' + (H - pb) + '">MSR ' + F(ms[0], 'pct1') + '</text><text x="4" y="18">' + F(ms[ms.length - 1], 'pct1') + '</text>';
    svg.innerHTML = out;
  }}
  function update(){{
    var ev = AgereFinance.evaluate(D, S);
    setv('t-delta', ev.expected_settlement_change, 'usd0', 1); setv('t-pearn', ev.p_earn_with_program, 'pct1'); setv('t-pearn0', ev.p_earn_do_nothing, 'pct1');
    setv('t-net', ev.aco_net_mean, 'usd0', 1); $('t-band').textContent = '5th-95th percentile band ' + F(ev.aco_net_p05, 'usd0') + ' to ' + F(ev.aco_net_p95, 'usd0');
    setv('t-var', ev.var, 'usd0'); setv('t-cvar', ev.cvar, 'usd0'); setv('t-call', ev.upside_call_value, 'usd0'); setv('t-put', ev.downside_put_cost, 'usd0');
    setv('t-op', ev.operating_cost_mean, 'usd0'); setv('t-oap', ev.oap_payments_mean, 'usd0'); setv('t-rav', ev.risk_adjusted_value, 'usd0', 1);
    hist(ev._net);
    var n = AgereFinance.nash(D, S); nashDraw(n);
    if (n.nash){{ setv('n-s', n.nash.sharing_rate, 'pct0'); setv('n-m', n.nash.msr, 'pct1'); setv('n-ua', n.nash.aco_value, 'usd0'); setv('n-up', n.nash.payer_value, 'usd0'); }}
    else {{ ['n-s','n-m','n-ua','n-up'].forEach(function(id){{ $(id).textContent = 'no agreement zone'; }}); }}
    setv('n-w', n.walk_away_sharing_rate, 'pct0'); setv('n-g', n.payer_expected_gross_savings, 'usd0');
  }}
  function arms(){{
    var rows = SIM.summary_table.map(function(r, i){{ var p = '{sim}:summary_table.' + i + '.';
      function cell(k, f){{ return r[k] ? '<td><span data-bind="' + p + k + '.mean" data-fmt="' + f + '"></span><br><small class="muted"><span data-bind="' + p + k + '.p05" data-fmt="' + f + '"></span> to <span data-bind="' + p + k + '.p95" data-fmt="' + f + '"></span></small></td>' : '<td>—</td>'; }}
      return '<tr><td>' + AGERE.esc(r.label) + '</td>' + cell('admissions', 'num1') + cell('ed_visits', 'num1') + cell('net_savings', 'usd0') + cell('shared_savings_change', 'usd0') +
        '<td><span data-bind="' + p + 'p_earn_shared_savings" data-fmt="pct1"></span></td></tr>'; }}).join('');
    $('arms').innerHTML = '<table class="arms"><tr><th>arm</th><th>admissions</th><th>ED visits</th><th>net savings</th><th>shared-savings change</th><th>P(earn)</th></tr>' + rows + '</table>';
    AGERE.bind($('arms'));
  }}
  document.addEventListener('DOMContentLoaded', function(){{ arms(); update(); }});
}})();
</script>"""
    return body, f'Contract war room: {_program_label(kind)}', f"{d['terms_label']} | simulator {d['simulation_id']}"


# ------------------------------------------------------------------------------------------------- pharmacist cards
@example('pharmacist_cards', 'Pharmacist decision cards', lambda p: [('barriers', {'limit': 200})], 'pharmacy leader',
         'One card per GDMT barrier-review patient with a one-tap decision written back to the work store')
def pharmacist_cards(bundle, rids, params):
    rid = rids[0]
    body = f"""
<style>
.pc-head h1{{margin:0 0 4px;font-size:26px}}.bar{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}}.bar button{{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;padding:4px 12px;cursor:pointer}}
.bar button.on{{background:var(--accent);color:var(--panel);border-color:var(--accent)}}.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;display:flex;flex-direction:column;gap:8px}}
.card h3{{margin:0;font-size:15px;font-family:var(--mono)}}.pill{{border-radius:999px;padding:1px 8px;font-size:12px;background:var(--chip)}}
.st-open_with_barrier{{color:var(--accent-2)}}.st-explained{{color:var(--good)}}.st-stale_only{{color:var(--muted)}}
blockquote{{margin:0;padding:8px 10px;border-left:3px solid var(--accent);background:var(--chip);border-radius:4px;font-size:13px}}
.src{{font-size:11px;color:var(--muted);font-family:var(--mono)}}.agents{{font-size:13px}}.acts{{display:flex;gap:6px;flex-wrap:wrap}}
.acts button{{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:6px;padding:5px 9px;cursor:pointer;font-size:12px}}
.acts button:hover{{border-color:var(--accent)}}.done{{opacity:.6}}.decided{{font-size:12px;color:var(--good)}}
</style>
<div class="pc-head"><h1>GDMT barrier review: pharmacist decision cards</h1>
<div class="muted"><span data-bind="{rid}:patients_with_barriers" data-fmt="int"></span> patients routed for heart failure GDMT have a documented
reason for a missing pillar, read from <span data-bind="{rid}:source_records" data-fmt="int"></span> source records. Each card shows the sentence the
reader found, what it rules out and what is still possible. One tap records your decision on the patient's work item.</div></div>
<div class="bar no-print" id="filters"></div>
<div class="cards" id="cards"></div>
<p class="no-print"><button id="export" class="agere-theme" type="button">Download decisions (JSON)</button> <span id="saved" class="muted"></span></p>
<script>
(function(){{
  var D = AGERE.get('{rid}'), esc = AGERE.esc, decisions = {{}}, filter = 'all';
  var LABEL = {{open_with_barrier: 'open: an agent is still possible', explained: 'explained: barrier closes the pillar', stale_only: 'history only: outside the recency window'}};
  var PILLAR = {{rasi: 'ARNI / ACE inhibitor / ARB', beta_blocker: 'Evidence-based beta blocker', mra: 'MRA', sglt2i: 'SGLT2 inhibitor'}};
  var CHOICES = [['alternative_agent', 'Start an alternative agent'], ['rechallenge', 'Rechallenge same agent'], ['accept_barrier', 'Accept barrier, close pillar'],
                 ['reask_patient', 'Re-ask the patient'], ['refer_prescriber', 'Refer to prescriber']];
  function item(c){{ var w = (c.work_items || []).filter(function(x){{return x.lane === 'plan:pharmacy_gdmt'}})[0]; return w ? w.item_id : null; }}
  function card(c, ci){{
    var p = Object.keys(c.pillars).map(function(k){{ var v = c.pillars[k], b = (v.barriers.length ? v.barriers : v.stale_barriers)[0] || {{}};
      var path = '{rid}:cards.' + ci + '.pillars.' + k;
      return '<div><div><b>' + esc(PILLAR[k] || k) + '</b> <span class="pill st-' + esc(v.status) + '">' + esc(LABEL[v.status] || v.status) + '</span></div>' +
        (b.evidence_text ? '<blockquote>' + esc(b.evidence_text) + '</blockquote><div class="src">' + esc(b.barrier_type) + ' · ' + esc(b.drug_key || b.drug_class || '') +
          ' · ' + esc(b.event_date || '') + ' · ' + esc(b.source_type || '') + ' ' + esc(b.record_id || '') + '</div>' : '') +
        '<div class="agents">Still possible: ' + (v.usable_agents.length ? esc(v.usable_agents.slice(0, 6).join(', ')) + (v.usable_agents.length > 6 ? ' and more' : '') : 'none') +
        (v.excluded_agents.length ? '<br><span class="muted">Ruled out: ' + esc(v.excluded_agents.join(', ')) + '</span>' : '') + '</div>' +
        '<div class="muted" style="font-size:12px">' + esc(v.action || '') + '</div></div>'; }}).join('');
    var wid = item(c), prior = (c.work_items || []).reduce(function(a, w){{ return a.concat(w.human_decisions || []); }}, []);
    var dec = decisions[c.patient_id] || (prior.length ? prior[prior.length - 1].decision : null);
    return '<div class="card' + (dec ? ' done' : '') + '" data-status="' + esc(c.priority_status) + '"><h3>' + esc(c.patient_id) + '</h3>' +
      '<div class="muted" style="font-size:12px">' + esc(c.practice || 'unattributed') + ' · missing: ' + esc((c.missing_pillars || []).map(function(m){{return PILLAR[m] || m}}).join(', ')) + '</div>' + p +
      '<div class="acts no-print">' + CHOICES.map(function(ch){{ return '<button data-pid="' + esc(c.patient_id) + '" data-item="' + esc(wid || '') + '" data-d="' + ch[0] + '">' + esc(ch[1]) + '</button>'; }}).join('') + '</div>' +
      (dec ? '<div class="decided">Decision recorded: ' + esc(dec) + '</div>' : '') + '</div>';
  }}
  function render(){{
    var cards = D.cards.map(function(c, i){{ return [c, i]; }}).filter(function(x){{ return filter === 'all' || x[0].priority_status === filter; }});
    document.getElementById('cards').innerHTML = cards.map(function(x){{ return card(x[0], x[1]); }}).join('') || '<p class="muted">No cards in this view.</p>';
    AGERE.bind(document.getElementById('cards'));
  }}
  function filters(){{
    var counts = {{}}; D.cards.forEach(function(c){{ counts[c.priority_status] = (counts[c.priority_status] || 0) + 1; }});
    var opts = [['all', 'All']].concat(Object.keys(LABEL).filter(function(k){{return counts[k]}}).map(function(k){{return [k, LABEL[k]]}}));
    var el = document.getElementById('filters');
    el.innerHTML = opts.map(function(o){{ return '<button data-f="' + o[0] + '" class="' + (o[0] === filter ? 'on' : '') + '">' + esc(o[1]) + ' (' + (o[0] === 'all' ? D.cards.length : counts[o[0]]) + ')</button>'; }}).join('');
  }}
  document.addEventListener('click', function(e){{
    var f = e.target.getAttribute && e.target.getAttribute('data-f'); if (f) {{ filter = f; filters(); render(); return; }}
    var d = e.target.getAttribute && e.target.getAttribute('data-d'); if (!d) return;
    var pid = e.target.getAttribute('data-pid'), wid = e.target.getAttribute('data-item');
    var note = window.prompt('Note for the record (optional)', '') || '';
    decisions[pid] = d; render();
    var body = {{item_id: wid, patient_id: pid, decision: d, note: note, deliverable_id: AGERE.data.meta.deliverable_id}};
    fetch('/api/workspaces/' + AGERE.data.meta.workspace + '/deliverable/decision', {{method: 'POST', headers: {{'content-type': 'application/json'}}, body: JSON.stringify(body)}})
      .then(function(r){{ if (!r.ok) throw new Error(r.status); document.getElementById('saved').textContent = 'Saved to the work store.'; }})
      .catch(function(){{ var q = []; try {{ q = JSON.parse(localStorage.getItem('agere-decisions') || '[]'); q.push(body); localStorage.setItem('agere-decisions', JSON.stringify(q)); }} catch(_e) {{}}
        document.getElementById('saved').textContent = 'Offline: decision kept in this page; download and import it.'; }});
  }});
  document.getElementById('export').addEventListener('click', function(){{
    var blob = new Blob([JSON.stringify({{workspace: AGERE.data.meta.workspace, decisions: decisions}}, null, 1)], {{type: 'application/json'}});
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'gdmt-barrier-decisions.json'; a.click();
  }});
  document.addEventListener('DOMContentLoaded', function(){{ filters(); render(); }});
}})();
</script>"""
    return body, 'Pharmacist decision cards: GDMT barrier review', 'barrier reader + routed plan + action backbone'


# ---------------------------------------------------------------------------------------------- self-running program
@example('self_running_program', 'Self-running program', lambda p: [('work', {'program_kind': p['program_kind'], 'view': 'timelines', 'limit': 400}),
                                                                    ('plan', {'program_kind': p['program_kind']})],
         'VP population health', 'The program as an agent timeline with receipts, a replayable funnel and only escalated exceptions')
def self_running_program(bundle, rids, params):
    rid, prid = rids
    kind = params['program_kind']
    body = f"""
<style>
.sr-head h1{{margin:0 0 4px;font-size:26px}}.sr-grid{{display:grid;grid-template-columns:1fr 340px;gap:14px;margin-top:12px}}@media (max-width:900px){{.sr-grid{{grid-template-columns:1fr}}}}
.fun{{display:flex;flex-direction:column;gap:4px}}.fr{{display:grid;grid-template-columns:150px 1fr 60px;gap:8px;align-items:center;font-size:13px}}
.fb{{height:14px;background:var(--accent);border-radius:3px;transition:width .2s}}.lanes{{max-height:560px;overflow:auto}}
.lane{{display:grid;grid-template-columns:150px 1fr;gap:8px;align-items:center;border-top:1px solid var(--line);padding:3px 0;font-size:12px}}
.lane .id{{font-family:var(--mono);font-size:11px;cursor:pointer}}.track{{position:relative;height:16px}}
.dot{{position:absolute;top:3px;width:9px;height:9px;border-radius:50%;background:var(--muted)}}.dot.ok{{background:var(--good)}}.dot.no{{background:var(--bad)}}.dot.wait{{background:var(--warn)}}
.ex{{border-left:3px solid var(--accent-2);padding:6px 8px;margin:6px 0;background:var(--chip);border-radius:4px;font-size:13px}}
.ctl{{display:flex;gap:10px;align-items:center;margin:8px 0}}.ctl input{{flex:1;accent-color:var(--accent)}}
#detail table{{font-size:12px;border-collapse:collapse;width:100%}}#detail td{{border-top:1px solid var(--line);padding:3px 4px;vertical-align:top}}
</style>
<div class="sr-head"><h1>Self-running program: {_e(_program_label(kind))}</h1>
<div class="muted">The agents work <span data-bind="{rid}:plan_items" data-fmt="int"></span> patients through outreach, consent, eligibility, prior
authorization and appeal, visits and completion, with <span data-bind="{rid}:counterparty_receipts" data-fmt="int"></span> counterparty receipts so far.
A person sees only the <span data-bind="{rid}:escalation_count" data-fmt="int"></span> exceptions the agents escalate. Counterparties are simulated; nothing is sent.</div></div>
<div class="sr-grid">
 <div class="panel">
  <div class="ctl no-print"><button id="play" class="agere-theme" type="button">Play</button><input id="day" type="range" min="0" value="0"><span id="dlabel" class="mono"></span></div>
  <div class="fun" id="funnel"></div>
  <h3 style="margin:14px 0 4px">Every patient's journey</h3><div class="muted" style="font-size:12px">Dots are counterparty receipts (green = advanced, red = stopped, amber = waiting). Click an id for its receipts.</div>
  <div class="lanes" id="lanes"></div>
 </div>
 <div>
  <div class="panel"><h3 style="margin:0 0 6px">Needs a person</h3><div id="exc"></div></div>
  <div class="panel" style="margin-top:12px"><h3 style="margin:0 0 6px">Agents next week</h3>
   <p>Follow-ups due by <span data-bind="{rid}:next_week.window_end"></span>: <b><span data-bind="{rid}:next_week.followups_due" data-fmt="int"></span></b></p>
   <p>Journeys still in progress: <b><span data-bind="{rid}:next_week.items_in_progress" data-fmt="int"></span></b></p><div id="nw"></div></div>
  <div class="panel" style="margin-top:12px" id="detail"><span class="muted">Select a patient.</span></div>
 </div>
</div>
<script>
(function(){{
  var W = AGERE.get('{rid}'), P = AGERE.get('{prid}'), esc = AGERE.esc, F = AGERE.fmt;
  var T = W.timelines || [], STAGES = [['outreach_attempted','Outreach attempted'],['reached','Reached'],['consented','Consented'],['eligible','Eligible'],['enrolled','Enrolled'],
    ['first_visit_scheduled','Visit scheduled'],['first_visit_attended','Visit attended'],['completed','Completed']];
  var OK = {{reached:1, consented:1, eligible:1, enrolled:1, scheduled:1, attended:1, completed:1, approved:1, overturned:1, appeal_overturned:1, prior_auth_approved:1, coverage_found:1, delivered:1, rescheduled:1}};
  var NO = {{declined:1, unreachable:1, denied:1, upheld:1, dropped_out:1, no_show:1, coverage_not_found:1, eligibility_denied:1, coverage_denied:1, dropped:1, not_scheduled:1}};
  var days = []; T.forEach(function(t){{ t.steps.forEach(function(s){{ if (s.at) days.push(s.at.slice(0, 10)); }}); }});
  days = Array.from(new Set(days)).sort();
  var slider = document.getElementById('day'); slider.max = Math.max(0, days.length - 1); slider.value = slider.max;
  function stageOf(s){{ var o = s.outcome || ''; if (s.step === 'patient_outreach') return o === 'reached' ? 'reached' : 'outreach_attempted';
    if (s.step === 'consent' && o === 'consented') return 'consented'; if (s.step === 'eligibility_check' && o === 'eligible') return 'eligible';
    if (s.step === 'enrollment' && (o === 'enrolled' || o === 'accepted')) return 'enrolled'; if (/schedul/.test(s.step) && /schedul/.test(o)) return 'first_visit_scheduled';
    if (/visit/.test(s.step) && (o === 'attended' || o === 'arrived')) return 'first_visit_attended'; if (o === 'completed') return 'completed'; return null; }}
  function funnel(cut){{
    var n = {{}}; STAGES.forEach(function(s){{ n[s[0]] = 0; }});
    T.forEach(function(t){{ var seen = {{}}; t.steps.forEach(function(s){{ if (!s.at || s.at.slice(0, 10) > cut) return; seen.outreach_attempted = 1; var g = stageOf(s); if (g) seen[g] = 1; }});
      if (seen.first_visit_attended || seen.completed) seen.first_visit_scheduled = 1; if (seen.completed) seen.first_visit_attended = 1;
      Object.keys(seen).forEach(function(k){{ if (k in n) n[k]++; }}); }});
    var mx = T.length || 1;
    document.getElementById('funnel').innerHTML = STAGES.map(function(s){{ return '<div class="fr"><span>' + esc(s[1]) + '</span><div><div class="fb" style="width:' + (100 * n[s[0]] / mx).toFixed(1) + '%"></div></div><span class="mono">' + F(n[s[0]], 'int') + '</span></div>'; }}).join('');
    document.getElementById('dlabel').textContent = cut;
  }}
  function lanes(){{
    var first = days[0], last = days[days.length - 1], span = (new Date(last) - new Date(first)) || 1;
    document.getElementById('lanes').innerHTML = T.map(function(t, i){{ return '<div class="lane"><span class="id" data-i="' + i + '">' + esc(t.patient_id) + '</span><div class="track">' +
      t.steps.filter(function(s){{return s.at}}).map(function(s){{ var x = 100 * (new Date(s.at.slice(0, 10)) - new Date(first)) / span, o = s.outcome || '';
        return '<span class="dot ' + (OK[o] ? 'ok' : NO[o] ? 'no' : 'wait') + '" style="left:calc(' + x.toFixed(2) + '% - 4px)" title="' + esc(s.step + ': ' + o + ' (' + s.at.slice(0, 10) + ')') + '"></span>'; }}).join('') + '</div></div>'; }}).join('');
  }}
  function detail(i){{
    var t = T[i]; document.getElementById('detail').innerHTML = '<h3 style="margin:0 0 6px;font-family:var(--mono);font-size:14px">' + esc(t.patient_id) + '</h3><div class="muted" style="font-size:12px">' +
      esc(t.agent || '') + ' · ' + esc(t.vendor || '') + ' · state ' + esc(t.state) + '</div><table>' + t.steps.map(function(s){{ return '<tr><td>' + esc((s.at || s.sent_on || '').slice(0, 10)) + '</td><td>' + esc(s.step) +
      '</td><td>' + esc(s.outcome || s.status) + '<br><span class="muted">' + esc(s.reason || '') + '</span></td><td class="mono">' + esc(s.receipt_id || '') + '</td></tr>'; }}).join('') + '</table>';
  }}
  function exceptions(){{
    var ex = W.escalations || [];
    document.getElementById('exc').innerHTML = ex.length ? ex.map(function(e){{ return '<div class="ex"><b>' + esc(e.type.replace(/_/g, ' ')) + '</b> · <span class="mono">' + esc(e.patient_id) + '</span><br>' +
      esc(e.why_escalated) + (e.reason ? ': ' + esc(e.reason) : '') + '<br><span class="muted">owner ' + esc((e.owner || {{}}).id || (e.owner || {{}}).kind || '') + (e.due ? ', due ' + esc(e.due) : '') + '</span></div>'; }}).join('') : '<p class="muted">Nothing needs a person right now.</p>';
    var nw = W.next_week.by_type || {{}};
    document.getElementById('nw').innerHTML = Object.keys(nw).map(function(k){{ return '<span class="chip">' + esc(k.replace(/_/g, ' ')) + ' ' + F(nw[k], 'int') + '</span> '; }}).join('');
  }}
  var playing = null;
  document.getElementById('play').addEventListener('click', function(){{
    if (playing) {{ clearInterval(playing); playing = null; this.textContent = 'Play'; return; }}
    var self = this; self.textContent = 'Pause'; slider.value = 0;
    playing = setInterval(function(){{ var v = +slider.value + 1; if (v > +slider.max) {{ clearInterval(playing); playing = null; self.textContent = 'Play'; return; }} slider.value = v; funnel(days[v]); }}, 120);
  }});
  slider.addEventListener('input', function(){{ funnel(days[+slider.value]); }});
  document.addEventListener('click', function(e){{ var i = e.target.getAttribute && e.target.getAttribute('data-i'); if (i !== null && i !== undefined && e.target.classList.contains('id')) detail(+i); }});
  document.addEventListener('DOMContentLoaded', function(){{ if (days.length) funnel(days[days.length - 1]); lanes(); exceptions(); }});
}})();
</script>"""
    return body, f'Self-running program: {_program_label(kind)}', 'action backbone agents + simulated counterparties'


def needs(name, params):
    return EXAMPLES[name]['needs'](params)


def render(name, bundle, rids, params):
    return EXAMPLES[name]['render'](bundle, rids, params)


def few_shot(name='pharmacist_cards', max_chars=9000):
    """A trimmed example body for the composer's cached system prompt (structure, binding and write-back patterns)."""
    import inspect
    src = inspect.getsource(EXAMPLES[name]['render'])
    return src[:max_chars]
