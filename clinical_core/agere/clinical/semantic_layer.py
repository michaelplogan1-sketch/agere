"""Semantic layer: typed, concept-normalised tables materialised from the received store.

Built once per source digest into `semantic.sqlite3` beside the store. Planners
query it with ordinary read-only SQL instead of exploring raw JSON, so one
generated query replaces many exploratory tool calls. Every row keeps its
source record_id; concept columns come from the concept library (all coding
systems and label variants), and measurement values carry both the received
value/unit and the canonical unit conversion.
"""
from collections import defaultdict
from pathlib import Path
import json, re, sqlite3, time

from . import aggregate, client_data, concepts, population_events, population_feeds, source_profile

VERSION = 'semantic-layer/18'  # 18: lab results dated specimen > collected > event > recorded
# (previous) 'semantic-layer/17'  # 17: population_events (payer claim services, unified claims, pharmacy events)
GFR_UNNORMALIZED = 'gfr_unnormalized'  # 'glomerular filtration rate' results in a unit that is not per 1.73 m2 (mL/min)

# Source contract: streams whose records are successive snapshots of one business entity (key field).
# Other repeated ids (payments per account, lines per claim, fills per prescription) are separate events, not versions.
VERSIONED_STREAMS = {'patient_billing_accounts': 'account_id', 'medical_claims': 'claim_id', 'pharmacy_claim_transactions': 'claim_id',
                     'appointments': 'appointment_id', 'provider_remittance_advice': 'claim_id', 'advance_care_document_status': 'document_id'}

DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY, age_reported INTEGER, sex_reported TEXT, coverage_type TEXT,
  coverage_status TEXT, pcp_clinician_id TEXT, pcp_practice_id TEXT);
CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT,
  available_date TEXT, organization_id TEXT, payload TEXT);
CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, label TEXT, icd10 TEXT,
  snomed TEXT, onset TEXT, event_date TEXT, recorded_date TEXT, available_date TEXT, organization_id TEXT);
CREATE TABLE measurement(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, name TEXT, value REAL,
  unit TEXT, value_canonical REAL, unit_canonical TEXT, result_status TEXT, event_date TEXT, recorded_date TEXT,
  available_date TEXT, organization_id TEXT);
CREATE TABLE encounter(record_id TEXT, patient_id TEXT, source_table TEXT, encounter_id TEXT, type TEXT,
  type_concept TEXT, status TEXT, event_date TEXT, end_date TEXT, available_date TEXT, organization_id TEXT,
  attending_clinician_id TEXT, disposition TEXT);
CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type TEXT, type_concept TEXT, status TEXT, event_date TEXT,
  end_date TEXT, available_date TEXT, organization_id TEXT, attending_clinician_id TEXT, disposition TEXT, versions INTEGER);
CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT, organization_id TEXT,
  readmitted_30d INTEGER, days_to_readmission INTEGER, discharge_known INTEGER, discharge_date_source TEXT, discharge_time_utc TEXT,
  admit_time_utc TEXT);
CREATE TABLE link(record_id TEXT, stream TEXT, field TEXT, target_record_id TEXT);
CREATE TABLE concept_patient(concept TEXT, kind TEXT, patient_id TEXT, records INTEGER, first_date TEXT, last_date TEXT,
  PRIMARY KEY(concept, patient_id));
CREATE TABLE claim_adjustment(claim_id TEXT, record_id TEXT, patient_id TEXT, adjudication_status TEXT, group_code TEXT,
  reason_code TEXT, category TEXT, amount_cents INTEGER, current_version INTEGER);
CREATE TABLE medication(record_id TEXT, patient_id TEXT, source_stream TEXT, evidence TEXT, drug_name TEXT, drug_class TEXT,
  status TEXT, event_date TEXT, available_date TEXT);
CREATE TABLE measurement_latest(patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL, unit_canonical TEXT,
  same_day_values INTEGER, same_day_min REAL, record_id TEXT, PRIMARY KEY(patient_id, concept));
CREATE TABLE current_record(stream TEXT, key_field TEXT, business_id TEXT, record_id TEXT, patient_id TEXT, versions INTEGER,
  first_available_date TEXT, available_date TEXT, event_date TEXT, payload TEXT, PRIMARY KEY(stream, business_id));
CREATE TABLE social_need(patient_id TEXT, need TEXT, positive INTEGER, instrument TEXT, source_stream TEXT, source_field TEXT,
  event_date TEXT, record_id TEXT);
CREATE TEMP VIEW v_claim_rows AS SELECT json_extract(payload,'$.claim_id') AS claim_id, patient_id,
  json_extract(payload,'$.source_system') AS payer, json_extract(payload,'$.service_date') AS service_date,
  CAST(json_extract(payload,'$.benefit_year') AS TEXT) AS benefit_year, json_extract(payload,'$.status') AS status,
  json_extract(payload,'$.payer_paid_cents') AS payer_paid_cents, json_extract(payload,'$.billed_cents') AS billed_cents,
  json_extract(payload,'$.allowed_cents') AS allowed_cents, available_date, json_extract(payload,'$.available_time') AS available_time,
  json_extract(payload,'$.service_organization_id') AS service_organization_id, record_id
  FROM record WHERE stream = 'medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL;
CREATE TABLE appointment(appointment_id TEXT PRIMARY KEY, patient_id TEXT, scheduled_date TEXT, booked_date TEXT, final_status TEXT,
  ever_no_show INTEGER, ever_cancelled INTEGER, ever_rescheduled INTEGER, kept INTEGER, organization_id TEXT, clinician_id TEXT,
  purpose TEXT, versions INTEGER);
CREATE TABLE claim(claim_id TEXT PRIMARY KEY, patient_id TEXT, payer TEXT, service_date TEXT, benefit_year TEXT, first_status TEXT,
  final_status TEXT, ever_denied INTEGER, denied_then_paid INTEGER, net_payer_paid_cents INTEGER, billed_cents INTEGER,
  allowed_cents INTEGER, first_available_date TEXT, last_available_date TEXT, service_organization_id TEXT, versions INTEGER);
"""
INDEXES = """
CREATE INDEX record_stream ON record(stream, patient_id);
CREATE INDEX condition_concept ON condition(concept, patient_id);
CREATE INDEX measurement_concept ON measurement(concept, patient_id);
CREATE INDEX encounter_concept ON encounter(type_concept, patient_id);
CREATE INDEX concept_patient_patient ON concept_patient(patient_id);
CREATE INDEX link_target ON link(target_record_id, stream);
CREATE INDEX visit_concept ON visit(type_concept, patient_id);
CREATE INDEX link_source ON link(record_id);
"""

SCHEMA_DOC = {
    'patient': 'One row per roster patient. age_reported/sex_reported as received; coverage_type/status from latest coverage record, '
               'else (population-feed members without one) coverage_type = line_of_business of the latest enrollment month and '
               'coverage_status active | inactive by whether a span covers the as-of date; coverage_source = coverage_record | '
               'enrollment_span (NULL = no coverage received). pcp_* = latest assigned primary-care relationship.',
    'condition': 'One row per (condition record, matching library concept); concept NULL when no library concept matches. '
                 'Sources: conditions, condition_assessments, problem_lists. label = received description.',
    'measurement': 'Labs and vitals, one row per (record, concept); concept NULL when unmapped. value/unit as received; '
                   'value_canonical in unit_canonical when the unit converts (else NULL). Cancelled results have value NULL. '
                   'concept egfr = BSA-normalised eGFR only (mL/min/1.73m2 or no unit); GFR results reported in another unit (mL/min, '
                   'measured GFR / clearance) are concept gfr_unnormalized and never count as eGFR. Use only result_current = 1 AND '
                   'excluded_reason IS NULL rows for values; value_censor marks censored strings (eGFR ">60" -> 60 with value_censor ">"); '
                   'see measurement_quality. Questionnaire totals are measurements too: mood_questionnaires rows (source_table '
                   'mood_questionnaires, one row per administration, name = instrument) carry concept phq9 (PHQ-9 total 0-27) or phq2 '
                   '(PHQ-2 total 0-6), value = total in points; event_time orders same-day administrations.',
    'encounter': 'Encounter records plus ADT notifications (source_table adt, type = ADT event). type_concept = library encounter concept. '
                 'ED visits that only ADT messages or payer claims carry (claims-only members) are added once each, deduplicated against '
                 'the EHR (source_table adt_messages | payer_medical_claims, type_concept emergency_visit); see utilization_event.',
    'visit': 'One row per encounter (encounter records repeat once per status version; use visit, not encounter, to count visits). '
             'Latest version wins; versions = number of received versions.',
    'stay': 'Distinct hospital stays (inpatient/observation encounters collapsed by admit day and overlap) with 30-day readmission flag. '
            'discharge_date and los_days are NULL when no discharge time was received (discharge_known = 0); never treat those as 0 days. '
            'discharge_date is the ADT discharge (A03) date of the stay\'s encounter when one was received (discharge_date_source = adt), '
            'else the date part of the encounter end_time (encounter_end_time_utc); discharge_time_utc keeps the UTC end timestamp. '
            'Readmission gaps use discharge_date. LOS uses one clock: admit_time_utc = UTC start of the admitting encounter; '
            'los_days = midnights between the UTC dates of admit_time_utc and discharge_time_utc, los_days_elapsed = fractional days '
            'between the two timestamps (state which one an answer uses); ADT/claims-only stays count local admit to local '
            'discharge dates. stay_source = encounters, or adt | payer_claim for admissions found only in ADT '
            'visits or inpatient (TOB 011x) / observation (revenue 0762) payer claims, so admission rates cover claims-only members; '
            'readmission flags of a patient with such stays are recomputed over all of the patient\'s stays.',
    'concept_patient': 'Patients having each library concept (condition, measurement, encounter), with record counts and first/last dates.',
    'link': 'Every record-to-record reference (fields ending _record_id/_record_ids): record_id/stream of the referring record, '
            'field name, target_record_id. Indexed on target_record_id - use it for linkage questions (e.g. orders without dispenses).',
    'record': 'Every received record (all streams). Use json_extract(payload, \'$.field\') for fields of streams not modelled above. '
              'payload supersedes_record_id is set on a correction: the record replaces that earlier record of the same stream '
              '(corrections per feed = records with it set, by stream; current version = not superseded). '
              'event_date_precision = minute | day | month | year | unknown. Fields ending _record_id point at another '
              'record (see link).',
    'appointment': 'One row per appointment_id (appointments arrive as a booked record plus later status records). scheduled_date from '
                   'the latest version carrying a scheduled_date (a reschedule moves it), else the date of slot_start (population-feed '
                   'bookings send booked_at + slot_start); booked_date = earliest booked version; lead_time_days = scheduled_date - '
                   'booked_date, NULL when either is unknown. final_status = latest status received; ever_* flags over all versions; '
                   'kept = any arrived, attended or fulfilled version. outcome = kept | no_show (never kept) | cancelled (never kept '
                   'or no-show) | rescheduled | scheduled (still booked) | unassigned (unassigned_requires_review) | unknown. '
                   'No-show rate = no_show / (kept + no_show), or with cancelled in the denominator - state which.',
    'claim_adjustment': 'One row per adjustment line on each medical claim adjudication record: group_code (CO/PR/OA), reason_code '
                        '(X12 CARC), category (deductible, coinsurance, copay, contractual_adjustment, else group-code), amount_cents; '
                        'current_version = 1 on the claim\'s latest version.',
    'note_fact': 'Facts found only in free text (EF, symptoms, edema, dyspnea, medication non-adherence and reasons, social needs, '
                 'imaging findings, follow-up instructions, goals of care, smoking status) in notes, documents and HIE ED/discharge '
                 'summaries, with negation, temporality and evidence spans; copied-forward note lines count once per patient; rule '
                 'extractor admitted per fact type at >= 0.90 measured precision.',
    'medication': 'Every medication mention, one row per (record, drug_class): source_stream and evidence (dispensed, claim, ordered, '
                  'listed, administered, reported, pharmacy_history, payer_claim = payer pharmacy claim), drug_name as received, status. drug_class from the active '
                  'ingredient (statin, beta_blocker, hf_evidence_based_beta_blocker, ace_inhibitor, arb, arni, mra, sglt2_inhibitor, '
                  'glp1_agonist, metformin, insulin, anticoagulant, opioid, ...); NULL when unclassified. drug_class values are exact ids '
                  '(full list and groups in drug_class_vocabulary): compare with = or IN, never LIKE - \'%beta%\' also matches '
                  'short_acting_beta_agonist and \'%antihistamine%\' matches both generations. Use beta_blocker, '
                  'first_generation_antihistamine (Beers), second_generation_antihistamine, short_acting_beta_agonist. '
                  'picked_up = 1 on fills that left the pharmacy (evidence dispensed | claim | payer_claim with status dispensed | '
                  'paid | completed, not reversed / returned to stock; prepared-only dispenses never count): use picked_up = 1 for '
                  'any fill, pickup or dispensing count. ONE pickup can arrive from two sources: the dispensing pharmacy '
                  '(pharmacy_dispenses, days in payload days_supply_reported) and the payer\'s pharmacy claim (payer_pharmacy_claims / '
                  'pharmacy_claims, days in payload days_supply). For fill counts or days\' supply never add the sources together: '
                  'take one fill per patient, drug class and fill date (e.g. per patient+event_date the larger days supply of the two '
                  'sources), then sum over dates; counting patients needs no such step.',
    'wellness_visit': 'One row per wellness_visit_services record: visit_type (medicare_annual_wellness, preventive_review, ...), '
                      'hcpcs_code, awv = 1 for a Medicare annual wellness visit (G0438 initial / G0439 subsequent, awv_kind), status '
                      '(count completed), coding_status, event_date, encounter_id. visit.wellness_visit_type / visit.awv mark the '
                      'encounter. Generic preventive reviews are not AWVs. source = ehr | payer_claim_line: AWVs known only from a '
                      'payer claim line G0438/G0439 (not within 1 day of an EHR AWV) are rows too, so count AWVs here, not on visit.',
    'drug_class_vocabulary': 'Every medication.drug_class id with its groups (class_group: antihypertensive, raas_inhibitor, '
                             'diabetes_medication, beers_high_risk; NULL row = ungrouped class). Join on drug_class for group questions.',
    'normalization': 'measurement.loinc/component/lab_category, condition.is_diagnosis/icd10_category/chapter/chronic/'
                     'normalized_label, medication.ingredients/therapeutic_category, visit.setting come from the normalization '
                     'dictionary (each source string mapped once to standard terminology).',
    'measurement_latest': 'Latest DATED value per patient and measurement concept (undated rows never count as latest); '
                          'same_day_values/same_day_min for same-date ties (HEDIS uses the lowest).',
    'current_record': 'Latest version of each business entity in versioned (snapshot) streams: stream, key_field, business_id, '
                      'versions, payload of the latest version. Use it for current state (current balance, current claim status). '
                      'It drops what earlier versions carried: a status-history stream (appointments: booked -> rescheduled -> '
                      'fulfilled) records a reschedule reason or an interim status on an earlier version, so "ever" / "had to be '
                      'rescheduled because" questions read record WHERE stream = ... over all versions (count DISTINCT the key).',
    'social_need': 'Social-needs screening answers normalised across instruments: one row per (screen record, need) with positive 0/1. '
                   'need = food | transport | cost_of_care, plus housing | utilities | safety from AHC HRSN screens (instrument '
                   'AHC_HRSN; positive when any item for the need is answered yes; source_field = items:<item names>). transport positive = transport_difficulty_reported or transport unreliable '
                   '(family_ride/reliable = negative); cost_of_care = difficulty paying for care (social screen) or medical bills '
                   '(financial screen). Screened = has a row for that need.',
    'standards': 'Standard codes received from the source sit beside the local values (NULL when a release has none): '
                 'condition.icd10 (+icd10_source), measurement.loinc (+loinc_source = source | dictionary, loinc_dictionary, '
                 'reference_range, abnormal_flag), medication.rxnorm (+rxnorm_source = source | dictionary_ingredient, '
                 'rxnorm_ingredient) and medication.ndc, visit.principal_icd10/place_of_service, claim.drg/drg_grouper_version/'
                 'principal_diagnosis/diagnosis_codes (JSON)/billing_npi/rendering_npi/type_of_bill/cpt/hcpcs/local_charge_code, '
                 'patient.birth_date/race/ethnicity/preferred_language/interpreter_needed/marital_status/zip/county (FIPS)/state. '
                 'Source codes win over dictionary mappings; *_source records which was used. meta.standards holds coverage.',
    'encounter_diagnosis': 'ICD-10-CM diagnoses coded on encounters: one row per (encounter_id, rank, icd10, type); '
                           'type principal/primary/secondary. Administrative contacts are uncoded by design.',
    'claim_line': 'One row per medical claim line: cpt or hcpcs (CPT/HCPCS service code), local_charge_code (the local charge '
                  'code, kept across releases), revenue_code (UB-04 lines), units, charge_cents.',
    'immunization': 'One row per immunization record: vaccine, product, cvx, mvx, status (administration_status), season (influenza '
                    'season as recorded, else July-June of the date, e.g. 2029-2030), source = ehr | payer_claim_line (influenza '
                    'vaccine CPT / G0008 claim lines not within 1 day of an EHR flu shot; code = the billed code).',
    'clinician': 'Clinician reference rows: specialty, synthetic npi, NUCC taxonomy_code, organization_id.',
    'organization': 'Organization reference rows: organization_type, npi, taxonomy_code, tin, city/state/postal_code; facility_class '
                    '(hospital, ambulatory_surgery_center, imaging_idtf, urgent_care, dialysis, skilled_nursing, home_health, hospice, '
                    'ambulance, dme, pharmacy, laboratory, primary_care, outpatient_practice, payer, other) from the directory type; '
                    'place_of_service = most frequent CMS POS on claims billed under the organization NPI (place_of_service_claims = n).',
    'claim': 'One row per medical claim_id (adjudications arrive as successive records: denied, paid, reversed, re-paid). first_status '
             'by arrival order; final_status follows the adjudication chain (each reversal paired with the payment it reverses; '
             'final = last unreversed adjudication by event time), received_final_status = last-arrival status, '
             'live_payer_paid_cents = payer paid on unreversed payments; denied_then_paid = a denial later followed by payment; net_payer_paid_cents = paid minus reversed. '
             'Legacy rows without claim_id are excluded. claim_source = medical_claims (our provider remittances) | payer_medical_claims (payer-feed claims '
             'that are not the payer copy of one of our claims; a -R replacement folds into its original): every claim of the population. '
             'first_paid_date / first_reversal_date per claim; per-adjudication money in claim_adjudication.',
    'patient_population': 'patient.data_segment (deep_chart | claims_plus_ehr | claims_only | duplicate_chart; NULL in panel-only '
                          'releases), patient.line_of_business/payer_id (latest member_month). condition.diagnosis_basis, '
                          'diagnosing_encounter_id, diagnosis_date (onset, else event date) on conditions records.',
    **population_feeds.SCHEMA_DOC,
    **population_events.SCHEMA_DOC,
    **client_data.SCHEMA_DOC,
}


def _date(r): return (r.get('event_date') or '')[:10] or None


def _result_date(r):
    """A lab result's clinical date: specimen time, else collected time, else event date, else recorded date (a result
    delivered without an event date is still dated, not dropped from time-windowed questions)."""
    for k in ('specimen_time', 'collected_time', 'event_date', 'recorded_date'):
        v = r.get(k)
        if v:
            return str(v)[:10]
    return None


MED_SOURCES = [  # stream, name field, status field, evidence label
    ('pharmacy_dispenses', 'medication_name', 'status', 'dispensed'), ('pharmacy_claims', 'medication_name', 'transaction_status', 'claim'),
    ('medication_orders', 'medication_name', 'status', 'ordered'), ('medication_lists', 'name', None, 'listed'),
    ('medication_administrations', 'medication_name', 'administration_status', 'administered'),
    ('reported_medication_entries', 'medication_name_reported', 'use_statement', 'reported'),
    ('pharmacy_history', 'medication_name', 'reported_status', 'pharmacy_history'),
    ('payer_pharmacy_claims', 'drug_name', 'claim_status', 'payer_claim')]


# Scored questionnaires whose totals are measurements (one row per administration; concept from the instrument name).
QUESTIONNAIRE_STREAMS = ('mood_questionnaires',)
QUESTIONNAIRE_CONCEPTS = ('phq9', 'phq2')


def _questionnaire_concept(instrument):
    name = str(instrument or '').strip()
    return next((c for c in QUESTIONNAIRE_CONCEPTS
                 if re.fullmatch(concepts.MEASUREMENTS[c]['names'], name, re.I)), None)


# Appointment status values received (v2.3 warehouse: arrived 403, attended 1,671, booked 38,677, cancelled 1,532,
# fulfilled 31,886, no_show 2,219, rescheduled 347, unassigned_requires_review 25 records), each mapped deliberately:
APPOINTMENT_KEPT = ('arrived', 'attended', 'fulfilled')          # patient seen: kept
APPOINTMENT_NO_SHOW = ('no_show',)                              # no-show (only when never kept)
APPOINTMENT_CANCELLED = ('cancelled',)                          # cancelled (only when never kept / no-show)
APPOINTMENT_SCHEDULED = ('booked',)                             # still scheduled, no outcome yet
# 'rescheduled' supersedes the slot: the appointment's outcome comes from later versions (outcome 'rescheduled' when none came).
APPOINTMENT_REVIEW = ('unassigned_requires_review',)            # scheduling contact unresolved: outcome 'unassigned'
# Any other status value: outcome 'unknown'.


# X12 claim adjustment reason codes most used in patient-responsibility questions.
CARC = {('PR', '1'): 'deductible', ('PR', '2'): 'coinsurance', ('PR', '3'): 'copay', ('CO', '45'): 'contractual_adjustment'}


def _revenue_cycle(out):
    """Claim timing and adjustment primitives: first original submission, first payer cash, net cash, and adjustment lines."""
    for col in ('first_submitted_date TEXT', 'first_payer_cash_date TEXT', 'net_payer_cash_cents INTEGER', 'first_adjudication_date TEXT'):
        out.execute(f'ALTER TABLE claim ADD COLUMN {col}')
    out.execute("""CREATE TEMP TABLE subs AS SELECT json_extract(payload,'$.claim_id') AS claim_id, min(event_date) AS first_sub
                     FROM record WHERE stream = 'medical_claim_submissions'
                     AND coalesce(json_extract(payload,'$.submission_kind'),'original') = 'original' GROUP BY 1""")
    out.execute('CREATE INDEX temp.subs_claim ON subs(claim_id)')
    out.execute('UPDATE claim SET first_submitted_date = (SELECT first_sub FROM subs WHERE subs.claim_id = claim.claim_id)')
    out.execute("""CREATE TEMP TABLE adjud AS SELECT json_extract(payload,'$.claim_id') AS claim_id, min(event_date) AS first_adj
                     FROM record WHERE stream = 'medical_claims' GROUP BY 1""")
    out.execute('CREATE INDEX temp.adjud_claim ON adjud(claim_id)')
    out.execute('UPDATE claim SET first_adjudication_date = (SELECT first_adj FROM adjud WHERE adjud.claim_id = claim.claim_id)')
    out.execute("""CREATE TEMP TABLE cash AS SELECT json_extract(payload,'$.claim_id') AS claim_id,
                     min(CASE WHEN json_extract(payload,'$.allocated_cents') > 0 THEN event_date END) AS first_cash,
                     sum(json_extract(payload,'$.allocated_cents')) AS net_cash
                     FROM record WHERE stream = 'payer_cash_allocations' GROUP BY 1""")
    out.execute('CREATE INDEX temp.cash_claim ON cash(claim_id)')
    out.execute('UPDATE claim SET first_payer_cash_date = (SELECT first_cash FROM cash WHERE cash.claim_id = claim.claim_id), '
                'net_payer_cash_cents = (SELECT net_cash FROM cash WHERE cash.claim_id = claim.claim_id)')
    current = {rid for (rid,) in out.execute("SELECT record_id FROM current_record WHERE stream = 'medical_claims'")}
    rows = []
    for rid, pid, payload in out.execute("SELECT record_id, patient_id, payload FROM record WHERE stream = 'medical_claims'"):
        r = json.loads(payload)
        for a in (r.get('adjustments') or []):
            if not isinstance(a, dict): continue
            g, code = str(a.get('group') or ''), str(a.get('reason_code') or '')
            rows.append((r.get('claim_id'), rid, pid, r.get('status'), g, code, CARC.get((g, code), f'{g}-{code}'),
                         a.get('amount_cents'), int(rid in current)))
    out.executemany('INSERT INTO claim_adjustment VALUES (?,?,?,?,?,?,?,?,?)', rows)
    out.execute('CREATE INDEX claim_adjustment_claim ON claim_adjustment(claim_id)')


NORMALIZATION = Path(__file__).resolve().parents[3] / 'config' / 'agere' / 'normalization'


def _dictionary(domain):
    target = NORMALIZATION / f'{domain}.json'
    return json.loads(target.read_text())['entries'] if target.exists() else {}


def _cuis(v):
    c = v.get('rxnorm_ingredient_cui')
    return ','.join(str(x) for x in c) if isinstance(c, list) else (str(c) if c not in (None, '') else None)


def _cui(v):
    """Single-ingredient CUI only; combination products stay uncoded rather than picking one ingredient."""
    c = v.get('rxnorm_ingredient_cui')
    if isinstance(c, list): c = c[0] if len(c) == 1 else None
    return str(c) if c not in (None, '') else None


def _normalize(out):
    """Apply the normalization dictionary (each distinct source string mapped once to standard terminology).
    Rules stay authoritative where they map; the dictionary fills gaps and adds standard codes and categories."""
    labs, conds, meds, encs = (_dictionary(d) for d in ('labs', 'conditions', 'medications', 'encounters'))
    for col in ('component TEXT', 'lab_category TEXT'):
        out.execute(f'ALTER TABLE measurement ADD COLUMN {col}')
    # loinc / loinc_source were filled from source codes by _standards; the dictionary only fills the gaps.
    out.executemany("""UPDATE measurement SET loinc_dictionary = ?, component = ?, lab_category = ?, concept = coalesce(concept, ?),
                         loinc_source = CASE WHEN loinc IS NOT NULL THEN loinc_source WHEN ? IS NOT NULL THEN 'dictionary' END,
                         loinc = coalesce(loinc, ?) WHERE name = ?""",
                    [(v.get('loinc'), v.get('component'), v.get('category'), v.get('agere_concept') if (v.get('confidence') or 0) >= 0.8 else None,
                      v.get('loinc'), v.get('loinc'), k) for k, v in labs.items()])
    # Source LOINC identifies the analyte even when the local name is not in the concept library or dictionary.
    by_loinc = defaultdict(set)
    for v in labs.values():
        if v.get('loinc') and v.get('agere_concept') and (v.get('confidence') or 0) >= 0.8: by_loinc[v['loinc']].add(v['agere_concept'])
    out.executemany("UPDATE measurement SET concept = ? WHERE concept IS NULL AND loinc_source = 'source' AND loinc = ?",
                    [(next(iter(cs)), code) for code, cs in by_loinc.items() if len(cs) == 1])
    for col in ('is_diagnosis INTEGER', 'icd10_category TEXT', 'chapter TEXT', 'chronic INTEGER', 'normalized_label TEXT'):
        out.execute(f'ALTER TABLE condition ADD COLUMN {col}')
    out.executemany('UPDATE condition SET is_diagnosis = ?, icd10_category = ?, chapter = ?, chronic = ?, normalized_label = ? WHERE label = ?',
                    [(None if v.get('is_diagnosis') is None else int(bool(v['is_diagnosis'])), v.get('icd10_category'), v.get('chapter'),
                      None if v.get('chronic') is None else int(bool(v['chronic'])), v.get('normalized_label'), k) for k, v in conds.items()])
    for col in ('ingredients TEXT', 'therapeutic_category TEXT'):
        out.execute(f'ALTER TABLE medication ADD COLUMN {col}')
    out.executemany('UPDATE medication SET ingredients = ?, therapeutic_category = ? WHERE drug_name = ?',
                    [(', '.join(v.get('ingredients') or []), v.get('therapeutic_category'), k) for k, v in meds.items()])
    # Drug classes: union of rule classes and dictionary classes (dictionary catches abbreviations such as ASA, HCTZ).
    extra = []
    for name, v in meds.items():
        have = {c for (c,) in out.execute('SELECT DISTINCT drug_class FROM medication WHERE drug_name = ? AND drug_class IS NOT NULL', (name,))}
        add = [c for c in (v.get('drug_classes') or []) if c not in have and (v.get('confidence') or 0) >= 0.8]
        if not add: continue
        for row in out.execute('SELECT DISTINCT record_id, patient_id, source_stream, evidence, drug_name, status, event_date, available_date, '
                               'therapeutic_category, ingredients FROM medication WHERE drug_name = ?', (name,)).fetchall():
            extra += [(row[0], row[1], row[2], row[3], row[4], c, row[5], row[6], row[7], row[9], row[8]) for c in add]
    out.executemany('INSERT INTO medication(record_id, patient_id, source_stream, evidence, drug_name, drug_class, status, event_date, '
                    'available_date, ingredients, therapeutic_category) VALUES (?,?,?,?,?,?,?,?,?,?,?)', extra)
    out.execute("DELETE FROM medication WHERE drug_class IS NULL AND drug_name IN (SELECT drug_name FROM medication WHERE drug_class IS NOT NULL)")
    _medication_codes(out)  # rows added above for dictionary classes need their source codes too
    # RxNorm: the source product RxCUI wins; the dictionary ingredient CUI fills rows the source left uncoded.
    out.executemany("""UPDATE medication SET rxnorm_ingredient = ?,
                         rxnorm_source = CASE WHEN rxnorm IS NOT NULL THEN rxnorm_source WHEN ? IS NOT NULL THEN 'dictionary_ingredient' END,
                         rxnorm = coalesce(rxnorm, ?) WHERE drug_name = ?""",
                    [(_cuis(v), _cui(v), _cui(v), k) for k, v in meds.items()])
    out.execute('ALTER TABLE visit ADD COLUMN setting TEXT')
    out.executemany('UPDATE visit SET setting = ? WHERE type = ?', [(v.get('setting'), k) for k, v in encs.items()])
    out.executemany('UPDATE visit SET in_person = ? WHERE type = ? AND type_concept IS NULL',
                    [(int(bool(v.get('in_person'))), k) for k, v in encs.items() if v.get('in_person') is not None])


def _medications(out):
    """One row per (medication record, drug class) across every medication source; class from the ingredient."""
    from .drug_classes import classify
    rows = []
    for stream, name_f, status_f, evidence in MED_SOURCES:
        for rid, pid, payload, d, av in out.execute('SELECT record_id, patient_id, payload, event_date, available_date FROM record WHERE stream = ?', (stream,)):
            r = json.loads(payload)
            name = r.get(name_f)
            if not name: continue
            status = r.get(status_f) if status_f else None
            for c in classify(name) or [None]:
                rows.append((rid, pid, stream, evidence, name, c, status, d, av))
    out.executemany('INSERT INTO medication VALUES (?,?,?,?,?,?,?,?,?)', rows)
    out.executescript('CREATE INDEX medication_class ON medication(drug_class, patient_id); CREATE INDEX measurement_latest_concept ON measurement_latest(concept);')


PATIENT_STANDARD = (('birth_date', 'birth_date'), ('race', 'race'), ('ethnicity', 'ethnicity'),
                    ('preferred_language', 'preferred_language'), ('interpreter_needed', 'interpreter_needed'),
                    ('marital_status', 'marital_status'), ('zip', 'postal_code'), ('county', 'county_fips'), ('state', 'state'))


def _columns(out, table):
    return {r[1] for r in out.execute(f'PRAGMA table_info({table})')}


def _add(out, table, *cols):
    have = _columns(out, table)
    for col in cols:
        if col.split()[0] not in have: out.execute(f'ALTER TABLE {table} ADD COLUMN {col}')


def _standards(out, people):
    """Standard codes received from the source (v2.0+ releases), beside the existing local values.
    Releases without them leave these columns NULL; nothing here is derived from free text or labels."""
    _add(out, 'patient', *(f'{c} {"INTEGER" if c == "interpreter_needed" else "TEXT"}' for c, _ in PATIENT_STANDARD))
    out.executemany('UPDATE patient SET ' + ', '.join(f'{c} = ?' for c, _ in PATIENT_STANDARD) + ' WHERE patient_id = ?',
                    [(*(p.get(f) for _, f in PATIENT_STANDARD), pid) for pid, p in people.items()])
    _add(out, 'condition', 'icd10_source TEXT')
    out.execute("UPDATE condition SET icd10_source = 'source' WHERE icd10 IS NOT NULL AND icd10 <> ''")
    _add(out, 'measurement', 'loinc TEXT', 'loinc_source TEXT', 'loinc_dictionary TEXT', 'reference_range TEXT', 'abnormal_flag TEXT')
    out.execute("""CREATE TEMP TABLE lab_codes AS SELECT record_id, json_extract(payload,'$.loinc') AS loinc,
                     json_extract(payload,'$.reference_range') AS rr, json_extract(payload,'$.abnormal_flag') AS flag
                     FROM record WHERE stream = 'labs' AND (json_extract(payload,'$.loinc') IS NOT NULL
                       OR json_extract(payload,'$.reference_range') IS NOT NULL OR json_extract(payload,'$.abnormal_flag') IS NOT NULL)""")
    out.execute('CREATE INDEX temp.lab_codes_id ON lab_codes(record_id)')
    out.execute("""UPDATE measurement SET (loinc, reference_range, abnormal_flag) =
                     (SELECT l.loinc, l.rr, l.flag FROM lab_codes l WHERE l.record_id = measurement.record_id)
                   WHERE source_table = 'labs' AND record_id IN (SELECT record_id FROM lab_codes)""")
    out.execute("UPDATE measurement SET loinc_source = 'source' WHERE loinc IS NOT NULL")
    out.executescript("""
    CREATE TABLE encounter_diagnosis(encounter_id TEXT, patient_id TEXT, rank INTEGER, icd10 TEXT, type TEXT, record_id TEXT);
    INSERT INTO encounter_diagnosis SELECT encounter_id, patient_id, rank, icd10, type, min(record_id) FROM (
      SELECT json_extract(r.payload,'$.encounter_id') AS encounter_id, r.patient_id, CAST(json_extract(d.value,'$.rank') AS INTEGER) AS rank,
             json_extract(d.value,'$.icd10') AS icd10, json_extract(d.value,'$.type') AS type, r.record_id
      FROM record r, json_each(r.payload, '$.diagnoses') d
      WHERE r.stream = 'encounters' AND json_type(r.payload, '$.diagnoses') = 'array')
    GROUP BY encounter_id, patient_id, rank, icd10, type;
    CREATE INDEX encounter_diagnosis_code ON encounter_diagnosis(icd10, patient_id);
    CREATE INDEX encounter_diagnosis_encounter ON encounter_diagnosis(encounter_id);
    CREATE TABLE claim_line(record_id TEXT, claim_id TEXT, patient_id TEXT, line_number INTEGER, service_date TEXT, code_system TEXT,
      cpt TEXT, hcpcs TEXT, local_charge_code TEXT, revenue_code TEXT, units REAL, charge_cents INTEGER, service_organization_id TEXT);
    INSERT INTO claim_line SELECT record_id, json_extract(payload,'$.claim_id'), patient_id, json_extract(payload,'$.line_number'),
      json_extract(payload,'$.service_date'), json_extract(payload,'$.service_code_system'),
      CASE WHEN json_extract(payload,'$.service_code_system') = 'CPT' THEN json_extract(payload,'$.service_code') END,
      CASE WHEN json_extract(payload,'$.service_code_system') = 'HCPCS' THEN json_extract(payload,'$.service_code') END,
      coalesce(json_extract(payload,'$.local_charge_code'),
               CASE WHEN json_extract(payload,'$.service_code_system') NOT IN ('CPT','HCPCS') THEN json_extract(payload,'$.service_code') END),
      json_extract(payload,'$.revenue_code'), json_extract(payload,'$.units'), json_extract(payload,'$.charge_cents'),
      json_extract(payload,'$.service_organization_id')
      FROM record WHERE stream = 'medical_claim_lines';
    CREATE INDEX claim_line_claim ON claim_line(claim_id);
    CREATE INDEX claim_line_cpt ON claim_line(cpt, patient_id);
    CREATE TABLE immunization(record_id TEXT, patient_id TEXT, vaccine TEXT, product TEXT, cvx TEXT, mvx TEXT, status TEXT,
      event_date TEXT, available_date TEXT);
    INSERT INTO immunization SELECT record_id, patient_id, json_extract(payload,'$.vaccine'), json_extract(payload,'$.product'),
      json_extract(payload,'$.cvx_code'), json_extract(payload,'$.mvx_code'), json_extract(payload,'$.administration_status'),
      event_date, available_date FROM record WHERE stream = 'immunizations';
    CREATE INDEX immunization_cvx ON immunization(cvx, patient_id);
    """)
    _add(out, 'visit', 'principal_icd10 TEXT', 'place_of_service TEXT')
    out.execute("""UPDATE visit SET principal_icd10 = (SELECT d.icd10 FROM encounter_diagnosis d WHERE d.encounter_id = visit.encounter_id
                     ORDER BY (d.type = 'principal' OR d.type = 'primary') DESC, d.rank LIMIT 1)""")
    out.execute("""CREATE TEMP TABLE pos AS SELECT json_extract(payload,'$.encounter_id') AS encounter_id,
                     max(json_extract(payload,'$.place_of_service')) AS pos FROM record
                     WHERE stream = 'encounters' AND json_extract(payload,'$.place_of_service') IS NOT NULL GROUP BY 1""")
    out.execute('CREATE INDEX temp.pos_enc ON pos(encounter_id)')
    out.execute('UPDATE visit SET place_of_service = (SELECT pos FROM pos WHERE pos.encounter_id = visit.encounter_id)')


def _medication_codes(out):
    _add(out, 'medication', 'rxnorm TEXT', 'rxnorm_source TEXT', 'rxnorm_ingredient TEXT', 'ndc TEXT')
    out.execute("""CREATE TEMP TABLE IF NOT EXISTS med_codes AS SELECT record_id, json_extract(payload,'$.rxcui') AS rxcui,
                     json_extract(payload,'$.ndc') AS ndc FROM record
                     WHERE (json_extract(payload,'$.rxcui') IS NOT NULL OR json_extract(payload,'$.ndc') IS NOT NULL)
                       AND stream IN (""" + ','.join(f"'{s[0]}'" for s in MED_SOURCES) + ')')
    out.execute('CREATE INDEX IF NOT EXISTS temp.med_codes_id ON med_codes(record_id)')
    out.execute("""UPDATE medication SET (rxnorm, ndc) = (SELECT CAST(c.rxcui AS TEXT), CAST(c.ndc AS TEXT) FROM med_codes c
                     WHERE c.record_id = medication.record_id)
                   WHERE rxnorm IS NULL AND record_id IN (SELECT record_id FROM med_codes)""")
    out.execute("UPDATE medication SET rxnorm_source = 'source' WHERE rxnorm IS NOT NULL AND rxnorm_source IS NULL")


def settle_history(history):
    """Effective claim state from its adjudication chain (same pairing as claims_review.settle).
    history: dicts with record_id, status, payer_paid_cents, reverses (original_adjudication_record_id), in event order
    (event_date, then arrival). Each reversal is paired with the payment it names; an unlinked reversal reverses the
    latest unreversed payment before it. final_status = the last unreversed, non-reversal adjudication by event time;
    'reversed' when every adjudication was reversed. Returns (final_status, live_net_paid_cents)."""
    reversed_ids = {x['reverses'] for x in history if x['status'] == 'reversed' and x.get('reverses')}
    for i, x in enumerate(history):
        if x['status'] == 'reversed' and not x.get('reverses'):
            prior = [y for y in history[:i] if y['status'] == 'paid' and y['record_id'] not in reversed_ids]
            if prior: reversed_ids.add(prior[-1]['record_id'])
    live = [x for x in history if x['status'] != 'reversed' and x['record_id'] not in reversed_ids]
    final = live[-1]['status'] if live else ('reversed' if history else None)
    return final, sum((x.get('payer_paid_cents') or 0) for x in live if x['status'] == 'paid')


def _settle_claims(out):
    """claim.final_status follows the adjudication chain, not arrival order: a reversal that arrives after the replacement
    payment no longer makes a repriced, repaid claim look reversed. The arrival-order value stays in received_final_status."""
    _add(out, 'claim', 'received_final_status TEXT', 'live_payer_paid_cents INTEGER')
    out.execute('UPDATE claim SET received_final_status = final_status')
    hist = defaultdict(list)
    for cid, rid, st, paid, orig in out.execute(
            """SELECT json_extract(payload,'$.claim_id'), record_id, json_extract(payload,'$.status'),
                      json_extract(payload,'$.payer_paid_cents'), json_extract(payload,'$.original_adjudication_record_id')
               FROM record WHERE stream = 'medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL
               ORDER BY event_date, available_date, json_extract(payload,'$.available_time'), record_id"""):
        hist[cid].append({'record_id': rid, 'status': st, 'payer_paid_cents': paid, 'reverses': orig})
    rows = [(*settle_history(h), cid) for cid, h in hist.items()]
    out.executemany('UPDATE claim SET final_status = ?, live_payer_paid_cents = ? WHERE claim_id = ?', rows)
    return {'claims': len(rows), 'final_status_changed': out.execute(
                'SELECT count(*) FROM claim WHERE final_status IS NOT received_final_status').fetchone()[0],
            'net_paid_differs_from_live_payment': out.execute(
                'SELECT count(*) FROM claim WHERE net_payer_paid_cents IS NOT live_payer_paid_cents').fetchone()[0]}


def _claim_codes(out):
    """Claim-level standard codes from the claim's latest adjudication version (MS-DRG, ICD-10 diagnoses, NPIs, bill type)."""
    cols = (('drg', '$.ms_drg'), ('drg_grouper_version', '$.grouper_version'), ('principal_diagnosis', '$.principal_diagnosis'),
            ('diagnosis_codes', '$.diagnosis_codes'), ('billing_npi', '$.billing_provider_npi'), ('rendering_npi', '$.rendering_provider_npi'),
            ('place_of_service', '$.place_of_service'), ('type_of_bill', '$.type_of_bill'), ('revenue_code', '$.revenue_code'),
            ('service_code_system', '$.service_code_system'), ('service_code', '$.service_code'), ('local_charge_code', '$.local_charge_code'),
            ('admission_date', '$.admission_date'), ('discharge_date', '$.discharge_date'))
    _add(out, 'claim', *(f'{c} TEXT' for c, _ in cols))
    out.execute('UPDATE claim SET (' + ', '.join(c for c, _ in cols) + ') = (SELECT ' +
                ', '.join(f"CAST(json_extract(r.payload,'{p}') AS TEXT)" for _, p in cols) +
                " FROM current_record r WHERE r.stream = 'medical_claims' AND r.business_id = claim.claim_id)")
    # Legacy local codes stayed in service_code before v2.0; keep one local column across releases.
    out.execute("UPDATE claim SET local_charge_code = service_code WHERE local_charge_code IS NULL AND service_code_system = 'synthetic_local'")
    _add(out, 'claim', 'cpt TEXT', 'hcpcs TEXT')
    out.execute("UPDATE claim SET cpt = CASE WHEN service_code_system = 'CPT' THEN service_code END, "
                "hcpcs = CASE WHEN service_code_system = 'HCPCS' THEN service_code END")
    out.execute('CREATE INDEX claim_drg ON claim(drg)')


def _entities(out, path):
    """Clinician and organization reference rows (NPI, NUCC taxonomy, TIN) from the build's received warehouse."""
    out.executescript("""
    CREATE TABLE clinician(clinician_id TEXT PRIMARY KEY, organization_id TEXT, specialty TEXT, npi TEXT, taxonomy_code TEXT);
    CREATE TABLE organization(organization_id TEXT PRIMARY KEY, organization_type TEXT, display_name TEXT, npi TEXT,
      taxonomy_code TEXT, tin TEXT, city TEXT, state TEXT, postal_code TEXT);
    """)
    source = Path(path).parent / 'warehouse.sqlite3'
    try:
        release = json.loads((Path(path) / 'run.json').read_text())['snapshot']['release']
    except (OSError, KeyError, ValueError):
        return 0
    if not source.is_file(): return 0
    db = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'source_entities'").fetchone(): return 0
        rows = [(k, json.loads(p)) for k, p in db.execute('SELECT kind, payload FROM source_entities WHERE release = ?', (release,))]
    finally:
        db.close()
    out.executemany('INSERT OR REPLACE INTO clinician VALUES (?,?,?,?,?)',
                    [(o.get('clinician_id'), o.get('organization_id'), o.get('specialty'), o.get('npi'), o.get('taxonomy_code'))
                     for k, o in rows if k == 'clinicians'])
    out.executemany('INSERT OR REPLACE INTO organization VALUES (?,?,?,?,?,?,?,?,?)',
                    [(o.get('organization_id'), o.get('organization_type'), o.get('display_name'), o.get('npi'), o.get('taxonomy_code'),
                      o.get('tin'), o.get('city'), o.get('state'), o.get('postal_code')) for k, o in rows if k == 'organizations'])
    return len(rows)


def _standards_summary(out):
    """Code coverage and provenance, stored in meta so every answer can state which codes came from the source."""
    one = lambda sql: out.execute(sql).fetchone()
    s = {}
    n, coded = one("SELECT count(DISTINCT record_id), count(DISTINCT CASE WHEN icd10_source = 'source' THEN record_id END) FROM condition WHERE source_table = 'conditions'")
    s['conditions_with_source_icd10'] = [coded, n]
    n, src, dic, dis = one("""SELECT count(DISTINCT record_id), count(DISTINCT CASE WHEN loinc_source = 'source' THEN record_id END),
        count(DISTINCT CASE WHEN loinc_source = 'dictionary' THEN record_id END),
        count(DISTINCT CASE WHEN loinc_source = 'source' AND loinc_dictionary IS NOT NULL AND loinc_dictionary <> loinc THEN record_id END)
        FROM measurement WHERE source_table = 'labs'""")
    s['labs_loinc'] = {'records': n, 'source': src, 'dictionary_fallback': dic, 'source_disagrees_with_dictionary': dis}
    s['medication_rxnorm'] = {k: v for k, v in out.execute(
        "SELECT coalesce(rxnorm_source, 'none'), count(DISTINCT record_id) FROM medication GROUP BY 1")}
    s['claim_lines_cpt_or_hcpcs'] = list(one("SELECT count(CASE WHEN cpt IS NOT NULL OR hcpcs IS NOT NULL THEN 1 END), count(*) FROM claim_line"))
    s['claims_with_drg'] = one('SELECT count(drg) FROM claim')[0]
    s['clinicians_with_npi'] = list(one('SELECT count(npi), count(*) FROM clinician'))
    s['patients_with_birth_date'] = list(one('SELECT count(birth_date), count(*) FROM patient'))
    return s


def _sql_in(values): return '(' + ','.join(repr(v) for v in values) + ')'


def _appointments(out):
    """One row per appointment_id. scheduled_date = scheduled_date of the latest version carrying one (a reschedule moves it),
    else the date of the latest received slot_start (population-feed bookings carry booked_at + slot_start instead); never the
    booking record's own date. booked_date = earliest 'booked' version date, else the earliest booked_date any version carries (native feeds keep only the
    current version but stamp the booking date on each); lead_time_days is NULL when either date is unknown."""
    kept, no_show, cancelled = _sql_in(APPOINTMENT_KEPT), _sql_in(APPOINTMENT_NO_SHOW), _sql_in(APPOINTMENT_CANCELLED)
    out.execute(f"""
    INSERT INTO appointment
    SELECT appointment_id, max(patient_id),
      coalesce(max(CASE WHEN rn_sched = 1 THEN scheduled_date END), max(CASE WHEN rn_slot = 1 THEN slot_date END)),
      coalesce(min(CASE WHEN status = 'booked' THEN event_date END), min(booked_payload)),
      max(CASE WHEN rn_last = 1 THEN status END),
      max(status IN {no_show}), max(status IN {cancelled}), max(status = 'rescheduled'), max(status IN {kept}),
      max(organization_id), max(clinician_id), max(purpose), count(*)
    FROM (SELECT *, row_number() OVER (PARTITION BY appointment_id ORDER BY scheduled_date IS NULL, event_date DESC, record_id DESC) AS rn_sched,
                 row_number() OVER (PARTITION BY appointment_id ORDER BY slot_date IS NULL, event_date DESC, record_id DESC) AS rn_slot
          FROM (SELECT json_extract(payload,'$.appointment_id') AS appointment_id, patient_id, json_extract(payload,'$.status') AS status,
                 substr(json_extract(payload,'$.scheduled_date'), 1, 10) AS scheduled_date,
                 substr(json_extract(payload,'$.slot_start'), 1, 10) AS slot_date,
                 substr(json_extract(payload,'$.booked_date'), 1, 10) AS booked_payload, event_date, organization_id, record_id,
                 json_extract(payload,'$.clinician_id') AS clinician_id, json_extract(payload,'$.purpose') AS purpose,
                 row_number() OVER (PARTITION BY json_extract(payload,'$.appointment_id')
                                    ORDER BY available_date DESC, json_extract(payload,'$.available_time') DESC, record_id DESC) AS rn_last
                FROM record WHERE stream = 'appointments' AND json_extract(payload,'$.appointment_id') IS NOT NULL))
    GROUP BY appointment_id""")
    out.execute('ALTER TABLE appointment ADD COLUMN lead_time_days INTEGER')
    out.execute('ALTER TABLE appointment ADD COLUMN outcome TEXT')
    out.execute("""UPDATE appointment SET lead_time_days = CASE WHEN scheduled_date IS NOT NULL AND booked_date IS NOT NULL
                     THEN CAST(julianday(scheduled_date) - julianday(booked_date) AS INTEGER) END,
                   outcome = CASE WHEN kept = 1 THEN 'kept' WHEN ever_no_show = 1 THEN 'no_show' WHEN ever_cancelled = 1 THEN 'cancelled'
                     WHEN final_status = 'rescheduled' THEN 'rescheduled' WHEN final_status IN """ + _sql_in(APPOINTMENT_SCHEDULED) + """ THEN 'scheduled'
                     WHEN final_status IN """ + _sql_in(APPOINTMENT_REVIEW) + """ THEN 'unassigned' ELSE 'unknown' END""")


# Medicare annual wellness visit HCPCS: G0438 initial, G0439 subsequent. (G0402 = IPPE 'Welcome to Medicare', not an AWV.)
AWV_HCPCS = {'G0438': 'initial', 'G0439': 'subsequent'}


def _wellness_visits(out):
    """wellness_visit: one row per wellness_visit_services record; awv = 1 for a Medicare annual wellness visit (HCPCS G0438/G0439,
    or visit_type medicare_annual_wellness when no code was sent). visit.wellness_visit_type / visit.awv mark the encounter."""
    out.execute("""CREATE TABLE wellness_visit(record_id TEXT PRIMARY KEY, patient_id TEXT, encounter_id TEXT, visit_type TEXT,
        hcpcs_code TEXT, awv INTEGER, awv_kind TEXT, status TEXT, coding_status TEXT, event_date TEXT, available_date TEXT,
        organization_id TEXT)""")
    rows = []
    for rid, pid, payload, d, av, org in out.execute("SELECT record_id, patient_id, payload, event_date, available_date, organization_id "
                                                     "FROM record WHERE stream = 'wellness_visit_services'").fetchall():
        r = json.loads(payload)
        code = str(r.get('hcpcs_code') or r.get('hcpcs') or '').strip().upper() or None
        vtype = r.get('visit_type')
        awv = code in AWV_HCPCS or (code is None and bool(re.search(r'annual[ _]wellness', str(vtype or ''), re.I)))
        rows.append((rid, pid, r.get('encounter_id'), vtype, code, int(awv), AWV_HCPCS.get(code) if awv else None, r.get('status'),
                     r.get('coding_status'), d, av, org))
    out.executemany('INSERT INTO wellness_visit VALUES (' + ','.join('?' * 12) + ')', rows)
    out.execute('CREATE INDEX wellness_visit_patient ON wellness_visit(patient_id, event_date)')
    out.execute('ALTER TABLE visit ADD COLUMN wellness_visit_type TEXT')
    out.execute('ALTER TABLE visit ADD COLUMN awv INTEGER')
    out.execute("""UPDATE visit SET (wellness_visit_type, awv) = (SELECT w.visit_type, max(w.awv) FROM wellness_visit w
                     WHERE w.encounter_id = visit.encounter_id) WHERE encounter_id IN (SELECT encounter_id FROM wellness_visit)""")


def _coverage_and_fills(out, as_of):
    """After the population feeds: (1) patients with no coverage record take coverage from their enrollment spans
    (patient.line_of_business, latest member month), coverage_status active when a span covers the as-of date, else inactive;
    patient.coverage_source says which. (2) medication.picked_up = 1 on rows that are picked-up fills (concepts.picked_up_sql)."""
    out.execute('ALTER TABLE patient ADD COLUMN coverage_source TEXT')
    out.execute("UPDATE patient SET coverage_source = 'coverage_record' WHERE coverage_type IS NOT NULL")
    if 'line_of_business' in _columns(out, 'patient'):
        out.execute("""UPDATE patient SET coverage_type = line_of_business, coverage_source = 'enrollment_span',
            coverage_status = CASE WHEN EXISTS (SELECT 1 FROM enrollment_span e WHERE e.patient_id = patient.patient_id
                AND e.coverage_start <= :d AND (e.coverage_end IS NULL OR e.coverage_end >= :d))
              THEN 'active' ELSE 'inactive' END
            WHERE coverage_type IS NULL AND line_of_business IS NOT NULL""", {'d': as_of})
    out.execute('ALTER TABLE medication ADD COLUMN picked_up INTEGER')
    out.execute('UPDATE medication SET picked_up = CASE WHEN ' +
                concepts.picked_up_sql(reversed_column='fill_reversed' in _columns(out, 'medication')) + ' THEN 1 ELSE 0 END')


def _derived(out):
    """Entity tables that resolve each stream's versioning, so queries count things, not records."""
    _appointments(out)
    _wellness_visits(out)
    out.executescript("""
    INSERT INTO claim
    SELECT claim_id, max(patient_id), max(payer), max(service_date), max(benefit_year),
      max(CASE WHEN rn_first = 1 THEN status END), max(CASE WHEN rn_last = 1 THEN status END),
      max(status = 'denied'),
      coalesce(max(CASE WHEN status = 'paid' THEN ord END) > min(CASE WHEN status = 'denied' THEN ord END), 0),
      sum(CASE status WHEN 'paid' THEN coalesce(payer_paid_cents,0) WHEN 'reversed' THEN -abs(coalesce(payer_paid_cents,0)) ELSE 0 END),
      max(billed_cents), max(allowed_cents), min(available_date), max(available_date), max(service_organization_id), count(*)
    FROM (SELECT *, coalesce(available_date,'') || coalesce(available_time,'') || record_id AS ord,
                 row_number() OVER (PARTITION BY claim_id ORDER BY available_date, available_time, record_id) AS rn_first,
                 row_number() OVER (PARTITION BY claim_id ORDER BY available_date DESC, available_time DESC, record_id DESC) AS rn_last
          FROM v_claim_rows)
    GROUP BY claim_id;
    """)
    out.executescript("""
    INSERT INTO social_need SELECT patient_id, 'food', json_extract(payload,'$.food_difficulty_reported'), json_extract(payload,'$.instrument'),
      stream, 'food_difficulty_reported', event_date, record_id FROM record WHERE stream = 'social_screens'
      AND json_extract(payload,'$.food_difficulty_reported') IS NOT NULL;
    INSERT INTO social_need SELECT patient_id, 'transport',
      CASE WHEN json_extract(payload,'$.transport_difficulty_reported') = 1 OR json_extract(payload,'$.transport') = 'unreliable' THEN 1 ELSE 0 END,
      json_extract(payload,'$.instrument'), stream,
      CASE WHEN json_extract(payload,'$.transport_difficulty_reported') IS NOT NULL THEN 'transport_difficulty_reported' ELSE 'transport' END,
      event_date, record_id FROM record WHERE stream = 'social_screens'
      AND (json_extract(payload,'$.transport_difficulty_reported') IS NOT NULL OR json_extract(payload,'$.transport') IS NOT NULL);
    INSERT INTO social_need SELECT patient_id, 'cost_of_care', json_extract(payload,'$.difficulty_paying_for_care_reported'), json_extract(payload,'$.instrument'),
      stream, 'difficulty_paying_for_care_reported', event_date, record_id FROM record WHERE stream = 'social_screens'
      AND json_extract(payload,'$.difficulty_paying_for_care_reported') IS NOT NULL;
    INSERT INTO social_need SELECT patient_id, 'cost_of_care', json_extract(payload,'$.difficulty_paying_medical_bills'), 'financial_screen',
      stream, 'difficulty_paying_medical_bills', event_date, record_id FROM record WHERE stream = 'financial_screens'
      AND json_extract(payload,'$.difficulty_paying_medical_bills') IS NOT NULL;
    CREATE INDEX social_need_patient ON social_need(need, patient_id);
    """)
    for stream, key in VERSIONED_STREAMS.items():
        out.execute(f"""INSERT INTO current_record
            SELECT stream, '{key}', bid, record_id, patient_id, n, first_av, available_date, event_date, payload FROM (
              SELECT r.*, json_extract(r.payload,'$.{key}') AS bid, count(*) OVER w AS n, min(r.available_date) OVER w AS first_av,
                     row_number() OVER (PARTITION BY json_extract(r.payload,'$.{key}')
                                        ORDER BY r.available_date DESC, json_extract(r.payload,'$.available_time') DESC, r.record_id DESC) AS rn
              FROM record r WHERE r.stream = '{stream}' AND json_extract(r.payload,'$.{key}') IS NOT NULL
              WINDOW w AS (PARTITION BY json_extract(r.payload,'$.{key}'))) WHERE rn = 1""")
    out.execute('CREATE INDEX current_record_patient ON current_record(stream, patient_id)')
    _medications(out)
    _revenue_cycle(out)
    out.execute("""INSERT INTO measurement_latest
        SELECT patient_id, concept, event_date, value_canonical, unit_canonical, n, mn, record_id FROM (
          SELECT m.*, count(*) OVER d AS n, min(m.value_canonical) OVER d AS mn,
                 row_number() OVER (PARTITION BY m.patient_id, m.concept ORDER BY m.event_date DESC, m.available_date DESC, m.record_id DESC) AS rn
          FROM measurement m WHERE m.concept IS NOT NULL AND m.value_canonical IS NOT NULL AND m.event_date IS NOT NULL
          WINDOW d AS (PARTITION BY m.patient_id, m.concept, m.event_date)) WHERE rn = 1""")
    for col in ('start_time TEXT', 'end_time TEXT'):
        out.execute(f'ALTER TABLE visit ADD COLUMN {col}')
    out.execute("""CREATE TEMP TABLE visit_times AS SELECT json_extract(payload,'$.encounter_id') AS encounter_id,
                     min(coalesce(json_extract(payload,'$.start_time'), json_extract(payload,'$.event_time'))) AS st,
                     max(json_extract(payload,'$.end_time')) AS et FROM record WHERE stream = 'encounters' GROUP BY 1""")
    out.execute('CREATE INDEX temp.visit_times_enc ON visit_times(encounter_id)')
    out.execute('UPDATE visit SET start_time = (SELECT st FROM visit_times t WHERE t.encounter_id = visit.encounter_id), '
                'end_time = (SELECT et FROM visit_times t WHERE t.encounter_id = visit.encounter_id)')
    out.execute('ALTER TABLE measurement ADD COLUMN event_time TEXT')
    out.execute("""UPDATE measurement SET event_time = (SELECT coalesce(json_extract(r.payload,'$.collected_time'), json_extract(r.payload,'$.event_time'))
                     FROM record r WHERE r.record_id = measurement.record_id)""")
    out.execute("ALTER TABLE visit ADD COLUMN in_person INTEGER")
    out.execute("ALTER TABLE visit ADD COLUMN is_practice INTEGER")
    out.execute("ALTER TABLE visit ADD COLUMN first_available_date TEXT")
    out.execute("""UPDATE visit SET in_person = CASE WHEN type_concept = 'telehealth_contact' OR lower(type) IN ('telephone','virtual','video','phone')
                     THEN 0 ELSE 1 END, is_practice = (organization_id LIKE '%PRACTICE%')""")
    out.execute("""CREATE TEMP TABLE first_arrival AS SELECT encounter_id, min(available_date) AS first_av FROM encounter
                     WHERE source_table = 'encounters' AND encounter_id IS NOT NULL GROUP BY 1""")
    out.execute('CREATE INDEX temp.first_arrival_enc ON first_arrival(encounter_id)')
    out.execute('UPDATE visit SET first_available_date = (SELECT first_av FROM first_arrival f WHERE f.encounter_id = visit.encounter_id)')
    out.execute("ALTER TABLE patient ADD COLUMN age_latest INTEGER")
    out.execute("""UPDATE patient SET age_latest = coalesce((SELECT CAST(json_extract(r.payload,'$.age_reported') AS INTEGER) FROM record r
                     WHERE r.stream = 'demographic_updates' AND r.patient_id = patient.patient_id
                       AND json_extract(r.payload,'$.age_reported') IS NOT NULL
                     ORDER BY r.event_date DESC, r.available_date DESC LIMIT 1), age_reported)""")
    # LOS in one clock: admit_date is the local encounter date while discharge_time_utc is UTC, so midnights are counted between
    # the UTC dates of the admit and discharge timestamps (los_days) and elapsed time between the timestamps (los_days_elapsed).
    # Without both timestamps: local admit and local (ADT) discharge dates when the discharge date is local, else NULL.
    out.execute("ALTER TABLE stay ADD COLUMN los_days INTEGER")
    out.execute("ALTER TABLE stay ADD COLUMN los_days_elapsed REAL")
    out.execute("""UPDATE stay SET
        los_days = CASE WHEN admit_time_utc IS NOT NULL AND discharge_time_utc IS NOT NULL
                          THEN CAST(julianday(substr(discharge_time_utc,1,10)) - julianday(substr(admit_time_utc,1,10)) AS INTEGER)
                        WHEN discharge_date_source = 'adt' THEN CAST(julianday(discharge_date) - julianday(admit_date) AS INTEGER) END,
        los_days_elapsed = CASE WHEN admit_time_utc IS NOT NULL AND discharge_time_utc IS NOT NULL
                          THEN round(julianday(discharge_time_utc) - julianday(admit_time_utc), 4) END
        WHERE discharge_date IS NOT NULL""")
    out.executescript("CREATE INDEX appointment_patient ON appointment(patient_id); CREATE INDEX claim_patient ON claim(patient_id);")
    _normalize(out)  # last: fills columns added above (visit.in_person, measurement concept)
    # The dictionary maps the 'glomerular filtration rate' name to an eGFR LOINC; that code is wrong for unnormalised GFR rows.
    out.execute("UPDATE measurement SET loinc = NULL, loinc_source = NULL WHERE concept = ? AND loinc_source = 'dictionary'", (GFR_UNNORMALIZED,))


def _drug_class_vocabulary(out):
    from .drug_classes import CLASSES, GROUPS
    out.execute('CREATE TABLE drug_class_vocabulary(drug_class TEXT, class_group TEXT)')
    rows = [(c, g) for g, members in GROUPS.items() for c in members]
    rows += [(c, None) for c in CLASSES if not any(c in m for m in GROUPS.values())]
    out.executemany('INSERT INTO drug_class_vocabulary VALUES (?,?)', sorted(rows, key=lambda r: (r[0], r[1] or '')))


def _local_discharges(rows):
    """Encounter records with end_time replaced by the ADT discharge date of the same encounter (the UTC end timestamp can
    fall on the next calendar day), for stay construction. Returns (rows, {discharge date: (source, UTC end time)})."""
    adt = {}
    for r in rows:
        if not r.get('encounter_id') or not r.get('event_date'): continue
        if (r.get('source_table') == 'adt' and r.get('event') == 'discharge') or \
           (r.get('source_table') == 'adt_messages' and r.get('trigger_event') == 'A03'):
            adt[r['encounter_id']] = r['event_date'][:10]
    out, utc = [], {}
    for r in rows:
        if r.get('source_table') == 'encounters' and r.get('end_time'):
            local = adt.get(r.get('encounter_id'))
            day = local or str(r['end_time'])[:10]
            prior = utc.get(day)
            if not prior or (prior[0] != 'adt' and local) or (prior[0] == ('adt' if local else 'encounter_end_time_utc') and str(r['end_time']) > prior[1]):
                utc[day] = ('adt' if local else 'encounter_end_time_utc', str(r['end_time']))
            if local: r = {**r, 'end_time': local + 'T12:00:00'}
        out.append(r)
    return out, utc


def build(path):
    path = Path(path)
    started = time.perf_counter()
    profile = source_profile.load(path)
    resolved = {c: concepts.resolve(c, profile) for c in (*concepts.CONDITIONS, *concepts.MEASUREMENTS, *concepts.ENCOUNTERS)}
    cond_labels = defaultdict(set)   # (stream, field, value) -> concepts
    meas_names = defaultdict(set)
    enc_values = defaultdict(set)
    wide = defaultdict(set)          # vitals column -> concepts
    for c, r in resolved.items():
        for b in r['bindings']:
            if 'values' in b and r['kind'] == 'condition':
                for v in b['values']: cond_labels[(b['stream'], b['field'], v)].add(c)
            elif 'values' in b:
                for v in b['values']: enc_values[(b['stream'], b['field'], v)].add(c)
            elif 'names' in b:
                for v in b['names']: meas_names[(b['stream'], v)].add(c)
            else:
                wide[b['value_field']].add(c)
    stay_type = re.compile(aggregate.STAY_TYPES, re.I)
    target = path / 'semantic.sqlite3'
    tmp = path / 'semantic.sqlite3.tmp'
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    out.executescript(DDL)
    src = sqlite3.connect(f'file:{path / "stack.sqlite3"}?mode=ro', uri=True)
    try:
        people = {pid: json.loads(p) for pid, p in src.execute('SELECT id, payload FROM patients')}
        current, per_patient = None, []

        def flush(pid, rows):
            if pid is None: return
            emit(pid, rows)

        cp = defaultdict(lambda: [0, None, None])
        def note(concept, kind, pid, d):
            e = cp[(concept, kind, pid)]; e[0] += 1
            if d: e[1] = min(e[1] or d, d); e[2] = max(e[2] or d, d)

        def emit(pid, rows):
            for r in rows:
                st = r.get('source_table')
                rid = r.get('record_id')
                d = _date(r)
                for k, v in r.items():
                    if k.endswith('_record_id') and isinstance(v, str):
                        out.execute('INSERT INTO link VALUES (?,?,?,?)', (rid, st, k, v))
                    elif k.endswith('_record_ids') and isinstance(v, list):
                        out.executemany('INSERT INTO link VALUES (?,?,?,?)', [(rid, st, k, x) for x in v if isinstance(x, str)])
                out.execute('INSERT OR IGNORE INTO record VALUES (?,?,?,?,?,?,?,?)',
                            (rid, pid, st, d, (r.get('recorded_date') or '')[:10] or None, (r.get('available_date') or '')[:10] or None,
                             r.get('organization_id'), json.dumps(r)))
                if st in concepts.CONDITION_STREAMS:
                    spec = concepts.CONDITION_STREAMS[st]
                    found = set()
                    code = r.get(spec['code'])
                    if code: found |= cond_labels.get((st, spec['code'], code), set())
                    label = None
                    for f in spec['labels']:
                        if r.get(f): label = label or r.get(f); found |= cond_labels.get((st, f, r.get(f)), set())
                    for c in sorted(found) or [None]:
                        out.execute('INSERT INTO condition VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                                    (rid, pid, st, c, label, r.get('icd10'), r.get('snomed'), r.get('onset'), d,
                                     (r.get('recorded_date') or '')[:10] or None, (r.get('available_date') or '')[:10] or None, r.get('organization_id')))
                        if c: note(c, 'condition', pid, d)
                if st in concepts.MEASUREMENT_STREAMS:
                    spec = concepts.MEASUREMENT_STREAMS[st]
                    md = _result_date(r) if st == 'labs' else d
                    name = r.get(spec['name']) or next((r.get(f) for f in spec['alt_names'] if r.get(f)), None)
                    series = []
                    if name is not None:
                        series.append((name, r.get(spec['value']), r.get(spec['unit']), meas_names.get((st, name), set())))
                    if st == 'vitals':
                        for col, cs in wide.items():
                            if r.get(col) is not None:
                                series.append((col, r.get(col), concepts.MEASUREMENTS[next(iter(cs))]['unit'], cs))
                    for name, value, unit, cs in series:
                        value = value if type(value) in (int, float) else None
                        if 'egfr' in cs and unit not in (None, '') and unit not in concepts.MEASUREMENTS['egfr']['units']:
                            cs = (cs - {'egfr'}) | {GFR_UNNORMALIZED}  # mL/min GFR / clearance: not a BSA-normalised eGFR
                        for c in sorted(cs) or [None]:
                            canon, cunit = None, None
                            if c == GFR_UNNORMALIZED:
                                note(c, 'measurement', pid, md)
                            elif c:
                                factor = concepts.MEASUREMENTS[c]['units'].get(unit)
                                cunit = concepts.MEASUREMENTS[c]['unit']
                                canon = value * factor if value is not None and factor is not None else None
                                note(c, 'measurement', pid, md)
                            out.execute('INSERT INTO measurement VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                        (rid, pid, st, c, name, value, unit, canon, cunit, r.get('result_status'), md,
                                         (r.get('recorded_date') or '')[:10] or None, (r.get('available_date') or '')[:10] or None, r.get('organization_id')))
                if st in QUESTIONNAIRE_STREAMS:
                    c = _questionnaire_concept(r.get('instrument'))
                    total = r.get('total') if r.get('total') is not None else r.get('score')
                    total = total if type(total) in (int, float) else None
                    if c or r.get('instrument'):
                        out.execute('INSERT INTO measurement VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                    (rid, pid, st, c, r.get('instrument'), total, 'points', total if c else None, 'points' if c else None,
                                     r.get('result_status') or r.get('status') or 'final', d, (r.get('recorded_date') or '')[:10] or None,
                                     (r.get('available_date') or '')[:10] or None, r.get('organization_id')))
                        if c: note(c, 'measurement', pid, d)
                if st in ('encounters', 'adt'):
                    field = 'type' if st == 'encounters' else 'event'
                    cs = set()
                    for f in ('type', 'event', 'patient_class'):
                        if r.get(f) is not None: cs |= enc_values.get((st, f, r.get(f)), set())
                    for c in sorted(cs) or [None]:
                        out.execute('INSERT INTO encounter VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                    (rid, pid, st, r.get('encounter_id'), r.get(field), c, r.get('status'), d,
                                     (r.get('end_time') or '')[:10] or None, (r.get('available_date') or '')[:10] or None,
                                     r.get('organization_id'), r.get('attending_clinician_id'), r.get('disposition')))
                        if c: note(c, 'encounter', pid, d)
            stay_rows, utc_end = _local_discharges(rows)
            utc_start = {}  # local admit day -> earliest UTC start timestamp of a stay encounter that day (same clock as discharge_time_utc)
            for r in rows:
                if r.get('source_table') != 'encounters' or not stay_type.match(str(r.get('type') or '').strip()): continue
                day, t = (r.get('event_date') or '')[:10], r.get('start_time') or r.get('event_time')
                if day and t and (day not in utc_start or str(t) < utc_start[day]): utc_start[day] = str(t)
            for s in aggregate._stays(stay_rows, {'start': '', 'end': ''}, '9999-12-31'):
                known = s['discharge_known']; src = utc_end.get(s['discharge']) if known else None
                out.execute('INSERT INTO stay VALUES (?,?,?,?,?,?,?,?,?,?,?)', (pid, s['admit'], s['discharge'] if known else None,
                                                                          s['type'], s['organization'], int(s['readmitted']),
                                                                          s['days_to_readmission'], int(known),
                                                                          (src[0] if src else 'encounter_end_time_utc') if known else None,
                                                                          src[1] if src else None, utc_start.get(s['admit'])))
            # Patient attributes (same rules as aggregate).
            person = people.get(pid, {})
            pcp = [r for r in rows if r.get('source_table') == 'care_team_relationships' and r.get('role') in aggregate.PCP_ROLES
                   and r.get('status') in (None, 'assigned')]
            pcp = max(pcp, key=lambda r: (r.get('effective_start') or r.get('event_date') or '')) if pcp else {}
            cov = [r for r in rows if r.get('source_table') == 'coverage']
            cov = max(cov, key=lambda r: r.get('event_date') or '') if cov else {}
            out.execute('INSERT OR REPLACE INTO patient VALUES (?,?,?,?,?,?,?)',
                        (pid, person.get('age_reported'), person.get('sex_reported'), cov.get('coverage_type'), cov.get('status'),
                         pcp.get('clinician_id'), pcp.get('organization_id')))

        for pid, payload in src.execute('SELECT patient, payload FROM records ORDER BY patient'):
            if pid != current:
                flush(current, per_patient); current, per_patient = pid, []
            per_patient.append(json.loads(payload))
        flush(current, per_patient)
        for pid, person in people.items():  # roster patients without records
            out.execute('INSERT OR IGNORE INTO patient(patient_id, age_reported, sex_reported) VALUES (?,?,?)',
                        (pid, person.get('age_reported'), person.get('sex_reported')))
        out.execute("""INSERT INTO visit SELECT encounter_id, patient_id, type, type_concept, status, event_date, end_date, available_date,
            organization_id, attending_clinician_id, disposition, n FROM (
              SELECT e.*, count(*) OVER (PARTITION BY coalesce(e.encounter_id, e.record_id)) AS n,
                     row_number() OVER (PARTITION BY coalesce(e.encounter_id, e.record_id)
                                        ORDER BY (e.status = 'completed') DESC, e.available_date DESC, e.record_id DESC) AS rn
              FROM encounter e WHERE e.source_table = 'encounters') WHERE rn = 1""")
        out.executemany('INSERT INTO concept_patient VALUES (?,?,?,?,?,?)',
                        [(c, k, pid, n, f, l) for (c, k, pid), (n, f, l) in cp.items()])
        out.executescript(INDEXES)
        out.execute("CREATE INDEX record_patient_stream ON record(patient_id, stream)")
        _standards(out, people)  # source standard codes first: the dictionary in _normalize only fills their gaps
        _derived(out)  # after indexes: derived entities read the record table by stream and patient
        settlement = _settle_claims(out)
        _drug_class_vocabulary(out)
        _claim_codes(out)
        _entities(out, path)
        meta = json.loads((path / 'run.json').read_text())
        feeds = population_feeds.build(out, people, meta['snapshot']['as_of'], path)  # after claims/entities: reads claim NPIs, organization
        _coverage_and_fills(out, meta['snapshot']['as_of'])  # after feeds: enrollment spans, fill reversals
        from . import population_events
        feeds['population_events'] = population_events.build(out)  # last: payer evidence folded into the entity tables
        # Build log steps 8, 9, 11: adjudication history (every version, with CARC), record corrections across streams and
        # canonical picked-up fills. History needs the build's received warehouse, which keeps superseded versions.
        from . import adjudication_history, fill_events
        warehouse = path.parent / 'warehouse.sqlite3'
        if adjudication_history.has_history(warehouse):
            feeds['adjudication_history'] = adjudication_history.build(out, warehouse)
            feeds['record_correction'] = adjudication_history.build_corrections(out, warehouse)
            if meta.get('schema') == 'agere-native-stack/1':
                # Native staging retires superseded versions from the record store, so payer_claim's first status and
                # version count come from the history (other builds keep their established values).
                feeds['payer_claim_history'] = adjudication_history.reconcile_payer_claim(out)
        feeds['fill_events'] = fill_events.build(out)
        out.executemany('INSERT INTO meta VALUES (?,?)', [('version', VERSION), ('source_digest', meta['source_digest']),
                                                          ('as_of', meta['snapshot']['as_of']),
                                                          ('standards', json.dumps(_standards_summary(out))),
                                                          ('claim_settlement', json.dumps(settlement)),
                                                          ('population_feeds', json.dumps(feeds))])
        out.commit()
    finally:
        src.close(); out.close()
    from . import note_facts
    note_facts.build(tmp)  # facts found only in free text, read from and written into the new build
    tmp.replace(target)
    from . import record_store; record_store.build(path)  # stream-clustered record copy for population scans (record_store.py)
    return {'path': str(target), 'elapsed_seconds': round(time.perf_counter() - started, 1)}


def ensure(path):
    path = Path(path)
    target = path / 'semantic.sqlite3'
    meta = json.loads((path / 'run.json').read_text())
    if target.exists():
        db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
        try:
            row = dict(db.execute('SELECT key, value FROM meta').fetchall())
        finally:
            db.close()
        if row.get('version') == VERSION and row.get('source_digest') == meta['source_digest']:
            return target
    build(path)
    return target


def describe(path):
    """Schema + vocabulary for planners: table columns, meanings, concept ids."""
    target = ensure(path)
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        tables = {}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name <> 'meta'"):
            cols = [r[1] for r in db.execute(f'PRAGMA table_info({name})')]
            tables[name] = {'columns': cols, 'meaning': SCHEMA_DOC.get(name, '')}
        streams = [r[0] for r in db.execute('SELECT DISTINCT stream FROM record ORDER BY stream')]
    finally:
        db.close()
    return {'tables': tables, 'record_streams': streams, 'concepts': concepts.catalog()}
