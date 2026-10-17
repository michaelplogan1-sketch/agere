"""Run the scorer cases (bench/fixtures/scorer_cases.json) against the generator's grading function, read-only.

Each case is one answer key and one prediction with the outcome a correct scorer gives: correct, incorrect or malformed
(its own count; never a crash, never graded correct). Agere never edits the generator; this only imports its _grade.

    .venv/bin/python bench/scorer_cases.py [--ecosystem /Users/mikel/synthetic-patient-ecosystem] [--json]
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys

CASES = Path(__file__).resolve().parent / 'fixtures/scorer_cases.json'
ECOSYSTEM = Path('/Users/mikel/synthetic-patient-ecosystem')


def load_cases(path=CASES):
    return json.loads(Path(path).read_text())['cases']


def outcome(grade, case):
    """The outcome the scorer gives a case: correct | incorrect | malformed | crash: <error>."""
    a = {'id': case['id'], **case['key']}
    try:
        answered, correct, status, _ = grade(a, case['prediction'], 'answer', {})
    except Exception as exc:  # a crash aborts a real scoring run
        return f'crash: {type(exc).__name__}: {exc}'
    if status == 'malformed':
        return 'malformed'
    return 'correct' if correct else 'incorrect'


def run(ecosystem=ECOSYSTEM, path=CASES):
    sys.path.insert(0, str(ecosystem))
    grade = importlib.import_module('ecosystem.eval_questions')._grade
    rows = []
    for c in load_cases(path):
        got = outcome(grade, c)
        rows.append({'id': c['id'], 'area': c['area'], 'title': c['title'], 'expected': c['expected'], 'got': got,
                     'ok': got == c['expected']})
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--ecosystem', type=Path, default=ECOSYSTEM)
    p.add_argument('--json', action='store_true')
    args = p.parse_args(argv)
    rows = run(args.ecosystem)
    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['id']} {r['area']:<14} expected {r['expected']:<9} got {r['got']:<10} {r['title']}")
    bad = [r for r in rows if not r['ok']]
    print(f'{len(rows) - len(bad)}/{len(rows)} cases pass')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
