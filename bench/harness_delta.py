"""Harness-Delta: separate what the Agere harness adds from what extra compute or a shortcut would add anyway.

A harness (retrieval, repair, checks, abstention) usually spends more model calls than a bare baseline, and a benchmark's train
split can leak a shortcut (copy the nearest training answer). A raw accuracy gain over a one-call baseline therefore proves
nothing about transferable value. This tool scores every arm per question and compares the harness with controls that spend the
same compute or none:

  matched resample   the bare arm given the harness's compute budget: best of k attempts, k = round(harness calls / bare calls)
                     (unbiased pass@k from the attempts available; majority vote also reported, since pass@k needs an oracle)
  shortcut copy      zero-model control: copy the SQL of the nearest TRAIN question (EHRSQL adapter) - the gain a leak would give
  abstain-all        the floor under the reliability metric

Outputs per control: arm score, harness score, raw delta, matched delta (harness minus the strongest compute-matched control), a
deterministic paired bootstrap 95% interval on the matched delta, and `shortcut_share`: the fraction of the harness's correct
answers the shortcut control also gets right. The verdict `transferable` needs the matched-delta interval to exclude zero.

    python bench/harness_delta.py --ehrsql <run.jsonl> [--split valid] [--out delta.json]

Rows are scored with the official reliability metric (ehrsql.reliability): +1 correct, 0 abstain on an answerable, -1 wrong.
Synthetic / public data only; no model is called here.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
import random
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def pass_at_k(scores, k):
    """Unbiased pass@k over n attempts of one question (correct = score 1); with n < k it is any-of-n."""
    n, c = len(scores), sum(s == 1 for s in scores)
    if n <= k: return 1.0 if c else 0.0
    if n - c < k: return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def resample_score(attempts, k):
    """Compute-matched best-of-k for one question: pass@k when any attempt is right, else the mean of the non-positive scores
    (a wrong answer still costs). Returns (pass@k reliability, majority-vote reliability)."""
    p = pass_at_k(attempts, k)
    worst = mean(s for s in attempts if s != 1) if any(s != 1 for s in attempts) else 0.0
    top = p + (1 - p) * worst
    first = attempts[:max(1, k)]
    vote = max(set(first), key=lambda s: (first.count(s), s))
    return top, float(vote)


def bootstrap(diffs, n=2000, seed=7):
    rnd, m = random.Random(seed), len(diffs)
    if not m: return None
    stats = sorted(mean(diffs[rnd.randrange(m)] for _ in range(m)) for _ in range(n))
    return [round(stats[int(.025 * n)], 4), round(stats[int(.975 * n) - 1], 4)]


def compare(harness, controls):
    """harness: {id: {'score': s, 'calls': c}}; controls: {name: {id: {'scores': [attempts] or 'score': s, 'calls': c}}}.
    All dicts must share ids. Scores are reliability values in {-1, 0, 1} (or any real, higher is better)."""
    ids = sorted(harness)
    h = [harness[i]['score'] for i in ids]
    h_calls = mean(harness[i].get('calls', 1) for i in ids)
    out = {'questions': len(ids), 'harness': {'score': round(mean(h), 4), 'calls_per_question': round(h_calls, 3),
                                            'correct': sum(s == 1 for s in h)}, 'controls': {}}
    best = None
    for name, ctl in controls.items():
        miss = [i for i in ids if i not in ctl]
        if miss: raise ValueError(f'control {name!r} lacks {len(miss)} ids')
        c_calls = mean(ctl[i].get('calls', 1) for i in ids)
        if any('scores' in ctl[i] for i in ids):
            k = max(1, round(h_calls / max(c_calls, 1e-9)))
            pairs = [resample_score(ctl[i]['scores'], k) for i in ids]
            c, vote = [p[0] for p in pairs], [p[1] for p in pairs]
            row = {'kind': 'matched_resample', 'k': k, 'calls_per_question': round(c_calls * k, 3), 'score_pass_at_k': round(mean(c), 4),
                   'score_majority_vote': round(mean(vote), 4), 'score_single': round(mean(mean(ctl[i]['scores']) for i in ids), 4)}
        else:
            c = [ctl[i]['score'] for i in ids]
            row = {'kind': 'shortcut_or_floor', 'calls_per_question': round(c_calls, 3), 'score': round(mean(c), 4)}
        diffs = [a - b for a, b in zip(h, c)]
        row.update({'delta': round(mean(diffs), 4), 'delta_ci95': bootstrap(diffs),
                    'shortcut_share': round(sum(1 for a, b in zip(h, c) if a == 1 and b == 1) / max(1, sum(a == 1 for a in h)), 4)})
        out['controls'][name] = row
        if best is None or mean(c) > best[1]: best = (name, mean(c), diffs)
    if best:
        ci = bootstrap(best[2])
        out['matched_delta'] = {'against': best[0], 'delta': round(mean(best[2]), 4), 'ci95': ci,
                                'verdict': 'transferable' if ci and ci[0] > 0 else 'not distinguishable from the strongest control'}
    return out


# ---- EHRSQL adapter -------------------------------------------------------------------------------------------------
def ehrsql_controls(run_path, split='valid'):
    from bench.external import ehrsql, ehrsql_run
    rows = {r['id']: r for r in map(json.loads, Path(run_path).read_text().splitlines()) if r.get('id')}
    items = [q for q in ehrsql.load_split(split) if q['id'] in rows]
    real = {q['id']: ehrsql.result_key(q['reference_sql'] or ehrsql.NULL, tag='real') for q in items}
    score = lambda sql: {q['id']: ehrsql.reliability(real[q['id']], ehrsql.result_key(sql[q['id']], tag='pred')) for q in items}
    harness_pred = {q['id']: rows[q['id']]['prediction'] for q in items}
    ex = ehrsql_run.Examples()
    copy_pred = {q['id']: (ex.top(q['question'], 1) or [('', ehrsql.NULL)])[0][1] for q in items}
    hs, cs = score(harness_pred), score(copy_pred)
    never_abstain = score({q['id']: rows[q['id']]['raw_sql'] if rows[q['id']]['raw_sql'] != ehrsql.NULL else copy_pred[q['id']] for q in items})
    harness = {i: {'score': hs[i], 'calls': 2 if rows[i]['repaired'] else 1} for i in hs}
    controls = {'copy_nearest_train_sql': {i: {'score': cs[i], 'calls': 0} for i in cs},
                'abstain_all': {i: {'score': 1 if real[i] == ehrsql.NULL else 0, 'calls': 0} for i in real},
                'never_abstain_model_sql': {i: {'score': never_abstain[i], 'calls': harness[i]['calls']} for i in never_abstain}}
    return harness, controls


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--ehrsql', type=Path, help='an ehrsql_run.py result JSONL (harness arm)')
    p.add_argument('--split', default='valid', choices=('valid', 'train'))
    p.add_argument('--out', type=Path)
    a = p.parse_args(argv)
    if not a.ehrsql: p.error('give --ehrsql <run.jsonl>')
    harness, controls = ehrsql_controls(a.ehrsql, a.split)
    out = compare(harness, controls)
    text = json.dumps(out, indent=1)
    if a.out: a.out.write_text(text + '\n')
    print(text)
    return out


if __name__ == '__main__':
    main()
