"""Evolutionary outreach-policy search against the population simulator (clinical_core/agere/evolve as a planner engine).

Setting (evolve.evaluator.Task): a program kind (default pharmacy_gdmt), the deterministic router's plan at the snapshot
as_of, a capacity of C patients per quarter filled into quarterly waves, a 12-month horizon. Each candidate policy is a
small sandboxed program (evolve/sandbox.py: math/statistics only, separate process) that ranks routed patients and may pick
a program variant. The simulator (evolve.evaluator.Dataset: engine.simulate per seed and wave, common random numbers)
scores it: net savings (gross medical savings - program cost), admissions avoided, gap closures, plus an equity floor.

Search, model-free and deterministic (no LLM is called): GenomeProposer encodes a policy as weights over standardized
de-identified features (risk, cost, missing pillars, barriers, router priority/rank, recent HF admission, reach modifiers),
an optional contact threshold and a program-variant rule, and writes it as a sandboxed program. Generation 0 is the
evolve/baselines set (router order, highest risk, outreach.py knapsack value, random); each generation mutates (Gaussian
weight noise, threshold shifts, variant-rule flips) and crosses over the archive's best elites and an elite from another
MAP-Elites niche (evolve.loop.evolve). The champion is chosen on training patients and seeds only, then reported on
held-out patients and held-out seeds with paired intervals against every baseline (evolve.loop.heldout_report).

EVERY OUTCOME IS SIMULATED by the population simulator under its stated (placeholder) parameters and program costs; nothing
is an observed effect. The simulator Dataset is cached per (semantic digest, task, seeds) so repeat questions are fast.
"""
import hashlib
import json
import math
import random
import re
from pathlib import Path

VERSION = 'policy-search/1'
OP = 'policy_search'
OBJECTIVES = ('net_savings', 'admissions_avoided', 'gap_closures')
DEFAULTS = {'program_kind': 'pharmacy_gdmt', 'objective': 'net_savings', 'capacity_per_quarter': 40, 'hours_per_quarter': None,
            'generations': 4, 'population': 4, 'iterations': 100, 'train_seeds': 1, 'heldout_seeds': 1, 'equity_floor': 0.5,
            'horizon_months': 12, 'seed': 7}
GENES = ('monthly_admission_rate', 'expected_monthly_cost', 'missing_count', 'barrier_count', 'task_priority', 'router_rank',
         'hf_admission_6m', 'hf_admissions_12m', 'ed_visits_12m', 'interpreter_needed', 'medicaid', 'age_80_plus')
BOOL = {'hf_admission_6m', 'interpreter_needed', 'medicaid', 'age_80_plus'}
CACHE_DIR = 'policy-search-cache'

PRODUCT = {'population': 'optional', 'view': 'policy',
           'use': 'OPTIMISE an outreach / scheduling-wave POLICY by evolutionary search against the population simulator: which '
                  'prioritisation rule maximises simulated net savings, admissions avoided or GDMT gap closures under a quarterly '
                  'capacity and an equity floor, vs router order, highest risk, knapsack value and random (outcomes simulated).'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'program_kind': 'pharmacy_gdmt', 'objective': 'net_savings|admissions_avoided|gap_closures|all',
                           'capacity_per_quarter': 40, 'hours_per_quarter': None, 'generations': 4, 'population': 4, 'iterations': 100,
                           'train_seeds': 1, 'heldout_seeds': 1, 'equity_floor': 0.5, 'horizon_months': 12, 'seed': 7},
            'meaning': 'Evolutionary (MAP-Elites, FunSearch-style) search over sandboxed outreach prioritisation programs scored by the '
                       'population simulator: capacity-limited quarterly waves, net savings / admissions avoided / gap closures, equity '
                       'floor by line of business and age band. Deterministic model-free mutation and crossover of feature-weight '
                       'policies seeded with the router, highest-risk, outreach-knapsack and random baselines; champion chosen on training '
                       'patients and seeds, reported on held-out patients and seeds with paired intervals vs each baseline. All outcomes '
                       'are simulated under placeholder parameters.'}
NATIVE = ('value',)


def schema(obj, string, array):
    i = lambda lo, hi, d: {'type': 'integer', 'minimum': lo, 'maximum': hi, 'description': d}  # noqa: E731
    return obj({'program_kind': string(['pharmacy_gdmt', 'gdmt_titration', 'hf_remote_monitoring', 'virtual_pt'], 'program family to staff'),
                'objective': string([*OBJECTIVES, 'all'], 'what the champion maximises (default net_savings)'),
                'capacity_per_quarter': i(1, 5000, 'patients contacted per quarter'),
                'hours_per_quarter': {'type': 'number', 'minimum': 1, 'maximum': 100000, 'description': 'pharmacist hours per quarter (optional)'},
                'generations': i(0, 12, 'evolution generations (default 4)'), 'population': i(1, 12, 'candidates per generation and objective'),
                'iterations': i(50, 2000, 'simulator Monte Carlo iterations (default 100)'),
                'train_seeds': i(1, 4, 'simulator seeds for training'), 'heldout_seeds': i(1, 4, 'simulator seeds held out'),
                'equity_floor': {'type': 'number', 'minimum': 0, 'maximum': 1, 'description': 'subgroup contact share >= floor x overall'},
                'horizon_months': i(6, 24, 'simulated horizon'), 'seed': i(0, 10 ** 6, 'search seed')})


def validate(p):
    if p is None: p = {}
    if not isinstance(p, dict) or set(p) - set(DEFAULTS): raise ValueError('policy_search parameters: ' + ', '.join(sorted(DEFAULTS)))
    out = {**DEFAULTS, **{k: v for k, v in p.items() if v is not None}}
    if not isinstance(out['program_kind'], str) or not re.fullmatch(r'[a-z_]{2,40}', out['program_kind']): raise ValueError('program_kind: a program kind')
    if out['objective'] not in (*OBJECTIVES, 'all'): raise ValueError(f'objective: {OBJECTIVES} or all')
    for k, lo, hi in (('capacity_per_quarter', 1, 5000), ('generations', 0, 12), ('population', 1, 12), ('iterations', 50, 2000),
                      ('train_seeds', 1, 4), ('heldout_seeds', 1, 4), ('horizon_months', 6, 24), ('seed', 0, 10 ** 6)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k} {lo}..{hi}')
    h = out['hours_per_quarter']
    if h is not None and (type(h) not in (int, float) or not 1 <= h <= 1e5): raise ValueError('hours_per_quarter 1..100000')
    if type(out['equity_floor']) not in (int, float) or not 0 <= out['equity_floor'] <= 1: raise ValueError('equity_floor 0..1')
    return out


# ---- genome <-> sandboxed policy code ------------------------------------------------------------------------
def feature_scales(rows):
    """{gene: (mean, sd)} over the routed feature rows (constants written into each policy)."""
    out = {}
    for g in GENES:
        vals = [float(r[g]) for r in rows if r.get(g) is not None]
        m = sum(vals) / len(vals) if vals else 0.0
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else 0.0
        out[g] = (round(m, 6), round(sd or 1.0, 6))
    return out


def code_for(genome, scales, alt_variant=None):
    """Genome {'w': {gene: weight}, 'threshold': float|None, 'barrier_alt': bool} -> sandbox policy source."""
    lines = ['# evolved by policy_search (weights over standardized features)', 'import math', '', 'def prioritize(p):', '    s = 0.0']
    for g in GENES:
        w = round(genome['w'].get(g, 0.0), 4)
        if not w: continue
        m, sd = scales[g]
        lines.append(f"    v = p.get('{g}')")
        lines.append(f"    s += {w} * ((float(v) if v is not None else {m}) - {m}) / {sd}")
    if genome.get('threshold') is not None:
        lines.append(f"    if s < {round(genome['threshold'], 4)}:")
        lines.append('        return None')
    lines.append('    return s')
    if genome.get('barrier_alt') and alt_variant:
        lines += ['', 'def choose_program(p, programs):', '    ids = [x.get(\'id\') for x in programs]',
                  f"    if p.get('barrier_count', 0) > 0 and '{alt_variant}' in ids:", f"        return '{alt_variant}'",
                  "    return p.get('routed_program')"]
    return '\n'.join(lines) + '\n'


class GenomeProposer:
    """Model-free evolutionary proposer for evolve.loop.evolve: mutation + crossover of feature-weight genomes."""

    def __init__(self, scales, *, per_call=4, seed=7, alt_variant=None):
        self.scales, self.per_call, self.seed, self.alt = scales, per_call, seed, alt_variant
        self.genomes, self.calls = {}, 0
        # generation-0 genomes approximating the hand rules (risk, gaps, router), so mutation has parents with genes
        self.seeds = [{'w': {'monthly_admission_rate': 1.0}, 'threshold': None, 'barrier_alt': False},
                      {'w': {'task_priority': 1.0, 'router_rank': -0.5}, 'threshold': None, 'barrier_alt': False},
                      {'w': {'monthly_admission_rate': 0.6, 'missing_count': 0.6, 'hf_admission_6m': 0.4}, 'threshold': None, 'barrier_alt': True},
                      {'w': {'monthly_admission_rate': 1.0, 'expected_monthly_cost': 0.5}, 'threshold': 0.0, 'barrier_alt': False}]
        # baselines are hand-written programs: map each to its nearest genome so elites that are baselines still have offspring
        self.baseline_genomes = {'router_order': self.seeds[1], 'highest_risk_first': self.seeds[0],
                                 'outreach_knapsack': {'w': {'monthly_admission_rate': 0.8, 'monthly_ed_rate_proxy': 0.0, 'missing_count': 0.5},
                                                       'threshold': None, 'barrier_alt': False},
                                 'random': {'w': {'router_rank': 0.1}, 'threshold': None, 'barrier_alt': False}}
        for g in self.baseline_genomes.values(): g['w'].pop('monthly_ed_rate_proxy', None)

    def register_baselines(self, codes):
        for name, code in codes.items():
            if name in self.baseline_genomes: self.genomes[code] = self.baseline_genomes[name]

    def _code(self, g):
        c = code_for(g, self.scales, self.alt)
        self.genomes[c] = g
        return c

    def _mutate(self, g, rng, scale):
        w = dict(g['w'])
        for gene in GENES:
            if gene in w or rng.random() < 0.25:
                w[gene] = round(w.get(gene, 0.0) + rng.gauss(0, scale), 4)
                if abs(w[gene]) < 0.02: w.pop(gene)
        thr = g.get('threshold')
        if rng.random() < 0.3: thr = None if (thr is not None and rng.random() < 0.5) else round((thr or -1.5) + rng.gauss(0, 0.5), 3)
        alt = (not g.get('barrier_alt')) if rng.random() < 0.15 else g.get('barrier_alt', False)
        return {'w': w or {'monthly_admission_rate': 1.0}, 'threshold': thr, 'barrier_alt': alt}

    def _cross(self, a, b, rng):
        w = {k: (a['w'].get(k, 0.0) if rng.random() < 0.5 else b['w'].get(k, 0.0)) for k in set(a['w']) | set(b['w'])}
        return {'w': {k: v for k, v in w.items() if v} or dict(a['w']), 'threshold': rng.choice([a.get('threshold'), b.get('threshold')]),
                'barrier_alt': rng.choice([a.get('barrier_alt', False), b.get('barrier_alt', False)])}

    def propose(self, ctx):
        self.calls += 1
        rng = random.Random(f"{self.seed}|{ctx['generation']}|{ctx['objective']}")
        parents = [self.genomes.get(p['code']) for p in ctx['parents']] + [self.genomes.get(e['code']) for e in ctx.get('inspiration', [])]
        known = [g for g in parents if g]
        out = []
        if ctx['generation'] == 1:  # first generation: the rule-shaped seed genomes join the baselines
            out += [self._code(g) for g in self.seeds]
        pool = known or self.seeds
        scale = 0.6 / math.sqrt(ctx['generation'])  # anneal mutation size
        while len(out) < self.per_call + (len(self.seeds) if ctx['generation'] == 1 else 0):
            if len(pool) > 1 and rng.random() < 0.35:
                a, b = rng.sample(pool, 2)
                g = self._mutate(self._cross(a, b, rng), rng, scale / 2)
            else:
                g = self._mutate(rng.choice(pool), rng, scale)
            out.append(self._code(g))
        return [{'code': c, 'origin': 'genome'} for c in dict.fromkeys(out)]

    def explain(self, code, profile):
        g = self.genomes.get(code)
        if not g: return None
        top = sorted(g['w'].items(), key=lambda kv: -abs(kv[1]))[:5]
        return ('Ranks patients by ' + ', '.join(f"{'+' if w > 0 else '-'}{abs(w):.2f} x standardized {k}" for k, w in top)
                + (f"; skips patients scoring below {g['threshold']:.2f}" if g.get('threshold') is not None else '')
                + ('; routes patients with a documented barrier to the barrier-review variant' if g.get('barrier_alt') else '') + '.')

    def spend(self):
        return {'calls': 0, 'estimated_usd': 0.0}


# ---- simulator dataset (cached) -----------------------------------------------------------------------------
def _semantic(path):
    from . import semantic_layer
    path = Path(path)
    if path.is_file(): return path, path.parent
    return (semantic_layer.ensure(path) if (path / 'run.json').exists() else path / 'semantic.sqlite3'), path


def _source_digest(semantic):
    import sqlite3
    try:
        db = sqlite3.connect(f'file:{semantic}?mode=ro', uri=True)
        try: meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
        finally: db.close()
    except sqlite3.Error:
        meta = {}
    return str(meta.get('source_digest') or hashlib.sha256(str(semantic).encode()).hexdigest())


def dataset(semantic, cache_root, task, seeds, patient_ids=None, log=None):
    import pickle
    from ..evolve import evaluator as ev
    from ..plans import catalog as catmod, router
    key = hashlib.sha256(json.dumps([VERSION, _source_digest(semantic), task.to_dict(), list(seeds), sorted(patient_ids) if patient_ids is not None else None],
                                    sort_keys=True, default=str).encode()).hexdigest()[:24]
    cache = Path(cache_root) / CACHE_DIR / f'ds-{key}.pkl' if cache_root else None
    if cache and cache.is_file():
        try:
            return pickle.loads(cache.read_bytes()), True
        except Exception:  # noqa: BLE001 - a stale or partial cache is rebuilt
            pass
    cat = catmod.load()
    ids = router.candidates(str(semantic), task.program_kind, task.as_of, catalog=cat)
    if patient_ids is not None:
        keep = set(patient_ids); ids = [i for i in ids if i in keep]
    if not ids: raise ValueError(f'no routed candidates for {task.program_kind} in the selected population')
    plan = router.route(str(semantic), ids, task.program_kind, task.as_of, catalog=cat)
    ds = ev.Dataset.build(str(semantic), task, seeds, catalog=cat, plan=plan, log=log)
    if cache:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(pickle.dumps(ds))
        except OSError:
            pass
    return ds, False


def search(ds, p, *, log=None):
    """Evolution + held-out report on a built Dataset -> report dict."""
    from ..evolve import baselines as basemod, evaluator as ev, loop
    log = log or (lambda m: None)
    tr = [s for s in ds.seeds][:p['train_seeds']]
    ho = [s for s in ds.seeds][p['train_seeds']:p['train_seeds'] + p['heldout_seeds']] or tr
    objectives = list(OBJECTIVES) if p['objective'] == 'all' else [p['objective']]
    scales = feature_scales(ds.rows)
    alt = next((v for v in ds.variants if 'barrier' in v), ds.variants[-1] if len(ds.variants) > 1 else None)
    prop = GenomeProposer(scales, per_call=p['population'], seed=p['seed'], alt_variant=alt)
    E = ev.Evaluator(ds)
    bases = basemod.all_baselines()
    prop.register_baselines(bases)
    arch = loop.evolve(ds, prop, train_seeds=tr, generations=p['generations'], objectives=objectives, baselines=bases, seed=p['seed'],
                       log=log, evaluator=E)
    rep = loop.heldout_report(ds, arch, bases, tr, ho, objectives=objectives, evaluator=E, proposer=prop, log=log)
    champ = rep['champions'][objectives[0]]
    full = E.evaluate(champ['code'], 'all', seeds=ho)
    worklist = sorted(({'patient_id': ds.pids[i], 'program': v, 'wave': q + 1} for i, v, q in full['_assignment']),
                      key=lambda r: (r['wave'], r['patient_id']))
    tried = [e for e in arch.entries if e.get('status') not in ('duplicate',)]
    return {'objectives': objectives, 'report': rep, 'worklist': worklist, 'train_seeds': tr, 'heldout_seeds': ho,
            'candidates_evaluated': len(tried), 'invalid': sum(1 for e in tried if e.get('status') == 'invalid'),
            'archive': arch.summary(), 'variants': ds.variants, 'patients': {k: int(len(v)) for k, v in ds.splits.items()},
            'capacity_train': ev.capacity_for(ds.task, len(ds.splits['train']), len(ds.pids)),
            'capacity_heldout': ev.capacity_for(ds.task, len(ds.splits['heldout']), len(ds.pids))}


def execute(path, patient_ids, params, as_of):
    import time
    from ..evolve import evaluator as ev
    p = validate(params)
    t0 = time.time()
    semantic, root = _semantic(path)
    task = ev.Task(program_kind=p['program_kind'], as_of=str(as_of)[:10], horizon_months=p['horizon_months'],
                   capacity_per_quarter=p['capacity_per_quarter'], hours_per_quarter=p['hours_per_quarter'],
                   iterations=p['iterations'], funnel_scenarios=10, equity_floor=float(p['equity_floor']))
    seeds = [101 + i for i in range(p['train_seeds'])] + [201 + i for i in range(p['heldout_seeds'])]
    ds, cached = dataset(semantic, root, task, seeds, patient_ids)
    t_sim = time.time() - t0
    res = search(ds, p)
    rep, o = res['report'], res['objectives'][0]
    champ = rep['champions'][o]
    pol = rep['policies']
    table = []
    for name, row in pol.items():
        h = row['heldout']['intervals']
        table.append({'policy': name, 'heldout_net_savings_usd': h['net_savings']['mean'], 'net_savings_p05': h['net_savings']['p05'],
                      'net_savings_p95': h['net_savings']['p95'], 'admissions_avoided': h['admissions_avoided']['mean'],
                      'gap_closures': h['gap_closures']['mean'], 'contacted': row['heldout']['contacted'],
                      'equity_passed': row['heldout']['equity']['passed'], 'train_net_savings_usd': row['train']['metrics']['net_savings']})
    table.sort(key=lambda r: -(r[{'net_savings': 'heldout_net_savings_usd'}.get(o, o)] or 0))
    vs = {b: d['heldout'][o] for b, d in (pol[f'best:{o}'].get('vs_baselines') or {}).items()}
    return {'schema': VERSION, 'as_of': str(as_of)[:10], 'parameters': p, 'objective': o, 'objectives': res['objectives'],
            'champion': {'id': champ['id'], 'origin': champ['origin'], 'generation': champ['generation'], 'code': champ['code'],
                         'rule_summary': champ['rule_summary'], 'explanation': champ.get('explanation')},
            'vs_baselines': vs, 'table': table, 'policies': pol, 'worklist': res['worklist'],
            'patient_ids': [r['patient_id'] for r in res['worklist']], 'search': {k: res[k] for k in (
                'candidates_evaluated', 'invalid', 'archive', 'train_seeds', 'heldout_seeds', 'patients', 'capacity_train', 'capacity_heldout', 'variants')},
            'routed_patients': len(ds.pids), 'dataset_cached': cached,
            'seconds': {'simulation': round(t_sim, 1), 'total': round(time.time() - t0, 1)}, 'simulated': True,
            'assumptions': ASSUMPTIONS + [f"Capacity {p['capacity_per_quarter']} patients per quarter over {task.n_waves} quarterly waves; "
                                          f"{p['iterations']} Monte Carlo iterations x {len(seeds)} seeds; equity floor {p['equity_floor']:g}."],
            'method': ('evolve.loop.evolve (MAP-Elites archive; model-free GenomeProposer mutation/crossover; sandboxed policies) on '
                       'evolve.evaluator.Dataset simulator columns; champion by training fitness; evolve.loop.heldout_report paired '
                       'held-out intervals vs router order, highest risk, outreach knapsack and random.')}


ASSUMPTIONS = ['ALL OUTCOMES ARE SIMULATED by the Agere population simulator (simulator/effects config: placeholder program costs, '
               'effect sizes and funnel rates), not observed results; synthetic data only.',
               'Policies see de-identified features only; the champion is chosen on training patients and seeds and reported on held-out ones.',
               'Scheduling here means assigning patients to quarterly outreach waves under capacity; clinic slot overbooking is the scheduling engine.',
               'An approved policy reaches the router only through config/agere/evolve_policy.json after human review (default off).']


def headline(r):
    o, c = r['objective'], r['champion']
    best = next(t for t in r['table'] if t['policy'] == f'best:{o}')
    key = {'net_savings': 'heldout_net_savings_usd'}.get(o, o)
    fmt = (lambda v: f'${v:,.0f}') if o == 'net_savings' else (lambda v: f'{v:.2f}')
    diffs = '; '.join(f"vs {b}: {d['mean']:+,.2f} [{d['p05']:+,.2f}, {d['p95']:+,.2f}]" for b, d in r['vs_baselines'].items())
    return (f"SIMULATED: evolutionary search ({r['search']['candidates_evaluated']} sandboxed policies, {r['parameters']['generations']} generations) "
            f"for {r['parameters']['program_kind']} over {r['routed_patients']} routed patients at {r['parameters']['capacity_per_quarter']}/quarter. "
            f"Champion {c['id']} ({c['origin']}) held-out {o.replace('_', ' ')} {fmt(best[key])} "
            f"(net savings {best['heldout_net_savings_usd']:,.0f} [{best['net_savings_p05']:,.0f}, {best['net_savings_p95']:,.0f}], "
            f"{best['admissions_avoided']:.2f} admissions avoided, {best['gap_closures']:.1f} gap closures, equity {'passed' if best['equity_passed'] else 'FAILED'}); "
            f"paired held-out differences {diffs}. Rule: {c.get('explanation') or c['rule_summary']}")


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['table'],
            'count': len(report['patient_ids']), 'patient_ids': report['patient_ids'], 'value': headline(report),
            'method': report['method'], 'limitations': report['assumptions'], 'membership_role': 'selected',
            'definitions': [{'term': 'simulated outcome', 'basis': ASSUMPTIONS[0]}],
            'overall': {'policy_search': {'champion': report['champion'], 'vs_baselines': report['vs_baselines'],
                                          'worklist': report['worklist'][:200], 'search': report['search'], 'simulated': True}}}


# ---- free local-compiler route ----------------------------------------------------------------------------
SEARCH = (r'\bwhich (?:outreach |targeting |prioriti[sz]ation )?(?:polic|strateg|rule|heuristic)\w*\b[^.?]*\bsimulat\w*|\bevolv\w*\b|\bevolutionary\b|\bgenetic algorithm\b|\bpolicy search\b|\bheuristic search\b|\bfunsearch\b|\balphaevolve\b|'
          r'\b(?:optimi[sz]\w*|search for|discover|find|learn|best|better)\b[^.?]{0,40}\b(?:outreach |prioriti[sz]ation |targeting |scheduling |wave )?'
          r'(?:polic(?:y|ies)|rules?|heuristics?|strateg(?:y|ies))\b')
OUTREACH = r'\boutreach\b|\bprioriti[sz]\w*\b|\btarget\w*\b|\bwhom? to (?:call|contact|enrol\w*)\b|\bgdmt\b|\bpharmac\w*\b|\bwaves?\b|\bschedul\w*\b|\benrol\w*\b|\bcapacity\b|\bcontact\w*\b'
SIM = r'\bsimulat\w*\b|\bsimulator\b|\bpolic(?:y|ies)\b|\bheuristics?\b|\brules?\b'


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not (re.search(SEARCH, low) and re.search(OUTREACH, low) and re.search(SIM, low)): return None
    if re.match(r'(?:what (?:is|are)|explain|define|how (?:does|do))\b', low): return None
    if re.search(r'\bno[- ]?shows?\b|\bover[- ]?book', low): return None
    params = {}
    if re.search(r'\badmissions?\b|\bhospitali[sz]ations?\b', low) and not re.search(r'\bsavings?\b|\bdollars?\b|\broi\b', low): params['objective'] = 'admissions_avoided'
    elif re.search(r'\bgaps?\b|\bclosures?\b|\bpillars?\b|\bgdmt starts?\b', low) and not re.search(r'\bsavings?\b', low): params['objective'] = 'gap_closures'
    elif re.search(r'\bevery objective\b|\ball (?:three )?objectives\b', low): params['objective'] = 'all'
    cap = re.search(r'\b(\d{1,4})\s+(?:patients?|members?|slots?|people|contacts?)\s+(?:per|a|each)\s+quarter\b', low)
    if cap and 1 <= int(cap.group(1)) <= 5000: params['capacity_per_quarter'] = int(cap.group(1))
    gen = re.search(r'\b(\d{1,2})\s+generations?\b', low)
    if gen and 0 <= int(gen.group(1)) <= 12: params['generations'] = int(gen.group(1))
    if re.search(r'\bremote monitoring\b', low): params['program_kind'] = 'hf_remote_monitoring'
    return plan(OP, params, 'Evolutionary outreach-policy search scored by the population simulator (outcomes simulated)', question,
                ['objective ' + params.get('objective', 'net_savings')])


TEMPLATES = ['Evolve a better targeting rule for our GDMT pharmacist program using the simulator',
             'search for an outreach policy that beats highest-risk-first in simulation',
             'Use evolutionary search to find the best prioritization heuristic for {n} patients per quarter',
             'which outreach rule maximizes simulated admissions avoided',
             'optimize the order we enroll heart failure patients into pharmacist outreach waves, simulated',
             'can a learned policy close more GDMT gaps than router order in the simulator',
             'run a heuristic search over outreach strategies against the population simulator',
             'find a scheduling policy for the outreach waves that improves simulated net savings']
