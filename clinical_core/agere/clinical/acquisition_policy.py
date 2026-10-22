"""Learned information-acquisition policy: where to spend evidence-acquisition effort, learned from recorded outcomes.

RESEARCH_PROGRAM.md (third prototype): estimate operation reliability and task improvement from recorded outcomes with
uncertainty, track full cost, and let "no further computation" be a valid result. This engine does that over the operations
Agere can actually take on a question (each route is one operation: apply compiled code, run a specialized router, reuse a saved
verified answer, retrieve evidence passages, escalate to a hosted model), per question family (count, rate/measure, cost/spend,
patient list/cohort, trend, comparison, other).

Data: every <stack>/learning.sqlite3 `outcomes` row (route, status, paid_calls, usd) with the answered-then-corrected/quarantined
rows counted as failures; a failed attempt keeps its cost. For each (family, operation):
  success   Beta(1,1) posterior on P(answered and never corrected); Wilson 95% interval reported
  cost      mean dollars per attempt (all attempts, failed ones included) and mean paid calls
  p_best    Thompson probability that the operation has the best success-per-cost in its family (cost floor 1 cent)
Policy per family: free operations first by success, then paid operations by lower-bound success per dollar, until the cumulative
success of the sequence reaches 95%. A paid operation whose success lower bound is below the floor has not demonstrated value and
the policy says "no further computation: ask for input or record the gap". Operations with fewer than min_attempts attempts are
marked explore (the posterior is wide) and are never treated as demonstrated.
Reduction in answer-failure rate here is an operational target; it is not expected clinical benefit. Synthetic data; no live call.
"""
import math
import random
import re
import sqlite3
from pathlib import Path

VERSION = 'acquisition-policy/1'
OP = 'acquisition_policy'
NEEDS_STACK = True
NATIVE = ('value',)
FAMILIES = ('count', 'rate_measure', 'cost_spend', 'patient_list', 'trend', 'comparison', 'other')
KIND = {'local_compiler': 'apply compiled code (free)', 'metric_router': 'run the governed-metric router', 'operator_router': 'run the operator router',
        'exact_question_cache': 'reuse a saved verified answer', 'semantic_cache': 'reuse a saved verified answer',
        'learned_verified_sql': 'reuse a saved verified answer', 'saved_cohort': 'reuse a saved verified answer',
        'evidence_retrieval': 'retrieve evidence passages', 'evidence_review': 'retrieve evidence passages',
        'hosted_verified_sql': 'escalate to a hosted model', 'hosted_missing_data': 'escalate to a hosted model',
        'frontier_escalation': 'escalate to a hosted model', 'shared_planner': 'escalate to a hosted model',
        'together_semantic_analyst': 'escalate to a hosted model', 'deferred_to_analyst': 'escalate to a hosted model',
        'local_model_sql': 'run the local model (free)', 'local_model_rewrite_compiled': 'run the local model (free)',
        'cohort_family': 'apply compiled code (free)', 'patient_therapy_review': 'apply compiled code (free)'}
PRODUCT = {'population': 'none', 'view': 'table',
           'use': 'WHERE TO SPEND EVIDENCE-ACQUISITION EFFORT: which operation (compiled code, specialized router, saved answer, evidence '
                  'retrieval, hosted-model escalation) to try first for each kind of question, learned from recorded outcomes with success '
                  'intervals and full cost, including when no further computation has demonstrated value.'}
CONTRACT = {'inputs': ['none'], 'parameters': {'family': 'count|rate_measure|cost_spend|patient_list|trend|comparison|other|all',
                                               'min_attempts': 12, 'floor': 0.1, 'seed': 0},
            'meaning': 'Per question family, per operation: success rate (answered and never corrected) with Wilson interval from the '
                       'recorded outcomes, mean dollars and paid calls per attempt, Thompson P(best value per dollar), and the learned try-order '
                       'with the explicit option of no further computation.'}


def schema(obj, string, array):
    return obj({'family': string(('all', *FAMILIES), 'Question family to report (default all)'),
                'min_attempts': {'type': 'integer', 'minimum': 2, 'maximum': 500, 'description': 'Attempts needed before an operation counts as demonstrated'},
                'floor': {'type': 'number', 'minimum': 0.0, 'maximum': 0.9, 'description': 'Success lower bound a paid operation must clear'},
                'seed': {'type': 'integer', 'minimum': 0, 'maximum': 1000000}})


def validate(p):
    allowed = {'family', 'min_attempts', 'floor', 'seed'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('acquisition_policy parameters: ' + ', '.join(sorted(allowed)))
    out = {'family': p.get('family', 'all'), 'min_attempts': p.get('min_attempts', 12), 'floor': p.get('floor', 0.1), 'seed': p.get('seed', 0)}
    if out['family'] not in ('all', *FAMILIES): raise ValueError(f'family: all|{"|".join(FAMILIES)}')
    if type(out['min_attempts']) is not int or not 2 <= out['min_attempts'] <= 500: raise ValueError('min_attempts 2..500')
    if isinstance(out['floor'], bool) or not isinstance(out['floor'], (int, float)) or not 0 <= out['floor'] <= 0.9: raise ValueError('floor 0..0.9')
    if type(out['seed']) is not int or not 0 <= out['seed'] <= 1000000: raise ValueError('seed 0..1000000')
    out['floor'] = float(out['floor'])
    return out


def family_of(question):
    q = ' '.join(str(question or '').lower().split())
    for fam, rx in (('patient_list', r'\b(?:list|which patients|who (?:has|have|is|are|should)|patient list|worklist|cohort of)\b'),
                    ('trend', r'\b(?:trend|over time|month over month|year over year|by month|by quarter|monthly|quarterly|since)\b'),
                    ('cost_spend', r'\b(?:cost|spend|paid|allowed|pmpm|dollars?|\$|revenue|payment|reimburse\w*)\b'),
                    ('rate_measure', r'\b(?:rate|percent|percentage|proportion|share|per 1,?000|measure|hedis|stars?|adherence)\b'),
                    ('comparison', r'\b(?:compare|versus|vs\.?|by (?:payer|practice|provider|pcp|clinic|site)|difference between|each)\b'),
                    ('count', r'\b(?:how many|count|number of|total)\b')):
        if re.search(rx, q): return fam
    return 'other'


def wilson(k, n, z=1.96):
    if n == 0: return 0.0, 1.0
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def outcome_rows(stack_path):
    """-> [(family, route, success, usd, calls)] from the stack's recorded outcomes."""
    target = Path(stack_path) / 'learning.sqlite3'
    if not target.exists(): return []
    out = []
    with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'outcomes' not in names: return []
        bad = set()
        for table in ('corrections', 'quarantined'):
            if table in names: bad |= {' '.join(str(q).lower().split()) for (q,) in db.execute(f'SELECT question FROM {table}')}
        for q, route, status, usd, calls in db.execute('SELECT question, route, status, usd, paid_calls FROM outcomes'):
            if status not in ('answered', 'partial', 'escalated', 'declined', 'internal_error', 'not_understood', 'unsupported', 'not_available'): continue
            ok = status == 'answered' and ' '.join(str(q).lower().split()) not in bad
            out.append((family_of(q), route, ok, float(usd or 0.0), int(calls or 0)))
    return out


def estimate(rows, p):
    cells = {}
    for fam, route, ok, usd, calls in rows:
        c = cells.setdefault((fam, route), {'n': 0, 'k': 0, 'usd': 0.0, 'calls': 0})
        c['n'] += 1; c['k'] += ok; c['usd'] += usd; c['calls'] += calls
    rng = random.Random(p['seed'])
    table, policy = [], []
    for fam in FAMILIES:
        if p['family'] not in ('all', fam): continue
        ops = {r: c for (f, r), c in cells.items() if f == fam and c['n'] >= 1}
        if not ops: continue
        draws = {r: [rng.betavariate(1 + c['k'], 1 + c['n'] - c['k']) / max(c['usd'] / c['n'], 0.01) for _ in range(400)] for r, c in ops.items()}
        best = {r: 0 for r in ops}
        for i in range(400): best[max(ops, key=lambda r: draws[r][i])] += 1
        rowsf = []
        for r, c in ops.items():
            lo, hi = wilson(c['k'], c['n']); mean_usd = c['usd'] / c['n']
            rowsf.append({'family': fam, 'operation': r, 'kind': KIND.get(r, 'other route'), 'attempts': c['n'], 'answered': c['k'],
                          'success': round(c['k'] / c['n'], 4), 'success_ci95': [round(lo, 4), round(hi, 4)], 'mean_usd': round(mean_usd, 5),
                          'mean_paid_calls': round(c['calls'] / c['n'], 3), 'p_best_value': round(best[r] / 400, 3),
                          'free': mean_usd == 0.0 and c['calls'] == 0, 'demonstrated': c['n'] >= p['min_attempts'] and lo > 0.0,
                          'explore': c['n'] < p['min_attempts']})
        table += sorted(rowsf, key=lambda x: (-x['attempts']))
        # try-order: free ops by success, then paid by lower-bound success per dollar, until cumulative success >= 95%
        useful = [x for x in rowsf if x['answered'] > 0]
        free = sorted([x for x in useful if x['free']], key=lambda x: -x['success_ci95'][0])
        paid = sorted([x for x in useful if not x['free']], key=lambda x: -(x['success_ci95'][0] / max(x['mean_usd'], 0.01)))
        seq, miss, skipped = [], 1.0, []
        for x in free + paid:
            if miss <= 0.05: break
            if not x['free'] and x['success_ci95'][0] < p['floor']: skipped.append(x['operation']); continue
            seq.append(x['operation']); miss *= 1 - x['success']
        policy.append({'family': fam, 'try_order': seq, 'cumulative_success': round(1 - miss, 4),
                       'expected_usd_if_all_tried': round(sum(next(y['mean_usd'] for y in rowsf if y['operation'] == o) for o in seq), 4),
                       'not_demonstrated': skipped,
                       'decision': ('no further computation has demonstrated value: ask for input or record the gap' if not seq else
                                    f"try {seq[0]} first" + (f", then {', '.join(seq[1:3])}" if len(seq) > 1 else ''))})
    return table, policy


ASSUMPTIONS = ['Outcome labels come from this stack\'s recorded answers: answered and never corrected or quarantined counts as success; an escalated, declined or errored attempt is a failure that keeps its cost.',
               'Routes are the operations taken; a route that was never tried for a family has no estimate (not zero value).',
               'Question families are assigned from the question wording; one question lands in one family.',
               'Success means the answer was delivered and not later corrected, not that it changed a clinical decision.',
               'Synthetic warehouse questions; costs are the estimated API dollars recorded per attempt.']
METHOD = ('Beta(1,1) posteriors on P(answered and never corrected) per (question family, operation) from recorded outcomes with Wilson 95% '
          'intervals; mean dollars and paid calls per attempt including failed attempts; Thompson sampling (400 draws) for P(best success per '
          'dollar, cost floor 1 cent); try-order = free operations by lower-bound success, then paid operations by lower-bound success per dollar '
          'until cumulative success reaches 95%; a paid operation below the success floor is not demonstrated and the policy may stop.')


def execute(stack, patient_ids, params, as_of):
    p = validate(params)
    rows = outcome_rows(stack.path)
    table, policy = estimate(rows, p)
    return {'schema': VERSION, 'as_of': str(as_of)[:10], 'parameters': p, 'outcomes': len(rows), 'table': table, 'policy': policy,
            'method': METHOD, 'assumptions': ASSUMPTIONS, 'patient_ids': []}


def headline(r):
    if not r['outcomes']: return 'No recorded outcomes in this stack yet: nothing to learn an acquisition policy from'
    parts = [f"{x['family']}: {x['decision']} (cumulative success {x['cumulative_success']:.0%}, about ${x['expected_usd_if_all_tried']:.2f} if every step is needed)"
             for x in r['policy']]
    return f"Learned acquisition policy from {r['outcomes']} recorded outcomes. " + '; '.join(parts)


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['table'], 'count': len(report['table']), 'patient_ids': [],
            'value': headline(report) + '.', 'method': report['method'], 'limitations': report['assumptions'], 'membership_role': 'evaluated',
            'definitions': [{'term': 'success', 'basis': 'answered and never corrected or quarantined'},
                            {'term': 'p_best_value', 'basis': 'Thompson probability of the best success per dollar within the family'}],
            'overall': {'acquisition_policy': {'policy': report['policy'], 'outcomes': report['outcomes']}}}


POLICY_RX = (r'\b(?:where|what|which)\b.{0,40}\b(?:spend|invest|put)\b.{0,40}\b(?:effort|evidence|acquisition|compute|computation|model (?:calls|spend))\b|'
             r'\b(?:acquisition|information[- ]acquisition) polic\w+\b|\bwhich (?:tier|operation|route|step)\b.{0,50}\b(?:first|cheapest|best|worth)\b|'
             r'\b(?:is it )?worth (?:escalating|calling|paying)\b.{0,40}\b(?:model|hosted|opus|claude)\b|\bwhen (?:should|do) we (?:stop|escalate)\b|'
             r'\bcheapest (?:way|route|path) to answer\b|\bwhere is (?:our )?(?:model|api|llm) spend (?:paying off|worth)\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    import re as _re
    low = ' '.join(str(question).lower().split())
    if not _re.search(POLICY_RX, low): return None
    params = {}
    fam = family_of(low)
    if fam != 'other' and _re.search(r'\b(?:for|on|about)\b.{0,30}\b(?:counts?|rates?|measures?|cost|spend|trend|lists?|comparisons?)\b', low): params['family'] = fam
    return plan(OP, params, 'Learn where to spend evidence-acquisition effort from recorded outcomes', question, [f"family {params.get('family', 'all')}"])


TEMPLATES = ['Where should we spend evidence acquisition effort?', 'Which operation should we try first for cost questions, and is escalating to a hosted model worth it?',
             'What is our learned acquisition policy by question type?', 'Where is our model spend paying off?',
             'What is the cheapest way to answer our count questions, and when should we stop?', 'When should we escalate to a hosted model instead of stopping?',
             'which tier should we try first for a trend question', 'information acquisition policy for rate and measure questions']
