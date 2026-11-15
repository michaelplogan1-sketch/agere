"""Recipe upkeep: each saved recipe's track record, and whether it keeps answering.

Promotion is gated; life after promotion was not. A promoted recipe is replayed with no model, so a wrong one repeats
silently. This module keeps a record for each recipe (bottles.bottle_event) and acts on it with one rule.

  use / near_use   the recipe answered a question, exactly or through the near-match rung (recorded by the router)
  check_agree      a different model wrote its own query for a question the recipe answers, and the rows agreed on the
                   full data and on every patient subset
  check_differ     they did not agree
  misapplied       the recipe answered a question it does not fit (found by the wrong-match test)

The rule, chosen from the research on library drift, which found retiring on thin evidence worse than not retiring at all:
  * one disagreement and no agreement -> a tie-break by a reader of a different kind (the frontier model);
  * two or more disagreements and more disagreements than agreements -> suspended: the recipe stops answering, is never
    deleted, and goes back to answering only if later checks agree more often than they differ;
  * a recipe misapplied twice is no longer offered by the near-match rung (bottles.near_blocked); exact matches still use it.

    python -m clinical_core.agere.tiered.upkeep backfill
    python -m clinical_core.agere.tiered.upkeep sweep [limit] [workers]
    python -m clinical_core.agere.tiered.upkeep report
"""
import json
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import bottles as bottle_store, store

OUT = Path('artifacts/agere-tiered-core/upkeep')
USE_CHECKS = 2          # besides its own question, re-check a recipe on up to this many questions it actually answered


def decide(record, status):
    """The rule. record: {kind: count}; status: the recipe's status now. -> (new status or None to leave it, reason, tie_break_needed)."""
    agree, differ = record.get('check_agree', 0), record.get('check_differ', 0)
    if status == 'promoted' and differ >= 2 and differ > agree:
        return 'suspended', f'independent re-checks disagreed {differ} times and agreed {agree}', False
    if status == 'suspended' and agree > differ:
        return 'promoted', f'later re-checks agreed {agree} times and disagreed {differ}', False
    return None, '', status == 'promoted' and differ == 1 and agree == 0


def _params_for_second(recipe):
    return {k: (int(v['value']) if v.get('type') == 'int' else str(v.get('value'))) for k, v in (recipe.get('params') or {}).items()}


def check(bottle, question, params, model):
    """One independent re-check. -> ('agree' | 'differ' | 'skipped', detail). Opens its own connections (runs in a thread)."""
    from . import curriculum, evaluate, models
    db = store.connect()
    try:
        saved = store.run(bottle['sql'], params, db)[1]
        second, step = models.llm_recipe(question, model=model)
        if second is None or second.get('read'):
            return 'skipped', {'why': 'the second model wrote no query', 'model': model, 'usd': step.get('usd', 0.0)}
        other = _params_for_second(second)
        rows = store.run(second['sql'], other, db)[1]
        detail = {'model': model, 'usd': step.get('usd', 0.0), 'second_sql': second['sql'][:600]}
        if not (evaluate.correct(saved, rows) and evaluate.correct(rows, saved)):
            return 'differ', {**detail, 'where': 'full data', 'saved_rows': len(saved), 'second_rows': len(rows), 'saved_first': str(saved[:2])[:200], 'second_first': str(rows[:2])[:200]}
        ok, parts = curriculum.agree_on_subsets(bottle['sql'], params, second['sql'], other, question)
        return ('agree', detail) if ok else ('differ', {**detail, 'where': 'a patient subset', 'subsets': [p for p in parts if not p['same']][:2]})
    except Exception as exc:
        if 'limit' in str(exc).lower(): raise
        return 'skipped', {'why': f'{type(exc).__name__}: {str(exc)[:160]}', 'model': model}
    finally:
        db.close()


def backfill(ledger_path='artifacts/agere-tiered-core/ledger.sqlite3'):
    """Past answers by saved recipes, from the question ledger, into the track record (once; marked as backfilled)."""
    bottles = bottle_store.Bottles()
    if bottles.db.execute("SELECT 1 FROM bottle_event WHERE detail_json LIKE '%backfilled%' LIMIT 1").fetchone(): return {'already': True}
    led, n = sqlite3.connect(ledger_path), Counter()
    for at, run, question, tier, recipe_json in led.execute("SELECT at, run, question, final_tier, recipe_json FROM question WHERE status = 'answered' AND final_tier IN ('bottles', 'near')"):
        recipe = json.loads(recipe_json or '{}')
        if not recipe.get('bottle_id'): continue
        bottles.event(recipe['bottle_id'], 'use' if tier == 'bottles' else 'near_use', question, recipe.get('params'), {'backfilled': True, 'run': run}, at=at)
        n[tier] += 1
    return dict(n)


def sweep(limit=None, workers=4, models_=None):
    """Re-check promoted recipes, those never checked and most used first: each on its own question and up to USE_CHECKS
    questions it answered, by the small model; a lone disagreement gets a tie-break by the frontier model. Then the rule."""
    from . import curriculum, models
    curriculum._keys(); curriculum._preload()
    first, tie = models_ or (models.SLM, models.LLM)
    bottles = bottle_store.Bottles()
    records = bottles.records()
    todo = [b for b in bottles.full() if b['status'] in ('promoted', 'suspended') and not b.get('read') and (b.get('provenance') or {}).get('question')]
    checked = lambda b: records.get(b['id'], {}).get('check_agree', 0) + records.get(b['id'], {}).get('check_differ', 0)
    used = lambda b: records.get(b['id'], {}).get('use', 0) + records.get(b['id'], {}).get('near_use', 0)
    todo.sort(key=lambda b: (checked(b) > 0, -used(b), b['id']))
    todo = todo[:limit]

    def questions(b):
        own = b['provenance']['question']
        hit = bottles_local().match(own, statuses=('promoted', 'suspended'))
        out = [(own, hit[1])] if hit and hit[0]['id'] == b['id'] else []
        out += [(q, p) for q, p in bottles_local().uses(b['id']) if q.lower() != own.lower()][:USE_CHECKS]
        return out

    import threading
    local = threading.local()
    def bottles_local():
        if not hasattr(local, 'b'): local.b = bottle_store.Bottles()
        return local.b

    def work(b):
        done = []
        for q, p in questions(b):
            verdict, detail = check(b, q, p, first)
            done.append((q, p, verdict, detail))
        return b, done

    started, out, lines = time.monotonic(), Counter(), []
    with ThreadPoolExecutor(int(workers)) as pool:
        results = list(pool.map(work, todo))
    for b, done in results:
        if not done: out['no question selects this recipe any more'] += 1; continue
        for q, p, verdict, detail in done:
            if verdict != 'skipped': bottles.event(b['id'], 'check_' + verdict, q, p, detail)
            out['checks ' + verdict] += 1
        record = bottles.record(b['id'])
        new, why, tie_break = decide(record, b['status'])
        if tie_break:
            q, p, _, _ = next(x for x in done if x[2] == 'differ')
            verdict, detail = check(b, q, p, tie)
            if verdict != 'skipped': bottles.event(b['id'], 'check_' + verdict, q, p, {**detail, 'tie_break': True})
            out['tie-breaks ' + verdict] += 1
            record = bottles.record(b['id'])
            new, why, _ = decide(record, b['status'])
        if new:
            bottles.set_status(b['id'], new, why)
            out[f'{b["status"]} -> {new}'] += 1
            differ = next((d for _, _, v, d in done if v == 'differ'), {})
            lines.append(f"{b['id']} {b['status']} -> {new}: {why} | {b['template']}\n   saved:  {b['sql'][:300]}\n   second: {str(differ.get('second_sql'))[:300]}")
        out['recipes re-checked'] += 1
    summary = {**dict(out), 'seconds': round(time.monotonic() - started, 1), 'usd': round(sum(d.get('usd', 0.0) for _, done in results for *_, d in done), 4)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"sweep-{time.strftime('%Y%m%d-%H%M%S')}.txt").write_text(json.dumps(summary, indent=1) + '\n\n' + '\n\n'.join(lines) + '\n')
    return summary


def report(ledger_path='artifacts/agere-tiered-core/ledger.sqlite3'):
    """Store-level standing: how many recipes are checked, agree, are suspended or blocked for near-match; and the share of
    answers given by saved recipes beside how many of those came from a recipe now known to disagree."""
    bottles = bottle_store.Bottles()
    records, full = bottles.records(), bottles.full()
    status = Counter(b['status'] for b in full)
    checked = [b for b in full if records.get(b['id'], {}).get('check_agree', 0) + records.get(b['id'], {}).get('check_differ', 0)]
    doubtful = {b['id'] for b in full if records.get(b['id'], {}).get('check_differ', 0)}
    uses = Counter()
    for bid, r in records.items():
        uses['answers by saved recipes'] += r['use'] + r['near_use']
        if bid in doubtful: uses['of those, by a recipe a re-check disagreed with'] += r['use'] + r['near_use']
    led = sqlite3.connect(ledger_path)
    total = led.execute("SELECT COUNT(*) FROM question WHERE status = 'answered'").fetchone()[0]
    by_saved = led.execute("SELECT COUNT(*) FROM question WHERE status = 'answered' AND final_tier IN ('bottles', 'near')").fetchone()[0]
    lines = [f"recipes by status: {dict(status)}",
             f"re-checked: {len(checked)} of {status['promoted'] + status['suspended']} promoted or suspended",
             f"re-checks: agree {sum(r['check_agree'] for r in records.values())}, differ {sum(r['check_differ'] for r in records.values())}",
             f"recipes with any disagreement: {len(doubtful)}; suspended: {status['suspended']}; blocked for near-match: {len(bottles.near_blocked())}",
             f"answered questions in the ledger: {total}; by a saved recipe: {by_saved} ({by_saved / total:.0%})" if total else 'no answered questions in the ledger',
             f"track record: {dict(uses)}"]
    return '\n'.join(lines)


def main(argv):
    if argv[:1] == ['backfill']: print(json.dumps(backfill())); return
    if argv[:1] == ['sweep']: print(json.dumps(sweep(int(argv[1]) if len(argv) > 1 else None, int(argv[2]) if len(argv) > 2 else 4), indent=1)); return
    if argv[:1] == ['report']: print(report()); return
    print(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
