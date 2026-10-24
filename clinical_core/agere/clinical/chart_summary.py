"""Care gaps and the patient brief ("summarize this patient", "what should I know before seeing X"), from computed facts only.

The brief is assembled by the same executors that answer single questions (chart_queries), so every line of it carries the same
sources and definitions. No model writes a fact. An optional narrative (narrate) is a Haiku paraphrase of the computed facts, returned
in a separate field marked `generated_from_facts: true`; it is never used in place of the facts.
"""
from __future__ import annotations

import time
from pathlib import Path

from . import chart_intents as CI
from . import chart_queries as Q

VERSION = 'chart-summary/1'
KEY_LABS = ('hba1c', 'egfr', 'ldl', 'systolic_bp', 'creatinine', 'potassium', 'uacr', 'weight', 'nt_probnp')
GAP_TIMEOUT_NOTE = 'care-gap engine unavailable for this call'


def care_gaps(ch, it, path=None):
    """Open and closed gaps for this patient from the care-gap closure engine (breast, colorectal, influenza, annual wellness)."""
    from . import care_gap_closure
    if path is None:
        return Q.no_data(f'The care-gap engine needs the stack path; none was given for {ch.pid}')
    try:
        rep = care_gap_closure.execute(Path(path), [ch.pid], {'focus': 'all', 'exclude_not_applicable': False}, ch.as_of)
    except Exception as exc:  # noqa: BLE001 - a missing source table is a stated gap, not a crash
        return Q.no_data(f'{GAP_TIMEOUT_NOTE}: {type(exc).__name__}: {str(exc)[:120]}')
    rows = rep['table']
    open_ = [r for r in rows if r['gap_status'] == 'open-gap']
    closed = [r for r in rows if r['gap_status'] == 'closed-gap']
    na = [r for r in rows if r['gap_status'] == 'not-applicable']
    table = [{'measure': r['measure_label'], 'status': r['gap_status'], 'evidence': r.get('evidence'), 'evidence_date': r.get('evidence_date'),
              'next_action': (r.get('next_action') or {}).get('step')} for r in rows]
    sources = [Q.src('care_gap_closure', r.get('evidence_record'), r.get('evidence_date'), measure=r['measure']) for r in rows if r.get('evidence_record')]
    head = (f"{len(open_)} open care gap(s) for {ch.pid}: " + '; '.join(r['measure_label'] for r in open_)) if open_ else f'No open care gap for {ch.pid}'
    head += f"; {len(closed)} closed, {len(na)} not applicable"
    if it.get('intent') == 'status':
        return Q.core('yes_no', head, value=bool(open_), count=len(open_), table=table, sources=sources, notes=[rep['method']])
    return Q.core('list', head, count=len(open_), table=table, sources=sources, facts={'open': [r['measure'] for r in open_], 'closed': [r['measure'] for r in closed]}, notes=[rep['method']])


def _sub(ch, domain, intent='latest_value', **kw):
    return {'domain': domain, 'intent': intent, 'concept': kw.get('concept'), 'window': kw.get('window'), 'attribute': kw.get('attribute'), 'ordinal': None,
            'mode': 'latest', 'flags': [], 'gaps': [], 'question': '', **{k: v for k, v in kw.items() if k not in ('concept', 'window', 'attribute')}}


def summary(ch, it, path=None):
    start, end = ch.window_year
    window = {'kind': 'rolling_12_months', 'start': start, 'end': end, 'start_exclusive': True, 'phrase': 'last 12 months'}
    sections, sources, lines, started = [], [], [], time.perf_counter()

    def add(title, c, line=None):
        if c is None:
            return
        sections.append({'section': title, 'answer_type': c['answer_type'], 'headline': c['headline'], 'table': c['table'][:25], 'facts': c['facts']})
        sources.extend(c['sources'][:6])
        if line is not False:
            lines.append(line or c['headline'])

    p = ch.patient or {}
    d = ch.death
    age = Q.age_at(p.get('birth_date'), ch.as_of)
    ident = f"{ch.pid}: age {age}, {p.get('sex_reported')}" + (f", deceased {d['death_date']}" if d else '')
    sections.append({'section': 'identity', 'headline': ident, 'table': [], 'facts': {'age': age,'sex': p.get('sex_reported'), 'deceased': bool(d)}})
    lines.append(ident)
    cov = Q.coverage(ch, _sub(ch, 'coverage'))
    add('coverage', cov, cov['headline'] if cov else None)
    add('practice', Q.practice(ch, _sub(ch, 'practice')))
    add('problems', Q.problem_list(ch, _sub(ch, 'problem_list')))
    add('conditions', Q.condition(ch, _sub(ch, 'condition', 'list')))
    meds = Q.medication(ch, _sub(ch, 'medication', 'list'))
    add('medications', meds)
    labs = []
    for concept in KEY_LABS:
        c = Q.lab(ch, _sub(ch, 'lab', concept={'kind': 'lab', 'value': concept, 'concepts': [concept]}))
        if c and c['answer_type'] == 'value':
            labs.append({'measure': Q.label_of(concept), 'value': c['value'], 'unit': c['unit'], 'date': c['facts'].get('date')})
            sources.extend(c['sources'])
    if labs:
        text = 'Latest results: ' + '; '.join(f"{x['measure']} {Q.fmt(x['value'])} {x['unit'] or ''} ({x['date']})".replace('  ', ' ') for x in labs)
        sections.append({'section': 'labs', 'headline': text, 'table': labs, 'facts': {}})
        lines.append(text)
    for domain in ('ed_visit', 'inpatient', 'observation'):
        c = Q.utilization(ch, _sub(ch, domain, 'count_in_window', window=window))
        add(f'{domain}_12m', c, False)
        if c and c['count']:
            lines.append(c['headline'])
    add('readmission', Q.readmission(ch, _sub(ch, 'readmission', 'status')))
    appts_nos = Q.appointments(ch, _sub(ch, 'appointment', 'count_in_window', window=window, question='no-shows'))
    add('no_shows_12m', appts_nos, appts_nos['headline'] if appts_nos and appts_nos['count'] else False)
    nxt = Q.appointments(ch, _sub(ch, 'appointment', 'last_event', question='next upcoming appointment'))
    add('next_appointment', nxt, nxt['headline'] if nxt and nxt['answer_type'] == 'date' else False)
    unfilled = Q.prescriptions(ch, {**_sub(ch, 'prescription', 'list'), 'flags': ['not_filled']})
    add('never_filled_prescriptions', unfilled, unfilled['headline'] if unfilled and unfilled['count'] else False)
    ret = Q.returned_fills(ch, _sub(ch, 'returned_fill', 'list'))
    add('returned_fills', ret, ret['headline'] if ret and ret['count'] else False)
    flu = Q.immunizations(ch, _sub(ch, 'immunization', 'last_event', concept={'kind': 'vaccine', 'value': 'influenza%'}))
    add('influenza_vaccine', flu)
    for test in ('bilateral_screening_mammography', 'FIT'):
        label = 'mammography' if test.startswith('bilateral') else 'colorectal screening'
        add(f'screening_{label}', Q.screenings(ch, _sub(ch, 'screening', 'last_event', concept={'kind': 'screening', 'value': test, 'label': label})))
    add('allergies', Q.allergies(ch, _sub(ch, 'allergy', 'list')))
    add('tobacco', Q.smoking(ch, _sub(ch, 'smoking')))
    needs = Q.social_needs(ch, _sub(ch, 'social_need', 'list'))
    add('social_needs', needs)
    add('social_vulnerability', Q.svi(ch, _sub(ch, 'svi')))
    spend = Q.claims(ch, _sub(ch, 'claims', 'sum', window=window, attribute='paid'))
    add('spend_12m', spend)
    add('care_gaps', care_gaps(ch, _sub(ch, 'care_gap', 'list'), path))
    head = f"Brief for {ch.pid} as of {ch.as_of}: " + ' | '.join(lines[:4]) + f' ({len(sections)} sections)'
    facts = {'deceased': bool(d), 'labs': labs, 'sections': [s['section'] for s in sections]}
    return Q.core('summary', head, table=[{'section': s['section'], 'summary': s['headline']} for s in sections], sources=sources, facts=facts,
                  notes=['assembled from structured sources only; every section is one of the single-question answers',
                         'utilization, spend and no-shows are for the 12 months ending the as-of date', f'built in {time.perf_counter() - started:.2f}s'],
                  sections=sections, text='\n'.join(f'- {x}' for x in lines))


def narrate(core_answer, *, model=None, max_tokens=500):
    """Optional narrative paraphrase of the computed facts (never a source of facts). `model` is a callable (system, user, max_tokens) ->
    (text, info) such as nl2sql.HostedModel(...); Haiku only. Returns {'text', 'generated_from_facts': True, 'model_info'} or None."""
    if model is None:
        return None
    system = ('You are a clinical documentation assistant. Rewrite the facts as a short, plain pre-visit brief of 120 words or fewer. '
              'Use only the facts given; add nothing, infer nothing, keep every number and date exactly as given. If a section says no data, say so.')
    user = 'FACTS\n' + core_answer.get('text', core_answer.get('headline', ''))
    text, info = model(system, user, max_tokens)
    return {'text': text, 'generated_from_facts': True, 'model_info': info}
