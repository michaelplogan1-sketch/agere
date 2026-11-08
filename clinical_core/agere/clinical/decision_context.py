"""One decision context for every path that answers a question (Mike, 2026-10-23).

The definitions an answer silently depends on are decided once, here, as data, and every path reads them from here: the grammar and
the local compiler, the governed and generated metrics, the engines, the cohort and specialist families, the atomic questions and the
Claude / System One planners (told in their prompts, checked in their SQL).

  patients        LIVING patients by default. An explicitly dated event-selected cohort retains every received subject;
                  present survival cannot remove a historical referral, discharge or encounter. Explicit living scope still applies.
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

VERSION = 'decision-context/9'  # 9: historical exposure retains its received subjects
# 3 (2026-10-25, Mike): event counts include everyone (the living rule is for counts / lists of patients); observation stays are hospital
# stays and admissions; pharmacy spend = net of reversals over all pharmacy claim transactions; medical spend = paid on counted claims
DECIDED_BY = 'Mike (clinical owner), 2026-10-23 / 2026-10-25'

# --- patients ------------------------------------------------------------------------------------------------------------------
DECEASED_ASK = re.compile(
    r"\b(?:deceased|died|dead|death|deaths|dying|die|dies|mortality|passed away|fatal(?:ity|ities)?|survival|survivors?|vital status|"
    r"all patients ever|ever[- ]enrolled|everyone ever|including (?:the )?(?:deceased|dead|death))\b", re.I)
LIVING_ASK = re.compile(r'\b(?:living|alive)\b', re.I)
HISTORICAL_EXPOSURE = re.compile(
    r'\bhistorical\s+(?:(?:eligible|enrolled|recorded)\s+)?'
    r'(?:exposure|eligibility|enrollment|member[- ]months?)\b', re.I)
ALL_RECEIVED_ROWS = re.compile(
    r'\b(?:all|every)\s+(?:the\s+)?received\s+(?:[a-z_]+[ -]+){0,5}'
    r'(?:rows?|records?|events?|claims?|stays?|appointments?|encounters?|observations?)\b', re.I)
RECORD_OBJECT = (r'(?:rows?|records?|accounts?|assertions?|annotations?|statements?|documents?|reports?|'
                 r'notes?|correspondence|observations?|events?|claims?)')
RECEIVED_RECORD_HEAD = re.compile(
    r'^\s*(?:(?:identify|show|list|find|review|audit|compare|count|reconcile)\s+)?'
    r'(?:(?:all|every|the)\s+)?(?:received|recorded)\s+'
    r'(?:[a-z0-9_-]+\s+){0,5}?'+RECORD_OBJECT+r'\b', re.I)
# Financial records remain the requested objects even when patient attribution
# is a requested field. A patient-first count/list still keeps its usual scope.
FINANCIAL_RECORD_HEAD = re.compile(
    r'^\s*(?:reconcile|review|audit|show|list|find|compare|count|summari[sz]e|aggregate|sum|'
    r'how many|number of|count of|total|sum of)\s+'
    r'(?:(?:all|every|the|received|recorded|patient|payer|account|remaining)\s+){0,4}'
    r'(?:payments?|refunds?|balances?|remittances?|charges?|credits?)\b', re.I)
PATIENT_WORDS = re.compile(r'\b(?:patients?|people|persons?|individuals?|who|whom|whose|adults?|children|women|men|females?|males?|seniors?|elderly)\b', re.I)
# questions about money or membership months are not patient counts: the living rule does not apply to them (they say "members",
# "claims", "paid", "PMPM", never a count of patients)
NON_PATIENT_GRAIN = re.compile(r'\b(?:member[- ]months?|pmpm|per member per month|claims?|paid|allowed|billed|denials?|denied|premiums?|'
                               r'revenue|reimbursements?|spend|spending|costs?|payments?|dollars?|cents?)\b', re.I)
LIVING_PREDICATE = 'NOT IN (SELECT patient_id FROM death)'
# Each person counted once (DEFAULT ON since 2026-10-23, Claude as build owner under Mike's standing approval: "patients" are people): a second chart of
# a person already on the roster (patient.data_segment = 'duplicate_chart') is out with the deceased, in every path at once (the SQL rewrite, the typed
# compiler, the population forms and the planner prompt). AGERE_EXCLUDE_DUPLICATE_CHARTS=0 counts charts instead.
DUPLICATE_SEGMENT = 'duplicate_chart'
COUNTED_PREDICATE = f"NOT IN (SELECT patient_id FROM death UNION SELECT patient_id FROM patient WHERE data_segment = '{DUPLICATE_SEGMENT}')"
SEGMENT_MARKER = 'patient.data_segment'  # sql_query._relations adds this name when the patient relation carries the column
EXCLUDE_DUPLICATE_CHARTS = True


def exclude_duplicate_charts():
    import os
    env = os.environ.get('AGERE_EXCLUDE_DUPLICATE_CHARTS', '').strip().lower()
    if env in ('0', 'off', 'false', 'no'): return False
    return EXCLUDE_DUPLICATE_CHARTS or env in ('1', 'on', 'true', 'yes')


def counted_predicate(has_segment=True):
    return COUNTED_PREDICATE if has_segment and exclude_duplicate_charts() else LIVING_PREDICATE

# --- labs ----------------------------------------------------------------------------------------------------------------------
ANY_LAB = re.compile(r"\b(?:ever|at any (?:time|point|visit)|any (?:time|result|value|reading|draw|measurement)|history of|had a history|"
                     r"any (?:hb)?a1c|any (?:ldl|hdl|egfr|gfr|creatinine|potassium|sodium|glucose|tsh|bnp|nt-?probnp|hemoglobin|hgb|platelets?|wbc|uacr|"
                     r"triglycerides?|cholesterol|blood pressure|bp|systolic|diastolic|weight|bmi|ferritin|troponin|phq-?9|inr|alt|ast|albumin)\b|"
                     r"at least (?:once|twice)\b|at least (?:two|2|\d+)(?:\s+\w+){0,2}?\s+(?:results?|readings?|draws?|tests?|measurements?|values?|times|occasions)\b|twice|two or more|more than once|repeated|consecutive|once|highest|lowest|peak|maximum|"
                     r"minimum|max|min|worst|best|trend(?:s|ing)?|over time|on any draw|every result|all results)\b"
                     r"(?!\s+(?:share|percent|percentage|proportion|fraction|rate|number|count|volume|cost|spend|spending|paid|amount|total|payers?|"
                     r"practices?|providers?|clinics?|groups?|segments?|average|ranking|performing)\b)", re.I)  # "the lowest share of ..." ranks groups, it is not a lab extreme

# --- windows -------------------------------------------------------------------------------------------------------------------
ROLLING_YEAR = re.compile(r"\b(?:in |during |within |over |for )?the (?:last|past|previous|trailing) (?:year|12 months|twelve months|365 days)\b|"
                          r"\b(?:last|past) year\b|\bin the last 12 months\b|\btrailing 12 months\b", re.I)
CALENDAR_YEAR = re.compile(r"\b(?:in|during|for|of)\s+((?:19|20)\d\d)\b", re.I)
RECEIPT_YEAR = re.compile(r'\b(?:received|arrived|available)\s+(?:(?:in|during)\s+)?((?:19|20)\d{2})\b',re.I)
ROLLING_YEAR_SQL = "date({as_of}, '-12 months')"  # an event date d is in the window when d > ROLLING_YEAR_SQL and d <= {as_of}


def receipt_window_request(question,as_of=None):
    """Bind only a directly stated, single receipt year; never guess other clocks."""
    q=question or ''
    head=RECEIVED_RECORD_HEAD.search(re.sub(r'^\s*(?:for|among)\s+','',q,flags=re.I))
    matches=[m for m in RECEIPT_YEAR.finditer(q)
        if(head is not None and m.group()in head.group()
           or re.search(r'\b'+RECORD_OBJECT+r'(?:\s+(?:that\s+)?(?:were|was))?\s*$',q[:m.start()],re.I))]
    years=set(re.findall(r'\b(?:19|20)\d{2}\b',q))
    if not matches or len(years)!=1:return None
    year=matches[0][1]
    contract={'source_clause':matches[0].group(),
            'assertion_window':{'basis':'arrival','start':year+'-01-01','end':year+'-12-31'},
            'authority':'explicit_question_receipt_year',
            'meaning':'Receipt/availability is distinct from source-event and clinical/reference dates.'}
    cutoff=str(as_of)[:10]if as_of is not None else None
    if cutoff and year+'-01-01'<=cutoff<year+'-12-31':
        contract['requested_calendar_end']=year+'-12-31'
        contract['unobserved_remainder_after_snapshot']=True
        contract['assertion_window']['end']=cutoff
    return contract


def validate_receipt_window(question,parameters,as_of=None):
    requested=receipt_window_request(question,as_of)
    if requested is not None and parameters.get('assertion_window')!=requested['assertion_window']:
        raise ValueError('Assertion receipt-window binding: '+repr(requested['source_clause'])+
            ' requires assertion_window='+str(requested['assertion_window'])+
            '. Correct the typed window before source acquisition; retain source-event and clinical/reference dates separately.')

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
LAB_EVIDENCE_CONDITIONS = ('diabetes', 'chronic_kidney_disease')  # generic concepts only (see concepts.LAB_EVIDENCE_CONDITIONS)

# --- events --------------------------------------------------------------------------------------------------------------------
EVENT_RELATIONS = {
    'ed_visit': "utilization_event WHERE kind = 'ed_visit'", 'inpatient_stay': "utilization_event WHERE kind = 'inpatient' (or stay)",
    'observation_stay': "utilization_event WHERE kind = 'observation'",
}
EHR_ONLY_EVENT_TABLES = ('visit', 'encounter')
EHR_ONLY_EVENT_VALUES = re.compile(r"\b(?:inpatient|emergency|observation|ed|er)\b", re.I)

# The analyst's definition views (analyst_definitions) are living-only by construction: a patient-grain query that reads one has the living rule applied
# by that view (no other tier writes these names; the executor creates them only for a query that reads them: sql_query.define_definition_views).
LIVING_VIEW_USE = re.compile(r'(?i)\b(?:living_patient|condition_member|latest_lab|medicare_member)\b')

# patient-keyed relations whose rows must be restricted to living patients inside a patient-grain query. death itself and the raw
# record stores are never rewritten.
PATIENT_TABLES = frozenset('''adt_visit allergy_entry appointment cancer_screening claim claim_adjudication claim_adjustment claim_line
clinical_note concept_patient condition diagnosis_any encounter encounter_diagnosis enrollment_span fill hie_document immunization
incident_diagnosis measurement measurement_latest medication medication_list_entry member_month note_fact note_therapy_change patient
patient_svi payer_adjudication payer_claim payer_claim_diagnosis payer_claim_line payer_pharmacy_claim pharmacy_claim_event prescription
problem_list_entry procedure_line returned_fill smoking_status social_need stay therapy_barrier therapy_barrier_candidate
utilization_event visit visit_group wellness_visit'''.split())

# the patient-keyed relations whose rows are clinical or operational events: counted for a population they read living patients like a
# patient count does. Money and membership relations are out (NON_PATIENT_GRAIN decides those by the question's own words), and so are the
# encounter / stay / visit relations that sequence questions ("discharges followed within 7 days") read, which count a deceased patient's
# events by design (test_sequence_language).
EVENT_GRAIN_TABLES = frozenset('''adt_visit appointment cancer_screening fill immunization utilization_event wellness_visit'''.split())


_COHORT_WORDS = re.compile(r'\b(?:patients|members|people|persons|cohort|population|everyone|similar|like|lookalikes?)\b', re.I)


def NAMED_PATIENT_QUESTION(question):
    """A question about ONE named patient (an id of the warehouse's formats, no cohort wording): the living default does not apply."""
    from .patient_ids import names_a_patient
    return names_a_patient(question) and not _COHORT_WORDS.search(question or '')


def asks_deceased(question):
    return bool(DECEASED_ASK.search(question or ''))


# Event counts (Mike 2026-10-25, "everyone, everywhere"): a count, volume, rate or share of EVENTS (admissions, ED visits, stays, fills,
# appointments, no-shows, discharges) includes every patient who had them; the living rule is for counts and lists of PATIENTS.
EVENT_NOUN = (r'(?:admissions?|readmissions?|hospitali[sz]ations?|(?:ed|er|emergency(?: department| room)?|office|clinic|hospital|inpatient|'
              r'observation|outpatient)?\s*visits?|stays?|encounters?|discharges?|fills?|refills?|appointments?|no-?shows?)')
EVENT_COUNT = re.compile(r'\b(?:how many|number of|count of|total|volume of|share of|percent(?:age)? of|proportion of|rate of|of all|of our|'
                         r'did|were|was)\s+(?:[\w%-]+\s+){0,4}?' + EVENT_NOUN + r'\b', re.I)
PATIENT_COUNT = re.compile(r'\b(?:how many|number of|count of|share of|percent(?:age)? of|proportion of|list(?: of)?|which|who)\s+(?:[\w%-]+\s+){0,3}?'
                           r'(?:patients|people|members|persons|individuals|adults|children|women|men|seniors)\b', re.I)


def counts_events(question):
    """Does the question count events, not patients? 'How many ED visits ...' yes; 'how many patients had an ED visit' no."""
    q = question or ''
    return bool(EVENT_COUNT.search(q)) and not PATIENT_COUNT.search(q)


def event_selected_cohort(question):
    """A dated event in a For/Among membership clause selects its subjects.

    This is not a new default for an unqualified count/list of current patients.
    Read only the membership clause: a request to show current responsibility
    later in the sentence must not erase its historical source population.
    """
    q = question or ''
    if not re.match(r'^\s*(?:for|among)\b', q, re.I):return False
    selection = re.split(r'[,;.?!]|\b(?:show|compare|distinguish|describe|report|identify)\b', q, maxsplit=1, flags=re.I)[0]
    received_subjects = PATIENT_WORDS.search(selection) or re.search(r'\b(?:members?|received|recorded)\b', selection, re.I)
    current_people = re.search(r'\bcurrent\s+(?:patients?|members?|people|population|cohort)\b', selection, re.I)
    # The date belongs to membership when it follows the subject's selecting
    # predicate. New source families (history updates, interviews, assessments)
    # must not silently revert to present survival because their noun is absent
    # from the event-count vocabulary. A report year before membership alone
    # does not select historical subjects.
    dated_membership = re.search(
        r'\b(?:patients?|members?|people|persons?|individuals?|adults?|children)\s+'
        r'(?:with|who|whose|having|receiving|discharged|admitted|aged|born)\b.*\b(?:19|20)\d{2}\b', selection, re.I)
    received_membership = re.match(r'^\s*(?:for|among)\s+(?:the\s+)?(?:received|recorded)\b', selection, re.I)
    event_membership = re.search(r'\b(?:' + EVENT_NOUN + r'|referrals?|requests?|observations?|discharged|admitted)\b', selection, re.I)
    return (bool(received_subjects) and bool(re.search(r'\b(?:19|20)\d{2}\b', selection))
            and bool(dated_membership or received_membership or event_membership)
            and not bool(LIVING_ASK.search(selection) or current_people))


def population(question):
    """Default patient counts are living-only; event-selected cohorts keep their subjects.

    Explicit living scope takes precedence over the historical-event exception.
    """
    q = question or ''
    if ALL_RECEIVED_ROWS.search(q) and not LIVING_ASK.search(q):
        return {'living_only': False, 'all_received_rows': True,
                'reason': 'explicit all-received-row scope includes every received patient ID in those rows'}
    if RECEIVED_RECORD_HEAD.search(q) and not LIVING_ASK.search(q):
        return {'living_only': False, 'record_subject_scope': True,
                'reason': 'received records are the requested objects; present survival does not remove their subjects'}
    if FINANCIAL_RECORD_HEAD.search(q) and not LIVING_ASK.search(q):
        return {'living_only': False, 'record_subject_scope': True,
                'reason': 'financial records are the requested objects; patient attribution does not remove their subjects'}
    if event_selected_cohort(q) and not LIVING_ASK.search(q):
        return {'living_only': False, 'event_selected_cohort': True,
                'reason': 'dated event-selected cohort retains every subject; present survival does not remove historical records'}
    if HISTORICAL_EXPOSURE.search(q) and not LIVING_ASK.search(q):
        return {'living_only': False, 'historical_exposure': True,
                'reason': 'historical exposure retains its subjects regardless of present survival; eligibility remains month-specific'}
    if counts_events(q) and not LIVING_ASK.search(q):
        return {'living_only': False, 'reason': 'a count of events, not of patients: the events of every patient count (Mike 2026-10-25)'}
    if asks_deceased(q):
        return {'living_only': False, 'reason': 'the question asks for the deceased (or death / mortality / all patients ever)'}
    if NAMED_PATIENT_QUESTION(q):
        return {'living_only': False, 'reason': 'a named patient is never excluded for being deceased (a deceased patient\'s answer says so)'}
    if NON_PATIENT_GRAIN.search(q) and not PATIENT_WORDS.search(q) and not LIVING_ASK.search(q):  # "the average living diabetic cost us ..." names living patients itself
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


# Lab rule (Mike 2026-10-24): diabetes also = ANY current HbA1c result >= 6.5 %; chronic kidney disease also = two current eGFR results
# < 60 at least 90 days apart. Same rows as the development keys (tools/dev_set/definitions.py LAB_EVIDENCE).
# the alias LAB_RULE_READ marks this read of every result: the latest-lab rewrite (enforce_sql) leaves it alone
LAB_RULE_READ = 'lab_rule_result'
_ANY_RESULT = ("SELECT patient_id, event_date, value_canonical AS value FROM measurement AS " + LAB_RULE_READ + " WHERE concept = '{c}' AND value_canonical IS NOT NULL "
               "AND result_current = 1 AND excluded_reason IS NULL AND source_table NOT IN ('claim_line', 'payer_claim_line')")
LAB_RULES = {
    'diabetes': "SELECT patient_id, MIN(event_date) AS event_date, 'lab_rule' AS source FROM (" + _ANY_RESULT.format(c='hba1c')
                + ") WHERE value >= 6.5 GROUP BY patient_id",
    'chronic_kidney_disease': "SELECT patient_id, MIN(event_date) AS event_date, 'lab_rule' AS source FROM (" + _ANY_RESULT.format(c='egfr')
                              + ") WHERE value < 60 GROUP BY patient_id HAVING julianday(MAX(event_date)) - julianday(MIN(event_date)) >= 90",
}


def lab_rule_sql(name, tables):
    """(patient_id, event_date, source) rows meeting the condition's lab rule, or None (no rule / no measurement table)."""
    m = tables.get('measurement') if isinstance(tables, dict) else None
    if name not in LAB_RULES or not m or not {'concept', 'value_canonical', 'result_current', 'excluded_reason', 'source_table'} <= set(m):
        return None
    return LAB_RULES[name]


def condition_evidence_sql(names, tables):
    """The one governed condition-evidence subquery (patient_id, event_date, source); see concepts.condition_evidence_sql."""
    from .concepts import condition_evidence_sql as governed
    return governed(names, tables)


def prompt_block():
    """The definitions as a short block for every LLM planner prompt (hosted typed interpreter, hosted SQL, analysts, routers)."""
    return (
        f'DECISION CONTEXT ({VERSION}; decided by {DECIDED_BY}). These definitions hold for every answer; apply them unless the question says otherwise.\n'
        '1. Counts and lists of PATIENTS are LIVING patients. Exclude anyone with a row in the death table: add patient_id NOT IN (SELECT patient_id FROM death) '
        'to every patient-keyed table a patient count or list reads. Include the deceased only when the question asks (deceased, died, death, '
        'mortality, "including deceased", "all patients ever"). Counts, volumes, rates and shares of EVENTS (admissions, ED visits, stays, fills, '
        'appointments, no-shows, discharges) include every patient who had them: no living filter on events. '
        'An explicit all-received-row scope also includes all patient IDs within those rows, including distinct-patient '
        'counts inside grouped event summaries. Do not add living_patient or death exclusions there unless the question asks for living patients.\n'
        'A request to identify/show/audit received records, statements, accounts or documents also retains their subjects; '
        'a relative clause such as "whose interpretation changes" refers to those records, not a living-patient population.\n'
        'A reconciliation/count/list of payments, refunds, account balances or remittances also retains every record subject, '
        'including when patient attribution is requested. A count/list of patients with those records keeps its own patient scope.\n'
        'A For/Among cohort selected by a dated recorded event (such as patients receiving a referral in a named year) also retains '
        'every subject of that historical event. Current survival/enrollment does not establish eligibility at the event. '
        'Keep subsequent deaths, ended relationships and unknown dates visible; apply living-only scope only when requested.\n'
        '2. Labs: the LATEST result per patient and concept (measurement_latest). Use measurement (every result) only when the question says ever, '
        'any, at any time, history of, at least once, highest, lowest or asks for a trend.\n'
        '3. A condition is documented by ONE evidence rule: an EHR diagnosis (condition with is_diagnosis = 1, plus problem-list rows whose flag is '
        'unknown) or a counted payer-claim diagnosis (payer_claim_diagnosis.counts_in_metrics = 1); diabetes also counts any current HbA1c >= 6.5 % '
        'and CKD two current eGFR < 60 at least 90 days apart (measurement); rule-outs never count. By ICD-10 code use diagnosis_any (the same evidence); by library concept use concept_patient. '
        'Claims-only members count.\n'
        "4. ED visits, inpatient stays and observation stays are utilization_event rows (kind ed_visit | inpatient | observation: EHR, ADT and claims "
        'together), never visit or encounter alone. A "hospital stay", "admission" or "hospitalization" is inpatient OR observation '
        "(kind IN ('inpatient', 'observation')); 'inpatient' alone means kind = 'inpatient'.\n"
        "5. 'last year', 'past year', 'last 12 months' = the 12 months ending at the as-of date: date > date(:as_of, '-12 months') AND date <= :as_of. "
        "A calendar year word ('in 2029') is that calendar year (date >= '2029-01-01' AND date <= '2029-12-31').\n"
        "6. 'Medicare' means coverage_type IN ('medicare', 'dual', 'medigap') (dual-eligible and Medigap members have Medicare); "
        "'traditional', 'original', 'fee-for-service' or 'Medicare only' means coverage_type = 'medicare'.\n"
        "7. 'On' / 'taking' a medication is current use: a picked-up fill (fill table) in the 12 months ending at the as-of date, unless the question gives its "
        "own period or asks 'ever'. A drug that was only ordered or listed ('prescribed') is a received mention, not current use.\n"
        "8. Money: pharmacy spend = net payer spend over ALL pharmacy claim transactions, reversals and recoupments netted "
        "(pharmacy_claim_event: SUM(payer_paid_delta_cents) by event_date). Medical spend and cost per claim = the payer paid amount on COUNTED "
        "medical claims (payer_claim with counts_in_metrics = 1: final, non-duplicate) by service date.\n"
        "9. Eligibility, coverage, payer or attribution at each event date requires the same subject's validity interval at that date. "
        "member_month describes coverage on ANY day of a month and its monthly selected attributes; joining its month to a truncated "
        "event date does not establish eligibility on that day. Use enrollment_span.coverage_start <= event date AND "
        "(coverage_end IS NULL OR coverage_end >= event date), retain unknown and overlapping assignments, and state any "
        "assignment convention. Monthly exposure remains a separate valid denominator; do not silently substitute it for event-date membership.")


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
_FROM = re.compile(r'(?is)\b(FROM|JOIN)(\s+)("?)([A-Za-z_]\w*)\3(?=[\s,;)]|$)(?:(\s+)(AS\s+)?("(?:[^"]|"")*"|[A-Za-z_]\w*))?')
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
    """Does this query count or list patients, or summarise a PATIENT attribute (average SVI, average age)? (money, event counts and row-level analyses are not patient grain.)"""
    masked, _ = _mask(sql or '')
    if returns == 'patient_set': return True
    if _COUNT_PATIENTS.search(masked) or _SELECT_PATIENT.search(masked) or _COUNT_ROWS_OF_PATIENT.search(masked) or _summarises_patient_attribute(masked):
        return True
    if re.search(r'(?i)\bcount\s*\(\s*distinct\b', masked):
        from .sql_read_roles import counts_distinct_patient_ids
        try:
            return counts_distinct_patient_ids(sql)
        except (ValueError, ImportError):
            state = _SCOPE.get()
            if state is not None: state.problem('the counted identifier could not be verified as a patient or event grain')
            return False  # an unproven alias does not authorize a patient-grain rewrite
    return False


_SUMMARY = re.compile(r'(?i)\b(?:avg|sum|min|max|median)\s*\(')
_FIRST_FROM = re.compile(r'(?i)\bfrom\s+"?(\w+)"?')


def _summarises_patient_attribute(masked):
    """An average / total over the patient row or its one-row-per-patient attribute relation (patient, patient_svi): the patients it describes are living patients."""
    first = _FIRST_FROM.search(masked)
    return bool(_SUMMARY.search(masked) and first and first.group(1).lower() in ('patient', 'patient_svi'))


def references_death(sql):
    """Does the SQL read the death relation for a death-based rule of its own? (A patient's age at death, request_plan's age field, is not one.)"""
    masked, _ = _mask(sql or '')
    masked = masked.replace('FROM death AS age_death', 'FROM age_at_death')
    return bool(re.search(r'\bdeath\b', masked, re.I))


def _cte_names(masked):
    return {m.group(1).lower() for m in re.finditer(r'(?i)(?:\bwith\s+(?:recursive\s+)?|,\s*)([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s+as\s*\(', masked)}


def _substitute(sql, living, latest, predicate=LIVING_PREDICATE):
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
        # the lab rule reads ANY result by definition (oracle Q13 / Q25 / Q28: rewritten to the latest, it lost patients)
        alias_name = (alias or '').strip('"').lower()
        is_measurement = table == 'measurement' and m.start() in latest and alias_name != LAB_RULE_READ
        if not living and not is_measurement: return m.group()
        if is_measurement:
            inner = '(SELECT * FROM measurement WHERE record_id IN (SELECT record_id FROM measurement_latest)'
            inner += (f' AND patient_id {predicate})' if living else ')')
        else:
            inner = f'(SELECT * FROM {table} WHERE patient_id {predicate})'
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
    masked, held = _mask(sql)
    references = {m.start(4): m.start() for m in _FROM.finditer(masked) if m.group(4).lower() == 'measurement'}
    latest = set()
    if state.lab_mode == 'latest' and references and (tables is None or 'measurement_latest' in tables):
        from .sql_read_roles import numeric_measurement_reads
        def placeholder(m):
            original = held[int(m[1])]
            return ('0' + ' ' * (len(m.group()) - 1)) if original.startswith("'") else ' ' * len(m.group())
        parseable = re.sub(r'\x00(\d+)\x00', placeholder, masked)
        try:
            latest = numeric_measurement_reads(parseable, references, exempt_aliases=(LAB_RULE_READ,))
        except (ValueError, ImportError) as exc:
            state.problem('the numeric versus completion scope of a measurement read could not be verified: ' + type(exc).__name__)
    if not living and not latest: return sql, None
    rewritten, done = _substitute(sql, living, latest, counted_predicate(tables is not None and SEGMENT_MARKER in tables))
    with state.lock: state.checked += 1
    receipt = {'living': done['living'] if living else [], 'latest_labs': done['latest'], 'rewritten': rewritten != sql}
    if living and not done['living'] and not re.search(r'(?i)\bpopulation\b', masked) and not LIVING_VIEW_USE.search(masked):
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


def _living_restriction(sql, restricted_views=()):
    """Known population exclusions, not a ban on reading death as evidence."""
    try:
        from sqlglot import exp, parse_one
        from sqlglot.optimizer.scope import traverse_scope
        from .analyst_definitions import LIVING_VIEWS
        from .clinical_definitions import PHENOTYPES
        from .plan_fence import _dependency_sql
        policies={*LIVING_VIEWS,*restricted_views,*('phenotype_'+name for name in PHENOTYPES)}
        tree=parse_one(_dependency_sql(sql),read='sqlite');scopes=list(traverse_scope(tree)or[]);restricted={}
        for scope in scopes:
            query=scope.expression;nullable=set();seen=[]
            origin=query.args.get('from_')
            if origin is not None and origin.this is not None:seen.append(origin.this.alias_or_name.casefold())
            for join in query.args.get('joins')or[]:
                right=join.this.alias_or_name.casefold();side=join.side.upper()
                if side in ('RIGHT','FULL'):nullable.update(seen)
                if side in ('LEFT','FULL'):nullable.add(right)
                seen.append(right)
            filters=[query.args[k]for k in ('where','having','qualify')if query.args.get(k)is not None]
            def filtered(alias):
                return any(not column.table or column.table.casefold()==alias.casefold()
                           for clause in filters for column in clause.find_all(exp.Column))
            narrowed=False
            for alias,(_,source)in scope.selected_sources.items():
                bound=(isinstance(source,exp.Table)and isinstance(source.this,exp.Identifier)
                       and not source.catalog and source.db.casefold()in ('','temp')and source.name.casefold()in policies)
                bound=bound or restricted.get(source,False)
                if bound and (alias.casefold()not in nullable or filtered(alias)):narrowed=True
            # Scalar/CASE projections do not change outer membership. A
            # filtered subquery does, including one reached through a CTE.
            for child in scope.subquery_scopes:
                if not restricted.get(child):continue
                parent=child.expression.parent
                while parent is not None and parent is not query:
                    if isinstance(parent,(exp.Where,exp.Having)):narrowed=True;break
                    parent=parent.parent
            narrowed=narrowed or any(restricted.get(child,False)for child in scope.set_operation_scopes)
            for predicate in query.find_all(exp.Not):
                if predicate.find_ancestor(exp.Select)is not query or not isinstance(predicate.this,(exp.In,exp.Exists)):continue
                if not any(t.name.casefold()=='death'for t in predicate.this.find_all(exp.Table)):continue
                parent=predicate.parent
                while parent is not None and parent is not query:
                    if isinstance(parent,(exp.Where,exp.Having)):narrowed=True;break
                    parent=parent.parent
            restricted[scope]=narrowed
        return bool(scopes and restricted[scopes[-1]])
    except Exception:  # native SQL validity is checked by its executor
        pass
    return False


def living_policy_results(calls):
    """Follow actual native population producers, not unused exploration.

    This detects known survival restrictions only. It does not certify general
    cohort completeness, clinical evidence or an arbitrary unknown operator.
    """
    narrowed=set()
    for call in calls or []:
        result=call.get('result')or{}
        if not isinstance(result,dict)or result.get('error')or not result.get('result_view'):continue
        args=call.get('input')or{};name=result['result_view'];restricted=False
        if call.get('name')=='query_sql':
            queries=[args.get('sql'),result.get('declared_executed_query')or result.get('executed_query')or call.get('sql')]
            restricted=any(isinstance(q,str)and _living_restriction(q,narrowed)for q in queries)
        elif call.get('name')=='compose_table':
            base=(result.get('assembly')or{}).get('base_result_view')or args.get('base')
            restricted=base in narrowed
        elif args.get('patient_query_sql'):
            restricted=_living_restriction(args['patient_query_sql'],narrowed)
        if restricted:narrowed.add(name)
    return narrowed


def population_scope_issues(question, *, calls=(), query=None, result_view=None):
    state=population(question)
    if not any(state.get(k)for k in ('all_received_rows','event_selected_cohort','record_subject_scope')):return []
    narrowed=living_policy_results(calls)
    if result_view in narrowed or isinstance(query,str)and _living_restriction(query,narrowed):
        return ['The requested historical/received population is narrowed by a living-only membership dependency. '
                'Retain its source subjects, including subsequent deaths. Use registered evidence with the requested time scope '
                '(condition_evidence preserves canonical condition evidence without condition_member survival filtering). '
                'Current membership does not establish historical eligibility. Preserve prior observations as history; '
                'repair the population and its affected native dependencies before source interpretation.']
    return []


def verify(question, answer, state=None, db=None, sqls=()):
    """The answer check. -> {'ok': bool, 'reasons': [...], 'deceased': [ids]}. Fails when the scope recorded a query it could not
    restrict, when a patient list of a living-only answer holds a deceased patient, or when an event is read from an EHR-only table."""
    state = state or _SCOPE.get() or Scope(question)
    reasons = [*state.problems, *event_source_findings(question, sqls)]
    if any(state.population.get(k)for k in ('all_received_rows','event_selected_cohort','record_subject_scope')):
        # Native bindings describe the returned fields. Earlier exploratory
        # queries remain in the audit but must not poison a repaired answer.
        bound = []
        for key in ('table_binding', 'query_evidence'):
            query = (answer.get(key) or {}).get('query') if isinstance(answer, dict) else None
            if isinstance(query, str): bound.append(query)
        def bound_prose(value):
            if isinstance(value, dict):
                source_input = value.get('input')
                query = source_input.get('sql') if isinstance(source_input, dict) else None
                if isinstance(query, str): bound.append(query)
                for child in value.values(): bound_prose(child)
            elif isinstance(value, list):
                for child in value: bound_prose(child)
        if isinstance(answer, dict): bound_prose(answer.get('computed_prose') or [])
        for sql in bound or sqls or []:
            if _living_restriction(sql):
                reasons.append('an all-received-row or dated event-selected cohort is narrowed by a living-patient exclusion; '
                               'include every patient ID in the requested received rows unless living patients were requested')
                break
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


# -- coverage (Mike 2026-10-23) --------------------------------------------------------------------------------------------
# "Medicare" includes dual-eligible and Medigap members by default (they all have Medicare); "traditional / fee-for-service /
# original Medicare (only)" narrows to coverage_type medicare.
MEDICARE_COVERAGE = ('medicare', 'dual', 'medigap')
TRADITIONAL_MEDICARE = re.compile(r'\b(?:traditional|original|fee[- ]for[- ]service|ffs)\s+medicare\b|\bmedicare\s+(?:fee[- ]for[- ]service|ffs|only)\b', re.I)


def coverage_values(value, question=''):
    """The coverage_type values a named coverage means under this decision context."""
    if value == 'medicare' and not TRADITIONAL_MEDICARE.search(question or ''): return MEDICARE_COVERAGE
    return (value,)
