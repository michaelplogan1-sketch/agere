"""Agreement among the runs whose executed answers passed every check.

Canonical forms compare exactly (sets and tables by key, whole numbers exactly), fractions after rounding to 4
significant digits, so two runs that computed the same thing agree and two that computed different things never do.

    unanimous   every run passed and all agree (>= 2 runs): answered, verified
    majority    more than half of all runs agree: answered, unverified
    escalate    no majority among the passing runs: one stronger-model pass decides or abstains
    abstain     fewer than two runs passed, or a run declined to answer because the meaning is unresolved
"""
from __future__ import annotations

import json
from collections import Counter


def _round(v):
    """Whole numbers compare exactly (21256 and 21259 are different counts); fractions to 4 significant digits."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if f.is_integer():
        return int(f)
    r = float(f'{f:.4g}')
    return int(r) if r.is_integer() else r


def canonical(answer_type, answer):
    if answer_type in ('count', 'value'):
        return json.dumps(_round(answer[answer_type]))
    if answer_type == 'patient_set':
        return json.dumps(sorted(set(answer['patient_ids'])))
    return json.dumps(sorted([[str(k) for k in r['keys']], _round(r['value'])] for r in answer['table']))


def decide(answer_type, runs):
    """runs: [{'answer': dict|None, 'passed': bool, 'clarify': bool}] -> (decision, answer or None, agreeing, passing)."""
    passing = [r for r in runs if r.get('passed') and r.get('answer') is not None]
    # a run that left the meaning open and declined to answer blocks the vote; one that noted a caveat and answered is a vote
    if any(r.get('clarify') and r.get('answer') is None for r in runs):
        return 'abstain', None, 0, len(passing)
    if len(passing) < 2:
        return ('escalate' if passing else 'abstain'), None, 0, len(passing)
    counts = Counter(canonical(answer_type, r['answer']) for r in passing)
    top, n = counts.most_common(1)[0]
    answer = next(r['answer'] for r in passing if canonical(answer_type, r['answer']) == top)
    if n == len(runs) == len(passing):
        return 'unanimous', answer, n, len(passing)
    if n * 2 > len(runs):
        return 'majority', answer, n, len(passing)
    return 'escalate', None, n, len(passing)
