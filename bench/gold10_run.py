"""Gold 10: ask the product the locked questions and score each patient list against the locked reference.

    .venv/bin/python bench/gold10_run.py ask   --out tmp/gold10/G1        # ask the product, then score
    .venv/bin/python bench/gold10_run.py score --answers tmp/gold10/G1/answers.jsonl
    .venv/bin/python bench/gold10_run.py selftest                          # the three checks on the test itself

The reference (docs/agere/gold10/locked.json, falling back to agere_reference.json until questions are locked) holds
customer identifiers per question. A question passes at F1 >= 0.98. Each scored row also records which part of the
stack answered (the answer's decision), so routing is visible per question.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / 'docs' / 'agere' / 'gold10'
PASS_F1 = 0.98


def reference():
    path = GOLD / 'locked.json' if (GOLD / 'locked.json').is_file() else GOLD / 'agere_reference.json'
    data = json.loads(path.read_text())
    return path.name, data


def score(answers, ref):
    rows, passed = [], 0
    by_id = {a['id']: a for a in answers}
    for qid, r in ref['questions'].items():
        want = set(r['ids'])
        a = by_id.get(qid) or {}
        got = set(a.get('patient_ids') or [])
        tp = len(want & got)
        p = tp / len(got) if got else (1.0 if not want else 0.0)
        rc = tp / len(want) if want else (1.0 if not got else 0.0)
        f1 = 2 * p * rc / (p + rc) if p + rc else 0.0
        ok = f1 >= PASS_F1
        passed += ok
        rows.append({'id': qid, 'pass': ok, 'f1': round(f1, 3), 'precision': round(p, 3), 'recall': round(rc, 3),
                     'expected': len(want), 'returned': len(got), 'status': a.get('status'),
                     'answered_by': a.get('decision'), 'missing_sample': sorted(want - got)[:5],
                     'extra_sample': sorted(got - want)[:5]})
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]
    return {'reference_fingerprint': hashlib.sha256(json.dumps(ref['questions'], sort_keys=True).encode()).hexdigest()[:16],
            'passed': passed, 'total': len(rows), 'score_fingerprint': digest, 'questions': rows}


def show(result):
    print(f"Gold 10: {result['passed']}/{result['total']} passed  (reference {result['reference_fingerprint']})")
    for r in result['questions']:
        print(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['id']:8s} F1 {r['f1']:.3f}  expected {r['expected']:4d}  "
              f"returned {r['returned']:4d}  answered by {r['answered_by']}")


def ask(out):
    from run_integrated_stack import keys
    keys()
    from clinical_core.agere.onboarding.workspace import Workspace, ask_many
    _, ref = reference()
    ws = Workspace(ref['workspace'])
    qs = [json.loads(l) for l in (GOLD / 'questions.jsonl').read_text().splitlines() if l.strip()]
    items = [{'id': q['id'], 'question': q['question'] + ' Definition: ' + q['definition'], 'answer_type': q['answer_type']}
             for q in qs if q['id'] in ref['questions']]
    out.mkdir(parents=True, exist_ok=True)
    rows = ask_many(ws, items, workers=3, out_path=out / 'answers_raw.jsonl', native_ids=True)
    with open(out / 'answers.jsonl', 'w') as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + '\n')
    return rows


def selftest():
    """The test must work before it grades Agere: the reference scores 100%, a planted fault fails exactly the
    questions it touches, and scoring the same answers twice gives the identical result."""
    _, ref = reference()
    perfect = [{'id': q, 'patient_ids': r['ids'], 'decision': 'reference'} for q, r in ref['questions'].items()]
    s1, s2 = score(perfect, ref), score(perfect, ref)
    checks = {'reference_scores_100': s1['passed'] == s1['total'],
              'rescore_identical': s1['score_fingerprint'] == s2['score_fingerprint']}
    # planted fault: hide the pharmacy fill table (as if staging lost every fill) and re-derive. Every question whose
    # query reads fills may fail; at least one must; no question that does not read fills may change.
    sys.path.insert(0, str(ROOT / 'bench'))
    import re
    import sqlite3
    import gold10_reference as g
    from clinical_core.agere.onboarding.workspace import Workspace, native_id
    ws = Workspace(ref['workspace'])
    db = sqlite3.connect(f'file:{ws.semantic_path}?mode=ro', uri=True)
    db.execute('CREATE TEMP VIEW fill AS SELECT * FROM main.fill WHERE 0')
    planted, reads = [], set()
    has_death = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name = 'death'").fetchone())
    for qid, sql in g.queries(ref['as_of'], death_table=has_death).items():
        if re.search(r'\bFROM fill\b|\bJOIN fill\b', sql):
            reads.add(qid)
        pids = sorted({r[0] for r in db.execute(sql) if r[0]})
        km = ws.customer_ids(pids)
        planted.append({'id': qid, 'patient_ids': sorted({native_id(km.get(p)) or p for p in pids}), 'decision': 'planted'})
    s3 = score(planted, ref)
    failed = {r['id'] for r in s3['questions'] if not r['pass']}
    checks['planted_fault_fails_only_affected'] = bool(failed) and failed <= reads
    checks['planted_fault_failed'] = sorted(failed)
    checks['planted_fault_may_affect'] = sorted(reads)
    print(json.dumps(checks, indent=1))
    return all(v for k, v in checks.items() if isinstance(v, bool))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['ask', 'score', 'selftest'])
    ap.add_argument('--out', type=Path, default=ROOT / 'tmp' / 'gold10' / 'latest')
    ap.add_argument('--answers', type=Path)
    a = ap.parse_args()
    if a.cmd == 'selftest':
        sys.exit(0 if selftest() else 1)
    _, ref = reference()
    if a.cmd == 'ask':
        rows = ask(a.out)
    else:
        rows = [json.loads(l) for l in a.answers.read_text().splitlines() if l.strip()]
    result = score(rows, ref)
    show(result)
    out = a.out if a.cmd == 'ask' else a.answers.parent
    (out / 'score.json').write_text(json.dumps(result, indent=1))


if __name__ == '__main__':
    main()
