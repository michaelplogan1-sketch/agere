"""Outreach learning: a Thompson-sampling bandit over outreach arms learned from the warehouse's logged outreach outcomes.

An arm is (channel, segment): channel = mail / phone / text (as logged), segment = program, risk tier (eligibility risk score
< 2 low, 2-3 medium, >= 3 high), attempt (first vs repeat) or none. Every outreach_attempts record is one pull; responded =
outcome 'engaged' (the patient engaged with the program). Declined, no answer, voicemail, wrong number and unable to reach are
non-responses (declined = reached but not engaged; reported separately as reach). A Beta(1, 1)-Bernoulli posterior per arm
(outreach_bandit.ThompsonOutreach) gives posterior mean, 95% credible interval and P(best); the next batch is allocated by
Thompson sampling. An optional continuation simulates further rounds at the posterior-mean rates (labelled 'simulated outcomes')
and reports cumulative regret against the best arm. The outcome log is synthetic warehouse data; no live outreach was observed.

response_rates(db, segment_by) feeds outreach_allocation (channel response probability per arm).
"""
from collections import Counter, defaultdict
import json
import re
import sqlite3
from pathlib import Path

VERSION = 'outreach-learning/1'
OP = 'outreach_bandit'
RESPONDED = ('engaged',)
REACHED = ('engaged', 'declined')
SEGMENTS = ('none', 'program', 'risk_tier', 'attempt')
PRIOR = (1.0, 1.0)

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'WHICH OUTREACH CHANNEL WORKS: learn from logged outreach outcomes which channel (phone, text, mail) gets patients to '
                  'engage, by program, risk tier or attempt; Thompson-sampling bandit posteriors, P(best), how to split the next batch '
                  '(explore vs exploit), simulated regret.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'segment_by': 'none|program|risk_tier|attempt', 'next_batch': 100, 'simulate_rounds': 0, 'simulate_batch': 25, 'seed': 0},
            'meaning': 'Beta-Bernoulli Thompson-sampling bandit over (channel x segment) arms from the outreach_attempts log (responded = '
                       'engaged): per arm attempts, engagements, reach, posterior mean with 95% credible interval, P(best arm); Thompson '
                       'allocation of the next batch of contacts; optional simulated continuation at posterior-mean rates with cumulative '
                       'regret (simulated outcomes, labelled). Outcome log is synthetic.'}
NATIVE = ('value',)


def schema(obj, string, array):
    return obj({'segment_by': string(SEGMENTS, 'Arm segment besides channel'),
                'next_batch': {'type': 'integer', 'minimum': 1, 'maximum': 10000, 'description': 'Contacts to allocate next by Thompson sampling'},
                'simulate_rounds': {'type': 'integer', 'minimum': 0, 'maximum': 200, 'description': 'Simulated continuation rounds (0 = none)'},
                'simulate_batch': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
                'seed': {'type': 'integer', 'minimum': 0, 'maximum': 1000000}})


def validate(p):
    allowed = {'segment_by', 'next_batch', 'simulate_rounds', 'simulate_batch', 'seed'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('outreach_bandit parameters: ' + ', '.join(sorted(allowed)))
    out = {'segment_by': p.get('segment_by', 'none'), 'next_batch': p.get('next_batch', 100), 'simulate_rounds': p.get('simulate_rounds', 0),
           'simulate_batch': p.get('simulate_batch', 25), 'seed': p.get('seed', 0)}
    if out['segment_by'] not in SEGMENTS: raise ValueError(f'segment_by: {SEGMENTS}')
    for k, lo, hi in (('next_batch', 1, 10000), ('simulate_rounds', 0, 200), ('simulate_batch', 1, 1000), ('seed', 0, 1000000)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k} {lo}..{hi}')
    return out


def risk_tier(score):
    try: s = float(score)
    except (TypeError, ValueError): return 'unknown'
    return 'low' if s < 2 else 'medium' if s < 3 else 'high'


def segment_of(r, segment_by):
    if segment_by == 'program': return str(r.get('program') or 'unknown')
    if segment_by == 'risk_tier': return risk_tier(r.get('eligibility_risk_score'))
    if segment_by == 'attempt': return 'first' if r.get('attempt_number') in (1, '1', None) else 'repeat'
    return 'all'


def attempts(db, segment_by='none', keep=None, as_of=None):
    """Logged outreach pulls: [{'arm': (channel, segment), 'responded', 'reached', 'outcome', 'patient_id', 'date', 'record_id'}]."""
    out = []
    for rid, pid, day, payload in db.execute("SELECT record_id, patient_id, substr(event_date,1,10), payload FROM record "
                                             "WHERE stream = 'outreach_attempts' ORDER BY event_date, record_id"):
        if (keep is not None and pid not in keep) or (as_of and day and day > as_of): continue
        r = json.loads(payload or '{}')
        outcome = str(r.get('outcome') or 'unknown')
        out.append({'arm': (str(r.get('channel') or 'unknown'), segment_of(r, segment_by)), 'responded': outcome in RESPONDED,
                    'reached': outcome in REACHED, 'outcome': outcome, 'patient_id': pid, 'date': day, 'record_id': rid})
    return out


def response_rates(db, segment_by='none', keep=None, as_of=None):
    """{(channel, segment): posterior mean engagement rate} for outreach_allocation (empty when nothing is logged)."""
    log = attempts(db, segment_by, keep, as_of)
    if not log: return {}
    from .outreach_bandit import ThompsonOutreach
    b = ThompsonOutreach.from_log(log, PRIOR)
    return {a: b.alpha[a] / (b.alpha[a] + b.beta[a]) for a in b.arms}


def run(db, patient_ids, p, as_of):
    import numpy as np
    from .outreach_bandit import ThompsonOutreach
    keep = set(patient_ids) if patient_ids is not None else None
    log = attempts(db, p['segment_by'], keep, as_of)
    base = {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'outcome_source': 'synthetic warehouse outreach_attempts log'}
    if not log:
        return {**base, 'status': 'no_outcomes', 'arms': [], 'attempts': 0, 'patient_ids': [], 'allocation': {}, 'simulation': None,
                'method': METHOD}
    bandit = ThompsonOutreach.from_log(log, PRIOR)
    summary = {tuple(s['arm']): s for s in bandit.summary(seed=p['seed'])}
    reach = defaultdict(lambda: [0, 0]); outcomes = defaultdict(Counter)
    for x in log:
        reach[x['arm']][0] += x['reached']; reach[x['arm']][1] += 1; outcomes[x['arm']][x['outcome']] += 1
    rng = np.random.default_rng(p['seed'])
    alloc = bandit.allocate(p['next_batch'], rng)
    arms = []
    for a in bandit.arms:
        s = summary[a]
        arms.append({'channel': a[0], 'segment': a[1], 'attempts': s['attempts'], 'engaged': s['responses'],
                     'observed_rate': round(s['responses'] / s['attempts'], 4) if s['attempts'] else None,
                     'reach_rate': round(reach[a][0] / reach[a][1], 4) if reach[a][1] else None,
                     'posterior_mean': s['posterior_mean'], 'ci95': s['ci95'], 'p_best': s['p_best'],
                     'next_batch_contacts': int(alloc.get(a, 0)), 'outcomes': dict(outcomes[a])})
    arms.sort(key=lambda r: (-r['posterior_mean'], r['channel'], r['segment']))
    simulation = None
    if p['simulate_rounds']:
        rates = {a: bandit.alpha[a] / (bandit.alpha[a] + bandit.beta[a]) for a in bandit.arms}
        sim = ThompsonOutreach(bandit.arms, PRIOR)
        sim.alpha, sim.beta = dict(bandit.alpha), dict(bandit.beta)
        best, regret, curve, engaged = max(rates.values()), 0.0, [], 0
        for k in range(p['simulate_rounds']):
            step = sim.allocate(p['simulate_batch'], rng)
            new = []
            for a, n in step.items():
                hits = rng.random(n) < rates[a]
                engaged += int(hits.sum()); regret += n * (best - rates[a])
                new += [{'arm': a, 'responded': bool(h)} for h in hits]
            sim.update(new)
            curve.append({'round': k + 1, 'cumulative_regret': round(regret, 3), 'cumulative_engaged': engaged})
        final = sim.summary(seed=p['seed'])
        simulation = {'label': 'simulated outcomes', 'basis': 'Bernoulli draws at the posterior-mean engagement rate of each arm (not observed data)',
                      'rounds': p['simulate_rounds'], 'batch': p['simulate_batch'], 'cumulative_regret': round(regret, 3),
                      'regret_per_contact': round(regret / (p['simulate_rounds'] * p['simulate_batch']), 5),
                      'simulated_engaged': engaged, 'curve': curve,
                      'final_p_best': {f'{s["arm"][0]}|{s["arm"][1]}': s['p_best'] for s in final}}
    ope = None
    try:  # off-policy evaluation before any recommendation (off_policy.py): DR value of the Thompson / best-arm policies vs logged
        from .off_policy import check_recommendation
        ope = check_recommendation(db, keep, as_of, p['segment_by'], p['seed'])
    except Exception as exc:  # noqa: BLE001 - the check annotates; it never takes the bandit answer down
        ope = {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'[:200]}
    return {**base, 'status': 'learning_from_logged_outcomes', 'arms': arms, 'attempts': len(log), 'off_policy_check': ope,
            'patients': len({x['patient_id'] for x in log}), 'patient_ids': sorted({x['patient_id'] for x in log}),
            'period': {'start': log[0]['date'], 'end': log[-1]['date']},
            'allocation': {f'{a[0]}|{a[1]}': int(n) for a, n in sorted(alloc.items())}, 'simulation': simulation, 'method': METHOD}


METHOD = ('Thompson sampling (Beta(1,1)-Bernoulli per channel x segment arm) over the logged outreach_attempts records; responded = '
          'outcome engaged; reach = engaged or declined. P(best) from 4,000 posterior draws; next batch allocated one posterior draw per '
          'contact. Simulated continuation (when requested) draws outcomes at the posterior means and is labelled simulated.')
ASSUMPTIONS = ['The outreach outcome log is synthetic warehouse data, not live outreach results.',
               'Engagement (outcome = engaged) is the reward; a declined contact was reached but is not a response.',
               'Attempts are treated as independent Bernoulli pulls; repeat attempts on the same patient and channel assignment by the '
               'program (not randomised) can bias arm comparisons.',
               'Simulated continuation outcomes are draws from the posterior means, not observations.']


def execute(path, patient_ids, params, as_of):
    from . import semantic_layer
    p = validate(params)
    target = semantic_layer.ensure(path) if (Path(path)/'run.json').exists() else Path(path)/'semantic.sqlite3'
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        return run(db, patient_ids, p, str(as_of)[:10])
    finally:
        db.close()


def headline(r):
    if not r['arms']: return 'No logged outreach outcomes in the received data; the bandit has nothing to learn from yet'
    b = r['arms'][0]
    seg = '' if b['segment'] == 'all' else f" for {b['segment']}"
    alloc = ', '.join(f"{k.replace('|all', '')} {v}" for k, v in sorted(r['allocation'].items(), key=lambda kv: -kv[1]))
    text = (f"Best arm: {b['channel']}{seg}, engagement {b['posterior_mean']:.0%} (95% CrI {b['ci95'][0]:.0%}-{b['ci95'][1]:.0%}, "
            f"P(best) {b['p_best']:.0%}) from {r['attempts']} logged attempts on {r['patients']} patients ({r['period']['start']} to "
            f"{r['period']['end']}, synthetic log). Next {r['parameters']['next_batch']} contacts by Thompson sampling: {alloc}")
    from .off_policy import describe_check
    ope = describe_check(r.get('off_policy_check'))
    if ope: text += '. ' + ope
    if r['simulation']:
        s = r['simulation']
        text += (f". Simulated outcomes ({s['rounds']} rounds x {s['batch']}): cumulative regret {s['cumulative_regret']:.1f} engagements "
                 f"({s['regret_per_contact']:.3f} per contact)")
    return text


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    if not report['arms']:
        return {'status': 'answered', 'answer_type': 'table', 'table': [], 'count': 0, 'patient_ids': [], 'value': headline(report) + '.',
                'method': report['method'], 'limitations': ASSUMPTIONS}
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['arms'], 'count': len(report['arms']),
            'patient_ids': report['patient_ids'], 'value': headline(report) + '.', 'method': report['method'],
            'definitions': [{'term': 'engagement', 'basis': 'outreach_attempts outcome = engaged'}],
            'limitations': ASSUMPTIONS, 'membership_role': 'evaluated',
            'overall': {'outreach_bandit': {'allocation': report['allocation'], 'simulation': report['simulation'],
                                            'off_policy_check': report.get('off_policy_check')}}}


# ---- free local-compiler route ----------------------------------------------------------------------------------------
CHANNEL = r'\b(?:channels?|phone|calls?|texts?|texting|sms|mail(?:ers?|ings?)?|letters?|outreach (?:methods?|modes?|types?))\b'
LEARN = (r'\bbandits?\b|\bthompson\b|\bexplor(?:e|ation|ing)\b|\bexploit\w*\b|\ba/?b test\w*\b|'
         r'\b(?:works?|worked|working|performs?|performing|best|better|most effective|highest (?:engagement|response))\b|'
         r'\b(?:engagement|response) rates? by (?:outreach )?(?:channel|method|mode)\b|\blearn\w*\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if re.match(r'(?:what (?:is|are)|explain|define|how (?:does|do) .* work)\b', low) and not re.search(r'\bour\b', low): return None
    if not re.search(r'\boutreach\b|\bengag\w*\b|\bcontact\w*\b|\breach\w*\b', low): return None
    if not re.search(CHANNEL, low) or not re.search(LEARN, low): return None
    if re.search(r'\ballocat\w*\b|\bassign\w*\b|\boptimi[sz]\w*\b|\bschedul\w*\b|\bhours?\b|\bslots?\b|\bcapacity\b', low) \
            and not re.search(r'\bbandits?\b|\bthompson\b', low): return None  # capacity allocation is outreach_allocation
    if re.search(r'\bwho (?:benefits?|should we)\b|\bpersuadables?\b|\bno[- ]?shows?\b|\breminders?\b', low): return None  # uplift / outreach list / scheduling
    params = {}
    if re.search(r'\brisk (?:tiers?|levels?|groups?)\b|\bby risk\b|\bhigh[- ]risk vs\b', low): params['segment_by'] = 'risk_tier'
    elif re.search(r'\bby program\b|\beach program\b', low): params['segment_by'] = 'program'
    elif re.search(r'\bfirst (?:attempt|contact)\b|\brepeat attempts?\b|\bby attempt\b', low): params['segment_by'] = 'attempt'
    n = re.search(r'\bnext (\d{1,5})\b|\b(\d{1,5}) (?:more )?(?:contacts|calls|attempts|outreach)\b', low)
    if n and 1 <= int(n.group(1) or n.group(2)) <= 10000: params['next_batch'] = int(n.group(1) or n.group(2))
    if re.search(r'\bsimulat\w*\b|\bregret\b', low): params['simulate_rounds'] = 20
    return plan(OP, params, 'Thompson-sampling bandit over outreach channels from the logged outcomes', question,
                [f"segment {params.get('segment_by', 'none')}"])


TEMPLATES = ['Which outreach channel gets the most patients to engage?', 'is texting or calling better for getting members to enroll',
             'Compare engagement rates by outreach channel for {conds}', 'what has our outreach log taught us about phone versus mail',
             'Use a bandit to decide how to split our next {n} outreach contacts', 'which contact method performs best by risk tier',
             'Should we keep exploring text outreach or exploit phone calls?', 'Thompson sampling on outreach channels with simulated regret',
             'Best performing outreach mode for first attempts versus repeat attempts', 'learn which outreach method works for high risk members']
