"""Tier 1: deterministic recipes over the semantic layer, their typed parameters, and validation.

A recipe is plain SQL with named parameters. Models never compute a number: they may only choose a
recipe, fill its parameters, or (Tier 4) write a new recipe that this module then executes and validates.
"""
import re

from . import store

KINDS = {'ed_visit': ('ed visits', 'emergency department visits', 'emergency visits', 'er visits'),
         'inpatient': ('inpatient admissions', 'inpatient stays', 'hospital admissions', 'hospitalizations', 'inpatient discharges'),
         'observation': ('observation stays', 'observation visits')}
PATIENT_GROUPS = {'sex_reported': ('sex', 'gender'), 'coverage_type': ('coverage type', 'insurance type', 'coverage')}
YEAR = r'(?P<year>20\d{2})'
MONTH = r'(?P<month>20\d{2}-\d{2})'


def _alt(mapping):
    return '(?P<%s>' + '|'.join(sorted((re.escape(s) for names in mapping.values() for s in names), key=len, reverse=True)) + ')'


def _key(mapping, text):
    text = ' '.join(str(text).lower().split())
    return next((k for k, names in mapping.items() if text == k or text in names), None)


RECIPES = {
    'utilization_count': {
        'title': 'Number of utilization events of one kind in one calendar year',
        'params': {'kind': {'type': 'enum', 'values': list(KINDS)}, 'year': {'type': 'year'}},
        'sql': "SELECT COUNT(*) AS events FROM utilization_event WHERE kind = :kind AND substr(event_date, 1, 4) = :year",
        'patterns': [r'how many ' + _alt(KINDS) % 'kind' + r' (?:were there|occurred|happened) in ' + YEAR]},
    'utilization_by_month': {
        'title': 'Monthly count of utilization events of one kind in one calendar year',
        'params': {'kind': {'type': 'enum', 'values': list(KINDS)}, 'year': {'type': 'year'}},
        'sql': "SELECT substr(event_date, 1, 7) AS month, COUNT(*) AS events FROM utilization_event "
               "WHERE kind = :kind AND substr(event_date, 1, 4) = :year GROUP BY 1 ORDER BY 1",
        'grain': ['month'], 'total': {'column': 'events', 'recipe': 'utilization_count'},
        'patterns': [r'(?:show|list) (?:the )?monthly ' + _alt(KINDS) % 'kind' + r' (?:in|for|during) ' + YEAR]},
    'stays_by_organization': {
        'title': 'Inpatient stays admitted in one year by organization, with received and absent discharge dates',
        'params': {'year': {'type': 'year'}},
        'sql': "SELECT organization_id, COUNT(*) AS stays, SUM(discharge_known) AS discharge_received, "
               "COUNT(*) - SUM(discharge_known) AS discharge_absent FROM stay WHERE type = 'inpatient' "
               "AND substr(admit_date, 1, 4) = :year GROUP BY 1 ORDER BY 1",
        'grain': ['organization_id'],
        'patterns': [r'(?:show|list) inpatient stays admitted in ' + YEAR + r' by organization']},
    'readmission_rate': {
        'title': '30-day readmission count and rate for inpatient stays discharged in one year',
        'params': {'year': {'type': 'year'}},
        'sql': "SELECT COUNT(*) AS stays, SUM(readmitted_30d) AS readmitted_30d, ROUND(1.0 * SUM(readmitted_30d) / COUNT(*), 4) AS rate "
               "FROM stay WHERE type = 'inpatient' AND discharge_known = 1 AND substr(discharge_date, 1, 4) = :year",
        'patterns': [r'what was the 30[- ]day readmission rate (?:in|for) ' + YEAR]},
    'appointments_by_status': {
        'title': 'Appointments scheduled in one year by final status',
        'params': {'year': {'type': 'year'}},
        'sql': "SELECT final_status, COUNT(*) AS appointments FROM appointment WHERE substr(scheduled_date, 1, 4) = :year GROUP BY 1 ORDER BY 1",
        'grain': ['final_status'],
        'patterns': [r'(?:show|list) appointments (?:scheduled )?in ' + YEAR + r' by (?:final )?status']},
    'patients_with_condition': {
        'title': 'Number of patients with recorded evidence of one condition concept',
        'params': {'concept': {'type': 'vocabulary', 'query': "SELECT DISTINCT concept FROM concept_patient WHERE kind = 'condition'"}},
        'sql': "SELECT COUNT(DISTINCT patient_id) AS patients FROM concept_patient WHERE kind = 'condition' AND concept = :concept",
        'patterns': [r'how many patients have (?:recorded )?(?P<concept>[a-z0-9 _-]+?)(?: evidence)?\??$']},
    'top_conditions': {
        'title': 'The most common condition concepts by number of patients',
        'params': {'n': {'type': 'int', 'min': 1, 'max': 50}},
        'sql': "SELECT concept, COUNT(DISTINCT patient_id) AS patients FROM concept_patient WHERE kind = 'condition' "
               "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT :n",
        'grain': ['concept'],
        'patterns': [r'what are the (?P<n>\d+) most common conditions']},
    'fills_by_class': {
        'title': 'Medication fills and distinct patients by drug class in one year',
        'params': {'year': {'type': 'year'}},
        'sql': "SELECT drug_class, COUNT(*) AS fills, COUNT(DISTINCT patient_id) AS patients FROM fill "
               "WHERE substr(fill_date, 1, 4) = :year AND drug_class IS NOT NULL GROUP BY 1 ORDER BY 1",
        'grain': ['drug_class'],
        'patterns': [r'(?:show|list) medication fills in ' + YEAR + r' by drug class']},
    'fills_for_class': {
        'title': 'Fills and distinct patients for one drug class in one year',
        'params': {'drug_class': {'type': 'vocabulary', 'query': "SELECT DISTINCT drug_class FROM fill WHERE drug_class IS NOT NULL"},
                   'year': {'type': 'year'}},
        'sql': "SELECT COUNT(*) AS fills, COUNT(DISTINCT patient_id) AS patients FROM fill WHERE drug_class = :drug_class "
               "AND substr(fill_date, 1, 4) = :year",
        'patterns': [r'how many (?P<drug_class>[a-z0-9 _-]+?) fills were there in ' + YEAR]},
    'claims_by_payer': {
        'title': 'Claims, billed, allowed and net payer-paid cents by payer for services in one year',
        'params': {'year': {'type': 'year'}},
        'sql': "SELECT payer, COUNT(*) AS claims, SUM(billed_cents) AS billed_cents, SUM(allowed_cents) AS allowed_cents, "
               "SUM(net_payer_paid_cents) AS net_payer_paid_cents FROM claim WHERE substr(service_date, 1, 4) = :year GROUP BY 1 ORDER BY 1",
        'grain': ['payer'],
        'patterns': [r'(?:show|list) claims (?:and paid amounts )?by payer for services in ' + YEAR]},
    'denial_rate_by_payer': {
        'title': 'Share of claims ever denied by payer for services in one year',
        'params': {'year': {'type': 'year'}},
        'sql': "SELECT payer, COUNT(*) AS claims, SUM(ever_denied) AS ever_denied, ROUND(1.0 * SUM(ever_denied) / COUNT(*), 4) AS denial_rate "
               "FROM claim WHERE substr(service_date, 1, 4) = :year GROUP BY 1 ORDER BY 1",
        'grain': ['payer'],
        'patterns': [r'what was the claim denial rate by payer in ' + YEAR]},
    'members_by_payer': {
        'title': 'Enrolled members by payer in one month',
        'params': {'month': {'type': 'month'}},
        'sql': "SELECT payer_id, COUNT(DISTINCT patient_id) AS members FROM member_month WHERE month = :month GROUP BY 1 ORDER BY 1",
        'grain': ['payer_id'],
        'patterns': [r'how many members did each payer have in ' + MONTH]},
    'deaths_in_year': {
        'title': 'Number of recorded deaths in one year',
        'params': {'year': {'type': 'year'}},
        'sql': "SELECT COUNT(*) AS deaths FROM death WHERE substr(death_date, 1, 4) = :year",
        'patterns': [r'how many (?:recorded )?deaths (?:were there|occurred) in ' + YEAR]},
    'patients_by_group': {
        'title': 'Number of patients by reported sex or by coverage type',
        'params': {'group': {'type': 'identifier', 'values': list(PATIENT_GROUPS)}},
        'sql': "SELECT {group} AS grp, COUNT(*) AS patients FROM patient GROUP BY 1 ORDER BY 1",
        'grain': ['grp'],
        'patterns': [r'how many patients are there by ' + _alt(PATIENT_GROUPS) % 'group']},
    'scanned_documents_stating': {
        'title': 'Number of scanned documents of one document type, dated in one month, whose text states a given thing (each document is read)',
        'params': {'document_type': {'type': 'vocabulary', 'query': "SELECT DISTINCT document_type FROM scanned_document WHERE readable = 1"},
                   'month': {'type': 'month'}, 'statement': {'type': 'text'}},
        'sql': "SELECT record_id, event_date FROM document WHERE source = 'scanned_document' AND document_type = :document_type "
               "AND substr(event_date, 1, 7) = :month ORDER BY record_id",
        'read': {'statement': 'The document states or reports {statement}.', 'mode': 'count'},
        'columns': ['documents_read', 'documents_stating', 'documents_not_stating', 'documents_undecided'],
        'patterns': [r'how many scanned (?P<document_type>[a-z /-]+?)s? dated in ' + MONTH +
                     r' (?:state|mention|report|describe|say|show|document) (?:that )?(?P<statement>.{3,200})']},
    'scanned_documents_stating_in_year': {
        'title': 'Number of scanned documents of one document type, dated in one calendar year, whose text states a given thing (each document is read)',
        'params': {'document_type': {'type': 'vocabulary', 'query': "SELECT DISTINCT document_type FROM scanned_document WHERE readable = 1"},
                   'year': {'type': 'year'}, 'statement': {'type': 'text'}},
        'sql': "SELECT record_id, event_date FROM document WHERE source = 'scanned_document' AND document_type = :document_type "
               "AND substr(event_date, 1, 4) = :year ORDER BY record_id",
        'read': {'statement': 'The document states or reports {statement}.', 'mode': 'count'},
        'columns': ['documents_read', 'documents_stating', 'documents_not_stating', 'documents_undecided'],
        'patterns': [r'how many scanned (?P<document_type>[a-z /-]+?)s? dated in ' + YEAR +
                     r' (?:state|mention|report|describe|say|show|document) (?:that )?(?P<statement>.{3,200})']},
    'hie_discharge_summary_value': {
        'title': 'For one patient, one stated value read out of each exchange (HIE) discharge summary, one row per summary',
        'params': {'patient_id': {'type': 'patient'}, 'value': {'type': 'text'}},
        'sql': "SELECT record_id, event_date FROM document WHERE source = 'hie_document' AND document_type = 'Discharge summary' "
               "AND patient_id = :patient_id ORDER BY event_date, record_id",
        'read': {'statement': 'The discharge summary gives {value}.', 'value': '{value}', 'mode': 'extract'},
        'columns': ['record_id', 'event_date', 'value'],
        'patterns': [r"what (?P<value>.{3,80}?) does each of patient (?P<patient_id>[a-z0-9-]+)'s hie discharge summaries (?:give|state|report|list)"]},
    # --- one patient's guideline heart-failure therapy: what the discrete fields show, and what the notes say ---
    'hf_pillar_fill_status': {
        'title': 'For one patient, pharmacy fill status of each guideline heart-failure medication pillar (discrete fill records)',
        'params': {'patient_id': {'type': 'patient'}},
        'sql': "WITH pillar(pillar, drug_class) AS (VALUES ('RAAS inhibitor (ACE inhibitor, ARB or ARNI)', 'ace_inhibitor'), "
               "('RAAS inhibitor (ACE inhibitor, ARB or ARNI)', 'arb'), ('RAAS inhibitor (ACE inhibitor, ARB or ARNI)', 'arni'), "
               "('Evidence-based beta blocker', 'hf_evidence_based_beta_blocker'), ('MRA', 'mra'), ('SGLT2 inhibitor', 'sglt2_inhibitor')), "
               "clock(as_of) AS (SELECT substr(value, 1, 10) FROM meta WHERE key = 'as_of') "
               "SELECT p.pillar, COUNT(f.fill_id) AS fills_on_record, COALESCE(MAX(f.fill_date), 'no fill on record') AS last_fill_date, "
               "COALESCE(CAST(julianday((SELECT as_of FROM clock)) - julianday(MAX(f.fill_date)) AS INTEGER), -1) AS days_since_last_fill, "
               "CASE WHEN MAX(f.fill_date) IS NULL THEN 'no fill on record' "
               "WHEN julianday((SELECT as_of FROM clock)) - julianday(MAX(f.fill_date)) <= 120 THEN 'filled in the last 120 days' "
               "ELSE 'no fill in the last 120 days' END AS status "
               "FROM pillar p LEFT JOIN fill f ON f.drug_class = p.drug_class AND f.patient_id = :patient_id GROUP BY p.pillar ORDER BY p.pillar",
        'grain': ['pillar'],
        'patterns': [r"what is patient (?P<patient_id>[a-z0-9-]+)'s fill status for each guideline heart failure medication pillar"]},
    'hf_safety_labs': {
        'title': 'For one patient, the latest potassium, eGFR, creatinine, systolic blood pressure and heart rate (discrete measurements)',
        'params': {'patient_id': {'type': 'patient'}},
        'sql': "SELECT concept, value_canonical AS value, unit_canonical AS unit, event_date FROM measurement_latest "
               "WHERE patient_id = :patient_id AND concept IN ('potassium', 'egfr', 'creatinine', 'systolic_bp', 'heart_rate') ORDER BY concept",
        'grain': ['concept'],
        'patterns': [r"what are patient (?P<patient_id>[a-z0-9-]+)'s latest potassium, egfr, creatinine, blood pressure and heart rate"]},
    'hf_medication_reasons_in_notes': {
        'title': 'For one patient, the reason each document states for a heart-failure medication not being taken, held, stopped or not started (each document is read)',
        'params': {'patient_id': {'type': 'patient'}},
        'sql': "SELECT record_id, event_date, source FROM document WHERE patient_id = :patient_id ORDER BY event_date, record_id",
        'read': {'statement': 'The document states a reason why a heart failure medication (a beta blocker, ACE inhibitor, ARB, ARNI, MRA or SGLT2 inhibitor) '
                              'is not being taken, was held, was stopped or was not started.',
                 'value': 'the stated reason, together with the medication it is about', 'mode': 'extract'},
        'columns': ['record_id', 'event_date', 'source', 'value'],
        'patterns': [r"what reason does each of patient (?P<patient_id>[a-z0-9-]+)'s documents give for a heart failure medication not being taken"]},
}
ENUM_WORDS = {'kind': KINDS, 'group': PATIENT_GROUPS}
_vocab = {}


def vocabulary(spec, db=None):
    if spec['query'] not in _vocab:
        _vocab[spec['query']] = sorted(r[0] for r in store.run(spec['query'], db=db)[1])
    return _vocab[spec['query']]


SECOND_QUESTION = re.compile(r'(?:[,;?]|\band\b|\balso\b|\bplus\b)\s*(?:and\s+|also\s+|then\s+)?(?:how many|how much|what|which|who|show|list|count|give|tell)\b', re.I)


def narrowed_vocabulary(question, params, db=None):
    """A recipe value that is a narrower vocabulary value than the question's own word ("type_2_diabetes" when the
    question says "diabetes" and diabetes is itself a value). -> a message, or None."""
    words = ' ' + ' '.join(re.findall(r'[a-z0-9]+', question.lower())) + ' '
    for query in ("SELECT DISTINCT concept FROM concept_patient WHERE kind = 'condition'", 'SELECT DISTINCT drug_class FROM fill WHERE drug_class IS NOT NULL'):
        values = {str(r[0]).lower() for r in store.run(query, None, db)[1]}
        for value in params.values():
            used = str(value).lower()
            if used not in values or f" {used.replace('_', ' ')} " in words: continue
            said = [v for v in values if v != used and re.search(rf'(?:^|_){re.escape(v)}(?:_|$)', used) and f" {v.replace('_', ' ')} " in words]
            if said: return f'The question says "{said[0]}", which is an exact vocabulary value; use it, not the narrower "{used}"'
    return None


def normalize(recipe_id, raw, db=None):
    """Typed parameters or None. Every value is checked against its type; nothing is guessed."""
    out = {}
    for name, spec in RECIPES[recipe_id]['params'].items():
        value = raw.get(name)
        if value is None: return None
        text = ' '.join(str(value).lower().replace('_', ' ').split())
        kind = spec['type']
        if kind == 'year':
            if not re.fullmatch(r'20\d{2}', text): return None
            out[name] = text
        elif kind == 'month':
            if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', text): return None
            out[name] = text
        elif kind == 'int':
            if not text.isdigit() or not spec['min'] <= int(text) <= spec['max']: return None
            out[name] = int(text)
        elif kind in ('enum', 'identifier'):
            key = _key(ENUM_WORDS[name], text) or (str(value) if str(value) in spec['values'] else None)
            if key not in spec['values']: return None
            out[name] = key
        elif kind == 'vocabulary':
            words = {v.replace('_', ' ').lower(): v for v in vocabulary(spec, db)}
            if text not in words: return None
            out[name] = words[text]
        elif kind == 'patient':  # an identifier that exists, returned as stored; never a near match
            found = store.run('SELECT patient_id FROM document WHERE lower(patient_id) = lower(:p) LIMIT 1', {'p': str(value).strip()}, db)[1]
            if not found: return None
            out[name] = found[0][0]
        elif kind == 'text':  # free words from the question, kept as written; used only as a reading statement
            words = ' '.join(str(value).split()).rstrip('?.')
            if not 3 <= len(words) <= 200: return None
            if SECOND_QUESTION.search(words): return None  # the words run on into another question: not one statement
            out[name] = words
        else: return None
    return out


def match(question, db=None):
    """Deterministic pattern match: (recipe_id, params) or None. No model."""
    text = ' '.join(question.lower().strip().rstrip('?.').split())
    for recipe_id, recipe in RECIPES.items():
        for pattern in recipe['patterns']:
            m = re.fullmatch(pattern, text) or re.fullmatch(pattern, text + '?')
            if m:
                params = normalize(recipe_id, m.groupdict(), db)
                if params is not None: return recipe_id, params
    return None


def execute(recipe_id, params, db=None):
    recipe = RECIPES[recipe_id]
    sql = recipe['sql']
    bound = dict(params)
    for name, spec in recipe['params'].items():
        if spec['type'] == 'identifier':  # a whitelisted column name; never free text
            if bound[name] not in spec['values']: raise store.RecipeError('Unknown column')
            sql = sql.replace('{' + name + '}', bound.pop(name))
    used = set(re.findall(r'(?<![:\w]):([a-z_][a-z0-9_]*)', sql))
    columns, rows = store.run(sql, {k: v for k, v in bound.items() if k in used}, db)
    return sql, columns, rows


def validate(question, sql, params, columns, rows, *, grain=None, total=None, db=None):
    """Deterministic checks on an executed answer. -> {'ok', 'checks': [{'name','ok','detail'}]}."""
    checks = []
    def add(name, ok, detail=''): checks.append({'name': name, 'ok': bool(ok), 'detail': detail})
    add('rows_returned', len(rows) > 0, f'{len(rows)} rows')
    add('row_shape', all(len(r) == len(columns) for r in rows) and len(set(columns)) == len(columns), f'{len(columns)} columns')
    empty = [c for i, c in enumerate(columns) if rows and all(r[i] is None for r in rows)]
    add('no_all_null_column', not empty, ', '.join(empty))
    mixed = [c for i, c in enumerate(columns) if len({type(r[i]).__name__ for r in rows if r[i] is not None} - {'int', 'float'}) > 1
             or {'str'} < {type(r[i]).__name__ for r in rows if r[i] is not None}]
    add('column_types_consistent', not mixed, ', '.join(mixed))
    if grain:
        missing = [g for g in grain if g not in columns]
        keys = [tuple(r[columns.index(g)] for g in grain) for r in rows] if not missing else []
        add('grain_unique', not missing and len(keys) == len(set(keys)), f"grain {grain}" + (f' missing {missing}' if missing else ''))
    wanted = set(re.findall(r'(?<![\d-])20\d{2}(?:-\d{2})?(?![\d])', question))
    seen = sql + ' ' + ' '.join(str(v) for v in (params or {}).values())
    absent = sorted(w for w in wanted if w not in seen and w[:4] not in seen)
    add('time_window_bound', not absent, 'question periods not in the recipe: ' + ', '.join(absent) if absent else ', '.join(sorted(wanted)))
    if total and total['column'] in columns:
        _, _, ref = execute(total['recipe'], {k: params[k] for k in RECIPES[total['recipe']]['params']}, db)
        got = sum(r[columns.index(total['column'])] or 0 for r in rows)
        add('total_reconciles', ref and got == ref[0][0], f"sum {got} vs {total['recipe']} {ref[0][0] if ref else None}")
    return {'ok': all(c['ok'] for c in checks), 'checks': checks}
