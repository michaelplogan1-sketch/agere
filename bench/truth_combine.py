"""Free (no model calls) Agere + LLM combination over saved truth runs, scored by the sealed scorer.

Agreement (same canonical answer) ships it; on disagreement one of three policies applies:
  abstain  - don't answer (measures precision when two independent systems agree)
  agere    - fall back to Agere's answer
  llm      - fall back to the LLM's answer
    .venv/bin/python bench/truth_combine.py --agere <run dir> --llm <run dir> [<extra llm run dir> ...] --out <dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.truth_run import score


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def same(a, b):
    """Two contracts give the same answer: counts/values within 0.5% (or 0.01 absolute), patient sets with Jaccard
    >= 0.95, tables with the same keys and values within 0.5%."""
    if a.get('status') != 'answered' or b.get('status') != 'answered':
        return a.get('status') == b.get('status') == 'unsupported'
    kind = a['answer_type']
    if kind in ('count', 'value'):
        x, y = a.get(kind), b.get(kind)
        return _num(x) and _num(y) and abs(x - y) <= max(0.01, 0.005 * max(abs(x), abs(y)))
    if kind == 'patient_set':
        x, y = set(a.get('patient_ids') or []), set(b.get('patient_ids') or [])
        return bool(x | y) and len(x & y) / len(x | y) >= 0.95
    x = {r['key']: r['value'] for r in a.get('table') or []}
    y = {r['key']: r['value'] for r in b.get('table') or []}
    return bool(x) and x.keys() == y.keys() and all(same({'status': 'answered', 'answer_type': 'value', 'value': x[k]},
                                                         {'status': 'answered', 'answer_type': 'value', 'value': y[k]})
                                                    for k in x)


def load(dirs):
    rows = {}
    for d in dirs:
        for line in open(Path(d) / 'rows.jsonl'):
            r = json.loads(line)
            rows[r['id']] = r
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--release', default='healthcare-warehouse-v2.8')
    p.add_argument('--agere', nargs='+', required=True)
    p.add_argument('--llm', nargs='+', required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args(argv)
    A, B = load(args.agere), load(args.llm)
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    agree = 0
    files = {k: open(args.out / f'question_answers_{k}.jsonl', 'w') for k in ('abstain', 'agere', 'llm')}
    for qid in sorted(set(A) | set(B)):
        a, b = (A.get(qid) or {}).get('contract'), (B.get(qid) or {}).get('contract')
        base = a or b
        if a and b and same(a, b):
            agree += 1
            for f in files.values():
                f.write(json.dumps(a) + '\n')
            continue
        files['abstain'].write(json.dumps({'id': qid, 'answer_type': base['answer_type'], 'status': 'abstain'}) + '\n')
        files['agere'].write(json.dumps(a or {'id': qid, 'answer_type': base['answer_type'], 'status': 'abstain'}) + '\n')
        files['llm'].write(json.dumps(b or {'id': qid, 'answer_type': base['answer_type'], 'status': 'abstain'}) + '\n')
    for f in files.values():
        f.close()
    for k in files:
        res = score(args.release, args.out / f'question_answers_{k}.jsonl')
        (args.out / f'score_{k}.txt').write_text(res.stdout + res.stderr)
        res = score(args.release, args.out / f'question_answers_{k}.jsonl', per_question=True)
        (args.out / f'per_question_{k}.txt').write_text(res.stdout + res.stderr)
    print(args.out, f'agree={agree} of {len(set(A) | set(B))}')


if __name__ == '__main__':
    main()
