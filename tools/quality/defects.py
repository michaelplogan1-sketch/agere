"""Six Sigma defect tracking for Agere answers, from every scored run in bench/results (read-only).

Unit of work: one benchmark question. Opportunity: one answerable question (expected answer type is not
'unsupported'); each answer is one opportunity for an engine error.
Defects, two tiers:
  strict      the bench scorer failed an answerable question (includes format differences and defensible definitions)
  adjudicated an independent adjudicator (bench/adjudicate.py; results saved in bench/results/adjudications/)
              classified the failure as engine_error. 'unparsed' adjudications give an upper bound.
DPMO = defects / opportunities x 1e6. Sigma level = z(1 - DPMO/1e6) + 1.5 (the conventional long-term shift).
Intervals are 95% Wilson score intervals. The p-chart reuses clinical_core/agere/clinical/monitor.control_chart.
The Pareto maps failures to failure-mode ids in docs/agere/fmea.json.

Usage:
  .venv/bin/python tools/quality/defects.py [--results DIR] [--json] [--update-fmea]
--update-fmea refreshes the evidence, occurrence and RPN fields of docs/agere/fmea.json and the ranked table in
docs/agere/FMEA.md. Nothing here makes a paid call or writes to bench/results.
"""
from __future__ import annotations

import argparse, copy, json, math, re, sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
RESULTS = ROOT / 'bench' / 'results'
FMEA_PATH = ROOT / 'docs' / 'agere' / 'fmea.json'
FMEA_MD = ROOT / 'docs' / 'agere' / 'FMEA.md'
DEVELOPMENT_SETS = {'C', 'D', 'C+D'}
FULL_SHARE = 0.9  # a run covering >= 90% of its set's questions is a full run; smaller runs are targeted reruns


# ---- statistics ---------------------------------------------------------------------------------------------
def wilson(k, n, z=1.959964):
    """95% Wilson score interval for k defects in n opportunities, or None when n is 0."""
    if not n: return None
    p = k / n; d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def dpmo(defects, opportunities):
    return None if not opportunities else 1e6 * defects / opportunities


def sigma_level(dpmo_value, shift=1.5):
    """Short-term sigma with the 1.5 sigma shift: 308,537 DPMO -> 2.0, 66,807 -> 3.0, 3.4 -> 6.0. None at 0 or 1e6."""
    if dpmo_value is None or dpmo_value <= 0 or dpmo_value >= 1e6: return None
    return NormalDist().inv_cdf(1 - dpmo_value / 1e6) + shift


def occurrence_rating(rate, count, bands):
    if count == 0: return 2
    for threshold, rating in bands:
        if rate >= threshold: return rating
    return 3


# ---- loading ------------------------------------------------------------------------------------------------
def _set_label(rows):
    letters = Counter((r.get('id') or '?')[0] for r in rows)
    if set(letters) <= {'C', 'D'} and letters: return 'C+D' if len(letters) == 2 else next(iter(letters))
    blind = Counter({k: v for k, v in letters.items() if k not in ('C', 'D')})
    return blind.most_common(1)[0][0] if blind else '?'


def load_runs(results=RESULTS):
    """Every scored run: {system, file, started, set, rows}. Unreadable lines are skipped and counted."""
    runs = []
    for path in sorted(Path(results).glob('*/*.jsonl')):
        rows, bad = [], 0
        for line in path.read_text().splitlines():
            if not line.strip(): continue
            try: row = json.loads(line)
            except json.JSONDecodeError: bad += 1; continue
            if isinstance(row, dict) and isinstance(row.get('score'), dict): rows.append(row)
        if not rows: continue
        label = _set_label(rows)
        if label not in DEVELOPMENT_SETS:  # a blind run that also re-ran development questions: keep only the blind set's rows
            rows = [r for r in rows if (r.get('id') or '?')[0] == label]
        runs.append({'system': path.parent.name, 'file': f'{path.parent.name}/{path.name}', 'started': min(r.get('started_at') or '' for r in rows) or path.stem,
                     'set': label, 'rows': rows, 'unreadable_lines': bad})
    largest = defaultdict(int)
    for r in runs: largest[r['set']] = max(largest[r['set']], len(r['rows']))
    for r in runs:
        r['full'] = len(r['rows']) >= FULL_SHARE * largest[r['set']]
        r['split'] = 'development' if r['set'] in DEVELOPMENT_SETS else 'blind'
    return sorted(runs, key=lambda r: r['started'])


def load_adjudications(results=RESULTS):
    out = {}
    for path in sorted((Path(results) / 'adjudications').glob('*.json')):
        try: doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError): continue
        if isinstance(doc, dict) and doc.get('results_file'): out[doc['results_file']] = doc
    return out


def load_fmea(path=FMEA_PATH):
    return json.loads(Path(path).read_text())


# ---- classification -----------------------------------------------------------------------------------------
def answerable(row):
    expected = row.get('expected') or {}
    return expected.get('answer_type') != 'unsupported' and expected.get('status') != 'unsupported'


def route(row):
    receipt = (row.get('response') or {}).get('planner_receipt') or {}
    return receipt.get('route') or ('error' if row.get('error') else 'unknown')


def mechanical_class(row):
    """Failure class from the bench score alone (no adjudication), or None for a correct answer."""
    score = row['score']
    if score.get('correct'): return None
    answer = row.get('answer') if isinstance(row.get('answer'), dict) else {}
    if not answerable(row):
        return 'unsupported_answered' if answer.get('answer_type') not in (None, 'unsupported') else 'abstention_rubric'
    reason = score.get('reason')
    if row.get('error') or reason == 'missing_answer': return 'execution_error'
    if reason == 'invalid_answer_contract': return 'invalid_contract'
    if reason == 'answer_type_mismatch':
        return 'false_refusal' if answer.get('answer_type') == 'unsupported' or answer.get('status') in ('unsupported', 'grounding_failed') else 'shape_mismatch'
    if reason == 'incomplete_answer': return 'partial_but_right'
    return 'wrong_value_unadjudicated'


def classify_engine_error(reason, fmea):
    """FMEA id for an adjudicated engine error, by the first matching pattern in fmea['classification_order']."""
    modes = {m['id']: m for m in fmea['modes']}
    for mode_id in fmea['classification_order']:
        pattern = (modes[mode_id].get('evidence_rule') or {}).get('adjudicated_pattern')
        if pattern and re.search(pattern, reason or '', re.I): return mode_id
    return 'unclassified'


# ---- per-run metrics ----------------------------------------------------------------------------------------
def run_metrics(run, adjudication=None):
    rows = run['rows']
    ans = [r for r in rows if answerable(r)]
    unans = [r for r in rows if not answerable(r)]
    strict = sum(1 for r in ans if not r['score'].get('correct'))
    classes = Counter(c for c in (mechanical_class(r) for r in rows) if c)
    from bench.score import recorded_cost
    costs = [recorded_cost(r) for r in rows]
    out = {'system': run['system'], 'file': run['file'], 'started': run['started'], 'set': run['set'], 'split': run['split'], 'full': run['full'],
           'questions': len(rows), 'answerable': len(ans), 'unanswerable': len(unans),
           'correct': sum(1 for r in rows if r['score'].get('correct')), 'correct_answerable': len(ans) - strict,
           'correct_abstentions': sum(1 for r in unans if r['score'].get('correct')),
           'strict_defects': strict, 'strict_rate': strict / len(ans) if ans else None, 'strict_ci': wilson(strict, len(ans)),
           'strict_dpmo': dpmo(strict, len(ans)), 'strict_sigma': sigma_level(dpmo(strict, len(ans))),
           'mechanical': dict(classes), 'known_cost_usd': sum(c for c in costs if c is not None),
           'cost_unknown_rows': sum(1 for c in costs if c is None), 'adjudicated': False}
    if adjudication:
        counts = adjudication.get('counts') or {}
        engine, unparsed = int(counts.get('engine_error', 0)), int(counts.get('unparsed', 0))
        out.update(adjudicated=True, adjudication_counts=counts, adjudication_provenance=adjudication.get('provenance'),
                   adjudication_items_complete=bool(adjudication.get('items_complete')),
                   engine_errors=engine, engine_errors_upper=engine + unparsed,
                   engine_rate=engine / len(ans) if ans else None, engine_ci=wilson(engine, len(ans)),
                   engine_upper_ci=wilson(engine + unparsed, len(ans)),
                   engine_dpmo=dpmo(engine, len(ans)), engine_sigma=sigma_level(dpmo(engine, len(ans))))
    return out


def p_chart(points, laney=False):
    """p-chart over runs: points = [{'label', 'defects', 'opportunities'}]; limits from monitor.control_chart (Laney-adjusted)."""
    points = [p for p in points if p.get('opportunities')]
    if len(points) < 2: return {'points': points, 'center': None, 'note': 'fewer than two runs'}
    from clinical_core.agere.clinical.monitor import control_chart
    chart = control_chart([p['defects'] for p in points], [p['opportunities'] for p in points], kind='p', laney=laney)
    out = []
    for p, c in zip(points, chart['points']):
        signal = None
        if c.get('value') is not None and c.get('ucl') is not None:
            signal = 'above' if c['value'] > c['ucl'] else 'below' if c['value'] < c['lcl'] else None
        out.append({**p, 'rate': c.get('value'), 'lcl': c.get('lcl'), 'ucl': c.get('ucl'), 'signal': signal})
    return {'points': out, 'center': chart['center'], 'laney_phi': chart['phi'], 'kind': 'p'}


def pareto(counter, fmea):
    names = {m['id']: m['failure_mode'] for m in fmea['modes']}
    total = sum(counter.values()); acc = 0; out = []
    for key, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        acc += n
        out.append({'mode': key, 'label': names.get(key, key.replace('_', ' ')), 'count': n, 'share': n / total if total else 0,
                    'cumulative': acc / total if total else 0})
    return out


def control_replay(rows):
    """Re-run the answer-level controls on preserved answers (pure functions; no service, no model)."""
    from clinical_core.agere.clinical.answer_checks import annotate
    hits = defaultdict(lambda: {'fired': 0, 'on_wrong': 0, 'on_correct': 0, 'warnings': 0})
    for r in rows:
        a = copy.deepcopy(r.get('answer')) if isinstance(r.get('answer'), dict) else None
        if not a: continue
        a.pop('quality_checks', None)
        annotate(a, r.get('question'), r.get('as_of'))
        for c in a.get('quality_checks') or []:
            if c.get('control') == 'error': continue
            h = hits[c['control']]; h['fired'] += 1
            h['on_correct' if r['score'].get('correct') else 'on_wrong'] += 1
            h['warnings'] += c.get('severity') == 'warning'
    return dict(hits)


# ---- whole report -------------------------------------------------------------------------------------------
def report(results=RESULTS, fmea=None):
    fmea = fmea or load_fmea()
    runs = load_runs(results)
    adjudications = load_adjudications(results)
    metrics = [run_metrics(r, adjudications.get(r['file'])) for r in runs]
    by_file = {r['file']: r for r in runs}
    agere_blind = [m for m in metrics if m['system'] == 'agere' and m['split'] == 'blind' and m['full']]
    # Adjudicated Pareto (agere): engine errors classified to FMEA modes; categories mapped directly.
    first_runs = sorted({m['set']: m for m in reversed(agere_blind)}.values(), key=lambda m: m['started'])  # first full run of each blind set
    # Latest adjudicated run per blind set: one adjudication per question set, so re-runs of a set are not double counted.
    latest_adj = sorted({m['set']: m for m in agere_blind if m['adjudicated']}.values(), key=lambda m: m['started'])
    adj_counter, adj_examples, hosted_engine, adj_opps = Counter(), defaultdict(list), 0, 0
    for m in latest_adj:
        doc = adjudications.get(m['file'])
        adj_opps += m['answerable']
        rows = {r['id']: r for r in by_file[m['file']]['rows']}
        engine_items = [i for i in doc.get('items') or [] if i.get('category') == 'engine_error']
        for i in engine_items:
            mode = classify_engine_error(i.get('reason'), fmea)
            adj_counter[mode] += 1; adj_examples[mode].append(f"{m['set']}:{i['id']}")
            if route(rows.get(i['id'], {})) == 'hosted_verified_sql': hosted_engine += 1
        unlisted = int((doc.get('counts') or {}).get('engine_error', 0)) - len(engine_items)
        if unlisted > 0: adj_counter['unclassified'] += unlisted
        for category, mode in fmea['category_modes'].items():
            if (doc.get('counts') or {}).get(category): adj_counter[mode] += int(doc['counts'][category])
        if (doc.get('counts') or {}).get('unparsed'): adj_counter['unparsed'] += int(doc['counts']['unparsed'])
    # Mechanical Pareto over every full blind agere run.
    mech = Counter(); mech_opps = sum(m['answerable'] for m in agere_blind); unans_opps = sum(m['unanswerable'] for m in agere_blind)
    for m in agere_blind:
        for k, n in m['mechanical'].items(): mech[fmea['mechanical_modes'].get(k) or k] += n
    replay_rows = [r for m in agere_blind for r in by_file[m['file']]['rows']]
    replay = control_replay(replay_rows) if replay_rows else {}
    # p-charts
    strict_chart = p_chart([{'label': m['set'], 'set': m['set'], 'file': m['file'], 'defects': m['strict_defects'], 'opportunities': m['answerable']} for m in first_runs])
    strict_chart_all = p_chart([{'label': f"{m['set']} {m['started'][5:16]}", 'set': m['set'], 'file': m['file'], 'defects': m['strict_defects'],
                                 'opportunities': m['answerable']} for m in agere_blind])
    adj_chart = p_chart([{'label': m['set'], 'set': m['set'], 'file': m['file'], 'defects': m['engine_errors'], 'opportunities': m['answerable']} for m in latest_adj])
    # Trend by blind set (first and latest full agere run; latest adjudication; frontier on the same set).
    sets = []
    for label in dict.fromkeys(m['set'] for m in agere_blind):
        mine = [m for m in agere_blind if m['set'] == label]
        adjudicated = [m for m in mine if m['adjudicated']]
        frontier = [m for m in metrics if m['system'] == 'frontier_sql' and m['set'] == label and m['full']]
        sets.append({'set': label, 'runs': len(mine), 'first': mine[0], 'latest': mine[-1], 'adjudicated': adjudicated[-1] if adjudicated else None,
                     'frontier': frontier[-1] if frontier else None})
    # Cost per question by route (latest full run of each blind set: the pipeline as it now stands).
    from bench.score import recorded_cost
    routes = defaultdict(lambda: {'questions': 0, 'known_usd': 0.0, 'unknown_cost': 0, 'answerable': 0, 'correct_answerable': 0})
    latest_runs = sorted({m['set']: m for m in agere_blind}.values(), key=lambda m: m['started'])  # latest full run of each blind set
    for r in (row for m in latest_runs for row in by_file[m['file']]['rows']):
        x = routes[route(r)]; x['questions'] += 1
        c = recorded_cost(r)
        if c is None: x['unknown_cost'] += 1
        else: x['known_usd'] += c
        if answerable(r): x['answerable'] += 1; x['correct_answerable'] += bool(r['score'].get('correct'))
    for x in routes.values():
        priced = x['questions'] - x['unknown_cost']
        x['usd_per_question'] = x['known_usd'] / priced if priced else None
        x['strict_accuracy'] = x['correct_answerable'] / x['answerable'] if x['answerable'] else None
    # Smoke (development + challenge regression) and acceptance.
    dev = [m for m in metrics if m['system'] == 'agere' and m['set'] == 'C+D' and m['full']]
    smoke = {'latest': dev[-1], 'threshold': dev[-1]['questions'] - 1, 'passed': dev[-1]['correct'] >= dev[-1]['questions'] - 1,  # bench/smoke.py: 49 of 50
             'note': 'Development + challenge regression from the latest full C+D run. bench/smoke.py known-answer probe results are printed, not saved, so they are not shown.'} if dev else None
    acceptance = None
    files = sorted((Path(results) / 'acceptance').glob('*.json'))
    if files:
        try:
            cases = json.loads(files[-1].read_text())
            by_op = defaultdict(lambda: [0, 0])
            for c in cases: by_op[c.get('expect_operation')][0] += bool(c.get('pass')); by_op[c.get('expect_operation')][1] += 1
            acceptance = {'file': f'acceptance/{files[-1].name}', 'passed': sum(bool(c.get('pass')) for c in cases), 'total': len(cases),
                          'by_operation': {k: {'passed': v[0], 'total': v[1]} for k, v in sorted(by_op.items())},
                          'routes': dict(Counter(c.get('route') or 'error' for c in cases)),
                          'known_usd': sum(c.get('usd') or 0 for c in cases), 'runs_recorded': len(files)}
        except (OSError, json.JSONDecodeError, AttributeError): acceptance = None
    # Overall DPMO: first (blind) attempt per set, every full blind run, and the latest adjudication per set.
    def total(ms, key):
        d = sum(m[key] for m in ms); o = sum(m['answerable'] for m in ms)
        return {'defects': d, 'opportunities': o, 'dpmo': dpmo(d, o), 'sigma': sigma_level(dpmo(d, o)), 'ci': wilson(d, o), 'runs': len(ms),
                'sets': list(dict.fromkeys(m['set'] for m in ms))}
    totals = {'strict_first_attempt': total(first_runs, 'strict_defects'), 'strict_all_runs': total(agere_blind, 'strict_defects'),
              'adjudicated': total(latest_adj, 'engine_errors')}
    evidence = {'adjudicated_counts': dict(adj_counter), 'adjudicated_examples': {k: v for k, v in adj_examples.items()},
                'adjudicated_opportunities': adj_opps, 'adjudicated_runs': [m['file'] for m in latest_adj], 'hosted_engine_errors': hosted_engine,
                'mechanical_counts': dict(mech), 'mechanical_opportunities': mech_opps, 'unanswerable_opportunities': unans_opps,
                'replay': replay, 'acceptance': acceptance}
    return {'schema': 'agere-defects/1', 'runs': metrics, 'blind_sets': sets, 'totals': totals,
            'p_chart_strict': strict_chart, 'p_chart_strict_all_runs': strict_chart_all, 'p_chart_adjudicated': adj_chart,
            'pareto_adjudicated': pareto(adj_counter, fmea), 'pareto_mechanical': pareto(mech, fmea),
            'routes': dict(routes), 'smoke': smoke, 'acceptance': acceptance, 'evidence': evidence,
            'adjudications': len(adjudications)}


# ---- FMEA evidence and ranking ------------------------------------------------------------------------------
def score_fmea(fmea, evidence):
    """Fill evidence, occurrence and RPN for every mode from the measured evidence; rank by residual RPN."""
    fmea = copy.deepcopy(fmea)
    bands = fmea['scales']['occurrence_bands']
    mech_mode_to_classes = defaultdict(list)
    for cls, mode in fmea['mechanical_modes'].items():
        if mode: mech_mode_to_classes[mode].append(cls)
    for m in fmea['modes']:
        rule, ev, rates = m.get('evidence_rule') or {}, {}, []
        if rule.get('adjudicated_pattern') or m['id'] in fmea['category_modes'].values():
            n = evidence['adjudicated_counts'].get(m['id'], 0)
            ev['adjudicated'] = {'count': n, 'opportunities': evidence['adjudicated_opportunities'],
                                 'examples': evidence['adjudicated_examples'].get(m['id'], [])[:8]}
            if evidence['adjudicated_opportunities']: rates.append((n / evidence['adjudicated_opportunities'], n))
        if rule.get('derived') == 'adjudicated_engine_errors_on_hosted_verified_sql':
            n = evidence['hosted_engine_errors']
            ev['adjudicated_hosted_route'] = {'count': n, 'opportunities': evidence['adjudicated_opportunities']}
            if evidence['adjudicated_opportunities']: rates.append((n / evidence['adjudicated_opportunities'], n))
        for cls in rule.get('mechanical') or []:
            if cls == 'acceptance_failure':
                acc = evidence.get('acceptance')
                if acc:
                    n = acc['total'] - acc['passed']
                    ev['acceptance'] = {'count': n, 'opportunities': acc['total'], 'file': acc['file']}
                    rates.append((n / acc['total'] if acc['total'] else 0, n))
                continue
            n = evidence['mechanical_counts'].get(m['id'], 0)
            opps = evidence['unanswerable_opportunities'] if cls == 'unsupported_answered' else evidence['mechanical_opportunities']
            ev['mechanical'] = {'count': n, 'opportunities': opps, 'classes': mech_mode_to_classes.get(m['id'], [])}
            if opps: rates.append((n / opps, n))
        if rule.get('replay_control'):
            h = (evidence.get('replay') or {}).get(rule['replay_control']) or {}
            n = h.get('on_wrong', 0)
            ev['control_replay'] = {'control': rule['replay_control'], 'fired_on_wrong_answers': n, 'fired_on_correct_answers': h.get('on_correct', 0),
                                    'opportunities': evidence['mechanical_opportunities']}
            if evidence['mechanical_opportunities']: rates.append((n / evidence['mechanical_opportunities'], n))
        if rates:
            rate, count = max(rates)
            m['occurrence'] = occurrence_rating(rate, max(n for _, n in rates), bands)
            m['occurrence_basis'] = f'measured: {count} per {round(count / rate) if rate else "n"} opportunities' if count else \
                'measured: not observed in the recorded opportunities'
        else:
            m['occurrence'] = m.get('occurrence_judgment') or 2
            m['occurrence_basis'] = 'judgment (no benchmark measures this mode)'
        m['evidence'] = ev
        m['rpn'] = m['severity'] * m['occurrence'] * m['detection']
        m['rpn_before'] = m['severity'] * m['occurrence'] * m['detection_before']
    for rank, m in enumerate(sorted(fmea['modes'], key=lambda x: (-x['rpn'], -x['rpn_before'], x['id'])), 1): m['rank'] = rank
    fmea['modes'].sort(key=lambda x: x['rank'])
    return fmea


def validate_fmea(fmea):
    """Schema problems (empty list = valid)."""
    problems = []
    for key in ('schema', 'scales', 'modes', 'classification_order', 'category_modes', 'mechanical_modes'):
        if key not in fmea: problems.append(f'missing {key}')
    ids = [m.get('id') for m in fmea.get('modes', [])]
    if len(ids) != len(set(ids)): problems.append('duplicate mode ids')
    stages = {'question_parse', 'routing', 'population', 'definitions', 'sql_generation', 'verification', 'execution',
              'answer_rendering', 'follow_ups', 'learning_reuse'}
    for m in fmea.get('modes', []):
        for key in ('id', 'stage', 'failure_mode', 'effect', 'cause', 'severity', 'detection', 'detection_before', 'controls_existing', 'controls_added', 'evidence_rule'):
            if key not in m: problems.append(f"{m.get('id')}: missing {key}")
        if m.get('stage') not in stages: problems.append(f"{m.get('id')}: unknown stage {m.get('stage')}")
        for key in ('severity', 'detection', 'detection_before', 'occurrence'):
            if key in m and not (isinstance(m[key], int) and 1 <= m[key] <= 10): problems.append(f"{m.get('id')}: {key} must be an integer 1-10")
        if 'rpn' in m and m['rpn'] != m['severity'] * m['occurrence'] * m['detection']: problems.append(f"{m['id']}: rpn != S x O x D")
        if m.get('detection', 0) > m.get('detection_before', 0): problems.append(f"{m.get('id')}: detection worse after controls")
    missing_stage = stages - {m.get('stage') for m in fmea.get('modes', [])}
    if missing_stage: problems.append(f'stages without failure modes: {sorted(missing_stage)}')
    for i in fmea.get('classification_order', []):
        if i not in ids: problems.append(f'classification_order names unknown mode {i}')
    return problems


def fmea_table(fmea):
    lines = ['| Rank | ID | Stage | Failure mode | S | O | D | RPN | RPN before | Evidence | Controls added |', '|---:|---|---|---|---:|---:|---:|---:|---:|---|---|']
    for m in fmea['modes']:
        ev = m.get('evidence') or {}
        parts = []
        if 'adjudicated' in ev: parts.append(f"{ev['adjudicated']['count']} adjudicated / {ev['adjudicated']['opportunities']}")
        if 'adjudicated_hosted_route' in ev: parts.append(f"{ev['adjudicated_hosted_route']['count']} hosted-route engine errors / {ev['adjudicated_hosted_route']['opportunities']}")
        if 'mechanical' in ev: parts.append(f"{ev['mechanical']['count']} scored / {ev['mechanical']['opportunities']}")
        if 'acceptance' in ev: parts.append(f"{ev['acceptance']['count']} acceptance fails / {ev['acceptance']['opportunities']}")
        if 'control_replay' in ev: parts.append(f"{ev['control_replay']['control']} fired on {ev['control_replay']['fired_on_wrong_answers']} wrong / {ev['control_replay']['fired_on_correct_answers']} right")
        if not parts: parts.append(m.get('occurrence_basis', ''))
        lines.append(f"| {m['rank']} | {m['id']} | {m['stage']} | {m['failure_mode']} | {m['severity']} | {m['occurrence']} | {m['detection']} | {m['rpn']} | {m['rpn_before']} | "
                     f"{'; '.join(parts)} | {', '.join(m['controls_added']) or '-'} |")
    return '\n'.join(lines)


def write_fmea(fmea, path=FMEA_PATH, md=FMEA_MD):
    Path(path).write_text(json.dumps(fmea, indent=1) + '\n')
    if Path(md).exists():
        text = Path(md).read_text()
        start, end = '<!-- fmea-table:start -->', '<!-- fmea-table:end -->'
        if start in text and end in text:
            head, rest = text.split(start, 1); _, tail = rest.split(end, 1)
            Path(md).write_text(head + start + '\n' + fmea_table(fmea) + '\n' + end + tail)


def _pct(x): return '-' if x is None else f'{100 * x:.1f}%'


def _f(x, spec): return '-' if x is None else format(x, spec)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--results', default=str(RESULTS)); p.add_argument('--json', action='store_true'); p.add_argument('--update-fmea', action='store_true')
    a = p.parse_args(argv)
    fmea = load_fmea()
    rep = report(Path(a.results), fmea)
    if a.update_fmea:
        scored = score_fmea(fmea, rep['evidence'])
        problems = validate_fmea(scored)
        if problems: print('FMEA problems:', problems); return 1
        write_fmea(scored)
    if a.json:
        print(json.dumps({k: v for k, v in rep.items() if k != 'runs'} | {'runs': rep['runs']}, indent=1, default=str)); return 0
    print('run                                   set  answerable  strict defects (95% CI)      DPMO      sigma   adjudicated engine errors (95% CI)  DPMO     sigma')
    for m in rep['runs']:
        if m['system'] != 'agere' or not m['full'] or m['split'] != 'blind': continue
        line = (f"{m['file']:38s} {m['set']:4s} {m['answerable']:10d}  {m['strict_defects']:3d} = {_pct(m['strict_rate']):6s} ({_pct(m['strict_ci'][0])}-{_pct(m['strict_ci'][1])})"
                f"  {_f(m['strict_dpmo'], '9.0f')}  {_f(m['strict_sigma'], '.2f')}")
        if m['adjudicated']:
            line += (f"   {m['engine_errors']:3d} = {_pct(m['engine_rate']):6s} ({_pct(m['engine_ci'][0])}-{_pct(m['engine_ci'][1])})"
                     f"  {_f(m['engine_dpmo'], '8.0f')}  {_f(m['engine_sigma'], '.2f')}")
        print(line)
    t = rep['totals']
    for k in ('strict_first_attempt', 'strict_all_runs', 'adjudicated'):
        x = t[k]
        if x['opportunities']:
            print(f"ALL {k:22s} {x['defects']}/{x['opportunities']} over {x['runs']} runs  DPMO {_f(x['dpmo'], '.0f')}  sigma {_f(x['sigma'], '.2f')}  95% CI {_pct(x['ci'][0])}-{_pct(x['ci'][1])}")
    print('\np-chart (strict, first full run of each blind set): center', None if rep['p_chart_strict']['center'] is None else f"{rep['p_chart_strict']['center']:.3f}")
    for pt in rep['p_chart_strict']['points']:
        print(f"  {pt['label']:20s} {pt['defects']:3d}/{pt['opportunities']:3d}  p={_f(pt.get('rate'), '.3f')}  limits {_f(pt.get('lcl'), '.3f')}-{_f(pt.get('ucl'), '.3f')} {pt.get('signal') or ''}")
    print('\nPareto (adjudicated agere failures -> FMEA):')
    for x in rep['pareto_adjudicated']: print(f"  {x['mode']:14s} {x['count']:3d}  {_pct(x['cumulative']):>6s}  {x['label'][:90]}")
    print('\nPareto (scored failures, all full blind runs, no adjudication):')
    for x in rep['pareto_mechanical']: print(f"  {x['mode']:26s} {x['count']:3d}  {_pct(x['cumulative']):>6s}")
    print('\nanswer-level control replay on preserved blind answers:', json.dumps(rep['evidence']['replay']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
