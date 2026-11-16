"""Governed definitions as code checks: a proposed query that contradicts a definition is rejected before it answers.

catalogue.py states the definitions in words for the SQL writer. Words are advice; on every frozen scorecard so far the
same definitions were still missed (stay rows counted as stays, a table holding only the latest value counted as every
result, the wrong claim table, both flu vaccines not counted). Each check here reads the question and the SQL, and when the
SQL takes a reading the definition rules out, returns what to do instead. The router raises it as an execution error, so the
frontier writer gets one corrected attempt, and a saved or chosen recipe that fails a check does not answer.

Checks are code-reviewed, conservative and string-based: a check applies only when the question's words and the SQL both
say so plainly. Built-in recipes (reviewed by hand) are not checked unless a model chose them for a question.
"""
import contextvars
import os
import re

ENABLED = contextvars.ContextVar('agere_governed_checks', default=None)   # None: follow AGERE_TIERED_GOVERNED (on by default)


def enabled():
    flag = ENABLED.get()
    return (os.environ.get('AGERE_TIERED_GOVERNED', 'on') == 'on') if flag is None else bool(flag)


def _reads(sql, table):
    return re.search(rf'(?i)\b(?:from|join)\s+(?:main\.)?"?{table}"?(?:\s|$|\)|,)', sql) is not None


def _has(text, pattern):
    return re.search(pattern, text, re.I) is not None


def _about_records(q):
    """A question written in the warehouse's own column names (admit_date, net_payer_paid_cents, {year} templates aside) or
    about a table's types and rows asks about those records as stored, not about the governed measure."""
    names = [w for w in re.findall(r'\b[a-z][a-z0-9]*_[a-z0-9_]+\b', q.lower())]
    return bool(names) or _has(q, r'\bstay types?\b|\bby type\b|\btype of stay|\bclaim rows?\b|\brows?\b|\brecorded\b.*\btime\b')


def whole_stays(q, s):
    """A hospital stay is counted once: use stay_episode, not stay rows or inpatient utilization events."""
    asks = _has(q, r'\b(?:stays?|admissions?|admitted|hospitali[sz]\w*|discharg\w*|readmi\w*|length of stay|los)\b')
    if not asks or _about_records(q) or _has(q, r'observation|\bed\b|emergency|utilization event|stay records?|stay rows?|\brecords? of\b|each record|source rows?'): return None
    if _reads(s, 'stay_episode'): return None
    # A question about the stay table's own columns (discharge_known, readmitted_30d, los_days_elapsed ...) is about its rows.
    if _has(q, r'\b(?:discharge_known|readmitted_30d|days_to_readmission|los_days\w*|discharge_date_source|discharge_time_utc|admit_time_utc|stay_source|stay table)\b'): return None
    # Counting distinct patients gives the same number from stay rows as from merged stays.
    aggregates = re.findall(r'(?i)\b(count|sum|avg|min|max|total)\s*\(\s*(distinct\s+)?([\w.]*)', s)
    if aggregates and all(a[0].lower() == 'count' and a[1] and a[2].split('.')[-1].lower() == 'patient_id' for a in aggregates): return None
    own_merge = _has(s, r'over\s*\(\s*partition\s+by\s+patient_id') and _has(s, r'discharge_date')   # a query that merges stays itself
    if own_merge: return None
    if _reads(s, 'stay') or (_reads(s, 'utilization_event') and _has(s, r"'inpatient'")):
        return ('Governed definition: a hospital stay is counted once. Read stay_episode (one row per inpatient stay after repeated, '
                'overlapping and continuing stay rows are merged: patient_id, admit_date, discharge_date, los_days, is_readmission, '
                'readmitted_within_30_days) instead of stay rows or inpatient utilization events.')
    return None


def latest_for_period(q, s):
    """measurement_latest holds only each patient's latest value; counts or averages of results use measurement."""
    if _reads(s, 'measurement_latest') and not _has(q, r'\b(?:latest|most recent|current|last|newest|currently)\b'):
        return ('Governed definition: measurement_latest holds only each patient\'s most recent value of a concept. To count patients '
                'with a result, or count or average results, read measurement.')
    return None


def spend_flag(q, s):
    """Spend and claim counts include only payer_claim rows with counts_in_metrics = 1."""
    if not _reads(s, 'payer_claim') or _has(s, r'counts_in_metrics') or _about_records(q): return None
    if _has(q, r'duplicate|void|superseded|every version|all claim records|including (?:duplicates|voids)'): return None
    return ('Governed definition: spend and claim counts use payer_claim rows with counts_in_metrics = 1 (duplicate submissions, '
            'superseded and voided claims are 0). Add that filter.')


_CLAIM_ONLY = []


def _claim_only_phrases():
    """Columns only the claim table has (net_payer_paid_cents, benefit_year, first_payer_cash_date ...), written as words."""
    if not _CLAIM_ONLY:
        from . import store
        try:
            db = store.connect()
            cols = lambda t: {r[1] for r in db.execute(f'PRAGMA table_info("{t}")')}
            only = cols('claim') - cols('payer_claim') - {'claim_id', 'patient_id', 'payer', 'service_date'}
            db.close()
        except Exception:
            only = set()
        _CLAIM_ONLY.extend(sorted(c.replace('_cents', '').replace('_', ' ') for c in only) or [''])
    return [c for c in _CLAIM_ONLY if c]


def spend_table(q, s):
    """Spend, paid amounts and claim counts come from payer_claim; the claim table is for denials and adjudication."""
    if not _reads(s, 'claim') or _reads(s, 'payer_claim') or _about_records(q): return None
    if any(phrase in q.lower() for phrase in _claim_only_phrases()): return None   # the question asks for the claim table's own fields
    if _has(q, r'deni|adjudicat|billed|allowed|status|resubmi|reversal|reprocess'): return None
    if _has(q, r'paid|spend|spent|cost|pmpm|per member|dollar|\bclaims?\b'):
        return ('Governed definition: spend, paid amounts and claim counts come from payer_claim (paid_cents, counts_in_metrics = 1, '
                'dated by service_from). The claim table is for denials and adjudication.')
    return None


def condition_source(q, s):
    """'Condition records' / 'the condition table' means condition; 'on record from any source' means concept_patient."""
    if _has(q, r'condition (?:table|records?)|conditions table|clinical (?:condition|record)|problem list'):
        if _reads(s, 'concept_patient') and not _reads(s, 'condition'):
            return 'Governed definition: the question names the clinical condition records: read condition, not concept_patient.'
    if _has(q, r'any source|from any|including claims|claims diagnos|on record from'):
        if _reads(s, 'condition') and not _reads(s, 'concept_patient'):
            return "Governed definition: a condition on record from any source is concept_patient with kind 'condition'."
    return None


def appointment_status(q, s):
    """No-shows and cancellations are appointment.final_status, unless the question asks about the outcome column."""
    if not _reads(s, 'appointment') or not _has(q, r'no[- ]?shows?|cancel|status'): return None
    if _has(s, r'\boutcome\b') and not _has(s, r'final_status') and not _has(q, r'\boutcome'):
        return "Governed definition: a no-show is final_status 'no_show' and a cancellation final_status 'cancelled'; use final_status, not outcome."
    return None


def flu_vaccines(q, s):
    """A flu shot is vaccine 'influenza' or 'influenza high-dose'."""
    if not _has(q, r'\bflu\b|influenza') or _has(q, r'standard[- ]dose|not high|excluding high|only influenza'): return None
    if _has(s, r"'influenza'") and not _has(s, r'high[- ]dose|influenza%|like\s+\'%?influenza'):
        return "Governed definition: a flu shot is vaccine 'influenza' or 'influenza high-dose': count both."
    return None


def aco_population(q, s):
    """The ACO (MSSP) population is payer WH-PAYER-MSSP's members, not every Medicare member."""
    if not _has(q, r'\baco\b|\bmssp\b|accountable care|shared savings'): return None
    # Any patient data read for an ACO question is the ACO's members' (proof_v8: ED visits counted for everyone); only a
    # question about the contract terms alone reads no population.
    tables = set(re.findall(r'(?i)\b(?:from|join)\s+(?:main\.)?"?([a-z_][a-z0-9_]*)', s)) - {'aco_contract'}
    if tables and not _has(s, r'wh-payer-mssp'):
        return ("Governed definition: the ACO (MSSP) population is the members with a member_month row for payer_id 'WH-PAYER-MSSP' "
                "in the year, and its spend is that payer's claims with counts_in_metrics = 1. Filter to payer_id 'WH-PAYER-MSSP'.")
    return None


def hospital_visit_rules(q, s):
    """ED times and the sepsis and heart failure cohorts come from hospital_visit, which applies the governed timing rules."""
    if _reads(s, 'hospital_visit') or _about_records(q): return None
    ed_time = _has(q, r'length of stay in the (?:ed|er|emergency)|(?:ed|er|emergency department) length of stay|boarding|\bboard(?:ed)?\b|throughput|door[- ]to|time in the (?:ed|er|emergency)|hours in the (?:ed|er|emergency)')
    if ed_time and (_reads(s, 'visit_group') or _reads(s, 'utilization_event') or _reads(s, 'encounter') or _reads(s, 'adt_visit')):
        return ('Governed definition: ED times come from hospital_visit (ed_minutes, boarding_minutes: minute-level times only, 0 to 72 hours, '
                'departure of an admitted patient = inpatient admission time). Read hospital_visit, not raw timestamps.')
    cohort = _has(q, r'sepsis|septic|heart failure|\bhf\b') and _has(q, r'hospitali[sz]|admi(?:t|ssion)|stay|mortality|died|death|readmi|length of stay')
    if cohort and any(_reads(s, x) for x in ('condition', 'diagnosis_any', 'concept_patient', 'encounter_diagnosis')):
        return ('Governed definition: sepsis and heart failure hospitalizations are hospital_visit rows with has_inpatient = 1 and is_sepsis = 1 or '
                'is_heart_failure = 1 (principal diagnosis), with length of stay and readmission from the stay_episode the visit falls in.')
    return None


def hospital_billing(q, s):
    """Revenue cycle questions read the health system's own claims: claim_source 'medical_claims'."""
    if not _reads(s, 'claim') or _about_records(q): return None
    # The hospital's revenue cycle framing (HFMA terms, or 'our' denials); 'claims ever denied' in general keeps the claim table's own measure.
    if _has(q, r'\bever\b'): return None
    hfma = _has(q, r'initial|first[- ]pass|final deni|overturn|appeal|days to (?:pay|payment|submit|bill)|payment lag|clean claim|revenue cycle|write[- ]off|first adjudicat')
    ours = _has(q, r'deni') and _has(q, r'\b(?:our|we|us|hospital|health system|billing|billed|submitted)\b')
    if not (hfma or ours): return None
    if not _has(s, r"medical_claims'") or _has(s, r"claim_source\s*(?:=|in\s*\()\s*'payer_medical_claims'"):
        return ("Governed definition: the health system's billing is claim rows with claim_source = 'medical_claims', by "
                "first_adjudication_date; payer_medical_claims rows are payer reports, not the hospital's claims.")
    return None


CHECKS = (aco_population, hospital_billing, hospital_visit_rules, whole_stays, latest_for_period, spend_flag, spend_table, condition_source, appointment_status, flu_vaccines)


def check(question, sql, params=None):
    """-> the messages of every governed definition the SQL contradicts for this question ([] when none). Parameter values
    are read as if written into the SQL (a recipe's :kind = 'inpatient' is a literal 'inpatient')."""
    if not enabled(): return []
    text = str(sql)
    for name, value in (params or {}).items():
        if isinstance(value, dict): value = value.get('value')
        if isinstance(value, str): text = re.sub(rf':{re.escape(name)}\b', "'" + value.replace("'", "''") + "'", text)
    q, s = ' ' + str(question) + ' ', ' '.join(text.split())
    return [m for m in (c(q, s) for c in CHECKS) if m]
