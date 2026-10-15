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
from bench.adapters import CONTRACT, MODEL, SEMANTIC_RULES, SEMANTIC_SQL_TOOL, _tool_loop, semantic_context
from bench.common import DEFAULT_SEMANTIC_DB, ROOT, SemanticWarehouse
from bench.score import recorded_cost, score_answer

RECONCILE_RULES = '''You are reconciling two independent analysts who answered the same question over this semantic layer
and disagree. Decide which answer follows the question's own stated definitions (scope, code sets, sources to combine,
date windows, tie-breaking, rounding, units, requested answer type). When the question defines a term, its definition
wins over any governed or default definition. Use query_sql (at most 4 calls) to check the decisive difference, not to
redo everything. If both are wrong and you can establish the right answer with your checks, return it as "neither".
If you cannot tell, abstain.
Return ONLY JSON: {"choice":"1|2|neither|abstain","reason":"one or two sentences","answer":<answer object>}
where answer is the chosen candidate's answer object copied exactly (choice 1 or 2), your corrected answer object
(neither), or null (abstain). The answer object follows this contract:
'''


def canonical(answer):
    if not isinstance(answer, dict) or answer.get('status') != 'answered':
        return None
    t = answer.get('answer_type')
    if t == 'patient_set':
        v = sorted(answer.get('patient_ids') or [])
    elif t == 'table':
        v = sorted(json.dumps({k: (round(x, 2) if isinstance(x, float) else x) for k, x in r.items()}, sort_keys=True)
                   for r in (answer.get('table') or []))
    elif t in ('count', 'value'):
        v = answer.get(t)
        v = round(v, 2) if isinstance(v, float) else v
    else:
        return None
    return json.dumps([t, v], sort_keys=True, default=str)


def candidate(row):
    answer = row.get('answer')
    out = {'answer': answer}
    sql = [c.get('sql') for c in (row.get('tool_calls') or []) if c.get('sql')]
    if sql:
        out['sql_run'] = sql[-3:]
    return out


def reconcile(question, a_row, b_row, semantic, *, seed=0, model=MODEL):
    order = [('agere', a_row), ('llm', b_row)]
    random.Random(f"{seed}:{question['id']}").shuffle(order)
    docs, _ = semantic_context(semantic)
    system = [{'type': 'text', 'text': SEMANTIC_RULES + RECONCILE_RULES + CONTRACT},
              {'type': 'text', 'text': docs, 'cache_control': {'type': 'ephemeral'}}]
    user = json.dumps({'question': question['question'], 'as_of': question['as_of'],
                       'semantic_snapshot_as_of': semantic.as_of,
                       'candidate_1': candidate(order[0][1]), 'candidate_2': candidate(order[1][1])}, default=str)

    def execute(use):
        if use.get('name') != 'query_sql' or set(use.get('input', {})) != {'sql'}:
            raise ValueError('Only query_sql with one sql string is supported')
        return semantic.query(use['input']['sql'], as_of=question['as_of'], max_rows=500, max_chars=30000)

    result = _tool_loop(user, system, [SEMANTIC_SQL_TOOL], execute, key=None, model=model, max_tools=4,
                        transport=__import__('bench.adapters', fromlist=['post_json']).post_json,
                        extra={'cache_control': {'type': 'ephemeral'}})
    verdict = result.get('answer') or {}
    choice = str(verdict.get('choice'))
    picked = {'1': order[0][0], '2': order[1][0]}.get(choice, choice)
    final = {'agere': a_row.get('answer'), 'llm': b_row.get('answer')}.get(picked)
    if picked == 'neither':
        final = verdict.get('answer')
    return {'picked': picked, 'reason': verdict.get('reason'), 'answer': final,
            'reconcile_cost_usd': result.get('estimated_cost_usd'), 'reconcile_error': result.get('error'),
            'reconcile_sql': [c.get('sql') for c in result.get('tool_calls') or []]}


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
