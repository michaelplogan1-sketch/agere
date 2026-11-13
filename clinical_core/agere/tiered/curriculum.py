"""Batch learning: many generated healthcare questions through the tiers, so that question shapes are learned
(bottled) and the questions the stack cannot answer are listed.

    python -m clinical_core.agere.tiered.curriculum generate 500      # small model writes questions from table and column names only
    python -m clinical_core.agere.tiered.curriculum run               # every question through all tiers; resumable; stops on a usage limit
    python -m clinical_core.agere.tiered.curriculum confirm           # second model writes its own query for each new recipe; equal result = confirmed
    python -m clinical_core.agere.tiered.curriculum merge             # confirmed recipes enter the main store as provisional, for the promotion gate
    python -m clinical_core.agere.tiered.curriculum report

The run uses its own bottle store, ledger and System One ledger under artifacts/agere-tiered-core/curriculum/, so it
never writes to the stores the app and the evals use. A frontier-written recipe is only ever provisional here. It is
confirmed when a different model, asked the same question on its own, writes a query that returns the same rows;
anything else stays unconfirmed and is never merged. No model sees data rows when writing questions.
"""
import json
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

from . import bottles as bottle_store, evaluate, ledger as ledger_store, models, recipes, router, store

HOME = Path('artifacts/agere-tiered-core/curriculum')
QUESTIONS, BOTTLES, LEDGER, S1 = HOME / 'questions.jsonl', HOME / 'bottles.sqlite3', HOME / 'ledger.sqlite3', HOME / 'system-one-ledger.sqlite3'
RUN = 'curriculum'
BATCH = 25
ROLES = ('care manager', 'medical director', 'quality lead', 'value-based contract lead', 'finance lead', 'population health analyst',
         'pharmacy lead', 'access and scheduling manager', 'utilization management nurse', 'health plan account manager')
TOPICS = ('emergency visits, admissions and observation stays', 'readmissions and length of stay', 'medication fills by drug class and patients on therapy',
          'claims, billed, allowed and paid amounts, denials by payer', 'enrollment, member months and payers', 'appointments, no-shows and cancellations',
          'conditions and how many patients have them', 'deaths', 'immunizations, wellness visits and cancer screening', 'social needs and smoking status',
          'what scanned radiology reports, consult notes and letters say', 'what HIE discharge summaries and ED summaries give for one patient',
          'facts and therapy changes already extracted from notes', 'encounters and visits by type and organization', 'patients by sex, coverage type and age')
GENERATE = '''Write %d different questions that a %s at a health system or health plan would type into an analytics tool.
Topic for this batch: %s.
Each question must be answerable from the tables below with one count, one table, or by reading a bounded set of
documents (always narrowed by a patient, or by a document type and one month). Vary the wording and the shape:
counts, breakdowns, top-N, rates, comparisons of two filters, distinct patients versus events. Use calendar years
2027 to 2029 or months such as 2029-03, and only these vocabulary values where a value is needed.
Do not ask why, do not ask for advice, forecasts or statistical modelling, and do not invent patient identifiers.
Return exactly one JSON object: {"questions": ["...", "..."]}

Tables and columns (names only):
%s

Vocabulary:
conditions: %s
drug classes: %s
scanned document types: %s'''


def _keys():
    os.environ.pop('ANTHROPIC_API_KEY', None)
    from run_integrated_stack import keys
    keys(); os.environ.pop('ANTHROPIC_API_KEY', None)


def _preload():
    """Import everything the run touches now, so later edits to the source on disk cannot change a run in progress."""
    from . import followup, narrative, read, split  # noqa: F401
    from ..clinical import system_one  # noqa: F401


def _norm(text):
    return ' '.join(str(text).lower().strip().rstrip('?.').split())


def _load():
    return [json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()] if QUESTIONS.exists() else []


def generate(n):
    """The small model writes questions from names and vocabulary only. Appends to questions.jsonl; duplicates dropped."""
    HOME.mkdir(parents=True, exist_ok=True)
    db = store.connect()
    def vocab(sql):
        try:
            return ', '.join(str(r[0]) for r in db.execute(sql))
        except Exception:  # a layer without that table simply offers no values for it
            return ''
    schema, conditions = store.schema_text(db), vocab("SELECT DISTINCT concept FROM concept_patient WHERE kind = 'condition' ORDER BY 1")
    classes = vocab('SELECT drug_class FROM fill WHERE drug_class IS NOT NULL GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 30')
    types = vocab('SELECT DISTINCT document_type FROM scanned_document WHERE readable = 1 ORDER BY 1')
    have, seen, usd, batch = _load(), None, 0.0, 0
    seen = {_norm(q['question']) for q in have}
    with open(QUESTIONS, 'a') as out:
        while len(seen) - len(have) < n and batch < 4 * (n // BATCH + 1):
            role, topic = ROLES[(len(have) // BATCH + batch) % len(ROLES)], TOPICS[(len(have) // BATCH + batch) % len(TOPICS)]
            batch += 1
            text, step = models._claude(models.SLM, 'You write realistic analytics questions. You never state data values you were not given.',
                                        GENERATE % (BATCH, role, topic, schema, conditions, classes, types))
            usd += step['usd']
            try:
                found = models._json(text).get('questions') or []
            except ValueError:
                continue
            for q in found:
                q = ' '.join(str(q).split())
                if 12 <= len(q) <= 300 and _norm(q) not in seen:
                    seen.add(_norm(q))
                    out.write(json.dumps({'id': f'Q{len(seen):05d}', 'question': q, 'role': role, 'topic': topic}) + '\n'); out.flush()
            print(f'batch {batch}: {len(seen) - len(have)} new questions, ${usd:.4f}', flush=True)
    return {'new': len(seen) - len(have), 'total': len(seen), 'usd': round(usd, 4)}


def run(limit=None):
    """Every not-yet-run question through all tiers. New frontier recipes are bottled as provisional in the run's own
    store. Stops cleanly on a usage limit; run again to continue."""
    _keys(); _preload()
    HOME.mkdir(parents=True, exist_ok=True)
    if not BOTTLES.exists() and Path(bottle_store.DEFAULT).exists(): shutil.copyfile(bottle_store.DEFAULT, BOTTLES)  # start from what is already learned
    bottles, led = bottle_store.Bottles(BOTTLES), ledger_store.Ledger(LEDGER)
    done = {r['eval_id'] for r in led.rows(RUN)}
    todo = [q for q in _load() if q['id'] not in done][:limit]
    print(f'{len(done)} already run, {len(todo)} to run', flush=True)
    for n, q in enumerate(todo, 1):
        try:
            outcome = router.answer(q['question'], bottles=bottles, bottle_new=True, s1_ledger=str(S1))
        except Exception as exc:
            if 'limit' in str(exc).lower():
                print(f'stopped on a usage limit after {n - 1} questions: {str(exc)[:160]}', flush=True)
                break
            outcome = {'status': 'error', 'final_tier': None, 'steps': [], 'seconds': 0.0, 'validation': None, 'recipe': None, 'rows': None,
                       'missing': f'{type(exc).__name__}: {str(exc)[:200]}'}
        led.write(RUN, q['question'], outcome, eval_id=q['id'], config={'role': q.get('role'), 'topic': q.get('topic'), 'bottled': outcome.get('bottled'),
                                                                        'missing': str(outcome.get('missing'))[:300] if outcome.get('missing') else None})
        usd = sum(s.get('usd', 0.0) for s in outcome['steps'])
        print(f"{n}/{len(todo)} {q['id']} {str(outcome['final_tier']):8} {outcome['status']:10} ${usd:.4f} {outcome['seconds']:5.1f}s | {q['question'][:110]}", flush=True)
    return report()


def confirm(limit=None):
    """Each provisional recipe from the run is checked by a different model writing its own query for the same question.
    Equal rows (order and column names ignored) -> 'confirmed'. A reading plan is not confirmed this way and stays provisional."""
    _keys(); _preload()
    bottles, db, out = bottle_store.Bottles(BOTTLES), store.connect(), Counter()
    for bottle in [b for b in bottles.full() if b['status'] == 'provisional'][:limit]:
        prov = bottle.get('provenance') or {}
        question = prov.get('question')
        if prov.get('second_model') or not question: continue
        verdict, usd = 'unconfirmed: no second query', 0.0
        if bottle.get('read'):
            verdict = 'unconfirmed: reading plans are not checked by a second query'
        else:
            try:
                hit = bottles.match(question, statuses=('provisional',))
                first = store.run(bottle['sql'], hit[1] if hit else {}, db)[1]
                second, step = models.llm_recipe(question, model=models.SLM)
                usd = step.get('usd', 0.0)
                if second is None or second.get('read'):
                    verdict = 'unconfirmed: the second model wrote no query'
                else:
                    params = {k: (int(v['value']) if v.get('type') == 'int' else str(v.get('value'))) for k, v in second['params'].items()}
                    rows = store.run(second['sql'], params, db)[1]
                    same = evaluate.correct(first, rows) and evaluate.correct(rows, first)
                    verdict = 'confirmed' if same and first else 'unconfirmed: the second query returned different rows' if first else 'unconfirmed: empty result'
            except Exception as exc:
                if 'limit' in str(exc).lower():
                    print('stopped on a usage limit', flush=True); break
                verdict = f'unconfirmed: {type(exc).__name__}'
        bottles.db.execute('UPDATE bottle SET provenance_json=? WHERE id=?',
                           (json.dumps({**prov, 'second_model': {'model': models.SLM, 'verdict': verdict, 'usd': usd}}), bottle['id']))
        bottles.db.commit()
        if verdict == 'confirmed': bottles.set_status(bottle['id'], 'confirmed', 'a second model wrote its own query for the question and it returned the same rows')
        out[verdict.split(':')[0]] += 1
        print(f"{bottle['id']} {verdict} | {bottle['template'][:110]}", flush=True)
    return dict(out)


def merge():
    """Confirmed recipes are copied into the main store as provisional (they answer nothing there until the promotion gate passes)."""
    source, main, out = bottle_store.Bottles(BOTTLES), bottle_store.Bottles(), Counter()
    for b in source.full():
        if b['status'] != 'confirmed': continue
        prov = b.get('provenance') or {}
        recipe = {'sql': b['sql'], 'params': {n: {**p, 'value': None} for n, p in b['params'].items()}, 'grain': b.get('grain') or [], 'question_template': b['template']}
        hit = source.match(prov.get('question', ''), statuses=('confirmed',))
        if not hit: out['skipped: original question no longer matches'] += 1; continue
        for name, value in hit[1].items(): recipe['params'][name]['value'] = value
        bid, why = main.add(prov['question'], recipe, b.get('validation') or {}, {**prov, 'from': 'curriculum', 'confirmed_by_second_model': True})
        out['merged as provisional' if bid else f'skipped: {why}'] += 1
    return dict(out)


def report():
    led, bottles = ledger_store.Ledger(LEDGER), bottle_store.Bottles(BOTTLES) if BOTTLES.exists() else None
    rows = led.rows(RUN)
    if not rows: return 'no questions run yet'
    declined = [r for r in rows if r['status'] != 'answered']
    lines = [f'CURRICULUM  questions run: {len(rows)} of {len(_load())} generated',
             f"answered: {sum(r['status'] == 'answered' for r in rows)}; not answered: {len(declined)}",
             f"answering tier: {dict(Counter(str(r['final_tier']) for r in rows if r['status'] == 'answered'))}",
             f"cost: ${sum(r['usd'] for r in rows):.2f} total, ${sum(r['usd'] for r in rows) / len(rows):.4f} per question; mean {sum(r['seconds'] for r in rows) / len(rows):.1f}s",
             'answered here is not checked for correctness: there are no reference answers for generated questions.']
    if bottles: lines.append(f"recipes in the run's store: {dict(Counter(b['status'] for b in bottles.all()))}")
    by_topic = Counter(json.loads(r['config_json'] or '{}').get('topic') for r in declined)
    if declined: lines += ['not answered, by topic:'] + [f'  {n:4d}  {t}' for t, n in by_topic.most_common()]
    reasons = Counter(' '.join(str(json.loads(r['config_json'] or '{}').get('missing') or r['status']).split())[:90] for r in declined)
    if declined: lines += ['most common reasons:'] + [f'  {n:4d}  {t}' for t, n in reasons.most_common(12)]
    text = '\n'.join(lines)
    (HOME / 'report.txt').write_text(text + '\n')
    return text


def main(argv):
    command = argv[0] if argv else 'report'
    number = int(argv[1]) if len(argv) > 1 else None
    if command == 'generate':
        os.environ.pop('ANTHROPIC_API_KEY', None)
        print(json.dumps(generate(number or 100)))
    elif command == 'run': print('\n' + run(number))
    elif command == 'confirm': print(json.dumps(confirm(number)))
    elif command == 'merge': print(json.dumps(merge()))
    else: print(report())


if __name__ == '__main__':
    main(sys.argv[1:])
