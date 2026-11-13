"""Bridge from the app's question entry to the tiered core.

try_answer() runs a fresh question through the tiers. A validated answer is returned in the app's result shape.
Anything the core does not answer (no tier fits, the frontier tier says one query cannot answer it, validation
fails) returns None and the caller continues on its existing path, so nothing the app could do before is lost.
"""
import os

from . import VERSION, bottles as bottle_store, ledger as ledger_store, router, store

TIERS = ('bottles', 'recipes', 'system1', 'slm', 'llm')
TIER_NAMES = {'bottles': 'a saved recipe for this question shape', 'recipes': 'a built-in recipe matched by wording',
              'slm': 'a built-in recipe chosen by System One and filled by the small model', 'llm': 'a recipe written by the frontier model and run by code'}


def enabled():
    return os.environ.get('AGERE_TIERED_CORE', 'off') == 'on'


def _headline(columns, rows):
    if len(rows) == 1:
        return '; '.join(f"{c.replace('_', ' ')}: {v}" for c, v in zip(columns, rows[0])) + '.'
    return f"{len(rows)} rows by {columns[0].replace('_', ' ')}; see the table."


def try_in_conversation(stack, question, previous, looks_like_follow_up):
    """A question asked inside a conversation. -> (result, complete question, note) or None for the existing path.
    A question the zero-cost tiers recognise is complete. Otherwise a follow-up is first made complete from the
    previous question (followup.rewrite); words that are not an edit of the previous question are tried as they are."""
    if not enabled(): return None
    from . import followup
    s1_ledger = getattr(getattr(stack, 'ledger', None), 'path', None) or os.environ.get('AGERE_TIERED_S1_LEDGER')
    try:
        standalone, how = followup.complete_question(question, previous, looks_like_follow_up, bottle_store.Bottles(), s1_ledger)
    except Exception as exc:
        if 'limit' in str(exc).lower(): raise
        return None
    if standalone is None: return None  # an edit we could not make safely: leave it to the existing path
    result = try_answer(stack, standalone)
    if result is None: return None
    note = 'Complete question answered by the tiered core'
    if how and standalone != question:
        by = {'period_swapped_by_code': 'the period was swapped into the previous question by code',
              'period_borrowed_by_code': 'the period was taken from the previous question by code',
              'relative_period_by_code': 'the period was worked out from the previous question by code',
              'small_model_rewrite_confirmed_by_system_one': 'rewritten from the previous question by the small model and confirmed by System One',
              'small_model_rewrite_matched_by_frontier_model': 'rewritten from the previous question by the small model; System One did not confirm it, and the frontier model independently wrote the same question'}[how['method']]
        note = f'Follow-up read as: "{standalone}" ({by}).'
        result['answer']['facts'] = {'follow_up': note, **(result['answer'].get('facts') or {})}
        result['planner_receipt']['follow_up'] = {**how, 'previous': previous, 'standalone': standalone}
        for key in ('new_api_estimate_usd', 'known_api_estimate_usd'): result['planning_economics'][key] += how['usd']
        result['planner_receipt']['known_api_estimate_usd'] += how['usd']
    return result, standalone, note


def try_in_cohort(stack, question, parent, key, scope='result'):
    """A follow-up about a selected patient group (parent: the saved answer whose patients were selected).
    -> the app's result dict bound to that group, or None for the existing path. Code restricts every table to the
    group's patients; the tiers answer the question as if the group were the whole population."""
    if not enabled(): return None
    from . import followup
    ids = sorted(set(parent.get('patient_ids') or []))
    if not ids or len(ids) > store.MAX_COHORT: return None
    s1_ledger = getattr(getattr(stack, 'ledger', None), 'path', None) or os.environ.get('AGERE_TIERED_S1_LEDGER')
    try:
        standalone, how = followup.group_question(question, s1_ledger, bottle_store.Bottles())
    except Exception as exc:
        if 'limit' in str(exc).lower(): raise
        return None
    if standalone is None: return None
    result = _single(stack, standalone, cohort=ids)
    if result is None: return None
    note = f'Asked about only the {len(ids)} selected patients, as: "{standalone}". Code restricted every patient table to those patients.'
    answer = result['answer']
    answer['facts'] = {'selected_patients': note, **(answer.get('facts') or {})}
    answer['limitations'] = [f'Computed only over the {len(ids)} selected patients; this is not a whole-population figure. '
                             'The selection itself was not re-checked.', *(answer.get('limitations') or [])]
    answer['inherited_evidence_context'] = {'id': key, 'source': parent.get('source')}
    result['question'] = question
    result['planner_receipt']['cohort'] = {**how, 'patients': len(ids), 'parent_context_id': key, 'standalone': standalone}
    for name in ('new_api_estimate_usd', 'known_api_estimate_usd'): result['planning_economics'][name] += how['usd']
    result['planner_receipt']['known_api_estimate_usd'] += how['usd']
    policy = ((parent.get('selected_population') or {}).get('proof') or {}).get('membership_policy') or (parent.get('cohort_transform') or {}).get('population_policy')
    result['cohort_transform'] = {'parent_context_id': key, 'input_patient_ids': ids, 'scope': scope, 'operations': [note],
                                  **({'population_policy': policy} if policy else {})}
    return result


def try_answer(stack, question):
    """-> the app's result dict, or None to continue on the existing path.
    A message that asks several separate questions is split (split.parts) and each part goes through the tiers; it
    is answered as one composite only when every part is answered. Otherwise it is tried as one question."""
    if not enabled(): return None
    from . import split
    s1_ledger = getattr(getattr(stack, 'ledger', None), 'path', None) or os.environ.get('AGERE_TIERED_S1_LEDGER')
    if split.candidate(question, bottle_store.Bottles()):
        try:
            found, how = split.parts(question, s1_ledger)
        except Exception as exc:
            if 'limit' in str(exc).lower(): raise
            found, how = None, None
        if found:
            results = []
            for part in found:
                results.append(_single(stack, part))
                if results[-1] is None: break  # one unanswered part: the message is not answered in pieces
            if all(results): return _combine(question, found, results, how)
    return _single(stack, question)


def _combine(question, parts, results, how):
    """One composite result from the parts' results. The existing answer view draws each section."""
    answers = [r['answer'] for r in results]
    usd = sum(r['planner_receipt']['known_api_estimate_usd'] for r in results) + how['usd']
    calls = sum(r['new_paid_api_calls'] for r in results) + 1
    tier = max((r['planner_receipt']['final_tier'] for r in results), key=TIERS.index)
    limitations = list(dict.fromkeys(line for a in answers for line in a.get('limitations') or []))
    answer = {'status': 'answered', 'answer_type': 'composite', 'headline': ' '.join(str(a.get('headline') or '') for a in answers)[:1200],
              'sections': [{'title': part, 'answer': a} for part, a in zip(parts, answers)],
              'table': [], 'sources': [], 'missing': [], 'patient_ids': [],
              'verification': 'checked' if all(a.get('verification') == 'checked' for a in answers) else 'unverified',
              'limitations': [f'The message was read as {len(parts)} separate questions (split by the small model, confirmed by System One); each was answered on its own.', *limitations],
              'facts': {'read_as': f'{len(parts)} separate questions: ' + ' | '.join(parts),
                        **{f'part_{i}': f"{part} — {a['facts'].get('answered_by', '')}" for i, (part, a) in enumerate(zip(parts, answers), 1)}},
              'grounding': [line for a in answers for line in a.get('grounding') or []]}
    nodes = [{**r['graph']['nodes'][0], 'id': f'tiered_{i}', 'purpose': f'Tiered core recipe (executed) for: {part}'} for i, (part, r) in enumerate(zip(parts, results))]
    return {'answer': answer, 'results': [], 'new_paid_api_calls': calls, 'workflow': [], 'question': question, 'route': 'tiered_core',
            'graph': {'interpretation': question, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes},
            'planner_receipt': {'route': 'tiered_core', 'version': VERSION, 'final_tier': tier, 'split': {**how, 'parts': parts},
                                'parts': [r['planner_receipt'] for r in results], 'new_paid_api_calls': calls, 'known_api_estimate_usd': usd,
                                'api_cost_known': True, 'frontier_called': any(r['planner_receipt']['frontier_called'] for r in results)},
            'planning_economics': {'new_api_estimate_usd': usd, 'known_api_estimate_usd': usd, 'total_cost_known': True, 'scope': 'Tiered core model calls only.'},
            'tiered_core': {'final_tier': tier, 'seconds': round(sum(r['tiered_core']['seconds'] for r in results) + how['seconds'], 2), 'parts': len(parts),
                            'escalations': [e for r in results for e in r['tiered_core']['escalations']]}}


def _single(stack, question, cohort=None):
    """One question through the tiers, inside a patient cohort when one is given. -> the app's result dict, or None."""
    s1_ledger = getattr(getattr(stack, 'ledger', None), 'path', None) or os.environ.get('AGERE_TIERED_S1_LEDGER')
    bottles = bottle_store.Bottles()
    try:
        outcome = router.answer(question, tiers=TIERS, bottles=bottles, bottle_new=True, s1_ledger=s1_ledger, cohort=cohort)
    except Exception as exc:  # the bridge never blocks a question: any failure falls through to the existing path
        if 'limit' in str(exc).lower(): raise
        return None
    ledger_store.Ledger().write('app', question, outcome, config={'tiers': list(TIERS), **({'cohort_patients': len(cohort)} if cohort is not None else {})})
    if outcome['status'] != 'answered': return None
    columns, rows, tier = outcome['columns'], outcome['rows'], outcome['final_tier']
    usd = sum(s.get('usd', 0.0) for s in outcome['steps'])
    calls = sum(1 for s in outcome['steps'] if s.get('model'))
    recipe = outcome['recipe'] or {}
    checks = ', '.join(c['name'].replace('_', ' ') for c in outcome['validation']['checks'])
    documents = outcome.get('documents') or []
    read_by = {}
    for d in documents: read_by[d.get('by')] = read_by.get(d.get('by'), 0) + 1
    statement = str((recipe.get('read') or {}).get('statement') or '')
    for name, value in (recipe.get('params') or {}).items():
        statement = statement.replace('{' + name + '}', str(value.get('value') if isinstance(value, dict) else value))
    reader_names = {'system1': 'System One', 'slm': 'the small model', 'llm': 'the frontier model'}
    from . import narrative
    summary, wrote = narrative.write(question, columns, rows, recipe.get('read'), statement)
    usd += wrote['usd']; calls += 1 if wrote['tokens'] else 0
    answer = {'status': 'answered', 'answer_type': 'table', 'headline': summary,
              'table': [dict(zip(columns, r)) for r in rows], 'sources': [], 'missing': [], 'patient_ids': [],
              # A recipe the frontier model has just written passed structural checks only; it is not called checked.
              # Document judgments are model readings, so an answer that rests on them is not called checked either.
              'verification': 'unverified' if tier == 'llm' or documents else 'checked',
              'limitations': [f'Answered by the tiered core using {TIER_NAMES[tier]}. ' + (
                                  f"{len(documents)} documents were selected by SQL and each was read against: \"{statement}\" "
                                  + 'Read by ' + ', '.join(f'{reader_names.get(k, k)} ({n})' for k, n in read_by.items()) + '. Code counted the readings.'
                                  if documents else 'Every number was computed by SQL on the semantic layer.'),
                              f'Deterministic checks passed: {checks}. These check structure, period and totals, not clinical meaning.',
                              *(['This recipe was written by the frontier model for this question and is not yet confirmed against a reference answer.']
                                if tier == 'llm' else [])]}
    sql = store.portable(recipe.get('sql', ''), cohort)  # saved with the answer, so it must run on the semantic layer as it is
    # Shown by the existing answer view with no view change: facts as key findings, grounding as evidence used,
    # table_binding as the computation.
    models_used = ', '.join(dict.fromkeys(str(s['model']) for s in outcome['steps'] if s.get('model'))) or 'none'
    answer['facts'] = {'answered_by': f"{TIER_NAMES[tier][0].upper() + TIER_NAMES[tier][1:]}. {outcome['seconds']} seconds, "
                                      f"${usd:.4f} in model calls (models used: {models_used})."}
    answer['facts']['summary_written_by'] = ('The small model worded the summary from facts computed by code; code checked that every number in it is in the result.'
                                             if wrote['by'] == 'small_model' else 'Code wrote the summary; no model.' +
                                             (' A small-model summary was ' + wrote['small_model'] + '.' if wrote.get('small_model') else ''))
    if documents:
        said = {'yes': 'gives it' if (recipe.get('read') or {}).get('mode') == 'extract' else 'states it', 'no': 'does not', None: 'undecided'}
        answer['facts']['documents_read'] = (f'{len(documents)} documents were selected by SQL and each was read against: "{statement}" '
                                             + 'Read by ' + ', '.join(f'{reader_names.get(k, k)} ({n})' for k, n in read_by.items()) + '.')
        answer['grounding'] = [f"{d['record_id']}: {said[d.get('label')]}" + (f" (read by {reader_names.get(d.get('by'), d.get('by'))})" if d.get('by') else '')
                               + (f" — value \"{d['value']}\"" if d.get('value') else '') + (f" — \"{' '.join(d['quote'].split())[:200]}\"" if d.get('quote') else '')
                               for d in documents]
    # A one-number answer is shown as that number, not as "1 returned row".
    if len(rows) == 1 and (recipe.get('read') or {}).get('mode') == 'count':
        answer.update({'answer_type': 'count', 'count': rows[0][1], 'unit': f' documents stating it, of {rows[0][0]} read'})
    elif len(rows) == 1 and len(columns) == 1 and isinstance(rows[0][0], (int, float)) and not isinstance(rows[0][0], bool):
        answer.update({'answer_type': 'count' if isinstance(rows[0][0], int) else 'value', 'count' if isinstance(rows[0][0], int) else 'value': rows[0][0],
                       'unit': ' ' + columns[0].replace('_', ' ')})  # the view prints the unit straight after the number
    answer['table_binding'] = {'computation': sql, 'parameters': recipe.get('params') or {}, **({'reading': recipe['read']} if recipe.get('read') else {}),
                               'checks_passed': [c['name'] for c in outcome['validation']['checks']]}
    return {'answer': answer, 'results': [], 'new_paid_api_calls': calls, 'workflow': [], 'question': question, 'route': 'tiered_core',
            'graph': {'interpretation': question, 'coverage': 'supported_observation', 'unmet_requirements': [],
                      'nodes': [{'id': 'tiered_0', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': 'table'},
                                 'purpose': 'Tiered core recipe (executed)'}]},
            'planner_receipt': {'route': 'tiered_core', 'version': VERSION, 'final_tier': tier, 'recipe': recipe, 'steps': outcome['steps'],
                                'escalations': outcome['escalations'], 'validation': outcome['validation'], 'new_paid_api_calls': calls,
                                'known_api_estimate_usd': usd, 'api_cost_known': True, 'frontier_called': tier == 'llm', 'narrative': wrote},
            'planning_economics': {'new_api_estimate_usd': usd, 'known_api_estimate_usd': usd, 'total_cost_known': True,
                                   'scope': 'Tiered core model calls only.'},
            'tiered_core': {'final_tier': tier, 'seconds': outcome['seconds'], 'escalations': outcome['escalations'], 'bottled': outcome.get('bottled'),
                            **({'documents': documents} if documents else {})}}
