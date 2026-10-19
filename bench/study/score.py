"""Score one arm-pass (or a whole run) into per-question rows.

    .venv/bin/python bench/study/score.py --run-dir artifacts/study/<run_id> [--arm A --pass 1]

Locked set: bench/gold10_run.py's scorer, generalized to the locked file as it grows. Patient lists ('ids' in the
reference) use gold10_run.score itself (pass at F1 >= 0.98); count/value entries ('count' or 'value' plus optional
'tolerance' = absolute, 'rel_tolerance' = relative) pass within tolerance (exact when none is stated).
Breadth set: the generator's scorer (python3 -m ecosystem.eval_scorer --questions ... --per-question), which prints only
per-question correct flags and aggregates. Answer keys are never read here except through those two scorers, and the
scorer's missing/extra id samples are dropped before anything is written.

Per-question row: set, id, domain, correct, cost_usd (ledger window when exact, else product-reported), cost_source,
seconds, route, decision, free (no paid call), certified tier + bound, status, error, answer signature (repeatability).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.study import common  # noqa: E402


def _gold10():
    sys.path.insert(0, str(common.ROOT / 'bench'))
    import gold10_run
    return gold10_run


def _close(got, want, tol, rel):
    try:
        g, w = float(got), float(want)
    except (TypeError, ValueError):
        return False
    return abs(g - w) <= max(float(tol or 0), float(rel or 0) * abs(w), 1e-9)


def score_locked(answers, ref=None):
    """-> {id: {'correct', 'f1'?, ...}} over the reference's questions (answers missing from the run count as wrong)."""
    g = _gold10()
    if ref is None:
        _, ref = g.reference()
    ps = {q: r for q, r in ref['questions'].items() if 'ids' in r}
    out = {}
    if ps:
        res = g.score(answers, {**ref, 'questions': ps})
        for r in res['questions']:  # missing_sample / extra_sample (answer-key ids) are deliberately not kept
            out[r['id']] = {'correct': bool(r['pass']), 'f1': r['f1'], 'precision': r['precision'], 'recall': r['recall'],
                            'expected': r['expected'], 'returned': r['returned']}
    by_id = {a['id']: a for a in answers}
    for qid, r in ref['questions'].items():
        if qid in ps:
            continue
        a = by_id.get(qid) or {}
        want = r.get('value', r.get('count'))
        got = a.get('count', a.get('value')) if a.get('status') == 'answered' else None
        out[qid] = {'correct': got is not None and _close(got, want, r.get('tolerance'), r.get('rel_tolerance'))}
    return out


def score_breadth(submission_path, out_path=None):
    """Run the generator scorer on a breadth submission -> ({id: {'correct', 'answered', ...}}, the scorer's output)."""
    cmd = ['python3', '-m', 'ecosystem.eval_scorer', '--release', common.BREADTH_RELEASE, '--questions', str(Path(submission_path).resolve()),
           '--sample-release', common.BREADTH_SAMPLE, '--per-question']
    proc = subprocess.run(cmd, cwd=str(common.GENERATOR), capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError(f'generator scorer failed: {proc.stderr[-800:]}')
    res = json.loads(proc.stdout)
    if out_path:
        Path(out_path).write_text(json.dumps(res, indent=1))
    return {r['id']: r for r in res.get('per_question') or []}, res


def signature(row):
    """A canonical digest of what was answered (for repeatability across passes)."""
    st = row.get('status')
    if st != 'answered':
        return f'status:{st}'
    if 'patient_ids' in row:
        val = sorted(row['patient_ids'])
    elif 'table' in row:
        val = sorted((str(r.get('key')), str(r.get('value'))) for r in row['table'])
    elif 'count' in row:
        val = ('count', row['count'])
    else:
        v = row.get('value')
        val = ('value', round(v, 6) if isinstance(v, float) else v)
    return hashlib.sha256(json.dumps(val, default=str).encode()).hexdigest()[:16]


def ledger_total(path):
    rows = common.read_jsonl(path)
    costs = [r['cost_usd'] for r in rows if r.get('status') == 'validated']
    return {'calls': len(rows), 'validated': len(costs), 'failed': sum(r.get('status') == 'failed' for r in rows),
            'cost_usd': round(sum(c for c in costs if c is not None), 8), 'unpriced': sum(c is None for c in costs),
            'by_model': {m: round(sum(r['cost_usd'] or 0 for r in rows if r.get('model') == m and r.get('status') == 'validated'), 8)
                         for m in sorted({r.get('model') for r in rows if r.get('model')})}}


def build_rows(answers, locked, breadth):
    rows = []
    for a in answers:
        s = locked.get(a['id']) if a.get('set') == 'locked' else breadth.get(a['id'])
        exact = 'ledger_cost_usd' in a
        cert = a.get('certified') or {}
        rows.append({'set': a.get('set'), 'id': a['id'], 'domain': a.get('domain'), 'answer_type': a.get('answer_type'),
                     'correct': bool(s and s.get('correct')), 'scored': s is not None,
                     **({'f1': s.get('f1')} if s and 'f1' in s else {}),
                     **({'f1_band': s.get('f1_band')} if s and 'f1_band' in s else {}),
                     'cost_usd': a.get('ledger_cost_usd') if exact else a.get('cost_usd'),
                     'cost_source': 'ledger' if exact else 'reported', 'reported_cost_usd': a.get('cost_usd'),
                     'ledger_calls': a.get('ledger_calls'), 'seconds': a.get('seconds'), 'route': a.get('route'),
                     'decision': a.get('decision'), 'pipeline': a.get('pipeline'), 'free': a.get('free'),
                     'certified_tier': cert.get('tier'), 'certified_bound': cert.get('bound'), 'status': a.get('status'),
                     'error': a.get('error'), 'signature': signature(a)})
    return rows


def summarize(rows, ledger):
    secs = sorted(r['seconds'] for r in rows if isinstance(r.get('seconds'), (int, float)))
    costs = [r['cost_usd'] for r in rows]
    return {'n': len(rows), 'correct': sum(r['correct'] for r in rows),
            'accuracy': round(sum(r['correct'] for r in rows) / len(rows), 4) if rows else None,
            'cost_usd': round(sum(c for c in costs if c is not None), 8), 'cost_unknown': sum(c is None for c in costs),
            'ledger_cost_usd': ledger['cost_usd'], 'ledger_calls': ledger['calls'],
            'median_seconds': round(statistics.median(secs), 2) if secs else None,
            'free': sum(r['free'] is True for r in rows), 'free_unknown': sum(r['free'] is None for r in rows),
            'by_set': {s: {'n': sum(r['set'] == s for r in rows), 'correct': sum(r['correct'] for r in rows if r['set'] == s)}
                       for s in sorted({r['set'] for r in rows})}}


def score_arm_pass(run_dir, arm, n, *, ref=None, breadth_scorer=None):
    d = common.arm_pass_dir(run_dir, arm, n)
    answers = common.read_jsonl(d / 'answers.jsonl')
    locked_ids = {a['id'] for a in answers if a.get('set') == 'locked'}
    locked = score_locked([a for a in answers if a.get('set') == 'locked'], ref) if locked_ids else {}
    locked = {k: v for k, v in locked.items() if k in locked_ids}
    breadth = {}
    if any(a.get('set') == 'breadth' for a in answers):
        breadth = (breadth_scorer or (lambda p: score_breadth(p, d / 'breadth_scorer.json')[0]))(d / 'breadth_submission.jsonl')
    rows = build_rows(answers, locked, breadth)
    ledger = ledger_total(d / 'ledger.jsonl')
    out = {'schema': 'agere-study-score/1', 'arm': arm, 'pass': n, 'summary': summarize(rows, ledger), 'ledger': ledger,
           'locked_scorer': 'bench/gold10_run.py score (F1 >= 0.98) + count/value tolerance' if locked else None,
           'breadth_scorer': 'ecosystem.eval_scorer --questions --per-question' if breadth else None, 'questions': rows}
    (d / 'score.json').write_text(json.dumps(out, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', type=Path, required=True)
    ap.add_argument('--arm')
    ap.add_argument('--pass', dest='n', type=int)
    a = ap.parse_args()
    run = json.loads((a.run_dir / 'run.json').read_text())
    for p in run['passes']:
        if (a.arm and p['arm'] != a.arm) or (a.n and p['pass'] != a.n):
            continue
        s = score_arm_pass(a.run_dir, p['arm'], p['pass'])['summary']
        print(f"{p['arm']} p{p['pass']}: {s['correct']}/{s['n']} correct  ${s['cost_usd']:.4f}  median {s['median_seconds']}s")


if __name__ == '__main__':
    main()
