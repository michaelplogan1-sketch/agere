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
    from clinical_core.agere.providers import load_keys as keys
    keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
    return os.environ.get('AGERE_TIERED_S1_LEDGER', str(OUT / 'system-one-ledger.sqlite3'))


def ask(question):
    bottles = bottle_store.Bottles()
    outcome = router.answer(question, tiers=TIERS, bottles=bottles, bottle_new=True, s1_ledger=_s1_ledger())
    ledger_store.Ledger().write('live', question, outcome, config={'tiers': list(TIERS)})
    return outcome


LOCK = OUT / 'auto-learn.lock'


def auto():
    """The whole loop with no manual step: a second model checks each provisional recipe, then the promotion gate runs.
    One run at a time (lock file); a lock older than two hours is treated as abandoned."""
    import os
    OUT.mkdir(parents=True, exist_ok=True)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 7200: return {'decision': 'already_running'}
    LOCK.write_text(str(os.getpid()))
    try:
        from . import curriculum
        checked = curriculum.confirm(main=True)
        gate = promote()
        # Each recipe the gate just promoted is re-checked by a different model before it builds a track record of its own.
        new = {c['bottle'] for c in gate.get('candidates', []) if c['status'] == 'promoted'}
        from . import upkeep
        kept = upkeep.sweep(only=new, workers=4) if new else {'recipes re-checked': 0}
        return {'second_model': checked, 'gate': gate, 'upkeep': kept}
    finally:
        LOCK.unlink(missing_ok=True)


def spawn_auto():
    """Start auto() as a separate process so the question that triggered it is not held up. Never raises."""
    import os, subprocess
    try:
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 7200: return False
        OUT.mkdir(parents=True, exist_ok=True)
        hosted_api = os.environ.get('AGERE_CLAUDE_TRANSPORT') == 'api' and os.environ.get('AGERE_HOSTED') == '1'  # only the hosted server keeps its key
        env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY' or hosted_api}
        with open(OUT / 'auto-learn.log', 'a') as log:
            subprocess.Popen([sys.executable, '-B', '-m', 'clinical_core.agere.tiered.learn', 'auto'], stdout=log, stderr=log, env=env, start_new_session=True)
        return True
    except Exception:
        return False


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
    confirmed = {b['id']: bool((b['provenance'] or {}).get('confirmed_by_second_model')) for b in bottles.full()}
    from . import upkeep
    recipe = {b['id']: b for b in bottles.full()}   # sql and params, for the equivalence step
    decisions = []
    for bottle in candidates:
        if bottle['id'] in used_wrong:
            status, reason = 'rejected', 'gave a wrong answer to an eval question'
        elif bottle['id'] not in used_right and not confirmed.get(bottle['id']):
            # No eval question exercises this shape and no second model reproduced its result: nothing shows it is right.
            status, reason = 'provisional', 'not promoted: no eval question exercises this shape and it is not confirmed by a second model'
        elif not passed:
            status, reason = 'rejected', f"full eval got worse with the candidates: correct {b['correct']}->{c['correct']}, frontier calls {b['frontier_calls']}->{c['frontier_calls']}"
        elif (eq := upkeep.gate(bottles, recipe.get(bottle['id'], bottle))) and eq['verdict'].startswith('differ'):
            # a counterexample: another parameter value, or NULLs in the data, make it disagree with an independent query
            status, reason = 'provisional', f"not promoted: the bounded equivalence check found a counterexample ({eq['verdict']}): {json.dumps(eq.get('counterexample'))[:300]}"
        else:
            status, reason = 'promoted', ('answered an eval question correctly; ' if bottle['id'] in used_right else 'no eval question exercises this shape; ') + \
                f"full eval no worse: correct {b['correct']}->{c['correct']}, frontier calls {b['frontier_calls']}->{c['frontier_calls']}"
        if status != 'provisional': bottles.set_status(bottle['id'], status, reason)
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
    elif sys.argv[1] == 'auto': print(json.dumps(auto(), indent=1, default=str))
    elif sys.argv[1] == 'promote-and-check':
        out = promote(); new = {c['bottle'] for c in out.get('candidates', []) if c['status'] == 'promoted'}
        from . import upkeep
        print(json.dumps({'gate': {k: v for k, v in out.items() if k != 'candidates'}, 'promoted': len(new), 'upkeep': upkeep.sweep(only=new) if new else None}, indent=1, default=str))
