"""Bridge from the app's question entry to the tiered core.

try_answer() runs a fresh question through the tiers. A validated answer is returned in the app's result shape.
Anything the core does not answer (no tier fits, the frontier tier says one query cannot answer it, validation
fails) returns None and the caller continues on its existing path, so nothing the app could do before is lost.
"""
import json
import contextvars
import os

from . import VERSION, bottles as bottle_store, ledger as ledger_store, router, store

# One time budget per question, shared by every rung (Mike 2026-11-13: an unbounded run must never happen). The cheap
# rungs finish in seconds; what is left when the last rung starts is all it gets, and it is skipped if too little is left.
QUESTION_SECONDS = float(os.environ.get('AGERE_TIERED_QUESTION_SECONDS', '90'))
TAKEOVER_NEEDS = 20.0
TAKEOVER_FLOOR = float(os.environ.get('AGERE_TIERED_TAKEOVER_FLOOR', '60'))   # seconds the takeover always gets
DEADLINE = contextvars.ContextVar('tiered_question_deadline', default=None)


def _budget(fresh=False):
    """Start the question's clock unless one is already running. -> seconds left."""
    import time
    if fresh or DEADLINE.get() is None: DEADLINE.set(time.monotonic() + QUESTION_SECONDS)
    return DEADLINE.get() - time.monotonic()


EARLIER = contextvars.ContextVar('tiered_earlier_questions', default=())  # earlier questions of this conversation, for the takeover rung
TIERS = ('bottles', 'recipes', 'system1', 'slm', 'near', 'llm')
ORDER = ('methods',) + TIERS + ('takeover',)  # methods and takeover are not router tiers: one runs before the router, one after it
MAX_GROUNDING = 200  # evidence lines shown for a document answer
TIER_NAMES = {'methods': 'a built-in analysis method', 'near': 'a saved recipe for the same question in other words (System One picked it, the small model filled its values, System One confirmed the fit)', 'bottles': 'a saved recipe for this question shape', 'recipes': 'a built-in recipe matched by wording',
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
    _budget(fresh=True)
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
    _budget(fresh=True)
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
    _budget(fresh=True)  # the question's clock starts (or restarts, once a follow-up has been made complete) here
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


def verification(bottle_id):
    """The saved recipe's own record, in a sentence a reviewer can check: who confirmed it and how often it has been re-checked."""
    if not bottle_id: return None
    try:
        from . import upkeep
        bottles = bottle_store.Bottles()
        row = bottles.db.execute('SELECT created, provenance_json FROM bottle WHERE id = ?', (bottle_id,)).fetchone()
        if not row: return None
        st, prov = upkeep.standing(bottles, bottle_id), json.loads(row[1] or '{}')
        last = bottles.db.execute("SELECT MAX(at) FROM bottle_event WHERE bottle_id = ? AND kind LIKE 'check_%'", (bottle_id,)).fetchone()[0]
        uses = st.get('use', 0) + st.get('near_use', 0)
        how = ('confirmed by a second model writing its own query' if prov.get('confirmed_by_second_model') else 'promoted after the regression set showed no loss')
        return (f"Saved recipe {bottle_id}, saved {str(row[0])[:10]}, {how}. Independent re-checks: {st['check_agree']} agreed, {st['check_differ']} disagreed"
                + (f', last on {str(last)[:10]}' if last else ', none yet') + f". Answered {uses} earlier question{'s' if uses != 1 else ''}.")
    except Exception:
        return None


def _readmission_headline(answers):
    """Code writes the summary of a readmission review from the parts' tables, in the record's own words. No model: the
    review turns on which statement belongs to which stay, and that is exactly what a reworded summary can blur."""
    from . import readmission
    tables = [(a.get('table') or []) for a in answers] + [[]] * 5
    stays, stated, follow, risk, labs = tables[:5]
    re = next((r for r in stays if r.get('stay') == 'readmission'), None)
    before = next((r for r in stays if r.get('stay') == 'stay before the readmission'), None)
    if not re: return 'No inpatient admission on that date is on record for this patient.'
    quotes = lambda label, kinds: list(dict.fromkeys(str(r.get('passage')) for r in stated if r.get('stay') == label and r.get('stated_as') in kinds))
    text = f"Admitted {re['admit_date']}" + (f", discharged {re['discharge_date']}" if re.get('discharge_date') not in (None, 'not recorded') else '')
    if before: text += f", {re['days_after_prior_discharge']} days after discharge from the stay of {before['admit_date']} to {before['discharge_date']}"
    text += '. '
    said = quotes('readmission', readmission.ADMISSION_KINDS)
    text += ('Why admitted, as the record states it: ' + '; '.join(f'"{q}"' for q in said[:3]) + '. ') if said else 'No document states why the patient was admitted on that date. '
    earlier = quotes('stay before the readmission', readmission.ADMISSION_KINDS)
    if before and earlier: text += 'The stay before it: ' + '; '.join(f'"{q}"' for q in earlier[:2]) + '. '
    told = quotes('stay before the readmission', ('follow-up instruction',))
    if told: text += 'Discharge instruction: ' + '; '.join(f'"{q}"' for q in told[:2]) + '. '
    seen = {str(r.get('item')): r for r in follow}
    visit = seen.get('outpatient visit within 7 days of discharge')
    if before and visit:
        text += ('Follow-up: an outpatient visit is on record on ' + str(visit.get('first_date')) + ', within 7 days of discharge. ' if visit.get('on_record') == 'yes'
                 else 'Follow-up: no outpatient visit is on record within 7 days of discharge. ')
    facts = {str(r.get('factor')): r.get('on_record') for r in risk}
    if facts:
        needs = [k.split(': ', 1)[1] for k in facts if k.startswith('positive social-need screen: ')]
        text += (f"Risk factors on record: age {facts.get('age')}, {facts.get('chronic conditions on record')} chronic conditions, "
                 f"{facts.get('inpatient admissions in the prior 12 months')} inpatient admissions in the prior 12 months, "
                 f"{facts.get('emergency visits in the prior 6 months')} emergency visits in the prior 6 months"
                 + (', positive social-need screens for ' + ', '.join(needs) if needs else ', no positive social-need screen') + '. ')
    any_lab = next((r for r in labs if r.get('concept') == '(any lab or vital)'), None)
    named = sorted({str(r.get('concept')) for r in labs if r.get('period') == 'readmission' and r.get('concept') != '(any lab or vital)'})
    if any_lab is not None:
        text += ('Labs and vitals during the readmission: ' + ', '.join(named[:8]) + '. ') if named else 'No lab or vital result is on record during the readmission. '
    imaging = quotes('readmission', ('imaging impression',)) + quotes('between the two stays', ('imaging impression',)) + quotes('stay before the readmission', ('imaging impression',))
    text += ('Imaging: ' + '; '.join(f'"{q}"' for q in imaging[:2]) + '. ') if imaging else 'No imaging report is on record around the two stays. '
    directive = [str(r.get('passage')) for r in stated if r.get('stated_as') == 'advance directive or code status']
    text += ('Advance directive or code status: ' + '; '.join(f'"{q}"' for q in directive[:2]) + '.') if directive and not directive[0].startswith('No advance directive') \
        else 'No advance directive or code status is documented anywhere in the record.'
    return text[:1800]


def _combine(question, parts, results, how):
    """One composite result from the parts' results. The existing answer view draws each section."""
    answers = [r['answer'] for r in results]
    usd = sum(r['planner_receipt']['known_api_estimate_usd'] for r in results) + how['usd']
    calls = sum(r['new_paid_api_calls'] for r in results) + (1 if how['tokens'] else 0)
    tier = max((r['planner_receipt']['final_tier'] for r in results), key=ORDER.index)
    limitations = list(dict.fromkeys(line for a in answers for line in a.get('limitations') or []))
    headline = _readmission_headline(answers) if how['method'].startswith('readmission') and len(answers) >= 3 else ' '.join(str(a.get('headline') or '') for a in answers)[:1200]
    answer = {'status': 'answered', 'answer_type': 'composite', 'headline': headline,
              'sections': [{'title': part, 'answer': a} for part, a in zip(parts, answers)],
              'table': [], 'sources': [], 'missing': [], 'patient_ids': [],
              'verification': 'checked' if all(a.get('verification') == 'checked' for a in answers) else 'unverified',
              'limitations': [f'The message was read as {len(parts)} separate questions ' + ('(a fixed split made by code); ' if how['method'].endswith('by_code') else '(split by the small model, confirmed by System One); ')
                              + 'each was answered on its own.',
                              *(['This reports what the fill records show and what the notes state. It does not judge what should be prescribed or what dose is tolerated.']
                                if how['method'].startswith('heart_failure') else []),
                              *(['This reports what the stay records show and what the documents state for each admission, each as a quote assigned to its stay by code. '
                                 'It does not judge what caused the readmission or whether it was avoidable.' + (
                                     ' No date was given, so the most recent readmission on record was used.' if how.get('date_from') != 'question' else '')]
                                if how['method'].startswith('readmission') else []), *limitations],
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
    """One question through the tiers, inside a patient cohort when one is given. -> the app's result dict, or None.
    When a purpose-built method could answer a nearby wording that System One would not accept as the same question,
    that wording is offered on the answer as a next question, so the user can take the exact route in one click."""
    near = {}
    result = _climb(stack, question, cohort, takeover_ok, near)
    answer = (result or {}).get('answer') or {}
    if near.get('restated') and answer.get('status') in ('answered', 'partial') and (result.get('tiered_core') or {}).get('final_tier') in ('llm', 'takeover'):
        answer['next_questions'] = [near['restated'], *[q for q in answer.get('next_questions') or [] if q != near['restated']]][:4]
        answer.setdefault('facts', {})['a_built_in_method_answers_a_nearby_question'] = (
            f'"{near["restated"]}" runs the {str(near.get("picked")).replace("_", " ")} method, computed by code. It was not used here because it is not exactly what you asked.')
    return result


def _climb(stack, question, cohort, takeover_ok, near):
    s1_ledger = getattr(getattr(stack, 'ledger', None), 'path', None) or os.environ.get('AGERE_TIERED_S1_LEDGER')
    bottles = bottle_store.Bottles()
    assist, prefer = False, None
    if cohort is None:
        from . import methods
        if methods.claims(stack, question, bottles):
            note = {}
            done = _method(stack, question, s1_ledger=s1_ledger, note=note)
            if done is not None: return done
            prefer = note.get('other')  # System One picked a neighbouring method over the one the compiler named
            assist = bool(prefer) and methods.worth_asking(stack, question, bottles, redirected=True)
        else:
            wanted = methods.needs(stack, question, bottles)
            if wanted and _interactive(stack):
                return _clarify(stack, question, 'needs_input', 'This needs information that is not in the data: ' + '; '.join(wanted) + '. Type it below, or choose how to go on.',
                                'Code recognised which analysis this asks for; it cannot run without these inputs, and no model was asked to guess them.',
                                [{'id': 'without', 'label': 'Answer without it', 'meaning': 'Give the best answer the warehouse supports and say what could not be computed'},
                                 {'id': 'explain', 'label': 'Explain what is needed', 'meaning': 'Describe each missing input and where it usually comes from'}],
                                context={'wanted': wanted})
            if wanted: return _needs_input(question, wanted)
            assist = methods.worth_asking(stack, question, bottles)
    try:
        # A question in the vocabulary of the analysis methods goes through the cheap tiers first, then the methods rung
        # (small model restates, code and System One check), and only then to a recipe written by the frontier model.
        ask_first = cohort is None and _interactive(stack)   # a person is in the conversation: an unclear question goes back to them
        outcome = router.answer(question, tiers=tuple(t for t in TIERS if t != 'llm') if assist else TIERS, bottles=bottles, bottle_new=not ledger_store.hosted(),
                                s1_ledger=s1_ledger, cohort=cohort, ask_first=ask_first)
        if assist and outcome['status'] not in ('answered', 'needs_clarification'):
            try:
                restated, how = methods.assisted(stack, question, s1_ledger, prefer=prefer)
            except Exception as exc:
                if 'limit' in str(exc).lower(): raise
                restated, how = None, None
            if restated:
                done = _method(stack, question, restated=restated, how=how)
                if done is not None: return done
            elif how:
                if how.get('method') == 'restatement_not_confirmed' and how.get('restated'): near.update(restated=how['restated'], picked=how.get('picked'))
                ledger_store.Ledger().write('app_method', question, {'status': 'declined', 'final_tier': None, 'steps': [how], 'seconds': how['seconds'],
                                                                     'validation': {'ok': False, 'reason': how['method']}, 'recipe': None}, config={'tiers': list(ORDER)})
            later = router.answer(question, tiers=('llm',), bottles=bottles, bottle_new=not ledger_store.hosted(), s1_ledger=s1_ledger, cohort=cohort,
                                  ask_first=ask_first)
            outcome = {**later, 'steps': outcome['steps'] + ([how] if how else []) + later['steps'], 'escalations': outcome['escalations'] + later['escalations'],
                       'seconds': round(outcome['seconds'] + (how['seconds'] if how else 0.0) + later['seconds'], 2)}
    except Exception as exc:  # the bridge never blocks a question: any failure falls through to the existing path
        if 'limit' in str(exc).lower(): raise
        return None
    if isinstance(outcome.get('bottled'), int) and os.environ.get('AGERE_TIERED_AUTO_LEARN', 'off') == 'on':
        from . import learn
        learn.spawn_auto()  # a new recipe was saved: check and promote it in the background, with no manual step
    ledger_store.Ledger().write('app', question, outcome, config={'tiers': list(TIERS), **({'cohort_patients': len(cohort)} if cohort is not None else {})})
    if outcome['status'] == 'needs_clarification':   # unclear, and a person is here: ask them before computing anything
        from . import clarify
        held = _converse(stack, question, clarify.begin(question, outcome['readings']), outcome['steps'], outcome['seconds'])
        if held is not None: return held   # a language model holds the conversation; without one, the one-card question below
        options = [{'id': r['id'], 'label': r['label'], 'meaning': r.get('meaning') or r['question']} for r in outcome['readings']]
        return _clarify(stack, question, 'reading', outcome['ask'],
                        'The question can be read more than one way and nothing in it or in the governed definitions settles which; '
                        'Agere asks rather than guesses. Pick one, or type what you mean.', options,
                        context={'questions': {r['id']: r['question'] for r in outcome['readings']}}, steps=outcome['steps'], seconds=outcome['seconds'])
    if outcome['status'] == 'needs_choice':   # two plain readings with different answers (readings.py): the user picks
        outcome = _two_readings(stack, question, outcome)
        if 'answer' in outcome: return outcome
    if outcome['status'] != 'answered':
        if not takeover_ok: return None
        asking = cohort is None and _interactive(stack)
        done = _takeover(question, cohort, outcome, stack=stack if asking else None)
        return done if done is not None or not asking else _closest(stack, question)
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
    sql = store.portable(recipe.get('sql', ''), cohort, recipe.get('params'))  # saved with the answer, so it must run on the semantic layer as it is
    # Shown by the existing answer view with no view change: facts as key findings, grounding as evidence used,
    # table_binding as the computation.
    models_used = ', '.join(dict.fromkeys(str(s['model']) for s in outcome['steps'] if s.get('model'))) or 'none'
    answer['facts'] = {'answered_by': f"{TIER_NAMES[tier][0].upper() + TIER_NAMES[tier][1:]}. {outcome['seconds']} seconds, "
                                      f"${usd:.4f} in model calls (models used: {models_used})."}
    verified = verification(recipe.get('bottle_id')) if tier in ('bottles', 'near') else None
    if verified: answer['facts']['verification'] = verified
    from . import catalogue
    rule = catalogue.stated(question) if catalogue.enabled() else None
    if rule: answer['facts']['definitions_used'] = rule
    checked = outcome.get('readings_checked') or {}
    if checked.get('agree'):
        answer['facts']['two_readings'] = f"This question can be read as {' or '.join(r.lower() for r in checked['readings'])}; both were computed and give the same answer."
    elif checked.get('answered_as'):
        answer['facts']['two_readings'] = f"This question can be read more than one way; only the reading \"{checked['answered_as'].lower()}\" could be answered, and that is what is shown."
    elif outcome.get('two_readings'):
        answer['facts']['two_readings'] = outcome['two_readings']
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


def _method(stack, question, restated=None, how=None, s1_ledger=None, note=None):
    """An analysis method of the wider application answers (methods.py). Code named the method from the question, or from
    a restatement the small model wrote and System One confirmed; code ran it. -> result or None."""
    from . import methods
    steps = [how] if how else []
    if restated is None:  # the compiler named the method from the user's own words: System One may still say it does not fit
        found = methods.plan(stack, question)
        ok, step = methods.fits(stack, question, found[2], s1_ledger) if found else (False, None)
        if step and step.get('label'): steps.append(step)
        if not ok:
            if note is not None and step and step.get('other'): note['other'] = step['other']
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
    try:  # related analyses the stack can run next, offered as questions (code only)
        menu = methods._menu(stack)
        near = [op for op in methods.shortlist(stack, restated or question, k=4) if op not in got['methods'] and menu['examples'].get(op)][:2]
        if near:
            answer['next_questions'] = [menu['examples'][op][0] for op in near]
            answer['facts']['you_could_ask_next'] = ' / '.join(answer['next_questions'])
    except Exception:
        pass
    return {**result, 'answer': answer, 'question': question, 'route': 'tiered_core', 'new_paid_api_calls': calls,
            'planner_receipt': {'route': 'tiered_core', 'version': VERSION, 'final_tier': 'methods', 'recipe': {'methods': got['methods'], **({'restated': restated} if restated else {})}, 'steps': steps,
                                'escalations': [], 'validation': {'ok': True, 'checks': [{'name': 'method_named_by_code', 'ok': True}]},
                                'new_paid_api_calls': calls, 'known_api_estimate_usd': usd, 'api_cost_known': True, 'frontier_called': False,
                                'compiler': {k: v for k, v in got['info'].items() if k in ('compiler', 'recognised', 'route', 'operation')}},
            'planning_economics': {'new_api_estimate_usd': usd, 'known_api_estimate_usd': usd, 'total_cost_known': True, 'scope': 'Tiered core model calls only.'},
            'tiered_core': {'final_tier': 'methods', 'seconds': got['seconds'], 'escalations': [], 'bottled': None, 'methods': got['methods']}}


def _interactive(stack):
    """May the stack put a question back to the user? Only in the local app (it holds the saved-question store), only
    when the request is interactive, and not when switched off."""
    if getattr(stack, 'path', None) is None or os.environ.get('AGERE_TIERED_CLARIFY', 'on') != 'on': return False
    from ..clinical import clarification
    return clarification.MODE.get() == 'interactive'


def _clarify(stack, question, kind, ask, reason, options, context=None, steps=(), seconds=0.0):
    """The stack asks the user one question instead of guessing. The app's existing clarification store saves it with
    the conversation and shows it as a card with the options and a free-text box; the reply comes back to resume()."""
    from ..clinical import clarification
    usd, calls = sum(s.get('usd', 0.0) for s in steps), sum(1 for s in steps if s.get('model'))
    slot = {'needs_input': 'missing_input', 'reading': 'intended_reading', 'closest': 'closest_analysis', 'conversation': 'intended_reading'}[kind]
    spec = clarification.validate({'slot': slot, 'kind': 'input' if kind == 'needs_input' else 'goal', 'question': ask, 'reason': reason,
                                   'options': [{'id': o['id'], 'label': o['label'], 'meaning': o['meaning']} for o in options]})
    ledger_store.Ledger().write('app_clarify', question, {'status': 'clarify', 'final_tier': None, 'steps': list(steps), 'seconds': seconds,
                                                          'validation': {'ok': True, 'reason': kind}, 'recipe': {'asked': ask, 'options': [o['label'] for o in options]}},
                                config={'tiers': list(ORDER)})
    checkpoint = {'schema': clarification.VERSION, 'kind': 'tiered', 'question': question, 'cohort': None, 'pause': {'request': spec}, 'calls': [],
                  'tiered': {'kind': kind, 'earlier': [q for q in EARLIER.get() if q], **(context or {})}}
    return {'answer': {'status': 'clarify', 'answer_type': 'unsupported', 'headline': ask, 'missing': [reason], 'patient_ids': [], 'actionable_cohort': False,
                       'sources': [], 'table': [], 'limitations': ['Nothing has been computed for this question yet. The stack is waiting for your reply.'],
                       'facts': {'answered_by': f'The stack asked a question instead of guessing. {seconds} seconds, ${usd:.4f} in model calls.'}},
            'results': [], 'workflow': [], 'question': question, 'route': 'tiered_core', 'new_paid_api_calls': calls, 'synthetic': True,
            'clinical_execution_authorized': False, 'clarification_checkpoint': checkpoint,
            'graph': {'interpretation': question, 'coverage': 'unsupported', 'unmet_requirements': [reason], 'nodes': []},
            'planner_receipt': {'route': 'tiered_core', 'version': VERSION, 'final_tier': 'clarify', 'recipe': None, 'steps': list(steps), 'escalations': [],
                                'validation': {'ok': True, 'checks': []}, 'new_paid_api_calls': calls, 'known_api_estimate_usd': usd, 'api_cost_known': True,
                                'frontier_called': kind == 'reading', 'clarification': {'kind': kind, 'asked': ask}},
            'planning_economics': {'new_api_estimate_usd': usd, 'known_api_estimate_usd': usd, 'total_cost_known': True, 'scope': 'Tiered core model calls only.'},
            'tiered_core': {'final_tier': 'clarify', 'seconds': seconds, 'escalations': [], 'bottled': None, 'asked': ask}}


def _converse(stack, question, turn, steps=(), seconds=0.0):
    """One turn of the clarifying conversation (clarify.py) as the app's result. -> result dict, or None to fall back.
    'ask': the model's question goes to the person as a card, the conversation saved with it; 'ready': the one explicit
    question runs through the normal stack; 'side_by_side': each reading still open is computed and shown together."""
    if turn is None: return None
    from . import clarify, models
    from ..clinical import clarification
    state = turn['state']
    if turn['action'] == 'ask':
        n = len(state['exchanges']) + 1
        return _clarify(stack, question, 'conversation', turn['ask'],
                        f'Question {n} of at most {clarify.MAX_EXCHANGES}: the customer\'s definitions, its own facilities and your saved defaults do not settle this, '
                        'and the answers would differ. Pick one, or say what you mean.', turn['options'],
                        context={'conversation': state}, steps=list(steps) + turn['steps'], seconds=seconds)
    asked = state['readings'] if turn['action'] == 'side_by_side' else [{'id': 'explicit', 'label': 'As clarified', 'question': turn['question']}]
    results = []
    with clarification.interaction('autonomous'):
        settled = models.NO_READINGS.set(True)   # the person has said what they mean: answered as worded, never split or asked again
        try:
            for r in asked:
                got = try_answer(stack, r['question'])
                if got is None and turn['action'] == 'ready':
                    got = _takeover(r['question'], None, {'status': 'unanswered', 'steps': [], 'escalations': [{'from': 'clarification', 'reason': 'clarified question'}],
                                                          'seconds': 0.0, 'missing': 'The person clarified the question in conversation; answer it exactly as worded.'})
                if got is not None: results.append((r, got))
        finally:
            models.NO_READINGS.reset(settled)
    if turn['action'] == 'side_by_side':   # a reading answered only by "needs an input" has no computation to set beside the others
        results = [(r, g) for r, g in results if (g.get('graph') or {}).get('nodes')]
    if not results: return None
    usd, calls = clarify.spent(state)
    talk = ' | '.join(f'Asked: "{x["asked"]}" Replied: "{x["reply"]}"' for x in state['exchanges']) or 'no question was needed; your saved defaults settled it'
    if len(results) == 1:
        r, result = results[0]
    else:
        how = {'method': 'clarification_side_by_side', 'usd': 0.0, 'tokens': 0, 'seconds': 0.0}
        result = _combine(question, [x['question'] for x, _ in results], [g for _, g in results], how)
        result['answer']['limitations'][0] = (f'After {len(state["exchanges"])} questions the reading was still open, so each remaining reading was '
                                              'computed by the stack and is shown side by side. Agere does not choose between them for you.')
        result['answer']['headline'] = 'Each reading still open, side by side. ' + result['answer']['headline']
    receipt, econ = result.setdefault('planner_receipt', {}), result.setdefault('planning_economics', {})
    receipt['known_api_estimate_usd'] = round((receipt.get('known_api_estimate_usd') or 0.0) + usd + sum(s.get('usd', 0.0) for s in steps), 6)
    econ['known_api_estimate_usd'] = econ['new_api_estimate_usd'] = receipt['known_api_estimate_usd']
    result['new_paid_api_calls'] = (result.get('new_paid_api_calls') or 0) + calls
    receipt['clarifying_conversation'] = {'exchanges': state['exchanges'], 'outcome': turn['action'], 'read_as': [x['question'] for x, _ in results],
                                          'defaults_applied': state['defaults'], 'defaults_saved': state.get('choices') or [], 'usd': usd,
                                          'models': list(dict.fromkeys(s['model'] for s in state['steps'] if s.get('model'))), 'authority': 'user_intent_only'}
    facts = result['answer'].setdefault('facts', {})
    result['answer']['facts'] = {'clarified': f'{talk}. Read as: ' + ' / '.join(f'"{x["question"]}"' for x, _ in results)
                                              + f'. The conversation cost ${usd:.4f} in model calls; the language model only clarified, the stack computed the answer.'
                                              + (' Your choices were saved as your defaults: ' + '; '.join(f'{c["about"]}: {c["choice"]}' for c in state['choices']) + '.'
                                                 if state.get('choices') else ''), **facts}
    return result


def _figure(columns, rows):
    """A one-row answer as its numbers ('175' or '261, 0.12'); None for a longer answer."""
    if not rows or len(rows) != 1: return None
    nums = [v for v in rows[0] if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return ', '.join(f'{v:,}' if isinstance(v, int) else f'{v:,.4g}' for v in nums) or None


def _two_readings(stack, question, outcome):
    """Both readings were answered and differ. In the local app the user is asked, with each reading's figure on its option;
    elsewhere both readings are returned side by side as one table, labelled, so no reading is chosen for the user."""
    rs = outcome['readings']
    usd = sum(s.get('usd', 0.0) for s in outcome['steps'])
    if _interactive(stack):
        options = [{'id': r['id'], 'label': r['label'] + (f": {_figure(r['columns'], r['rows'])}" if _figure(r['columns'], r['rows']) else ''),
                    'meaning': r['meaning']} for r in rs]
        return _clarify(stack, question, 'reading', outcome['ask'],
                        'Both readings were computed and the answers differ; Agere does not choose between them for you.', options,
                        context={'questions': {r['id']: r['question'] for r in rs}}, steps=outcome['steps'], seconds=outcome['seconds'])
    first = rs[0]
    if all(len(r['rows'] or []) == 1 and r['columns'] == first['columns'] for r in rs):
        columns, rows = ['reading', *first['columns']], [[r['label'], *r['rows'][0]] for r in rs]
    else:   # longer answers cannot share one table: the first reading is shown and the other is named
        columns, rows = first['columns'], first['rows']
    validation = {'ok': True, 'checks': [c for r in rs for c in (r['validation'] or {}).get('checks', [])]}
    return {**outcome, 'status': 'answered', 'final_tier': first['final_tier'], 'recipe': first['recipe'], 'columns': columns, 'rows': rows, 'validation': validation,
            'two_readings': f"{outcome['ask'].split('?')[0]}? Both are shown: " + '; '.join(f"{r['label'].lower()}: {_figure(r['columns'], r['rows']) or 'see rows'}" for r in rs) + '.'}


def _closest(stack, question):
    """Nothing answered: offer the closest analyses the stack can run, as questions the user can pick, or let them rephrase."""
    from . import methods
    if not methods.enabled(): return None
    try:
        menu = methods._menu(stack)
        near = [op for op in methods.shortlist(stack, question, k=3) if menu['examples'].get(op)][:3]
    except Exception:
        return None
    if len(near) < 2: return None
    options = [{'id': op, 'label': menu['examples'][op][0], 'meaning': ' '.join(menu['ops'][op].split())[:160]} for op in near]
    return _clarify(stack, question, 'closest', 'I could not answer that as asked. Is one of these what you need, or can you say it another way?',
                    'No method, recipe or the frontier model produced an answer inside the limits.', options,
                    context={'questions': {op: menu['examples'][op][0] for op in near}})


def _not_answered(question, note):
    """A reply could not be turned into an answer: say so plainly, with what was tried. No model."""
    return {'answer': {'status': 'unsupported', 'answer_type': 'unsupported', 'headline': 'That still could not be answered. ' + note, 'missing': [note], 'patient_ids': [],
                       'sources': [], 'table': [], 'limitations': ['Nothing was computed.']},
            'results': [], 'workflow': [], 'question': question, 'route': 'tiered_core', 'new_paid_api_calls': 0,
            'graph': {'interpretation': question, 'coverage': 'unsupported', 'unmet_requirements': [note], 'nodes': []},
            'planner_receipt': {'route': 'tiered_core', 'version': VERSION, 'final_tier': None, 'recipe': None, 'steps': [], 'escalations': [],
                                'validation': {'ok': False, 'checks': []}, 'new_paid_api_calls': 0, 'known_api_estimate_usd': 0.0, 'api_cost_known': True, 'frontier_called': False},
            'planning_economics': {'new_api_estimate_usd': 0.0, 'known_api_estimate_usd': 0.0, 'total_cost_known': True, 'scope': 'Tiered core model calls only.'},
            'tiered_core': {'final_tier': None, 'seconds': 0.0, 'escalations': [], 'bottled': None}}


def resume(stack, saved, reply):
    """The user answered a question the stack asked (_clarify). -> the app's result dict. The reply is answered, never
    met with a second question: an option that is itself a question runs through the ladder; added detail is joined to
    the original question and tried through the ladder; otherwise the frontier model answers with the exchange in front of it."""
    _budget(fresh=True)
    from ..clinical import clarification
    context, original, request = saved.get('tiered') or {}, saved['question'], saved['pause']['request']
    option, text = reply.get('option') or {}, ' '.join(str(reply.get('text') or '').split())
    said = text or option.get('meaning') or option.get('label') or ''
    EARLIER.set(tuple(context.get('earlier') or ())[-4:])
    result, read_as = None, None
    if context.get('kind') == 'conversation':   # the clarifying conversation goes on: the model reads the reply and asks, or hands over
        from . import clarify
        held = _converse(stack, original, clarify.reply(context['conversation'], text, option))
        if held is not None: return held
    with clarification.interaction('autonomous'):
        picked = (context.get('questions') or {}).get(option.get('id'))
        if picked:
            read_as = picked
        elif text and context.get('kind') == 'closest':
            read_as = text  # the user said it another way: that is the question now
        elif text and context.get('kind') == 'needs_input':
            read_as = original.rstrip(' ?.') + ', ' + text
        elif text and context.get('kind') == 'reading':   # they said what they mean in their own words
            read_as = original.rstrip(' ?.') + ' (' + text + ')'
        if read_as:
            from . import models
            settled = models.NO_READINGS.set(True)   # the person has said which reading: it is answered, not split or asked again
            try:
                result = try_answer(stack, read_as)
            finally:
                models.NO_READINGS.reset(settled)
            if result is not None and (result.get('tiered_core') or {}).get('needs_input'): result = None  # still short of an input: the frontier model answers
        if result is None:
            if context.get('kind') == 'needs_input' and option.get('id') == 'explain':
                why = ('The user asked what these missing inputs are: ' + '; '.join(context.get('wanted') or []) + '. Explain in plain language what each one is and where an '
                       'organisation usually finds it. Then say what the warehouse does hold that relates to the question. Do not invent any of the missing values.')
            elif context.get('kind') == 'needs_input' and not text:
                why = ('These inputs are not in the question or the warehouse: ' + '; '.join(context.get('wanted') or []) + '. The user asked for the best answer the warehouse '
                       'supports without them. Compute what can be computed, and state plainly what cannot be computed without each input. Do not invent them.')
            else:
                why = f'The user was asked: "{request["question"]}" and answered: "{said}". Answer the original question on that basis. State the chosen reading as a caveat.'
            result = _takeover(original, None, {'status': 'unanswered', 'steps': [], 'escalations': [{'from': 'clarification', 'reason': 'user replied'}],
                                                'seconds': 0.0, 'missing': why})
    if result is None: return _not_answered(original, 'Try a narrower question: one measure, one period, or one patient.')
    note = f'You were asked: "{request["question"]}" and answered: "{said}".' + (f' Read as: "{read_as}".' if read_as and read_as != said else '')
    result['answer']['facts'] = {'your_reply': note, **(result['answer'].get('facts') or {})}
    result['planner_receipt']['clarification_reply'] = {'asked': request['question'], 'reply': said, 'read_as': read_as, 'authority': 'user_intent_only'}
    return result


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


def _takeover(question, cohort, outcome, stack=None):
    """No tier answered: the frontier model takes over, bounded by turns and seconds (takeover.py). -> result or None."""
    from . import takeover
    if not takeover.enabled(): return None
    why = str(outcome.get('missing') or '; '.join(e.get('reason', '') for e in outcome.get('escalations') or [])[-400:])
    earlier = [q for q in EARLIER.get() if q and q != question]
    if earlier and 'previous question in the conversation' not in why:
        why += ' | Earlier questions in this conversation, oldest first (use them only if this question refers to them): ' + ' / '.join(f'"{q}"' for q in earlier)
    left = (DEADLINE.get() - __import__('time').monotonic()) if DEADLINE.get() is not None else None
    # The last rung is never skipped for time (Mike 2026-11-17: an unanswered question goes to the frontier model). When the
    # cheaper rungs used the question's budget, the takeover still gets TAKEOVER_FLOOR seconds of its own; it stays bounded.
    if left is not None: left = max(left, TAKEOVER_FLOOR)
    try:
        got = takeover.answer(question, cohort, why=why, **({'may_ask': True} if stack is not None else {}), **({'seconds': left} if left is not None else {}))
    except Exception as exc:
        if 'limit' in str(exc).lower(): raise
        import sys
        print(f'tiered takeover failed: {type(exc).__name__}: {str(exc)[:200]}', file=sys.stderr, flush=True)
        return None
    if got is None: return None
    if got.get('ask'):  # the frontier model asked the user instead of guessing between readings
        return _clarify(stack, question, 'reading', got['ask'], 'The question can be read in more than one way, or depends on something only you know; the answers would differ.',
                        [{'id': f'r{i}', 'label': o[:120], 'meaning': o} for i, o in enumerate(got['options'], 1)],
                        steps=outcome['steps'] + got['steps'], seconds=round(outcome['seconds'] + got['seconds'], 2))
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
    if got.get('next'):
        answer['next_questions'] = got['next']
        answer['facts']['you_could_ask_next'] = ' / '.join(got['next'])
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
