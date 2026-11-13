"""Eval harness: run every eval question through the router, score it against its reference answer,
write one ledger row per question, and print the stage report (accuracy, cost per question, tier distribution).

    python -m clinical_core.agere.tiered.evaluate <stage-name> <tier,tier,...> [--bottle-new] [--bottles PATH]
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

from . import bottles as bottle_store, ledger as ledger_store, router

EVAL = Path('eval/tiered/questions.json')
OUT = Path('artifacts/agere-tiered-core')


def _value(v):
    if isinstance(v, bool): return int(v)
    if isinstance(v, (int, float)): return round(float(v), 4)
    return v


def correct(reference_rows, rows):
    """Every reference row's values appear together in a distinct answer row, and the row counts are equal.
    Column names and row order are ignored; extra answer columns are allowed."""
    if rows is None or len(rows) != len(reference_rows): return False
    remaining = [Counter(_value(v) for v in r) for r in rows]
    for ref in reference_rows:
        need = Counter(_value(v) for v in ref)
        hit = next((i for i, have in enumerate(remaining) if not need - have), None)
        if hit is None: return False
        remaining.pop(hit)
    return True


def year_variants(questions):
    """The same question shapes with other parameter values: 2029 and 2028 swapped in the question and in its
    reference SQL, and the reference answer recomputed. Questions that name no year are left out."""
    import re
    from . import store
    swap = lambda text: re.sub(r'2029|2028', lambda m: '2028' if m.group() == '2029' else '2029', text)
    out = []
    for q in questions:
        if not q.get('reference_sql') or not re.search(r'2029|2028', q['question']): continue
        sql = swap(q['reference_sql'])
        out.append({**q, 'id': q['id'] + 'v', 'question': swap(q['question']), 'reference_sql': sql, 'reference_rows': store.run(sql)[1]})
    return out


def run(stage, tiers, *, bottle_new=False, bottles_path=None, only=None, s1_ledger=None, variants=False, bottle_statuses=('promoted',),
        promote_by_eval=True, readers=None):
    questions = json.loads(EVAL.read_text())['questions']
    if only: questions = [q for q in questions if q['id'] in only]
    if variants: questions = year_variants(questions)
    run_id = f"{stage}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    led = ledger_store.Ledger()
    bottles = bottle_store.Bottles(bottles_path) if 'bottles' in tiers or bottle_new else None
    results = []
    for q in questions:
        outcome = router.answer(q['question'], tiers=tiers, bottles=bottles, bottle_new=bottle_new, s1_ledger=s1_ledger, bottle_statuses=bottle_statuses,
                                **({'readers': readers} if readers else {}))
        ok = outcome['status'] in ('answered', 'partial') and correct(q['reference_rows'], outcome.get('rows'))
        if promote_by_eval and isinstance(outcome.get('bottled'), int):
            # Deterministic validation cannot tell a plausible wrong recipe from a right one, so a new bottle
            # is promoted only when its answer matched the eval reference; otherwise it is rejected with the reason.
            bottles.set_status(outcome['bottled'], 'promoted' if ok else 'rejected',
                               'answer matched the eval reference' if ok else 'answer did not match the eval reference for ' + q['id'])
        led.write(run_id, q['question'], outcome, eval_id=q['id'], correct=ok, config={'stage': stage, 'tiers': list(tiers), 'bottle_new': bottle_new})
        results.append({'id': q['id'], 'shape': q['shape'], 'status': outcome['status'], 'final_tier': outcome['final_tier'], 'correct': ok,
                        'usd': sum(s.get('usd', 0.0) for s in outcome['steps']), 'tokens': sum(s.get('tokens', 0) for s in outcome['steps']),
                        'seconds': outcome['seconds'], 'escalations': outcome['escalations'], 'bottled': outcome.get('bottled'),
                        'bottle_id': (outcome.get('recipe') or {}).get('bottle_id') if outcome['final_tier'] == 'bottles' else None,
                        'frontier_calls': sum(s.get('tier') == 'llm' and 'model' in s for s in outcome['steps']),
                        'reads': {s['reader']: {'calls': s['calls'], 'decided': s['decided'], 'usd': round(s['usd'], 5)}
                                  for s in outcome['steps'] if s.get('tier') == 'read'},
                        'validation_failed': [c['name'] for c in (outcome.get('validation') or {}).get('checks', []) if not c['ok']]})
        print(f"{q['id']} {q['shape']:14} {str(outcome['final_tier']):8} {outcome['status']:10} {'correct' if ok else 'WRONG':7} "
              f"${results[-1]['usd']:.4f} {results[-1]['tokens']:6d} tok {outcome['seconds']:6.1f}s", flush=True)
    return run_id, results


def report(stage, tiers, run_id, results):
    n = len(results)
    usd = sum(r['usd'] for r in results)
    lines = [f'STAGE REPORT  {stage}   run {run_id}', f'tiers enabled: {", ".join(tiers)}', f'questions: {n}',
             f"correct: {sum(r['correct'] for r in results)}/{n} ({100 * sum(r['correct'] for r in results) / n:.1f}%)",
             f"returned an answer: {sum(r['status'] != 'unanswered' for r in results)}/{n}",
             f"cost: ${usd:.4f} total, ${usd / n:.4f} per question, ${usd / max(1, sum(r['correct'] for r in results)):.4f} per correct answer",
             f"tokens: {sum(r['tokens'] for r in results)} total", f"latency: {sum(r['seconds'] for r in results) / n:.1f}s mean",
             'final tier distribution:']
    for tier, count in Counter(str(r['final_tier']) for r in results).most_common():
        rows = [r for r in results if str(r['final_tier']) == tier]
        lines.append(f"  {tier:8} {count:3d}  correct {sum(r['correct'] for r in rows):3d}  ${sum(r['usd'] for r in rows):.4f}")
    lines.append('by question shape:')
    reads = {}
    for r in results:
        for name, got in (r.get('reads') or {}).items():
            agg = reads.setdefault(name, {'calls': 0, 'decided': 0, 'usd': 0.0})
            for k in agg: agg[k] += got[k]
    if reads:
        lines.append('documents read, by reader (calls / decided / cost):')
        lines += [f"  {name:8} {a['calls']:4d} / {a['decided']:4d} / ${a['usd']:.4f}" for name, a in reads.items()]
    for shape in ('canonical', 'paraphrase', 'novel', 'doc_canonical', 'doc_paraphrase', 'doc_novel'):
        rows = [r for r in results if r['shape'] == shape]
        if rows:
            lines.append(f"  {shape:14} correct {sum(r['correct'] for r in rows)}/{len(rows)}  tiers {dict(Counter(str(r['final_tier']) for r in rows))}")
    wrong = [r for r in results if not r['correct']]
    if wrong:
        lines.append('not correct:')
        lines += [f"  {r['id']} {r['status']} tier={r['final_tier']} validation_failed={r['validation_failed']}" for r in wrong]
    bottled = [r for r in results if isinstance(r.get('bottled'), int)]
    refused = [r for r in results if isinstance(r.get('bottled'), dict)]
    if bottled or refused:
        lines.append(f'bottles created: {len(bottled)}; not bottled: {len(refused)}')
        lines += [f"  {r['id']} not bottled: {r['bottled']['not_bottled']}" for r in refused]
    text = '\n'.join(lines)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n')
    (OUT / f'{run_id}.results.json').write_text(json.dumps(results, indent=1))
    return text


def main(argv):
    stage, tiers = argv[0], tuple(argv[1].split(','))
    bottle_new = '--bottle-new' in argv
    bottles_path = argv[argv.index('--bottles') + 1] if '--bottles' in argv else None
    only = set(argv[argv.index('--only') + 1].split(',')) if '--only' in argv else None
    s1_ledger = None
    readers = tuple(argv[argv.index('--readers') + 1].split(',')) if '--readers' in argv else None
    if 'system1' in tiers or 'system1' in (readers or ('system1',)):
        import os
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys()  # System One provider keys only; Claude stays on the subscription transport
        os.environ.pop('ANTHROPIC_API_KEY', None)
        s1_ledger = os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3')
    run_id, results = run(stage, tiers, bottle_new=bottle_new, bottles_path=bottles_path, only=only, variants='--variants' in argv, s1_ledger=s1_ledger, readers=readers)
    print('\n' + report(stage, tiers, run_id, results))


if __name__ == '__main__':
    main(sys.argv[1:])
