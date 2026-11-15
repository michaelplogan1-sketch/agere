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


# Inpatient stays with one row per hospital episode, in two steps.
# 1. One source writes a stay as two rows: the admission with no discharge date, and a same-day row on the day of discharge.
#    The same-day row is dropped and its date becomes the open row's discharge date (in every case with a discharge summary
#    that day, the summary says care began on the open row's admission date).
# 2. Rows of one patient that overlap, repeat, or begin on the day an earlier row ends (a second source's copy, or a transfer)
#    are one episode: first admission date to last discharge date. A readmission is counted between episodes, never inside one.
_STAYS = ("raw AS (SELECT s.patient_id, s.admit_date, COALESCE(s.discharge_date, (SELECT MIN(t.admit_date) FROM stay t WHERE t.patient_id = s.patient_id AND t.type = 'inpatient' "
          "AND t.discharge_date = t.admit_date AND t.admit_date > s.admit_date AND julianday(t.admit_date) - julianday(s.admit_date) <= 30)) AS discharge_date, s.organization_id "
          "FROM stay s WHERE s.type = 'inpatient' AND NOT (s.discharge_date = s.admit_date AND EXISTS (SELECT 1 FROM stay o WHERE o.patient_id = s.patient_id AND o.type = 'inpatient' "
          "AND o.discharge_date IS NULL AND o.admit_date < s.admit_date AND julianday(s.admit_date) - julianday(o.admit_date) <= 30))), "
          "fl AS (SELECT patient_id, admit_date, discharge_date, organization_id, CASE WHEN admit_date <= MAX(discharge_date) OVER (PARTITION BY patient_id "
          "ORDER BY admit_date, (discharge_date IS NULL), discharge_date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) THEN 0 ELSE 1 END AS starts FROM raw), "
          "gr AS (SELECT patient_id, admit_date, discharge_date, organization_id, SUM(starts) OVER (PARTITION BY patient_id "
          "ORDER BY admit_date, (discharge_date IS NULL), discharge_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS episode FROM fl), "
          "st AS (SELECT patient_id, MIN(admit_date) AS admit_date, MAX(discharge_date) AS discharge_date, 'inpatient' AS type, MIN(organization_id) AS organization_id "
          "FROM gr GROUP BY patient_id, episode), ")
# The readmission is the inpatient stay admitted on the given date (the row with a discharge date when a source repeats the
# stay); the stay before it is the latest one discharged in the 30 days before that date. Either may be missing.
_READMIT_CTE = ("WITH " + _STAYS + "re AS (SELECT admit_date, discharge_date, organization_id FROM st WHERE patient_id = :patient_id AND type = 'inpatient' AND admit_date = :readmit_date "
                "ORDER BY (discharge_date IS NULL), discharge_date DESC LIMIT 1), "
                "idx AS (SELECT admit_date, discharge_date, organization_id FROM st WHERE patient_id = :patient_id AND type = 'inpatient' AND discharge_date IS NOT NULL "
                "AND admit_date < :readmit_date AND discharge_date < :readmit_date AND julianday(:readmit_date) - julianday(discharge_date) <= 30 "
                "ORDER BY discharge_date DESC, admit_date LIMIT 1), "
                "w AS (SELECT (SELECT admit_date FROM re) AS readmit_admit, (SELECT discharge_date FROM re) AS readmit_discharge, (SELECT organization_id FROM re) AS readmit_org, "
                "(SELECT admit_date FROM idx) AS index_admit, (SELECT discharge_date FROM idx) AS index_discharge, (SELECT organization_id FROM idx) AS index_org) ")
DATE = r'(?P<readmit_date>20\d{2}-\d{2}-\d{2})'
# Each inpatient stay once (one row per patient and admission date, with its latest discharge date), discharged in the year, and
# whether an inpatient admission followed 1 to 30 days after that discharge.
_STAY_ONCE = ("WITH " + _STAYS + "s AS (SELECT patient_id, admit_date, MAX(discharge_date) AS discharge_date FROM st WHERE type = 'inpatient' AND discharge_date IS NOT NULL GROUP BY patient_id, admit_date), "
              "d AS (SELECT s.patient_id, s.admit_date, s.discharge_date, EXISTS (SELECT 1 FROM st b WHERE b.patient_id = s.patient_id AND b.type = 'inpatient' AND b.admit_date > s.admit_date "
              "AND julianday(b.admit_date) - julianday(s.discharge_date) BETWEEN 1 AND 30) AS readmitted FROM s WHERE substr(s.discharge_date, 1, 4) = :year) ")
_READMIT_PAIRS = ("WITH " + _STAYS + "pairs AS (SELECT b.patient_id, b.admit_date AS readmit_admit, MAX(a.discharge_date) AS index_discharge FROM st a JOIN st b ON a.patient_id = b.patient_id "
                  "WHERE a.type = 'inpatient' AND b.type = 'inpatient' AND a.discharge_date IS NOT NULL AND a.admit_date < b.admit_date "
                  "AND julianday(b.admit_date) - julianday(a.discharge_date) BETWEEN 1 AND 30 AND substr(b.admit_date, 1, 4) = :year GROUP BY b.patient_id, b.admit_date) ")
# Every readmission in a year with its two stays, the 7-day visit flag and every document from the prior admission to 30 days after the readmission.
_READMIT_DOCS = (_READMIT_PAIRS + ", c AS (SELECT p.patient_id, p.readmit_admit, p.index_discharge, "
               "(SELECT MAX(s.admit_date) FROM st s WHERE s.patient_id = p.patient_id AND s.type = 'inpatient' AND s.discharge_date = p.index_discharge) AS index_admit, "
               "(SELECT MAX(s.discharge_date) FROM st s WHERE s.patient_id = p.patient_id AND s.type = 'inpatient' AND s.admit_date = p.readmit_admit) AS readmit_discharge, "
               "EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = p.patient_id AND v.event_date > p.index_discharge AND v.event_date <= date(p.index_discharge, '+7 day') "
               "AND v.event_date < p.readmit_admit AND v.type NOT IN ('inpatient', 'emergency', 'observation', 'telephone', 'orders_only')) AS visit7 FROM pairs p) "
               "SELECT c.patient_id, c.readmit_admit, COALESCE(c.index_admit, '') AS index_admit, c.index_discharge, COALESCE(c.readmit_discharge, '') AS readmit_discharge, c.visit7, "
               "d.record_id, d.event_date, d.document_type FROM c LEFT JOIN document d ON d.patient_id = c.patient_id AND d.event_date >= COALESCE(c.index_admit, c.index_discharge) "
               "AND d.event_date <= date(COALESCE(c.readmit_discharge, c.readmit_admit), '+30 day') ORDER BY c.patient_id, c.readmit_admit, d.event_date, d.record_id")
# Members enrolled in the year, their attributed practice (latest month) and age at year end; then one row per member and
# measure they are eligible for, with gap = 1 when the measure is not met and a plain statement of what is missing.
_CARE_GAPS = ("en AS (SELECT m.patient_id, (SELECT x.attribution_practice_id FROM member_month x WHERE x.patient_id = m.patient_id AND substr(x.month, 1, 4) = :year "
              "ORDER BY x.month DESC LIMIT 1) AS practice, MAX(m.line_of_business = 'medicare' OR m.line_of_business = 'dual') AS medicare FROM member_month m WHERE substr(m.month, 1, 4) = :year GROUP BY m.patient_id), "
              "pe AS (SELECT en.*, p.sex_reported AS sex, CAST((julianday(:year || '-12-31') - julianday(p.birth_date)) / 365.25 AS INTEGER) AS age FROM en JOIN patient p ON p.patient_id = en.patient_id), "
              "dm AS (SELECT pe.patient_id, pe.practice, (SELECT x.value_canonical FROM measurement x WHERE x.patient_id = pe.patient_id AND x.concept = 'hba1c' AND substr(x.event_date, 1, 4) = :year "
              "ORDER BY x.event_date DESC LIMIT 1) AS a1c FROM pe WHERE pe.age BETWEEN 18 AND 75 AND EXISTS (SELECT 1 FROM condition c WHERE c.patient_id = pe.patient_id "
              "AND c.concept IN ('diabetes', 'type_2_diabetes', 'type_1_diabetes') AND COALESCE(c.event_date, '') <= :year || '-12-31')), "
              "g AS (SELECT 'diabetes_a1c' AS measure, patient_id, practice, CASE WHEN a1c IS NULL OR a1c > 9 THEN 1 ELSE 0 END AS gap, "
              "CASE WHEN a1c IS NULL THEN 'no HbA1c result in the year' WHEN a1c > 9 THEN 'latest HbA1c ' || a1c || ', above 9' ELSE '' END AS detail FROM dm "
              "UNION ALL SELECT 'statin_cardiovascular', pe.patient_id, pe.practice, CASE WHEN EXISTS (SELECT 1 FROM fill f WHERE f.patient_id = pe.patient_id AND f.drug_class = 'statin' "
              "AND substr(f.fill_date, 1, 4) = :year) THEN 0 ELSE 1 END, 'no statin fill in the year' FROM pe WHERE pe.age BETWEEN 21 AND 75 AND EXISTS (SELECT 1 FROM condition c "
              "WHERE c.patient_id = pe.patient_id AND c.concept IN ('coronary_artery_disease', 'myocardial_infarction') AND COALESCE(c.event_date, '') <= :year || '-12-31') "
              "UNION ALL SELECT 'breast_cancer_screening', pe.patient_id, pe.practice, CASE WHEN EXISTS (SELECT 1 FROM cancer_screening s WHERE s.patient_id = pe.patient_id "
              "AND s.test = 'bilateral_screening_mammography' AND s.event_date >= (CAST(:year AS INTEGER) - 2) || '-10-01' AND s.event_date <= :year || '-12-31') THEN 0 ELSE 1 END, "
              "'no screening mammogram since 1 October two years earlier' FROM pe WHERE pe.sex = 'F' AND pe.age BETWEEN 52 AND 74 "
              "UNION ALL SELECT 'annual_wellness_visit', pe.patient_id, pe.practice, CASE WHEN EXISTS (SELECT 1 FROM wellness_visit w WHERE w.patient_id = pe.patient_id AND w.awv = 1 "
              "AND substr(w.event_date, 1, 4) = :year) THEN 0 ELSE 1 END, 'no annual wellness visit in the year' FROM pe WHERE pe.medicare = 1)")
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
    'hf_stored_barrier_records': {
        'title': 'For one patient, barrier records already extracted from notes for guideline heart-failure medications (stored by the earlier extraction pipeline)',
        'params': {'patient_id': {'type': 'patient'}},
        'sql': "SELECT COALESCE(gdmt_pillar, drug_class) AS pillar_or_class, barrier_type, evidence_text, event_date, record_id, method AS extracted_by FROM therapy_barrier "
               "WHERE patient_id = :patient_id AND (gdmt_pillar IS NOT NULL OR drug_class IN ('ace_inhibitor', 'arb', 'arni', 'hf_evidence_based_beta_blocker', 'beta_blocker', 'mra', 'sglt2_inhibitor')) "
               "UNION ALL SELECT 'no stored record', 'none', 'The earlier extraction stored no barrier record for these medications', '', '', '' "
               "WHERE NOT EXISTS (SELECT 1 FROM therapy_barrier WHERE patient_id = :patient_id AND (gdmt_pillar IS NOT NULL OR drug_class IN "
               "('ace_inhibitor', 'arb', 'arni', 'hf_evidence_based_beta_blocker', 'beta_blocker', 'mra', 'sglt2_inhibitor'))) ORDER BY 4, 5",
        'patterns': [r"what barrier records are stored for patient (?P<patient_id>[a-z0-9-]+)'s guideline heart failure medications"]},
    'hf_medication_reasons_in_notes': {
        'title': 'For one patient, the reason each document states for a heart-failure medication not being taken, held, stopped or not started (each document is read)',
        'params': {'patient_id': {'type': 'patient'}},
        'sql': "SELECT record_id, event_date, source FROM document WHERE patient_id = :patient_id ORDER BY event_date, record_id",
        # Typed judgments on passages (reasons.py): code finds each passage about a pillar medication, System One picks the kind of reason.
        'read': {'statement': 'The passage states a reason a guideline heart failure medication is not being taken, was held, stopped, not started or not increased.',
                 'mode': 'medication_reasons'},
        'columns': ['pillar', 'medication', 'reason_kind', 'passage', 'first_date', 'last_date', 'documents'],
        'patterns': [r"what reason does each of patient (?P<patient_id>[a-z0-9-]+)'s documents give for a heart failure medication not being taken"]},
    # --- one patient's most recent 30-day readmission: the two stays, what happened between them, and what the record states ---
    'readmission_stays': {
        'title': "For one patient, the inpatient stay admitted on a given date and the stay discharged in the 30 days before it (discrete stay records)",
        'params': {'patient_id': {'type': 'patient'}, 'readmit_date': {'type': 'date'}},
        'sql': _READMIT_CTE + "SELECT 'stay before the readmission' AS stay, index_admit AS admit_date, index_discharge AS discharge_date, index_org AS organization_id, "
               "-1 AS days_after_prior_discharge FROM w WHERE index_admit IS NOT NULL UNION ALL "
               "SELECT 'readmission', readmit_admit, COALESCE(readmit_discharge, 'not recorded'), readmit_org, "
               "COALESCE(CAST(julianday(readmit_admit) - julianday(index_discharge) AS INTEGER), -1) FROM w WHERE readmit_admit IS NOT NULL ORDER BY 2",
        'grain': ['stay'],
        'patterns': [r"what are patient (?P<patient_id>[a-z0-9-]+)'s readmission on " + DATE + r" and the stay before it"]},
    'readmission_chart_statements': {
        'title': "For one patient, what the documents state about the admission on a given date and the stay before it: why admitted, discharge instructions, imaging impressions, and any advance directive or code status (each document is read)",
        'params': {'patient_id': {'type': 'patient'}, 'readmit_date': {'type': 'date'}},
        'sql': _READMIT_CTE + "SELECT d.record_id, d.event_date, d.source, d.document_type, COALESCE(w.index_admit, '') AS index_admit, COALESCE(w.index_discharge, '') AS index_discharge, "
               "w.readmit_admit, COALESCE(w.readmit_discharge, '') AS readmit_discharge FROM document d, w WHERE d.patient_id = :patient_id AND w.readmit_admit IS NOT NULL "
               "ORDER BY d.event_date, d.record_id",
        # Code finds each statement and assigns it to a stay (readmission.py); System One judges only the unlabelled admission sentences.
        'read': {'statement': 'The passage says why the patient was admitted to hospital.', 'mode': 'admission_reasons'},
        'columns': ['stay', 'stated_as', 'passage', 'record_id', 'event_date'],
        'patterns': [r"what do patient (?P<patient_id>[a-z0-9-]+)'s documents state about the admission on " + DATE + r" and the stay before it"]},
    'readmission_follow_up': {
        'title': "For one patient, what is on record between the prior discharge and the admission on a given date: an outpatient visit within 7 days, telephone contacts, emergency visits, appointments by status, pharmacy fills",
        'params': {'patient_id': {'type': 'patient'}, 'readmit_date': {'type': 'date'}},
        'sql': _READMIT_CTE +
               "SELECT 'outpatient visit within 7 days of discharge' AS item, CASE WHEN COUNT(*) > 0 THEN 'yes' ELSE 'no' END AS on_record, COALESCE(MIN(v.event_date), '') AS first_date "
               "FROM visit v, w WHERE v.patient_id = :patient_id AND v.event_date > w.index_discharge AND v.event_date <= date(w.index_discharge, '+7 day') AND v.event_date < w.readmit_admit "
               "AND v.type NOT IN ('inpatient', 'emergency', 'observation', 'telephone', 'orders_only') "
               "UNION ALL SELECT 'outpatient visits between the stays', CAST(COUNT(*) AS TEXT), COALESCE(MIN(v.event_date), '') FROM visit v, w WHERE v.patient_id = :patient_id "
               "AND v.event_date > w.index_discharge AND v.event_date < w.readmit_admit AND v.type NOT IN ('inpatient', 'emergency', 'observation', 'telephone', 'orders_only') "
               "UNION ALL SELECT 'telephone contacts between the stays', CAST(COUNT(*) AS TEXT), COALESCE(MIN(v.event_date), '') FROM visit v, w WHERE v.patient_id = :patient_id "
               "AND v.event_date > w.index_discharge AND v.event_date < w.readmit_admit AND v.type = 'telephone' "
               "UNION ALL SELECT 'emergency visits between the stays', CAST(COUNT(*) AS TEXT), COALESCE(MIN(v.event_date), '') FROM visit v, w WHERE v.patient_id = :patient_id "
               "AND v.event_date > w.index_discharge AND v.event_date < w.readmit_admit AND v.type = 'emergency' "
               "UNION ALL SELECT 'appointments between the stays: ' || a.final_status, CAST(COUNT(*) AS TEXT), MIN(a.scheduled_date) FROM appointment a, w WHERE a.patient_id = :patient_id "
               "AND a.scheduled_date > w.index_discharge AND a.scheduled_date < w.readmit_admit GROUP BY a.final_status "
               "UNION ALL SELECT 'pharmacy fills between the stays', CAST(COUNT(*) AS TEXT), COALESCE(MIN(f.fill_date), '') FROM fill f, w WHERE f.patient_id = :patient_id "
               "AND f.fill_date > w.index_discharge AND f.fill_date < w.readmit_admit ORDER BY 1",
        'grain': ['item'],
        'patterns': [r"what follow-up is on record for patient (?P<patient_id>[a-z0-9-]+) between the prior discharge and the admission on " + DATE]},
    'readmission_risk_factors': {
        'title': "For one patient, recorded risk factors as of the admission on a given date: age, coverage, chronic conditions, inpatient admissions in the prior 12 months, emergency visits in the prior 6 months, length of the prior stay, medication classes filled, positive social-need screens, interpreter need",
        'params': {'patient_id': {'type': 'patient'}, 'readmit_date': {'type': 'date'}},
        'sql': _READMIT_CTE +
               "SELECT 'age' AS factor, CAST(age_latest AS TEXT) AS on_record FROM patient WHERE patient_id = :patient_id "
               "UNION ALL SELECT 'coverage', COALESCE(coverage_type, 'not recorded') FROM patient WHERE patient_id = :patient_id "
               "UNION ALL SELECT 'interpreter needed', CASE WHEN interpreter_needed IN (1, '1', 'true', 'yes') THEN 'yes' ELSE 'no' END FROM patient WHERE patient_id = :patient_id "
               "UNION ALL SELECT 'chronic conditions on record', CAST(COUNT(DISTINCT concept) AS TEXT) FROM condition WHERE patient_id = :patient_id AND chronic = 1 AND concept IS NOT NULL "
               "UNION ALL SELECT 'chronic condition: ' || concept, MIN(COALESCE(event_date, '')) FROM condition WHERE patient_id = :patient_id AND chronic = 1 AND concept IS NOT NULL GROUP BY concept "
               "UNION ALL SELECT 'inpatient admissions in the prior 12 months', CAST(COUNT(DISTINCT s.admit_date) AS TEXT) FROM st s WHERE s.patient_id = :patient_id AND s.type = 'inpatient' "
               "AND s.admit_date < :readmit_date AND s.admit_date >= date(:readmit_date, '-365 day') "
               "UNION ALL SELECT 'emergency visits in the prior 6 months', CAST(COUNT(DISTINCT v.event_date) AS TEXT) FROM visit v WHERE v.patient_id = :patient_id AND v.type = 'emergency' "
               "AND v.event_date < :readmit_date AND v.event_date >= date(:readmit_date, '-183 day') "
               "UNION ALL SELECT 'length of the prior stay in days', COALESCE(CAST(CAST(julianday(index_discharge) - julianday(index_admit) AS INTEGER) AS TEXT), 'no prior stay') FROM w "
               "UNION ALL SELECT 'medication classes filled in the prior 120 days', CAST(COUNT(DISTINCT f.drug_class) AS TEXT) FROM fill f WHERE f.patient_id = :patient_id AND f.drug_class IS NOT NULL "
               "AND f.fill_date < :readmit_date AND f.fill_date >= date(:readmit_date, '-120 day') "
               "UNION ALL SELECT 'positive social-need screen: ' || n.need, MAX(n.event_date) FROM social_need n WHERE n.patient_id = :patient_id AND n.positive = 1 AND n.event_date <= :readmit_date GROUP BY n.need "
               "ORDER BY 1",
        'grain': ['factor'],
        'patterns': [r"what risk factors are on record for patient (?P<patient_id>[a-z0-9-]+) as of the admission on " + DATE]},
    # --- Bridge Call (a day-2 call after discharge). The queue and the call sheet are recipes; the agent's rules are code in
    # clinical_core/agere/tiered/bridge_call.py. Nothing here contacts anyone. Both show only what was on record by the call
    # date: a condition, appointment or fill recorded later is not on the sheet.
    'bridge_call_queue': {
        'title': "Patients due a day-2 call on a given date: each hospital stay that ended two days earlier, the patient alive and not back in hospital, with what is on record since discharge (outpatient visits, telephone contacts, pharmacy fills, the next appointment)",
        'params': {'call_date': {'type': 'date'}},
        'sql': "WITH " + _STAYS + "due AS (SELECT s.patient_id, s.admit_date, s.discharge_date FROM st s WHERE s.discharge_date = date(:call_date, '-2 day') "
               "AND NOT EXISTS (SELECT 1 FROM death d WHERE d.patient_id = s.patient_id AND d.death_date <= :call_date) "
               "AND NOT EXISTS (SELECT 1 FROM st b WHERE b.patient_id = s.patient_id AND b.admit_date > s.admit_date AND b.admit_date <= :call_date)) "
               "SELECT due.patient_id, due.admit_date, due.discharge_date, CAST(julianday(due.discharge_date) - julianday(due.admit_date) AS INTEGER) AS days_in_hospital, "
               "COALESCE(p.preferred_language, 'not recorded') AS preferred_language, CASE WHEN p.interpreter_needed IN (1, '1', 'true', 'yes') THEN 'yes' ELSE 'no' END AS interpreter_needed, "
               "(SELECT COUNT(*) FROM st x WHERE x.patient_id = due.patient_id AND x.admit_date < due.admit_date AND x.admit_date >= date(due.admit_date, '-365 day')) AS admissions_in_prior_12_months, "
               "(SELECT COUNT(*) FROM visit v WHERE v.patient_id = due.patient_id AND v.event_date > due.discharge_date AND v.event_date <= :call_date AND v.type NOT IN ('inpatient', 'emergency', 'observation', 'orders_only')) AS contacts_since_discharge, "
               "(SELECT COUNT(*) FROM fill f WHERE f.patient_id = due.patient_id AND f.fill_date >= due.discharge_date AND f.fill_date <= :call_date) AS fills_since_discharge, "
               "COALESCE((SELECT MIN(a.scheduled_date) FROM appointment a WHERE a.patient_id = due.patient_id AND a.scheduled_date > due.discharge_date AND COALESCE(a.booked_date, a.scheduled_date) <= :call_date), 'none on record') AS next_appointment "
               "FROM due LEFT JOIN patient p ON p.patient_id = due.patient_id ORDER BY admissions_in_prior_12_months DESC, due.patient_id",
        'grain': ['patient_id'],
        'empty': 'No patient is due a day-2 call on that date',
        'patterns': [r'who is due a bridge call on (?P<call_date>20\d{2}-\d{2}-\d{2})', r'which patients are due a (?:day[- ]2|bridge|post[- ]discharge) call on (?P<call_date>20\d{2}-\d{2}-\d{2})']},
    'bridge_call_sheet': {
        'title': "For one patient discharged on a given date, what a caller needs on day 2: the stay, language and interpreter need, coverage, age, prior admissions, chronic conditions, positive social-need screens, pharmacy fills and contacts since discharge, and the next appointment",
        'params': {'patient_id': {'type': 'patient'}, 'discharge_date': {'type': 'date'}},
        'sql': "WITH " + _STAYS + "s AS (SELECT admit_date, discharge_date FROM st WHERE patient_id = :patient_id AND discharge_date = :discharge_date ORDER BY admit_date DESC LIMIT 1) "
               "SELECT 'stay' AS item, COALESCE((SELECT admit_date || ' to ' || discharge_date FROM s), 'no stay ending on that date') AS on_record "
               "UNION ALL SELECT 'age', CAST(age_latest AS TEXT) FROM patient WHERE patient_id = :patient_id "
               "UNION ALL SELECT 'coverage', COALESCE(coverage_type, 'not recorded') FROM patient WHERE patient_id = :patient_id "
               "UNION ALL SELECT 'preferred language', COALESCE(preferred_language, 'not recorded') FROM patient WHERE patient_id = :patient_id "
               "UNION ALL SELECT 'interpreter needed', CASE WHEN interpreter_needed IN (1, '1', 'true', 'yes') THEN 'yes' ELSE 'no' END FROM patient WHERE patient_id = :patient_id "
               "UNION ALL SELECT 'inpatient admissions in the prior 12 months', CAST(COUNT(*) AS TEXT) FROM st x, s WHERE x.patient_id = :patient_id AND x.admit_date < s.admit_date AND x.admit_date >= date(s.admit_date, '-365 day') "
               "UNION ALL SELECT 'chronic condition: ' || concept, MIN(COALESCE(event_date, '')) FROM condition WHERE patient_id = :patient_id AND chronic = 1 AND concept IS NOT NULL AND COALESCE(event_date, '') <= date(:discharge_date, '+2 day') GROUP BY concept "
               "UNION ALL SELECT 'positive social-need screen: ' || n.need, MAX(n.event_date) FROM social_need n WHERE n.patient_id = :patient_id AND n.positive = 1 AND n.event_date <= date(:discharge_date, '+2 day') GROUP BY n.need "
               "UNION ALL SELECT 'pharmacy fills since discharge (to day 2)', CAST(COUNT(*) AS TEXT) FROM fill f WHERE f.patient_id = :patient_id AND f.fill_date >= :discharge_date AND f.fill_date <= date(:discharge_date, '+2 day') "
               "UNION ALL SELECT 'medication classes filled in the 120 days before admission', CAST(COUNT(DISTINCT f.drug_class) AS TEXT) FROM fill f, s WHERE f.patient_id = :patient_id AND f.drug_class IS NOT NULL "
               "AND f.fill_date < s.admit_date AND f.fill_date >= date(s.admit_date, '-120 day') "
               "UNION ALL SELECT 'visits or telephone contacts since discharge (to day 2)', CAST(COUNT(*) AS TEXT) FROM visit v WHERE v.patient_id = :patient_id AND v.event_date > :discharge_date "
               "AND v.event_date <= date(:discharge_date, '+2 day') AND v.type NOT IN ('inpatient', 'emergency', 'observation', 'orders_only') "
               "UNION ALL SELECT 'next appointment', COALESCE((SELECT MIN(a.scheduled_date) FROM appointment a WHERE a.patient_id = :patient_id AND a.scheduled_date > :discharge_date "
               "AND COALESCE(a.booked_date, a.scheduled_date) <= date(:discharge_date, '+2 day')), 'none on record') ORDER BY 1",
        'grain': ['item'],
        'patterns': [r"what is on the (?:bridge )?call sheet for patient (?P<patient_id>[a-z0-9-]+) discharged on (?P<discharge_date>20\d{2}-\d{2}-\d{2})"]},
    # --- ED Second Look and Facility Line. The trigger lists and the screens are recipes; the rules are code in
    # clinical_core/agere/tiered/second_look.py. Both show only what was on record by the date asked about.
    'ed_return_arrivals': {
        'title': "Emergency department arrivals on a given date by patients discharged from hospital in the previous 30 days: days since discharge, where the stay discharged them to, and what is on record since (contacts, pharmacy fills, the next appointment)",
        'params': {'arrival_date': {'type': 'date'}},
        'sql': "WITH " + _STAYS + "ed AS (SELECT DISTINCT patient_id FROM utilization_event WHERE kind = 'ed_visit' AND event_date = :arrival_date), "
               "last AS (SELECT s.patient_id, MAX(s.discharge_date) AS discharge_date FROM st s JOIN ed ON ed.patient_id = s.patient_id "
               "WHERE s.discharge_date < :arrival_date AND s.discharge_date >= date(:arrival_date, '-30 day') GROUP BY s.patient_id) "
               "SELECT last.patient_id, last.discharge_date, CAST(julianday(:arrival_date) - julianday(last.discharge_date) AS INTEGER) AS days_since_discharge, "
               "COALESCE((SELECT a.discharge_disposition FROM adt_visit a WHERE a.patient_id = last.patient_id AND a.discharge_date = last.discharge_date AND a.discharge_disposition IS NOT NULL LIMIT 1), 'not recorded') AS discharged_to, "
               "(SELECT COUNT(*) FROM visit v WHERE v.patient_id = last.patient_id AND v.event_date > last.discharge_date AND v.event_date < :arrival_date AND v.type NOT IN ('inpatient', 'emergency', 'observation', 'orders_only')) AS contacts_since_discharge, "
               "(SELECT COUNT(*) FROM fill f WHERE f.patient_id = last.patient_id AND f.fill_date >= last.discharge_date AND f.fill_date <= :arrival_date) AS fills_since_discharge, "
               "COALESCE((SELECT MIN(a.scheduled_date) FROM appointment a WHERE a.patient_id = last.patient_id AND a.scheduled_date >= :arrival_date AND COALESCE(a.booked_date, a.scheduled_date) <= :arrival_date), 'none on record') AS next_appointment "
               "FROM last WHERE NOT EXISTS (SELECT 1 FROM death d WHERE d.patient_id = last.patient_id AND d.death_date < :arrival_date) "
               "AND NOT EXISTS (SELECT 1 FROM st h WHERE h.patient_id = last.patient_id AND h.admit_date < :arrival_date AND (h.discharge_date IS NULL OR h.discharge_date >= :arrival_date)) "
               "ORDER BY days_since_discharge, last.patient_id",
        'grain': ['patient_id'],
        'empty': 'No patient discharged in the previous 30 days arrived in the emergency department on that date',
        'patterns': [r'which recently discharged patients (?:arrived|are) in the emergency department on ' + DATE.replace('readmit_date', 'arrival_date'),
                     r'who (?:came|is) back (?:to|in) the emergency department on ' + DATE.replace('readmit_date', 'arrival_date')]},
    'facility_census': {
        'title': "Patients in a skilled nursing or rehabilitation facility after a hospital discharge, on a given date: discharged to a facility in the previous 30 days, alive, and not readmitted since",
        'params': {'census_date': {'type': 'date'}},
        'sql': "WITH " + _STAYS + "f AS (SELECT s.patient_id, s.admit_date, s.discharge_date, a.discharge_disposition FROM st s JOIN adt_visit a ON a.patient_id = s.patient_id AND a.discharge_date = s.discharge_date "
               "WHERE a.discharge_disposition IN ('snf', 'irf') AND s.discharge_date <= :census_date AND s.discharge_date >= date(:census_date, '-30 day') "
               "AND NOT EXISTS (SELECT 1 FROM st b WHERE b.patient_id = s.patient_id AND b.admit_date > s.admit_date AND b.admit_date <= :census_date) "
               "AND NOT EXISTS (SELECT 1 FROM death d WHERE d.patient_id = s.patient_id AND d.death_date <= :census_date)) "
               "SELECT DISTINCT f.patient_id, f.discharge_date, CASE f.discharge_disposition WHEN 'snf' THEN 'skilled nursing facility' ELSE 'rehabilitation facility' END AS facility, "
               "CAST(julianday(:census_date) - julianday(f.discharge_date) AS INTEGER) AS days_in_facility, "
               "(SELECT COUNT(*) FROM st x WHERE x.patient_id = f.patient_id AND x.admit_date < f.admit_date AND x.admit_date >= date(f.admit_date, '-365 day')) AS admissions_in_prior_12_months "
               "FROM f ORDER BY days_in_facility, f.patient_id",
        'grain': ['patient_id'],
        'empty': 'No patient is in a facility after a hospital discharge on that date',
        'patterns': [r'which patients are in a (?:nursing |rehabilitation |post[- ]acute )?facility after discharge on ' + DATE.replace('readmit_date', 'census_date'),
                     r'who is on the facility line (?:list|census) on ' + DATE.replace('readmit_date', 'census_date')]},
    # --- Value-based care. Cost per member per month, the attributed panel, high-cost members and measure-style care gaps.
    # The measures follow the shape of common quality measures but are simplified to what this warehouse holds and are not
    # certified measure results; each title states the rule. Spend is payer paid amounts on claims that count in metrics.
    'pmpm_by_line_of_business': {
        'title': "Cost per member per month in one year by line of business: member months, members, payer paid dollars on claims that count in metrics, and paid per member per month",
        'params': {'year': {'type': 'year'}},
        'sql': "WITH mm AS (SELECT line_of_business, COUNT(*) AS member_months, COUNT(DISTINCT patient_id) AS members FROM member_month WHERE substr(month, 1, 4) = :year GROUP BY 1), "
               "paid AS (SELECT c.line_of_business, SUM(c.paid_cents) AS paid_cents FROM payer_claim c WHERE c.counts_in_metrics = 1 AND substr(c.service_from, 1, 4) = :year GROUP BY 1) "
               "SELECT mm.line_of_business, mm.member_months, mm.members, ROUND(COALESCE(paid.paid_cents, 0) / 100.0, 2) AS paid_dollars, "
               "ROUND(COALESCE(paid.paid_cents, 0) / 100.0 / mm.member_months, 2) AS paid_pmpm FROM mm LEFT JOIN paid ON paid.line_of_business = mm.line_of_business ORDER BY mm.member_months DESC",
        'grain': ['line_of_business'],
        'patterns': [r'what (?:was|is) the (?:cost|spend|paid amount) per member per month (?:in|for) ' + YEAR + r'(?: by line of business)?', r'what (?:was|is) the pmpm (?:in|for) ' + YEAR + r'(?: by line of business)?']},
    'attributed_panel_by_practice': {
        'title': "Each attributed practice's panel in one year: members, member months, payer paid per member per month, inpatient admissions and emergency visits per 1,000 member-years (claims and events in the months the member was attributed to that practice)",
        'params': {'year': {'type': 'year'}},
        'sql': "WITH mm AS (SELECT patient_id, month, attribution_practice_id AS practice FROM member_month WHERE substr(month, 1, 4) = :year AND attribution_practice_id IS NOT NULL), "
               "paid AS (SELECT mm.practice, SUM(c.paid_cents) AS cents FROM payer_claim c JOIN mm ON mm.patient_id = c.patient_id AND mm.month = substr(c.service_from, 1, 7) "
               "WHERE c.counts_in_metrics = 1 GROUP BY 1), "
               "ev AS (SELECT mm.practice, SUM(u.kind = 'inpatient') AS admissions, SUM(u.kind = 'ed_visit') AS ed_visits FROM utilization_event u JOIN mm ON mm.patient_id = u.patient_id "
               "AND mm.month = substr(u.event_date, 1, 7) GROUP BY 1), "
               "p AS (SELECT practice, COUNT(DISTINCT patient_id) AS members, COUNT(*) AS member_months FROM mm GROUP BY 1) "
               "SELECT p.practice AS attribution_practice_id, p.members, p.member_months, ROUND(COALESCE(paid.cents, 0) / 100.0 / p.member_months, 2) AS paid_pmpm, "
               "ROUND(1000.0 * 12 * COALESCE(ev.admissions, 0) / p.member_months, 1) AS admissions_per_1000, ROUND(1000.0 * 12 * COALESCE(ev.ed_visits, 0) / p.member_months, 1) AS ed_visits_per_1000 "
               "FROM p LEFT JOIN paid ON paid.practice = p.practice LEFT JOIN ev ON ev.practice = p.practice ORDER BY p.member_months DESC, p.practice",
        'grain': ['attribution_practice_id'],
        'patterns': [r'(?:show|what is) (?:each|every) (?:attributed )?practice(?:\'s)? panel (?:in|for) ' + YEAR, r'how do (?:the )?attributed practices compare (?:in|for) ' + YEAR + r'(?: on cost and utili[sz]ation)?']},
    'high_cost_members': {
        'title': "The highest-cost members in one year: payer paid dollars on claims that count in metrics, with their attributed practice, chronic conditions on record, inpatient admissions and emergency visits that year",
        'params': {'year': {'type': 'year'}, 'n': {'type': 'int', 'min': 1, 'max': 500}},
        'sql': "WITH paid AS (SELECT patient_id, SUM(paid_cents) AS cents FROM payer_claim WHERE counts_in_metrics = 1 AND substr(service_from, 1, 4) = :year GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT :n) "
               "SELECT paid.patient_id, ROUND(paid.cents / 100.0, 2) AS paid_dollars, "
               "COALESCE((SELECT m.attribution_practice_id FROM member_month m WHERE m.patient_id = paid.patient_id AND substr(m.month, 1, 4) = :year ORDER BY m.month DESC LIMIT 1), 'not attributed') AS attribution_practice_id, "
               "(SELECT COUNT(DISTINCT concept) FROM condition c WHERE c.patient_id = paid.patient_id AND c.chronic IS NOT 0 AND c.concept IN (SELECT concept FROM condition WHERE chronic = 1) "
               "AND COALESCE(c.event_date, '') <= :year || '-12-31') AS chronic_conditions, "
               "(SELECT COUNT(*) FROM utilization_event u WHERE u.patient_id = paid.patient_id AND u.kind = 'inpatient' AND substr(u.event_date, 1, 4) = :year) AS admissions, "
               "(SELECT COUNT(*) FROM utilization_event u WHERE u.patient_id = paid.patient_id AND u.kind = 'ed_visit' AND substr(u.event_date, 1, 4) = :year) AS ed_visits "
               "FROM paid ORDER BY paid.cents DESC, paid.patient_id",
        'grain': ['patient_id'],
        'patterns': [r'who (?:were|are) the (?:top )?(?P<n>\d{1,3}) (?:highest[- ]cost|most expensive) members (?:in|for) ' + YEAR, r'list the (?:top )?(?P<n>\d{1,3}) (?:highest[- ]cost|most expensive) members (?:in|for) ' + YEAR]},
    'care_gaps_summary': {
        'title': "Measure-style care gaps in one year among members enrolled that year (simplified, not certified measure results): diabetes with no HbA1c in the year or the latest above 9 (ages 18 to 75); coronary disease or heart attack with no statin fill in the year (ages 21 to 75); women 52 to 74 with no screening mammogram from 1 October two years earlier to year end; Medicare members with no annual wellness visit in the year",
        'params': {'year': {'type': 'year'}},
        'sql': "WITH " + _CARE_GAPS + " SELECT measure, COUNT(*) AS eligible, SUM(gap = 0) AS met, SUM(gap) AS gaps, ROUND(1.0 * SUM(gap = 0) / COUNT(*), 4) AS rate_met FROM g GROUP BY measure ORDER BY measure",
        'grain': ['measure'],
        'patterns': [r'what are (?:the |our )?care gaps (?:in|for) ' + YEAR, r'how are we doing on quality measures (?:in|for) ' + YEAR, r'summari[sz]e (?:the )?care gaps (?:in|for) ' + YEAR]},
    'care_gaps_by_practice': {
        'title': "Measure-style care gaps in one year by attributed practice (same simplified rules as the care-gap summary): eligible members, gaps and rate met for each measure and practice",
        'params': {'year': {'type': 'year'}},
        'sql': "WITH " + _CARE_GAPS + " SELECT practice AS attribution_practice_id, measure, COUNT(*) AS eligible, SUM(gap) AS gaps, ROUND(1.0 * SUM(gap = 0) / COUNT(*), 4) AS rate_met FROM g "
               "GROUP BY practice, measure ORDER BY measure, gaps DESC, practice",
        'grain': ['attribution_practice_id', 'measure'],
        'patterns': [r'(?:show )?care gaps by (?:attributed )?practice (?:in|for) ' + YEAR, r'which practices have the most care gaps (?:in|for) ' + YEAR]},
    'care_gap_list': {
        'title': "The members with an open care gap in one year for one measure (diabetes_a1c, statin_cardiovascular, breast_cancer_screening or annual_wellness_visit), one row each with their attributed practice and what is missing: the worklist behind the care-gap summary",
        'params': {'year': {'type': 'year'}, 'measure': {'type': 'enum', 'values': ['diabetes_a1c', 'statin_cardiovascular', 'breast_cancer_screening', 'annual_wellness_visit']}},
        'sql': "WITH " + _CARE_GAPS + " SELECT patient_id, practice AS attribution_practice_id, detail AS what_is_missing FROM g WHERE gap = 1 AND measure = :measure ORDER BY practice, patient_id",
        'grain': ['patient_id'],
        'empty': 'No member has an open gap for that measure in that year',
        'patterns': [r'(?:list|show|who has) (?:the )?(?:open )?(?P<measure>diabetes_a1c|statin_cardiovascular|breast_cancer_screening|annual_wellness_visit) gaps? (?:in|for) ' + YEAR]},
    'readmission_labs': {
        'title': "For one patient, lab and vital results by period around the admission on a given date: the stay before it, between the stays, and the readmission (count, lowest, highest, last date)",
        'params': {'patient_id': {'type': 'patient'}, 'readmit_date': {'type': 'date'}},
        'sql': _READMIT_CTE + ", m AS (SELECT x.concept, x.value_canonical, x.event_date, CASE "
               "WHEN x.event_date >= w.readmit_admit AND x.event_date <= date(COALESCE(w.readmit_discharge, w.readmit_admit), '+1 day') THEN 'readmission' "
               "WHEN x.event_date >= w.index_admit AND x.event_date <= w.index_discharge THEN 'stay before the readmission' "
               "WHEN x.event_date > w.index_discharge AND x.event_date < w.readmit_admit THEN 'between the two stays' END AS period "
               "FROM measurement x, w WHERE x.patient_id = :patient_id AND x.concept IS NOT NULL AND w.readmit_admit IS NOT NULL) "
               "SELECT period, concept, COUNT(*) AS results, MIN(value_canonical) AS lowest, MAX(value_canonical) AS highest, MAX(event_date) AS last_date FROM m WHERE period IS NOT NULL GROUP BY 1, 2 "
               "UNION ALL SELECT 'readmission', '(any lab or vital)', COUNT(*), NULL, NULL, COALESCE(MAX(event_date), '') FROM m WHERE period = 'readmission' ORDER BY 1, 2",
        'grain': ['period', 'concept'], 'nullable': ('lowest', 'highest'),
        'patterns': [r"what lab and vital results are on record for patient (?P<patient_id>[a-z0-9-]+) around the admission on " + DATE]},
    # --- every readmission in a year: the committee view. A readmission is an inpatient stay beginning 1 to 30 days after an
    #     earlier inpatient discharge; a stay recorded twice on the same day is not one. ---
    'readmission_review_summary': {
        'title': "Readmissions in one year (an inpatient stay beginning 1 to 30 days after an earlier inpatient discharge): how many, and how many had an outpatient visit within 7 days of the prior discharge, no contact at all between the stays, came back within 7 days, had two or more prior admissions in 12 months, or a positive social-need screen",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_PAIRS + ", f AS (SELECT p.*, "
               "EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = p.patient_id AND v.event_date > p.index_discharge AND v.event_date <= date(p.index_discharge, '+7 day') "
               "AND v.event_date < p.readmit_admit AND v.type NOT IN ('inpatient', 'emergency', 'observation', 'telephone', 'orders_only')) AS visit7, "
               "EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = p.patient_id AND v.event_date > p.index_discharge AND v.event_date < p.readmit_admit "
               "AND v.type NOT IN ('inpatient', 'emergency', 'observation', 'orders_only')) AS any_contact, "
               "(SELECT COUNT(DISTINCT s.admit_date) FROM st s WHERE s.patient_id = p.patient_id AND s.type = 'inpatient' AND s.admit_date < p.readmit_admit "
               "AND s.admit_date >= date(p.readmit_admit, '-365 day')) AS prior12, "
               "EXISTS (SELECT 1 FROM social_need n WHERE n.patient_id = p.patient_id AND n.positive = 1 AND n.event_date <= p.readmit_admit) AS social, "
               "CAST(julianday(p.readmit_admit) - julianday(p.index_discharge) AS INTEGER) AS days FROM pairs p) "
               "SELECT 'readmissions' AS finding, COUNT(*) AS readmissions, 1.0 AS share FROM f "
               "UNION ALL SELECT 'patients readmitted', COUNT(DISTINCT patient_id), NULL FROM f "
               "UNION ALL SELECT 'outpatient visit within 7 days of the prior discharge', SUM(visit7), ROUND(1.0 * SUM(visit7) / COUNT(*), 4) FROM f "
               "UNION ALL SELECT 'no outpatient visit within 7 days', SUM(1 - visit7), ROUND(1.0 * SUM(1 - visit7) / COUNT(*), 4) FROM f "
               "UNION ALL SELECT 'no visit or telephone contact at all between the stays', SUM(1 - any_contact), ROUND(1.0 * SUM(1 - any_contact) / COUNT(*), 4) FROM f "
               "UNION ALL SELECT 'readmitted within 7 days of discharge', SUM(days <= 7), ROUND(1.0 * SUM(days <= 7) / COUNT(*), 4) FROM f "
               "UNION ALL SELECT 'two or more inpatient admissions in the prior 12 months', SUM(prior12 >= 2), ROUND(1.0 * SUM(prior12 >= 2) / COUNT(*), 4) FROM f "
               "UNION ALL SELECT 'a positive social-need screen on record', SUM(social), ROUND(1.0 * SUM(social) / COUNT(*), 4) FROM f",
        'patterns': [r'summari[sz]e (?:the |our )?readmissions (?:in|for) ' + YEAR, r'what do (?:the |our )?readmissions (?:in|for) ' + YEAR + r' have in common']},
    'readmission_documented_patterns': {
        'title': "What the charts state across every readmission in one year: how many have a stated reason, whether it is the same problem as the stay before, whether the prior discharge summary gave a follow-up instruction and whether a visit followed, and the most common stated reasons (every document around each readmission is read by code)",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_DOCS,
        'read': {'statement': 'The document states why the patient was admitted to hospital.', 'mode': 'readmission_patterns'},
        'columns': ['finding', 'readmissions', 'share'],
        'patterns': [r'what do the charts say about (?:the |our )?readmissions (?:in|for) ' + YEAR, r'why were patients readmitted (?:in|during) ' + YEAR,
                     r'what reasons do the charts give for (?:the |our )?readmissions (?:in|for) ' + YEAR]},
    'readmission_case_list': {
        'title': "Every readmission in one year, one row each: the reason its chart states, the problem group, whether it is the same problem as the stay before, the follow-up instruction given and whether a visit followed within 7 days (documents read by code)",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_DOCS,
        'read': {'statement': 'The document states why the patient was admitted to hospital.', 'mode': 'readmission_cases'},
        'columns': ['patient_id', 'prior_discharge_date', 'readmission_date', 'days_between', 'stated_reason', 'problem_group', 'compared_with_prior_stay',
                    'follow_up_instruction', 'outpatient_visit_within_7_days'],
        'patterns': [r'list (?:the |every |all )?readmissions (?:in|for) ' + YEAR + r'(?: with what (?:the|their) charts? (?:state|say)s?)?', r'show (?:me )?(?:the |every |all )?readmissions (?:in|for) ' + YEAR + r' (?:one by one|case by case)']},
    'readmissions_instruction_no_visit': {
        'title': "Readmissions in one year where the prior discharge summary gave a follow-up instruction and no outpatient visit followed within 7 days, one row each with the instruction quoted",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_DOCS,
        'read': {'statement': 'The document states why the patient was admitted to hospital.', 'mode': 'readmission_cases', 'only': 'instruction_no_visit'},
        'columns': ['patient_id', 'prior_discharge_date', 'readmission_date', 'days_between', 'stated_reason', 'problem_group', 'compared_with_prior_stay',
                    'follow_up_instruction', 'outpatient_visit_within_7_days'],
        'patterns': [r'which readmissions (?:in|for) ' + YEAR + r' had a follow[- ]up instruction (?:but|and) no (?:outpatient )?visit(?: within 7 days)?']},
    'readmissions_same_problem': {
        'title': "Readmissions in one year whose stated reason is in the same problem group as the stay before, one row each",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_DOCS,
        'read': {'statement': 'The document states why the patient was admitted to hospital.', 'mode': 'readmission_cases', 'only': 'same_problem'},
        'columns': ['patient_id', 'prior_discharge_date', 'readmission_date', 'days_between', 'stated_reason', 'problem_group', 'compared_with_prior_stay',
                    'follow_up_instruction', 'outpatient_visit_within_7_days'],
        'patterns': [r'which readmissions (?:in|for) ' + YEAR + r' were for the same problem as the (?:stay|admission) before']},
    'readmissions_no_stated_reason': {
        'title': "Readmissions in one year for which no document states why the patient was admitted, one row each",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_DOCS,
        'read': {'statement': 'The document states why the patient was admitted to hospital.', 'mode': 'readmission_cases', 'only': 'no_stated_reason'},
        'columns': ['patient_id', 'prior_discharge_date', 'readmission_date', 'days_between', 'stated_reason', 'problem_group', 'compared_with_prior_stay',
                    'follow_up_instruction', 'outpatient_visit_within_7_days'],
        'patterns': [r'which readmissions (?:in|for) ' + YEAR + r' have no (?:stated|documented) reason']},
    'readmission_drivers_summary': {
        'title': "What else is on record that drives a return, across every readmission in one year: advanced illness, social need, an emergency note saying the workup was unremarkable and the patient was admitted, discharge to a nursing or rehabilitation facility, and whether the patient could manage at home (markers on record, counted by code; how each is marked is stated beside it)",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_DOCS,
        'read': {'statement': 'The record carries a marker of what drove the return.', 'mode': 'readmission_drivers'},
        'columns': ['finding', 'readmissions', 'share', 'how_it_is_marked'],
        'patterns': [r'what (?:is driving|drives|drove) (?:the |our )?readmissions (?:in|for) ' + YEAR, r'why are patients (?:really )?coming back (?:in|during) ' + YEAR]},
    'readmission_driver_list': {
        'title': "Every readmission in one year, one row each, with where the stay before discharged the patient to and which of five drivers the record marks: advanced illness, social need, emergency department threshold, sent back from a facility, self-care at home",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_DOCS,
        'read': {'statement': 'The record carries a marker of what drove the return.', 'mode': 'readmission_driver_cases'},
        'columns': ['patient_id', 'prior_discharge_date', 'readmission_date', 'prior_discharge_to', 'advanced_illness', 'social_need', 'emergency_department_threshold', 'sent_back_from_a_facility', 'self_care_at_home'],
        'patterns': [r'list (?:the |every |all )?readmissions (?:in|for) ' + YEAR + r' by (?:driver|what drove them)']},
    'readmission_documented_barriers': {
        'title': "What the notes written between discharge and readmission say got in the way, across every readmission in one year: a missed visit, an unconfirmed medication list, cost or supply, transport, a refusal, a side effect, a lab limit (each note is read; one example quoted per kind)",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_DOCS,
        'read': {'statement': 'The sentence describes something that got in the way of care after discharge.', 'mode': 'readmission_barriers'},
        'columns': ['finding', 'readmissions', 'share', 'example_from_a_note'],
        'patterns': [r'what do the notes say got in the way before (?:the |our )?readmissions (?:in|for) ' + YEAR, r'what barriers do the notes document before (?:the |our )?readmissions (?:in|for) ' + YEAR]},
    'readmission_barriers_summary': {
        'title': "What is on record before each readmission in one year that could have got in the way: a missed or cancelled appointment, no appointment at all, no pharmacy fill, a documented medication barrier in the prior 90 days, a positive social-need screen",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_PAIRS + ", f AS (SELECT p.*, "
               "EXISTS (SELECT 1 FROM appointment a WHERE a.patient_id = p.patient_id AND a.scheduled_date > p.index_discharge AND a.scheduled_date < p.readmit_admit AND a.final_status = 'no_show') AS no_show, "
               "EXISTS (SELECT 1 FROM appointment a WHERE a.patient_id = p.patient_id AND a.scheduled_date > p.index_discharge AND a.scheduled_date < p.readmit_admit AND a.final_status = 'cancelled') AS cancelled, "
               "NOT EXISTS (SELECT 1 FROM appointment a WHERE a.patient_id = p.patient_id AND a.scheduled_date > p.index_discharge AND a.scheduled_date < p.readmit_admit) AS no_appointment, "
               "NOT EXISTS (SELECT 1 FROM fill x WHERE x.patient_id = p.patient_id AND x.fill_date > p.index_discharge AND x.fill_date < p.readmit_admit) AS no_fill, "
               "EXISTS (SELECT 1 FROM therapy_barrier b WHERE b.patient_id = p.patient_id AND b.event_date <= p.readmit_admit AND b.event_date >= date(p.readmit_admit, '-90 day')) AS barrier, "
               "EXISTS (SELECT 1 FROM social_need n WHERE n.patient_id = p.patient_id AND n.positive = 1 AND n.event_date <= p.readmit_admit) AS social FROM pairs p) "
               "SELECT 'readmissions' AS on_record_before_the_readmission, COUNT(*) AS readmissions, 1.0 AS share FROM f "
               "UNION ALL SELECT 'no appointment scheduled between the stays', SUM(no_appointment), ROUND(1.0 * SUM(no_appointment) / COUNT(*), 4) FROM f "
               "UNION ALL SELECT 'a no-show appointment between the stays', SUM(no_show), ROUND(1.0 * SUM(no_show) / COUNT(*), 4) FROM f "
               "UNION ALL SELECT 'a cancelled appointment between the stays', SUM(cancelled), ROUND(1.0 * SUM(cancelled) / COUNT(*), 4) FROM f "
               "UNION ALL SELECT 'no pharmacy fill between the stays', SUM(no_fill), ROUND(1.0 * SUM(no_fill) / COUNT(*), 4) FROM f "
               "UNION ALL SELECT 'a documented medication barrier in the prior 90 days', SUM(barrier), ROUND(1.0 * SUM(barrier) / COUNT(*), 4) FROM f "
               "UNION ALL SELECT 'a positive social-need screen on record', SUM(social), ROUND(1.0 * SUM(social) / COUNT(*), 4) FROM f "
               "UNION ALL SELECT 'medication barrier: ' || b.barrier_type, COUNT(DISTINCT p.patient_id || p.readmit_admit), ROUND(1.0 * COUNT(DISTINCT p.patient_id || p.readmit_admit) / (SELECT COUNT(*) FROM pairs), 4) "
               "FROM pairs p JOIN therapy_barrier b ON b.patient_id = p.patient_id AND b.event_date <= p.readmit_admit AND b.event_date >= date(p.readmit_admit, '-90 day') GROUP BY b.barrier_type "
               "UNION ALL SELECT 'social need: ' || n.need, COUNT(DISTINCT p.patient_id || p.readmit_admit), ROUND(1.0 * COUNT(DISTINCT p.patient_id || p.readmit_admit) / (SELECT COUNT(*) FROM pairs), 4) "
               "FROM pairs p JOIN social_need n ON n.patient_id = p.patient_id AND n.positive = 1 AND n.event_date <= p.readmit_admit GROUP BY n.need",
        'patterns': [r'what got in the way before (?:the |our )?readmissions (?:in|for) ' + YEAR, r'what barriers are on record (?:for|before) (?:the |our )?readmissions (?:in|for) ' + YEAR]},
    'readmission_rate_each_stay_once': {
        'title': "30-day readmission rate for inpatient stays discharged in one year, counting each stay once (a stay recorded by two sources is one stay) and a readmission as an inpatient admission 1 to 30 days after discharge",
        'params': {'year': {'type': 'year'}},
        'sql': _STAY_ONCE + "SELECT COUNT(*) AS stays, SUM(readmitted) AS readmitted_within_30_days, ROUND(1.0 * SUM(readmitted) / COUNT(*), 4) AS rate FROM d",
        'patterns': [r'what was the 30[- ]day readmission rate (?:in|for) ' + YEAR + r' counting each stay once', r'what was the true 30[- ]day readmission rate (?:in|for) ' + YEAR]},
    'readmission_rate_by_condition': {
        'title': "30-day readmission rate by condition for inpatient stays discharged in one year, each stay counted once: stays, readmitted within 30 days and rate for patients with each condition",
        'params': {'year': {'type': 'year'}},
        'sql': _STAY_ONCE + "SELECT c.concept AS condition, COUNT(*) AS stays, SUM(d.readmitted) AS readmitted_within_30_days, ROUND(1.0 * SUM(d.readmitted) / COUNT(*), 4) AS rate "
               "FROM d JOIN concept_patient c ON c.patient_id = d.patient_id AND c.kind = 'condition' GROUP BY c.concept HAVING COUNT(*) >= 10 ORDER BY 2 DESC, 1",
        'grain': ['condition'],
        'patterns': [r'what was the 30[- ]day readmission rate by condition (?:in|for) ' + YEAR, r'which conditions had the highest readmission rates? (?:in|for) ' + YEAR]},
    'frequent_readmitters': {
        'title': "Patients with two or more readmissions in one year (an inpatient admission 1 to 30 days after an earlier inpatient discharge), with how many",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_PAIRS + "SELECT patient_id, COUNT(*) AS readmissions, MIN(readmit_admit) AS first_readmission, MAX(readmit_admit) AS last_readmission FROM pairs "
               "GROUP BY patient_id HAVING COUNT(*) >= 2 ORDER BY 2 DESC, 1",
        'grain': ['patient_id'],
        'patterns': [r'which patients were readmitted (?:more than once|two or more times|repeatedly) (?:in|during) ' + YEAR, r'who are the frequent readmitters (?:in|for) ' + YEAR]},
    'readmissions_without_follow_up': {
        'title': "Readmissions in one year with no outpatient visit within 7 days of the prior discharge, one row per readmission (patient, prior discharge date, readmission date, days between)",
        'params': {'year': {'type': 'year'}},
        'sql': _READMIT_PAIRS + "SELECT p.patient_id, p.index_discharge AS prior_discharge_date, p.readmit_admit AS readmission_date, "
               "CAST(julianday(p.readmit_admit) - julianday(p.index_discharge) AS INTEGER) AS days_between FROM pairs p WHERE NOT EXISTS ("
               "SELECT 1 FROM visit v WHERE v.patient_id = p.patient_id AND v.event_date > p.index_discharge AND v.event_date <= date(p.index_discharge, '+7 day') "
               "AND v.event_date < p.readmit_admit AND v.type NOT IN ('inpatient', 'emergency', 'observation', 'telephone', 'orders_only')) ORDER BY 3, 1",
        'grain': ['patient_id', 'readmission_date'],
        'patterns': [r'which readmissions (?:in|for) ' + YEAR + r' had no (?:outpatient )?follow[- ]up visit within 7 days',
                     r'list (?:the )?readmissions (?:in|for) ' + YEAR + r' (?:with no|without) (?:outpatient )?follow[- ]up(?: visit)? within 7 days']},
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
        elif kind == 'date':
            if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])', text): return None
            out[name] = text
        elif kind == 'int':
            if not text.isdigit() or not spec['min'] <= int(text) <= spec['max']: return None
            out[name] = int(text)
        elif kind in ('enum', 'identifier'):
            key = _key(ENUM_WORDS.get(name, {}), text) or (str(value) if str(value) in spec['values'] else None)
            if key not in spec['values']: return None
            out[name] = key
        elif kind == 'vocabulary':
            words = {v.replace('_', ' ').lower(): v for v in vocabulary(spec, db)}
            if text not in words: return None
            out[name] = words[text]
        elif kind == 'patient':  # an identifier that exists, returned as stored; never a near match
            found = store.run('SELECT patient_id FROM document WHERE lower(patient_id) = lower(:p) LIMIT 1', {'p': str(value).strip()}, db)[1]
            if not found:   # a patient with no documents is still a patient: four readmitted patients in 2029 have none
                try:
                    found = store.run('SELECT patient_id FROM patient WHERE lower(patient_id) = lower(:p) LIMIT 1', {'p': str(value).strip()}, db)[1]
                except Exception:
                    found = []
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


def validate(question, sql, params, columns, rows, *, grain=None, total=None, db=None, nullable=()):
    """Deterministic checks on an executed answer. -> {'ok', 'checks': [{'name','ok','detail'}]}."""
    checks = []
    def add(name, ok, detail=''): checks.append({'name': name, 'ok': bool(ok), 'detail': detail})
    add('rows_returned', len(rows) > 0, f'{len(rows)} rows')
    add('row_shape', all(len(r) == len(columns) for r in rows) and len(set(columns)) == len(columns), f'{len(columns)} columns')
    # nullable: columns a recipe declares may be wholly empty when the supported answer is "none on record" (a patient with no lab results).
    empty = [c for i, c in enumerate(columns) if c not in nullable and rows and all(r[i] is None for r in rows)]
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
