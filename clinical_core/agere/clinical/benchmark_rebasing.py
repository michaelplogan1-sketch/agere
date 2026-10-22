"""Benchmark ratchet and rebasing simulator for MSSP and LEAD ('benchmark_rebasing' operation): "should we renew early?"

Population: Medicare FFS members (member_month product medicare_ffs / dual_ffs; payer WH-PAYER-MSSP when present) in scope.
Observed expenditure: plan-paid claims PMPM per calendar year, IBNR-completed (mlr.cells -> vbc._claims / vbc._completion);
the current partial year is a completed run rate. Trend g = CAGR across full observed years (trend_pct overrides).

Rulebook (config/agere/programs, via programs.py; ids cited in every answer):
  historical benchmark   benchmark years BY1..BY3 = the three years before an agreement starts, trended to BY3 at g and weighted
                         10/30/60 (42 CFR 425.652(a)); years before the data are back-cast at g (stated).
  update                 three-way blend: (1 - ACPT weight) x national/regional trend + ACPT weight x ACPT
                         (mssp_program_rules benchmark.acpt_weight, cfr_652).
  regional adjustment    regional_weight x (regional per capita - historical), capped at +5% / -1.5% of national per capita
                         (benchmark.regional_adjustment_positive_cap / _negative_cap, cfr_656). Regional and national per capita
                         default to the population's own expenditure without the ACO's savings (stated).
  prior savings adjust.  at renewal: lesser of 50% of average per capita savings in the prior agreement or 5% of national per capita
                         (benchmark.prior_savings_adjustment, cfr_658); positive regional adjustment -> the greater of the two,
                         negative -> PSA offsets it.
  rebasing (the ratchet) each new agreement period re-derives the historical benchmark from the latest three years, so savings the
                         ACO achieved lower its next benchmark.
  LEAD                   benchmark set once at entry, no rebasing for the model's 10 years, ACPT-blended trend, risk-score growth
                         cap 3% (lead benchmark_design, lead_53); settlement by LEAD corridors, discount and quality withhold.
Scenarios over the horizon from the decision year: stay to the end of the current agreement then renew (rebase) every
agreement_years; renew early at the decision year; enter LEAD at the decision year. ACO actual PMPM = counterfactual x (1 - savings_pct)
from the decision year. Shared savings/losses each performance year via vbc.settle with programs.contract_terms (MSSP MSR from the
rulebook's population schedule); NPV at discount_rate; the savings rate at which the recommendation flips is found by bisection.
"""
import math
import re

from . import revenue_common as rc

VERSION = 'benchmark-rebasing/1'
OP = 'benchmark_rebasing'
TRACKS = ('enhanced', 'basic_a', 'basic_b', 'basic_c', 'basic_d', 'basic_e')
LEAD_VARIANTS = ('Global', 'Professional', 'Global higher-spending')
SCENARIOS = ('stay', 'renew_early', 'lead')
BY_WEIGHTS = (0.1, 0.3, 0.6)
FFS = ('medicare_ffs', 'dual_ffs')
RULE_NAMES = ('benchmark.acpt_weight', 'benchmark.regional_adjustment_positive_cap', 'benchmark.regional_adjustment_negative_cap',
              'benchmark.prior_savings_adjustment', 'benchmark.risk_score_growth_cap')

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'MSSP / LEAD BENCHMARK REBASING and RATCHET scenarios: should we renew early, stay to term or enter LEAD; per-year '
                  'benchmark, savings and shared savings from the program rulebooks; NPV and break-even savings rate.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'track': 'enhanced|basic_a..basic_e', 'lead_variant': 'Global|Professional|Global higher-spending',
                           'agreement_start': 0, 'agreement_years': 5, 'decision_year': 0, 'horizon_years': 10, 'savings_pct': 0.02,
                           'trend_pct': None, 'acpt_pct': None, 'regional_weight': 0.35, 'regional_pmpm': 0, 'national_pmpm': 0,
                           'discount_rate': 0.05, 'participant_revenue': 0, 'scenarios': ['stay', 'renew_early', 'lead']},
            'meaning': 'Simulate the MSSP benchmark ratchet: historical benchmark = BY 10/30/60 of the three years before each agreement, '
                       'trended; ACPT-blended update; regional adjustment capped +5%/-1.5%; prior savings adjustment at renewal; rebasing '
                       'at each renewal lowers the benchmark by the ACO\'s own savings. Compare staying to term, renewing early and LEAD '
                       '(no rebasing for 10 years); shared savings per year via vbc.settle with rulebook terms; NPV; break-even savings rate.'}
NATIVE = ('value',)


def schema(obj, string, array):
    num = lambda lo, hi, d: {'type': 'number', 'minimum': lo, 'maximum': hi, 'description': d}
    return obj({'track': string(TRACKS), 'lead_variant': string(LEAD_VARIANTS),
                'agreement_start': {'type': 'integer', 'description': 'Start year of the current agreement (0 = the year before the as-of year)'},
                'agreement_years': {'type': 'integer', 'minimum': 3, 'maximum': 6},
                'decision_year': {'type': 'integer', 'description': 'First year an early renewal / LEAD entry takes effect (0 = as-of year + 1)'},
                'horizon_years': {'type': 'integer', 'minimum': 3, 'maximum': 15},
                'savings_pct': num(-0.2, 0.3, 'ACO spending below its counterfactual from the decision year'),
                'trend_pct': num(-0.1, 0.2, 'Annual expenditure trend (null = observed CAGR)'),
                'acpt_pct': num(-0.1, 0.2, 'Accountable Care Prospective Trend (null = the trend)'),
                'regional_weight': num(0, 0.5, 'Regional adjustment weight'), 'regional_pmpm': num(0, 1e5, 'Regional per capita PMPM (0 = own counterfactual)'),
                'national_pmpm': num(0, 1e5, 'National per capita PMPM (0 = regional)'), 'discount_rate': num(0, 0.3, 'NPV discount rate'),
                'participant_revenue': num(0, 1e10, 'ACO participant revenue (BASIC revenue-based loss cap)'),
                'scenarios': array(string(SCENARIOS), 'Scenarios; empty = all')})


def validate(p):
    allowed = set(CONTRACT['parameters'])
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('benchmark_rebasing parameters: ' + ', '.join(sorted(allowed)))
    out = {'track': p.get('track', 'enhanced'), 'lead_variant': p.get('lead_variant', 'Global'), 'agreement_start': p.get('agreement_start') or 0,
           'agreement_years': p.get('agreement_years', 5), 'decision_year': p.get('decision_year') or 0, 'horizon_years': p.get('horizon_years', 10),
           'savings_pct': float(p.get('savings_pct', 0.02)), 'trend_pct': p.get('trend_pct'), 'acpt_pct': p.get('acpt_pct'),
           'regional_weight': float(p.get('regional_weight', 0.35)), 'regional_pmpm': float(p.get('regional_pmpm') or 0),
           'national_pmpm': float(p.get('national_pmpm') or 0), 'discount_rate': float(p.get('discount_rate', 0.05)),
           'participant_revenue': float(p.get('participant_revenue') or 0), 'scenarios': [s for s in SCENARIOS if s in (p.get('scenarios') or SCENARIOS)]}
    if out['track'] not in TRACKS: raise ValueError(f'track: {TRACKS}')
    if out['lead_variant'] not in LEAD_VARIANTS: raise ValueError(f'lead_variant: {LEAD_VARIANTS}')
    for k in ('agreement_start', 'decision_year'):
        if type(out[k]) is not int or not (out[k] == 0 or 2000 <= out[k] <= 2100): raise ValueError(f'{k}: 0 or a year')
    if type(out['agreement_years']) is not int or not 3 <= out['agreement_years'] <= 6: raise ValueError('agreement_years: 3..6')
    if type(out['horizon_years']) is not int or not 3 <= out['horizon_years'] <= 15: raise ValueError('horizon_years: 3..15')
    for k in ('trend_pct', 'acpt_pct'):
        if out[k] is not None:
            if isinstance(out[k], bool) or not isinstance(out[k], (int, float)) or not -0.1 <= out[k] <= 0.2: raise ValueError(f'{k}: -0.1..0.2')
            out[k] = float(out[k])
    if not -0.2 <= out['savings_pct'] <= 0.3: raise ValueError('savings_pct: -0.2..0.3')
    if not 0 <= out['regional_weight'] <= 0.5 or not 0 <= out['discount_rate'] <= 0.3: raise ValueError('regional_weight 0..0.5; discount_rate 0..0.3')
    if set(p.get('scenarios') or []) - set(SCENARIOS): raise ValueError(f'scenarios: subset of {SCENARIOS}')
    return out


# ---- rulebook ------------------------------------------------------------------------------------------------------------
def rules(p):
    from . import programs
    res = programs.resolve('mssp_program_rules')
    val = lambda n, d=None: programs.value(res, n, d)
    if p['track'] == 'enhanced':
        terms = programs.contract_terms('mssp_enhanced')
    else:
        terms = programs.contract_terms(programs.resolve('mssp_basic', variant=p['track'][-1].upper()))
    lead = programs.contract_terms('lead', variant=p['lead_variant'])
    lead_res = programs.resolve('lead', variant=p['lead_variant'])
    cites = programs.sources(res, set(RULE_NAMES)) + terms['program']['sources'] + programs.sources(lead_res, {'benchmark_design'}) \
        + lead['program']['sources']
    seen, uniq = set(), []
    for c in cites:
        if c['id'] in seen: continue
        seen.add(c['id']); uniq.append(c)
    return {'acpt_weight': float(val('benchmark.acpt_weight', 0.3333)), 'pos_cap': float(val('benchmark.regional_adjustment_positive_cap', 0.05)),
            'neg_cap': float(val('benchmark.regional_adjustment_negative_cap', -0.015)), 'psa_share': 0.5, 'psa_cap': 0.05,
            'mssp_terms': terms, 'lead_terms': lead, 'lead_design': programs.value(lead_res, 'benchmark_design'), 'citations': uniq,
            'labels': {'mssp': terms['program']['label'], 'lead': lead['program']['label'], 'rules': res['label']}}


def settle_terms(t, members, p):
    from . import programs
    t = {k: v for k, v in t.items() if k != 'program'}
    if t.get('msr_schedule') and 'msr' not in t:
        rate = programs.variable_msr(members, t['msr_schedule'])
        t.update(msr=rate, **({'mlr': rate} if 'mlr' not in t else {}))
    t.pop('msr_schedule', None)
    if t.get('loss_cap_revenue_pct') is not None:
        if p['participant_revenue']: t['participant_revenue'] = p['participant_revenue']
        else: t.pop('loss_cap_revenue_pct')
    return t


# ---- observed expenditure -------------------------------------------------------------------------------------------------
def population(db, patient_ids):
    keep = set(patient_ids) if patient_ids is not None else None
    rows = [(pid, payer) for pid, payer, product in db.execute('SELECT patient_id, payer_id, product FROM member_month') if product in FFS]
    mssp = {pid for pid, payer in rows if payer == 'WH-PAYER-MSSP'}
    ids = mssp or {pid for pid, _ in rows}
    return (ids & keep if keep is not None else ids), ('MSSP-assigned (payer WH-PAYER-MSSP)' if mssp else 'Medicare FFS')


def observed(db, ids, as_of):
    from . import mlr
    y_end = int(as_of[:4])
    cells = mlr.cells(db, f'{y_end - 5}-01-01', as_of, ids, True, as_of)
    years = {}
    for (product, y), c in cells.items():
        if product not in FFS: continue
        a = years.setdefault(int(y), {'member_months': 0, 'paid': 0.0, 'members': 0})
        a['member_months'] += c['member_months']; a['paid'] += c['paid_completed']; a['members'] += c['members']
    return {y: {**v, 'pmpm': v['paid'] / v['member_months']} for y, v in sorted(years.items()) if v['member_months']}


def trend_of(obs, as_of):
    full = [y for y in obs if y < int(as_of[:4])]
    if len(full) >= 2 and obs[full[0]]['pmpm'] > 0:
        g = (obs[full[-1]]['pmpm'] / obs[full[0]]['pmpm']) ** (1 / (full[-1] - full[0])) - 1
        return max(-0.05, min(0.15, g)), full
    return 0.03, full


# ---- simulation -----------------------------------------------------------------------------------------------------------
class World:
    def __init__(self, obs, p, rule, as_of):
        self.p, self.rule = p, rule
        self.g = p['trend_pct'] if p['trend_pct'] is not None else trend_of(obs, as_of)[0]
        self.acpt = p['acpt_pct'] if p['acpt_pct'] is not None else self.g
        self.update = (1 - rule['acpt_weight']) * (1 + self.g) + rule['acpt_weight'] * (1 + self.acpt)
        years = sorted(obs)
        self.first, self.last = years[0], years[-1]
        self.obs = {y: obs[y]['pmpm'] for y in years}
        full = [y for y in years if y < int(as_of[:4])] or years
        self.anchor = full[-1]
        self.decision = p['decision_year'] or int(as_of[:4]) + 1
        self.members = max(1, round(obs[self.anchor]['member_months'] / 12))
        self.mm = self.members * 12

    def counterfactual(self, y):
        if y in self.obs and y <= self.anchor: return self.obs[y]
        if y < self.first: return self.obs[self.first] / (1 + self.g) ** (self.first - y)
        if y in self.obs and y < self.decision: return self.obs[y]  # partial current year: completed run rate
        return self.obs[self.anchor] * (1 + self.g) ** (y - self.anchor)

    def actual(self, y, s):
        return self.counterfactual(y) * (1 - s) if y >= self.decision else self.counterfactual(y)

    def regional(self, y):
        return self.p['regional_pmpm'] * (1 + self.g) ** (y - self.anchor) if self.p['regional_pmpm'] else self.counterfactual(y)

    def national(self, y):
        return self.p['national_pmpm'] * (1 + self.g) ** (y - self.anchor) if self.p['national_pmpm'] else self.regional(y)

    def historical(self, start, s, prior_savings=None):
        """Benchmark at agreement start (in BY3 dollars) with its components."""
        bys = [start - 3, start - 2, start - 1]
        hist = sum(w * self.actual(b, s) * (1 + self.g) ** (start - 1 - b) for w, b in zip(BY_WEIGHTS, bys))
        nat = self.national(start - 1)
        reg = self.p['regional_weight'] * (self.regional(start - 1) - hist)
        reg = max(self.rule['neg_cap'] * nat, min(self.rule['pos_cap'] * nat, reg))
        psa = 0.0
        if prior_savings:
            avg = sum(prior_savings) / len(prior_savings)
            psa = max(0.0, min(self.rule['psa_share'] * avg, self.rule['psa_cap'] * nat))
        adj = max(reg, psa) if reg >= 0 else reg + psa
        return {'start': start, 'benchmark_years': bys, 'historical': hist, 'regional_adjustment': reg, 'prior_savings_adjustment': psa,
                'adjusted': hist + adj}


def simulate(world, scenario, s):
    p, rule = world.p, world.rule
    from . import vbc
    years = list(range(world.decision, world.decision + p['horizon_years']))
    cur = p['agreement_start'] or world.decision - 2
    if scenario == 'stay':
        starts = [cur] + list(range(cur + p['agreement_years'], years[-1] + 1, p['agreement_years']))
    elif scenario == 'renew_early':
        starts = [cur] + list(range(world.decision, years[-1] + 1, p['agreement_years']))
    else:
        starts = [world.decision]
    terms = settle_terms(rule['lead_terms'] if scenario == 'lead' else rule['mssp_terms'], world.members, p)
    rows, agreements, savings_by_agreement = [], {}, {}
    for y in years:
        start = max(a for a in starts if a <= y)
        if start not in agreements:
            prior = None
            if scenario != 'lead' and start != starts[0]:
                prev = max(a for a in starts if a < start)
                prior = [max(0.0, b - a) for (yy, b, a) in savings_by_agreement.get(prev, [])] or None
            agreements[start] = world.historical(start, s, prior)
        h = agreements[start]
        bench = h['adjusted'] * world.update ** (y - (start - 1))
        actual = world.actual(y, s)
        net = vbc.settle(actual, bench, world.mm, terms)
        savings_by_agreement.setdefault(start, []).append((y, bench, actual))
        disc = (1 + p['discount_rate']) ** (y - world.decision)
        rows.append({'scenario': scenario, 'year': y, 'agreement_start': start, 'rebased_this_year': y == start,
                     'benchmark_pmpm': round(bench, 2), 'actual_pmpm': round(actual, 2), 'counterfactual_pmpm': round(world.counterfactual(y), 2),
                     'savings_pct_of_benchmark': round((bench - actual) / bench, 4), 'shared_savings': round(net, 2),
                     'pv_shared_savings': round(net / disc, 2), 'historical_benchmark': round(h['historical'], 2),
                     'regional_adjustment_pmpm': round(h['regional_adjustment'], 2), 'prior_savings_adjustment_pmpm': round(h['prior_savings_adjustment'], 2)})
    return rows


def npv(world, scenario, s):
    return sum(r['pv_shared_savings'] for r in simulate(world, scenario, s))


def break_even(world, a, b, lo=-0.05, hi=0.15):
    """Savings rate where NPV(a) = NPV(b) (first sign change on a grid, refined by bisection); None when no flip."""
    f = lambda s: npv(world, a, s) - npv(world, b, s)
    grid = [lo + (hi - lo) * i / 40 for i in range(41)]
    vals = [f(s) for s in grid]
    for (x0, v0), (x1, v1) in zip(zip(grid, vals), zip(grid[1:], vals[1:])):
        if v0 == 0: return round(x0, 4)
        if v0 * v1 < 0:
            for _ in range(40):
                m = (x0 + x1) / 2; vm = f(m)
                if vm * v0 <= 0: x1 = m
                else: x0, v0 = m, vm
            return round((x0 + x1) / 2, 4)
    return None


def run(db, patient_ids, p, as_of):
    ids, label = population(db, patient_ids)
    obs = observed(db, ids, as_of) if ids else {}
    rule = rules(p)
    if not obs:
        return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'status': 'partial', 'rows': [], 'scenarios': {},
                'assumptions': [f'No {label} member-months with claims in scope: nothing to project.'], 'citations': rule['citations'],
                'method': VERSION, 'population': label}
    world = World(obs, p, rule, as_of)
    rows, summary = [], {}
    for sc in p['scenarios']:
        r = simulate(world, sc, p['savings_pct'])
        rows += r
        summary[sc] = {'npv': round(sum(x['pv_shared_savings'] for x in r), 2), 'cumulative': round(sum(x['shared_savings'] for x in r), 2),
                       'first_year_benchmark': r[0]['benchmark_pmpm'], 'rebases': sum(x['rebased_this_year'] for x in r)}
    best = max(summary, key=lambda k: summary[k]['npv'])
    flips = {}
    if {'stay', 'renew_early'} <= set(summary): flips['renew_early_vs_stay'] = break_even(world, 'renew_early', 'stay')
    if {'lead', 'renew_early'} <= set(summary): flips['lead_vs_renew_early'] = break_even(world, 'lead', 'renew_early')
    ratchet = None
    if 'stay' in summary:
        nxt = p['agreement_start'] or world.decision - 2
        nxt += p['agreement_years']
        if nxt < world.decision + p['horizon_years']:
            with_s, without = world.historical(nxt, p['savings_pct']), world.historical(nxt, 0.0)
            ratchet = {'renewal_year': nxt, 'historical_with_savings': round(with_s['historical'], 2),
                       'historical_without_savings': round(without['historical'], 2),
                       'ratchet_pmpm': round(without['historical'] - with_s['historical'], 2)}
    obs_rows = [{'year': y, 'member_months': v['member_months'], 'pmpm': round(v['pmpm'], 2), 'partial_year': y >= int(as_of[:4])}
                for y, v in obs.items()]
    assumptions = [f'Synthetic warehouse; population = {label}, {world.members:,} members (last full year {world.anchor}); expenditure = '
                   'plan-paid claims PMPM, IBNR-completed; the current year is a completed run rate.',
                   f"Trend {world.g:.2%}/yr ({'supplied' if p['trend_pct'] is not None else 'observed CAGR of full years'}); ACPT {world.acpt:.2%} "
                   f"weighted {rule['acpt_weight']:.0%} in the update factor ({world.update - 1:.2%}/yr).",
                   f"ACO spending {p['savings_pct']:.1%} below its counterfactual from {world.decision} (supplied or default savings effort).",
                   f"Current agreement started {p['agreement_start'] or world.decision - 2} ({p['agreement_years']}-year agreements); early renewal and "
                   f'LEAD entry take effect {world.decision}.',
                   f"Regional per capita {'supplied' if p['regional_pmpm'] else 'defaults to the population counterfactual (no savings)'}, weight "
                   f"{p['regional_weight']:.0%}; national per capita {'supplied' if p['national_pmpm'] else '= regional'}.",
                   'Risk scores held constant (risk-score growth caps of MSSP and LEAD do not bind); assignment stable at the last full year; '
                   'quality standard met (full sharing rate, LEAD withhold earned back).',
                   'Benchmark-year expenditures before the observed data are back-cast at the trend.',
                   f"LEAD terms: {rule['labels']['lead']}; {rule['lead_design']}."]
    return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'status': 'answered', 'population': label, 'observed': obs_rows,
            'trend': round(world.g, 4), 'update_factor': round(world.update, 4), 'members': world.members, 'decision_year': world.decision,
            'rows': rows, 'scenarios': summary, 'best': best, 'break_even_savings_pct': flips, 'ratchet': ratchet,
            'citations': rule['citations'], 'assumptions': assumptions, 'labels': rule['labels'],
            'method': (f"{VERSION}: historical benchmark = BY 10/30/60 trended (42 CFR 425.652), ACPT-blended update (acpt_weight "
                       f"{rule['acpt_weight']}), regional adjustment capped {rule['pos_cap']:+.1%}/{rule['neg_cap']:+.1%} of national (425.656), "
                       'prior savings adjustment at renewal (425.658), rebasing at each renewal; LEAD without rebasing; vbc.settle with '
                       f"{rule['labels']['mssp']} / {rule['labels']['lead']} terms. Rule ids: " + ', '.join(c['id'] for c in rule['citations']) + '.')}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def headline(r):
    if not r.get('scenarios'): return 'Benchmark rebasing: no Medicare FFS expenditure in scope'
    names = {'stay': 'stay to term', 'renew_early': f"renew early ({r['decision_year']})", 'lead': 'enter LEAD'}
    parts = [f"{names[k]} NPV {rc.money(v['npv'])}" for k, v in r['scenarios'].items()]
    be = r['break_even_savings_pct'].get('renew_early_vs_stay')
    flip = f'; renewing early and staying break even at {be:.1%} savings' if be is not None else ''
    rat = f"; staying ratchets the {r['ratchet']['renewal_year']} benchmark down ${r['ratchet']['ratchet_pmpm']:,.0f} PMPM" if r.get('ratchet') else ''
    rec = {'stay': 'stay to the end of the current agreement', 'renew_early': 'renew early', 'lead': 'enter LEAD'}[r['best']]
    return (f"Over {r['parameters']['horizon_years']} years at {r['parameters']['savings_pct']:.1%} savings: " + '; '.join(parts) +
            f" -> {rec}{flip}{rat}")


def answer(report, plan, question):
    table = [{k: v for k, v in x.items()} for x in report['rows']]
    return rc.answer(report, plan, value=headline(report) + '.', table=table,
                     number=report['scenarios'][report['best']]['npv'] if report.get('scenarios') else None,
                     definitions=[{'term': 'benchmark rebasing', 'basis': 'new agreement period re-derives the historical benchmark from the '
                                                                        'three most recent years (42 CFR 425.652); savings lower it (ratchet)'},
                                  {'term': 'prior savings adjustment', 'basis': '42 CFR 425.658: lesser of 50% of average per capita savings or 5% of national'}],
                     sources=[{'id': c['id'], 'title': c['title'], 'url': c['url'], 'section': c['section']} for c in report.get('citations', [])],
                     overall_key='benchmark_rebasing')


# ---- free local-compiler route ------------------------------------------------------------------------------------------
REBASE_RX = (r'\brebas\w*\b|\bratchet\w*\b|\brenew\w* (?:our |the )?(?:mssp |aco |agreement |participation )*(?:agreement |contract )?early\b|'
             r'\bearly renewal\b|\brenew early\b|\bnew agreement period\b|\bbenchmark (?:reset|ratchet|erosion|decline|rebase)\b|'
             r'\bterminate (?:and|to) renew\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if not re.search(REBASE_RX, q): return None
    if not re.search(r'\bmssp\b|\baco\b|\bshared savings\b|\blead\b|\bbenchmark\b|\bagreement\b|\brenew', q): return None
    if rc.knowledge_only(q) and not re.search(r'\bshould\b', q): return None
    params = {}
    for t in ('a', 'b', 'c', 'd', 'e'):
        if re.search(rf'\b(?:basic )?level {t}\b', q): params['track'] = f'basic_{t}'
    if 'track' not in params and re.search(r'\bbasic\b', q): params['track'] = 'basic_e'
    if re.search(r'\bprofessional\b', q): params['lead_variant'] = 'Professional'
    s = re.search(r'(\d+(?:\.\d+)?) ?% (?:savings|below (?:the )?(?:benchmark|counterfactual)|cost reduction|reduction)|sav(?:e|ing|ings) (?:of )?(\d+(?:\.\d+)?) ?%', q)
    if s: params['savings_pct'] = float(s.group(1) or s.group(2)) / 100
    t = re.search(r'(\d+(?:\.\d+)?) ?% (?:cost |annual |medical )?trend|trend (?:of |is |at )?(\d+(?:\.\d+)?) ?%', q)
    if t: params['trend_pct'] = float(t.group(1) or t.group(2)) / 100
    h = re.search(r'\b(?:over|next) (\d+) years\b', q)
    if h and 3 <= int(h.group(1)) <= 15: params['horizon_years'] = int(h.group(1))
    a = re.search(r'\bagreement (?:started|start(?:ing|s)?|began|beginning) (?:in )?(20\d\d)\b|\bsince (20\d\d)\b', q)
    if a: params['agreement_start'] = int(a.group(1) or a.group(2))
    return plan(OP, params, 'MSSP / LEAD benchmark rebasing and ratchet scenarios (stay vs renew early vs LEAD)', question,
                [f"track {params.get('track', 'enhanced')}"])


TEMPLATES = ['Should we renew our MSSP agreement early?', 'What does the benchmark ratchet cost us if we stay to the end of the agreement?',
             'Compare early renewal vs staying vs moving to LEAD with no rebasing', 'How much will rebasing lower our ACO benchmark?',
             'Should our ACO renew early if we keep saving 3%?', 'Model MSSP rebasing over the next 10 years at a 4% trend',
             'Is early renewal better for us under BASIC level E?', 'What savings rate makes early renewal break even for our ACO?']
