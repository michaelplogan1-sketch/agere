"""Metric-first measurement on SAVED receipts only (no API calls): coverage, oracle precision, and the stricter free-form
acceptance policy replayed on the hosted ladder's saved answers.

For every benchmark question with a saved hosted-ladder receipt (bench/results/agere/*.jsonl, latest hosted_verified_sql
attempt per question; the set behind the ladder's 0.60 truth precision), oracle.json states the governed metric and slots
the metric router SHOULD choose (null = no governed metric fits: the question stays on the free-form route). The chosen
metric is compiled by the production code (metric_router.validate_slots / compile_metric) and executed READ ONLY against
the stack's semantic layer; its result is compared with the benchmark reference value-by-value (column names ignored; the
same truth_match rule the ladder was scored with).

Usage: .venv/bin/python tools/metric_first/replay.py [stack] [results_dir] [out.json]
"""
import json, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bench.common import load_questions  # noqa: E402
from clinical_core.agere.clinical import metric_router, metrics  # noqa: E402

STACK = ROOT / 'artifacts/agere-healthcare-v123/stack'
RESULTS = Path('/Users/mikel/clinical-insights-local/bench/results/agere')
ORACLE = Path(__file__).with_name('oracle.json')


def signature(rows):
    norm = lambda v: round(float(v), 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    return json.dumps(sorted(sorted(json.dumps(norm(v), default=str) for v in r) for r in rows))


def truth_match(question, expected, rows):
    kind, tol = question['answer_type'], question.get('tolerance', 0)
    if kind == 'unsupported': return False
    if kind in ('count', 'value'):
        v = rows[0][0] if len(rows) == 1 and len(rows[0]) == 1 else None
        e = expected.get(kind)
        return isinstance(v, (int, float)) and isinstance(e, (int, float)) and abs(v - e) <= max(tol, 1e-6 if kind == 'value' else 0)
    if kind == 'patient_set':
        return bool(rows) and {r[0] for r in rows} == set(expected.get('patient_ids') or [])
    return signature(rows) == signature([list(r.values()) for r in expected.get('table') or []])


def ladder_rows(answer):
    kind = answer.get('answer_type')
    if kind in ('count', 'value'): return [[answer.get(kind)]]
    if kind == 'patient_set': return [[i] for i in answer.get('patient_ids') or []]
    if kind == 'table': return [list(r.values()) for r in answer.get('table') or []]
    return []


def saved_ladder(results=RESULTS):
    qs = {q['id']: q for q in load_questions()}
    latest = {}
    for f in sorted(Path(results).glob('*.jsonl')):
        for line in open(f):
            try: r = json.loads(line)
            except ValueError: continue
            pr = ((r.get('response') or {}).get('planner_receipt') or {})
            if pr.get('route') == 'hosted_verified_sql' and r['id'] in qs and r.get('expected') and r.get('answer') and \
                    (r['id'] not in latest or r['started_at'] > latest[r['id']]['started_at']):
                latest[r['id']] = r
    return qs, latest


def run_ro(stack, query):
    db = sqlite3.connect(f'file:{Path(stack) / "semantic.sqlite3"}?mode=ro', uri=True)
    try:
        return [list(r) for r in db.execute(query).fetchall()]
    finally:
        db.close()


def compute(entry, question, stack):
    """-> (rows | None, info) for one oracle entry."""
    lib = metric_router.routable()
    mid = entry['metric']
    m = lib[mid]
    slots = metric_router.validate_slots(m, entry.get('slots') or {})
    node = metric_router.compile_metric(mid, m, slots, question['as_of'], question['question'])
    info = {'metric': mid, 'version': m['version'], 'unaccounted_numbers': metric_router.unaccounted_numbers(question['question'], m, slots, question['as_of'])}
    if node['operation'] == 'sql':
        info['query'] = node['parameters']['query']
        rows = run_ro(stack, node['parameters']['query'])
        return rows, info
    from clinical_core.agere.clinical import measures
    p = node['parameters']
    report = measures.evaluate(stack, p['measure'], period=p['period'], group_by=p['group_by'], as_of=question['as_of'])
    rate = ((report.get('overall') or {}).get('rate'))
    return [[rate]], {**info, 'measure_report': {k: report.get(k) for k in ('overall',)}}


_UNIVERSE = {}


def acceptance_replay(question, record, stack):
    """The stricter free-form policy on the ladder's saved final SQL (metric lint path; v2 independent agreement was not
    recorded in these receipts, so only the lint path can promote an answer to verified)."""
    hs = record['response']['planner_receipt']['hosted_sql']
    final = [a for a in hs['attempts'] if a.get('sql') and not a.get('error')]
    sql = final[-1]['sql'] if final else None
    if str(stack) not in _UNIVERSE:
        db = sqlite3.connect(f'file:{Path(stack) / "semantic.sqlite3"}?mode=ro', uri=True)
        try:
            _UNIVERSE[str(stack)] = {d: metrics.universe(db, d) for d in metrics.DIMENSIONS}
        finally:
            db.close()
    return metric_router.acceptance(question['question'], sql, hs, question['as_of'], source=_UNIVERSE[str(stack)])


def main(stack=STACK, results=RESULTS, out=None):
    qs, latest = saved_ladder(results)
    oracle = json.loads(ORACLE.read_text())['oracle']
    rows_out, n = [], len(latest)
    for qid, rec in sorted(latest.items()):
        q = qs[qid]
        ladder_ok = truth_match(q, rec['expected'], ladder_rows(rec['answer']))
        entry = oracle.get(qid)
        row = {'id': qid, 'answer_type': q['answer_type'], 'ladder_correct': ladder_ok,
               'ladder_agreement': rec['response']['planner_receipt']['hosted_sql'].get('agreement')}
        if entry and entry.get('metric'):
            try:
                rows, info = compute(entry, q, stack)
                row.update(covered=True, metric_correct=truth_match(q, rec['expected'], rows), **info,
                           metric_result=json.dumps(rows[:6], default=str)[:300])
            except (ValueError, KeyError, sqlite3.Error) as exc:
                row.update(covered=True, metric_correct=False, metric=entry['metric'], error=str(exc)[:300])
        else:
            row.update(covered=False, reason=(entry or {}).get('why'))
        try:
            row['acceptance'] = acceptance_replay(q, rec, stack)
        except Exception as exc:  # noqa: BLE001 - replay bookkeeping
            row['acceptance'] = {'status': 'error', 'basis': str(exc)[:200]}
        rows_out.append(row)
    cov = [r for r in rows_out if r['covered']]
    ok = sum(r['metric_correct'] for r in cov)
    lad_cov = sum(r['ladder_correct'] for r in cov)
    unc = [r for r in rows_out if not r['covered']]
    lad_unc = sum(r['ladder_correct'] for r in unc)
    ver = [r for r in unc if r['acceptance']['status'] == 'verified']
    low = [r for r in unc if r['acceptance']['status'] == 'low_confidence']
    summary = {
        'questions': n, 'ladder_correct': sum(r['ladder_correct'] for r in rows_out),
        'ladder_precision': round(sum(r['ladder_correct'] for r in rows_out) / n, 3),
        'covered_by_governed_metric': len(cov), 'coverage': round(len(cov) / n, 3),
        'oracle_metric_correct_on_covered': ok, 'oracle_precision_on_covered': round(ok / len(cov), 3) if cov else None,
        'ladder_precision_on_covered': round(lad_cov / len(cov), 3) if cov else None,
        'uncovered': len(unc), 'ladder_precision_on_uncovered': round(lad_unc / len(unc), 3) if unc else None,
        'uncovered_ladder_verified_by_lint': len(ver), 'verified_correct': sum(r['ladder_correct'] for r in ver),
        'uncovered_ladder_low_confidence': len(low), 'low_confidence_correct': sum(r['ladder_correct'] for r in low),
    }
    asserted = ok + sum(r['ladder_correct'] for r in ver)
    summary['confident_answers'] = len(cov) + len(ver)
    summary['confident_precision'] = round(asserted / summary['confident_answers'], 3) if summary['confident_answers'] else None
    summary['confident_coverage'] = round(summary['confident_answers'] / n, 3)
    report = {'schema': 'agere-metric-first-replay/1', 'stack': str(stack), 'summary': summary, 'rows': rows_out}
    if out: Path(out).write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(summary, indent=1))
    return report


if __name__ == '__main__':
    a = sys.argv[1:]
    main(a[0] if a else STACK, a[1] if len(a) > 1 else RESULTS, a[2] if len(a) > 2 else None)
