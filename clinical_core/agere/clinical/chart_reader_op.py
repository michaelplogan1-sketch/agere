"""The `chart_reader` operation: one patient's chart read as a graph node (engine_suite contract), so the cascade rung
chart_reader.tier() and the shared planner can run it through analysis_graph like any other engine.

  parameters   patient_id (exact, from patient_ids), question (verbatim), subject_quote, how (id | member_id | pronoun | prior_turn)
  inputs       none (the patient is a parameter, not a cohort: a named patient is never filtered by the living default)
  execute      chart_reader.answer(...) over the structured sources / note frames; NEEDS_STACK because a note question reads passages
  answer       the chart_reader answer contract merged into the graph answer (headline kept through `chart_headline`)

Registered by one line in engine_suite.MODULES: 'chart_reader': 'chart_reader_op'.
"""
from __future__ import annotations

import re

OP = 'chart_reader'
NEEDS_STACK = True
NATIVE = ('count', 'value')
PRODUCT = {'population': 'none', 'view': 'table',
           'use': 'ONE PATIENT\'S CHART: any question about a single named patient (SYN-/WHP-/P- id, member id) over labs, medications and fills, '
                  'conditions, visits, ED / inpatient stays, appointments, immunizations, screening, allergies, social needs, claims and spend, '
                  'care gaps, a pre-visit brief, and what the notes say (note frames). Answers carry units, dates and source record ids.'}
CONTRACT = {'inputs': [],
            'parameters': {'patient_id': 'exact patient id from the warehouse', 'question': 'the single-patient question verbatim',
                           'subject_quote': 'how the question named the patient', 'how': 'patient_id|member_id|pronoun|prior_turn',
                           'chart': 'the typed chart request (intent, domain, concept, window, ordering, note predicates)'},
            'meaning': 'Answer a question about one named patient from the structured chart and the notes. Latest labs per the decision '
                       'context, ED / inpatient events from all sources, spend from counted payer claims; a named deceased patient is '
                       'answered and flagged. Use for questions that name a patient id, not for cohorts.'}
ASSUMPTIONS = ['Synthetic warehouse data; a named patient is answered even when deceased, and the answer says so.',
               'Free-text note questions are readings by System One of retrieved passages, not clinician adjudication.']
HOW = ('patient_id', 'member_id', 'pronoun', 'prior_turn')


def schema(obj, string, array):
    return obj({'patient_id': {'type': 'string', 'description': 'Exact patient id (SYN-xxxxxxxx-xxx, WHP-xxxxxxxxxxxx or P-xxxx)'},
                'question': {'type': 'string', 'description': 'The single-patient question, verbatim'},
                'subject_quote': {'type': 'string', 'description': 'The words that named the patient'},
                'how': string(HOW, 'How the patient was identified'),
                'chart': {'type': 'object', 'description': 'The typed chart request: intent, domain, concept, window, ordering, note predicates'}})


def validate(p):
    if not isinstance(p, dict) or set(p) - {'patient_id', 'question', 'subject_quote', 'how', 'chart'}:
        raise ValueError('chart_reader parameters: patient_id, question, subject_quote, how, chart')
    if 'chart' in p and not isinstance(p['chart'], dict):
        raise ValueError('chart: the typed chart request (an object)')
    pid, question = p.get('patient_id'), p.get('question')
    if not isinstance(pid, str) or not re.fullmatch(r'(?:SYN-[0-9A-F]{8}-[0-9A-F]{3}|WHP-[0-9A-F]{12}|P-[A-Z0-9]{2,8})', pid):
        raise ValueError('patient_id: an exact warehouse patient id (SYN-xxxxxxxx-xxx, WHP-xxxxxxxxxxxx or P-xxxx)')
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ValueError('question: the single-patient question, 1-2000 characters')
    how = p.get('how') or 'patient_id'
    if how not in HOW:
        raise ValueError(f'how: one of {HOW}')
    out = {'patient_id': pid, 'question': question.strip(), 'subject_quote': p.get('subject_quote') or pid, 'how': how}
    if p.get('chart'):
        out['chart'] = p['chart']  # the typed request the engine executes (dropping it lost the inherited window / ordinal)
    return out


def execute(stack, patient_ids, params, as_of):
    from . import chart_reader, patient_ids as PI
    spec = {'subject': {'kind': 'patient', 'patient_id': params['patient_id'], 'quote': params['subject_quote']}, 'question': params['question']}
    # Execute the typed request the plan carries (intent with any window or ordinal inherited at planning time, note predicates);
    # re-parsing the text here would lose the conversation context, which belongs to the planning request.
    chart = params.get('chart') or {}
    if chart.get('note_predicates'):
        spec['note'] = chart['note_predicates']
    elif chart.get('intent'):
        spec['chart'] = {k: chart.get(k) for k in ('intent', 'domain', 'concept', 'window', 'ordinal', 'attribute', 'mode', 'flags', 'comparisons', 'screen_filter') if k in chart}
    out = chart_reader.answer(stack, spec, as_of=as_of, question=params['question'], prior=PI.current_prior())
    return {'schema': chart_reader.VERSION, 'answer_contract': out, 'patient_ids': [params['patient_id']]}


def answer(report, plan, question):
    out = dict(report['answer_contract'])
    partial = out.get('status') != 'answered' or plan.get('coverage') != 'supported_observation' or bool(plan.get('unmet_requirements'))
    t = out.get('answer_type')
    graph_type = {'value': 'value', 'count': 'count'}.get(t, 'table')
    value = out.get('value')
    if graph_type == 'table' or isinstance(value, bool):
        value = out.get('headline')  # the dev scorer and the UI read count / value; text answers carry their headline as the value
    count = out.get('count') if t == 'count' else (1 if t == 'yes_no' and out.get('yes_no') else 0 if t == 'yes_no' else out.get('count'))
    if graph_type == 'table' and not out.get('table') and t == 'label' and out.get('value') is not None:
        # A sourced label is a populated observation, just like a boolean. Its
        # scalar value must survive adaptation so guards can inspect the result.
        out = {**out, 'table': [{'patient_id': pid, 'value': out['value']}
                               for pid in report['patient_ids']]}
    merged = {**out, 'status': 'unsupported' if out.get('status') == 'unsupported' else ('partial' if partial and out.get('status') != 'answered' else 'answered'),
              'primary_answer_type': t, 'primary_value': out.get('value'),
              'answer_type': graph_type, 'value': value, 'count': count, 'patient_ids': report['patient_ids'], 'membership_role': 'selected',
              'chart_headline': out.get('headline'), 'definitions': [{'term': 'chart reader', 'basis': out.get('method')}],
              'limitations': [*ASSUMPTIONS, *(out.get('limitations') or [])]}
    if out.get('answer_type') == 'yes_no':
        merged['answer_type'] = 'table'
        merged['value'] = out.get('headline')
        if not merged.get('table'):
            # A determinate NO is a populated boolean answer, not an empty
            # result table. Preserve that value through the graph adapter so
            # zero-result checks do not reject a correctly read negative chart.
            merged['table'] = [{'patient_id': report['patient_ids'][0],
                                'answer': out.get('yes_no', out.get('value'))}]
    return merged


def route(question, profile, as_of):
    """Not a local-compiler route: chart_reader.tier() is the cascade rung, because it needs the stack's connection and the prior turn."""
    return None
