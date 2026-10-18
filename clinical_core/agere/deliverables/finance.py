"""Contract what-if math shared by the Python side (initial state, tests) and the browser (live sliders).

draws(...) assembles, from executed engines only, the per-draw arrays a page needs to re-run the contract in the browser:
  * simulator (simulator/engine.py, return_draws=True): per-draw avoided medical cost of the arm's patients, pharmacy cost,
    per-patient savings (for attribution), and the funnel milestones (simulator/funnel.scenarios, same seed / scenarios as
    the engine) -> per-draw outreach / enrolled / attended / completed counts per program;
  * contract finance (clinical/contract_strategy.Simulation): member bootstrap of completion-adjusted allowed cost x trend
    x risk-score drift -> per-draw population cost dispersion around the simulator's projected do-nothing PMPM;
  * contract terms (clinical/programs.contract_terms, MSR resolved for the population as the engine does) and the ACCESS
    OAP parameters (simulator/config.json, cited) when a program carries them.
evaluate(d, sliders) is the reference implementation of what the page computes; JS_EVAL is its browser twin (tested for
parity). Shapley attribution of the simulated settlement change across practices / programs is exact (<= 9 players).
"""
from __future__ import annotations

from itertools import combinations
from math import factorial

import numpy as np

DEFAULT_SLIDERS = {'scale': 1.0, 'benchmark_factor': 1.0, 'attainment': None, 'cost_multiplier': 1.0, 'trend': 0.0,
                   'aco_is_access_participant': True, 'risk_aversion': 0.5, 'confidence': 0.95}


def payout(actual, bench, mm, t):
    """vbc.settle, vectorised over actual and benchmark PMPM arrays (corridors, discounts, withholds, caps)."""
    from ..clinical import contract_strategy
    return contract_strategy.payout(actual, bench, mm, t)


def _paid_fraction(oap, attainment):
    w, oat = oap['withheld_share'], oap['outcome_attainment_threshold']
    return (1 - w) + w * np.minimum(1.0, np.asarray(attainment, float) / oat)


def evaluate(d, sliders=None):
    """Reference evaluation of a what-if bundle (the dict draws() returns) at slider values. -> metrics dict."""
    s = {**DEFAULT_SLIDERS, **(sliders or {})}
    pop, terms = d['population'], d['terms']
    mm, P0 = pop['member_months'], pop['projected_do_nothing_pmpm']
    boot, zt, zd = (np.array(pop[k], float) for k in ('boot_rel', 'z_trend', 'z_drift'))
    pmpm_dn = P0 * boot * (1 + s['trend'] + pop['trend_sd'] * zt)
    bench = P0 * s['benchmark_factor'] * (1 + pop['risk_drift_sd'] * zd)
    arm = d['arm']
    saved = np.array(arm['saved_total'], float) * s['scale']
    op = np.zeros_like(saved)
    for prog, c in arm['counts'].items():
        unit = arm['unit_costs'].get(prog) or {}
        for flag, key in (('outreach', 'per_outreach'), ('enrolled', 'per_enrolled'), ('attended', 'per_attended'), ('completed', 'per_completed')):
            if unit.get(key):
                op = op + np.array(c[flag], float) * unit[key]
    op = op * s['scale'] * s['cost_multiplier']
    oap_total = np.zeros_like(saved)
    oap_comgmt = np.zeros_like(saved)
    oap_medicare = np.zeros_like(saved)
    att_used = None
    for prog, oap in (arm.get('oap') or {}).items():
        c = arm['counts'][prog]
        enrolled = np.array(c['enrolled'], float) * s['scale']
        att = s['attainment'] if s['attainment'] is not None else oap['simulated_attainment']
        att_used = att
        allowed = oap['annual_allowed'] * oap['months'] / 12.0
        paid = allowed * _paid_fraction(oap, att)
        oap_total = oap_total + enrolled * paid
        oap_comgmt = oap_comgmt + enrolled * oap['co_management_per_enrolled']
        if oap['counts_in_benchmark']:
            oap_medicare = oap_medicare + enrolled * paid * oap['medicare_share']
    pmpm_arm = pmpm_dn - saved / mm + oap_medicare / mm
    set_dn = payout(pmpm_dn, bench, mm, terms)
    set_arm = payout(pmpm_arm, bench, mm, terms)
    delta = set_arm - set_dn
    aco_net = delta - op + ((oap_total + oap_comgmt) if s['aco_is_access_participant'] else 0.0)
    conf = s['confidence']
    q = float(np.quantile(aco_net, 1 - conf))
    tail = aco_net[aco_net <= q]
    var, cvar = max(0.0, -q), max(0.0, -float(tail.mean())) if tail.size else 0.0
    gross = saved
    payer_net = gross - op - oap_total
    return {'expected_settlement_do_nothing': float(set_dn.mean()), 'expected_settlement_with_program': float(set_arm.mean()),
            'expected_settlement_change': float(delta.mean()), 'p_earn_do_nothing': float((set_dn > 0).mean()),
            'p_earn_with_program': float((set_arm > 0).mean()), 'p_owe_with_program': float((set_arm < 0).mean()),
            'aco_net_mean': float(aco_net.mean()), 'aco_net_p05': float(np.quantile(aco_net, .05)), 'aco_net_p50': float(np.quantile(aco_net, .5)),
            'aco_net_p95': float(np.quantile(aco_net, .95)), 'p_aco_net_positive': float((aco_net > 0).mean()), 'var': var, 'cvar': cvar,
            'risk_adjusted_value': float(aco_net.mean()) - s['risk_aversion'] * cvar,
            'upside_call_value': float(np.maximum(set_arm, 0).mean()), 'downside_put_cost': float(np.maximum(-set_arm, 0).mean()),
            'gross_medical_savings_mean': float(gross.mean()), 'operating_cost_mean': float(op.mean()), 'oap_payments_mean': float(oap_total.mean()),
            'co_management_mean': float(oap_comgmt.mean()), 'payer_net_savings_mean': float(payer_net.mean()),
            'attainment_used': att_used, 'benchmark_pmpm_mean': float(bench.mean()), 'do_nothing_pmpm_mean': float(pmpm_dn.mean())}


def nash(d, sliders=None, bounds=None):
    """Nash bargaining over (sharing rate, MSR) for a one-sided shared-savings offer on the arm's cost draws (the
    contract_strategy.negotiate method): ACO utility = E[net] - lambda x CVaR (net = payout - operating cost + OAP when the ACO
    is the ACCESS participant), payer utility = E[(benchmark - cost) x mm] - E[payout]; reservation values 0."""
    s = {**DEFAULT_SLIDERS, **(sliders or {})}
    b = bounds or d['negotiation_bounds']
    pop = d['population']
    mm, P0 = pop['member_months'], pop['projected_do_nothing_pmpm']
    boot, zt, zd = (np.array(pop[k], float) for k in ('boot_rel', 'z_trend', 'z_drift'))
    pmpm_dn = P0 * boot * (1 + s['trend'] + pop['trend_sd'] * zt)
    bench = P0 * s['benchmark_factor'] * (1 + pop['risk_drift_sd'] * zd)
    ev = evaluate(d, s)
    saved = np.array(d['arm']['saved_total'], float) * s['scale']
    cost = pmpm_dn - saved / mm
    extra = -ev['operating_cost_mean'] + ((ev['oap_payments_mean'] + ev['co_management_mean']) if s['aco_is_access_participant'] else 0.0)
    gross = float(((bench - cost) * mm).mean())
    best, grid = None, []
    S = np.round(np.arange(b['sharing_rate_min'], b['sharing_rate_max'] + 1e-9, 0.01), 4)
    M = np.round(np.arange(b['msr_min'], b['msr_max'] + 1e-9, 0.005), 4)
    for m in M:
        for sr in S:
            t = {'sharing_rate': float(sr), 'msr': float(m), 'two_sided': False}
            pay = payout(cost, bench, mm, t)
            net = pay + extra
            q = float(np.quantile(net, 1 - s['confidence']))
            tail = net[net <= q]
            cv = max(0.0, -float(tail.mean())) if tail.size else 0.0
            ua, up = float(net.mean()) - s['risk_aversion'] * cv, gross - float(pay.mean())
            prod = ua * up if ua > 0 and up > 0 else None
            row = {'sharing_rate': float(sr), 'msr': float(m), 'aco_value': ua, 'payer_value': up, 'nash_product': prod}
            grid.append(row)
            if prod is not None and (best is None or prod > best['nash_product']):
                best = row
    walk = None
    m0 = b['msr_min']
    for sr in np.round(np.arange(0, 1.0 + 1e-9, 0.01), 4):
        pay = payout(cost, bench, mm, {'sharing_rate': float(sr), 'msr': float(m0), 'two_sided': False})
        if float((pay + extra).mean()) >= 0:
            walk = float(sr)
            break
    return {'nash': best, 'walk_away_sharing_rate': walk, 'walk_away_at_msr': float(m0), 'payer_expected_gross_savings': gross,
            'grid_points': len(grid)}


def shapley(per_patient_saved, players_of, d, sliders=None, max_players=9):
    """Exact Shapley split of the expected settlement change across players (practices, programs) from the simulator's
    per-patient avoided cost draws. per_patient_saved: (N, n); players_of: list of n player names."""
    s = {**DEFAULT_SLIDERS, **(sliders or {})}
    pop, terms = d['population'], d['terms']
    mm, P0 = pop['member_months'], pop['projected_do_nothing_pmpm']
    boot, zt, zd = (np.array(pop[k], float) for k in ('boot_rel', 'z_trend', 'z_drift'))
    pmpm_dn = P0 * boot * (1 + s['trend'] + pop['trend_sd'] * zt)
    bench = P0 * s['benchmark_factor'] * (1 + pop['risk_drift_sd'] * zd)
    X = np.asarray(per_patient_saved, float)
    names = sorted(set(players_of))
    if len(names) > max_players:
        raise ValueError(f'at most {max_players} players (got {len(names)}); group the rest')
    by = {p: X[:, [i for i, q in enumerate(players_of) if q == p]].sum(1) for p in names}
    base = payout(pmpm_dn, bench, mm, terms)
    value = {}
    for k in range(len(names) + 1):
        for c in combinations(names, k):
            sv = sum((by[p] for p in c), np.zeros(len(pmpm_dn)))
            value[frozenset(c)] = float((payout(pmpm_dn - sv / mm, bench, mm, terms) - base).mean())
    n = len(names)
    out = []
    for p in names:
        phi = 0.0
        for c, v in value.items():
            if p in c:
                continue
            w = factorial(len(c)) * factorial(n - len(c) - 1) / factorial(n)
            phi += w * (value[c | {p}] - v)
        out.append({'player': p, 'shapley_value': phi, 'standalone_value': value[frozenset([p])],
                    'without_player_value': value[frozenset(names) - {p}], 'expected_avoided_cost': float(by[p].mean()),
                    'patients': sum(1 for q in players_of if q == p)})
    total = value[frozenset(names)]
    tot_avoided = sum(r['expected_avoided_cost'] for r in out)
    for r in out:
        r['pro_rata_by_avoided_cost'] = total * r['expected_avoided_cost'] / tot_avoided if tot_avoided else 0.0
        r['marginal_counterfactual'] = total - r['without_player_value']
    return {'pooled_settlement_change': total, 'players': sorted(out, key=lambda r: -r['shapley_value']), 'coalitions_evaluated': len(value)}


# Browser twin of evaluate() + nash(); reads the same structure (AGERE.get(rid) of a what_if result).
JS_EVAL = r"""
var AgereFinance = (function(){
  function corridor(pct, cs){ var t = 0; for (var i = 0; i < cs.length; i++){ var c = cs[i]; var hi = c.to == null ? pct : Math.min(pct, c.to);
    t += c.rate * Math.max(0, hi - c.from); } return t; }
  function settle(actual, bench, mm, t){
    if (!mm) return 0;
    var withhold = t.quality_withhold_pct || 0, earned = t.quality_mode === 'withhold' ? (t.quality_score == null ? 1 : t.quality_score) : 1;
    var b = bench * (1 - (t.discount_pct || 0) - withhold * (1 - earned));
    var sp = b !== 0 ? (b - actual) / b : 0, total = (b - actual) * mm;
    var dmsr = t.corridors ? 0 : 0.02, msr = t.msr == null ? dmsr : t.msr, mlr = t.mlr == null ? msr : t.mlr;
    var quality = (t.quality_mode || 'sharing_multiplier') === 'sharing_multiplier' ? (t.quality_score == null ? 1 : t.quality_score) : 1;
    var shared = t.corridors ? b * mm * corridor(Math.max(sp, 0), t.corridors) * quality : total * (t.sharing_rate || 0) * quality;
    if (t.savings_cap_pct != null) shared = Math.min(shared, b * mm * t.savings_cap_pct);
    if (sp >= msr && sp > 0) return shared;
    if (t.two_sided && -sp >= mlr && sp < 0){
      var owed = t.corridors ? b * mm * corridor(Math.max(-sp, 0), t.corridors) : -total * (t.loss_rate == null ? 1 - (t.sharing_rate || 0) : t.loss_rate);
      if (t.loss_cap_pct != null) owed = Math.min(owed, b * mm * t.loss_cap_pct);
      if (t.loss_cap_revenue_pct != null && t.participant_revenue != null) owed = Math.min(owed, t.participant_revenue * t.loss_cap_revenue_pct);
      return -owed;
    }
    return 0;
  }
  function q(arr, p){ var s = arr.slice().sort(function(a,b){return a-b}); var i = (s.length - 1) * p, lo = Math.floor(i), hi = Math.ceil(i);
    return s[lo] + (s[hi] - s[lo]) * (i - lo); }
  function mean(a){ var m = 0; for (var i = 0; i < a.length; i++) m += a[i]; return a.length ? m / a.length : 0; }
  function base(d, s){
    var p = d.population, N = p.boot_rel.length, dn = new Array(N), bench = new Array(N);
    for (var i = 0; i < N; i++){ dn[i] = p.projected_do_nothing_pmpm * p.boot_rel[i] * (1 + s.trend + p.trend_sd * p.z_trend[i]);
      bench[i] = p.projected_do_nothing_pmpm * s.benchmark_factor * (1 + p.risk_drift_sd * p.z_drift[i]); }
    return {dn: dn, bench: bench, N: N, mm: p.member_months};
  }
  function parts(d, s){
    var B = base(d, s), N = B.N, arm = d.arm, saved = new Array(N), op = new Array(N), oapT = new Array(N), oapC = new Array(N), oapM = new Array(N), att = null;
    for (var i = 0; i < N; i++){ saved[i] = arm.saved_total[i] * s.scale; op[i] = 0; oapT[i] = 0; oapC[i] = 0; oapM[i] = 0; }
    Object.keys(arm.counts).forEach(function(prog){ var c = arm.counts[prog], u = arm.unit_costs[prog] || {};
      [['outreach','per_outreach'],['enrolled','per_enrolled'],['attended','per_attended'],['completed','per_completed']].forEach(function(fk){
        if (u[fk[1]]) for (var i = 0; i < N; i++) op[i] += c[fk[0]][i] * u[fk[1]]; }); });
    for (var i = 0; i < N; i++) op[i] *= s.scale * s.cost_multiplier;
    Object.keys(arm.oap || {}).forEach(function(prog){ var o = arm.oap[prog], c = arm.counts[prog];
      att = s.attainment == null ? o.simulated_attainment : s.attainment;
      var paid = o.annual_allowed * o.months / 12 * ((1 - o.withheld_share) + o.withheld_share * Math.min(1, att / o.outcome_attainment_threshold));
      for (var i = 0; i < N; i++){ var e = c.enrolled[i] * s.scale; oapT[i] += e * paid; oapC[i] += e * o.co_management_per_enrolled;
        if (o.counts_in_benchmark) oapM[i] += e * paid * o.medicare_share; } });
    return {B: B, saved: saved, op: op, oapT: oapT, oapC: oapC, oapM: oapM, att: att};
  }
  function evaluate(d, s){
    var P = parts(d, s), B = P.B, N = B.N, t = d.terms, sdn = new Array(N), sarm = new Array(N), net = new Array(N), cost = new Array(N);
    for (var i = 0; i < N; i++){
      cost[i] = B.dn[i] - P.saved[i] / B.mm + P.oapM[i] / B.mm;
      sdn[i] = settle(B.dn[i], B.bench[i], B.mm, t); sarm[i] = settle(cost[i], B.bench[i], B.mm, t);
      net[i] = (sarm[i] - sdn[i]) - P.op[i] + (s.aco_is_access_participant ? P.oapT[i] + P.oapC[i] : 0);
    }
    var qv = q(net, 1 - s.confidence), tail = net.filter(function(x){return x <= qv}), cvar = tail.length ? Math.max(0, -mean(tail)) : 0;
    var up = 0, dn = 0, ed = 0, ea = 0, ow = 0, pos = 0; for (var i = 0; i < N; i++){ up += Math.max(sarm[i], 0); dn += Math.max(-sarm[i], 0);
      if (sdn[i] > 0) ed++; if (sarm[i] > 0) ea++; if (sarm[i] < 0) ow++; if (net[i] > 0) pos++; }
    var m = mean(net), payer = 0; for (var i = 0; i < N; i++) payer += P.saved[i] - P.op[i] - P.oapT[i];
    return {expected_settlement_do_nothing: mean(sdn), expected_settlement_with_program: mean(sarm), expected_settlement_change: mean(sarm) - mean(sdn),
      p_earn_do_nothing: ed / N, p_earn_with_program: ea / N, p_owe_with_program: ow / N, aco_net_mean: m, aco_net_p05: q(net, .05),
      aco_net_p50: q(net, .5), aco_net_p95: q(net, .95), p_aco_net_positive: pos / N, var: Math.max(0, -qv), cvar: cvar,
      risk_adjusted_value: m - s.risk_aversion * cvar, upside_call_value: up / N, downside_put_cost: dn / N,
      gross_medical_savings_mean: mean(P.saved), operating_cost_mean: mean(P.op), oap_payments_mean: mean(P.oapT), co_management_mean: mean(P.oapC),
      payer_net_savings_mean: payer / N, attainment_used: P.att, benchmark_pmpm_mean: mean(B.bench), do_nothing_pmpm_mean: mean(B.dn), _net: net};
  }
  function nash(d, s){
    var ev = evaluate(d, s), P = parts(d, s), B = P.B, N = B.N, b = d.negotiation_bounds, cost = new Array(N), g = 0;
    for (var i = 0; i < N; i++){ cost[i] = B.dn[i] - P.saved[i] / B.mm; g += (B.bench[i] - cost[i]) * B.mm; }
    g /= N;
    var extra = -ev.operating_cost_mean + (s.aco_is_access_participant ? ev.oap_payments_mean + ev.co_management_mean : 0), best = null, grid = [];
    function payArr(sr, m){ var t = {sharing_rate: sr, msr: m, two_sided: false}, a = new Array(N); for (var i = 0; i < N; i++) a[i] = settle(cost[i], B.bench[i], B.mm, t); return a; }
    for (var mi = 0; b.msr_min + mi * 0.005 <= b.msr_max + 1e-9; mi++){ var m = Math.round((b.msr_min + mi * 0.005) * 1e4) / 1e4;
      for (var si = 0; b.sharing_rate_min + si * 0.01 <= b.sharing_rate_max + 1e-9; si++){ var sr = Math.round((b.sharing_rate_min + si * 0.01) * 1e4) / 1e4;
        var pay = payArr(sr, m), net = pay.map(function(x){return x + extra}), qv = q(net, 1 - s.confidence), tail = net.filter(function(x){return x <= qv});
        var cv = tail.length ? Math.max(0, -mean(tail)) : 0, ua = mean(net) - s.risk_aversion * cv, upv = g - mean(pay);
        var prod = ua > 0 && upv > 0 ? ua * upv : null, row = {sharing_rate: sr, msr: m, aco_value: ua, payer_value: upv, nash_product: prod};
        grid.push(row); if (prod !== null && (best === null || prod > best.nash_product)) best = row; } }
    var walk = null; for (var k = 0; k <= 100; k++){ var sr2 = k / 100, pay2 = payArr(sr2, b.msr_min); if (mean(pay2) + extra >= 0){ walk = sr2; break; } }
    return {nash: best, walk_away_sharing_rate: walk, walk_away_at_msr: b.msr_min, payer_expected_gross_savings: g, grid: grid};
  }
  return {settle: settle, evaluate: evaluate, nash: nash, quantile: q, mean: mean};
})();
"""
