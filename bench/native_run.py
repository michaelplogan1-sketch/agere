"""Answer the native slice's question set through a native workspace (the product path) and write the submission.

    .venv/bin/python bench/native_run.py --workspace pilot-native-v2-12 --out tmp/native/N1 [--workers 3] [--limit N]
    (generator repo) python3 -m ecosystem.eval_scorer --release releases/healthcare-warehouse-v2.12 \
        --questions <out>/question_answers.jsonl --sample-release releases/healthcare-warehouse-v2.12-sample-0.30-native

Each question is asked with its definition, as a customer would. Patient lists are submitted in the customer's own
identifiers (EHR id, MRN or member id), which the scorer resolves through its sealed crosswalk.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QUESTIONS = Path('/Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v2.12-sample-0.30-native/questions/questions.jsonl')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workspace', default='pilot-native-v2-12')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--types', help='comma-separated answer types to run (e.g. patient_set)')
    a = ap.parse_args()
    from run_integrated_stack import keys
    keys()
    from clinical_core.agere.onboarding.workspace import Workspace, ask_many
    ws = Workspace(a.workspace)
    qs = [json.loads(l) for l in QUESTIONS.read_text().splitlines() if l.strip()]
    if a.types:
        qs = [q for q in qs if q['answer_type'] in a.types.split(',')]
    qs = qs[:a.limit]
    items = [{'id': q['id'], 'question': q['question'] + (' Definition: ' + q['definition'] if q.get('definition') else ''),
              'answer_type': q['answer_type']} for q in qs]
    a.out.mkdir(parents=True, exist_ok=True)
    rows = ask_many(ws, items, workers=a.workers, out_path=a.out / 'answers_raw.jsonl', native_ids=True)
    with open(a.out / 'question_answers.jsonl', 'w') as f:
        for r in rows:
            f.write(json.dumps({k: v for k, v in r.items() if k not in ('cost_usd', 'decision')}, default=str) + '\n')
    costs = [r.get('cost_usd') for r in rows]
    print(json.dumps({'questions': len(rows), 'answered': sum(r.get('status') == 'answered' for r in rows),
                      'cost_known_usd': round(sum(c for c in costs if isinstance(c, (int, float))), 4),
                      'cost_unknown': sum(1 for c in costs if c is None)}))


if __name__ == '__main__':
    main()
