"""Measure the metric layer on the adjudicated hosted-route answers of blind sets M, N, O (saved receipts only).

For every answer that took the hosted verified-SQL route in the latest adjudicated full run of M, N and O (the
scope of docs/agere/verification_v2_replay.json: engine errors = 'wrong', bench-correct = 'right'):
  1. resolve the question to a metric (deterministic match rules only) and run metrics.lint on the DELIVERED SQL,
     with value-set universes read from the live stack's semantic layer (read-only);
  2. for the wrong answers, execute the metric's canonical SQL template read-only and compare it with the saved
     reference answer (values compared without regard to column names; question tolerance).
Also reports value-set coverage of every templated metric against the received data and the concept library.
No model is called; nothing is written except --out.

Usage: .venv/bin/python tools/quality/metric_layer_replay.py [--results DIR] [--stack DIR] [--out FILE]
"""
import argparse, glob, json, math, sqlite3, sys, time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.quality import defects as D  # noqa: E402
from clinical_core.agere.clinical import metrics  # noqa: E402

LIVE = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')
RESULTS = Path('/Users/mikel/clinical-insights-local/bench/results')
UNCAUGHT_BY_V2 = None  # read from docs/agere/verification_v2_replay.json when present


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _close(a, b, tol):
    if _num(a) and _num(b): return abs(a - b) <= max(tol, 1e-9)
    return a == b


def template_answer(db, sql, returns):
    cur = db.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchmany(5001)
    if returns == 'patient_set' or (cols == ['patient_id']):
        ids = sorted({r[0] for r in rows if r[0]})
        return {'answer_type': 'patient_set', 'patient_ids': ids, 'count': len(ids)}
    if len(rows) == 1 and len(cols) == 1:
        v = rows[0][0]
        return {'answer_type': 'count' if returns == 'count' else 'value', 'count': v if returns == 'count' else None, 'value': v}
    return {'answer_type': 'table', 'table': [dict(zip(cols, r)) for r in rows]}


def reproduces(expected, got, tol):
    """Same answer, column names ignored: scalar within tolerance, same patient set, or same multiset of row values."""
    kind = expected.get('answer_type')
    if kind in ('count', 'value'):
        want = expected.get('count') if kind == 'count' else expected.get('value')
        have = got.get('count') if got.get('count') is not None else got.get('value')
        if got.get('answer_type') == 'patient_set': have = got['count']
        return _close(have, want, tol)
    if kind == 'patient_set':
        return got.get('answer_type') == 'patient_set' and set(got['patient_ids']) == set(expected.get('patient_ids') or [])
    if kind == 'table':
        if got.get('answer_type') != 'table': return False
        exp = [list(r.values()) for r in expected.get('table') or []]
        act = [list(r.values()) for r in got['table']]
        if len(exp) != len(act) or any(len(e) != len(a) for e, a in zip(exp, act)): return False
        key = lambda r: tuple(str(x) for x in r if not _num(x))
        remaining = sorted(exp, key=key)
        for row in sorted(act, key=key):
            hit = next((i for i, e in enumerate(remaining) if all(_close(x, y, tol) for x, y in zip(row, e))), None)
            if hit is None: return False
            remaining.pop(hit)
        return True
    return False


def run(results, stack, out):
    started = time.time()
    runs = D.load_runs(results); adj = D.load_adjudications(results)
    ms = [D.run_metrics(r, adj.get(r['file'])) for r in runs]
    latest = {m['set']: m for m in ms if m['system'] == 'agere' and m['split'] == 'blind' and m['full'] and m['adjudicated'] and m['set'] in 'MNO'}
    by_file = {r['file']: r for r in runs}
    qs = {q['id']: q for f in glob.glob(str(ROOT / 'bench' / 'questions' / '*.json')) for q in json.load(open(f))}
    v2 = {}
    v2_file = ROOT / 'docs' / 'agere' / 'verification_v2_replay.json'
    if v2_file.exists():
        v2 = {i['id']: i for i in json.loads(v2_file.read_text())['items']}
    db = sqlite3.connect(f'file:{stack / "semantic.sqlite3"}?mode=ro', uri=True)
    items = []
    for s, m in sorted(latest.items()):
        cats = {i['id']: i for i in adj[m['file']].get('items') or []}
        for row in by_file[m['file']]['rows']:
            if D.route(row) != 'hosted_verified_sql' or not D.answerable(row): continue
            cat = (cats.get(row['id']) or {}).get('category')
            label = 'wrong' if cat == 'engine_error' else 'right' if row['score'].get('correct') else (cat or 'unadjudicated')
            if label not in ('wrong', 'right'): continue
            q = row['question']; as_of = row.get('as_of') or '2029-08-15'
            sql = (row.get('answer') or {}).get('query') or ''
            r = metrics.resolve({'question': q, 'as_of': as_of})
            item = {'set': s, 'id': row['id'], 'label': label, 'question': q[:200],
                    'v2_caught': bool((v2.get(row['id']) or {}).get('caught')), 'metric': r and r['id'], 'variant': r and r.get('variant')}
            if r:
                healed_sql, repairs = metrics.heal(q, sql, as_of, resolution=r)
                findings = metrics.lint(q, healed_sql, as_of, source=stack, resolution=r)
                item['healed'] = [h['message'] for h in repairs]
                item['lint'] = [{'rule': f['rule'], 'message': f['message']} for f in findings]
                if repairs:  # does the mechanical repair change the delivered answer, and does it reach the reference?
                    try:
                        before = template_answer(db, sql, (row.get('answer') or {}).get('answer_type') or 'value')
                        after = template_answer(db, healed_sql, (row.get('answer') or {}).get('answer_type') or 'value')
                        item['heal_changes_result'] = before != after
                        item['healed_reproduces_reference'] = reproduces(row.get('expected') or {}, after, 0.051)
                    except sqlite3.Error as exc:
                        item['heal_error'] = str(exc)[:200]
                if label == 'wrong' and r.get('sql'):
                    try:
                        got = template_answer(db, r['sql'], r['metric']['output']['returns'])
                        tol = (qs.get(row['id']) or {}).get('tolerance', 0) or 0
                        item['template_reproduces_reference'] = reproduces(row.get('expected') or {}, got, max(tol, 0.051))
                        item['template_result'] = (got.get('value') if got['answer_type'] in ('count', 'value') else
                                                   got.get('count') if got['answer_type'] == 'patient_set' else f"{len(got['table'])} rows")
                    except sqlite3.Error as exc:
                        item['template_error'] = str(exc)[:200]
                    exp = row.get('expected') or {}
                    item['reference'] = exp.get('count') if exp.get('answer_type') == 'count' else exp.get('value') if exp.get('answer_type') == 'value' else \
                        len(exp.get('patient_ids') or []) if exp.get('answer_type') == 'patient_set' else f"{len(exp.get('table') or [])} rows"
            else:
                item['lint'] = []; item['healed'] = []
            items.append(item)
            print(f"{s} {row['id']} {label:<5} v2={'Y' if item['v2_caught'] else '-'} metric={item['metric'] or '-':<34} "
                  f"healed={len(item['healed'])} lint={[f['rule'] for f in item['lint']]} reproduces={item.get('template_reproduces_reference')}", flush=True)
    wrong = [i for i in items if i['label'] == 'wrong']; right = [i for i in items if i['label'] == 'right']
    uncaught = [i for i in wrong if not i['v2_caught']]
    flagged = lambda xs: [i for i in xs if i['lint'] or i['healed']]
    blocking = lambda xs: [i for i in xs if i['lint']]
    summary = {
        'wrong': len(wrong), 'right': len(right), 'uncaught_by_v2': len(uncaught),
        'uncaught_resolved_to_metric': sum(1 for i in uncaught if i['metric']),
        'uncaught_flagged_by_metric_lint': len(flagged(uncaught)),
        'uncaught_template_reproduces_reference': sum(1 for i in uncaught if i.get('template_reproduces_reference')),
        'all_wrong_flagged': len(flagged(wrong)),
        'right_resolved_to_metric': sum(1 for i in right if i['metric']),
        'right_false_alarms': len(flagged(right)),
        'right_false_alarms_lint_after_heal': len(blocking(right)),
        'right_healed_result_unchanged': sum(1 for i in right if i['healed'] and not i.get('heal_changes_result')),
        'uncaught_flagged_lint_after_heal': len(blocking(uncaught)),
        'uncaught_healed_to_reference': sum(1 for i in uncaught if i.get('healed_reproduces_reference')),
        'caught_after_v2_plus_metric_lint': sum(1 for i in wrong if i['v2_caught'] or i['lint'] or i['healed']),
        'flagged_by_rule_on_uncaught': dict(Counter(f['rule'] for i in uncaught for f in {x['rule']: x for x in i['lint'] + [{'rule': 'ML-WINDOW (healed)'} for _ in i['healed']]}.values())),
        'false_alarm_by_rule': dict(Counter(f['rule'] for i in right for f in {x['rule']: x for x in i['lint'] + [{'rule': 'ML-WINDOW (healed)'} for _ in i['healed']]}.values())),
    }
    lib = metrics.library()
    report = {'schema': 'metric-layer-replay/1', 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'seconds': None, 'metric_layer': metrics.VERSION,
              'scope': 'latest adjudicated full run of blind sets M, N, O; hosted_verified_sql route; delivered SQL linted against the '
                       'resolved metric (deterministic match, value-set universes from the live semantic layer, read-only); metric '
                       'template SQL executed read-only for wrong answers and compared with the saved reference; no model called',
              'caveat': 'in-sample: the metrics behind these failures were authored after inspecting them; false alarms on the 59 right '
                        'answers and reproduction of the reference are the informative numbers',
              'metrics': {'total': len(lib), 'templated': sum(1 for m in lib.values() if m.get('sql_template')),
                          'by_domain': dict(Counter(m['domain'] for m in lib.values())),
                          'cited': sum(1 for m in lib.values() if m['status'] == 'cited'),
                          'internal': sum(1 for m in lib.values() if m['status'] == 'internal')},
              'summary': summary, 'items': items}
    dicts = {}
    norm = ROOT / 'config' / 'agere' / 'normalization'
    for dim, f in (('visit.type', 'encounters'), ('measurement.name', 'labs'), ('condition.label', 'conditions')):
        if (norm / f'{f}.json').exists(): dicts[dim] = json.loads((norm / f'{f}.json').read_text()).get('entries') or {}
    report['value_set_coverage'] = metrics.coverage_report(stack, dictionaries=dicts)
    report['seconds'] = round(time.time() - started, 1)
    Path(out).write_text(json.dumps(report, indent=1, default=str) + '\n')
    print(json.dumps(summary, indent=1))
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--results', default=str(RESULTS))
    ap.add_argument('--stack', default=str(LIVE))
    ap.add_argument('--out', default=str(ROOT / 'docs' / 'agere' / 'metric_layer_replay.json'))
    a = ap.parse_args()
    run(Path(a.results), Path(a.stack), a.out)
