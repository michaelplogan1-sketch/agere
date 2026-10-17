"""Build a dev/holdout question file from the release's own questions.jsonl, so question text can never go stale.

    .venv/bin/python bench/question_set.py --release healthcare-warehouse-v2.8 --ids-from tmp/layers/dev20.jsonl \
        > bench/questions/dev20.jsonl
Each line: {id, answer_type, question} with question = "<question> Definition: <definition> Use data received as of <as_of>."
(the same prompt bench/truth_run.py sends in defined mode).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.truth_run import ECOSYSTEM, prompt


def build(release, ids, as_of):
    rel = {q['id']: q for q in map(json.loads, (ECOSYSTEM / 'releases' / release / 'questions/questions.jsonl').read_text().splitlines())}
    missing = [i for i in ids if i not in rel]
    if missing:
        raise SystemExit(f'not in {release}: {", ".join(missing)}')
    return [{'id': i, 'answer_type': rel[i]['answer_type'], 'question': prompt(rel[i], 'defined', as_of)} for i in ids]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--release', default='healthcare-warehouse-v2.8')
    p.add_argument('--ids-from', type=Path, help='a jsonl whose ids (in order) select the questions')
    p.add_argument('--ids', nargs='*')
    p.add_argument('--as-of', default='2030-05-27')
    args = p.parse_args(argv)
    ids = list(args.ids or []) + ([json.loads(l)['id'] for l in args.ids_from.read_text().splitlines() if l.strip()] if args.ids_from else [])
    for q in build(args.release, ids, args.as_of):
        print(json.dumps(q))


if __name__ == '__main__':
    main()
