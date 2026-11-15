"""Eval harness: run every eval question through the router, score it against its reference answer,
write one ledger row per question, and print the stage report (accuracy, cost per question, tier distribution).

    python -m clinical_core.agere.tiered.evaluate <stage-name> <tier,tier,...> [--bottle-new] [--bottles PATH]
"""
import json
import re
import sys
import time
from collections import Counter
from datetime import timedelta
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
        if len(outcomes) > 3:  # barrier records the earlier extraction stored
            mine += [{'quote': r[2], 'text': f'{r[0]} {r[1]}', 'record_id': r[4]} for r in outcomes[3].get('rows') or [] if r[1] != 'none']
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
        if data.get('extras_reviewed'):
            # The extras were read by a person and accepted as true statements from the record: with that review a
            # patient is complete when every documented reason was found and no quote is unsupported.
            lines.append(f"{name}  with extras reviewed ({data['extras_reviewed']['by']}, {data['extras_reviewed']['on']}): patients with every documented reason found "
                         f"and no unsupported quote {sum(1 for r in results if len(r[arm]['found']) == len(r['required']) and not r[arm]['unsupported'] and 'error' not in r[arm])}/{n}")
    text = '\n'.join(lines)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n'); (OUT / f'{run_id}.results.json').write_text(json.dumps(results, indent=1))
    return text


def takeover_eval(shapes=('novel', 'paraphrase'), workers=4):
    """The takeover rung alone on reference questions: the frontier model is handed each question with no cheaper tier
    tried, and the table it chose is scored against the reference rows by the same rule as every other set. The
    reference is never shown to it. These are questions the tiers already answer; this measures the fallback."""
    from concurrent.futures import ThreadPoolExecutor
    from . import takeover
    items = [q for q in json.loads(EVAL.read_text())['questions'] if q['shape'] in shapes]
    def one(q):
        try:
            got = takeover.answer(q['question'])
        except Exception as exc:
            return {'id': q['id'], 'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:160]}', 'usd': 0.0, 'seconds': 0.0, 'turns': 0}
        if got is None: return {'id': q['id'], 'ok': False, 'error': 'no answer inside the limits', 'usd': 0.0, 'seconds': 0.0, 'turns': takeover.MAX_STEPS}
        ref = json.loads(q['reference_rows']) if isinstance(q['reference_rows'], str) else q['reference_rows']
        table_ok = correct(ref, got['rows'])
        # the answer text may be right when the chosen table has extra rows: every reference number is in the written answer
        said = {t.replace(',', '') for t in __import__('re').findall(r'\d[\d,]*(?:\.\d+)?', got['text'])}
        text_ok = all(str(v if not isinstance(v, float) else round(v, 4)).rstrip('0').rstrip('.') in said or str(v) in said for r in ref for v in r if isinstance(v, (int, float)) and not isinstance(v, bool)) and len(ref) <= 3
        return {'id': q['id'], 'shape': q['shape'], 'question': q['question'], 'ok': table_ok, 'text_has_reference_numbers': text_ok, 'unsupported': got['unsupported'],
                'usd': round(got['usd'], 5), 'seconds': got['seconds'], 'turns': len(got['steps']), 'answer': got['text'][:400], 'sql': got['sql'][:600]}
    with ThreadPoolExecutor(workers) as pool: results = list(pool.map(one, items))
    run = 'takeover-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    n, ok = len(results), sum(r['ok'] for r in results)
    either = sum(r['ok'] or r.get('text_has_reference_numbers', False) for r in results)
    lines = [f'TAKEOVER RUNG EVAL  run {run}', f'items: {n} (shapes: {", ".join(shapes)}; the reference is never shown to the model)',
             f'chosen table matches the reference: {ok}/{n}', f'table matches, or every reference number is in the written answer (1 to 3 row references only): {either}/{n}',
             f'answers containing a number code could not find in what the model was shown: {sum(1 for r in results if r.get("unsupported"))}/{n}',
             f'cost: ${sum(r["usd"] for r in results):.4f} total, ${sum(r["usd"] for r in results) / max(n, 1):.4f} per question; mean {sum(r["seconds"] for r in results) / max(n, 1):.1f}s; mean turns {sum(r["turns"] for r in results) / max(n, 1):.1f}',
             'not matching:'] + [f"  {r['id']} table {r['ok']} text {r.get('text_has_reference_numbers')} {r.get('error') or r.get('answer', '')[:160]}" for r in results if not r['ok']]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'{run}.results.json').write_text(json.dumps(results, indent=1, default=str)); (OUT / f'{run}.report.txt').write_text('\n'.join(lines) + '\n')
    return '\n'.join(lines)


READMIT_READER_SYSTEM = '''You are a clinical assistant reviewing one patient's record: the inpatient stays first, then every document, each
starting with its record id in square brackets. Answer the question from the record only. The question is about the
hospital admission the question names; when it names no date, the patient's most recent readmission within 30 days of an earlier discharge.
Return exactly one JSON object and nothing else:
{"summary": "<two or three sentences answering the question>",
 "readmission_admit_date": "<YYYY-MM-DD admission date of that readmission>",
 "reasons": [{"reason": "<the documented reason for the READMISSION>", "record_id": "<id of the document that states it>",
              "quote": "<the words from that document, copied exactly>"}]}
List a reason only when a document states it for that readmission. If the record documents none, return "reasons": [].'''


def _score_readmission(items, accepted, texts):
    """items: [{'quote','record_id'}] given as reasons for the readmission. -> (correct, unsupported, wrong_stay)."""
    corpus = {i: _norm_text(t['text']) for i, t in texts.items()}
    quote = lambda it: _norm_text(it.get('quote')).split(': ', 1)[-1]
    supported = [it for it in items if len(quote(it)) >= 5 and quote(it) in corpus.get(str(it.get('record_id')), '')]
    good = {a['record_id']: a['phrase'] for a in accepted}
    right = [it for it in supported if str(it['record_id']) in good and good[str(it['record_id'])] in _norm_text(it['quote'])]
    wrong = [it for it in supported if str(it['record_id']) not in good]
    return bool(right), len(items) - len(supported), len(wrong)


def readmit(s1_ledger=None, only=None, path='eval/tiered/readmission_reasons_2.json'):
    """Why was a patient readmitted: the stack's readmission path against a frontier model handed the stays and the whole chart."""
    from . import models, split
    data = json.loads(Path(path).read_text())
    run_id = f"readmit-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    led, bottles, results, db = ledger_store.Ledger(), bottle_store.Bottles(), [], store.connect()
    for pt in data['patients']:
        if only and pt['id'] not in only: continue
        question = data['question'].format(patient=pt['id'], readmit_admit=pt['readmit_admit'])
        parts, _ = split.known(question)
        outcomes = [router.answer(part, bottles=bottles, s1_ledger=s1_ledger) for part in parts]
        for part, o in zip(parts, outcomes): led.write(run_id, part, o, eval_id=pt['id'])
        ids = [r[0] for r in db.execute('SELECT record_id FROM document WHERE patient_id = ? ORDER BY event_date, record_id', (pt['id'],))]
        texts = store.texts(ids, db)
        from . import readmission as _rd
        mine = [{'quote': r[2], 'record_id': r[3]} for r in outcomes[1].get('rows') or [] if r[0] == 'readmission' and r[1] in _rd.ADMISSION_KINDS]
        stated = next((r[1] for r in outcomes[0].get('rows') or [] if r[0] == 'readmission'), None)
        ok, unsupported, wrong = _score_readmission(mine, pt['accepted'], texts)
        stack = {'statuses': [o['status'] for o in outcomes], 'correct': ok and outcomes[1]['status'] == 'answered', 'unsupported': unsupported, 'wrong_stay': wrong,
                 'admit_date_right': stated == pt['readmit_admit'], 'reasons_reported': len(mine), 'usd': sum(s.get('usd', 0.0) for o in outcomes for s in o['steps']),
                 'seconds': round(sum(o['seconds'] for o in outcomes), 1), 'frontier_calls': sum(s.get('model') == models.LLM for o in outcomes for s in o['steps']),
                 'reasons': [m['quote'][:120] for m in mine][:6]}
        stays = '\n'.join(' | '.join(str(v) for v in r) for r in db.execute(
            "SELECT type, admit_date, COALESCE(discharge_date, 'not recorded'), organization_id, readmitted_30d FROM stay WHERE patient_id = ? ORDER BY admit_date", (pt['id'],)))
        prompt = ('Question: ' + question + '\n\nINPATIENT AND OBSERVATION STAYS\ntype | admit_date | discharge_date | organization | followed_by_readmission_within_30_days\n' + stays +
                  '\n\nDOCUMENTS\n\n' + '\n\n'.join(f"[{i}] {texts[i]['event_date']}\n{texts[i]['text'].strip()}" for i in ids if i in texts))
        try:
            text, step = models._claude(models.LLM, READMIT_READER_SYSTEM, prompt)
            try:
                got = models._json(text)
            except ValueError:
                got = {'reasons': [], 'unusable': text[:200]}
            theirs = [{'quote': x.get('quote'), 'record_id': x.get('record_id')} for x in got.get('reasons') or [] if isinstance(x, dict)]
            ok2, u2, w2 = _score_readmission(theirs, pt['accepted'], texts)
            reader = {'correct': ok2 and 'unusable' not in got, 'unsupported': u2, 'wrong_stay': w2, 'admit_date_right': str(got.get('readmission_admit_date')) == pt['readmit_admit'],
                      'reasons_reported': len(theirs), 'usd': step['usd'], 'tokens': step['tokens'], 'seconds': step['seconds'], 'reasons': [str(t['quote'])[:120] for t in theirs][:6],
                      'stated_admit_date': str(got.get('readmission_admit_date'))[:12], 'summary': str(got.get('summary'))[:400], **({'unusable': got['unusable']} if 'unusable' in got else {})}
        except Exception as exc:
            if 'limit' in str(exc).lower(): raise
            reader = {'correct': False, 'unsupported': 0, 'wrong_stay': 0, 'admit_date_right': False, 'reasons_reported': 0, 'usd': 0.0, 'tokens': 0, 'seconds': 0.0,
                      'error': f'{type(exc).__name__}: {str(exc)[:160]}'}
        results.append({'patient': pt['id'], 'documents': len(ids), 'characters': len(prompt), 'stack': stack, 'chart_reader': reader})
        show = lambda a: f"{'ok' if a['correct'] else 'MISS':4} date {'ok' if a['admit_date_right'] else 'no':2} unsupported {a['unsupported']} wrong-stay {a['wrong_stay']} ${a['usd']:.4f} {a['seconds']:5.1f}s"
        print(f"{pt['id']} docs {len(ids):3d} | stack {show(stack)} | reader {show(reader)}", flush=True)
    db.close()
    n = len(results)
    lines = [f'READMISSION REASONS COMPARISON  run {run_id}', f'{n} patients; one documented readmission reason each']
    for arm, name in (('stack', 'stack       '), ('chart_reader', 'chart reader')):
        rows = [r[arm] for r in results]
        lines.append(f"{name}  reason right {sum(a['correct'] for a in rows)}/{n}   readmission date right {sum(a['admit_date_right'] for a in rows)}/{n}   "
                     f"quotes not in the named document {sum(a['unsupported'] for a in rows)}   reasons taken from another stay {sum(a['wrong_stay'] for a in rows)}   "
                     f"${sum(a['usd'] for a in rows) / n:.4f} per patient   {sum(a['seconds'] for a in rows) / n:.1f}s per patient")
    text = '\n'.join(lines)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n'); (OUT / f'{run_id}.results.json').write_text(json.dumps(results, indent=1))
    return text


REVIEW_READER_SYSTEM = '''You are a clinical assistant doing a readmission chart review for one patient. You are given the patient's
structured records (stays, visits, appointments, lab and vital results, conditions, social-need screens) and then every
document, each starting with its record id in square brackets. Answer from the record only, about the hospital admission
on the date the question names (the readmission) and the inpatient stay discharged before it.
Definitions to use:
- one hospital stay: inpatient stay rows of the patient that overlap, repeat, or begin on the day an earlier row ends are one
  stay, from the first admission date to the last discharge date; a row with no discharge date followed within 30 days by a
  row admitted and discharged on one day is one stay ending on that day. The stay before the readmission is the latest such
  stay discharged 1 to 30 days before the readmission began
- follow-up visit within 7 days: a visit dated after the prior discharge, within 7 days of it and before the readmission,
  of a type other than inpatient, emergency, observation, telephone or orders_only
- inpatient admissions in the prior 12 months: hospital stays (as defined above) that began in the 365 days before the
  readmission admission date, not counting the readmission itself
- lab resulted during the readmission: any named lab or vital result dated from the readmission admission date through
  one day after its discharge
- advance directive documented: any document mentions an advance directive, POLST/MOLST, DNR/DNI, code status, full code,
  health care proxy, living will, goals of care, power of attorney, comfort care, hospice or palliative care
Return exactly one JSON object and nothing else:
{"summary": "<three or four sentences>",
 "readmission_reason": {"quote": "<words copied exactly from a document about the readmission>", "record_id": "<its id>"},
 "prior_stay_reason": {"quote": "<words copied exactly from a document about the stay before it>", "record_id": "<its id>"},
 "follow_up_instruction": {"quote": "<the follow-up instruction copied exactly from the prior stay's discharge summary>", "record_id": "<its id>"},
 "follow_up_visit_within_7_days": true or false,
 "days_between_discharge_and_readmission": <integer>,
 "inpatient_admissions_in_prior_12_months": <integer>,
 "any_lab_resulted_during_readmission": true or false,
 "advance_directive_documented": true or false}
Use null for a quote the record does not contain.'''
REVIEW_ITEMS = ('readmission_reason', 'prior_stay_reason', 'follow_up_instruction', 'follow_up_visit_within_7_days', 'days_between', 'prior_admissions', 'any_lab', 'advance_directive')


def _quote_ok(item, want_record, phrase, texts):
    """A quote counts when it is words of the named document, the document is the accepted one, and it contains the phrase."""
    if not isinstance(item, dict): return False
    quote, rid = _norm_text(item.get('quote')).split(': ', 1)[-1] if ': ' in _norm_text(item.get('quote'))[:40] else _norm_text(item.get('quote')), str(item.get('record_id'))
    whole = _norm_text(item.get('quote'))
    doc = _norm_text((texts.get(rid) or {}).get('text'))
    return len(whole) >= 5 and rid == want_record and (whole in doc or quote in doc) and phrase in whole


def _review_prompt(db, question, pid, ra, index_admit, readmit_discharge, ids, texts):
    """The structured rows and every document of one patient, as handed to the frontier model."""
    table = lambda sql, args: '\n'.join(' | '.join(str(v) for v in r) for r in db.execute(sql, args))
    window = (pid, index_admit, readmit_discharge or ra)
    prompt = ('Question: ' + question +
              '\n\nSTAYS (type | admit_date | discharge_date | organization)\n' + table("SELECT type, admit_date, COALESCE(discharge_date, 'not recorded'), organization_id FROM stay WHERE patient_id = ? ORDER BY admit_date", (pid,)) +
              '\n\nVISITS in the year before the readmission (date | type)\n' + table("SELECT event_date, type FROM visit WHERE patient_id = ? AND event_date >= date(?, '-365 day') AND event_date <= ? ORDER BY 1", (pid, ra, ra)) +
              '\n\nAPPOINTMENTS around the two stays (scheduled_date | final_status)\n' + table("SELECT scheduled_date, final_status FROM appointment WHERE patient_id = ? AND scheduled_date >= ? AND scheduled_date <= date(?, '+7 day') ORDER BY 1", window) +
              '\n\nLAB AND VITAL RESULTS around the two stays (date | concept | value | unit)\n' + table("SELECT event_date, concept, value_canonical, unit_canonical FROM measurement WHERE patient_id = ? AND concept IS NOT NULL AND event_date >= ? AND event_date <= date(?, '+1 day') ORDER BY 1, 2", window) +
              '\n\nCHRONIC CONDITIONS (concept | first date)\n' + table("SELECT concept, MIN(event_date) FROM condition WHERE patient_id = ? AND chronic = 1 AND concept IS NOT NULL GROUP BY 1", (pid,)) +
              '\n\nSOCIAL-NEED SCREENS (need | positive | date)\n' + table("SELECT need, positive, event_date FROM social_need WHERE patient_id = ? ORDER BY 3", (pid,)) +
              '\n\nDOCUMENTS\n\n' + '\n\n'.join(f"[{i}] {texts[i]['event_date']}\n{texts[i]['text'].strip()}" for i in ids if i in texts))
    return prompt


def review(s1_ledger=None, only=None, path='eval/tiered/readmission_review_2.json'):
    """A readmission chart review, eight items per patient: the stack's five-part review against a frontier model handed the
    structured rows and the whole chart."""
    from . import models, readmission as rd, split
    data = json.loads(Path(path).read_text())
    run_id = f"review-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    led, bottles, results, db = ledger_store.Ledger(), bottle_store.Bottles(), [], store.connect()
    for pt in data['patients']:
        if only and pt['id'] not in only: continue
        pid, ra = pt['id'], pt['readmit_admit']
        question = data['question'].format(patient=pid, readmit_admit=ra)
        parts, _ = split.known(question)
        outcomes = [router.answer(part, bottles=bottles, s1_ledger=s1_ledger) for part in parts]
        for part, o in zip(parts, outcomes): led.write(run_id, part, o, eval_id=pid)
        ids = [r[0] for r in db.execute('SELECT record_id FROM document WHERE patient_id = ? ORDER BY event_date, record_id', (pid,))]
        texts = store.texts(ids, db)
        want = {'readmission_reason': (pt['readmission_reason']['record_id'], pt['readmission_reason']['phrase']),
                'prior_stay_reason': (pt['prior_stay_reason']['record_id'], pt['prior_stay_reason']['phrase']),
                'follow_up_instruction': (pt['follow_up_instruction']['record_id'], 'follow')}
        truth = {'follow_up_visit_within_7_days': pt['follow_up_visit_within_7_days'], 'days_between': pt['days_between'], 'prior_admissions': pt['inpatient_admissions_in_prior_12_months'],
                 'any_lab': bool(pt['lab_concepts_resulted_during_readmission']), 'advance_directive': pt['advance_directive_documented']}
        rows = lambda n: (outcomes[n].get('rows') or []) if len(outcomes) > n else []
        said = lambda stay, kinds: [{'quote': r[2], 'record_id': r[3]} for r in rows(1) if r[0] == stay and r[1] in kinds]
        fact = lambda n, name: next((r[1] for r in rows(n) if r[0] == name), None)
        num = lambda v: int(v) if str(v).lstrip('-').isdigit() else None
        mine = {'readmission_reason': any(_quote_ok(q, *want['readmission_reason'], texts) for q in said('readmission', rd.ADMISSION_KINDS)),
                'prior_stay_reason': any(_quote_ok(q, *want['prior_stay_reason'], texts) for q in said('stay before the readmission', rd.ADMISSION_KINDS)),
                'follow_up_instruction': any(_quote_ok(q, *want['follow_up_instruction'], texts) for q in said('stay before the readmission', ('follow-up instruction',))),
                'follow_up_visit_within_7_days': (fact(2, 'outpatient visit within 7 days of discharge') == 'yes') == truth['follow_up_visit_within_7_days'],
                'days_between': num(next((r[4] for r in rows(0) if r[0] == 'readmission'), None)) == truth['days_between'],
                'prior_admissions': num(fact(3, 'inpatient admissions in the prior 12 months')) == truth['prior_admissions'],
                'any_lab': ((num(next((r[2] for r in rows(4) if r[1] == '(any lab or vital)'), None)) or 0) > 0) == truth['any_lab'],
                'advance_directive': any(r[1] == 'advance directive or code status' and r[3] for r in rows(1)) == truth['advance_directive']}
        stack = {'items': mine, 'right': sum(mine.values()), 'statuses': [o['status'] for o in outcomes], 'usd': sum(s.get('usd', 0.0) for o in outcomes for s in o['steps']),
                 'seconds': round(sum(o['seconds'] for o in outcomes), 1), 'frontier_calls': sum(s.get('model') == models.LLM for o in outcomes for s in o['steps'])}
        prompt = _review_prompt(db, question, pid, ra, pt['index_admit'], pt['readmit_discharge'], ids, texts)
        try:
            text, step = models._claude(models.LLM, REVIEW_READER_SYSTEM, prompt)
            try:
                got = models._json(text)
            except ValueError:
                got = {'unusable': text[:200]}
            theirs = {'readmission_reason': _quote_ok(got.get('readmission_reason'), *want['readmission_reason'], texts),
                      'prior_stay_reason': _quote_ok(got.get('prior_stay_reason'), *want['prior_stay_reason'], texts),
                      'follow_up_instruction': _quote_ok(got.get('follow_up_instruction'), *want['follow_up_instruction'], texts),
                      'follow_up_visit_within_7_days': got.get('follow_up_visit_within_7_days') is truth['follow_up_visit_within_7_days'],
                      'days_between': got.get('days_between_discharge_and_readmission') == truth['days_between'],
                      'prior_admissions': got.get('inpatient_admissions_in_prior_12_months') == truth['prior_admissions'],
                      'any_lab': got.get('any_lab_resulted_during_readmission') is truth['any_lab'],
                      'advance_directive': got.get('advance_directive_documented') is truth['advance_directive']}
            reader = {'items': theirs, 'right': sum(theirs.values()), 'usd': step['usd'], 'tokens': step['tokens'], 'seconds': step['seconds'], 'summary': str(got.get('summary'))[:500],
                      'stated': {k: got.get(k) for k in ('follow_up_visit_within_7_days', 'days_between_discharge_and_readmission', 'inpatient_admissions_in_prior_12_months',
                                                         'any_lab_resulted_during_readmission', 'advance_directive_documented')}, **({'unusable': got['unusable']} if 'unusable' in got else {})}
        except Exception as exc:
            if 'limit' in str(exc).lower(): raise
            reader = {'items': {k: False for k in REVIEW_ITEMS}, 'right': 0, 'usd': 0.0, 'tokens': 0, 'seconds': 0.0, 'error': f'{type(exc).__name__}: {str(exc)[:160]}'}
        results.append({'patient': pid, 'documents': len(ids), 'characters': len(prompt), 'stack': stack, 'chart_reader': reader})
        miss = lambda a: ','.join(k for k, v in a['items'].items() if not v) or '-'
        print(f"{pid} docs {len(ids):3d} | stack {stack['right']}/8 ${stack['usd']:.4f} {stack['seconds']:5.1f}s miss {miss(stack)} | reader {reader['right']}/8 ${reader['usd']:.4f} {reader['seconds']:5.1f}s miss {miss(reader)}", flush=True)
    db.close()
    n = len(results)
    lines = [f'READMISSION REVIEW COMPARISON  run {run_id}', f'{n} patients; 8 items each ({n * 8} in all)']
    for arm, name in (('stack', 'stack       '), ('chart_reader', 'chart reader')):
        rows_ = [r[arm] for r in results]
        lines.append(f"{name}  items right {sum(a['right'] for a in rows_)}/{n * 8}   patients with all 8 right {sum(a['right'] == 8 for a in rows_)}/{n}   "
                     f"${sum(a['usd'] for a in rows_) / n:.4f} per patient   {sum(a['seconds'] for a in rows_) / n:.1f}s per patient")
        lines.append(f"{name}  by item: " + '  '.join(f"{k} {sum(a['items'][k] for a in rows_)}/{n}" for k in REVIEW_ITEMS))
    text = '\n'.join(lines)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n'); (OUT / f'{run_id}.results.json').write_text(json.dumps(results, indent=1))
    return text


def _episodes(raw):
    """Hospital episodes worked out in Python, separately from the stack's SQL. raw: (patient, admit, discharge) inpatient rows.
    A stay written as an open row plus a same-day row on its discharge day is one stay; rows of a patient that overlap, repeat
    or begin on the day an earlier row ends are one episode. -> [(patient, first admit, last discharge)]."""
    from collections import defaultdict
    from datetime import date
    day = lambda x: date.fromisoformat(str(x)[:10])
    by = defaultdict(list)
    for p, a, d in raw: by[p].append((a, d))
    out = []
    for p, rows in by.items():
        opens = [a for a, d in rows if not d]
        tails = {a for a, d in rows if d and d == a and any(0 < (day(a) - day(b)).days <= 30 for b in opens)}
        clean = sorted(((a, d or min((b for b in tails if 0 < (day(b) - day(a)).days <= 30), default=None)) for a, d in rows if not (d == a and a in tails)),
                       key=lambda t: (t[0], t[1] is None, t[1] or ''))
        current = None
        for a, d in clean:
            if current and current[1] and a <= current[1]: current[1] = max(current[1], d or current[1])
            else:
                if current: out.append((p, current[0], current[1]))
                current = [a, d]
        if current: out.append((p, current[0], current[1]))
    return out


def committee(year='2029', s1_ledger=None, workers=4, limit=None):
    """Every readmission in one year, two ways. The stack: one case-list question. A frontier model doing all the work: it is
    handed each readmission's structured rows and whole chart, one call per readmission, and code adds up its answers.
    Table items are scored against counts made here straight from the stay and visit tables; note items are compared case
    by case and every disagreement is written out for reading."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date
    from . import models
    run_id = f"committee-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    db, bottles, day = store.connect(), bottle_store.Bottles(), lambda x: date.fromisoformat(str(x)[:10])
    raw = db.execute("SELECT patient_id, admit_date, discharge_date FROM stay WHERE type = 'inpatient'").fetchall()
    stays = _episodes(raw)
    cases = {}
    for pid, admit, _ in stays:                                      # the reference, without the stack's SQL
        if not str(admit).startswith(year): continue
        prior = [(d, a) for p, a, d in stays if p == pid and d and a < admit and 1 <= (day(admit) - day(d)).days <= 30]
        if not prior: continue
        dis, index_admit = max(prior)
        visits = [v[0] for v in db.execute("SELECT event_date FROM visit WHERE patient_id = ? AND type NOT IN ('inpatient', 'emergency', 'observation', 'telephone', 'orders_only')", (pid,))]
        cases[(pid, admit)] = {'patient': pid, 'readmit_admit': admit, 'index_admit': index_admit, 'index_discharge': dis,
                               'readmit_discharge': max((d for p, a, d in stays if p == pid and a == admit and d), default=None),
                               'days_between': (day(admit) - day(dis)).days,
                               'visit7': any(0 < (day(v) - day(dis)).days <= 7 and day(v) < day(admit) for v in visits),
                               'prior_admissions': len({a for p, a, d in stays if p == pid and a < admit and (day(admit) - day(a)).days <= 365})}
    keys = sorted(cases)[:limit]
    outcome = router.answer(f'List the readmissions in {year}', bottles=bottles, s1_ledger=s1_ledger)
    ledger_store.Ledger().write(run_id, f'List the readmissions in {year}', outcome, eval_id='committee')
    mine = {(r[0], r[2]): {'visit7': r[8] == 'yes', 'days_between': r[3], 'reason': not str(r[4]).startswith('not stated'), 'reason_text': r[4],
                           'instruction': r[7] != 'none on record', 'instruction_text': r[7]} for r in outcome.get('rows') or []}
    stack = {'status': outcome['status'], 'tier': outcome['final_tier'], 'usd': sum(s.get('usd', 0.0) for s in outcome['steps']), 'seconds': outcome['seconds'],
             'frontier_calls': sum(s.get('model') == models.LLM for s in outcome['steps'])}

    def read(key):
        c = cases[key]
        own = store.connect()
        try:
            ids = [r[0] for r in own.execute('SELECT record_id FROM document WHERE patient_id = ? ORDER BY event_date, record_id', (c['patient'],))]
            texts = store.texts(ids, own)
            question = (f"Review the readmission of patient {c['patient']} on {c['readmit_admit']}: why were they readmitted, what did the chart say, did they get follow-up, "
                        'what are their risk factors, are there advance directives, and what do the labs and imaging show?')
            prompt = _review_prompt(own, question, c['patient'], c['readmit_admit'], c['index_admit'], c['readmit_discharge'], ids, texts)
        finally:
            own.close()
        corpus = [_norm_text(t['text']) for t in texts.values()]
        quoted = lambda item: isinstance(item, dict) and len(_norm_text(item.get('quote'))) >= 5 and any(_norm_text(item['quote']) in doc for doc in corpus)
        step = {'usd': 0.0, 'tokens': 0, 'seconds': 0.0}
        try:
            text, step = models._claude(models.LLM, REVIEW_READER_SYSTEM, prompt)
            got = models._json(text)
        except ValueError:
            return {'unusable': True, 'usd': step['usd'], 'tokens': step['tokens'], 'seconds': step['seconds'], 'characters': len(prompt)}
        except Exception as exc:
            if 'limit' in str(exc).lower(): raise
            return {'error': f'{type(exc).__name__}: {str(exc)[:160]}', 'usd': 0.0, 'tokens': 0, 'seconds': 0.0, 'characters': len(prompt)}
        said = lambda k: (got.get(k) or {}).get('quote') if isinstance(got.get(k), dict) else None
        return {'visit7': got.get('follow_up_visit_within_7_days'), 'days_between': got.get('days_between_discharge_and_readmission'),
                'prior_admissions': got.get('inpatient_admissions_in_prior_12_months'), 'reason': quoted(got.get('readmission_reason')), 'reason_text': said('readmission_reason'),
                'reason_not_in_chart': bool(said('readmission_reason')) and not quoted(got.get('readmission_reason')),
                'instruction': quoted(got.get('follow_up_instruction')), 'instruction_text': said('follow_up_instruction'),
                'instruction_not_in_chart': bool(said('follow_up_instruction')) and not quoted(got.get('follow_up_instruction')),
                'usd': step['usd'], 'tokens': step['tokens'], 'seconds': step['seconds'], 'characters': len(prompt)}

    started = time.monotonic()
    with ThreadPoolExecutor(workers) as pool:
        theirs = dict(zip(keys, pool.map(read, keys)))
    wall = round(time.monotonic() - started, 1)
    n = len(keys)
    failed = [k for k in keys if 'visit7' not in theirs[k]]
    table = lambda arm, item: sum(1 for k in keys if k in arm and arm[k].get(item) == cases[k][item])
    tally = lambda arm, test: sum(1 for k in keys if k in arm and 'visit7' in arm[k] and test(arm[k]))
    lines = [f'READMISSION COMMITTEE COMPARISON  run {run_id}', f'{n} readmissions in {year} (stay and visit tables, each hospital episode counted once, worked out here without the stack)', '',
             'cost and time for the whole year',
             f"  stack                       ${stack['usd']:.4f}   {stack['seconds']:.1f}s   frontier calls {stack['frontier_calls']}   ({stack['status']} by {stack['tier']})",
             f"  frontier model, every chart ${sum(t['usd'] for t in theirs.values()):.2f}   {sum(t['seconds'] for t in theirs.values()):.0f}s of model time ({wall}s on the clock, {workers} at a time)   "
             f"{sum(t['tokens'] for t in theirs.values())} tokens   {n} calls   {len(failed)} calls gave no usable answer", '',
             'items with an answer in the tables (right / readmissions)',
             f"  outpatient visit within 7 days   stack {table(mine, 'visit7')}/{n}   frontier model {table(theirs, 'visit7')}/{n}",
             f"  days from discharge to readmission   stack {table(mine, 'days_between')}/{n}   frontier model {table(theirs, 'days_between')}/{n}",
             f"  inpatient admissions in the prior 12 months   stack (not on its list)   frontier model {table(theirs, 'prior_admissions')}/{n}", '',
             'the counts a committee would be shown (reference from the tables where one exists)',
             f"  no outpatient visit within 7 days   reference {sum(not cases[k]['visit7'] for k in keys)}   stack {tally(mine, lambda a: not a['visit7'])}   frontier model {tally(theirs, lambda a: a['visit7'] is False)}",
             f"  chart states a reason for the readmission   stack {tally(mine, lambda a: a['reason'])}   frontier model {tally(theirs, lambda a: a['reason'])}",
             f"  prior discharge summary gave a follow-up instruction   stack {tally(mine, lambda a: a['instruction'])}   frontier model {tally(theirs, lambda a: a['instruction'])}",
             f"  instruction given and no visit within 7 days   stack {tally(mine, lambda a: a['instruction'] and not a['visit7'])}   frontier model {tally(theirs, lambda a: a['instruction'] and a['visit7'] is False)}",
             f"  frontier model quotes not found in the chart   reason {tally(theirs, lambda a: a['reason_not_in_chart'])}   instruction {tally(theirs, lambda a: a['instruction_not_in_chart'])}", '',
             'note items where the two differ (to be read case by case)']
    differ = [(k, item) for k in keys for item in ('reason', 'instruction') if k in mine and 'visit7' in theirs[k] and mine[k][item] != theirs[k][item]]
    lines += [f"  {k[0]} {k[1]} {item}: stack [{str(mine[k][item + '_text'])[:110]}] | frontier model [{str(theirs[k][item + '_text'])[:110]}]" for k, item in differ] or ['  none']
    text = '\n'.join(lines)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n')
    (OUT / f'{run_id}.results.json').write_text(json.dumps([{'case': cases[k], 'stack': mine.get(k), 'frontier': theirs[k]} for k in keys], indent=1))
    db.close()
    return text


DRIVER_READER_SYSTEM = '''You are a clinical assistant reviewing one hospital readmission. You are given the patient's structured records
and then every document, each starting with its record id in square brackets. Answer from the record only, about the admission on
the date the question names (the readmission) and the hospital stay discharged before it.
One hospital stay: inpatient stay rows that overlap, repeat, or begin on the day an earlier row ends are one stay; a row with no
discharge date followed within 30 days by a row admitted and discharged on one day is one stay ending on that day.
Mark each of five drivers true or false by exactly these definitions:
- advanced_illness: heart failure, chronic kidney disease, COPD, cancer or dementia among the conditions listed, first recorded on or
  before the readmission date; or five or more listed conditions by that date, counting diabetes with type 2 diabetes as one and heart
  failure with heart failure reduced ef as one; or three or more hospital stays beginning in the 365
  days before the admission date of the stay before the readmission; or age 85 or over
- social_need: a positive social-need screen dated on or before the readmission; or coverage uninsured; or, in the latest social-data
  vendor row, low food access = 1 or an isolation, transport, housing or financial-stress score of 8 or more
- emergency_department_threshold: a document about the readmission says the workup was unremarkable and the patient was admitted
- sent_back_from_a_facility: the stay before the readmission ended in discharge to a skilled nursing or rehabilitation facility
  (the admission-discharge feed's disposition for that discharge date, or else the Disposition line of that stay's discharge summary)
- self_care_at_home: that stay ended in discharge with home health (same two sources, and not to a facility); or dementia among the
  chronic conditions by the readmission date; or age 75 or over with household size 1 in the vendor row; or low health literacy in
  the vendor row
Return exactly one JSON object and nothing else:
{"advanced_illness": true or false, "social_need": true or false, "emergency_department_threshold": true or false,
 "sent_back_from_a_facility": true or false, "self_care_at_home": true or false, "why": "<one sentence>"}'''
DRIVER_KEYS = ('advanced_illness', 'social_need', 'emergency_department_threshold', 'sent_back_from_a_facility', 'self_care_at_home')


def drivers_eval(year='2029', s1_ledger=None, workers=4, limit=None):
    """The five drivers of a return, three ways: a reference worked out here in Python straight from the tables and documents, the
    stack's driver list, and a frontier model handed the rows and whole chart of each readmission with the same definitions."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date
    from . import models
    run_id = f"drivers-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    db, bottles, day = store.connect(), bottle_store.Bottles(), lambda x: date.fromisoformat(str(x)[:10])
    eps = _episodes(db.execute("SELECT patient_id, admit_date, discharge_date FROM stay WHERE type = 'inpatient'").fetchall())
    cases = {}
    for pid, admit, out in eps:                                   # the reference: its own queries, its own wording checks
        if not str(admit).startswith(year): continue
        prior = [(d, a) for p, a, d in eps if p == pid and d and a < admit and 1 <= (day(admit) - day(d)).days <= 30]
        if not prior: continue
        dis, ia = max(prior)
        age, cover = db.execute('SELECT age_latest, coverage_type FROM patient WHERE patient_id = ?', (pid,)).fetchone() or (None, None)
        age = int(age) if str(age or '').isdigit() else -1
        known_chronic = {r[0] for r in db.execute('SELECT concept FROM condition WHERE chronic = 1 AND concept IS NOT NULL')}
        chronic = {r[0] for r in db.execute("SELECT concept FROM condition WHERE patient_id = ? AND concept IS NOT NULL AND chronic IS NOT 0 AND COALESCE(event_date, '') <= ?", (pid, admit))} & known_chronic
        stays_before = sum(1 for p, a, d in eps if p == pid and a < ia and (day(ia) - day(a)).days <= 365)
        v = db.execute('SELECT social_isolation_score, transportation_risk_score, housing_instability_score, financial_stress_score, food_access_low, est_household_size, health_literacy_estimate '
                       'FROM sdoh_vendor WHERE patient_id = ? ORDER BY delivery_date DESC LIMIT 1', (pid,)).fetchone()
        screen = db.execute('SELECT 1 FROM social_need WHERE patient_id = ? AND positive = 1 AND event_date <= ?', (pid, admit)).fetchone()
        feed = db.execute('SELECT discharge_disposition FROM adt_visit WHERE patient_id = ? AND discharge_date = ? AND discharge_disposition IS NOT NULL', (pid, dis)).fetchone()
        went = str(feed[0]).lower() if feed else ''
        docs = db.execute("SELECT record_id, event_date, document_type FROM document WHERE patient_id = ? AND event_date >= ? AND event_date <= date(?, '+30 day')", (pid, ia, out or admit)).fetchall()
        texts = store.texts([r[0] for r in docs], db)
        ed = False
        for rid, when, kind in docs:
            text = ' '.join((texts.get(rid) or {}).get('text', '').split())
            named = re.search(r'Admission date: (\d{4}-\d\d-\d\d)', text)
            about_readmission = (admit <= named.group(1) <= (out or admit)) if named else (admit <= str(when)[:10] <= str(day(out or admit) + timedelta(days=7)))
            about_prior = (ia <= named.group(1) <= dis) if named else (ia <= str(when)[:10] <= str(day(dis) + timedelta(days=3)))
            after = min((a for p, a, d in eps if p == pid and a > admit), default=None)   # the patient's next admission, from the episodes worked out here
            if about_readmission and (named or not after or str(when)[:10] < after) and re.search(r'workup unremarkable[^.]*\.\s*admitted', text, re.I): ed = True
            if not went and about_prior and not about_readmission and 'discharge' in str(kind).lower():
                m = re.search(r'Disposition\s*:?\s+([^.]{2,80})', text, re.I)
                if m: went = m.group(1).split(' Follow')[0].strip().lower()
        facility = bool(re.search(r'snf|skilled nursing|nursing (?:home|facility)|irf|rehab', went))
        cases[(pid, admit)] = {'patient': pid, 'readmit_admit': admit, 'index_admit': ia, 'index_discharge': dis, 'readmit_discharge': out, 'went': went,
            'advanced_illness': bool(chronic & {'heart_failure', 'heart_failure_reduced_ef', 'chronic_kidney_disease', 'copd', 'cancer', 'dementia'}) or len({'diabetes' if 'diabetes' in x else 'heart_failure' if x.startswith('heart_failure') else x for x in chronic}) >= 5 or stays_before >= 3 or age >= 85,
            'social_need': bool(screen) or str(cover or '').lower() == 'uninsured' or bool(v and (v[4] in (1, '1') or any(x is not None and x >= 8 for x in v[:4]))),
            'emergency_department_threshold': ed, 'sent_back_from_a_facility': facility,
            'self_care_at_home': (not facility and 'home' in went and 'health' in went) or 'dementia' in chronic or bool(v and ((v[5] in (1, '1') and age >= 75) or str(v[6] or '').lower() == 'low'))}
    keys = sorted(cases)[:limit]
    outcome = router.answer(f'List the readmissions in {year} by driver', bottles=bottles, s1_ledger=s1_ledger)
    ledger_store.Ledger().write(run_id, f'List the readmissions in {year} by driver', outcome, eval_id='drivers')
    mine = {(r[0], r[2]): dict(zip(DRIVER_KEYS, (x != 'no' for x in r[4:9]))) for r in outcome.get('rows') or []}
    stack = {'status': outcome['status'], 'tier': outcome['final_tier'], 'usd': sum(s.get('usd', 0.0) for s in outcome['steps']), 'seconds': outcome['seconds'],
             'frontier_calls': sum(s.get('model') == models.LLM for s in outcome['steps'])}

    def read(key):
        c = cases[key]
        own = store.connect()
        try:
            ids = [r[0] for r in own.execute('SELECT record_id FROM document WHERE patient_id = ? ORDER BY event_date, record_id', (c['patient'],))]
            texts = store.texts(ids, own)
            table = lambda sql, args: '\n'.join(' | '.join(str(v) for v in r) for r in own.execute(sql, args)) or '(none)'
            pid = c['patient']
            question = f"Which of the five drivers does the record mark for the readmission of patient {pid} on {c['readmit_admit']}?"
            base = _review_prompt(own, question, pid, c['readmit_admit'], c['index_admit'], c['readmit_discharge'], ids, texts).split('\n\nDOCUMENTS\n\n')[0]
            base = base.split('\n\nCHRONIC CONDITIONS')[0] + '\n\nSOCIAL-NEED SCREENS' + base.split('\n\nSOCIAL-NEED SCREENS')[1]   # one condition list only, given below
            prompt = (base +
                      '\n\nPATIENT (age | coverage)\n' + table('SELECT age_latest, coverage_type FROM patient WHERE patient_id = ?', (pid,)) +
                      '\n\nCONDITIONS ON RECORD WITH FIRST DATE (concept | first date)\n' + table("SELECT concept, MIN(COALESCE(event_date, '')) FROM condition WHERE patient_id = ? AND concept IS NOT NULL AND chronic IS NOT 0 GROUP BY 1", (pid,)) +
                      '\n\nADMISSION-DISCHARGE FEED (admit | discharge | disposition)\n' + table('SELECT admit_date, discharge_date, discharge_disposition FROM adt_visit WHERE patient_id = ? ORDER BY 1', (pid,)) +
                      '\n\nSOCIAL-DATA VENDOR, latest row (isolation | transport | housing | financial stress | low food access | household size | health literacy)\n' +
                      table('SELECT social_isolation_score, transportation_risk_score, housing_instability_score, financial_stress_score, food_access_low, est_household_size, health_literacy_estimate FROM sdoh_vendor WHERE patient_id = ? ORDER BY delivery_date DESC LIMIT 1', (pid,)) +
                      '\n\nDOCUMENTS\n\n' + '\n\n'.join(f"[{i}] {texts[i]['event_date']}\n{texts[i]['text'].strip()}" for i in ids if i in texts))
        finally:
            own.close()
        step = {'usd': 0.0, 'tokens': 0, 'seconds': 0.0}
        try:
            text, step = models._claude(models.LLM, DRIVER_READER_SYSTEM, prompt)
            got = models._json(text)
        except ValueError:
            return {'unusable': True, **{k: step[k] for k in ('usd', 'tokens', 'seconds')}}
        except Exception as exc:
            if 'limit' in str(exc).lower(): raise
            return {'error': f'{type(exc).__name__}: {str(exc)[:160]}', 'usd': 0.0, 'tokens': 0, 'seconds': 0.0}
        return {**{k: got.get(k) is True for k in DRIVER_KEYS}, 'why': str(got.get('why'))[:300], **{k: step[k] for k in ('usd', 'tokens', 'seconds')}}

    started = time.monotonic()
    with ThreadPoolExecutor(workers) as pool:
        theirs = dict(zip(keys, pool.map(read, keys)))
    wall, n = round(time.monotonic() - started, 1), len(keys)
    right = lambda arm, k: sum(1 for key in keys if key in arm and arm[key].get(k) == cases[key][k])
    yes = lambda arm, k: sum(1 for key in keys if key in arm and arm[key].get(k) is True)
    lines = [f'READMISSION DRIVERS COMPARISON  run {run_id}', f'{n} readmissions in {year}; five drivers each ({n * 5} items); reference worked out here in Python', '',
             f"  stack                       ${stack['usd']:.4f}   {stack['seconds']:.1f}s   frontier calls {stack['frontier_calls']}   ({stack['status']} by {stack['tier']})",
             f"  frontier model, every chart ${sum(t['usd'] for t in theirs.values()):.2f}   {sum(t['seconds'] for t in theirs.values()):.0f}s of model time ({wall}s on the clock, {workers} at a time)   "
             f"{sum(t['tokens'] for t in theirs.values())} tokens   {sum(1 for t in theirs.values() if 'why' not in t)} calls gave no usable answer", '',
             f"  items right of {n * 5}:   stack {sum(right(mine, k) for k in DRIVER_KEYS)}   frontier model {sum(right(theirs, k) for k in DRIVER_KEYS)}", '']
    lines += [f"  {k:32} reference {sum(cases[key][k] for key in keys):3d}   stack {yes(mine, k):3d} (right {right(mine, k)}/{n})   frontier model {yes(theirs, k):3d} (right {right(theirs, k)}/{n})" for k in DRIVER_KEYS]
    lines += ['', 'where an arm differs from the reference']
    lines += [f"  {key[0]} {key[1]} {k}: reference {cases[key][k]} | stack {mine.get(key, {}).get(k)} | frontier model {theirs[key].get(k)} | {str(theirs[key].get('why'))[:150]}"
              for key in keys for k in DRIVER_KEYS if mine.get(key, {}).get(k) != cases[key][k] or theirs[key].get(k) != cases[key][k]] or ['  none']
    text = '\n'.join(lines)
    (OUT / f'{run_id}.report.txt').write_text(text + '\n')
    (OUT / f'{run_id}.results.json').write_text(json.dumps([{'case': cases[k], 'stack': mine.get(k), 'frontier': theirs[k]} for k in keys], indent=1))
    db.close()
    return text


REWORD_SYSTEM = '''Reword one analytics question so it asks exactly the same thing in clearly different words and word order.
Keep every value exactly as written (years, months, numbers, names of conditions, drug classes, payers, document types).
Do not add or remove any filter, grouping or measure. Return exactly one JSON object: {"reworded": "<the question>"}.'''


def near_eval(limit=60, s1_ledger=None):
    """Does a saved recipe answer a rewording of its own question? For promoted saved shapes, the small model rewords the
    original question; the reference is the saved recipe's own rows for the original. Each rewording runs through the tiers
    below the frontier, with the near-match rung off and then on. Reads the saved recipes; saves nothing."""
    import os, shutil, tempfile
    os.environ.pop('ANTHROPIC_API_KEY', None)
    from run_integrated_stack import keys
    keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
    from . import models
    scratch = Path(tempfile.mkdtemp(prefix='near-eval-'))
    shutil.copy(os.environ.get('AGERE_TIERED_BOTTLES', bottle_store.DEFAULT), scratch / 'bottles.sqlite3')
    bottles, db = bottle_store.Bottles(scratch / 'bottles.sqlite3'), store.connect()
    saved = [b for b in bottles.full() if b['status'] == 'promoted' and not b.get('read') and (b.get('provenance') or {}).get('question')]
    picked = saved[::max(1, len(saved) // int(limit))][:int(limit)]
    below = ('bottles', 'recipes', 'system1', 'slm', 'near')
    lines, tally, usd_near, sec_near = [], {'off': {}, 'on': {}}, 0.0, 0.0
    for b in picked:
        question = b['provenance']['question']
        hit = bottles.match(question, statuses=('promoted',))
        if not hit or hit[0]['id'] != b['id']: continue
        reference = store.run(b['sql'], hit[1], db)[1]
        try:
            text, _ = models._claude(models.SLM, REWORD_SYSTEM, question)
            reworded = ' '.join(str(models._json(text).get('reworded') or '').split())
        except Exception as exc:
            if 'limit' in str(exc).lower(): raise
            continue
        if len(reworded) < 12 or bottles.match(reworded, statuses=('promoted',)): continue  # an exact match would not test the rung
        row = {}
        for mode in ('off', 'on'):
            os.environ['AGERE_TIERED_NEAR'] = mode
            try:
                o = router.answer(reworded, tiers=below, bottles=bottles, s1_ledger=s1_ledger)
            except Exception as exc:
                if 'limit' in str(exc).lower(): raise
                o = {'status': 'error', 'final_tier': None, 'rows': None, 'steps': [], 'seconds': 0.0}
            right = o['status'] == 'answered' and correct(reference, o['rows']) and correct(o['rows'], reference)
            kind = (f"{o['final_tier']} right" if right else f"{o['final_tier']} WRONG") if o['status'] == 'answered' else 'left for the frontier model'
            tally[mode][kind] = tally[mode].get(kind, 0) + 1; row[mode] = kind
            if mode == 'on' and o.get('final_tier') == 'near':
                usd_near += sum(s.get('usd', 0.0) for s in o['steps']); sec_near += o['seconds']
        line = f"{b['id']:4} off: {row['off']:28} on: {row['on']:28} | {question[:70]} -> {reworded[:90]}"
        print(line, flush=True); lines.append(line)
    os.environ['AGERE_TIERED_NEAR'] = 'off'
    n = sum(tally['on'].values()); near_n = sum(v for k, v in tally['on'].items() if k.startswith('near'))
    summary = (f"near-match rung on {n} rewordings of saved questions | rung off: {tally['off']} | rung on: {tally['on']} | "
               f"near answers: ${usd_near / near_n if near_n else 0:.4f} and {sec_near / near_n if near_n else 0:.1f}s each")
    (OUT / f"near-eval-{time.strftime('%Y%m%d-%H%M%S')}.txt").write_text('\n'.join(lines + [summary]) + '\n')
    return summary


def methods_eval(path='eval/tiered/methods.json', config=None, workers=1):
    """Routing check for the methods rung on the real warehouse: each question through the whole app ladder.
    Scored on which rung answered and which method ran (eval/tiered/methods.json), not on the figures."""
    import json, os, time
    from pathlib import Path
    os.environ.pop('ANTHROPIC_API_KEY', None)
    from run_integrated_stack import keys
    keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
    from .. import model_policy
    os.environ.update({k: v for k, v in model_policy.ECONOMICAL_RUNTIME.items() if isinstance(v, str)})
    from ..local_release import validate_config
    from ..clinical.stack_runtime import Stack
    from . import app, bottles as bottle_store
    # The run reads the saved recipes but writes to copies: it saves no recipe, starts no learning and keeps its own ledger.
    import shutil, tempfile
    scratch = Path(tempfile.mkdtemp(prefix='methods-eval-'))
    source = Path(os.environ.get('AGERE_TIERED_BOTTLES', bottle_store.DEFAULT))
    if source.exists(): shutil.copy(source, scratch / 'bottles.sqlite3')
    os.environ.update({'AGERE_TIERED_BOTTLES': str(scratch / 'bottles.sqlite3'), 'AGERE_TIERED_LEDGER': str(scratch / 'ledger.sqlite3'), 'AGERE_TIERED_AUTO_LEARN': 'off'})
    config = config or os.environ.get('AGERE_TIERED_STACK_CONFIG') or 'artifacts/agere-full-stack-rerun-2026-10-30/focused/evidence-work-network/active-config.json'
    stack = Stack(Path(validate_config(config)['build']) / 'stack', allow_live=False)
    items = json.loads(Path(path).read_text())['questions']
    lines, right, usd, seconds, by = [], 0, 0.0, 0.0, {'method_right': 0, 'method_wrong': 0, 'method_missed': 0, 'plain_kept': 0, 'plain_taken': 0}
    for item in items:
        t0 = time.monotonic()
        try:
            result = app.try_answer(stack, item['question'])
        except Exception as exc:
            result = None; print(f"{item['id']} error {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        took = round(time.monotonic() - t0, 1)
        core = (result or {}).get('tiered_core') or {}
        ran = core.get('methods') or []
        cost = ((result or {}).get('planning_economics') or {}).get('known_api_estimate_usd') or 0.0
        wanted = [item['method']] if isinstance(item['method'], str) else list(item['method'] or [])
        if wanted:
            kind = 'method_right' if set(wanted) & set(ran) else 'method_wrong' if ran else 'method_missed'
        else:
            kind = 'plain_taken' if ran else 'plain_kept'
        ok = kind in ('method_right', 'plain_kept')
        by[kind] += 1; right += ok; usd += cost; seconds += took
        restated = ((result or {}).get('planner_receipt') or {}).get('recipe', {}).get('restated') if ran else None
        line = (f"{'ok  ' if ok else 'MISS'} {item['id']} want={'/'.join(wanted) or 'tiers'} got={','.join(ran) or core.get('final_tier') or 'not answered'} "
                f"{took}s ${cost:.4f}" + (' (restated)' if restated else '') + f" | {item['question']}")
        print(line, flush=True); lines.append(line)
    methods_n = sum(1 for i in items if i['method']); plain_n = len(items) - methods_n
    summary = (f"methods rung routing: {right}/{len(items)} | method questions {by['method_right']}/{methods_n} right, {by['method_wrong']} wrong method, "
               f"{by['method_missed']} left to other rungs | plain questions {by['plain_kept']}/{plain_n} left to the tiers, {by['plain_taken']} taken by a method | "
               f"${usd:.4f}, {seconds:.0f}s total")
    out = Path('artifacts/agere-tiered-core'); out.mkdir(parents=True, exist_ok=True)
    (out / f"methods-eval-{Path(path).stem}-{time.strftime('%Y%m%d-%H%M%S')}.txt").write_text('\n'.join(lines + [summary]) + '\n')
    return summary


def main(argv):
    if argv[0] == 'methods':
        print('\n' + methods_eval(*argv[1:2]))
        return
    if argv[0] == 'takeover':
        print('\n' + takeover_eval(tuple(argv[1].split(',')) if len(argv) > 1 else ('novel', 'paraphrase')))
        return
    if argv[0] == 'committee':
        import os
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        print('\n' + committee(argv[1] if len(argv) > 1 else '2029', os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3'),
                               limit=int(argv[2]) if len(argv) > 2 else None))
        return
    if argv[0] == 'drivers':
        import os
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        print('\n' + drivers_eval(argv[1] if len(argv) > 1 else '2029', os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3'),
                                  limit=int(argv[2]) if len(argv) > 2 else None))
        return
    if argv[0] == 'near':
        import os
        print('\n' + near_eval(argv[1] if len(argv) > 1 else 60, os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3')))
        return
    if argv[0] == 'review':
        import os
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        pick = [x for x in argv[1:] if not x.endswith('.json')]
        print('\n' + review(os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3'), set(pick[0].split(',')) if pick else None))
        return
    if argv[0] == 'readmit':
        import os
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        paths = [x for x in argv[1:] if x.endswith('.json')]; pick = [x for x in argv[1:] if not x.endswith('.json')]
        print('\n' + readmit(os.environ.get('AGERE_TIERED_S1_LEDGER', 'artifacts/agere-tiered-core/system-one-ledger.sqlite3'), set(pick[0].split(',')) if pick else None, *paths[:1]))
        return
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
