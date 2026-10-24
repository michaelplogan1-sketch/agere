"""Read-back: a deterministic plain-English rendering of a question spec (docs/agere/QUESTION_SPEC.md, Addition 3).

    read_back(spec, catalog=None, question=None, as_of=None) -> str
    parts(spec, catalog=None, question=None, as_of=None) -> [{'id', 'kind', 'text', 'quotes'}]

The spec is the typed request (request_plan): source, select, where, group_by, order_by, limit, optional analyses, subject. Nothing here
calls a model or the warehouse. The sentence names the population, the decision-context defaults that were applied (living patients,
latest labs, the 12 months ending at the as-of date, the one condition-evidence rule), the measure, the window, the grouping, every note
predicate (frame, status, window, how passages combine) and the subject. spec_verify.py checks each part against the question with System One,
using the part's own sentence as evidence; the whole sentence is shown with every answer as answer['read_back'].
"""
import re

VERSION = 'spec-readback/1'

ACRONYMS = {'sglt2': 'SGLT2', 'glp1': 'GLP-1', 'ace': 'ACE', 'arb': 'ARB', 'ckd': 'CKD', 'copd': 'COPD', 'hba1c': 'HbA1c', 'egfr': 'eGFR',
            'ldl': 'LDL', 'ed': 'ED', 'er': 'ER', 'bp': 'BP', 'hf': 'HF', 'hfref': 'HFrEF', 'icd10': 'ICD-10', 'svi': 'SVI', 'pcp': 'PCP',
            'pmpm': 'PMPM', 'phq9': 'PHQ-9', 'uacr': 'UACR', 'hie': 'HIE', 'ehr': 'EHR', 'adt': 'ADT', 'id': 'ID', 'a1c': 'A1c', 'mra': 'MRA',
            'arni': 'ARNI', 'dpp4': 'DPP-4', 'pdc': 'PDC', 'cad': 'CAD', 'ocr': 'OCR', 'rx': 'Rx', 'cpt': 'CPT', 'hcc': 'HCC', 'ldlc': 'LDL-C'}
TABLE_NOUNS = {'patient': 'patients', 'utilization_event': 'utilization events (ED, inpatient and observation, all sources)', 'stay': 'stays',
               'visit': 'visits', 'claim': 'claims', 'payer_claim': 'payer claims', 'payer_claim_line': 'payer claim lines',
               'pharmacy_claim': 'pharmacy claims', 'fill': 'fills', 'medication': 'medication rows', 'condition': 'diagnosis rows',
               'measurement': 'results', 'measurement_latest': 'latest results', 'appointment': 'appointments', 'encounter': 'encounters',
               'member_month': 'member months', 'social_need': 'social-need rows', 'clinician': 'clinicians'}
OPS = {'=': 'is', '!=': 'is not', '>': 'is above', '>=': 'is at least', '<': 'is below', '<=': 'is at most', 'like': 'matches',
       'in': 'is one of', 'not_in': 'is none of', 'is_null': 'is missing', 'not_null': 'is present'}
STATUS_WORDS = {'documented': 'documented', 'denied': 'explicitly denied', 'possible': 'possible or suspected', 'planned_not_done': 'planned but not done',
                'historical': 'historical', 'other_person': 'about another person', 'not_mentioned': 'not mentioned'}
FRAME_WORDS = {'condition': 'a condition', 'symptom': 'a symptom', 'medication_event': 'a medication event', 'procedure_test': 'a procedure or test',
               'finding_value': 'a finding or value', 'adverse_event': 'an adverse event', 'care_process': 'a care process',
               'social_need': 'a social need', 'function_cognition': 'function or cognition', 'preference_refusal': 'a preference or refusal',
               'plan_followup': 'a plan or follow-up', 'note_contradiction': 'a contradiction between notes'}
COMBINE_WORDS = {'any': 'any passage or note counts', 'latest': 'the latest passage decides', 'first': 'the first passage decides',
                 'trend': 'the passages are read as a trend', 'conflict': 'passages that disagree are reported as a conflict',
                 'all': 'every passage must agree'}


def words(identifier):
    """'sglt2_inhibitor' -> 'SGLT2 inhibitor'; 'payer_claim.paid_cents' -> 'payer claim paid cents'."""
    out = []
    for w in re.split(r'[_.\s]+', str(identifier)):
        if w: out.append(ACRONYMS.get(w.lower(), w))
    return ' '.join(out)


def _list(items, joiner='or'):
    items = [str(i) for i in items]
    if len(items) <= 1: return ''.join(items)
    return ', '.join(items[:-1]) + f' {joiner} ' + items[-1]


def field_label(field):
    table, _, column = str(field).partition('.')
    if table == 'patient': return words(column)
    if table in ('measurement', 'measurement_latest') and column.startswith('value'): return ('latest ' if table == 'measurement_latest' else '') + 'result value'
    return f'{words(column)} ({words(table)})'


def value_text(value):
    if isinstance(value, list): return _list([value_text(v) for v in value])
    if isinstance(value, bool): return 'true' if value else 'false'
    if isinstance(value, str): return value if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) else f'"{value}"'
    return str(value)


def concept_text(expr):
    names = [words(n.partition('@')[0]) for n in expr['names']]
    period = expr.get('period')
    window = f' with evidence dated {period["start"]} to {period["end"]}' if period else ''
    if expr['kind'] == 'condition':
        return f'a documented {_list(names)} diagnosis (EHR diagnosis or counted payer-claim diagnosis){window}'
    return f'a picked-up {_list(names)} fill (dispensed, not reversed){window}'


def note_text(expr):
    statuses = _list([STATUS_WORDS.get(s, s) for s in expr['statuses']])
    frame = FRAME_WORDS.get(expr['frame'], expr['frame'])
    detail = ''
    if expr.get('detail'):
        detail = ' (' + '; '.join(f'{words(k)}: {_list([words(x) for x in (v if isinstance(v, list) else [v])])}' for k, v in expr['detail'].items()) + ')'
    window = expr.get('window')
    when = f' between {window["start"]} and {window["end"]}' if window else ''
    where = f', read from {_list([words(s) for s in expr["sources"]], "and")}' if expr.get('sources') else ''
    return (f'the notes show {frame} "{expr["concept"]["term"]}"{detail} as {statuses}{when}{where}; System One reads the passages per patient and '
            f'{COMBINE_WORDS.get(expr.get("combine", "any"), expr.get("combine"))}; a patient it cannot decide is counted as unknown, not as no')


KIND_WORDS = {'ed_visit': 'ED visit', 'inpatient': 'inpatient stay', 'observation': 'observation stay'}
SUBJECT_COLUMNS = ('drug_class', 'concept', 'kind', 'type', 'type_concept')
VALUE_COLUMNS = ('value', 'value_canonical', 'value_numeric')
OP_WORDS = {'>': 'above', '>=': 'at least', '<': 'below', '<=': 'at most', '=': 'equal to', '!=': 'not equal to'}


def _and_args(expr):
    if isinstance(expr, dict) and expr.get('op') == 'and':
        return [c for a in expr['args'] for c in _and_args(a)]
    return [expr] if isinstance(expr, dict) else []


def _date_window(clauses):
    """(text, rest): date comparisons of one field folded into a window phrase."""
    dates = [c for c in clauses if c.get('op') in ('>', '>=', '<', '<=', 'within_last_months') and str(c.get('field', '')).endswith(('_date', '_time', '_at'))]
    rest = [c for c in clauses if c not in dates]
    low = next((c for c in dates if c['op'] in ('>', '>=')), None)
    high = next((c for c in dates if c['op'] in ('<', '<=')), None)
    last = next((c for c in dates if c['op'] == 'within_last_months'), None)
    if last: return f'within the last {last["value"]} months (the {last["value"]} months ending at the as-of date)', rest
    if low and high:
        end = f'through {high["value"]}' if high['op'] == '<=' else f'up to but not including {high["value"]}'
        return f'from {low["value"]}{" (exclusive)" if low["op"] == ">" else ""} {end}', rest
    if low: return f'after {low["value"]}' if low['op'] == '>' else f'on or after {low["value"]}', rest
    if high: return f'before {high["value"]}' if high['op'] == '<' else f'through {high["value"]}', rest
    return '', rest


def exists_phrase(expr, negated):
    table, where = expr['table'], expr.get('where')
    lead = 'no' if negated else 'a'
    if table == 'death' and not where: return f'{lead} death record'
    clauses = _and_args(where)
    subject = next((c for c in clauses if c.get('op') in ('=', 'in') and str(c.get('field', '')).split('.')[-1] in SUBJECT_COLUMNS), None)
    clauses = [c for c in clauses if c is not subject]
    value = next((c for c in clauses if str(c.get('field', '')).split('.')[-1] in VALUE_COLUMNS and c.get('op') in OP_WORDS), None)
    clauses = [c for c in clauses if c is not value]
    window, clauses = _date_window(clauses)
    names = []
    if subject:
        raw = subject['value'] if isinstance(subject['value'], list) else [subject['value']]
        names = [KIND_WORDS.get(v, words(v)) for v in raw]
    label = _list(names)
    if table in ('measurement', 'measurement_latest'):
        what = f'{"latest " if table == "measurement_latest" else ""}{label or "lab"} result'
    elif table in ('fill', 'pharmacy_claim', 'payer_pharmacy_claim', 'medication', 'returned_fill'):
        what = f'{label + " " if label else ""}{"fill" if table != "medication" else "medication record"}'
    elif table in ('utilization_event', 'visit', 'stay', 'encounter', 'claim', 'payer_claim', 'appointment'):
        kind = bool(subject) and subject['field'].split('.')[-1] == 'kind'
        noun = TABLE_NOUNS.get(table, words(table)).split(' (')[0]
        noun = noun[:-1] if noun.endswith('s') else noun
        what = label if kind else f'{noun}' + (f' of type {label}' if label else '')
    else:
        what = f'{words(table)} record' + (f' of {label}' if label else '')
    if value: what += f' {OP_WORDS[value["op"]]} {value_text(value["value"])}'
    if window: what += f' {window}'
    if clauses: what += ' where ' + ' and '.join(predicate_text(c) for c in clauses)
    if lead == 'a' and re.match(r'[aeiouAEIOU]|ED |EHR|HIE|ACE|ARB|MRA|SGLT|eGFR|LDL', what): lead = 'an'
    return f'{lead} {what}'


def predicate_text(expr, negated=False):
    """The condition as a noun phrase ('a documented diabetes diagnosis', 'no SGLT2 inhibitor fill within the last 12 months')."""
    if not isinstance(expr, dict): return 'every row'
    op = expr.get('op')
    if op == 'concept':
        text = concept_text(expr)
        return ('no ' + text.replace('a documented ', 'documented ', 1).replace('a picked-up ', 'picked-up ', 1)) if negated else text
    if op == 'note':
        text = note_text(expr)
        return ('not (' + text + ')') if negated else text
    if op in ('and', 'or'):
        joiner = ' and ' if op == 'and' else ' or '
        inner = joiner.join(_wrap(a, op) for a in expr['args'])
        return f'not ({inner})' if negated else inner
    if op == 'not':
        return predicate_text(expr['arg'], not negated)
    if op == 'exists':
        return exists_phrase(expr, negated)
    if op == 'sum_at_least':
        text = f'a total of {words(expr["field"])} {expr.get("cmp", ">=")} {value_text(expr["amount"])} over {TABLE_NOUNS.get(expr["table"], words(expr["table"]))}' + (
            f' where {predicate_text(expr["where"])}' if expr.get('where') else '')
        return f'not ({text})' if negated else text
    if op == 'at_least_of':
        text = f'at least {expr["n"]} of: ' + '; '.join(predicate_text(a) for a in expr['args'])
        return f'not ({text})' if negated else text
    if op == 'count_at_least':
        text = f'{"more than" if expr.get("strict") else "at least"} {expr["n"]} {words(expr["table"])} event(s)' + (f' where {predicate_text(expr["where"])}' if expr.get('where') else '')
        return f'not ({text})' if negated else text
    if op == 'sequence':
        low, high = expr['days']
        anchor = expr['anchor']
        what = words(anchor['table']) if 'table' in anchor else 'the counted event'
        then = expr['then']
        then_text = _list([words(t['table']) for t in then['any']]) if isinstance(then, dict) and 'any' in then else words(then['table'])
        text = f'a {then_text} event {low} to {high} days from the {what} event' + (f' (where {predicate_text(anchor["where"])})' if anchor.get('where') else '')
        return f'no {then_text} event {low} to {high} days from the {what} event' if negated else text
    clauses = _and_args(expr)
    field = field_label(expr.get('field', ''))
    value = expr.get('value')
    if op == 'within_last_months':
        text = f'{field} within the last {value} months (the {value} months ending at the as-of date)'
    elif op in ('is_null', 'not_null'):
        text = f'{field} {OPS[op]}'
    elif op in OP_WORDS:
        text = f'{field} {OP_WORDS[op]} {value_text(value)}'
    else:
        text = f'{field} {OPS.get(op, op)} {value_text(value)}'
    return f'not ({text})' if negated else text


def _wrap(expr, parent):
    text = predicate_text(expr)
    if isinstance(expr, dict) and expr.get('op') in ('and', 'or') and expr['op'] != parent: return f'({text})'
    return text


def leaves(expr, negated=False, out=None):
    """[(leaf predicate, negated)] of a predicate tree, in order."""
    out = [] if out is None else out
    if not isinstance(expr, dict): return out
    op = expr.get('op')
    if op in ('and', 'or', 'at_least_of'):
        for a in expr['args']: leaves(a, negated, out)
    elif op == 'not':
        leaves(expr['arg'], not negated, out)
    else:
        out.append((expr, negated))
    return out


def _quotes(expr):
    q = expr.get('quote') if isinstance(expr, dict) else None
    return [q] if isinstance(q, str) else [x for x in (q or []) if isinstance(x, str)]


def population_text(spec, question=None):
    from . import decision_context as DC
    table = spec['source']['table']
    subject = spec.get('subject')
    if subject: return f'patient {subject["patient_id"]} (named in the question)'
    noun = TABLE_NOUNS.get(table, words(table) + ' rows')
    if table == 'patient' or 'patient' in noun:
        state = DC.population(question or '') if question is not None else {'living_only': True}
        if state['living_only'] and table == 'patient': return 'living patients'
        if table == 'patient': return 'patients, deceased patients included because the question asks for them'
    return noun


def measure_text(expr, spec):
    kind = expr.get('kind')
    alias = words(expr.get('alias', ''))
    if kind == 'note_answer': return 'the note answer itself (status, details and the supporting quotes)'
    if kind == 'ratio':
        scale = {1: 'a fraction', 100: 'a percent', 1000: 'a rate per 1,000'}.get(expr.get('scale', 1), 'a ratio')
        return f'{alias}: {measure_text(expr["numerator"], spec)} (numerator) out of {measure_text(expr["denominator"], spec)} (denominator), as {scale}'
    field = expr.get('field')
    cond = f', only those with {predicate_text(expr["where"])}' if expr.get('where') else ''
    if kind == 'cohort_group':
        return f'{alias}: the groups ' + _list([f'"{g["label"]}" ({predicate_text(g["where"])})' for g in expr['groups']], 'and')
    if kind == 'sum_related':
        return f'the total {words(expr["field"])} of these patients over {TABLE_NOUNS.get(expr["table"], words(expr["table"]))}{cond}'
    if kind == 'count_related':
        return f'the total number of {TABLE_NOUNS.get(expr["table"], words(expr["table"]))} of these patients{cond}'
    if kind == 'count_distinct' and str(field).endswith('patient_id'): return f'the number of distinct patients{cond}'
    if kind == 'count_distinct': return f'the number of distinct {field_label(field)}{cond}'
    if kind == 'count': return f'the number of {TABLE_NOUNS.get(spec["source"]["table"], "rows")}{cond}'
    if kind == 'field':
        grain = f' (by {expr["grain"]})' if expr.get('grain') else ''
        return 'the patient list' if str(field).endswith('patient_id') else f'{field_label(field)}{grain}'
    if kind in ('sum', 'avg', 'min', 'max'):
        verb = {'sum': 'the total', 'avg': 'the average', 'min': 'the lowest', 'max': 'the highest'}[kind]
        return f'{verb} {field_label(field)}{cond}'
    return alias or str(kind)


def defaults_text(spec, question, as_of):
    """The decision-context defaults this spec used, as short clauses."""
    from . import decision_context as DC
    q = question or ''
    out = []
    state = DC.population(q)
    if not spec.get('subject') and state['living_only'] and not DC.NON_PATIENT_GRAIN.search(q):
        out.append('patients are living patients (no death record) unless the question asks for the deceased')
    elif not spec.get('subject') and not state['living_only'] and DC.asks_deceased(q):
        out.append('the deceased are included because the question asks for them')
    blob = repr(spec)
    if re.search(r"measurement|hba1c|egfr|ldl|lab", blob, re.I) and DC.lab_mode(q) == 'latest':
        out.append('a lab means the latest result per patient and concept')
    if "'concept'" in blob and "'kind': 'condition'" in blob:
        out.append('a condition is an EHR diagnosis or a counted payer-claim diagnosis (diabetes and CKD also count laboratory evidence)')
    if as_of:
        for w in DC.year_window(q, as_of):
            if w['kind'] == 'rolling_12_months':
                start = DC.rolling_year(as_of)[0]
                out.append(f'"{w["phrase"]}" is the 12 months ending {as_of} ({start} to {as_of})')
            else:
                out.append(f'"{w["phrase"]}" is the calendar year {w["start"]} to {w["end"]}')
    return out


def parts(spec, catalog=None, question=None, as_of=None):
    """The checkable parts of a spec, each with the sentence System One checks against the question."""
    from . import decision_context as DC
    if as_of is None and catalog is not None: as_of = (getattr(catalog, 'identity', None) or {}).get('as_of')
    as_of = str(as_of)[:10] if as_of else None
    who = population_text(spec, question)
    who = f'patients, counting only living patients (the standing default)' if who == 'living patients' else who
    out = [{'id': 'population', 'kind': 'population', 'text': f'It answers for {who}.', 'quotes': _quotes(spec['source'])}]
    if spec.get('subject'):
        out.append({'id': 'subject', 'kind': 'subject', 'text': f'It is about one patient, {spec["subject"]["patient_id"]}.', 'quotes': _quotes(spec['subject'])})
    for i, m in enumerate(spec['select']):
        out.append({'id': f'measure.{i}', 'kind': 'measure', 'text': f'It reports {measure_text(m, spec)}.', 'quotes': _quotes(m)})
    for i, (leaf, negated) in enumerate(leaves(spec.get('where'))):
        out.append({'id': f'predicate.{i}', 'kind': 'predicate', 'quotes': _quotes(leaf),
                    'text': f'It keeps only those with {predicate_text(leaf, negated)}.'})
    where = spec.get('where')
    if isinstance(where, dict) and where.get('op') in ('and', 'or', 'not') and (where.get('op') != 'and' or any(isinstance(a, dict) and a.get('op') in ('or', 'not') for a in where.get('args', []))):
        out.append({'id': 'structure', 'kind': 'structure', 'quotes': _quotes(where), 'text': f'It keeps those with {predicate_text(where)}.'})
    if as_of and question:
        for i, w in enumerate(DC.year_window(question, as_of)):
            if w['kind'] == 'rolling_12_months':
                out.append({'id': f'window.{i}', 'kind': 'window', 'quotes': [w['phrase']],
                            'text': f'The time window "{w["phrase"]}" is the 12 months ending {as_of}, from {DC.rolling_year(as_of)[0]} through {as_of}.'})
            else:
                out.append({'id': f'window.{i}', 'kind': 'window', 'quotes': [w['phrase']],
                            'text': f'The time window "{w["phrase"]}" is the calendar year from {w["start"]} through {w["end"]}.'})
    if spec.get('group_by'):
        out.append({'id': 'grouping', 'kind': 'grouping', 'quotes': [q for m in spec['select'] if m.get('alias') in spec['group_by'] for q in _quotes(m)],
                    'text': f'It breaks the result down by {_list([words(g) for g in spec["group_by"]], "and")}.'})
    if spec.get('order_by') or spec.get('limit'):
        order = _list([f'{words(o["alias"])} ({"high to low" if o["direction"] == "desc" else "low to high"})' for o in spec.get('order_by', [])], 'then')
        limit = f'keeps the first {spec["limit"]["value"]}' if spec.get('limit') else ''
        out.append({'id': 'ordering', 'kind': 'ordering', 'quotes': [q for o in spec.get('order_by', []) for q in _quotes(o)] + (_quotes(spec['limit']) if spec.get('limit') else []),
                    'text': 'It ' + ' and '.join(x for x in ((f'orders by {order}' if order else ''), limit) if x) + '.'})
    for i, a in enumerate(spec.get('analyses') or []):
        out.append({'id': f'analysis.{i}', 'kind': 'analysis', 'quotes': _quotes(a),
                    'text': f'It runs the {words(a["operation"])} analysis ({a["parameters"].get("id") or ", ".join(f"{k}={v}" for k, v in list(a["parameters"].items())[:4])}) on that population.'})
    return out


def read_back(spec, catalog=None, question=None, as_of=None):
    """One plain-English sentence for the whole spec, with the decision-context defaults it applied."""
    if as_of is None and catalog is not None: as_of = (getattr(catalog, 'identity', None) or {}).get('as_of')
    as_of = str(as_of)[:10] if as_of else None
    select = spec['select']
    measures = _list([measure_text(m, spec) for m in select], 'and')
    verb = 'Read' if select and select[0].get('kind') == 'note_answer' else 'Reported'
    if len(select) == 1 and select[0].get('kind') in ('count', 'count_distinct') and not spec.get('group_by'): verb = 'Counted'
    if len(select) == 1 and select[0].get('kind') == 'field' and str(select[0].get('field', '')).endswith('patient_id') and not spec.get('group_by'): verb = 'Listed'
    cond = ''
    where = spec.get('where')
    if isinstance(where, dict): cond = ' with ' + predicate_text(where)
    if verb == 'Counted' and select[0].get('kind') == 'count_distinct' and str(select[0].get('field', '')).endswith('patient_id'):
        sentence = f'Counted {population_text(spec, question)}' + (cond if cond else '')
    elif verb == 'Listed':
        sentence = f'Listed {population_text(spec, question)}' + cond
    else:
        sentence = f'{verb} {measures} for {population_text(spec, question)}' + cond
    if spec.get('group_by'): sentence += f', broken down by {_list([words(g) for g in spec["group_by"]], "and")}'
    if spec.get('order_by'):
        sentence += ', ordered by ' + _list([f'{words(o["alias"])} {"descending" if o["direction"] == "desc" else "ascending"}' for o in spec['order_by']], 'then')
    if spec.get('limit'): sentence += f', keeping the first {spec["limit"]["value"]}'
    for a in spec.get('analyses') or []:
        sentence += f'; then ran the {words(a["operation"])} analysis'
    sentence += '.'
    defaults = defaults_text(spec, question, as_of)
    if defaults: sentence += ' Defaults applied: ' + '; '.join(defaults) + '.'
    open_items = [u.get('quote') for u in spec.get('unresolved') or [] if isinstance(u, dict)]
    if open_items: sentence += ' Not answered: ' + '; '.join(map(str, [q if isinstance(q, str) else ' '.join(q) for q in open_items])) + '.'
    return sentence
