"""Bridge from the app's question entry to the tiered core.

try_answer() runs a fresh question through the tiers. A validated answer is returned in the app's result shape.
Anything the core does not answer (no tier fits, the frontier tier says one query cannot answer it, validation
fails) returns None and the caller continues on its existing path, so nothing the app could do before is lost.
"""
import contextvars
import os

from . import VERSION, bottles as bottle_store, ledger as ledger_store, router, store

EARLIER = contextvars.ContextVar('tiered_earlier_questions', default=())  # earlier questions of this conversation, for the takeover rung
TIERS = ('bottles', 'recipes', 'system1', 'slm', 'llm')
ORDER = ('methods',) + TIERS + ('takeover',)  # methods and takeover are not router tiers: one runs before the router, one after it
MAX_GROUNDING = 200  # evidence lines shown for a document answer
TIER_NAMES = {'methods': 'a built-in analysis method', 'bottles': 'a saved recipe for this question shape', 'recipes': 'a built-in recipe matched by wording',
              'slm': 'a built-in recipe chosen by System One and filled by the small model', 'llm': 'a recipe written by the frontier model and run by code',
              'takeover': 'the frontier model working the question out itself with read-only queries'}


def enabled():
    return os.environ.get('AGERE_TIERED_CORE', 'off') == 'on'


def _headline(columns, rows):
    if len(rows) == 1:
        return '; '.join(f"{c.replace('_', ' ')}: {v}" for c, v in zip(columns, rows[0])) + '.'
    return f"{len(rows)} rows by {columns[0].replace('_', ' ')}; see the table."


_HISTORY, _HISTORY_MAX = {}, 2000  # conversation id -> its last complete questions (oldest first); memory only


def _remember(conversation, question):
    if not conversation or not question: return
    if len(_HISTORY) >= _HISTORY_MAX and conversation not in _HISTORY: _HISTORY.pop(next(iter(_HISTORY)))
    _HISTORY[conversation] = [q for q in _HISTORY.get(conversation, []) if q != question][-3:] + [question]


def try_in_conversation(stack, question, previous, looks_like_follow_up, earlier=None, conversation=None):
    got = _in_conversation(stack, question, previous, looks_like_follow_up, earlier or [*_HISTORY.get(conversation, []), previous])
    if got: _remember(conversation, got[1])
    return got


def _in_conversation(stack, question, previous, looks_like_follow_up, earlier=None):
    """A question asked inside a conversation. -> (result, complete question, note) or None for the existing path.
    A question the zero-cost tiers recognise is complete. Otherwise a follow-up is first made complete from the
    previous question (followup.rewrite); words that are not an edit of the previous question are tried as they are."""
    if not enabled(): return None
    from . import followup
    EARLIER.set(tuple(dict.fromkeys(q for q in (earlier or [previous]) if q))[-4:])  # the takeover rung reads a question in its conversation
    s1_ledger = getattr(getattr(stack, 'ledger', None), 'path', None) or os.environ.get('AGERE_TIERED_S1_LEDGER')
    try:
        standalone, how = followup.complete_question(question, previous, looks_like_follow_up, bottle_store.Bottles(), s1_ledger)
    except Exception as exc:
        if 'limit' in str(exc).lower(): raise
        return None
    if standalone is None:
        # An edit no cheap method could make safely. The frontier model takes over with both questions in front of it.
        outcome = {'status': 'unanswered', 'steps': [], 'escalations': [{'from': 'followup', 'reason': str((how or {}).get('method'))}], 'seconds': 0.0,
                   'missing': f'This is a follow-up to the previous question in the conversation: "{previous}". Work out what the user means by it and answer that.'}
        result = _takeover(question, None, outcome)
        if result is None: return None
        note = 'The follow-up could not be turned into a complete question by the cheaper methods, so the frontier model answered it with the previous question as context.'
        result['answer']['facts'] = {'follow_up': note, **(result['answer'].get('facts') or {})}
        result['planner_receipt']['follow_up'] = {**(how or {}), 'previous': previous, 'standalone': None}
        return result, question, note
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
    if standalone is None:
        # No cheap method could restate the question safely: the frontier model answers it inside the group.
        outcome = {'status': 'unanswered', 'steps': [], 'escalations': [{'from': 'cohort', 'reason': str((how or {}).get('method'))}], 'seconds': 0.0,
                   'missing': f'Words such as "those patients", "them" or "their" mean the {len(ids)} selected patients. Every patient table is already restricted to them, so query the tables directly.'}
        result, standalone, how = _takeover(question, ids, outcome), question, {'method': 'frontier_takeover_in_group', 'usd': 0.0, 'tokens': 0, 'seconds': 0.0}
    else:
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
    fixed = split.known(question)
    from . import methods
    if not fixed and (methods.claims(stack, question, bottle_store.Bottles()) or methods.needs(stack, question, bottle_store.Bottles())):
        return _single(stack, question)  # an analysis method reads the whole message; it is not split first
    if fixed or split.candidate(question, bottle_store.Bottles()):
        try:
            found, how = fixed or split.parts(question, s1_ledger)
        except Exception as exc:
            if 'limit' in str(exc).lower(): raise
            found, how = None, None
        if found:
            results = []
            for part in found:
                results.append(_single(stack, part, takeover_ok=False))  # the takeover rung answers the whole message, never a part
                if results[-1] is None: break  # one unanswered part: the message is not answered in pieces
            if all(results): return _combine(question, found, results, how)
    return _single(stack, question)


def _combine(question, parts, results, how):
    """One composite result from the parts' results. The existing answer view draws each section."""
    answers = [r['answer'] for r in results]
    usd = sum(r['planner_receipt']['known_api_estimate_usd'] for r in results) + how['usd']
    calls = sum(r['new_paid_api_calls'] for r in results) + (1 if how['tokens'] else 0)
    tier = max((r['planner_receipt']['final_tier'] for r in results), key=ORDER.index)
    limitations = list(dict.fromkeys(line for a in answers for line in a.get('limitations') or []))
    answer = {'status': 'answered', 'answer_type': 'composite', 'headline': ' '.join(str(a.get('headline') or '') for a in answers)[:1200],
              'sections': [{'title': part, 'answer': a} for part, a in zip(parts, answers)],
              'table': [], 'sources': [], 'missing': [], 'patient_ids': [],
              'verification': 'checked' if all(a.get('verification') == 'checked' for a in answers) else 'unverified',
              'limitations': [f'The message was read as {len(parts)} separate questions ' + ('(a fixed split made by code); ' if how['method'].endswith('by_code') else '(split by the small model, confirmed by System One); ')
                              + 'each was answered on its own.',
                              *(['This reports what the fill records show and what the notes state. It does not judge what should be prescribed or what dose is tolerated.']
                                if how['method'].startswith('heart_failure') else []), *limitations],
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


def _single(stack, question, cohort=None, takeover_ok=True):
    """One question through the tiers, inside a patient cohort when one is given. -> the app's result dict, or None."""
    s1_ledger = getattr(getattr(stack, 'ledger', None), 'path', None) or os.environ.get('AGERE_TIERED_S1_LEDGER')
    bottles = bottle_store.Bottles()
    assist = False
    if cohort is None:
        from . import methods
        if methods.claims(stack, question, bottles):
            done = _method(stack, question, s1_ledger=s1_ledger)
            if done is not None: return done
        else:
            wanted = methods.needs(stack, question, bottles)
            if wanted: return _needs_input(question, wanted)
            assist = methods.worth_asking(stack, question, bottles)
    try:
        # A question in the vocabulary of the analysis methods goes through the cheap tiers first, then the methods rung
        # (small model restates, code and System One check), and only then to a recipe written by the frontier model.
        outcome = router.answer(question, tiers=tuple(t for t in TIERS if t != 'llm') if assist else TIERS, bottles=bottles, bottle_new=True,
                                s1_ledger=s1_ledger, cohort=cohort)
        if assist and outcome['status'] != 'answered':
            try:
                restated, how = methods.assisted(stack, question, s1_ledger)
            except Exception as exc:
                if 'limit' in str(exc).lower(): raise
                restated, how = None, None
            if restated:
                done = _method(stack, question, restated=restated, how=how)
                if done is not None: return done
            elif how:
                ledger_store.Ledger().write('app_method', question, {'status': 'declined', 'final_tier': None, 'steps': [how], 'seconds': how['seconds'],
                                                                     'validation': {'ok': False, 'reason': how['method']}, 'recipe': None}, config={'tiers': list(ORDER)})
            later = router.answer(question, tiers=('llm',), bottles=bottles, bottle_new=True, s1_ledger=s1_ledger, cohort=cohort)
            outcome = {**later, 'steps': outcome['steps'] + ([how] if how else []) + later['steps'], 'escalations': outcome['escalations'] + later['escalations'],
                       'seconds': round(outcome['seconds'] + (how['seconds'] if how else 0.0) + later['seconds'], 2)}
    except Exception as exc:  # the bridge never blocks a question: any failure falls through to the existing path
        if 'limit' in str(exc).lower(): raise
        return None
    if isinstance(outcome.get('bottled'), int) and os.environ.get('AGERE_TIERED_AUTO_LEARN', 'off') == 'on':
        from . import learn
        learn.spawn_auto()  # a new recipe was saved: check and promote it in the background, with no manual step
    ledger_store.Ledger().write('app', question, outcome, config={'tiers': list(TIERS), **({'cohort_patients': len(cohort)} if cohort is not None else {})})
    if outcome['status'] != 'answered':
        return _takeover(question, cohort, outcome) if takeover_ok else None
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
                               for d in (documents if len(documents) <= MAX_GROUNDING else sorted(documents, key=lambda d: d.get('label') != 'yes')[:MAX_GROUNDING])]
        if len(documents) > MAX_GROUNDING:
            answer['grounding'].append(f'... and {len(documents) - MAX_GROUNDING} more documents read, not listed here (documents that state it are listed first).')
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


def _method(stack, question, restated=None, how=None, s1_ledger=None):
    """An analysis method of the wider application answers (methods.py). Code named the method from the question, or from
    a restatement the small model wrote and System One confirmed; code ran it. -> result or None."""
    from . import methods
    steps = [how] if how else []
    if restated is None:  # the compiler named the method from the user's own words: System One may still say it does not fit
        found = methods.plan(stack, question)
        ok, step = methods.fits(stack, question, found[2], s1_ledger) if found else (False, None)
        if step and step.get('label'): steps.append(step)
        if not ok:
            ledger_store.Ledger().write('app_method', question, {'status': 'declined', 'final_tier': None, 'steps': steps, 'seconds': step['seconds'] if step else 0.0,
                                                                 'validation': {'ok': False, 'reason': 'system_one_says_method_does_not_fit'},
                                                                 'recipe': {'methods': found[2] if found else []}}, config={'tiers': list(ORDER)})
            return None
    got = methods.run(stack, restated or question)
    if got is None: return None
    usd, calls = sum(s.get('usd', 0.0) for s in steps), sum(1 for s in steps if s.get('model'))
    got['seconds'] = round(got['seconds'] + sum(s.get('seconds', 0.0) for s in steps), 2)
    named = ', '.join(m.replace('_', ' ') for m in got['methods'])
    if 'result' not in got:
        import sys
        print(f"tiered method declined: {got['declined']}", file=sys.stderr, flush=True)
        ledger_store.Ledger().write('app_method', question, {'status': 'declined', 'final_tier': None, 'steps': steps, 'seconds': got['seconds'],
                                                             'validation': {'ok': False, 'reason': got['declined']}, 'recipe': {'methods': got['methods']}},
                                    config={'tiers': list(ORDER)})
        return None
    result = got['result']
    answer = dict(result['answer'])
    table = answer.get('table') or []
    columns = list(table[0]) if table and isinstance(table[0], dict) else []
    ledger_store.Ledger().write('app_method', question, {'status': 'answered', 'final_tier': 'methods', 'steps': steps, 'seconds': got['seconds'],
                                                         'validation': {'ok': True, 'checks': [{'name': 'method_named_by_code', 'ok': True}]},
                                                         'recipe': {'methods': got['methods']}, 'columns': columns,
                                                         'rows': [[r.get(c) for c in columns] for r in table[:50]]},
                                config={'tiers': list(ORDER)})
    models_used = ', '.join(dict.fromkeys(str(s['model']) for s in steps if s.get('model'))) or 'none'
    answer['facts'] = {**(answer.get('facts') or {}),
                       'answered_by': f"{TIER_NAMES['methods'][0].upper() + TIER_NAMES['methods'][1:]}: {named}. {got['seconds']} seconds, ${usd:.4f} in model calls (models used: {models_used})."}
    chosen = ('The wording was not one the code recognises, so the small model restated the question as: "' + restated + '". Code checked that the restatement adds no '
              'number or period and that it names this method; System One confirmed it asks the same thing.'
              if restated else 'Code read the question and chose the method' + ('; System One confirmed the method fits the question.' if calls else '.'))
    answer['limitations'] = [f'Answered by the tiered core using {TIER_NAMES["methods"]} ({named}). {chosen} Code ran the method on the warehouse; no model computed any figure.',
                             'The method states its own assumptions below. Forecasts, simulations and estimated effects are model outputs under those assumptions, not measured facts.',
                             *(answer.get('limitations') or [])]
    if restated: answer['verification'] = 'unverified'  # the choice of method rests on a model restatement, so it is not called checked
    return {**result, 'answer': answer, 'question': question, 'route': 'tiered_core', 'new_paid_api_calls': calls,
            'planner_receipt': {'route': 'tiered_core', 'version': VERSION, 'final_tier': 'methods', 'recipe': {'methods': got['methods'], **({'restated': restated} if restated else {})}, 'steps': steps,
                                'escalations': [], 'validation': {'ok': True, 'checks': [{'name': 'method_named_by_code', 'ok': True}]},
                                'new_paid_api_calls': calls, 'known_api_estimate_usd': usd, 'api_cost_known': True, 'frontier_called': False,
                                'compiler': {k: v for k, v in got['info'].items() if k in ('compiler', 'recognised', 'route', 'operation')}},
            'planning_economics': {'new_api_estimate_usd': usd, 'known_api_estimate_usd': usd, 'total_cost_known': True, 'scope': 'Tiered core model calls only.'},
            'tiered_core': {'final_tier': 'methods', 'seconds': got['seconds'], 'escalations': [], 'bottled': None, 'methods': got['methods']}}


def _needs_input(question, wanted):
    """The application's compiler recognised the question and named what it lacks (methods.needs). The user is told what
    to add; no model is asked to guess a contract term or a benchmark. Code only, no cost."""
    listed = '; '.join(wanted)
    ledger_store.Ledger().write('app_method', question, {'status': 'needs_input', 'final_tier': 'methods', 'steps': [], 'seconds': 0.0,
                                                         'validation': {'ok': True, 'checks': [{'name': 'missing_input_named_by_code', 'ok': True}]},
                                                         'recipe': {'requires_input': wanted}}, config={'tiers': list(ORDER)})
    answer = {'status': 'unsupported', 'answer_type': 'table', 'headline': 'This needs information that is not in the data. Add it to the question and ask again: ' + listed + '.',
              'table': [{'needed': w} for w in wanted], 'sources': [], 'patient_ids': [], 'missing': [f'Missing input: {w}' for w in wanted],
              'facts': {'answered_by': 'Code recognised the question and named the inputs it needs. 0 seconds, $0.0000 in model calls (models used: none).'},
              'limitations': ['Answered by the tiered core: code recognised which analysis this asks for and which inputs it needs that are neither in the question nor in the warehouse. '
                              'No model was asked to guess them.']}
    return {'answer': answer, 'results': [], 'new_paid_api_calls': 0, 'workflow': [], 'question': question, 'route': 'tiered_core',
            'graph': {'interpretation': 'Needs inputs not in the data: ' + question, 'coverage': 'unsupported', 'unmet_requirements': answer['missing'], 'nodes': []},
            'planner_receipt': {'route': 'tiered_core', 'version': VERSION, 'final_tier': 'methods', 'recipe': {'requires_input': wanted}, 'steps': [], 'escalations': [],
                                'validation': {'ok': True, 'checks': [{'name': 'missing_input_named_by_code', 'ok': True}]}, 'new_paid_api_calls': 0,
                                'known_api_estimate_usd': 0.0, 'api_cost_known': True, 'frontier_called': False},
            'planning_economics': {'new_api_estimate_usd': 0.0, 'known_api_estimate_usd': 0.0, 'total_cost_known': True, 'scope': 'Tiered core model calls only.'},
            'tiered_core': {'final_tier': 'methods', 'seconds': 0.0, 'escalations': [], 'bottled': None, 'needs_input': wanted}}


def _takeover(question, cohort, outcome):
    """No tier answered: the frontier model takes over, bounded by turns and seconds (takeover.py). -> result or None."""
    from . import takeover
    if not takeover.enabled(): return None
    why = str(outcome.get('missing') or '; '.join(e.get('reason', '') for e in outcome.get('escalations') or [])[-400:])
    earlier = [q for q in EARLIER.get() if q and q != question]
    if earlier and 'previous question in the conversation' not in why:
        why += ' | Earlier questions in this conversation, oldest first (use them only if this question refers to them): ' + ' / '.join(f'"{q}"' for q in earlier)
    try:
        got = takeover.answer(question, cohort, why=why)
    except Exception as exc:
        if 'limit' in str(exc).lower(): raise
        import sys
        print(f'tiered takeover failed: {type(exc).__name__}: {str(exc)[:200]}', file=sys.stderr, flush=True)
        return None
    if got is None: return None
    spent = sum(s.get('usd', 0.0) for s in outcome['steps'])
    usd, calls = spent + got['usd'], sum(1 for s in outcome['steps'] if s.get('model')) + len(got['steps'])
    seconds = round(outcome['seconds'] + got['seconds'], 2)
    ledger_store.Ledger().write('app_takeover', question, {**outcome, 'status': 'answered', 'final_tier': 'takeover', 'columns': got['columns'], 'rows': got['rows'],
                                                           'steps': outcome['steps'] + got['steps'], 'seconds': seconds},
                                config={'tiers': list(ORDER), **({'cohort_patients': len(cohort)} if cohort is not None else {})})
    sql = store.portable(got['sql'], cohort) if got['sql'] else ''
    answer = {'status': 'answered', 'answer_type': 'table', 'headline': got['text'], 'table': [dict(zip(got['columns'], r)) for r in got['rows'][:500]],
              'sources': [], 'missing': [], 'patient_ids': [], 'verification': 'unverified',
              'limitations': [f"No cheaper tier could answer this, so the frontier model took over: it ran {len(got['queries'])} read-only queries"
                              + (f" and read {len(got['documents'])} documents" if got['documents'] else '') + ', then wrote this answer. It is not checked against a reference.',
                              *([f"These numbers in the answer were not found in any query result and should not be relied on: {', '.join(got['unsupported'][:12])}."] if got['unsupported'] else
                                ['Code checked that every number in the answer appears in a query result, a document it read, or the question.']),
                              *got['caveats']],
              'facts': {'answered_by': f"{TIER_NAMES['takeover'][0].upper() + TIER_NAMES['takeover'][1:]}. {seconds} seconds, ${usd:.4f} in model calls (model: {got['model']})."},
              'grounding': [f'Query {i}: {q}' for i, q in enumerate(got['queries'], 1)] + [f'Document read: {d}' for d in got['documents']]}
    if sql: answer['table_binding'] = {'computation': sql, 'parameters': {}, 'checks_passed': []}
    return {'answer': answer, 'results': [], 'new_paid_api_calls': calls, 'workflow': [], 'question': question, 'route': 'tiered_core',
            'graph': {'interpretation': question, 'coverage': 'supported_observation', 'unmet_requirements': [],
                      'nodes': [{'id': 'tiered_0', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': 'table'},
                                 'purpose': 'Frontier model takeover (queries executed by code)'}]},
            'planner_receipt': {'route': 'tiered_core', 'version': VERSION, 'final_tier': 'takeover', 'recipe': {'sql': got['sql']}, 'steps': outcome['steps'] + got['steps'],
                                'escalations': outcome['escalations'] + [{'from': 'tiers', 'reason': 'no tier answered; frontier model took over'}],
                                'validation': {'ok': not got['unsupported'], 'checks': [{'name': 'numbers_in_answer_are_in_results', 'ok': not got['unsupported']}]},
                                'new_paid_api_calls': calls, 'known_api_estimate_usd': usd, 'api_cost_known': True, 'frontier_called': True,
                                'takeover': {'queries': got['queries'], 'documents': got['documents'], 'unsupported_numbers': got['unsupported']}},
            'planning_economics': {'new_api_estimate_usd': usd, 'known_api_estimate_usd': usd, 'total_cost_known': True, 'scope': 'Tiered core model calls only.'},
            'tiered_core': {'final_tier': 'takeover', 'seconds': seconds, 'escalations': outcome['escalations'], 'bottled': None}}
