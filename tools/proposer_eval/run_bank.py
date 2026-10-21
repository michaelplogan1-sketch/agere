"""Dev-bank measurement for the shared planner's proposer cascade, in process (no server).

Sends question text only (chains keep their conversation) through conversation.handle_compose, the same handler as
/api/stack/compose, against a stack directory the caller cloned cold. Grades with the dev bank's own score.py
(lenient: first cell of a one-row reference). Never pointed at the sealed bank.

Usage: python tools/proposer_eval/run_bank.py --stack DIR --out DIR [--allow-live] [--ids A,B] [--limit N]
Resumable: items already in OUT/raw are skipped.
"""
import argparse
import importlib.util
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BANK_DIR = Path('/Users/mikel/agere-eval-bank-2')
assert 'sealed' not in str(BANK_DIR)


def order(items, seed):
    rng = random.Random(seed)
    solo = [i for i in items if i['kind'] != 'chain_turn']
    rng.shuffle(solo)
    chains = {}
    for i in items:
        if i['kind'] == 'chain_turn':
            chains.setdefault(i['chain']['id'], []).append(i)
    keys = sorted(chains)
    rng.shuffle(keys)
    return solo + [t for k in keys for t in sorted(chains[k], key=lambda t: t['chain']['turn'])]


def extract(resp):
    """The value a user would read: count/value scalar, table rows, or None for a decline."""
    a = resp.get('answer') or {}
    if a.get('status') not in ('answered', 'partial'):
        return None, True
    if 'comparison' in a:
        c = a['comparison']
        return [[c['earlier']['value'], c['later']['value'], c['change']]], False
    if 'count' in a and a.get('answer_type') != 'table':
        return a['count'], False
    if 'value' in a and not isinstance(a['value'], (dict, list)):
        return a['value'], False
    if isinstance(a.get('table'), list) and a['table']:
        return [list(r.values()) if isinstance(r, dict) else r for r in a['table']], False
    for s in a.get('sections') or []:
        v, _ = extract({'answer': {**s['answer'], 'status': 'answered'}})
        if v is not None:
            return v, False
    import re
    m = re.search(r'-?\$?[\d,]*\.?\d+', str(a.get('headline') or ''))
    return (m.group(0) if m else None), False


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stack', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--allow-live', action='store_true')
    p.add_argument('--ids')
    p.add_argument('--limit', type=int)
    p.add_argument('--seed', type=int, default=20261021)
    a = p.parse_args()
    out = Path(a.out); raw = out / 'raw'; raw.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('bank_score', BANK_DIR / 'score.py')
    score = importlib.util.module_from_spec(spec); spec.loader.exec_module(score)
    items = json.loads((BANK_DIR / 'bank.json').read_text())['items']
    seq = order(items, a.seed)
    if a.ids:
        wanted = set(a.ids.split(','))
        seq = [i for i in seq if i['id'] in wanted or (i['kind'] == 'chain_turn' and i['chain']['id'] in wanted)]
    if a.limit:
        seq = seq[:a.limit]
    if a.allow_live:
        from run_integrated_stack import keys
        keys()
    from clinical_core.agere.clinical.stack_runtime import Stack
    from clinical_core.agere.clinical import conversation
    from clinical_core.agere.clinical.question_input import failure
    stack = Stack(Path(a.stack), allow_live=a.allow_live)
    for n, it in enumerate(seq, 1):
        target = raw / f"{it['id']}.json"
        if target.exists():
            continue
        body = {'question': it['question']}
        if it['kind'] == 'chain_turn':
            body['conversation_id'] = f"dev-{it['chain']['id']}-{a.seed}"
        t = time.time()
        try:
            resp = conversation.handle_compose(stack, body)
        except Exception as exc:  # noqa: BLE001 - a decline is an answer here
            usage = getattr(exc, 'planner_receipt', {}) or {}
            resp = {'answer': {**failure(exc), 'headline': str(exc)[:400]}, 'error': type(exc).__name__,
                    'new_paid_api_calls': usage.get('new_paid_api_calls', 0),
                    'planning_economics': {'known_api_estimate_usd': usage.get('known_api_estimate_usd', 0)}}
        secs = time.time() - t
        rec = {'id': it['id'], 'position': n, 'seconds': round(secs, 2), 'question': it['question'],
               'response': {k: resp.get(k) for k in ('answer', 'route', 'new_paid_api_calls', 'planning_economics', 'error')},
               'interpretation': ((resp.get('planner_receipt') or {}).get('interpretation') or {}),
               'proposers': (resp.get('planner_receipt') or {}).get('proposers')}
        target.write_text(json.dumps(rec, default=str))
        ans = rec['response']['answer'] or {}
        print(f"{n:3d}/{len(seq)} {it['id']:6s} {ans.get('status')} "
              f"by={rec['interpretation'].get('interpreter')} calls={resp.get('new_paid_api_calls')} "
              f"usd={(resp.get('planning_economics') or {}).get('known_api_estimate_usd')} {secs:.1f}s | "
              f"{str(ans.get('headline') or '')[:80]}", flush=True)
    # Grade every raw record present.
    by_id = {i['id']: i for i in items}
    rows, grades = [], Counter()
    for f in sorted(raw.glob('*.json')):
        rec = json.loads(f.read_text())
        value, unknown = extract(rec['response'])
        usd = (rec['response'].get('planning_economics') or {}).get('known_api_estimate_usd') or 0
        ans = {'id': rec['id'], 'answer': value, 'unknown': unknown, 'cost_usd': usd, 'latency_s': rec['seconds'],
               'model_calls': rec['response'].get('new_paid_api_calls') or 0,
               'interpreter': rec['interpretation'].get('interpreter')}
        g = score.grade(by_id[rec['id']], ans)
        grades[g] += 1
        rows.append({**ans, 'grade': g, 'kind': by_id[rec['id']]['kind']})
    (out / 'graded.jsonl').write_text(''.join(json.dumps(r, default=str) + '\n' for r in rows))
    answerable = [r for r in rows if r['kind'] != 'unanswerable']
    costs = [r['cost_usd'] for r in rows]
    summary = {'items': len(rows), 'grades': dict(grades),
               'answered': sum(1 for r in rows if not r['unknown']),
               'answerable': len(answerable),
               'correct': sum(1 for r in answerable if r['grade'] in ('correct', 'correct_alt')),
               'wrong_answerable': sum(1 for r in answerable if r['grade'] == 'wrong'),
               'not_understood_answerable': sum(1 for r in answerable if r['grade'] == 'unknown'),
               'unanswerable_pass': f"{grades['unknown_pass']} of {sum(1 for r in rows if r['kind'] == 'unanswerable')}",
               'cost_total_usd': round(sum(costs), 4), 'cost_mean_usd': round(sum(costs) / max(1, len(costs)), 4),
               'latency_mean_s': round(sum(r['latency_s'] for r in rows) / max(1, len(rows)), 2),
               'by_interpreter': dict(Counter(f"{r['interpreter']}:{r['grade']}" for r in rows))}
    (out / 'summary.json').write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
