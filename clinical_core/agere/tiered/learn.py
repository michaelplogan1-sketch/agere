"""Stage 7: the learning loop and its promotion gate.

Every answered question is already a ledger row (question, tier, recipe, result, pass/fail): that is the training
log. A recipe the frontier model wrote for a live question becomes a *provisional* bottle and answers nothing.
promote() runs the full eval set twice, with and without the provisional bottles, and promotes them only if the
eval is no worse on accuracy and no worse on frontier use. Otherwise they are rejected and the reason is recorded.
Frontier use is measured in frontier calls, because the dollar cost of an identical call varies with prompt caching.

    python -m clinical_core.agere.tiered.learn ask "<question>"      # answer a live question; bottle a frontier recipe as provisional
    python -m clinical_core.agere.tiered.learn promote               # run the gate over all provisional bottles
    python -m clinical_core.agere.tiered.learn training              # write the training log as JSON lines
"""
import json
import sys
import time
from pathlib import Path

from . import bottles as bottle_store, evaluate, ledger as ledger_store, router

TIERS = ('bottles', 'recipes', 'system1', 'slm', 'llm')
OUT = Path('artifacts/agere-tiered-core')


def _s1_ledger():
    import os
    os.environ.pop('ANTHROPIC_API_KEY', None)
    from run_integrated_stack import keys
    keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
    return os.environ.get('AGERE_TIERED_S1_LEDGER', str(OUT / 'system-one-ledger.sqlite3'))


def ask(question):
    bottles = bottle_store.Bottles()
    outcome = router.answer(question, tiers=TIERS, bottles=bottles, bottle_new=True, s1_ledger=_s1_ledger())
    ledger_store.Ledger().write('live', question, outcome, config={'tiers': list(TIERS)})
    return outcome


def promote():
    bottles = bottle_store.Bottles()
    candidates = [b for b in bottles.all() if b['status'] == 'provisional']
    if not candidates: return {'decision': 'nothing_to_promote', 'candidates': 0}
    s1 = _s1_ledger()
    base_id, base = evaluate.run('gate-current', TIERS, s1_ledger=s1, promote_by_eval=False)
    cand_id, cand = evaluate.run('gate-with-candidates', TIERS, s1_ledger=s1, bottle_statuses=('promoted', 'provisional'), promote_by_eval=False)
    score = lambda rows: {'correct': sum(r['correct'] for r in rows), 'frontier_calls': sum(r['frontier_calls'] for r in rows),
                          'usd': round(sum(r['usd'] for r in rows), 4)}
    b, c = score(base), score(cand)
    ids = {x['id'] for x in candidates}
    used_wrong = sorted({r['bottle_id'] for r in cand if r['bottle_id'] in ids and not r['correct']})
    used_right = sorted({r['bottle_id'] for r in cand if r['bottle_id'] in ids and r['correct']} - set(used_wrong))
    passed = c['correct'] >= b['correct'] and c['frontier_calls'] <= b['frontier_calls']
    decisions = []
    for bottle in candidates:
        if bottle['id'] in used_wrong:
            status, reason = 'rejected', 'gave a wrong answer to an eval question'
        elif not passed:
            status, reason = 'rejected', f"full eval got worse with the candidates: correct {b['correct']}->{c['correct']}, frontier calls {b['frontier_calls']}->{c['frontier_calls']}"
        else:
            status, reason = 'promoted', ('answered an eval question correctly; ' if bottle['id'] in used_right else 'no eval question exercises this shape; ') + \
                f"full eval no worse: correct {b['correct']}->{c['correct']}, frontier calls {b['frontier_calls']}->{c['frontier_calls']}"
        bottles.set_status(bottle['id'], status, reason)
        decisions.append({'bottle': bottle['id'], 'template': bottle['template'], 'status': status, 'reason': reason})
    out = {'decision': 'promoted' if passed else 'rejected', 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'current': {'run': base_id, **b},
           'with_candidates': {'run': cand_id, **c}, 'candidates': decisions}
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / 'promotion-log.jsonl', 'a') as f: f.write(json.dumps(out) + '\n')
    return out


def training():
    led = ledger_store.Ledger()
    cur = led.db.execute('SELECT run, question, final_tier, status, validation, recipe_json, result_rows, eval_id, correct, usd, tokens FROM question ORDER BY id')
    names = [d[0] for d in cur.description]
    path = OUT / 'training-log.jsonl'
    rows = 0
    with open(path, 'w') as f:
        for r in cur.fetchall():
            f.write(json.dumps(dict(zip(names, r))) + '\n'); rows += 1
    return {'path': str(path), 'rows': rows}


if __name__ == '__main__':
    if sys.argv[1] == 'ask':
        o = ask(sys.argv[2])
        print(json.dumps({'status': o['status'], 'final_tier': o['final_tier'], 'rows': (o.get('rows') or [])[:5], 'bottled': o.get('bottled'),
                          'escalations': o['escalations'], 'usd': round(sum(s.get('usd', 0) for s in o['steps']), 4)}, indent=1))
    elif sys.argv[1] == 'promote': print(json.dumps(promote(), indent=1))
    elif sys.argv[1] == 'training': print(json.dumps(training()))
