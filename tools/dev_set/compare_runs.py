"""Compare a paraphrase run with a dev run (both written by run_inprocess --out).

    .venv/bin/python -m tools.dev_set.compare_runs PARAPHRASE.json DEV.json [--list PREFIX] [--wrong PREFIX]

Prints, per bucket, how many paraphrases are correct / wrong / refused, and how many of the paraphrases of DEV-correct questions stay
correct (the generalization measure), plus the paraphrases that are wrong on a question the dev wording gets right (new wrong answers).
"""
import argparse
import json
from collections import Counter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paraphrase')
    ap.add_argument('dev')
    ap.add_argument('--list', default=None, help='list eligible paraphrases (dev wording correct) that are not correct, for ids with this prefix')
    ap.add_argument('--wrong', default=None, help='list WRONG paraphrases for ids with this prefix')
    a = ap.parse_args()
    dev = {r['id']: r for r in json.load(open(a.dev))}
    par = json.load(open(a.paraphrase))
    tot = Counter()
    for r in par:
        o = dev.get(r['id'].rsplit('-p', 1)[0])
        elig = bool(o) and o['verdict'] == 'correct'
        tot[(r['bucket'], 'all', r['verdict'])] += 1
        if elig:
            tot[(r['bucket'], 'elig', r['verdict'])] += 1
        if a.list is not None and elig and r['verdict'] != 'correct' and r['id'].startswith(a.list):
            print(r['id'], r['verdict'], '|', r['asked'][:110], '|', (r.get('read_as') or '')[:110], '->', r['headline'][:150])
        if a.wrong is not None and r['verdict'] == 'WRONG' and r['id'].startswith(a.wrong):
            print(r['id'], 'dev=' + (o['verdict'] if o else '?'), '|', r['asked'][:110], '->', r['headline'][:150])
    buckets = sorted({k[0] for k in tot})
    print('\nbucket                              n   correct  wrong  refused | eligible  stay-correct  new-wrong')
    ta = Counter()
    for b in buckets:
        n = sum(tot[(b, 'all', v)] for v in ('correct', 'WRONG', 'refused'))
        el = sum(tot[(b, 'elig', v)] for v in ('correct', 'WRONG', 'refused'))
        row = (n, tot[(b, 'all', 'correct')], tot[(b, 'all', 'WRONG')], tot[(b, 'all', 'refused')], el, tot[(b, 'elig', 'correct')], tot[(b, 'elig', 'WRONG')])
        ta.update(dict(enumerate(row)))
        print(f'{b:34} {row[0]:3} {row[1]:7} {row[2]:6} {row[3]:8} | {row[4]:8} {row[5]:12} {row[6]:9}')
    print(f"{'TOTAL':34} {ta[0]:3} {ta[1]:7} {ta[2]:6} {ta[3]:8} | {ta[4]:8} {ta[5]:12} {ta[6]:9}   stay-correct {100 * ta[5] / max(1, ta[4]):.1f}%")


if __name__ == '__main__':
    main()
