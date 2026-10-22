"""Measure the cross-family position-swapped judge (clinical/judge.py) on labelled pairs from saved non-blind bench rows.

  .venv/bin/python tools/calibration/measure_judge.py [--limit N] [--write]

Pairs (which candidate answers the question is known):
  swap   a right Agere answer (executed plan + result) against the right answer to a DIFFERENT question of the same answer type
  hard   a wrong Agere answer against the reference answer for the same question (reference rendered as its result only)
Blind (heldout*) rows are never used. Calls Jev and Perplexity (4 typed calls per pair, cached); --write saves
config/agere/judge_accuracy.json, which every judge verdict then carries.
"""
import argparse, json, random, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools import certify_gates as CG  # noqa: E402
from clinical_core.agere.clinical import judge as J  # noqa: E402


def candidate(row, answer=None):
    resp = row.get('response') or {}
    return {'name': 'agere', 'graph': resp.get('graph') or {}, 'result': {'answer': answer or row['answer']}}


def reference(row):
    e = row['expected'] or {}
    shown = {k: e.get(k) for k in ('answer_type', 'count', 'value') if e.get(k) is not None}
    if e.get('table'): shown['rows'] = e['table'][:3]
    return f"Reference computation result: {json.dumps(shown, default=str)[:600]}"


def pairs(limit=None, seed=7):
    pool, _, blind = CG.bench_rows(CG.results_dir())
    rows = [r for r in pool if CG.asserted(r.get('answer')) and CG.norm(r['question']) not in blind]
    right = [r for r in rows if not r['wrong']]
    rnd = random.Random(seed)
    out = []
    for r in right:
        others = [o for o in right if o is not r and (o['answer'] or {}).get('answer_type') == (r['answer'] or {}).get('answer_type')]
        if not others: continue
        o = rnd.choice(others)
        a, b = candidate(r), candidate(o)
        flip = rnd.random() < 0.5
        out.append({'question': r['question'], 'a': b if flip else a, 'b': a if flip else b, 'right': 'B' if flip else 'A', 'set': 'swap'})
    for r in rows:
        if r['wrong'] and r.get('expected'):
            out.append({'question': r['question'], 'a': candidate(r), 'b': reference(r), 'right': 'B', 'set': 'hard'})
    rnd.shuffle(out)
    return out[:limit] if limit else out


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument('--limit', type=int); ap.add_argument('--write', action='store_true')
    a = ap.parse_args(argv)
    from run_integrated_stack import keys
    keys()
    from clinical_core.agere.gateway import BudgetLedger, default_ledger_path
    ledger = BudgetLedger(default_ledger_path())
    try:
        ps = pairs(a.limit)
        out = J.measure(ps, ledger=ledger)
        for name in ('swap', 'hard'):
            sub = [p for p in ps if p['set'] == name]
            if sub: out[f'set_{name}'] = J.measure(sub, ledger=ledger)  # cached: free
    finally:
        ledger.close()
    out.update(measured_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), set='saved non-blind bench rows (swap + hard pairs)')
    print(json.dumps({k: v for k, v in out.items() if k not in ('set_swap', 'set_hard')}, indent=1))
    for name in ('swap', 'hard'):
        s = out.get(f'set_{name}') or {}
        print(name, {k: s.get(k) for k in ('n', 'decided', 'accuracy', 'coverage', 'split_rate')})
    if a.write: J.ACCURACY_FILE.write_text(json.dumps(out, indent=1) + '\n')
    return out


if __name__ == '__main__':
    main()
