"""Acquisition advice from recorded delivery outcomes, with paired replay and explicit unknowns.

Marginal success does not identify sequence coverage. Paired replay retains source,
question, episode and attempt identity, counts costs until observed delivery, and
never imputes an untried route. Reordering a replay assumes stable route outcomes;
it is not an observed policy trial or clinical qualification.
"""
import json
import math
import random
import re
import sqlite3
from pathlib import Path

VERSION = 'acquisition-policy/2'
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
                       'with paired residual coverage/cost when episode/source/attempt evidence supports it; missing joint outcomes '
                       'remain unknown, and untried routes are not failures. The independent-marginal scenario is not a stopping rule.'}


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
    """Read attempts without inventing release/episode identities for legacy records."""
    target = Path(stack_path) / 'learning.sqlite3'
    if not target.exists(): return []
    out = []
    with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'outcomes' not in names: return []
        bad = set()
        for table in ('corrections', 'quarantined'):
            if table in names:
                bad |= {' '.join(str(q).lower().split()) for (q,) in db.execute(f'SELECT question FROM {table}')}
        columns = {r[1] for r in db.execute('PRAGMA table_info(outcomes)')}
        fields = ['question', 'route', 'status', 'usd', 'paid_calls', 'created', 'source_context', 'episode_id', 'attempt_id', 'prior_routes']
        select = ','.join(f'"{f}"' if f in columns else 'NULL' for f in fields)
        for row in db.execute('SELECT rowid,' + select + ' FROM outcomes'):
            rowid, *values = row
            item = dict(zip(fields, values))
            q, status = item['question'], item['status']
            if status not in ('answered', 'partial', 'escalated', 'declined', 'internal_error', 'not_understood', 'unsupported', 'not_available'):
                continue
            out.append({'family': family_of(q), 'route': item['route'], 'ok': status == 'answered' and ' '.join(str(q).lower().split()) not in bad,
                        'usd': item['usd'], 'calls': item['paid_calls'], 'question': q,
                        'source_context': item['source_context'], 'episode_id': item['episode_id'],
                        'attempt_id': item['attempt_id'], 'prior_routes': item['prior_routes'],
                        'created': item['created'], 'record_identity': [str(target.resolve()), rowid]})
    return out


def _attempts(rows):
    """Exact duplicate event IDs count once; conflicting IDs invalidate pairing/cost."""
    result, events = [], {}
    for value in rows:
        if isinstance(value, dict):
            row = dict(value)
        else:
            fam, route, ok, usd, calls = value
            row = dict(family=fam, route=route, ok=ok, usd=usd, calls=calls)
        event = (row.get('source_context'), row.get('attempt_id'))
        if all(event):
            if event in events:
                prior = events[event]
                fields = ('family','route','ok','usd','calls','question','episode_id','prior_routes')
                if any(prior.get(k) != row.get(k) for k in fields):
                    prior.update(ok=None, usd=None, calls=None, identity_conflict=True)
                continue
            events[event] = row
        result.append(row)
    return result


def paired_replay(rows, sequence):
    """Empirical union bounds and stopping cost on explicit episodes; no missing counterfactuals."""
    groups, unidentified = {}, 0
    for row in rows:
        identity = tuple(row.get(k) for k in ('source_context','episode_id','question'))
        if not all(identity) or not row.get('attempt_id') or row.get('identity_conflict'):
            unidentified += 1
            continue
        group = groups.setdefault(identity, {})
        group.setdefault(row['route'], []).append(row)
    base = {'basis': 'paired replay; route outcome/cost invariance under reordering assumed',
            'episodes': len(groups), 'unidentified_attempts': unidentified, 'clinical_qualification': False}
    if unidentified or not groups:
        return {**base, 'coverage': None, 'coverage_bounds': None, 'expected_usd_per_question': None,
                'usd_per_delivered_workflow': None, 'conditional': None,
                'reason': 'joint coverage not identified: explicit question/source/episode/attempt evidence required'}
    delivered, unknown, total, cost_known = 0, 0, 0., True
    residual_n, observed_n, gained, step_cost = 0, 0, 0, []
    for group in groups.values():
        # A route with retries has no identified single-operation counterfactual.
        observed = {route: items[0] for route, items in group.items() if len(items) == 1 and items[0].get('ok') is not None}
        for i, route in enumerate(sequence):
            item = observed.get(route)
            if item is None: continue
            history = item.get('prior_routes')
            try:
                history = json.loads(history) if isinstance(history, str) else history
                if history is not None and not isinstance(history, list): raise ValueError('invalid route history')
                # Empty history is a fresh route observation. Reordering it is
                # explicitly a replay assumption. Nonempty observed histories
                # cannot be transferred to a different prefix or attempt.
                if history:
                    names = [h.get('route') if isinstance(h, dict) else h for h in history]
                    if names != list(sequence[:i]): raise ValueError('different observed route history')
                    for h in history:
                        if isinstance(h, dict) and h.get('attempt_id') and h['attempt_id'] != observed.get(h['route'], {}).get('attempt_id'):
                            raise ValueError('different prior attempt')
            except (ValueError, TypeError, KeyError):
                observed.pop(route, None)
        prior = sequence[:-1]
        if sequence and all(r in observed and not observed[r]['ok'] for r in prior):
            residual_n += 1
            last = observed.get(sequence[-1])
            if last is not None:
                observed_n += 1
                gained += bool(last['ok'])
                step_cost.append(last.get('usd'))
        yes = any(observed.get(r, {}).get('ok') is True for r in sequence)
        missing = any(r not in observed for r in sequence)
        delivered += yes
        unknown += not yes and missing
        for route in sequence:
            item = observed.get(route)
            if item is None:
                cost_known = False
                break
            if item.get('usd') is None:
                cost_known = False
            else:
                total += float(item['usd'])
            if item['ok']: break
    n = len(groups)
    conditional = {'residual_episodes': residual_n, 'observed': observed_n, 'additional_deliveries': gained,
                   'success': gained / observed_n if observed_n else None,
                   'all_residual_observed': observed_n == residual_n,
                   'success_ci95': list(wilson(gained, observed_n)),
                   'mean_usd': sum(step_cost) / len(step_cost) if step_cost and all(c is not None for c in step_cost) else None}
    return {**base, 'coverage': delivered / n if not unknown else None,
            'coverage_bounds': [delivered / n, (delivered + unknown) / n], 'delivered': delivered,
            'unknown_episodes': unknown, 'conditional': conditional,
            'expected_usd_per_question': total / n if cost_known else None,
            'usd_per_delivered_workflow': total / delivered if cost_known and not unknown and delivered else None,
            'reason': 'complete observed replay' if not unknown else 'missing route outcomes remain unknown'}


def estimate(rows, p):
    rows = _attempts(rows)
    cells = {}
    for row in rows:
        fam, route, ok, usd, calls = (row.get(k) for k in ('family','route','ok','usd','calls'))
        c = cells.setdefault((fam, route), {'n': 0, 'k': 0, 'unknown': 0, 'usd': 0.0, 'calls': 0, 'cost_unknown': False, 'calls_unknown': False})
        c['n'] += 1; c['k'] += bool(ok)
        c['unknown'] += ok is None
        c['cost_unknown'] |= usd is None
        c['calls_unknown'] |= calls is None
        c['usd'] += float(usd or 0); c['calls'] += int(calls or 0)
    rng = random.Random(p['seed'])
    table, policy = [], []
    for fam in FAMILIES:
        if p['family'] not in ('all', fam): continue
        ops = {r: c for (f, r), c in cells.items() if f == fam and c['n'] >= 1}
        if not ops: continue
        draws = {r: [rng.betavariate(1 + c['k'], 1 + c['n'] - c['unknown'] - c['k']) / max(c['usd'] / c['n'], 0.01) for _ in range(400)]
                 for r, c in ops.items() if not c['cost_unknown'] and c['n'] > c['unknown']}
        best = {r: 0 for r in ops}
        for i in range(400):
            if draws: best[max(draws, key=lambda r: draws[r][i])] += 1
        rowsf = []
        for r, c in ops.items():
            settled = c['n'] - c['unknown']
            lo, hi = wilson(c['k'], settled); mean_usd = c['usd'] / c['n']
            rowsf.append({'family': fam, 'operation': r, 'kind': KIND.get(r, 'other route'), 'attempts': c['n'], 'answered': c['k'],
                          'settled_attempts': settled, 'unknown_labels': c['unknown'],
                          'success': round(c['k'] / settled, 4) if settled else None, 'success_ci95': [round(lo, 4), round(hi, 4)], 'mean_usd': None if c['cost_unknown'] else round(mean_usd, 5),
                          'mean_paid_calls': None if c['calls_unknown'] else round(c['calls'] / c['n'], 3),
                          'p_best_value': round(best[r] / 400, 3) if r in draws else None,
                          'free': not c['cost_unknown'] and not c['calls_unknown'] and mean_usd == 0.0 and c['calls'] == 0, 'demonstrated': settled >= p['min_attempts'] and lo > 0.0,
                          'explore': settled < p['min_attempts']})
        table += sorted(rowsf, key=lambda x: (-x['attempts']))
        # Accuracy first: compare additional delivery on unresolved episodes.
        # The old fixed 95% product-of-marginals stop is not an acquisition budget.
        family_rows = [r for r in rows if r['family'] == fam]
        remaining = [x for x in rowsf if x['answered'] > 0]
        seq, skipped, steps = [], [], []
        while remaining:
            options = []
            for x in remaining:
                replay = paired_replay(family_rows, seq + [x['operation']])
                cond = replay['conditional']
                rate = cond['success'] if cond and cond['success'] is not None else x['success']
                lo = cond['success_ci95'][0] if cond and cond['observed'] else x['success_ci95'][0]
                evidence_n = cond['observed'] if cond and cond['observed'] else x['settled_attempts']
                if cond and cond['all_residual_observed'] and cond['residual_episodes'] and cond['additional_deliveries'] == 0:
                    continue
                if evidence_n < p['min_attempts'] or not x['free'] and lo < p['floor']:
                    continue
                cost = cond['mean_usd'] if cond else x['mean_usd']
                # Success is primary; cost breaks ties within the free/paid class.
                rank = (x['free'], rate, -(cost if cost is not None else float('inf')), -rowsf.index(x))
                options.append((rank, x, replay))
            if not options:
                skipped.extend(x['operation'] for x in remaining)
                break
            _, chosen, replay = max(options, key=lambda item: item[0])
            seq.append(chosen['operation']); remaining.remove(chosen)
            steps.append({'operation': chosen['operation'], **replay})
            if replay['coverage'] == 1.0:
                break
        replay = paired_replay(family_rows, seq)
        all_cost = [x['mean_usd'] for x in rowsf if x['operation'] in seq]
        independence = 1 - math.prod(1 - x['success'] for x in rowsf if x['operation'] in seq)
        policy.append({'family': fam, 'try_order': seq, 'cumulative_success': replay['coverage'],
                       'coverage_bounds': replay['coverage_bounds'], 'sequence_evidence': replay, 'steps': steps,
                       'independence_scenario': {'coverage': round(independence, 4),
                           'assumption': 'comparable populations and independent marginal failures; not supported joint coverage or a stopping rule'},
                       'expected_usd_if_all_tried': round(sum(all_cost), 5) if all(c is not None for c in all_cost) else None,
                       'expected_usd_per_original_question': replay['expected_usd_per_question'],
                       'usd_per_delivered_workflow': replay['usd_per_delivered_workflow'],
                       'not_demonstrated': skipped,
                       'remaining_candidates': [x['operation'] for x in remaining],
                       'decision': ('no further computation is justified by these evidence rules; untried value remains unknown' if not seq else
                                    ('paired replay order: ' if replay['coverage'] is not None else 'provisional order; joint delivery unresolved: ') + ', then '.join(seq)),
                       'stop_reason': 'all recorded episodes covered; future performance unqualified' if replay['coverage'] == 1 else
                                      'coverage or additional route value remains unresolved; no automatic stopping instruction'})
    return table, policy


ASSUMPTIONS = ['Delivered and never corrected/quarantined is an operational proxy, not clinical truth or benefit.',
               'Source, exact question, episode and attempt identities define pairing; legacy evidence cannot establish joint coverage.',
               'Fresh-route replay assumes route outcomes and costs remain stable under the proposed order; supplied nonempty route histories must match. It is not an observed policy experiment or globally optimal policy.',
               'Untried routes and missing outcomes/costs stay unknown. Repeated attempts retain their cost; identical event IDs count once.',
               'Empirical coverage and descriptive Wilson intervals do not qualify clinical generalization or a selected policy.',
               'The independent-marginal scenario is an assumption, not measured sequence coverage or permission to stop.']
METHOD = ('Marginal attempt-level delivery/cost summaries and descriptive Wilson intervals; paired residual delivery replay with '
          'explicit missing-outcome bounds and stopping costs. Accuracy precedes cost; no product-of-marginals stopping rule. '
          'Thompson marginal value draws are descriptive, not conditional acquisition value or policy qualification.')


def execute(stack, patient_ids, params, as_of):
    p = validate(params)
    raw = outcome_rows(getattr(stack, 'path', stack))  # engines receive the stack path; some callers pass the stack
    rows = _attempts(raw)
    table, policy = estimate(rows, p)
    return {'schema': VERSION, 'as_of': str(as_of)[:10], 'parameters': p, 'outcomes': len(rows),
            'recorded_rows': len(raw), 'duplicate_records': len(raw) - len(rows),
            'recorded_total_usd': sum(r['usd'] for r in rows) if all(r.get('usd') is not None for r in rows) else None,
            'recorded_paid_calls': sum(r['calls'] for r in rows) if all(r.get('calls') is not None for r in rows) else None,
            'table': table, 'policy': policy,
            'method': METHOD, 'assumptions': ASSUMPTIONS, 'patient_ids': []}


def headline(r):
    if not r['outcomes']: return 'No recorded outcomes in this stack yet: nothing to learn an acquisition policy from'
    parts = []
    for x in r['policy']:
        rate = x['cumulative_success']
        text = 'joint coverage unknown' if rate is None else f'paired replay delivery {rate:.0%}'
        cost = x['expected_usd_per_original_question']
        spend = 'stopping cost unknown' if cost is None else f'${cost:.2f} per original question in replay'
        parts.append(f"{x['family']}: {x['decision']} ({text}; {spend})")
    return f"Acquisition evidence from {r['outcomes']} recorded attempts. " + '; '.join(parts)


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['table'], 'count': len(report['table']), 'patient_ids': [],
            'value': headline(report) + '.', 'method': report['method'], 'limitations': report['assumptions'], 'membership_role': 'evaluated',
            'definitions': [{'term': 'success', 'basis': 'answered and never corrected or quarantined'},
                            {'term': 'p_best_value', 'basis': 'Descriptive marginal Thompson draws among known-cost operations, not conditional sequence value'}],
            'overall': {'acquisition_policy': {k: report[k] for k in ('policy','outcomes','recorded_rows','duplicate_records',
                                                                    'recorded_total_usd','recorded_paid_calls')}}}


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
