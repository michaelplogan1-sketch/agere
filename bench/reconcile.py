"""Agere + frontier_semantic working together, measured on saved bench rows.

Agree (same canonical answer) -> ship it. Disagree -> one reconciliation turn: Opus gets the question, both candidate
answers (blind order, with each one's grounding and SQL when recorded), the semantic-layer docs and up to 4 read-only
SQL checks, and must say which candidate follows the question's stated definitions. It may return a corrected answer
('neither') or abstain. The reconciler never sees the reference. Scored with the strict scorer.

    .venv/bin/python bench/reconcile.py --agere <a.jsonl>... --llm <b.jsonl>... --out bench/results/combined/<name>.jsonl
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench import adapters
from bench.common import DEFAULT_SEMANTIC_DB, ROOT, SemanticWarehouse
from bench.score import recorded_cost, score_answer
# The reconcile rules, comparison key, candidate view and turn are shared with the product's "together" path
# (clinical_core/agere/clinical/semantic_analyst.py); the bench re-exports them so both send identical prompts.
from clinical_core.agere.clinical import semantic_analyst as _analyst
from clinical_core.agere.clinical.semantic_analyst import RECONCILE_RULES, candidate, canonical  # noqa: F401 - re-exports

MODEL = adapters.MODEL


def reconcile(question, a_row, b_row, semantic, *, seed=0, model=MODEL):
    try:
        send = adapters._sender(None, model, adapters.post_json)
    except Exception as exc:
        return {'picked': 'None', 'reason': None, 'answer': None, 'reconcile_cost_usd': 0.0,
                'reconcile_error': type(exc).__name__ + ': ' + str(exc), 'reconcile_sql': []}
    out = _analyst.reconcile(question['question'], question['as_of'], a_row, b_row, semantic, send=send, model=model,
                             seed_key=f"{seed}:{question['id']}", labels=('agere', 'llm'),
                             definitions=False)  # bench measurements keep the plain documented layer
    out.pop('result')
    return out


def load(paths):
    rows = {}
    for p in paths:
        for line in open(p):
            r = json.loads(line)
            rows[r['id']] = r
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--agere', nargs='+', required=True)
    p.add_argument('--llm', nargs='+', required=True)
    p.add_argument('--questions', nargs='+', default=['heldout10a.json', 'heldout10b.json'])
    p.add_argument('--semantic-database', type=Path, default=DEFAULT_SEMANTIC_DB)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--model', default=MODEL)
    args = p.parse_args(argv)
    qs = {q['id']: q for f in args.questions for q in json.load(open(ROOT / 'questions' / f))}
    A, B = load(args.agere), load(args.llm)
    semantic = SemanticWarehouse(args.semantic_database)

    def one(qid):
        q, a, b = qs[qid], A.get(qid) or {}, B.get(qid) or {}
        expected = a.get('expected') or b.get('expected')
        ca, cb = canonical(a.get('answer')), canonical(b.get('answer'))
        if ca is not None and ca == cb:
            row = {'picked': 'agree', 'answer': a.get('answer'), 'reconcile_cost_usd': 0.0}
        elif all((x.get('answer') or {}).get('status') == 'unsupported' for x in (a, b)):
            row = {'picked': 'agree_unsupported', 'answer': b.get('answer'), 'reconcile_cost_usd': 0.0}
        else:
            row = reconcile(q, a, b, semantic, model=args.model)
        score = score_answer(q, expected, row['answer']) if expected is not None else None
        sa = score_answer(q, expected, a.get('answer')) if expected is not None else None
        sb = score_answer(q, expected, b.get('answer')) if expected is not None else None
        return {'id': qid, 'domain': q.get('domain'), **row, 'score': score,
                'agere_correct': bool(sa and sa['correct']), 'llm_correct': bool(sb and sb['correct']),
                'base_cost_usd': (recorded_cost(a) or 0) + (recorded_cost(b) or 0)}

    with ThreadPoolExecutor(args.jobs) as pool:
        out = list(pool.map(one, sorted(qs)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, 'w') as f:
        for r in out:
            f.write(json.dumps(r, default=str) + '\n')
    n = len(out)
    correct = sum(bool(r['score'] and r['score']['correct']) for r in out)
    answered = [r for r in out if (r['answer'] or {}).get('status') == 'answered']
    right_answered = sum(bool(r['score'] and r['score']['correct']) for r in answered)
    rc = sum(r.get('reconcile_cost_usd') or 0 for r in out)
    print(f"combined {correct}/{n}; answered {len(answered)}, precision on answered {right_answered}/{len(answered)}")
    print(f"agere alone {sum(r['agere_correct'] for r in out)}, llm alone {sum(r['llm_correct'] for r in out)}, "
          f"either {sum(r['agere_correct'] or r['llm_correct'] for r in out)}")
    print(f"reconcile cost ${rc:.2f}; base cost ${sum(r['base_cost_usd'] for r in out):.2f}")
    from collections import Counter
    print('picked:', Counter(r['picked'] for r in out))
    print('errors:', [r['id'] for r in out if r.get('reconcile_error')])


if __name__ == '__main__':
    main()
