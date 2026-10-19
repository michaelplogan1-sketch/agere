"""Paired analysis of a scored run -> artifacts/study/<run_id>/analysis.json.

    .venv/bin/python bench/study/analyze.py --run-dir artifacts/study/<run_id>

Primary comparisons use each arm's first pass (cold), paired on the questions both arms answered:
- accuracy: McNemar's exact test (two-sided binomial on the discordant pairs), A vs every other arm;
- cost per question, cost per correct answer, median and p90 latency, free-tier share: bootstrap 95% intervals over
  questions (percentile method, paired resampling of question indices, fixed seed);
- certificate validity: per certified tier, the observed error of the answers it certified vs the bound in
  config/agere/cascade_thresholds.json (with a one-sided Clopper-Pearson upper limit on the observed rate);
- repeatability: identical answers between pass 1 and pass 2 of each arm (arm A's pass 2 is warm by design);
- ablations: A minus each F arm on every metric;
- projection: measured cost per question x 1k / 10k / 100k questions a month, with the bootstrap interval.
Every metric block names the score file(s) it was computed from ('source', relative to the run folder).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.study import common  # noqa: E402

B = 2000
SEED = 20261018
VOLUMES = (1_000, 10_000, 100_000)


# --- statistics ---------------------------------------------------------------------------------------------------------------

def binom_cdf(k, n, p):
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p-value from the discordant counts b (A right, X wrong) and c (A wrong, X right)."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * binom_cdf(min(b, c), n, 0.5))


def clopper_pearson_upper(k, n, conf=0.95):
    """One-sided upper confidence limit on a binomial rate (k events in n)."""
    if n <= 0:
        return 1.0
    if k >= n:
        return 1.0
    lo, hi = k / n, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if binom_cdf(k, n, mid) > 1 - conf:
            lo = mid
        else:
            hi = mid
    return hi


def percentile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def median(xs):
    return percentile(xs, 0.5)


def _metric_fns():
    def mean_cost(rows):
        c = [r['cost_usd'] for r in rows if r['cost_usd'] is not None]
        return sum(c) / len(c) if c else None

    def cost_per_correct(rows):
        k = sum(r['correct'] for r in rows)
        c = [r['cost_usd'] for r in rows if r['cost_usd'] is not None]
        return sum(c) / k if k and c else None

    def lat(q):
        return lambda rows: percentile([r['seconds'] for r in rows if isinstance(r.get('seconds'), (int, float))], q)

    def free_share(rows):
        k = [r['free'] for r in rows if r['free'] is not None]
        return sum(k) / len(k) if k else None

    def accuracy(rows):
        return sum(r['correct'] for r in rows) / len(rows) if rows else None
    return {'accuracy': accuracy, 'cost_per_question': mean_cost, 'cost_per_correct': cost_per_correct,
            'latency_median': lat(0.5), 'latency_p90': lat(0.9), 'free_share': free_share}


METRICS = _metric_fns()


_SAMPLES = {}


def samples(n, b=B, seed=SEED):
    """The b resampled index lists for n questions (shared by every metric and arm, so comparisons are paired)."""
    key = (n, b, seed)
    if key not in _SAMPLES:
        rng = random.Random(seed)
        _SAMPLES[key] = [[rng.randrange(n) for _ in range(n)] for _ in range(b)]
    return _SAMPLES[key]


def bootstrap(rows, fn, *, b=B, seed=SEED):
    """Percentile 95% interval of fn over question resamples (None draws, e.g. no correct answers, are skipped)."""
    n = len(rows)
    if n == 0:
        return None
    vals = []
    for idx in samples(n, b, seed):
        v = fn([rows[i] for i in idx])
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return [percentile(vals, 0.025), percentile(vals, 0.975)]


def paired_bootstrap(ra, rx, fn, *, b=B, seed=SEED):
    """95% interval of fn(A) - fn(X) over paired question resamples."""
    n = len(ra)
    if n == 0:
        return None
    vals = []
    for idx in samples(n, b, seed):
        va, vx = fn([ra[i] for i in idx]), fn([rx[i] for i in idx])
        if va is not None and vx is not None:
            vals.append(va - vx)
    if not vals:
        return None
    return [percentile(vals, 0.025), percentile(vals, 0.975)]


def excludes_zero(ci):
    return bool(ci) and (ci[0] > 0 or ci[1] < 0)


def _r(v, nd=6):
    return None if v is None else round(v, nd)


# --- analysis -----------------------------------------------------------------------------------------------------------------

def load_scores(run_dir):
    run = json.loads((Path(run_dir) / 'run.json').read_text())
    scores = {}
    for p in run['passes']:
        f = common.arm_pass_dir(run_dir, p['arm'], p['pass']) / 'score.json'
        if f.is_file():
            scores[(p['arm'], p['pass'])] = json.loads(f.read_text())
    return run, scores


def src(arm, n):
    return f'{arm}/p{n}/score.json'


def arm_block(rows, arm, n):
    out = {'arm': arm, 'pass': n, 'source': src(arm, n), 'n': len(rows), 'correct': sum(r['correct'] for r in rows),
           'cost_unknown': sum(r['cost_usd'] is None for r in rows),
           'total_cost_usd': _r(sum(r['cost_usd'] for r in rows if r['cost_usd'] is not None)),
           'free': sum(r['free'] is True for r in rows)}
    for name, fn in METRICS.items():
        out[name] = {'value': _r(fn(rows)), 'ci95': [_r(x) for x in (bootstrap(rows, fn) or [])] or None}
    return out


def compare(ra, rx, arm_a, arm_x, n_a=1, n_x=1):
    """Paired comparison on the questions both runs hold, in the same order."""
    b = sum(a['correct'] and not x['correct'] for a, x in zip(ra, rx))
    c = sum(x['correct'] and not a['correct'] for a, x in zip(ra, rx))
    out = {'vs': arm_a, 'arm': arm_x, 'n_paired': len(ra), 'sources': [src(arm_a, n_a), src(arm_x, n_x)],
           'a_only_correct': b, 'x_only_correct': c, 'mcnemar_p': _r(mcnemar_exact(b, c), 6), 'differences': {}}
    for name, fn in METRICS.items():
        va, vx = fn(ra), fn(rx)
        ci = paired_bootstrap(ra, rx, fn)
        out['differences'][name] = {'a': _r(va), 'x': _r(vx), 'a_minus_x': _r(None if va is None or vx is None else va - vx),
                                    'ci95': [_r(v) for v in ci] if ci else None, 'excludes_zero': excludes_zero(ci)}
    ca, cx = METRICS['cost_per_question'](ra), METRICS['cost_per_question'](rx)
    out['cost_ratio_x_over_a'] = _r(cx / ca, 4) if ca and cx is not None else None
    return out


def headlines(comp, set_name, label_a, label_x):
    """Claims stated only where the interval (or McNemar test, for accuracy) excludes no difference."""
    out = []
    d = comp['differences']
    if comp['mcnemar_p'] is not None and comp['mcnemar_p'] < 0.05 and d['accuracy']['a_minus_x'] is not None:
        word = 'more' if d['accuracy']['a_minus_x'] > 0 else 'fewer'
        out.append({'text': f"{label_a} answered {abs(d['accuracy']['a_minus_x']) * 100:.1f} points {word} {set_name} questions "
                            f"correctly than {label_x} (McNemar p = {comp['mcnemar_p']:.3g}, {comp['n_paired']} paired questions).",
                    'metric': 'accuracy', 'sources': comp['sources']})
    for name, unit in (('cost_per_question', 'USD per question'), ('latency_median', 'median seconds'), ('latency_p90', 'p90 seconds'),
                       ('cost_per_correct', 'USD per correct answer')):
        m = d[name]
        if m['excludes_zero'] and m['a_minus_x'] is not None:
            word = 'lower' if m['a_minus_x'] < 0 else 'higher'
            out.append({'text': f"{label_a} {unit}: {m['a']:.4g} vs {m['x']:.4g} for {label_x} ({word}; difference 95% CI "
                                f"{m['ci95'][0]:.4g} to {m['ci95'][1]:.4g}) on {set_name}.", 'metric': name, 'sources': comp['sources']})
    return out


def certificates(scores, thresholds):
    """Per arm-pass and tier: observed error among certified answers vs the certificate's bound."""
    tiers = (thresholds.get('tiers') or {})
    out = []
    for (arm, n), s in sorted(scores.items()):
        groups = {}
        for r in s['questions']:
            if r.get('certified_tier') and r.get('scored'):
                groups.setdefault(r['certified_tier'], []).append(r)
        for tier, rows in sorted(groups.items()):
            k = sum(not r['correct'] for r in rows)
            rec = tiers.get(tier) or {}
            bound = rows[0].get('certified_bound') if rows[0].get('certified_bound') is not None else rec.get('bound')
            upper = clopper_pearson_upper(k, len(rows), 1 - float(rec.get('delta') or 0.05))
            out.append({'arm': arm, 'pass': n, 'tier': tier, 'source': src(arm, n), 'certified_answers': len(rows), 'errors': k,
                        'observed_error': _r(k / len(rows), 4), 'observed_upper': _r(upper, 4),
                        'confidence': 1 - float(rec.get('delta') or 0.05), 'alpha': rec.get('alpha'), 'bound': bound,
                        'within_bound': None if bound is None else k / len(rows) <= float(bound),
                        'note': 'few certified answers: the observed rate is weak evidence either way' if len(rows) < 30 else None})
    return out


def repeatability(scores):
    out = []
    arms = sorted({a for a, _ in scores})
    for arm in arms:
        if (arm, 1) in scores and (arm, 2) in scores:
            r1 = {(r['set'], r['id']): r for r in scores[(arm, 1)]['questions']}
            r2 = {(r['set'], r['id']): r for r in scores[(arm, 2)]['questions']}
            keys = sorted(set(r1) & set(r2))
            same = sum(r1[k]['signature'] == r2[k]['signature'] for k in keys)
            same_c = sum(r1[k]['correct'] == r2[k]['correct'] for k in keys)
            out.append({'arm': arm, 'sources': [src(arm, 1), src(arm, 2)], 'n_paired': len(keys), 'identical_answers': same,
                        'identical_share': _r(same / len(keys), 4) if keys else None, 'same_correctness': same_c,
                        'warm_second_pass': arm == common.WARM_ARM,
                        'changed': [f'{k[0]}:{k[1]}' for k in keys if r1[k]['signature'] != r2[k]['signature']][:50]})
    return out


def analyze(run_dir):
    run_dir = Path(run_dir)
    run, scores = load_scores(run_dir)
    thresholds = json.loads(common.THRESHOLDS.read_text()) if common.THRESHOLDS.is_file() else {}
    arms = [a for a in common.ARM_ORDER if (a, 1) in scores]
    sets = sorted({r['set'] for s in scores.values() for r in s['questions']})
    if len(sets) > 1:
        sets.append('all')
    labels = {a: f"{a} ({common.ARMS[a]['label']})" for a in common.ARMS}
    out = {'schema': 'agere-study-analysis/1', 'run_id': run['run_id'], 'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'run_source': 'run.json', 'primary_pass': 1, 'bootstrap': {'resamples': B, 'seed': SEED, 'method': 'percentile, paired'},
           'git': run.get('git'), 'pricing_sha256': run.get('pricing_sha256'), 'questions': run.get('questions'),
           'arms': {a: run['arms'].get(a) for a in arms}, 'sets': {}, 'headlines': []}

    def rows_of(arm, n, set_name):
        rs = scores[(arm, n)]['questions']
        return {(r['set'], r['id']): r for r in rs if set_name == 'all' or r['set'] == set_name}

    for set_name in sets:
        block = {'arms': {}, 'comparisons': {}, 'ablations': {}, 'warm': None, 'projection': {}}
        for arm in arms:
            rs = list(rows_of(arm, 1, set_name).values())
            if rs:
                block['arms'][arm] = arm_block(rs, arm, 1)
        if 'A' in arms:
            base = rows_of('A', 1, set_name)
            for arm in arms:
                if arm == 'A':
                    continue
                other = rows_of(arm, 1, set_name)
                keys = sorted(set(base) & set(other))
                if not keys:
                    continue
                comp = compare([base[k] for k in keys], [other[k] for k in keys], 'A', arm)
                target = block['ablations'] if arm.startswith('F') else block['comparisons']
                comp['component'] = common.ARMS[arm]['label'] if arm.startswith('F') else None
                target[arm] = comp
                if not arm.startswith('F'):
                    out['headlines'] += [{**h, 'set': set_name} for h in headlines(comp, set_name, labels['A'], labels[arm])]
            if ('A', 2) in scores:
                warm = rows_of('A', 2, set_name)
                keys = sorted(set(base) & set(warm))
                if keys:
                    block['warm'] = compare([warm[k] for k in keys], [base[k] for k in keys], 'A', 'A', 2, 1)
                    block['warm']['note'] = "arm A pass 2 (warm caches) minus pass 1 (cold): 'a' = warm, 'x' = cold"
        for arm, ab in block['arms'].items():
            cpq = ab['cost_per_question']
            block['projection'][arm] = {'source': ab['source'], 'cost_per_question': cpq['value'],
                                        'monthly_usd': {str(v): {'value': _r(cpq['value'] * v, 2) if cpq['value'] is not None else None,
                                                                 'ci95': [_r(x * v, 2) for x in cpq['ci95']] if cpq['ci95'] else None}
                                                        for v in VOLUMES}}
        out['sets'][set_name] = block
    out['certificates'] = certificates(scores, thresholds)
    out['repeatability'] = repeatability(scores)
    out['passes'] = [{'arm': a, 'pass': n, 'source': src(a, n), **s['summary'], 'ledger': s.get('ledger')}
                     for (a, n), s in sorted(scores.items())]
    out['total_spend_usd'] = _r(sum(s['summary']['ledger_cost_usd'] or 0 for s in scores.values()), 6)
    (run_dir / 'analysis.json').write_text(json.dumps(out, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', type=Path, required=True)
    a = ap.parse_args()
    out = analyze(a.run_dir)
    print(f"analysis.json written ({len(out['headlines'])} headline claims; total spend ${out['total_spend_usd']})")


if __name__ == '__main__':
    main()
