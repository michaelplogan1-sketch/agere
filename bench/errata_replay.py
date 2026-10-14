"""Re-score SAVED Agere receipts with and without bench/errata.json (no model or API calls, no warehouse access).

Two measurements, both on the hosted-ladder receipt set used by tools/metric_first/replay.py (latest hosted_verified_sql
attempt per question in bench/results/agere/*.jsonl):
  ladder  the ladder's own saved answers, compared value-by-value with column names ignored (the replay's truth_match);
  oracle  governed-metric results for covered questions, from a JSON file {id: {"rows": [[...]], "metric": id}}
          produced by the metric-first replay (optional).

Errata scoring (convention per bench/errata.py): canonical answer, or an accepted alternative whose definition the
answer states; value/table questions without a stated precision match within half a unit of the reference's precision.
For the ladder the stated definition is the answer's own interpretation text; for the oracle it is the governed metric's
definition (config/agere/metrics/*.json).

Usage: python bench/errata_replay.py [--results DIR] [--metric-rows FILE] [--errata FILE] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.common import ROOT, finite_number, load_questions
from bench.errata import (ERRATA_PATH, answer_from, definition_text, load_errata, part_expected, source_of,
                          states_definition, with_precision)

METRICS_DIR = ROOT.parent / 'config/agere/metrics'


def saved_ladder(results, questions):
    latest = {}
    for path in sorted(Path(results).glob('*.jsonl')):
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            receipt = (row.get('response') or {}).get('planner_receipt') or {}
            if (receipt.get('route') == 'hosted_verified_sql' and row.get('id') in questions and row.get('expected')
                    and row.get('answer') and (row['id'] not in latest or row['started_at'] > latest[row['id']]['started_at'])):
                latest[row['id']] = row
    return latest


def answer_rows(answer):
    kind = answer.get('answer_type')
    if kind in ('count', 'value'):
        return [[answer.get(kind)]]
    if kind == 'patient_set':
        return [[i] for i in answer.get('patient_ids') or []]
    if kind == 'table':
        return [list(r.values()) for r in answer.get('table') or []]
    return []


def _num(v):
    return finite_number(v) and not isinstance(v, bool)


def _row_match(actual, expected, tol):
    if len(actual) != len(expected):
        return False
    a_str = sorted(json.dumps(v, default=str) for v in actual if not _num(v))
    e_str = sorted(json.dumps(v, default=str) for v in expected if not _num(v))
    a_num = sorted(v for v in actual if _num(v))
    e_num = sorted(v for v in expected if _num(v))
    return a_str == e_str and len(a_num) == len(e_num) and all(abs(x - y) <= tol for x, y in zip(a_num, e_num))


def signature(rows):
    norm = lambda v: round(float(v), 4) if _num(v) else v
    return json.dumps(sorted(sorted(json.dumps(norm(v), default=str) for v in r) for r in rows))


def rows_match(question, expected, rows, tolerance=0.0):
    """The metric-first replay's truth_match (column names ignored), with a numeric tolerance."""
    kind = question['answer_type']
    if kind == 'unsupported' or expected is None:
        return False
    tol = max(tolerance, question.get('tolerance', 0) or 0)
    if kind in ('count', 'value'):
        v = rows[0][0] if len(rows) == 1 and len(rows[0]) == 1 else None
        e = expected.get(kind)
        return _num(v) and _num(e) and abs(v - e) <= max(tol, 1e-6 if kind == 'value' else 0)
    if kind == 'patient_set':
        return bool(rows) and {r[0] for r in rows} == set(expected.get('patient_ids') or [])
    table = [list(r.values()) for r in expected.get('table') or []]
    if tolerance <= 0:
        return signature(rows) == signature(table)
    if len(rows) != len(table):
        return False
    remaining = list(table)
    tol = max(tol, 1e-4)
    for row in rows:
        hit = next((i for i, e in enumerate(remaining) if _row_match(row, e, tol)), None)
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def errata_match(question, expected, rows, entry, release, stated_text):
    """-> (correct, how) under errata scoring."""
    if entry is None:
        return rows_match(question, expected, rows), 'none'
    q = with_precision(question, entry)
    tol = (q.get('tolerance', 0) or 0) if 'precision' in entry else 0.0
    canon = part_expected(question, entry['canonical'], release, expected, canonical=True)
    if canon is None:
        return rows_match(question, expected, rows), 'unavailable_for_release'
    if rows_match(q, canon, rows, tol):
        return True, 'canonical'
    for alt in entry.get('alternatives', []):
        alt_exp = part_expected(question, alt, release, expected)
        if alt_exp is not None and rows_match(q, alt_exp, rows, tol):
            if states_definition({'interpretation': stated_text}, alt['stated_any_of']):
                return True, 'alternative:' + alt['id']
            return False, 'alternative_unstated:' + alt['id']
    return False, 'miss'


def metric_definitions():
    out = {}
    for path in sorted(METRICS_DIR.glob('*.json')):
        data = json.loads(path.read_text())
        for metric in data.get('metrics', data if isinstance(data, list) else []):
            if isinstance(metric, dict) and metric.get('id'):
                out[metric['id']] = json.dumps(metric, default=str)
    return out


def replay(results, metric_rows=None, errata=None):
    questions = {q['id']: q for q in load_questions()}
    entries = (errata or {}).get('questions', {})
    latest = saved_ladder(results, questions)
    mdefs = metric_definitions() if metric_rows else {}
    rows_out = []
    for qid, record in sorted(latest.items()):
        q, exp, rel = questions[qid], record['expected'], record.get('release')
        ladder_rows = answer_rows(record['answer'])
        row = {'id': qid, 'category': (entries.get(qid) or {}).get('category'),
               'ladder_default': rows_match(q, exp, ladder_rows)}
        row['ladder_errata'], row['ladder_how'] = errata_match(q, exp, ladder_rows, entries.get(qid), rel,
                                                               definition_text(record['answer']))
        m = (metric_rows or {}).get(qid)
        if m is not None:
            mrows = m.get('rows') or []
            row['covered'] = True
            row['metric'] = m.get('metric')
            row['oracle_default'] = bool(mrows) and rows_match(q, exp, mrows)
            row['oracle_errata'], row['oracle_how'] = errata_match(q, exp, mrows, entries.get(qid), rel,
                                                                   mdefs.get(m.get('metric'), ''))
        rows_out.append(row)
    n = len(rows_out)
    cov = [r for r in rows_out if r.get('covered')]
    pct = lambda a, b: round(a / b, 3) if b else None
    summary = {'questions': n,
               'ladder_correct_default': sum(r['ladder_default'] for r in rows_out),
               'ladder_correct_errata': sum(r['ladder_errata'] for r in rows_out)}
    summary['ladder_precision_default'] = pct(summary['ladder_correct_default'], n)
    summary['ladder_precision_errata'] = pct(summary['ladder_correct_errata'], n)
    if metric_rows is not None:
        summary.update(covered=len(cov), oracle_correct_default=sum(r['oracle_default'] for r in cov),
                       oracle_correct_errata=sum(r['oracle_errata'] for r in cov))
        summary['oracle_precision_default'] = pct(summary['oracle_correct_default'], len(cov))
        summary['oracle_precision_errata'] = pct(summary['oracle_correct_errata'], len(cov))
    return {'schema': 'bench-errata-replay/1', 'summary': summary, 'rows': rows_out}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--results', type=Path, default=ROOT / 'results' / 'agere')
    parser.add_argument('--metric-rows', type=Path)
    parser.add_argument('--errata', type=Path, default=ERRATA_PATH)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    questions = {q['id']: q for q in load_questions()}
    errata = load_errata(args.errata, questions)
    metric_rows = json.loads(args.metric_rows.read_text()) if args.metric_rows else None
    report = replay(args.results, metric_rows, errata)
    if args.out:
        args.out.write_text(json.dumps(report, indent=1))
    print(json.dumps(report['summary'], indent=1))
    return report


if __name__ == '__main__':
    main()
