"""Run the hybrid core (clinical_core/agere/hybrid) on a question file.

    # 1. prompts for N independent model runs (each run sees the questions in batches; runs never see each other)
    .venv/bin/python bench/hybrid_run.py prompt tmp/hybrid/H1 --questions tmp/layers/dev20.jsonl --runs 3 --batch 10
    # 2. a model answers each prompt file, writing tmp/hybrid/H1/model/<prompt name>.jsonl ({id, contract, sql} lines)
    # 3. validate, compile or execute, check, vote; writes answers_hybrid.jsonl and escalate.md
    .venv/bin/python bench/hybrid_run.py execute tmp/hybrid/H1 --questions tmp/layers/dev20.jsonl
    # 4. (optional) a stronger model answers escalate.md into model/escalation.jsonl; re-run execute
    # 5. score: .venv/bin/python bench/layer_score.py tmp/hybrid/H1
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clinical_core.agere.hybrid import contract as hc, execute as hx, vote as hv

REPO = Path(__file__).resolve().parents[1]
DB = REPO / 'artifacts/agere-healthcare-v28-sample-0.30/stack/semantic.sqlite3'
INDEX = REPO / 'tmp/layers/catalog_index.md'
PROMPT = REPO / 'clinical_core/agere/hybrid/analyst_prompt.md'


def load_questions(path):
    qs = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    for q in qs:
        m = re.search(r'as of (\d{4}-\d{2}-\d{2})', q['question'])
        q.setdefault('as_of', m.group(1) if m else None)
    return qs


def prompt(args):
    qs = load_questions(args.questions)
    (args.run / 'prompts').mkdir(parents=True, exist_ok=True)
    (args.run / 'model').mkdir(parents=True, exist_ok=True)
    template = PROMPT.read_text()
    names = []
    for k in range(1, args.runs + 1):
        for j in range(0, len(qs), args.batch):
            name = f'run{k}_b{j // args.batch + 1}'
            batch = '\n'.join(f"- {q['id']} [{q['answer_type']}] {q['question']}" for q in qs[j:j + args.batch])
            text = template.format(db=args.db, index=INDEX, out=(args.run / 'model' / f'{name}.jsonl').resolve(), questions=batch)
            (args.run / 'prompts' / f'{name}.md').write_text(text)
            names.append(name)
    print('\n'.join(str(args.run / 'prompts' / f'{n}.md') for n in names))


def read_model(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get('id'):
                out[r['id']] = r
    return out


def evaluate(q, item, db):
    """One model run on one question -> {answer, passed, clarify, path, problems}."""
    if not item:
        return {'answer': None, 'passed': False, 'clarify': False, 'path': None, 'problems': ['no output']}
    contract = item.get('contract')
    problems = hc.validate(contract, q)
    if problems:
        return {'answer': None, 'passed': False, 'clarify': False, 'path': None, 'problems': problems}
    if hc.needs_clarification(contract) and not item.get('sql'):
        return {'answer': None, 'passed': False, 'clarify': True, 'path': None, 'problems': ['unresolved: ' + '; '.join(contract['unresolved'])]}
    sql, why = hx.compile_metric(contract, q['as_of'])
    path = 'governed' if sql else 'sql'
    notes = [f'governed metric not used: {why}'] if why else []
    if not sql:
        sql = item.get('sql')
    cols, rows, err = hx.run_sql(db, sql)
    if err:
        return {'answer': None, 'passed': False, 'clarify': False, 'path': path, 'problems': notes + [f'execution: {err}'], 'sql': sql}
    answer, why = hx.to_answer(contract, cols, rows)
    if answer is None:
        return {'answer': None, 'passed': False, 'clarify': False, 'path': path, 'problems': notes + [f'shape: {why}'], 'sql': sql}
    problems = hx.checks(contract, answer, db)
    return {'answer': answer, 'passed': not problems, 'clarify': hc.needs_clarification(contract), 'path': path,
            'problems': notes + problems, 'sql': sql}


def execute(args):
    qs = load_questions(args.questions)
    model = args.run / 'model'
    runs = defaultdict(dict)  # run name -> id -> item
    for f in sorted(model.glob('run*_b*.jsonl')):
        runs[f.stem.split('_')[0]].update(read_model(f))
    escalation = read_model(model / 'escalation.jsonl') if (model / 'escalation.jsonl').exists() else {}
    details, answers, esc = [], [], []
    for q in qs:
        per = {name: evaluate(q, items.get(q['id']), args.db) for name, items in sorted(runs.items())}
        decision, answer, agree, passing = hv.decide(q['answer_type'], list(per.values()))
        final = decision
        if decision == 'escalate':
            if q['id'] in escalation:
                e = evaluate(q, escalation[q['id']], args.db)
                per['escalation'] = e
                answer, final = (e['answer'], 'escalated') if e['passed'] and not e['clarify'] else (None, 'escalated_abstain')
            else:
                esc.append((q, per, runs))
        status = 'answered' if answer is not None else 'abstain'
        answers.append({'id': q['id'], 'answer_type': q['answer_type'], 'status': status, **(answer or {}),
                        'interpretation': f'hybrid {final}; {agree}/{len(per)} agree; {passing} passed checks'})
        details.append({'id': q['id'], 'decision': final, 'agree': agree, 'passing': passing,
                        'runs': {k: {kk: v[kk] for kk in ('passed', 'clarify', 'path', 'problems')} | {'canonical': hv.canonical(q['answer_type'], v['answer'])[:200] if v['answer'] else None}
                                 for k, v in per.items()}})
    with open(args.run / 'answers_hybrid.jsonl', 'w') as f:
        for a in answers:
            f.write(json.dumps(a, default=str) + '\n')
    with open(args.run / 'runs.jsonl', 'w') as f:
        for d in details:
            f.write(json.dumps(d, default=str) + '\n')
    write_escalation(args.run, esc)
    tally = defaultdict(int)
    for d in details:
        tally[d['decision']] += 1
    print(dict(tally), f'| answered {sum(a["status"] == "answered" for a in answers)}/{len(answers)}',
          f'| governed path used {sum(1 for d in details for r in d["runs"].values() if r["path"] == "governed")}',
          f'| run outputs failing checks {sum(1 for d in details for r in d["runs"].values() if not r["passed"])}')
    for d in details:
        bad = {k: r['problems'] for k, r in d['runs'].items() if r['problems']}
        if bad:
            print(' ', d['id'], d['decision'], json.dumps(bad)[:400])


def write_escalation(run, esc):
    path = run / 'escalate.md'
    if not esc:
        path.write_text('No disagreements to escalate.\n')
        return
    parts = [PROMPT.read_text().split('Write your results to')[0].format(db=DB, index=INDEX),
             'The analysts below disagreed or failed checks. For each question, review their contracts, SQL and the '
             'executed outcome, decide what the question means, and write ONE contract and SQL (or unresolved, sql null, '
             f"to abstain). Write to {(run / 'model' / 'escalation.jsonl').resolve()}, one JSON line per question: "
             '{"id", "contract", "sql"}. Then reply "done".\n']
    for q, per, runs in esc:
        parts.append(f"\n## {q['id']} [{q['answer_type']}] {q['question']}")
        for name, r in per.items():
            item = runs[name].get(q['id']) or {}
            parts.append(f"### {name}: passed={r['passed']} problems={r['problems']}\n"
                         f"result: {hv.canonical(q['answer_type'], r['answer'])[:300] if r['answer'] else None}\n"
                         f"contract: {json.dumps(item.get('contract'))}\nsql: {item.get('sql')}")
    path.write_text('\n'.join(parts))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('step', choices=['prompt', 'execute'])
    p.add_argument('run', type=Path)
    p.add_argument('--questions', type=Path, default=REPO / 'tmp/layers/dev20.jsonl')
    p.add_argument('--db', type=Path, default=DB)
    p.add_argument('--runs', type=int, default=3)
    p.add_argument('--batch', type=int, default=10)
    args = p.parse_args(argv)
    (prompt if args.step == 'prompt' else execute)(args)


if __name__ == '__main__':
    main()
