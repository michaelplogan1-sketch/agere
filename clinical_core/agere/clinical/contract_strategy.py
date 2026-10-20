"""Risk contracts as financial options: value, compare, select a track and negotiate terms.

A shared-savings contract pays the ACO a call-like upside and, when two-sided, charges a put-like downside:

    payout = sharing x (benchmark - cost), capped, when savings reach the MSR
           - loss x (cost - benchmark), capped, when losses reach the MLR

MSR/MLR are qualification thresholds, not deductibles: eligible amounts are shared
from the first dollar. Marginal risk corridors are a separate contract mechanism.

(tiered corridors, program discounts and quality withholds follow vbc.settle exactly; payout() is its vectorised
twin and is tested against it). Each contract is valued by Monte Carlo over the performance-year cost:

  * population cost variability: member bootstrap of completion-adjusted allowed cost and member-months
    (vbc._claims / vbc._completion, as vbc.settlement_risk does);
  * trend: performance-year cost = base PMPM x (1 + trend), trend ~ N(stated or 0, sd); the sd defaults to the
    standard error of the observed annual trend in monthly completed PMPM (data-derived);
  * risk-score drift: the risk-adjusted benchmark moves by N(0, risk_drift_sd) (assumed 1% unless stated).

Per contract: expected payout and net value (after any stated care-management spend), SD, probability of loss,
VaR and CVaR at the stated confidence, the option decomposition (value of the upside call, cost of the downside
put) and Greeks-style finite-difference sensitivities on common random numbers (dValue/dBenchmark per $1 PMPM,
dValue/dTrend per percentage point, vega per +10% cost volatility).

Track selection ranks options by risk-adjusted value = E[net] - lambda x CVaR-loss (lambda stated, user-settable)
and finds the benchmark and trend at which the ranking flips. Negotiation computes the Nash bargaining solution
over sharing rate x MSR (ACO utility = risk-adjusted net value; payer utility = expected retained savings; both
above their reservation values) and the frontier of term combinations worth the same to us.

Benchmarks, offers, care-management spend and the counterparty's reservation terms are never invented: they come
from the question (or validate() raises 'needs supplied ...', which the router turns into a requires-input answer).
Program terms come from the cited rulebook (programs.py). Typical commercial ranges (cited) are used only as labelled
default bargaining bounds, never as a specific payer's offer.
"""
import math

import numpy as np

from . import programs, vbc

VERSION = 'contract-strategy/1'
FOCUS = ('compare', 'value', 'negotiate', 'capacity')
TARGETS = ('nash', 'break_even')
TYPICAL = 'commercial_shared_savings_typical'
MAX_OPTIONS = 8
DEFAULTS = {'risk_aversion': 0.5, 'confidence': 0.95, 'trend_pct': 0.0, 'risk_drift_sd': 0.01, 'draws': 2000, 'seed': 7,
            'cost_reduction_pct': 0.0, 'our_reservation': 0.0}
TREND_SD_FALLBACK = 0.03
TERM_KEYS = tuple(k for k in programs.SETTLEMENT_TERMS) + ('participant_revenue',)
NUMBER = (int, float)


def _num(v):
    return isinstance(v, NUMBER) and not isinstance(v, bool) and math.isfinite(v)


# ---- options: named program tracks (cited rulebook) or stated offer terms ---------------------------------
def _program_ref(program):
    """'MSSP ENHANCED 2026' | {'id','variant','year'} -> list of refs (a program named without its option expands to every option)."""
    if isinstance(program, dict):
        ref = {k: program.get(k) for k in ('id', 'variant', 'year')}
    else:
        docs = programs.load()
        ref = {'id': program, 'variant': None, 'year': None} if program in docs else programs.detect(program)
        if ref is None:
            raise ValueError(f'Unknown program: {program}')
    doc = programs.load().get(ref['id'])
    if doc is None: raise ValueError(f"Unknown program id: {ref['id']}")
    if doc['kind'] == 'typical_range':
        raise ValueError(f"Contract strategy needs supplied offer terms: {doc['label']} are typical published ranges, not a payer's offer")
    if not doc.get('settlement'):
        if doc.get('choose'): raise ValueError(f"Contract strategy needs supplied program track: {doc['choose']}")
        raise ValueError(f"{doc['label']} has no shared-savings settlement terms in the rulebook")
    variants = list(doc.get('variants') or {})
    if variants and not ref.get('variant') and not doc.get('default_variant'):
        return [{**ref, 'variant': v} for v in variants]
    return [ref]


def _clean_terms(t):
    out = {k: t[k] for k in TERM_KEYS if k in t and t[k] is not None}
    if 'program' in t: out['program'] = t['program']
    return out


def _option(o, i):
    if not isinstance(o, dict): raise ValueError('Each option must be an object with a program and/or terms')
    stated = _clean_terms(o.get('terms') or {})
    stated.pop('program', None)
    for k, v in stated.items():
        if k in ('two_sided',):
            if not isinstance(v, bool): raise ValueError(f'{k} must be true/false')
        elif k in ('corridors', 'msr_schedule', 'quality_mode'):
            continue
        elif not _num(v) or v < 0 or (k not in ('participant_revenue',) and v > 1.5):
            raise ValueError(f'Option term {k} must be a rate between 0 and 1 (got {v})')
    program = o.get('program')
    if program:
        out, refs = [], _program_ref(program)
        for ref in refs:
            res = programs.resolve(ref)
            terms = programs.contract_terms(res, stated)
            vbc.validate_settlement_terms(terms)
            label = o.get('label') if o.get('label') and len(refs) == 1 else res['label']
            out.append({'label': str(label)[:80], 'program': {'id': res['id'], 'variant': res['variant'], 'year': res['year']},
                        'terms': _clean_terms(terms),
                        'source': o.get('source') or ('program rulebook (cited)' + (' + question overrides' if stated else ''))})
        return out
    if not _num(stated.get('sharing_rate')) and not stated.get('corridors'):
        raise ValueError(f"Contract strategy needs supplied terms for option {o.get('label') or i + 1}: sharing_rate (or a named program track)")
    vbc.validate_settlement_terms(stated)
    return [{'label': str(o.get('label') or f'Offer {chr(65 + i)}')[:80], 'program': None, 'terms': stated, 'source': o.get('source') or 'question'}]


def _bounded(p, key, lo, hi, default=None, kind='number'):
    v = p.get(key)
    if v is None: v = default
    if v is None: return None
    if not _num(v) or not lo <= v <= hi: raise ValueError(f'{key} must be a {kind} between {lo} and {hi}')
    return int(v) if kind == 'integer' else float(v)


def validate(p):
    if not isinstance(p, dict): raise ValueError('contract_strategy parameters must be an object')
    focus = p.get('focus') or 'compare'
    if focus not in FOCUS: raise ValueError(f'focus must be one of {FOCUS}')
    target = p.get('target') or 'nash'
    if target not in TARGETS: raise ValueError(f'target must be one of {TARGETS}')
    bench = p.get('benchmark_pmpm')
    if not _num(bench) or bench <= 0:
        raise ValueError('Contract strategy needs supplied benchmark_pmpm (the contract-specific benchmark in $ PMPM; never assumed)')
    raw = p.get('options') or []
    if not isinstance(raw, list): raise ValueError('options must be a list')
    options = [x for i, o in enumerate(raw) for x in _option(o, i)]
    if len(options) > MAX_OPTIONS: raise ValueError(f'At most {MAX_OPTIONS} contract options')
    if focus != 'negotiate' and not options:
        raise ValueError('Contract strategy needs supplied contract options: named tracks (MSSP BASIC A-E / ENHANCED, ACO REACH '
                         'Professional / Global, LEAD) or offer terms (sharing rate, MSR, loss rate, caps)')
    spend = _bounded(p, 'care_management_pmpm', 0, 10000)
    if focus == 'negotiate' and target == 'break_even' and spend is None:
        raise ValueError('Contract strategy needs supplied care_management_pmpm (expected care-management spend, $ PMPM) to find the break-even sharing rate')
    cp = p.get('counterparty') or {}
    if not isinstance(cp, dict): raise ValueError('counterparty must be an object')
    counterparty = {}
    for k in ('sharing_rate_min', 'sharing_rate_max', 'msr_min', 'msr_max'):
        if cp.get(k) is not None:
            if not _num(cp[k]) or not 0 <= cp[k] <= 1: raise ValueError(f'counterparty {k} must be a rate between 0 and 1')
            counterparty[k] = float(cp[k])
    if cp.get('reservation_value') is not None:
        if not _num(cp['reservation_value']): raise ValueError('counterparty reservation_value must be a number ($)')
        counterparty['reservation_value'] = float(cp['reservation_value'])
    if counterparty.get('sharing_rate_min', 0) > counterparty.get('sharing_rate_max', 1) or counterparty.get('msr_min', 0) > counterparty.get('msr_max', 1):
        raise ValueError('counterparty bounds are inverted')
    period = p.get('period') or {}
    if period and not (isinstance(period, dict) and set(period) <= {'start', 'end'}):
        raise ValueError('period needs start/end dates')
    stated = list(p['stated']) if isinstance(p.get('stated'), list) else \
        sorted(k for k in ('risk_aversion', 'confidence', 'trend_pct', 'trend_sd', 'risk_drift_sd', 'cost_reduction_pct',
                           'care_management_pmpm', 'risk_capacity', 'our_reservation') if p.get(k) is not None)
    return {'focus': focus, 'target': target, 'benchmark_pmpm': float(bench), 'options': options,
            'care_management_pmpm': spend,
            'risk_aversion': _bounded(p, 'risk_aversion', 0, 10, DEFAULTS['risk_aversion']),
            'confidence': _bounded(p, 'confidence', 0.5, 0.999, DEFAULTS['confidence']),
            'trend_pct': _bounded(p, 'trend_pct', -0.5, 0.5, DEFAULTS['trend_pct']),
            'trend_sd': _bounded(p, 'trend_sd', 0, 0.5),
            'risk_drift_sd': _bounded(p, 'risk_drift_sd', 0, 0.2, DEFAULTS['risk_drift_sd']),
            'cost_reduction_pct': _bounded(p, 'cost_reduction_pct', 0, 0.5, DEFAULTS['cost_reduction_pct']),
            'risk_capacity': _bounded(p, 'risk_capacity', 0, 1e12),
            'our_reservation': _bounded(p, 'our_reservation', -1e12, 1e12, DEFAULTS['our_reservation']),
            'counterparty': counterparty,
            'draws': _bounded(p, 'draws', 200, 20000, DEFAULTS['draws'], 'integer'),
            'seed': _bounded(p, 'seed', 0, 2 ** 31 - 1, DEFAULTS['seed'], 'integer'),
            'period': {k: str(v)[:10] for k, v in period.items() if v},
            'stated': stated}


# ---- the option payoff (vectorised vbc.settle) -----------------------------------------------------------
def _corridor(pct, corridors):
    total = np.zeros_like(pct)
    for c in corridors:
        hi = np.minimum(pct, float(c['to'])) if c.get('to') is not None else pct
        total += float(c['rate']) * np.maximum(0.0, hi - float(c['from']))
    return total


def payout(actual, benchmark, mm, t):
    """Net settlement for arrays of actual PMPM and benchmark PMPM (before program discount/withhold): + paid to the
    ACO, - owed by it. Same arithmetic as vbc.settle, element by element."""
    vbc.validate_settlement_terms(t)
    actual = np.asarray(actual, dtype=float)
    bench = np.broadcast_to(np.asarray(benchmark, dtype=float), actual.shape)
    if not np.isfinite(actual).all() or (actual < 0).any() or not np.isfinite(bench).all() or (bench <= 0).any():
        raise ValueError('Settlement simulation needs finite nonnegative costs and positive benchmarks')
    if not _num(mm) or mm < 0: raise ValueError('Settlement member-months must be finite and nonnegative')
    if not mm: return np.zeros_like(actual)
    withhold = float(t.get('quality_withhold_pct') or 0)
    earned = float(t.get('quality_score', 1.0)) if t.get('quality_mode') == 'withhold' else 1.0
    b = bench * (1 - float(t.get('discount_pct') or 0) - withhold * (1 - earned))
    with np.errstate(divide='ignore', invalid='ignore'):
        sp = np.where(b != 0, (b - actual) / b, 0.0)
    total = (b - actual) * mm
    default_msr = 0.0 if t.get('corridors') else 0.02
    msr, mlr = float(t.get('msr', default_msr)), float(t.get('mlr', t.get('msr', default_msr)))
    quality = float(t.get('quality_score', 1.0)) if t.get('quality_mode', 'sharing_multiplier') == 'sharing_multiplier' else 1.0
    if t.get('corridors'): shared = b * mm * _corridor(np.maximum(sp, 0), t['corridors']) * quality
    else: shared = total * float(t.get('sharing_rate', 0)) * quality
    if t.get('savings_cap_pct') is not None: shared = np.minimum(shared, b * mm * float(t['savings_cap_pct']))
    up = (sp >= msr) & (sp > 0)
    owed = np.zeros_like(actual)
    down = np.zeros(actual.shape, dtype=bool)
    if t.get('two_sided'):
        if t.get('corridors'): owed = b * mm * _corridor(np.maximum(-sp, 0), t['corridors'])
        else: owed = -total * float(t['loss_rate'])
        if t.get('loss_cap_pct') is not None: owed = np.minimum(owed, b * mm * float(t['loss_cap_pct']))
        if t.get('loss_cap_revenue_pct') is not None and t.get('participant_revenue') is not None:
            owed = np.minimum(owed, float(t['participant_revenue']) * float(t['loss_cap_revenue_pct']))
        down = (-sp >= mlr) & (sp < 0)
    return np.where(up, shared, np.where(down, -owed, 0.0))


def _resolve_msr(t, members):
    """Program MSR/MLR that vary with the assigned population (vbc._shared_savings does the same)."""
    if t.get('msr_schedule') and 'msr' not in t:
        rate = programs.variable_msr(members, t['msr_schedule'])
        t = {**t, 'msr': rate, **({'mlr': rate} if 'mlr' not in t else {})}
    return {k: v for k, v in t.items() if k != 'msr_schedule'}


def risk_measures(net, confidence):
    """VaR and CVaR of the loss (positive = dollars at risk) at the given confidence."""
    q = float(np.quantile(net, 1 - confidence))
    tail = net[net <= q]
    return max(0.0, -q), max(0.0, -float(tail.mean())) if tail.size else 0.0


def value(cost, bench, mm, terms, spend_total=0.0, confidence=0.95, risk_aversion=0.5):
    """Value one contract on simulated (cost, benchmark) draws -> (stats, payout array)."""
    pay = payout(cost, bench, mm, terms)
    net = pay - spend_total
    var, cvar = risk_measures(net, confidence)
    q = lambda a, v: float(np.quantile(a, v))
    mean = float(net.mean())
    return ({'expected_payout': float(pay.mean()), 'expected_net': mean, 'sd': float(net.std()),
             'p_earn_savings': float((pay > 0).mean()), 'p_owe_loss': float((pay < 0).mean()), 'p_net_loss': float((net < 0).mean()),
             'value_at_risk': var, 'cvar': cvar, 'upside_value': float(np.maximum(pay, 0).mean()),
             'downside_cost': float(np.maximum(-pay, 0).mean()), 'net_p5': q(net, .05), 'net_p50': q(net, .5), 'net_p95': q(net, .95),
             'risk_adjusted_value': mean - risk_aversion * cvar}, pay)


# ---- the population cost distribution --------------------------------------------------------------------
def member_costs(db, people, start, end):
    """Completion-adjusted allowed cost and member-months per enrolled member (vbc.settlement_risk's inputs)."""
    claims = vbc._claims(db, start, end)
    completion, _ = vbc._completion(claims, end)
    months = {pid: set(info['months']) for pid, info in people.items() if info['months']}
    cost = dict.fromkeys(months, 0.0)
    for pid, month, allowed, _, _, _ in claims:
        if month in months.get(pid, ()): cost[pid] += allowed / 100 * completion.get(month, 1.0)
    pids = sorted(months)
    return np.array([cost[x] for x in pids]), np.array([len(months[x]) for x in pids], dtype=float)


def observed_trend(db, people_ids, as_of, end, years=3):
    """Annual trend in monthly completed PMPM (log-linear OLS) and its standard error, over up to `years` years."""
    start = f'{int(end[:4]) - years}{end[4:7]}-01'
    people = vbc.eligibility(db, as_of, start, end)
    if people_ids is not None: people = {k: v for k, v in people.items() if k in people_ids}
    claims = vbc._claims(db, start, end)
    completion, _ = vbc._completion(claims, end)
    members, cost = {}, {}
    for info in people.values():
        for m in info['months']: members[m] = members.get(m, 0) + 1
    months = {pid: set(i['months']) for pid, i in people.items()}
    for pid, month, allowed, *_ in claims:
        if month in months.get(pid, ()): cost[month] = cost.get(month, 0.0) + allowed / 100 * completion.get(month, 1.0)
    series = sorted((m, cost[m] / members[m]) for m in members if cost.get(m, 0) > 0 and members[m] >= 10)
    if len(series) < 12: return {'months': len(series), 'annual_trend': None, 'annual_trend_se': None}
    t = np.arange(len(series), dtype=float)
    y = np.log([v for _, v in series])
    X = np.column_stack([np.ones_like(t), t])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    s2 = float(resid @ resid) / max(1, len(y) - 2)
    se = math.sqrt(s2 / float(((t - t.mean()) ** 2).sum()))
    return {'months': len(series), 'first_month': series[0][0], 'last_month': series[-1][0],
            'annual_trend': math.exp(12 * beta[1]) - 1, 'annual_trend_se': 12 * se}


class Simulation:
    """Common random numbers for every contract and every finite difference: member bootstrap x trend x risk drift."""

    def __init__(self, c, m, draws, seed, trend_pct, trend_sd, risk_drift_sd, cost_reduction_pct=0.0, chunk=250):
        rng = np.random.default_rng(seed)
        n = len(c)
        boot = np.empty(draws)
        for s in range(0, draws, chunk):
            k = min(chunk, draws - s)
            idx = rng.integers(0, n, size=(k, n))
            boot[s:s + k] = c[idx].sum(axis=1) / m[idx].sum(axis=1)
        self.boot, self.z_trend, self.z_drift = boot, rng.standard_normal(draws), rng.standard_normal(draws)
        self.mm, self.members, self.base_pmpm = float(m.sum()), n, float(c.sum() / m.sum())
        self.trend_pct, self.trend_sd, self.risk_drift_sd, self.reduction = trend_pct, trend_sd, risk_drift_sd, cost_reduction_pct

    @classmethod
    def from_draws(cls, cost, bench_mult=None, mm=1.0, members=1):
        """A simulation over given cost draws (tests and closed-form checks): no bootstrap, trend or drift."""
        self = cls.__new__(cls)
        cost = np.asarray(cost, dtype=float)
        self.boot, self.z_trend = cost, np.zeros_like(cost)
        self.z_drift = np.zeros_like(cost) if bench_mult is None else np.asarray(bench_mult, dtype=float)
        self.mm, self.members, self.base_pmpm = float(mm), members, float(cost.mean())
        self.trend_pct, self.trend_sd, self.risk_drift_sd, self.reduction = 0.0, 0.0, 1.0 if bench_mult is not None else 0.0, 0.0
        return self

    def scenario(self, benchmark, trend=None, vol=1.0):
        trend = self.trend_pct if trend is None else trend
        cost = self.boot * (1 + trend + self.trend_sd * self.z_trend) * (1 - self.reduction)
        if vol != 1.0:
            mu = cost.mean()
            cost = mu + vol * (cost - mu)
        # Cost volatility around sparse member costs can produce negative draws;
        # medical spending scenarios have a zero lower bound, disclosed below.
        return np.maximum(cost, 0.0), benchmark * (1 + self.risk_drift_sd * self.z_drift)


def _evaluate(sim, terms, benchmark, spend, p, trend=None, vol=1.0):
    cost, bench = sim.scenario(benchmark, trend, vol)
    return value(cost, bench, sim.mm, terms, spend, p['confidence'], p['risk_aversion'])


def greeks(sim, terms, benchmark, spend, p):
    """Finite-difference sensitivities of the expected net value on common random numbers."""
    h = min(0.5 * benchmark, max(1.0, 0.005 * benchmark))
    f = lambda **kw: _evaluate(sim, terms, kw.pop('b', benchmark), spend, p, **kw)[0]['expected_net']
    t0 = sim.trend_pct
    return {'delta_benchmark_per_usd_pmpm': (f(b=benchmark + h) - f(b=benchmark - h)) / (2 * h),
            'delta_trend_per_point': (f(trend=t0 + 0.01) - f(trend=t0 - 0.01)) / 2,
            'vega_per_10pct_volatility': (f(vol=1.1) - f(vol=0.9)) / 2}


def _root(fn, lo, hi, center, n=41, iters=40):
    """Root of fn on [lo, hi] closest to center (grid scan, then bisection) or None."""
    xs = np.linspace(lo, hi, n)
    ys = [fn(x) for x in xs]
    brackets = [(xs[i], xs[i + 1], ys[i], ys[i + 1]) for i in range(n - 1) if ys[i] == 0 or ys[i] * ys[i + 1] < 0]
    if not brackets: return None
    a, b, fa, _ = min(brackets, key=lambda br: abs((br[0] + br[1]) / 2 - center))
    if fa == 0: return float(a)
    for _ in range(iters):
        mid = (a + b) / 2
        fm = fn(mid)
        if fm == 0: return float(mid)
        if (fm > 0) == (fa > 0): a, fa = mid, fm
        else: b = mid
    return float((a + b) / 2)


def break_even(sim, best, other, benchmark, spend, p):
    """Benchmark ($ PMPM) and trend at which `other` ties `best` on risk-adjusted value (the ranking flips there)."""
    rav = lambda t, **kw: _evaluate(sim, t, kw.pop('b', benchmark), spend, p, **kw)[0]['risk_adjusted_value']
    diff_b = lambda b: rav(best, b=b) - rav(other, b=b)
    diff_t = lambda tr: rav(best, trend=tr) - rav(other, trend=tr)
    t0 = sim.trend_pct
    return {'benchmark_pmpm': _root(diff_b, 0.75 * benchmark, 1.25 * benchmark, benchmark),
            'trend_pct': _root(diff_t, t0 - 0.15, t0 + 0.15, t0)}


def max_loss_rate(sim, terms, benchmark, spend, p, capacity):
    """Largest loss-sharing rate (0-1) keeping VaR at the stated confidence within the stated risk capacity."""
    if not terms.get('two_sided') or terms.get('corridors'): return None
    var = lambda r: _evaluate(sim, {**terms, 'loss_rate': r}, benchmark, spend, p)[0]['value_at_risk']
    if var(1.0) <= capacity: return 1.0
    if var(0.0) > capacity: return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if var(mid) <= capacity else (lo, mid)
    return lo


# ---- negotiation: Nash bargaining over sharing rate x MSR ---------------------------------------------------
def typical_bounds():
    """Cited typical commercial ranges (NOT any payer's offer) used as default bargaining bounds."""
    try:
        res = programs.resolve(TYPICAL)
        rng = programs.value(res, 'provider_sharing_rate_range')
        cites = programs.sources(res, {'provider_sharing_rate_range', 'msr_practice'})
        return {'sharing_rate_min': float(rng['low']), 'sharing_rate_max': float(rng['high']), 'msr_min': 0.0, 'msr_max': 0.05,
                'label': f"typical published range ({res['label']}): sharing {rng['low']:.0%}-{rng['high']:.0%}, MSR none to 5% "
                         '(payer retentions of 2% and 5% observed); not this payer\'s offer',
                'sources': cites}
    except (OSError, ValueError, KeyError, TypeError):
        return {'sharing_rate_min': 0.3, 'sharing_rate_max': 0.8, 'msr_min': 0.0, 'msr_max': 0.05,
                'label': 'typical range 30-80% sharing, MSR 0-5% (rulebook unavailable); not this payer\'s offer', 'sources': []}


def negotiate(sim, base, benchmark, spend, p, counterparty=None, our_reservation=0.0):
    """Nash bargaining solution over (sharing rate, MSR) within the counterparty's bounds, the break-even sharing rate
    and the frontier of equal-value-to-us terms. ACO utility = E[net] - lambda x CVaR; payer utility = expected
    retained savings E[(benchmark - cost) x member-months] - E[payout]; each must beat its reservation value."""
    cp = dict(counterparty or {})
    typical = typical_bounds()
    bounds, provenance = {}, {}
    for k in ('sharing_rate_min', 'sharing_rate_max', 'msr_min', 'msr_max'):
        bounds[k] = cp[k] if k in cp else typical[k]
        provenance[k] = 'stated' if k in cp else 'typical range default'
    d_p = float(cp.get('reservation_value', 0.0))
    base = {k: v for k, v in base.items() if k not in ('corridors', 'program')}
    base.setdefault('two_sided', False)
    cost, bench = sim.scenario(benchmark)
    b_eff = bench * (1 - float(base.get('discount_pct') or 0))
    gross = float(((b_eff - cost) * sim.mm).mean())  # payer's expected savings before sharing
    S = np.round(np.arange(bounds['sharing_rate_min'], bounds['sharing_rate_max'] + 1e-9, 0.01), 4)
    M = np.round(np.arange(bounds['msr_min'], bounds['msr_max'] + 1e-9, 0.005), 4)
    terms_at = lambda s, m: {**base, 'sharing_rate': float(s), 'msr': float(m), **({'mlr': float(m)} if base.get('two_sided') and 'mlr' not in base else {})}
    grid, best = [], None
    for m in M:
        for s in S:
            st, _ = value(cost, bench, sim.mm, terms_at(s, m), spend, p['confidence'], p['risk_aversion'])
            ua, up = st['risk_adjusted_value'], gross - st['expected_payout']
            prod = (ua - our_reservation) * (up - d_p) if ua > our_reservation and up > d_p else None
            row = {'sharing_rate': float(s), 'msr': float(m), 'aco_value': ua, 'aco_expected_net': st['expected_net'],
                   'payer_value': up, 'nash_product': prod}
            grid.append(row)
            if prod is not None and (best is None or prod > best['nash_product']): best = row
    # frontier: for each MSR, the sharing rate worth a given expected net value to us (break-even = 0; the Nash value)
    fine = np.round(np.arange(0, 1.0 + 1e-9, 0.01), 4)
    frontier = []
    for m in M:
        ev = np.array([value(cost, bench, sim.mm, terms_at(s, m), spend, p['confidence'], 0.0)[0]['expected_net'] for s in fine])
        r4 = lambda v: None if v is None else round(v, 4)
        row = {'msr': float(m), 'break_even_sharing_rate': r4(_interp_level(fine, ev, 0.0))}
        if best is not None: row['sharing_rate_for_nash_value'] = r4(_interp_level(fine, ev, best['aco_expected_net']))
        frontier.append(row)
    stated_msr = float(base['msr']) if _num(base.get('msr')) else bounds['msr_min']
    ev_stated = np.array([value(cost, bench, sim.mm, terms_at(s, stated_msr), spend, p['confidence'], 0.0)[0]['expected_net'] for s in fine])
    return {'bounds': bounds, 'bounds_provenance': provenance, 'typical_range_label': typical['label'], 'typical_sources': typical['sources'],
            'our_reservation': our_reservation, 'payer_reservation': d_p,
            'payer_reservation_provenance': 'stated' if 'reservation_value' in cp else 'default 0 (payer walks away to fee-for-service)',
            'payer_expected_gross_savings': gross, 'nash': best, 'agreement_zone': best is not None,
            'break_even_sharing_rate': (lambda v: None if v is None else round(v, 4))(_interp_level(fine, ev_stated, 0.0)),
            'break_even_at_msr': stated_msr, 'frontier': frontier, 'grid_points': len(grid),
            'surface': [{k: (round(v, 2) if isinstance(v, float) and k not in ('sharing_rate', 'msr') else v) for k, v in r.items()}
                        for r in grid if abs(r['msr'] - (best or {}).get('msr', stated_msr)) < 1e-9]}


def _interp_level(xs, ys, level):
    """Smallest x where the (monotone non-decreasing in x) curve ys reaches level, linearly interpolated; None if never."""
    for i in range(len(xs)):
        if ys[i] >= level:
            if i == 0: return float(xs[0]) if ys[0] == level or level <= ys[0] else None
            x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
            return float(x0 + (level - y0) / (y1 - y0) * (x1 - x0)) if y1 != y0 else float(x1)
    return None


# ---- execution -------------------------------------------------------------------------------------------
def _histograms(nets, bins=28):
    allv = np.concatenate(list(nets.values()))
    lo, hi = float(np.quantile(allv, 0.005)), float(np.quantile(allv, 0.995))
    if hi <= lo: lo, hi = lo - 1, hi + 1
    edges = np.linspace(lo, hi, bins + 1)
    out = {}
    for label, net in nets.items():
        counts, _ = np.histogram(np.clip(net, lo, hi), bins=edges)
        out[label] = [round(float(c) / len(net), 5) for c in counts]
    return {'edges': [round(float(e), 2) for e in edges], 'shares': out}


def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    db = vbc._connect(path)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        start, end = vbc._period(p, as_of)
        people = vbc.eligibility(db, as_of, start, end)
        keep = set(patient_ids) if patient_ids is not None else None
        if keep is not None: people = {k: v for k, v in people.items() if k in keep}
        c, m = member_costs(db, people, start, end)
        trend_obs = observed_trend(db, keep, as_of, end)
    finally:
        db.close()
    base_report = {'schema': VERSION, 'parameters': p, 'period': {'start': start, 'end': end}, 'members_in_scope': int(len(c))}
    if not len(c) or m.sum() == 0 or c.sum() <= 0:
        return {**base_report, 'status': 'unsupported', 'reason': 'No enrolled members with paid claims in the base period',
                'options': [], 'method': 'Monte Carlo contract valuation', 'limitations': []}
    trend_sd = p['trend_sd'] if p['trend_sd'] is not None else (
        min(0.10, max(0.005, trend_obs['annual_trend_se'])) if trend_obs.get('annual_trend_se') else TREND_SD_FALLBACK)
    sim = Simulation(c, m, p['draws'], p['seed'], p['trend_pct'], trend_sd, p['risk_drift_sd'], p['cost_reduction_pct'])
    B, spend = p['benchmark_pmpm'], (p['care_management_pmpm'] or 0.0) * sim.mm
    rows, nets = [], {}
    for o in p['options']:
        terms = _resolve_msr(o['terms'], sim.members)
        st, pay = _evaluate(sim, terms, B, spend, p)
        row = {'option': o['label'], **{k: round(v, 4 if k.startswith('p_') else 2) for k, v in st.items()},
               **{k: round(v, 2) for k, v in greeks(sim, terms, B, spend, p).items()},
               'msr_used': terms.get('msr', 0.0 if terms.get('corridors') else 0.02), 'two_sided': bool(terms.get('two_sided')),
               'source': o['source']}
        if p['risk_capacity'] is not None:
            row['within_risk_capacity'] = st['value_at_risk'] <= p['risk_capacity']
            mlr = max_loss_rate(sim, terms, B, spend, p, p['risk_capacity'])
            if mlr is not None: row['max_loss_rate_within_capacity'] = round(mlr, 4)
        row['_terms'] = terms
        rows.append(row)
        nets[o['label']] = pay - spend
    rows.sort(key=lambda r: -r['risk_adjusted_value'])
    for i, r in enumerate(rows): r['rank'] = i + 1
    flips = []
    if len(rows) >= 2:
        best = rows[0]
        for r in rows[1:]:
            be = break_even(sim, best['_terms'], r['_terms'], B, spend, p)
            flips.append({'leader': best['option'], 'challenger': r['option'], 'flip_benchmark_pmpm': None if be['benchmark_pmpm'] is None else round(be['benchmark_pmpm'], 2),
                          'flip_trend_pct': None if be['trend_pct'] is None else round(be['trend_pct'], 4)})
    negotiation = None
    if p['focus'] == 'negotiate':
        base = next((dict(r['_terms']) for r in rows if not r['_terms'].get('corridors')), {'two_sided': False})
        negotiation = negotiate(sim, base, B, spend, p, p['counterparty'], p['our_reservation'])
        if negotiation['nash']: negotiation['nash'] = {k: (round(v, 2) if isinstance(v, float) and k not in ('sharing_rate', 'msr') else v)
                                                        for k, v in negotiation['nash'].items()}
        for k in ('payer_expected_gross_savings',): negotiation[k] = round(negotiation[k], 2)
    sources = []
    for o in p['options']:
        for s in ((o['terms'].get('program') or {}).get('sources') or []):
            if s['url'] not in {x['url'] for x in sources}: sources.append(s)
    terms_out = {r['option']: {k: v for k, v in r.pop('_terms').items() if k != 'program'} for r in rows}
    assumptions = [
        f"Benchmark ${B:,.2f} PMPM as supplied; risk-score drift moves it by N(0, {p['risk_drift_sd']:.1%})"
        + (' (stated).' if 'risk_drift_sd' in p['stated'] else ' (assumed default; set risk_drift_sd).'),
        f"Performance-year cost = base-period completion-adjusted allowed PMPM ${sim.base_pmpm:,.2f} x (1 + trend), trend ~ N({p['trend_pct']:+.1%}, {trend_sd:.1%})"
        + (' (trend stated).' if 'trend_pct' in p['stated'] else ' (no trend stated: base cost carried forward).')
        + (' Trend SD stated.' if 'trend_sd' in p['stated'] else
           ' Trend SD = standard error of the observed annual trend.' if trend_obs.get('annual_trend_se') else
           f' Trend SD {TREND_SD_FALLBACK:.0%} assumed (fewer than 12 usable months).'),
        f"Risk-adjusted value = E[net] - {p['risk_aversion']:g} x CVaR{p['confidence']:.0%}"
        + (' (lambda stated).' if 'risk_aversion' in p['stated'] else ' (lambda default 0.5; set risk_aversion).'),
        ('Care-management spend $%s PMPM as supplied.' % f"{p['care_management_pmpm']:,.2f}") if p['care_management_pmpm'] is not None
        else 'No care-management spend supplied: net value = settlement payout.',
        'Program terms from the cited rulebook; full quality credit is assumed unless stated, not established. '
        'Participant-revenue loss caps require supplied participant revenue.',
        'Simulated spending is floored at zero after trend and volatility adjustments; this is a modeling assumption.']
    if p['cost_reduction_pct']: assumptions.append(f"Cost reduced by {p['cost_reduction_pct']:.1%} (stated program effect).")
    report = {**base_report, 'status': 'answered', 'options': rows, 'terms': terms_out, 'ranking_flips': flips, 'negotiation': negotiation,
              'distribution': _histograms(nets) if nets else None,
              'simulation': {'draws': p['draws'], 'seed': p['seed'], 'members': sim.members, 'member_months': sim.mm,
                             'base_pmpm': round(sim.base_pmpm, 2), 'trend_sd': round(trend_sd, 4), 'risk_drift_sd': p['risk_drift_sd'],
                             'observed_trend': {k: (round(v, 4) if isinstance(v, float) else v) for k, v in trend_obs.items()}},
              'risk_aversion': p['risk_aversion'], 'confidence': p['confidence'], 'assumptions': assumptions, 'sources': sources,
              'method': ('Contracts valued as options on performance-year cost: payout = sharing x max(benchmark - cost - MSR, 0) capped (call) minus '
                         'loss x max(cost - benchmark - MLR, 0) capped (put), with the exact vbc.settle arithmetic (corridors, discounts, withholds, caps). '
                         f"{p['draws']:,} Monte Carlo draws (seed {p['seed']}): member bootstrap of completion-adjusted allowed cost x trend x risk-score drift, "
                         'common random numbers across contracts. VaR/CVaR of net value; Greeks by central finite differences; ranking by '
                         'E[net] - lambda x CVaR with the benchmark and trend at which it flips (bisection)'
                         + ('; Nash bargaining over sharing rate x MSR (product of gains over reservation values) with the equal-value frontier.'
                            if negotiation else '.')),
              'limitations': ['Benchmark rebasing, ratchets, attribution churn beyond the member bootstrap, rule changes and counterparty '
                              'behaviour are not modelled.', 'Typical ranges are public survey ranges, never a specific payer\'s offer.',
                              'Synthetic data: values illustrate the method, not a real contract decision.']}
    return report


def summarize(report):
    """-> (one-paragraph answer, table rows)."""
    if report.get('status') != 'answered':
        return report.get('reason') or 'Contract strategy could not be computed.', []
    rows, p = report['options'], report['parameters']
    lam, conf = report['risk_aversion'], report['confidence']
    usd = lambda v: ('-' if v < 0 else '') + f'${abs(v):,.0f}'
    parts = []
    if rows:
        b = rows[0]
        parts.append(f"{'Best risk-adjusted' if len(rows) > 1 else 'Value'}: {b['option']} — expected net {usd(b['expected_net'])} "
                     f"(upside call worth {usd(b['upside_value'])}, downside put costs {usd(b['downside_cost'])}), {conf:.0%} CVaR loss {usd(b['cvar'])}, "
                     f"P(loss) {b['p_net_loss']:.0%}, risk-adjusted {usd(b['risk_adjusted_value'])} at lambda {lam:g}.")
        if b['risk_adjusted_value'] < p['our_reservation']:
            parts.append(f"No option beats staying out (risk-adjusted value below {usd(p['our_reservation'])} at lambda {lam:g}); "
                         f"{b['option']} is the least costly if we must take one.")
        for r in rows[1:3]:
            parts.append(f"#{r['rank']} {r['option']}: expected net {usd(r['expected_net'])}, CVaR {usd(r['cvar'])}, risk-adjusted {usd(r['risk_adjusted_value'])}.")
        for f in report['ranking_flips'][:2]:
            bits = []
            if f['flip_benchmark_pmpm'] is not None: bits.append(f"benchmark ${f['flip_benchmark_pmpm']:,.2f} PMPM")
            if f['flip_trend_pct'] is not None: bits.append(f"cost trend {f['flip_trend_pct']:+.1%}")
            parts.append(f"{f['challenger']} overtakes {f['leader']} at " + ' or '.join(bits) + '.' if bits else
                         f"{f['leader']} stays ahead of {f['challenger']} across benchmark ±25% and trend ±15 points.")
        if p['focus'] == 'capacity':
            parts.append(f"At {conf:.0%} confidence the loss to plan for (VaR) is " + '; '.join(f"{r['option']} {usd(r['value_at_risk'])}" for r in rows[:4]) + '.')
            if p['risk_capacity'] is not None:
                fit = [r['option'] for r in rows if r.get('within_risk_capacity')]
                parts.append(f"Within the stated ${p['risk_capacity']:,.0f} capacity: {', '.join(fit) if fit else 'none'}.")
    n = report.get('negotiation')
    if n:
        if n['nash']:
            parts.append(f"Nash bargaining solution: {n['nash']['sharing_rate']:.0%} sharing with a {n['nash']['msr']:.1%} MSR "
                         f"(our risk-adjusted value {usd(n['nash']['aco_value'])}, payer retains {usd(n['nash']['payer_value'])}).")
        else:
            parts.append('No agreement zone: no term combination within the bounds beats both reservation values.')
        if n['break_even_sharing_rate'] is not None and p['care_management_pmpm'] is not None:
            parts.append(f"Break-even sharing rate at a {n['break_even_at_msr']:.1%} MSR: {n['break_even_sharing_rate']:.1%} "
                         f"(covers ${p['care_management_pmpm']:,.2f} PMPM care-management spend in expectation).")
        elif p['care_management_pmpm'] is not None:
            parts.append('No sharing rate up to 100% covers the stated care-management spend in expectation.')
        if any(v != 'stated' for v in n['bounds_provenance'].values()):
            parts.append('Bounds: ' + n['typical_range_label'] + '.')
    table = [{k: v for k, v in r.items()} for r in rows]
    return ' '.join(parts), table
