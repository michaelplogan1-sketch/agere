"""Measure readiness: which hospital safety, quality, regulatory, revenue cycle, perioperative and workforce measures this warehouse can compute, and what is missing.

The first question a hospital's quality or safety team asks of a new analytics product is not a number: it is "can you compute
this measure from our data?". Each measure here names its steward and specification, its computability class, and the data
elements its specification needs. Every element is probed in code against the warehouse in use and counted. A measure is
  ready      every required element is present in enough rows to compute it,
  partial    some are present: part of the measure can be computed (the recipe says which part),
  not_ready  the elements that define it are absent.
Computability classes (reports/Hospital analytics question catalogue.md): hospital_computed (the hospital's own data gives the
official value), cms_claims (CMS computes the official value from claims; a hospital can only estimate it), survey_vendor (a
survey vendor returns it), composite (derived from the others).

Definitions are taken from the CMS eCQI Resource Center 2026 pages (ecqi.healthit.gov/ecqm/hosp-inpt/2026/...), AHRQ, CDC NHSN
and CMS program pages, as summarised in that report; the probes are this module's, code-reviewed, not learned.

Governed tables on every connection (store.connect): measure_readiness (one row per measure), measure_requirement (one row per
measure and data element, with the rows found), measure_alias (names a person may use for a measure).
"""
import re
import sqlite3

from . import store

IN_STAY = "JOIN stay_episode e ON e.patient_id = {t}.patient_id AND substr({t}.{d}, 1, 10) BETWEEN e.admit_date AND COALESCE(e.discharge_date, e.admit_date)"
ADMIN = "FROM medication m WHERE m.source_stream = 'medication_administrations'"
MAR = "FROM medication_administration m WHERE 1 = 1"   # the FHIR source's medication administration record

# element key -> (plain description, SQL returning one count, rows needed)
ELEMENTS = {
    'glucose_timed_in_stays': ('timed glucose results during inpatient stays',
        "SELECT COUNT(*) FROM measurement m " + IN_STAY.format(t='m', d='event_date') + " WHERE m.concept = 'glucose' AND m.event_time IS NOT NULL", 30),
    'diabetes_diagnosis': ('diabetes diagnoses', "SELECT COUNT(*) FROM condition WHERE concept IN ('diabetes', 'type_2_diabetes', 'type_1_diabetes')", 10),
    'admission_times': ('inpatient admission times', "SELECT COUNT(*) FROM hospital_visit WHERE has_inpatient = 1 AND inpatient_admit IS NOT NULL", 30),
    'admin_times': ('medication administration times (not only dates)', ("SELECT COUNT(*) " + MAR + " AND m.administered_at IS NOT NULL", "SELECT COUNT(*) " + ADMIN + " AND length(m.event_date) > 10"), 30),
    'admin_hypoglycemic': ('administrations of insulin or other hypoglycemic medicines', tuple("SELECT COUNT(*) " + s + " AND m.drug_class IN ('insulin', 'sulfonylurea', 'meglitinide')" for s in (MAR, ADMIN)), 10),
    'admin_opioid': ('opioid administrations', tuple("SELECT COUNT(*) " + s + " AND m.drug_class = 'opioid'" for s in (MAR, ADMIN)), 10),
    'admin_antagonist': ('opioid antagonist (naloxone, nalmefene) administrations', tuple("SELECT COUNT(*) " + s + " AND (m.drug_class = 'opioid_antagonist' OR lower(m.drug_name) LIKE '%naloxone%')" for s in (MAR, ADMIN)), 1),
    'admin_location': ('the location of each administration (to exclude the operating room)',
        ("SELECT COUNT(*) FROM medication_administration WHERE location_type IS NOT NULL", "SELECT COUNT(*) FROM pragma_table_info('medication') WHERE name IN ('location', 'unit', 'department', 'administration_location')"), 1),
    'serial_creatinine': ('stays of 48 hours or more with at least three timed creatinine results',
        "SELECT COUNT(*) FROM (SELECT e.patient_id, e.admit_date FROM stay_episode e JOIN measurement m ON m.patient_id = e.patient_id AND m.concept = 'creatinine' "
        "AND m.event_time IS NOT NULL AND m.event_date BETWEEN e.admit_date AND e.discharge_date WHERE julianday(e.discharge_date) - julianday(e.admit_date) >= 2 "
        "GROUP BY 1, 2 HAVING COUNT(*) >= 3)", 30),
    'present_on_admission': ('present-on-admission indicators on diagnoses', ("SELECT COUNT(*) FROM encounter_diagnosis WHERE poa IS NOT NULL",
        "SELECT (SELECT COUNT(*) FROM pragma_table_info('payer_claim_diagnosis') WHERE name LIKE '%poa%') + (SELECT COUNT(*) FROM pragma_table_info('encounter_diagnosis') WHERE name LIKE '%poa%') "
        "+ (SELECT COUNT(*) FROM pragma_table_info('condition') WHERE name LIKE '%poa%' OR name LIKE '%present_on_admission%')"), 1),
    'pressure_injury_documented': ('pressure injury diagnoses or assessments during stays',
        ("SELECT COUNT(*) FROM encounter_diagnosis WHERE icd10 LIKE 'L89%'", "SELECT COUNT(*) FROM diagnosis_any d " + IN_STAY.format(t='d', d='event_date') + " WHERE d.icd10 LIKE 'L89%'"), 10),
    'falls_documented': ('documented falls during stays', ("SELECT COUNT(*) FROM encounter_diagnosis WHERE icd10 GLOB 'W[01][0-9]*'", "SELECT COUNT(*) FROM diagnosis_any d " + IN_STAY.format(t='d', d='event_date') + " WHERE d.icd10 GLOB 'W[01][0-9]*'"), 5),
    'operative_times': ('operative procedure start and end times', ("SELECT COUNT(*) FROM procedure_event WHERE start IS NOT NULL AND end IS NOT NULL", "SELECT COUNT(*) FROM pragma_table_info('procedure_line') WHERE name LIKE '%time%' OR name LIKE '%start%'"), 1),
    'device_days': ('central line and urinary catheter days', "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%device%' OR name LIKE '%catheter%' OR name LIKE '%line_day%'", 1),
    'cultures': ('blood and urine culture results', "SELECT COUNT(*) FROM measurement WHERE lower(name) LIKE '%culture%' OR concept LIKE '%culture%'", 10),
    'lactate_timed': ('timed lactate results', "SELECT COUNT(*) FROM measurement WHERE lower(name) LIKE '%lactate%' AND event_time IS NOT NULL", 10),
    'antibiotic_admin_times': ('timed antibiotic administrations',
        tuple("SELECT COUNT(*) " + s + c + " AND m.drug_class IN ('cephalosporin', 'macrolide', 'tetracycline_antibiotic', 'penicillin', 'fluoroquinolone', 'carbapenem')" for s, c in ((MAR, ' AND m.administered_at IS NOT NULL'), (ADMIN, ' AND length(m.event_date) > 10'))), 10),
    'sepsis_hospitalizations': ('sepsis hospitalizations by principal diagnosis', "SELECT COUNT(*) FROM hospital_visit WHERE has_inpatient = 1 AND is_sepsis = 1", 5),
    'ed_departure_times': ('usable ED arrival and departure times', "SELECT COUNT(*) FROM hospital_visit WHERE ed_minutes IS NOT NULL", 30),
    'decision_to_admit_times': ('decision-to-admit times', "SELECT COUNT(*) FROM hospital_visit WHERE boarding_minutes IS NOT NULL", 30),
    'treatment_space_times': ('time placed in an ED treatment space', "SELECT COUNT(*) FROM pragma_table_info('visit_group') WHERE name LIKE '%room%' OR name LIKE '%bed_assigned%' OR name LIKE '%treatment%'", 1),
    'left_before_evaluation': ('ED visits recorded as left before evaluation', "SELECT COUNT(*) FROM hospital_visit WHERE lower(disposition) LIKE '%left%' OR lower(disposition) LIKE '%lwbs%' OR lower(disposition) LIKE '%ama%'", 1),
    'merged_stays': ('inpatient stays, each counted once', "SELECT COUNT(*) FROM stay_episode", 30),
    'principal_diagnosis': ('principal diagnoses on inpatient visits', "SELECT COUNT(*) FROM hospital_visit WHERE has_inpatient = 1 AND principal_icd10 IS NOT NULL", 30),
    'discharge_disposition': ('discharge dispositions on inpatient visits', "SELECT COUNT(*) FROM hospital_visit WHERE has_inpatient = 1 AND disposition IS NOT NULL", 30),
    'first_day_vitals_labs': ('vital signs and labs in the first 24 hours of a stay',
        "SELECT COUNT(*) FROM measurement m JOIN stay_episode e ON e.patient_id = m.patient_id AND m.event_date = e.admit_date", 30),
    'survey_results': ('patient experience survey results', "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%hcahps%' OR name LIKE '%survey%' OR name LIKE '%experience%'", 1),
    # revenue cycle (2026-11-16): the health system's own submitted claims, claim_source 'medical_claims'
    'hospital_claims_adjudicated': ("the health system's submitted claims with an adjudication date",
        "SELECT COUNT(*) FROM claim WHERE claim_source = 'medical_claims' AND first_adjudication_date IS NOT NULL", 100),
    'claim_first_final_status': ('first and final adjudication status on each claim',
        "SELECT COUNT(*) FROM claim WHERE claim_source = 'medical_claims' AND first_status IS NOT NULL AND final_status IS NOT NULL", 100),
    'claim_submit_cash_dates': ('claim submission and first payer cash dates',
        "SELECT COUNT(*) FROM claim WHERE claim_source = 'medical_claims' AND first_submitted_date IS NOT NULL AND first_payer_cash_date IS NOT NULL", 100),
    'claim_denial_reasons': ("denial reason codes (835 CARC/RARC) on the health system's own claims",
        "SELECT COUNT(*) FROM pragma_table_info('claim') WHERE name IN ('carc', 'denial_reason', 'rarc')", 1),
    'inpatient_claim_drg': ('MS-DRG on inpatient claims', "SELECT COUNT(*) FROM claim WHERE claim_source = 'medical_claims' AND drg IS NOT NULL", 30),
    'drg_weights': ('the CMS MS-DRG relative weight table', "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%drg_weight%' OR name LIKE '%ms_drg%'", 1),
    'ar_balances': ('accounts receivable balances and net patient service revenue (general ledger)',
        "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%receivable%' OR name LIKE '%ar_balance%' OR name LIKE '%general_ledger%'", 1),
    'unbilled_accounts': ('discharged-not-final-billed accounts with gross charges',
        "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%unbilled%' OR name LIKE '%dnfb%' OR name LIKE '%patient_account%'", 1),
    'revenue_cycle_expense': ('revenue cycle operating expense (general ledger)', "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%general_ledger%' OR name LIKE '%expense%'", 1),
    # perioperative (AACD Procedural Times Glossary)
    'or_case_times': ('operating room cases with patient in-room and out-of-room times',
        "SELECT COUNT(*) FROM procedure_event WHERE location_type = 'OR' AND start IS NOT NULL AND end IS NOT NULL", 10),
    'or_scheduled_times': ('scheduled case start times', "SELECT COUNT(*) FROM pragma_table_info('procedure_event') WHERE name LIKE '%schedul%' OR name LIKE '%booked%'", 1),
    'or_room_ids': ('the operating room each case used', "SELECT COUNT(*) FROM pragma_table_info('procedure_event') WHERE name LIKE '%room%'", 1),
    'block_schedule': ('the OR block schedule and release log', "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%block%'", 1),
    # workforce
    'staff_hours': ('productive hours worked by job code and unit (timekeeping)',
        "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%timekeep%' OR name LIKE '%payroll%' OR name LIKE '%staff%' OR name LIKE '%shift%'", 1),
    'midnight_census': ('midnight census by unit', "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%census%'", 1),
    'hr_employment': ('employee hires, terminations and open positions (HR)',
        "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%employee%' OR name LIKE '%hr\\_%' ESCAPE '\\' OR name LIKE '%position%'", 1),
}

# measure id, name, steward and specification, class, program, required elements, aliases
MEASURES = (
    ('HH-HYPO', 'Hospital Harm - Severe Hypoglycemia', 'CMS816v5, eCQI 2026', 'hospital_computed', 'IQR, Promoting Interoperability, ORYX (required)',
     ('glucose_timed_in_stays', 'admin_hypoglycemic', 'admin_times'), ('severe hypoglycemia', 'hypoglycemia', 'cms816', 'hh hypo')),
    ('HH-HYPER', 'Hospital Harm - Severe Hyperglycemia', 'CMS871v5, eCQI 2026', 'hospital_computed', 'IQR, Promoting Interoperability, ORYX (required)',
     ('glucose_timed_in_stays', 'diabetes_diagnosis', 'admission_times'), ('severe hyperglycemia', 'hyperglycemia', 'cms871', 'hh hyper')),
    ('HH-ORAE', 'Hospital Harm - Opioid-Related Adverse Events', 'CMS819v4, eCQI 2026', 'hospital_computed', 'IQR (mandatory from CY2027)',
     ('admin_opioid', 'admin_antagonist', 'admin_times', 'admin_location'), ('opioid-related adverse events', 'opioid adverse events', 'orae', 'naloxone rescue', 'cms819')),
    ('HH-AKI', 'Hospital Harm - Acute Kidney Injury', 'CMS832v3, eCQI 2026', 'hospital_computed', 'IQR (mandatory from CY2028)',
     ('serial_creatinine', 'present_on_admission'), ('acute kidney injury', 'aki', 'cms832', 'hospital-acquired aki')),
    ('HH-PI', 'Hospital Harm - Pressure Injury', 'CMS826v3, eCQI 2026', 'hospital_computed', 'IQR (mandatory from CY2028)',
     ('pressure_injury_documented', 'present_on_admission'), ('pressure injury', 'pressure injuries', 'hapi', 'pressure ulcer', 'cms826')),
    ('HH-FI', 'Hospital Harm - Falls with Injury', 'CMS1017v2, eCQI 2026', 'hospital_computed', 'IQR (mandatory from CY2028)',
     ('falls_documented', 'present_on_admission'), ('falls with injury', 'falls', 'inpatient falls', 'cms1017')),
    ('HH-RF', 'Hospital Harm - Postoperative Respiratory Failure', 'CMS1218v2, eCQI 2026', 'hospital_computed', 'IQR (mandatory from CY2028)',
     ('operative_times',), ('postoperative respiratory failure', 'cms1218')),
    ('PSI-90', 'Patient Safety and Adverse Events Composite (PSI 90)', 'AHRQ PSI v2025', 'cms_claims', 'HACRP, Star Rating',
     ('present_on_admission', 'merged_stays'), ('psi 90', 'psi-90', 'psi90', 'patient safety indicators', 'patient safety composite')),
    ('CLABSI', 'Central line-associated bloodstream infection', 'CDC NHSN protocol', 'hospital_computed', 'HACRP, VBP, Leapfrog (via NHSN)',
     ('device_days', 'cultures'), ('clabsi', 'central line infections', 'central line-associated bloodstream infection')),
    ('CAUTI', 'Catheter-associated urinary tract infection', 'CDC NHSN protocol', 'hospital_computed', 'HACRP, VBP, Leapfrog (via NHSN)',
     ('device_days', 'cultures'), ('cauti', 'catheter-associated urinary tract infection', 'catheter infections')),
    ('SEP-1', 'Severe Sepsis and Septic Shock: Management Bundle', 'CMS SEP-1 (chart-abstracted)', 'hospital_computed', 'IQR, VBP',
     ('sepsis_hospitalizations', 'lactate_timed', 'cultures', 'antibiotic_admin_times'), ('sep-1', 'sep1', 'sepsis bundle', 'sepsis bundle compliance')),
    ('ECAT', 'Emergency Care Access and Timeliness', 'CMS1244, CY2026 OPPS final rule', 'hospital_computed', 'OQR (voluntary 2027, mandatory 2028)',
     ('ed_departure_times', 'decision_to_admit_times', 'treatment_space_times', 'left_before_evaluation'),
     ('ecat', 'ed timeliness', 'emergency care access and timeliness', 'ed access and timeliness', 'cms1244')),
    ('HRRP', 'Hospital Readmissions Reduction Program readmission measures', 'CMS HRRP (claims-based)', 'cms_claims', 'HRRP payment adjustment',
     ('merged_stays', 'principal_diagnosis'), ('hrrp', 'readmissions reduction program', 'readmission penalty')),
    ('HWR', 'Hybrid Hospital-Wide Readmission', 'CMS hybrid HWR (claims plus EHR data)', 'cms_claims', 'IQR (70% of discharges must be submitted)',
     ('merged_stays', 'first_day_vitals_labs'), ('hybrid hospital-wide readmission', 'hospital-wide readmission', 'hwr')),
    ('MORT-IH', 'In-hospital mortality (internal)', 'Agere governed definition: death in hospital on inpatient visits', 'hospital_computed', 'Internal quality dashboard',
     ('discharge_disposition',), ('in-hospital mortality', 'inpatient mortality', 'hospital mortality')),
    ('SEPSIS-OUT', 'Sepsis hospitalization outcomes (internal)', 'Agere governed definition: principal diagnosis A40, A41, R65.2', 'hospital_computed', 'Internal quality dashboard; HRRP sepsis readmission from FY2030',
     ('sepsis_hospitalizations', 'merged_stays'), ('sepsis outcomes', 'sepsis mortality', 'sepsis readmissions')),
    ('RC-IDR', 'Initial denial rate (by claims and by billed dollars)', 'HFMA Claim Integrity Task Force', 'hospital_computed', 'Revenue cycle KPI',
     ('hospital_claims_adjudicated', 'claim_first_final_status'), ('initial denial rate', 'first-pass denial rate', 'denial rate')),
    ('RC-FDR', 'Final denial rate', 'HFMA Claim Integrity Task Force', 'hospital_computed', 'Revenue cycle KPI',
     ('hospital_claims_adjudicated', 'claim_first_final_status'), ('final denial rate', 'final denials')),
    ('RC-OVT', 'Denial overturn rate', 'HFMA Claim Integrity Task Force', 'hospital_computed', 'Revenue cycle KPI',
     ('claim_first_final_status',), ('denial overturn rate', 'overturn rate', 'appeal success rate')),
    ('RC-RSN', 'Denials by reason', 'X12 835 CARC/RARC codes', 'hospital_computed', 'Revenue cycle KPI',
     ('claim_first_final_status', 'claim_denial_reasons'), ('denials by reason', 'denial reasons', 'top denial reasons', 'carc')),
    ('RC-DTP', 'Days from claim submission to payment', 'Agere governed definition: first submission to first payer cash', 'hospital_computed', 'Revenue cycle KPI',
     ('claim_submit_cash_dates',), ('days to payment', 'days to pay', 'payment lag')),
    ('RC-CMI', 'Case mix index', 'HFMA MAP Keys (Medicare MS-DRG weights, all payers)', 'hospital_computed', 'Finance KPI',
     ('inpatient_claim_drg', 'drg_weights'), ('case mix index', 'cmi', 'case mix')),
    ('RC-AR', 'Net days in accounts receivable', 'HFMA MAP Key AR-1', 'hospital_computed', 'Revenue cycle KPI',
     ('ar_balances',), ('net days in a/r', 'days in ar', 'days in a/r', 'a/r days', 'ar days')),
    ('RC-DNFB', 'Days in discharged not final billed', 'HFMA MAP Key PB-1', 'hospital_computed', 'Revenue cycle KPI',
     ('unbilled_accounts',), ('dnfb', 'discharged not final billed', 'days in dnfb')),
    ('RC-CTC', 'Cost to collect', 'HFMA MAP Key FM-6', 'hospital_computed', 'Revenue cycle KPI',
     ('revenue_cycle_expense', 'ar_balances'), ('cost to collect',)),
    ('OR-VOL', 'Operating room cases and minutes', 'AACD Procedural Times Glossary (patient in room to out of room)', 'hospital_computed', 'Perioperative KPI',
     ('or_case_times',), ('or volume', 'or cases', 'or minutes', 'surgical volume', 'case volume')),
    ('OR-FCOTS', 'First-case on-time start', 'AACD Procedural Times Glossary', 'hospital_computed', 'Perioperative KPI',
     ('or_case_times', 'or_scheduled_times', 'or_room_ids'), ('first case on time start', 'first-case on-time start', 'fcots', 'on-time starts')),
    ('OR-TURN', 'Room turnover time', 'AACD Procedural Times Glossary item 2.22', 'hospital_computed', 'Perioperative KPI',
     ('or_case_times', 'or_room_ids'), ('turnover time', 'room turnover', 'or turnover')),
    ('OR-BLOCK', 'Block utilization', 'Local definition (raw and adjusted)', 'hospital_computed', 'Perioperative KPI',
     ('or_case_times', 'or_room_ids', 'block_schedule'), ('block utilization', 'block utilisation', 'block release')),
    ('NUR-NHPPD', 'Nursing hours per patient day', 'NDNQI; CDC NHSN Nurse Staffing protocol', 'hospital_computed', 'Leapfrog, NDNQI',
     ('staff_hours', 'midnight_census'), ('nursing hours per patient day', 'nhppd', 'hppd', 'nurse staffing')),
    ('NUR-TURN', 'RN turnover and vacancy', 'NSI Nursing Solutions methodology', 'hospital_computed', 'Workforce KPI',
     ('hr_employment',), ('rn turnover', 'nurse turnover', 'vacancy rate', 'rn vacancy')),
    ('HCAHPS', 'HCAHPS patient experience survey', 'CMS HCAHPS (32 items from 2025 discharges)', 'survey_vendor', 'IQR, VBP, Star Rating',
     ('survey_results',), ('hcahps', 'patient experience', 'patient satisfaction')),
)

_cache = {}


def _key():
    from . import aco   # the warehouse version read from its file directly; a store connection would install this module again
    try:
        return (str(store.path()), str(store.mapping() and store.mapping().get('semantic_layer')), aco._version())
    except sqlite3.Error:
        return (str(store.path()), None, None)


_busy = []


def probe():
    """-> (measure rows, requirement rows), computed once per warehouse version in this process."""
    k = _key()
    if k in _cache: return _cache[k]
    _busy.append(1)   # the probes' own connection must not install these tables again
    try:
        db = store.connect()
    finally:
        _busy.pop()
    found = {}
    for key, (_, sqls, _) in ELEMENTS.items():
        found[key] = 0
        for sql in ((sqls,) if isinstance(sqls, str) else sqls):   # the first source that holds the element wins
            try:
                found[key] = int(db.execute(sql).fetchone()[0] or 0)
            except sqlite3.Error:
                continue
            if found[key]: break
    db.close()
    measures, requirements = [], []
    for mid, name, spec, klass, program, needs, _ in MEASURES:
        present = [n for n in needs if found[n] >= ELEMENTS[n][2]]
        missing = [n for n in needs if n not in present]
        status = 'ready' if not missing else 'partial' if present else 'not_ready'
        measures.append((mid, name, spec, klass, program, status, len(present), len(needs),
                         '; '.join(ELEMENTS[n][0] for n in missing) or None))
        for n in needs:
            requirements.append((mid, n, ELEMENTS[n][0], found[n], ELEMENTS[n][2], int(found[n] >= ELEMENTS[n][2])))
    _cache[k] = (measures, requirements)
    return _cache[k]


def install(db):
    """Create the readiness tables on one connection (called by store.connect, before it is made read-only)."""
    if _busy: return
    measures, requirements = probe()
    db.execute('CREATE TEMP TABLE measure_readiness(measure_id TEXT, measure_name TEXT, specification TEXT, computability_class TEXT, programs TEXT, '
               'status TEXT, elements_present INTEGER, elements_required INTEGER, missing_data TEXT)')
    db.executemany('INSERT INTO measure_readiness VALUES (?,?,?,?,?,?,?,?,?)', measures)
    db.execute('CREATE TEMP TABLE measure_requirement(measure_id TEXT, element TEXT, description TEXT, rows_found INTEGER, rows_needed INTEGER, present INTEGER)')
    db.executemany('INSERT INTO measure_requirement VALUES (?,?,?,?,?,?)', requirements)
    db.execute('CREATE TEMP TABLE measure_alias(alias TEXT, measure_id TEXT)')
    db.executemany('INSERT INTO measure_alias VALUES (?, ?)',
                   [(a, mid) for mid, name, *_, aliases in MEASURES for a in {mid.lower(), name.lower(), *aliases}])


def alias(text):
    """Code only: the measure a phrase names, or None."""
    t = ' '.join(re.sub(r'[^a-z0-9 -]', ' ', str(text).lower()).split())
    for mid, name, *_, aliases in MEASURES:
        if t in {mid.lower(), name.lower(), *aliases}: return mid
    return None


def named_not_ready(question):
    """Code only: the measures this question names (by an alias, as whole words) that this warehouse cannot compute.
    -> [(measure id, name, missing data)]. An answer to one of them would be built on data that is not there (proof_v12:
    'days of revenue in DNFB' computed from claim dates), so the router declines and says what is missing."""
    t = ' ' + ' '.join(re.sub(r'[^a-z0-9/ -]', ' ', str(question).lower()).split()) + ' '
    measures, _ = probe()
    status = {m[0]: m for m in measures}
    out = []
    for mid, name, *_, aliases in MEASURES:
        m = status.get(mid)
        if not m or m[5] != 'not_ready': continue
        if any(f' {a} ' in t for a in {mid.lower(), name.lower(), *aliases} if len(a) > 3 or a in ('cmi', 'dnfb')):
            out.append((mid, name, m[8]))
    return out

