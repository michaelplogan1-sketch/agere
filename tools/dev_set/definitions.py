"""Definitions every dev-set answer key uses (Mike's decisions, 2026-10-23). One file, so a definition can be re-aligned once.

Every item is a SQL fragment (a string) or a small function that returns one; keys.py and the bucket modules build their
queries only from these. Dates are ISO strings (YYYY-MM-DD) as the semantic layer stores them.

  patients          living patients (no `death` row) and not a duplicate chart, unless a question asks for the deceased
  labs              latest result by default (`measurement_latest`); "ever / any / highest / lowest / history" = any result
  time windows      "last year" / "past year" = the 12 months ending at the as-of date; "in 2029" = that calendar year
  condition         EHR diagnoses + problem-list entries with an unknown flag + counted payer-claim diagnoses;
                    diabetes and CKD also count lab evidence; rule-outs (is_diagnosis = 0) never count
  spend             payer claims with counts_in_metrics = 1, paid amount by service_from date (reversals / recoupments net out)
  ED use            utilization_event kind = 'ed_visit'; admissions: utilization_event kind = 'inpatient'
"""
from __future__ import annotations

import datetime as _dt

SEMANTIC_DB_RELATIVE = 'artifacts/agere-healthcare-v28-sample-0.30/stack/semantic.sqlite3'
AS_OF = '2030-05-27'            # table meta.as_of of the v28 sample-0.30 stack; keys.py asserts it still matches
LAST_YEAR_START = '2029-05-28'  # first day of the 12 months ending AS_OF (exclusive lower bound is 2029-05-27)
CALENDAR_YEAR = '2029'          # "in 2029"


def months_before(iso_date: str, months: int) -> str:
    """The day after the date `months` months earlier (12 months ending iso_date start the day after iso_date - 12 months)."""
    d = _dt.date.fromisoformat(iso_date)
    y, m = divmod(d.year * 12 + (d.month - 1) - months, 12)
    import calendar
    last = calendar.monthrange(y, m + 1)[1]
    return (_dt.date(y, m + 1, min(d.day, last)) + _dt.timedelta(days=1)).isoformat()


assert months_before(AS_OF, 12) == LAST_YEAR_START


def in_last_year(column: str) -> str:
    return f"{column} >= '{LAST_YEAR_START}' AND {column} <= '{AS_OF}'"


def in_year(column: str, year: str = CALENDAR_YEAR) -> str:
    return f"substr({column}, 1, 4) = '{year}'"


# --- who counts as a patient -------------------------------------------------------------------------------------------
# duplicate_chart rows are second charts of a patient already in the roster (no notes, almost no events): counting them
# would count one person twice, so they are out with the deceased.
LIVING = ("SELECT patient_id FROM patient WHERE patient_id NOT IN (SELECT patient_id FROM death) "
          "AND data_segment <> 'duplicate_chart'")
DECEASED = "SELECT patient_id FROM death"
LIVING_CTE = f"living AS ({LIVING})"

# The practice a patient belongs to: the PCP practice on the patient row, otherwise the attributing practice on the most
# recent enrollment span.
PRACTICE_CTE = """practice_of AS (
  SELECT p.patient_id,
         COALESCE(p.pcp_practice_id,
                  (SELECT e.attribution_practice_id FROM enrollment_span e
                    WHERE e.patient_id = p.patient_id AND e.attribution_practice_id IS NOT NULL
                    ORDER BY e.coverage_start DESC LIMIT 1)) AS practice_id
    FROM patient p)"""

# --- labs ---------------------------------------------------------------------------------------------------------------
# Latest result: measurement_latest (one row per patient and concept, the latest dated result, corrections applied).
LATEST_LAB = "SELECT patient_id, event_date, value_canonical AS value, unit_canonical AS unit FROM measurement_latest WHERE concept = '{concept}'"
# Any result: every current, usable result row of a real lab or vital (billed-test rows without a result are not results).
ANY_LAB = ("SELECT patient_id, event_date, value_canonical AS value, unit_canonical AS unit, record_id FROM measurement "
           "WHERE concept = '{concept}' AND value_canonical IS NOT NULL AND result_current = 1 AND excluded_reason IS NULL "
           "AND source_table <> 'claim_line' AND source_table <> 'payer_claim_line'")


def latest_lab(concept: str) -> str:
    return LATEST_LAB.format(concept=concept)


def any_lab(concept: str) -> str:
    return ANY_LAB.format(concept=concept)


# --- conditions ---------------------------------------------------------------------------------------------------------
# name -> ICD-10 LIKE patterns (applied to EHR rows, problem-list entries and payer-claim diagnoses alike)
CONDITIONS = {
    # code sets aligned with the product's canonical concept library (decision_context / tools/dev_oracle.py), 2026-10-23
    'diabetes': ['E08%', 'E09%', 'E10%', 'E11%', 'E13%'],
    'ckd': ['N18%', 'E08.2%', 'E09.2%', 'E10.2%', 'E11.2%', 'E13.2%', 'I12%', 'I13%'],
    'heart_failure': ['I50%', 'I11.0%', 'I13.0%', 'I13.2%'],
    'hypertension': ['I10%', 'I11%', 'I12%', 'I13%', 'I15%', 'I16%'],
    'copd': ['J43%', 'J44%'],
    'asthma': ['J45%'],
    'depression': ['F32%', 'F33%'],
    'cad': ['I25%'],
    'afib': ['I48%'],
    'hyperlipidemia': ['E78%'],
    'obesity': ['E66%'],
    'dementia': ['F01%', 'F02%', 'F03%', 'G30%'],
    'osteoporosis': ['M81%'],
    'hypothyroidism': ['E03%'],
}
# Lab evidence (only for these two): diabetes = any HbA1c result >= 6.5 %; CKD = two eGFR results < 60 at least 90 days apart.
LAB_EVIDENCE = {
    'diabetes': ("SELECT DISTINCT patient_id, MIN(event_date) AS first_date, 'lab' AS source FROM (" + ANY_LAB.format(concept='hba1c')
                 + ") WHERE value >= 6.5 GROUP BY patient_id"),
    'ckd': ("SELECT patient_id, MIN(event_date) AS first_date, 'lab' AS source FROM (" + ANY_LAB.format(concept='egfr')
            + ") WHERE value < 60 GROUP BY patient_id HAVING julianday(MAX(event_date)) - julianday(MIN(event_date)) >= 90"),
}


def _likes(column: str, name: str) -> str:
    return '(' + ' OR '.join(f"{column} LIKE '{p}'" for p in CONDITIONS[name]) + ')'


def condition_evidence(name: str, labs: bool = True) -> str:
    """SQL returning (patient_id, first_date, source) rows of evidence that a patient has the condition. No filtering by
    living status (join `living` for that)."""
    parts = [
        # EHR diagnoses: condition rows flagged as diagnoses, or problem-list rows whose flag is unknown (NULL); rule-outs (0) never.
        f"SELECT patient_id, COALESCE(diagnosis_date, event_date) AS first_date, 'ehr' AS source FROM condition "
        f"WHERE (is_diagnosis = 1 OR (is_diagnosis IS NULL AND diagnosis_basis = 'ehr_problem_list')) AND {_likes('icd10', name)}",
        f"SELECT patient_id, COALESCE(onset_date, first_recorded) AS first_date, 'problem_list' AS source FROM problem_list_entry "
        f"WHERE {_likes('icd10', name)}",
        f"SELECT patient_id, service_from AS first_date, 'claim' AS source FROM payer_claim_diagnosis "
        f"WHERE counts_in_metrics = 1 AND {_likes('icd10', name)}",
    ]
    if labs and name in LAB_EVIDENCE:
        parts.append(LAB_EVIDENCE[name])
    return ' UNION ALL '.join(parts)


def condition_cte(name: str, labs: bool = True) -> str:
    """CTE `has_<name>(patient_id, first_date)` over living patients only."""
    return (f"has_{name} AS (SELECT patient_id, MIN(first_date) AS first_date FROM ({condition_evidence(name, labs)}) "
            f"WHERE patient_id IN (SELECT patient_id FROM living) GROUP BY patient_id)")


# --- medications --------------------------------------------------------------------------------------------------------
# Active medication = a medication-list entry whose list status is 'active' (the list is the clinician-facing "current meds";
# listed_active_no_fill_180d entries stay active, with the discrepancy visible in the column).
ACTIVE_MEDS = "SELECT patient_id, name, rxcui, list_source, last_fill_date, discrepancy FROM medication_list_entry WHERE list_status = 'active'"
# A fill = a row of `fill` (dispense + counted pharmacy-claim fills merged); PDC (proportion of days covered) is computed
# in keys.py from these rows: covered days over [first fill, window end], fills carried forward when they overlap,
# class = fill.drug_class, at least two fills, adherent when PDC >= 0.8.
PDC_THRESHOLD = 0.8
PDC_MIN_FILLS = 2

# --- utilization --------------------------------------------------------------------------------------------------------
ED_VISITS = "SELECT patient_id, event_date, record_id, readmitted_30d FROM utilization_event WHERE kind = 'ed_visit'"
ADMISSIONS = "SELECT patient_id, event_date AS admit_date, end_date AS discharge_date, record_id, readmitted_30d FROM utilization_event WHERE kind = 'inpatient'"
# Outpatient follow-up: a completed visit of these types (office, primary care, specialty, telehealth) -- not an ED, urgent
# care, inpatient, telephone-only or orders-only contact.
FOLLOW_UP_VISIT = ("SELECT patient_id, event_date FROM visit WHERE type IN ('office', 'primary_care', 'specialty_followup', "
                   "'ambulatory', 'specialist_office', 'telehealth', 'pediatric_office', 'outpatient') "
                   "AND COALESCE(status, 'completed') = 'completed'")

# --- spend and coverage -------------------------------------------------------------------------------------------------
SPEND_CLAIMS = "SELECT * FROM payer_claim WHERE counts_in_metrics = 1"          # paid_cents nets reversals and recoupments
MEMBER_MONTHS = "SELECT patient_id, month, line_of_business, product, payer_id, attribution_practice_id FROM member_month"
DENIED_STATUSES = ("denied", "denied_upheld")                                     # final_status of a counted claim
OUT_OF_NETWORK = "network_status = 'out_of_network'"
HIGH_COST_CLAIMANT_USD = 50_000                                                    # "high-cost claimant" = year paid >= $50,000

# --- social needs -------------------------------------------------------------------------------------------------------
# Screened-positive need: a social_need row with positive = 1 (latest screen not tracked: any positive screen counts, in the window asked).
POSITIVE_NEED = "SELECT DISTINCT patient_id, need FROM social_need WHERE positive = 1"
# Purchased SDOH: sdoh_vendor rows with match_status exact/high confidence only when the question says "matched"; default = any row.
FOOD_ACCESS_LOW = "SELECT DISTINCT patient_id FROM sdoh_vendor WHERE food_access_low = 1"
# Purchased SDOH ("the vendor data"): the patient's latest matched delivery (match_status exact or partial_address; no_match rows carry no scores).
SDOH_LATEST_CTE = ("sdoh_latest AS (SELECT * FROM (SELECT v.*, ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY delivery_date DESC, record_id DESC) rn "
                   "FROM sdoh_vendor v WHERE match_status IN ('exact', 'partial_address')) WHERE rn = 1)")
VENDOR_HIGH_RISK = 7.0        # a 1-10 vendor risk score at or above this is "high" (financial stress, transportation, housing, isolation)
HIGH_SVI = 75.0               # patient_svi.svi_overall is a 0-100 percentile; "high social vulnerability" = 75 or above

# Pharmacy claims: a fill is a payer_pharmacy_claim with counts_as_fill = 1 (paid), dated by fill_date; spend = plan_paid_cents of fills.
PHARMACY_FILLS = "SELECT * FROM payer_pharmacy_claim WHERE counts_as_fill = 1"
GENERIC_TIERS = ("generic",)  # formulary_tier values brand / generic / specialty; rows with no tier are excluded from generic-rate denominators

# --- quality-measure conventions ----------------------------------------------------------------------------------------
AGE = 'age_latest'  # DEPRECATED: a stale reported age (the product forbids it); keys use age_of() = age from birth date at AS_OF


def age_of(alias: str = '') -> str:
    """Age in whole years at AS_OF from patient.birth_date (the decision context's rule; never age_latest)."""
    b = f'{alias}.birth_date' if alias else 'birth_date'
    return (f"(CAST(strftime('%Y', '{AS_OF}') AS INT) - CAST(strftime('%Y', {b}) AS INT) - "
            f"(strftime('%m-%d', '{AS_OF}') < strftime('%m-%d', {b})))")
A1C_POOR_CONTROL = 9.0                               # "uncontrolled / poor control" = latest HbA1c > 9.0 %
A1C_GOAL = 8.0                                       # "controlled" = latest HbA1c < 8.0 %
BP_CONTROL = (140, 90)                               # controlled = latest systolic < 140 AND latest diastolic < 90
# Heart-failure GDMT pillars; a patient is "on" a pillar with at least one fill of its classes in the last 12 months.
GDMT_PILLARS = {
    'beta_blocker': ['hf_evidence_based_beta_blocker'],
    'raas': ['ace_inhibitor', 'arb', 'arni'],
    'mra': ['mra'],
    'sglt2': ['sglt2_inhibitor'],
}


def filled_class_last_year(classes) -> str:
    """Patients with >= 1 fill in any of the drug classes in the last 12 months."""
    marks = ', '.join(f"'{c}'" for c in classes)
    return f"SELECT DISTINCT patient_id FROM fill WHERE drug_class IN ({marks}) AND {in_last_year('fill_date')}"


# Screening evidence (a result counts, a billed screening claim counts; colonoscopy and FIT both count for colorectal).
MAMMOGRAM_EVIDENCE = ("SELECT patient_id, event_date FROM cancer_screening WHERE test = 'bilateral_screening_mammography' "
                      "UNION ALL SELECT patient_id, service_date FROM procedure_line WHERE cpt = '77067' AND counts_in_metrics = 1")
COLORECTAL_EVIDENCE = ("SELECT patient_id, event_date FROM cancer_screening WHERE test = 'FIT' "
                       "UNION ALL SELECT patient_id, service_date FROM procedure_line WHERE cpt IN ('45378', '45380', '45385', '82274') AND counts_in_metrics = 1")
# Cervical cancer screening has NO evidence source in this warehouse (no Pap / HPV codes or results): a question about it is "not in the data".
ED_VISIT_GROUP = "SELECT patient_id, start_date, end_date, principal_icd10 FROM visit_group WHERE visit_class = 'emergency'"   # ED treat-and-release (not admitted)
