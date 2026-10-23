"""One decision context for every path that answers a question (Mike, 2026-10-23).

The definitions an answer silently depends on are decided once, here, as data, and every path reads them from here: the grammar and
the local compiler, the governed and generated metrics, the engines, the cohort and specialist families, the atomic questions and the
Claude / System One planners (told in their prompts, checked in their SQL).

  patients        LIVING patients: no row in the semantic `death` table. The deceased are included only when the question asks
                  ("including deceased", "who died", "deceased", "death", "mortality", "all patients ever", ...).
  labs            the LATEST result (measurement_latest: one row per patient and concept). "ever / any / at any time / history of /
                  highest / lowest / trend" means any result (measurement).
  conditions      ONE definition of condition evidence everywhere: an EHR diagnosis (condition rows with is_diagnosis = 1, plus
                  problem-list rows whose flag is unknown) or a counted payer-claim diagnosis (payer_claim_diagnosis.counts_in_metrics = 1);
                  diabetes and CKD also count laboratory evidence (Mike 2026-10-21); rule-outs stay out; claims-only members count.
                  The governed diagnosis_any relation is the same evidence read by ICD-10 code (semantic_layer.DIAGNOSIS_ANY_VERSION 2).
  events          ED visits, inpatient stays and observation stays are the all-source utilization_event / stay relations, never the
                  EHR-only visit / encounter tables.
  windows         "last year" / "past year" / "last 12 months" = the 12 months ending at the as-of date, date(as_of, '-12 months') <
                  date <= as_of; a calendar year word ("in 2029") = that calendar year.

Enforcement, not advice: compose() opens a scope (scope()); sql_query.execute then rewrites every patient-grain query inside it so the
living filter and latest labs are in the SQL that runs (enforce_sql), analysis_graph restricts the universe and the cohort atoms
(enforce_plan, living_filter), and answer_checks runs the mandatory check `decision_context` (verify). Every answer carries
answer['decision_context'] (receipt()). A question that explicitly asks for something else (the deceased, any lab result) is never
rewritten.
"""
import contextlib
import contextvars
import re
import threading
from pathlib import Path

VERSION = 'decision-context/1'
DECIDED_BY = 'Mike (clinical owner), 2026-10-23'

# --- patients ------------------------------------------------------------------------------------------------------------------
DECEASED_ASK = re.compile(
    r"\b(?:deceased|died|dead|death|deaths|dying|die|dies|mortality|passed away|fatal(?:ity|ities)?|survival|survivors?|vital status|"
    r"all patients ever|ever[- ]enrolled|everyone ever|including (?:the )?(?:deceased|dead|death))\b", re.I)
LIVING_ASK = re.compile(r'\b(?:living|alive)\b', re.I)
PATIENT_WORDS = re.compile(r'\b(?:patients?|people|persons?|individuals?|who|whom|whose|adults?|children|women|men|females?|males?|seniors?|elderly)\b', re.I)
# questions about money or membership months are not patient counts: the living rule does not apply to them (they say "members",
# "claims", "paid", "PMPM", never a count of patients)
NON_PATIENT_GRAIN = re.compile(r'\b(?:member[- ]months?|pmpm|per member per month|claims?|paid|allowed|billed|denials?|denied|premiums?|'
                               r'revenue|reimbursements?|spend|spending|costs?|payments?|dollars?|cents?)\b', re.I)
LIVING_PREDICATE = 'NOT IN (SELECT patient_id FROM death)'

# --- labs ----------------------------------------------------------------------------------------------------------------------
ANY_LAB = re.compile(r"\b(?:ever|at any (?:time|point|visit)|any (?:time|result|value|reading|draw|measurement)|history of|had a history|"
                     r"at least (?:once|twice|two|2|\d+)|twice|two or more|more than once|repeated|consecutive|once|highest|lowest|peak|maximum|"
                     r"minimum|max|min|worst|best|trend(?:s|ing)?|over time|on any draw|every result|all results)\b", re.I)

# --- windows -------------------------------------------------------------------------------------------------------------------
ROLLING_YEAR = re.compile(r"\b(?:in |during |within |over |for )?the (?:last|past|previous|trailing) (?:year|12 months|twelve months|365 days)\b|"
                          r"\b(?:last|past) year\b|\bin the last 12 months\b|\btrailing 12 months\b", re.I)
CALENDAR_YEAR = re.compile(r"\b(?:in|during|for|of)\s+((?:19|20)\d\d)\b", re.I)
ROLLING_YEAR_SQL = "date({as_of}, '-12 months')"  # an event date d is in the window when d > ROLLING_YEAR_SQL and d <= {as_of}

# --- conditions ----------------------------------------------------------------------------------------------------------------
CONDITION_EVIDENCE = {
    'ehr_diagnosis': 'condition rows with is_diagnosis = 1, plus problem-list rows (diagnosis_basis = ehr_problem_list) whose flag is unknown',
    'payer_claim_diagnosis': 'payer_claim_diagnosis rows with counts_in_metrics = 1 whose ICD-10 is in the condition\'s code set '
                             '(claims-only members count)',
    'laboratory_evidence': 'diabetes and CKD only: condition rows whose basis is laboratory evidence (Mike 2026-10-21)',
    'excluded': 'rule-out and assessment-topic rows; lab-inferred rows of every other condition; note-only and medication-indication evidence',
    'by_code': 'diagnosis_any = the same evidence by ICD-10 code (EHR condition rows, counted payer-claim diagnoses, laboratory-evidence '
               'rows of diabetes and CKD); count DISTINCT patient_id',
}
LAB_EVIDENCE_CONDITIONS = ('diabetes', 'type_1_diabetes', 'type_2_diabetes', 'chronic_kidney_disease')

# --- events --------------------------------------------------------------------------------------------------------------------
EVENT_RELATIONS = {
    'ed_visit': "utilization_event WHERE kind = 'ed_visit'", 'inpatient_stay': "utilization_event WHERE kind = 'inpatient' (or stay)",
    'observation_stay': "utilization_event WHERE kind = 'observation'",
}
EHR_ONLY_EVENT_TABLES = ('visit', 'encounter')
EHR_ONLY_EVENT_VALUES = re.compile(r"\b(?:inpatient|emergency|observation|ed|er)\b", re.I)

# patient-keyed relations whose rows must be restricted to living patients inside a patient-grain query. death itself and the raw
# record stores are never rewritten.
PATIENT_TABLES = frozenset('''adt_visit allergy_entry appointment cancer_screening claim claim_adjudication claim_adjustment claim_line
clinical_note concept_patient condition diagnosis_any encounter encounter_diagnosis enrollment_span fill hie_document immunization
incident_diagnosis measurement measurement_latest medication medication_list_entry member_month note_fact note_therapy_change patient
patient_svi payer_adjudication payer_claim payer_claim_diagnosis payer_claim_line payer_pharmacy_claim pharmacy_claim_event prescription
problem_list_entry procedure_line returned_fill smoking_status social_need stay therapy_barrier therapy_barrier_candidate
utilization_event visit visit_group wellness_visit'''.split())


_COHORT_WORDS = re.compile(r'\b(?:patients|members|people|persons|cohort|population|everyone|similar|like|lookalikes?)\b', re.I)


def NAMED_PATIENT_QUESTION(question):
    """A question about ONE named patient (an id of the warehouse's formats, no cohort wording): the living default does not apply."""
    from .patient_ids import names_a_patient
    return names_a_patient(question) and not _COHORT_WORDS.search(question or '')


def asks_deceased(question):
    return bool(DECEASED_ASK.search(question or ''))


def population(question):
    """-> {'living_only': bool, 'reason': str}. Patients are living patients unless the question asks for the deceased."""
    q = question or ''
    if asks_deceased(q):
        return {'living_only': False, 'reason': 'the question asks for the deceased (or death / mortality / all patients ever)'}
    if NAMED_PATIENT_QUESTION(q):
        return {'living_only': False, 'reason': 'a named patient is never excluded for being deceased (a deceased patient\'s answer says so)'}
    if NON_PATIENT_GRAIN.search(q) and not PATIENT_WORDS.search(q):
        return {'living_only': False, 'reason': 'a membership or money question, not a count of patients'}
    if LIVING_ASK.search(q):
        return {'living_only': True, 'reason': 'the question asks for living patients'}
    return {'living_only': True, 'reason': 'patients are living patients by default (no row in death)'}


def lab_mode(question, concept=None):
    """'latest' (measurement_latest) unless the question says ever / any / at any time / history of / highest / lowest."""
    return 'any' if ANY_LAB.search(question or '') else 'latest'


def add_months(day, months):
    """date(day, '+N months') as SQLite computes it (a day that does not exist rolls forward)."""
    import calendar
    from datetime import date, timedelta
    d = date.fromisoformat(str(day)[:10])
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last)) if d.day <= last else date(year, month, last) + timedelta(days=d.day - last)


def rolling_year(as_of):
    """The 12 months ending at as_of as inclusive ISO dates (first day, as_of): the day after date(as_of, '-12 months') through as_of."""
    from datetime import timedelta
    return (add_months(as_of, -12) + timedelta(days=1)).isoformat(), str(as_of)[:10]


def year_window(question, as_of):
    """The time windows a question names: [{'phrase', 'kind', 'start', 'end', 'rule'}]. Start is exclusive for the rolling year
    (date > start) and inclusive for a calendar year (date >= start); end is inclusive."""
    out = []
    for m in ROLLING_YEAR.finditer(question or ''):
        start = add_months(as_of, -12).isoformat()
        out.append({'phrase': m.group(), 'kind': 'rolling_12_months', 'start': start, 'end': str(as_of)[:10],
                    'rule': f"date > date(as_of, '-12 months') AND date <= as_of"})
    for m in CALENDAR_YEAR.finditer(question or ''):
        out.append({'phrase': m.group(), 'kind': 'calendar_year', 'start': f'{m[1]}-01-01', 'end': f'{m[1]}-12-31',
                    'rule': f'date >= {m[1]}-01-01 AND date <= {m[1]}-12-31'})
    return out


def condition_evidence_sql(names, tables):
    """The one governed condition-evidence subquery (patient_id, event_date, source); see concepts.condition_evidence_sql."""
    from .concepts import condition_evidence_sql as governed
    return governed(names, tables)


def prompt_block():
    """The definitions as a short block for every LLM planner prompt (hosted typed interpreter, hosted SQL, analysts, routers)."""
    return (
        f'DECISION CONTEXT ({VERSION}; decided by {DECIDED_BY}). These definitions hold for every answer; apply them unless the question says otherwise.\n'
        '1. Patients are LIVING patients. Exclude anyone with a row in the death table: add patient_id NOT IN (SELECT patient_id FROM death) '
        'to every patient-keyed table a patient count or list reads. Include the deceased only when the question asks (deceased, died, death, '
        'mortality, "including deceased", "all patients ever").\n'
        '2. Labs: the LATEST result per patient and concept (measurement_latest). Use measurement (every result) only when the question says ever, '
        'any, at any time, history of, at least once, highest, lowest or asks for a trend.\n'
        '3. A condition is documented by ONE evidence rule: an EHR diagnosis (condition with is_diagnosis = 1, plus problem-list rows whose flag is '
        'unknown) or a counted payer-claim diagnosis (payer_claim_diagnosis.counts_in_metrics = 1); diabetes and CKD also count laboratory-evidence '
        'condition rows; rule-outs never count. By ICD-10 code use diagnosis_any (the same evidence); by library concept use concept_patient. '
        'Claims-only members count.\n'
        "4. ED visits, inpatient stays and observation stays are utilization_event rows (kind ed_visit | inpatient | observation: EHR, ADT and claims "
        'together), never visit or encounter alone.\n'
        "5. 'last year', 'past year', 'last 12 months' = the 12 months ending at the as-of date: date > date(:as_of, '-12 months') AND date <= :as_of. "
        "A calendar year word ('in 2029') is that calendar year (date >= '2029-01-01' AND date <= '2029-12-31').")


# --- the scope an answer runs in -------------------------------------------------------------------------------------------------
_SCOPE = contextvars.ContextVar('agere_decision_context', default=None)


class Scope:
    """What one question decided, and what was enforced while it ran (shared by the threads of the question)."""

    def __init__(self, question, as_of=None):
        self.question = question or ''
        self.as_of = as_of
        self.population = population(self.question)
        self.lab_mode = lab_mode(self.question)
        self.windows = year_window(self.question, as_of) if as_of else []
        self.rewrites = []
        self.problems = []
        self.checked = 0
        self.lock = threading.Lock()

    @property
    def living_only(self):
        return self.population['living_only']

    def note(self, kind, detail):
        with self.lock:
            entry = {'kind': kind, **detail}
            if entry not in self.rewrites: self.rewrites.append(entry)

    def problem(self, text):
        with self.lock:
            if text not in self.problems: self.problems.append(text)

    def reset_run(self):
        """A proposal was checked: what it rewrote or could not restrict does not describe the next proposal."""
        with self.lock:
            self.rewrites, self.problems = [], []


@contextlib.contextmanager
def scope(question, as_of=None):
    """Run a block under the decision context of one question (shared_planner.compose). Re-entrant: an inner scope for the same
    question reuses the outer one, so a follow-up that composes again does not reset the receipt."""
    current = _SCOPE.get()
    if current is not None and current.question == (question or ''):
        yield current
        return
    state = Scope(question, as_of)
    token = _SCOPE.set(state)
    try:
        yield state
    finally:
        _SCOPE.reset(token)


def current():
    return _SCOPE.get()


def copy_context_call(fn, *args, **kwargs):
    """Submit-able wrapper: run fn in a copy of the caller's context so a worker thread keeps the question's scope."""
    return contextvars.copy_context().run(fn, *args, **kwargs)


# --- SQL enforcement -------------------------------------------------------------------------------------------------------------
_KEYWORDS = frozenset('''where on join left right inner outer cross full natural group order limit union except intersect having using
select window set values returning offset'''.split())
_FROM = re.compile(r'(?is)\b(FROM|JOIN)(\s+)("?)([A-Za-z_]\w*)\3(?=[\s,;)]|$)(?:(\s+)(AS\s+)?([A-Za-z_]\w*))?')
_COUNT_PATIENTS = re.compile(r'(?is)\bcount\s*\(\s*distinct\b[^;]{0,200}?patient_id')
_SELECT_PATIENT = re.compile(r'(?is)^\s*(?:with\b.*?\)\s*)?select\s+(?:distinct\s+)?(?:\w+\.)?"?patient_id"?\s*(?:,|\bfrom\b|as\b)')
_COUNT_ROWS_OF_PATIENT = re.compile(r'(?is)\bselect\s+count\s*\(\s*(?:\*|1|[\w."]*patient_id)\s*\)[^;]*?\bfrom\s+"?patient"?(?=[\s,;)]|$)')


def _mask(sql):
    """The SQL with string literals and comments replaced by placeholders (restored by _unmask), so a table name inside either is never
    rewritten."""
    held = []

    def keep(m):
        held.append(m.group())
        return f'\x00{len(held) - 1}\x00'
    return re.sub(r"'(?:[^']|'')*'|--[^\n]*|/\*.*?\*/", keep, sql, flags=re.S), held


def _unmask(sql, held):
    return re.sub(r'\x00(\d+)\x00', lambda m: held[int(m[1])], sql)


def patient_grain(sql, returns=None):
    """Does this query count or list patients? (money, event counts and row-level analyses are not patient grain.)"""
    masked, _ = _mask(sql or '')
    if returns == 'patient_set': return True
    return bool(_COUNT_PATIENTS.search(masked) or _SELECT_PATIENT.search(masked) or _COUNT_ROWS_OF_PATIENT.search(masked))


def references_death(sql):
    """Does the SQL read the death relation for a death-based rule of its own? (A patient's age at death, request_plan's age field, is not one.)"""
    masked, _ = _mask(sql or '')
    masked = masked.replace('FROM death AS age_death', 'FROM age_at_death')
    return bool(re.search(r'\bdeath\b', masked, re.I))


def _cte_names(masked):
    return {m.group(1).lower() for m in re.finditer(r'(?i)(?:\bwith\s+(?:recursive\s+)?|,\s*)([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s+as\s*\(', masked)}


def _substitute(sql, living, latest):
    """-> (rewritten SQL, {'living': [tables], 'latest': bool}). Each FROM/JOIN of a patient-keyed relation becomes a filtered subquery
    that keeps the relation's name or alias, so every qualified reference still resolves. One pass: inserted text is not rescanned."""
    masked, held = _mask(sql)
    ctes = _cte_names(masked)
    done = {'living': [], 'latest': False}

    def sub(m):
        kw, ws, quote, name, ws2, as_kw, alias = m.groups()
        table = name.lower()
        if alias and alias.lower() in _KEYWORDS:
            tail, alias, as_kw = (ws2 or '') + alias, None, None
        else:
            tail = ''
        if table in ctes or table not in PATIENT_TABLES: return m.group()
        is_measurement = table == 'measurement' and latest
        if not living and not is_measurement: return m.group()
        if is_measurement:
            inner = '(SELECT * FROM measurement WHERE record_id IN (SELECT record_id FROM measurement_latest)'
            inner += (f' AND patient_id {LIVING_PREDICATE})' if living else ')')
        else:
            inner = f'(SELECT * FROM {table} WHERE patient_id {LIVING_PREDICATE})'
        if is_measurement: done['latest'] = True
        if living and table not in done['living']: done['living'].append(table)
        label = alias or f'{quote}{name}{quote}'
        return f'{kw}{ws}{inner} AS {label}{tail}'

    out = _FROM.sub(sub, masked)
    return _unmask(out, held), done


def enforce_sql(sql, returns=None, tables=None):
    """The query to run under the current scope and what was changed: (sql, receipt | None). No scope -> unchanged (tools, tests).

    A patient-grain query is restricted to living patients (every patient-keyed relation it reads) unless the question asks for the
    deceased or the query already reads `death`; a patient-grain query that filters measurement on a result reads the latest result per
    patient and concept unless the question asks for any result. A patient-grain query this cannot restrict (no patient-keyed relation
    to filter, not a subset of a given population) is recorded as a problem: the answer check declines the proposal."""
    state = _SCOPE.get()
    if state is None or not sql: return sql, None
    if not patient_grain(sql, returns): return sql, None
    # `tables` = the relations of the database the query runs on: a database without `death` (or measurement_latest) has nothing to apply
    living = state.living_only and not references_death(sql) and (tables is None or 'death' in tables)
    masked, _ = _mask(sql)
    uses_measurement = (bool(re.search(r'(?i)\b(?:FROM|JOIN)\s+"?measurement"?(?=[\s,;)]|$)', masked)) and 'value' in masked.lower()
                        and not re.search(r'(?i)\bhaving\b|\bover\s*\(', masked))  # per-patient result counts and windows need every result
    latest = state.lab_mode == 'latest' and uses_measurement and (tables is None or 'measurement_latest' in tables)
    if not living and not latest: return sql, None
    rewritten, done = _substitute(sql, living, latest)
    with state.lock: state.checked += 1
    receipt = {'living': done['living'] if living else [], 'latest_labs': done['latest'], 'rewritten': rewritten != sql}
    if living and not done['living'] and not re.search(r'(?i)\bpopulation\b', masked):
        state.problem('a patient-grain query reads no patient-keyed relation the living-patient rule could restrict')
    if receipt['rewritten']:
        state.note('living_patients' if living and done['living'] else 'latest_labs',
                   {'tables': done['living'], 'latest_labs': done['latest']})
    return rewritten, receipt


# --- plan enforcement (cohort atoms, universe) ------------------------------------------------------------------------------------
def enforce_plan(plan, question=None):
    """The graph plan with lab thresholds in cohort atoms set to the question's lab mode (latest unless the question says ever / any).
    Returns (plan, changes). Runs before the result-cache key is computed, so the rewritten plan is what is cached."""
    state = _SCOPE.get()
    mode = state.lab_mode if state is not None else lab_mode(question)
    if mode != 'latest' or not isinstance(plan, dict): return plan, []
    import copy
    changes = []
    plan = copy.deepcopy(plan)
    for node in plan.get('nodes', []):
        if node.get('operation') != 'cohort': continue
        spec = (node.get('parameters') or {}).get('specification') or {}
        for group in ('all', 'any', 'exclude'):
            for atom in spec.get(group) or []:
                if atom.get('kind') == 'measurement' and atom.get('aggregate') == 'any':
                    atom['aggregate'] = 'latest'
                    changes.append({'node': node.get('id'), 'concept': atom.get('concept')})
    if changes and state is not None: state.note('latest_labs', {'cohort_atoms': changes})
    return plan, changes


def deceased_among(db, patient_ids):
    """The ids that have a row in `death` (db: an open semantic connection)."""
    ids = [p for p in dict.fromkeys(patient_ids or []) if p]
    out = set()
    try:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            out |= {r[0] for r in db.execute(f"SELECT patient_id FROM death WHERE patient_id IN ({','.join('?' * len(chunk))})", chunk)}
    except Exception as exc:  # noqa: BLE001 - no death relation (or an interrupted probe): nobody is known to be deceased
        if 'death' not in str(exc) and 'interrupt' not in str(exc).lower(): raise
        return set()
    return out


def living_ids(db, patient_ids):
    dead = deceased_among(db, patient_ids)
    return [p for p in patient_ids if p not in dead]


def living_universe(db, universe):
    """The roster minus everyone in death."""
    try:
        return set(universe) - {r[0] for r in db.execute('SELECT patient_id FROM death')}
    except Exception as exc:  # noqa: BLE001 - a database without a death relation has no deceased patients to remove
        if 'death' not in str(exc): raise
        return set(universe)


def condition_membership(path, spec):
    """For the cohort engine: {concept: set(patient_id)} under the ONE condition-evidence rule, for each whole-record default-evidence
    condition concept atom of a (grounded) cohort spec, read from the semantic layer's concept_patient (EHR diagnoses, counted payer-claim
    diagnoses, laboratory evidence for diabetes and CKD). None outside a question scope or when the atoms name no such concept."""
    if _SCOPE.get() is None: return None
    from .concepts import CONDITIONS
    names = sorted({a['concept'] for g in ('all', 'any', 'exclude') for a in spec.get(g) or []
                    if a.get('kind') == 'concept' and a.get('concept') in CONDITIONS and not a.get('evidence')
                    and not (a.get('window') or {}).get('start') and not (a.get('window') or {}).get('end')
                    and (a.get('window') or {}).get('basis', 'all_observed') == 'all_observed'})
    target = Path(path) / 'semantic.sqlite3'
    if not names or not target.exists(): return None
    import sqlite3
    from . import sql_query
    db = sql_query.register(sqlite3.connect(f'file:{target}?mode=ro', uri=True))
    try:
        return {n: {r[0] for r in db.execute("SELECT patient_id FROM concept_patient WHERE kind = 'condition' AND concept = ?", (n,))} for n in names}
    except sqlite3.Error:
        return None
    finally:
        db.close()


# --- receipt and check -------------------------------------------------------------------------------------------------------------
def receipt(question=None, state=None):
    state = state or _SCOPE.get() or Scope(question)
    return {'version': VERSION, 'decided_by': DECIDED_BY,
            'population': {**state.population},
            'lab_mode': state.lab_mode,
            'condition_evidence': 'ehr_diagnosis + counted payer_claim_diagnosis (+ laboratory evidence for diabetes and CKD)',
            'windows': state.windows,
            'rewrites': list(state.rewrites), 'problems': list(state.problems)}


EVENT_QUESTION = re.compile(r'\b(?:ed visits?|er visits?|emergency (?:department )?(?:visits?|room)|went to the (?:ed|er)|inpatient|admitted|admissions?|'
                            r'hospital stays?|observation stays?|observation)\b', re.I)
_EHR_EVENT_TABLE = re.compile(r'(?is)\b(?:FROM|JOIN)\s+"?(visit|encounter)"?\b(?:(?!\bFROM\b|\bJOIN\b).){0,400}?\'(?:inpatient|emergency|observation|ed|er)\b')


def event_source_findings(question, sqls):
    """ED visits, inpatient stays and observation stays are all-source events (utilization_event / stay). A patient-grain query that
    defines them from the EHR-only visit or encounter table undercounts (COPD inpatient stay in 2029: 7 against 25)."""
    if not EVENT_QUESTION.search(question or ''): return []
    out = []
    for sql in sqls or []:
        masked, held = _mask(sql)
        text = _unmask(masked, held)
        if patient_grain(sql) and _EHR_EVENT_TABLE.search(text) and not re.search(r'(?i)\b(?:utilization_event|stay)\b', masked):
            out.append('ED visits, inpatient stays and observation stays are all-source events: read utilization_event (kind ed_visit | '
                       'inpatient | observation) or stay, not the EHR-only visit / encounter table')
    return list(dict.fromkeys(out))


def verify(question, answer, state=None, db=None, sqls=()):
    """The answer check. -> {'ok': bool, 'reasons': [...], 'deceased': [ids]}. Fails when the scope recorded a query it could not
    restrict, when a patient list of a living-only answer holds a deceased patient, or when an event is read from an EHR-only table."""
    state = state or _SCOPE.get() or Scope(question)
    reasons = [*state.problems, *event_source_findings(question, sqls)]
    dead = []
    if state.living_only and db is not None and isinstance(answer, dict):
        ids = [*(answer.get('patient_ids') or []), *(answer.get('unknown_patient_ids') or [])]
        dead = sorted(deceased_among(db, ids))
        if dead:
            reasons.append(f'the answer lists {len(dead)} deceased patients but patients are living patients unless the question asks '
                           'for the deceased')
    return {'ok': not reasons, 'reasons': reasons, 'deceased': dead}


def stamp(answer, question=None, state=None):
    """answer['decision_context'] = the receipt (version, population, lab mode, windows, what was enforced)."""
    if isinstance(answer, dict): answer['decision_context'] = receipt(question, state)
    return answer


def finalize(stack_path, result, state):
    """compose(): stamp the receipt on the answer and make the patient list agree with the decision. Exact tiers (grammar, saved
    contracts) never pass answer_checks; their SQL already restricts to living patients, so a deceased id here means a query the
    rewrite could not reach: the ids are dropped, a count that equalled the list is recomputed, and the answer says so."""
    answer = result.get('answer') if isinstance(result, dict) else None
    if not isinstance(answer, dict): return result
    if state.living_only and answer.get('status') in ('answered', 'partial') and stack_path is not None:
        target = Path(stack_path) / 'semantic.sqlite3'
        if target.exists():
            import sqlite3
            with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
                ids = answer.get('patient_ids') or []
                dead = deceased_among(db, [*ids, *(answer.get('unknown_patient_ids') or [])])
            if dead:
                kept = [p for p in ids if p not in dead]
                if answer.get('count') == len(ids) and answer.get('answer_type') in ('patient_set', 'count'):
                    answer['count'] = len(kept)
                    if isinstance(answer.get('headline'), str):
                        answer['headline'] = re.sub(r'^\d[\d,]*', str(len(kept)), answer['headline'], count=1)
                answer['patient_ids'] = kept
                answer['unknown_patient_ids'] = [p for p in answer.get('unknown_patient_ids') or [] if p not in dead]
                answer['limitations'] = [*(answer.get('limitations') or []),
                                         f'{len(dead)} deceased patients were removed from this answer: patients are living patients unless '
                                         'the question asks for the deceased (decision context).']
                state.note('deceased_removed', {'count': len(dead)})
    stamped = answer.get('decision_context')
    if isinstance(stamped, dict) and stamped.get('version') == VERSION:  # the answer check already stamped the accepted proposal
        stamped['rewrites'] = [*stamped.get('rewrites', []), *state.rewrites]
        stamped['problems'] = [*stamped.get('problems', []), *state.problems]
    else:
        answer['decision_context'] = receipt(state.question, state)
    return result
