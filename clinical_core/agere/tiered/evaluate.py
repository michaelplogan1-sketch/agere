"""Eval harness: run every eval question through the router, score it against its reference answer,
write one ledger row per question, and print the stage report (accuracy, cost per question, tier distribution).

    python -m clinical_core.agere.tiered.evaluate <stage-name> <tier,tier,...> [--bottle-new] [--bottles PATH]
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

from . import bottles as bottle_store, ledger as ledger_store, router, store

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
    for shape in ('canonical', 'paraphrase', 'novel', 'doc_canonical', 'doc_paraphrase', 'doc_novel', 'ext_canonical', 'ext_paraphrase', 'ext_novel'):
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


def followups(s1_ledger=None):
    """Follow-up eval: each item is a previous question and what the user typed next. The typed words are made
    complete exactly as the app does it (followup.complete_question), sent through all tiers, and scored against the
    reference. An item marked decline is correct only when the core returns no answer."""
    from . import followup
    from ..clinical.conversation import is_followup
    items = json.loads(Path('eval/tiered/followups.json').read_text())['items']
    run_id = f"followups-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    led, bottles, results = ledger_store.Ledger(), bottle_store.Bottles(), []
    for it in items:
        standalone, how = followup.complete_question(it['follow_up'], it['previous'], is_followup(it['follow_up']), bottles, s1_ledger)
        outcome = router.answer(standalone, bottles=bottles, s1_ledger=s1_ledger) if standalone else None
        answered = bool(outcome) and outcome['status'] == 'answered'
        ok = (not answered) if it.get('decline') else answered and correct(it['reference_rows'], outcome.get('rows'))
        usd = how['usd'] + (sum(s.get('usd', 0.0) for s in outcome['steps']) if outcome else 0.0)
        if outcome: led.write(run_id, it['follow_up'], outcome, eval_id=it['id'], correct=ok, config={'previous': it['previous'], 'standalone': standalone, 'how': how})
        results.append({'id': it['id'], 'kind': it['kind'], 'method': how['method'], 'standalone': standalone, 'final_tier': outcome and outcome['final_tier'],
                        'status': outcome['status'] if outcome else 'not_sent', 'correct': ok, 'usd': usd,
                        'frontier_calls': (sum(s.get('tier') == 'llm' and 'model' in s for s in outcome['steps']) if outcome else 0) + bool(how.get('frontier_called'))})
        print(f"{it['id']} {it['kind']:9} {how['method'][:34]:34} {str(results[-1]['final_tier']):8} {results[-1]['status']:10} {'correct' if ok else 'WRONG':7} ${usd:.4f} | {standalone}", flush=True)
    n = len(results)
    lines = [f'FOLLOW-UP EVAL  run {run_id}', f'items: {n}', f"correct: {sum(r['correct'] for r in results)}/{n}",
             f"cost: ${sum(r['usd'] for r in results):.4f} total, ${sum(r['usd'] for r in results) / n:.4f} per item",
             f"frontier model called on: {sum(r['frontier_calls'] > 0 for r in results)}/{n}", 'by kind:']
    for kind in dict.fromkeys(r['kind'] for r in results):
        rows = [r for r in results if r['kind'] == kind]
        lines.append(f"  {kind:9} correct {sum(r['correct'] for r in rows)}/{len(rows)}  methods {dict(Counter(r['method'] for r in rows))}")
    lines += ['not correct:'] + [f"  {r['id']} {r['method']} -> {r['standalone']} ({r['status']}, tier {r['final_tier']})" for r in results if not r['correct']] if any(not r['correct'] for r in results) else []
    text = '\n'.join(lines)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n'); (OUT / f'{run_id}.results.json').write_text(json.dumps(results, indent=1))
    return text


def chains(s1_ledger=None, path='eval/tiered/followup_chains.json'):
    """Multi-turn eval. The first turn of a chain is a complete question; each later turn is made complete from the
    last answered turn's complete question, as the app does, then sent through all tiers and scored."""
    from . import followup
    from ..clinical.conversation import is_followup
    data = json.loads(Path(path).read_text())['chains']
    run_id = f"chains-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    led, bottles, results = ledger_store.Ledger(), bottle_store.Bottles(), []
    for chain in data:
        previous = None
        for n, turn in enumerate(chain['turns'], 1):
            standalone, how = followup.complete_question(turn['say'], previous, is_followup(turn['say']), bottles, s1_ledger)
            outcome = router.answer(standalone, bottles=bottles, s1_ledger=s1_ledger) if standalone else None
            answered = bool(outcome) and outcome['status'] == 'answered'
            ok = (not answered) if turn.get('decline') else answered and correct(turn['reference_rows'], outcome.get('rows'))
            usd = how['usd'] + (sum(s.get('usd', 0.0) for s in outcome['steps']) if outcome else 0.0)
            if outcome: led.write(run_id, turn['say'], outcome, eval_id=f"{chain['id']}.{n}", correct=ok, config={'previous': previous, 'standalone': standalone, 'how': how})
            results.append({'id': f"{chain['id']}.{n}", 'first': n == 1, 'decline': bool(turn.get('decline')), 'method': how['method'], 'say': turn['say'],
                            'standalone': standalone, 'final_tier': outcome and outcome['final_tier'], 'status': outcome['status'] if outcome else 'not_sent',
                            'correct': ok, 'usd': usd, 'seconds': (outcome['seconds'] if outcome else 0) + how.get('seconds', 0),
                            'frontier_calls': (sum(s.get('tier') == 'llm' and 'model' in s for s in outcome['steps']) if outcome else 0) + bool(how.get('frontier_called'))})
            print(f"{results[-1]['id']} {how['method'][:30]:30} {str(results[-1]['final_tier']):8} {results[-1]['status']:10} {'correct' if ok else 'WRONG':7} ${usd:.4f} | {turn['say']} -> {standalone}", flush=True)
            if answered: previous = standalone
    later = [r for r in results if not r['first']]
    whole = {}
    for r in results: whole[r['id'].split('.')[0]] = whole.get(r['id'].split('.')[0], True) and r['correct']
    lines = [f'FOLLOW-UP CHAINS  run {run_id}', f'chains: {len(data)}; turns: {len(results)} ({len(later)} after the first)',
             f"turns correct: {sum(r['correct'] for r in results)}/{len(results)}; later turns correct: {sum(r['correct'] for r in later)}/{len(later)}",
             f"chains with every turn correct: {sum(whole.values())}/{len(whole)}",
             f"cost: ${sum(r['usd'] for r in results):.4f} total, ${sum(r['usd'] for r in results) / len(results):.4f} per turn; mean {sum(r['seconds'] for r in results) / len(results):.1f}s per turn",
             f"frontier model called on: {sum(r['frontier_calls'] > 0 for r in results)}/{len(results)} turns",
             f"how later turns were read: {dict(Counter(r['method'] for r in later))}", f"final tier: {dict(Counter(str(r['final_tier']) for r in results))}"]
    wrong = [r for r in results if not r['correct']]
    if wrong: lines += ['not correct:'] + [f"  {r['id']} \"{r['say']}\" [{r['method']}] -> {r['standalone']} ({r['status']}, tier {r['final_tier']})" for r in wrong]
    text = '\n'.join(lines)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n'); (OUT / f'{run_id}.results.json').write_text(json.dumps(results, indent=1))
    return text


def multi(s1_ledger=None):
    """Messages that ask several separate questions. Mirrors the app: a candidate is split, each part goes through all
    tiers and the message counts as answered in parts only when every part is answered; otherwise it is tried as one."""
    from . import split
    items = json.loads(Path('eval/tiered/multi_part.json').read_text())['items']
    run_id = f"multi-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    led, bottles, results = ledger_store.Ledger(), bottle_store.Bottles(), []
    for it in items:
        found, how = split.parts(it['question'], s1_ledger) if split.candidate(it['question'], bottles) else (None, {'method': 'not_a_candidate', 'usd': 0.0})
        outcomes = []
        for part in found or []:
            outcomes.append(router.answer(part, bottles=bottles, s1_ledger=s1_ledger))
            led.write(run_id, part, outcomes[-1], eval_id=it['id'], config={'message': it['question'], 'split': how})
            if outcomes[-1]['status'] != 'answered': break
        in_parts = bool(found) and all(o['status'] == 'answered' for o in outcomes)
        if not in_parts:
            whole = router.answer(it['question'], bottles=bottles, s1_ledger=s1_ledger)
            led.write(run_id, it['question'], whole, eval_id=it['id'], config={'split': how})
            outcomes.append(whole)
        if it['kind'] == 'several':
            left, ok = list(outcomes), in_parts and len(outcomes) == len(it['parts'])
            for ref in it['parts'] if ok else []:
                hit = next((o for o in left if correct(ref['reference_rows'], o.get('rows'))), None)
                if hit is None: ok = False; break
                left.remove(hit)
        else:
            ok = not in_parts and outcomes[-1]['status'] == 'answered' and correct(it['reference_rows'], outcomes[-1].get('rows'))
        usd = how['usd'] + sum(s.get('usd', 0.0) for o in outcomes for s in o['steps'])
        results.append({'id': it['id'], 'kind': it['kind'], 'split': how['method'], 'parts': found, 'answered_in_parts': in_parts, 'correct': ok, 'usd': usd,
                        'tiers': [o['final_tier'] for o in outcomes], 'statuses': [o['status'] for o in outcomes],
                        'frontier_calls': sum(s.get('tier') == 'llm' and 'model' in s for o in outcomes for s in o['steps'])})
        print(f"{it['id']} {it['kind']:7} {how['method'][:36]:36} {'parts' if in_parts else 'whole':5} {'correct' if ok else 'WRONG':7} ${usd:.4f} {results[-1]['tiers']} | {found or it['question']}", flush=True)
    n = len(results)
    lines = [f'MULTI-PART EVAL  run {run_id}', f'items: {n}', f"correct: {sum(r['correct'] for r in results)}/{n}",
             *(f"  {kind}: {sum(r['correct'] for r in results if r['kind'] == kind)}/{sum(r['kind'] == kind for r in results)}" for kind in ('several', 'one')),
             f"cost: ${sum(r['usd'] for r in results):.4f} total, ${sum(r['usd'] for r in results) / n:.4f} per item",
             f"frontier model called on: {sum(r['frontier_calls'] > 0 for r in results)}/{n}", f"split outcomes: {dict(Counter(r['split'] for r in results))}"]
    wrong = [r for r in results if not r['correct']]
    if wrong: lines += ['not correct:'] + [f"  {r['id']} [{r['split']}] in parts: {r['answered_in_parts']} statuses {r['statuses']} tiers {r['tiers']} -> {r['parts']}" for r in wrong]
    text = '\n'.join(lines)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n'); (OUT / f'{run_id}.results.json').write_text(json.dumps(results, indent=1))
    return text


def cohort(s1_ledger=None):
    """Follow-ups typed about a selected patient group. Mirrors the app: the words are made a complete question about
    the group (followup.group_question) and that question goes through all tiers on a connection restricted to the group."""
    from . import followup
    data = json.loads(Path('eval/tiered/cohort_followups.json').read_text())
    run_id = f"cohort-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    led, bottles, results, db = ledger_store.Ledger(), bottle_store.Bottles(), [], store.connect()
    groups = {k: [r[0] for r in db.execute(v['sql'])] for k, v in data['cohorts'].items()}
    for it in data['items']:
        ids = groups[it['cohort']]
        standalone, how = followup.group_question(it['say'], s1_ledger, bottles)
        outcome = router.answer(standalone, bottles=bottles, s1_ledger=s1_ledger, cohort=ids) if standalone else None
        answered = bool(outcome) and outcome['status'] == 'answered'
        ok = (not answered) if it.get('decline') else answered and correct(it['reference_rows'], outcome.get('rows'))
        usd = how['usd'] + (sum(s.get('usd', 0.0) for s in outcome['steps']) if outcome else 0.0)
        if outcome: led.write(run_id, it['say'], outcome, eval_id=it['id'], correct=ok, config={'cohort': it['cohort'], 'patients': len(ids), 'standalone': standalone, 'how': how})
        results.append({'id': it['id'], 'cohort': it['cohort'], 'patients': len(ids), 'method': how['method'], 'standalone': standalone, 'final_tier': outcome and outcome['final_tier'],
                        'status': outcome['status'] if outcome else 'not_sent', 'correct': ok, 'usd': usd, 'seconds': (outcome['seconds'] if outcome else 0) + how.get('seconds', 0),
                        'frontier_calls': sum(s.get('tier') == 'llm' and 'model' in s for s in outcome['steps']) if outcome else 0})
        print(f"{it['id']} {it['cohort']} {how['method'][:34]:34} {str(results[-1]['final_tier']):8} {results[-1]['status']:10} {'correct' if ok else 'WRONG':7} ${usd:.4f} | {it['say']} -> {standalone}", flush=True)
    n = len(results)
    lines = [f'COHORT FOLLOW-UP EVAL  run {run_id}', f'items: {n}', f"correct: {sum(r['correct'] for r in results)}/{n}",
             f"cost: ${sum(r['usd'] for r in results):.4f} total, ${sum(r['usd'] for r in results) / n:.4f} per item; mean {sum(r['seconds'] for r in results) / n:.1f}s",
             f"frontier model called on: {sum(r['frontier_calls'] > 0 for r in results)}/{n}", f"how the words were read: {dict(Counter(r['method'] for r in results))}",
             f"final tier: {dict(Counter(str(r['final_tier']) for r in results))}"]
    wrong = [r for r in results if not r['correct']]
    if wrong: lines += ['not correct:'] + [f"  {r['id']} [{r['method']}] -> {r['standalone']} ({r['status']}, tier {r['final_tier']})" for r in wrong]
    text = '\n'.join(lines)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n'); (OUT / f'{run_id}.results.json').write_text(json.dumps(results, indent=1))
    return text


READER_SYSTEM = '''You are a clinical data assistant reading documents from a patient record system. Answer the question
using only the documents given; each starts with its record id in square brackets. Read every document.
Return exactly one JSON object and nothing else:
- a "how many" question: {"count": <number of documents that state it>, "record_ids": [<ids of those documents>]}
- a "which documents" question: {"record_ids": [<ids of the documents that state it>]}
- a "what does each document give" question: {"values": {"<record_id>": "<the value as written>"}} with only the documents that give the value'''


def _reader_correct(reference_rows, got):
    """The chart reader's JSON against the reference. Lenient on values: one that contains the reference value passes."""
    norm = lambda v: ' '.join(str(v).lower().strip(' .;:').split())
    if len(reference_rows) == 1 and all(isinstance(v, int) for v in reference_rows[0]):
        return got.get('count') == reference_rows[0][1]
    if all(len(r) == 1 for r in reference_rows):
        return set(got.get('record_ids') or []) == {r[0] for r in reference_rows}
    values = {str(k): norm(v) for k, v in (got.get('values') or {}).items() if str(v).strip()}
    return set(values) == {r[0] for r in reference_rows} and all(norm(r[1]) in values[r[0]] for r in reference_rows)


def compare(s1_ledger=None, only=None):
    """The stack against a frontier model that is simply handed the same documents (chart_reader_comparison.json)."""
    from . import models
    data = json.loads(Path('eval/tiered/chart_reader_comparison.json').read_text())
    run_id = f"compare-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    led, bottles, results, db = ledger_store.Ledger(), bottle_store.Bottles(), [], store.connect()
    for it in data['items']:
        if only and it['id'] not in only: continue
        outcome = router.answer(it['question'], bottles=bottles, s1_ledger=s1_ledger)
        led.write(run_id, it['question'], outcome, eval_id=it['id'], correct=outcome['status'] == 'answered' and correct(it['reference_rows'], outcome.get('rows')))
        docs = [d['record_id'] for d in outcome.get('documents') or []]
        stack = {'status': outcome['status'], 'tier': outcome['final_tier'], 'rows': (outcome.get('rows') or [])[:30], 'seconds': outcome['seconds'],
                 'correct': outcome['status'] == 'answered' and correct(it['reference_rows'], outcome.get('rows')),
                 'usd': sum(s.get('usd', 0.0) for s in outcome['steps']), 'frontier_calls': sum(s.get('tier') == 'llm' and 'model' in s for s in outcome['steps'])}
        texts = store.texts(docs, db)
        prompt = 'Question: ' + it['question'] + '\n\nDocuments:\n\n' + '\n\n'.join(f"[{i}]\n{texts[i]['text'].strip()}" for i in docs if i in texts)
        runs = []
        for _ in range(it.get('repeats', 1) if docs else 0):
            try:
                text, step = models._claude(models.LLM, READER_SYSTEM, prompt)
                try:
                    got = models._json(text)
                except ValueError:
                    got = {'unusable': text[:200]}
                runs.append({'correct': _reader_correct(it['reference_rows'], got), 'count': got.get('count'), 'ids_listed': len(got.get('record_ids') or []) if 'record_ids' in got else None,
                             'values': got.get('values'), 'usd': step['usd'], 'tokens': step['tokens'], 'seconds': step['seconds'], **({'unusable': got['unusable']} if 'unusable' in got else {})})
            except Exception as exc:
                if 'limit' in str(exc).lower(): raise
                runs.append({'correct': False, 'error': f'{type(exc).__name__}: {str(exc)[:160]}', 'usd': 0.0, 'tokens': 0, 'seconds': 0.0})
        results.append({'id': it['id'], 'group': it['group'], 'question': it['question'], 'reference_rows': it['reference_rows'], 'documents': len(docs), 'characters': len(prompt), 'stack': stack, 'chart_reader': runs})
        said = ', '.join(('ok' if r['correct'] else 'WRONG') + (f"({r['count']})" if r.get('count') is not None else '') for r in runs) or 'not run (no documents selected)'
        print(f"{it['id']} {it['group'][:14]:14} docs {len(docs):4d} | stack {'ok' if stack['correct'] else 'WRONG':5} ${stack['usd']:.4f} {stack['seconds']:5.1f}s | reader {said} ${sum(r['usd'] for r in runs):.4f} {sum(r['seconds'] for r in runs):5.1f}s", flush=True)
    lines = [f'CHART-READER COMPARISON  run {run_id}', 'arm A: the tiered stack.  arm B: Claude Sonnet handed the same documents in one prompt.', '']
    for group in dict.fromkeys(r['group'] for r in results):
        rows = [r for r in results if r['group'] == group]
        runs = [x for r in rows for x in r['chart_reader']]
        lines += [f"{group}: {len(rows)} questions, {sum(r['documents'] for r in rows)} documents",
                  f"  stack         correct {sum(r['stack']['correct'] for r in rows)}/{len(rows)}   ${sum(r['stack']['usd'] for r in rows) / len(rows):.4f} per question   {sum(r['stack']['seconds'] for r in rows) / len(rows):.1f}s",
                  f"  chart reader  correct {sum(x['correct'] for x in runs)}/{len(runs)} runs   ${sum(x['usd'] for x in runs) / max(1, len(runs)):.4f} per run   {sum(x['seconds'] for x in runs) / max(1, len(runs)):.1f}s"]
    many = [r for r in results if len(r['chart_reader']) > 1]
    if many: lines += ['', 'repeat runs of the chart reader on the same question (reference in brackets):'] + [f"  {r['id']} [{r['reference_rows'][0][1]}]: {[x.get('count') for x in r['chart_reader']]}" for r in many]
    text = '\n'.join(lines)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n'); (OUT / f'{run_id}.results.json').write_text(json.dumps(results, indent=1))
    return text


HF_READER_SYSTEM = '''You are a clinical assistant reviewing one patient's record: discrete fields first, then every document, each
starting with its record id in square brackets. Answer the question from the record only.
Return exactly one JSON object and nothing else:
{"summary": "<two or three sentences answering the question>",
 "reasons": [{"medication": "<drug or class>", "reason": "<the documented reason>", "record_id": "<id of the document that states it>",
              "quote": "<the sentence from that document, copied exactly>"}]}
List a reason only when a document states it. If the record documents no reason, return "reasons": [].'''


def _norm_text(text):
    return ' '.join(str(text or '').lower().split())


def _score_reasons(items, required, optional, texts):
    """items: [{'quote','text'}]. -> found, unsupported, extra (see hf_reasons.json rule)."""
    corpus = [_norm_text(t['text']) for t in texts.values()]
    supported = lambda it: len(_norm_text(it.get('quote'))) >= 12 and any(_norm_text(it['quote']) in doc for doc in corpus)
    said = lambda it: _norm_text(str(it.get('quote')) + ' ' + str(it.get('text')))
    found = [r for r in required if any(r in said(it) for it in items)]
    unsupported = [it for it in items if not supported(it)]
    extra = [it for it in items if supported(it) and not any(ph in said(it) for ph in required + optional)]
    return found, unsupported, extra


def hf(s1_ledger=None, only=None, path='eval/tiered/hf_reasons.json'):
    """Why is a heart-failure patient not on every guideline medication: the stack against a frontier model handed the chart."""
    from . import models, split
    data = json.loads(Path(path).read_text())
    run_id = f"hf-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    led, bottles, results, db = ledger_store.Ledger(), bottle_store.Bottles(), [], store.connect()
    for pt in data['patients']:
        if only and pt['id'] not in only: continue
        question = data['question'].format(patient=pt['id'])
        parts, _ = split.known(question)
        outcomes = [router.answer(part, bottles=bottles, s1_ledger=s1_ledger) for part in parts]
        for part, o in zip(parts, outcomes): led.write(run_id, part, o, eval_id=pt['id'])
        ids = [r[0] for r in db.execute('SELECT record_id FROM document WHERE patient_id = ? ORDER BY event_date, record_id', (pt['id'],))]
        texts = store.texts(ids, db)
        mine = [{'quote': d.get('quote'), 'text': d.get('value'), 'record_id': d['record_id']} for d in outcomes[2].get('documents') or [] if d.get('label') == 'yes']
        found, unsupported, extra = _score_reasons(mine, pt['required'], data['optional_everywhere'], texts)
        stack = {'statuses': [o['status'] for o in outcomes], 'found': found, 'unsupported': len(unsupported), 'extra': [e['quote'] for e in extra][:6], 'reasons_reported': len(mine),
                 'correct': all(o['status'] == 'answered' for o in outcomes) and len(found) == len(pt['required']) and not unsupported and not extra,
                 'usd': sum(s.get('usd', 0.0) for o in outcomes for s in o['steps']), 'seconds': round(sum(o['seconds'] for o in outcomes), 1),
                 'frontier_calls': sum(s.get('model') == models.LLM for o in outcomes for s in o['steps']),
                 'distinct_reasons': sorted({_norm_text(m['text'])[:120] for m in mine})[:8]}
        table = lambda o: '\n'.join(' | '.join(str(v) for v in r) for r in [o.get('columns') or []] + (o.get('rows') or []))
        prompt = ('Question: ' + question + '\n\nDISCRETE FIELDS\nPharmacy fills by guideline pillar:\n' + table(outcomes[0]) + '\n\nLatest measurements:\n' + table(outcomes[1]) +
                  '\n\nDOCUMENTS\n\n' + '\n\n'.join(f"[{i}] {texts[i]['event_date']}\n{texts[i]['text'].strip()}" for i in ids if i in texts))
        try:
            text, step = models._claude(models.LLM, HF_READER_SYSTEM, prompt)
            try:
                got = models._json(text)
            except ValueError:
                got = {'reasons': [], 'unusable': text[:200]}
            theirs = [{'quote': x.get('quote'), 'text': str(x.get('medication')) + ' ' + str(x.get('reason')), 'record_id': x.get('record_id')} for x in got.get('reasons') or [] if isinstance(x, dict)]
            f2, u2, e2 = _score_reasons(theirs, pt['required'], data['optional_everywhere'], texts)
            reader = {'found': f2, 'unsupported': len(u2), 'unsupported_quotes': [str(x['quote'])[:160] for x in u2][:4], 'extra': [str(e['quote'])[:160] for e in e2][:6], 'reasons_reported': len(theirs),
                      'correct': 'unusable' not in got and len(f2) == len(pt['required']) and not u2 and not e2, 'usd': step['usd'], 'tokens': step['tokens'], 'seconds': step['seconds'],
                      'summary': str(got.get('summary'))[:500], **({'unusable': got['unusable']} if 'unusable' in got else {})}
        except Exception as exc:
            if 'limit' in str(exc).lower(): raise
            reader = {'found': [], 'unsupported': 0, 'extra': [], 'reasons_reported': 0, 'correct': False, 'usd': 0.0, 'tokens': 0, 'seconds': 0.0, 'error': f'{type(exc).__name__}: {str(exc)[:160]}'}
        results.append({'patient': pt['id'], 'required': pt['required'], 'documents': len(ids), 'characters': len(prompt), 'stack': stack, 'chart_reader': reader})
        show = lambda a: f"{'ok' if a['correct'] else 'MISS':4} found {len(a['found'])}/{len(pt['required'])} unsupported {a['unsupported']} extra {len(a['extra'])} ${a['usd']:.4f} {a['seconds']:5.1f}s"
        print(f"{pt['id']} docs {len(ids):3d} | stack {show(stack)} | reader {show(reader)}", flush=True)
    n, need = len(results), sum(len(r['required']) for r in results)
    lines = [f'HEART-FAILURE REASONS COMPARISON  run {run_id}', f'{n} patients; {need} documented reasons to find; {sum(not r["required"] for r in results)} patients whose record documents none', '']
    for arm, name in (('stack', 'stack       '), ('chart_reader', 'chart reader')):
        rows = [r[arm] for r in results]
        lines.append(f"{name}  patients correct {sum(a['correct'] for a in rows)}/{n}   reasons found {sum(len(a['found']) for a in rows)}/{need}   unsupported quotes {sum(a['unsupported'] for a in rows)}   "
                     f"extra reasons {sum(len(a['extra']) for a in rows)}   ${sum(a['usd'] for a in rows) / n:.4f} per patient   {sum(a['seconds'] for a in rows) / n:.1f}s per patient")
    text = '\n'.join(lines)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n'); (OUT / f'{run_id}.results.json').write_text(json.dumps(results, indent=1))
    return text


def main(argv):
    if argv[0] == 'hf':
        import os
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        paths = [x for x in argv[1:] if x.endswith('.json')]; pick = [x for x in argv[1:] if not x.endswith('.json')]
        print('\n' + hf(os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3'), set(pick[0].split(',')) if pick else None, *paths[:1]))
        return
    if argv[0] == 'compare':
        import os
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        print('\n' + compare(os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3'), set(argv[1].split(',')) if len(argv) > 1 else None))
        return
    if argv[0] == 'cohort':
        import os
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        print('\n' + cohort(os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3')))
        return
    if argv[0] == 'multi':
        import os
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        print('\n' + multi(os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3')))
        return
    if argv[0] == 'chains':
        import os
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        print('\n' + chains(os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3'), *argv[1:2]))
        return
    if argv[0] == 'followups':
        import os
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        print('\n' + followups(os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3')))
        return
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
