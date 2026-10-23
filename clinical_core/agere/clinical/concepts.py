"""Clinical concept library: one definition per concept, bound to whatever coding
systems and literal labels the received data actually uses.

A definition lists recognisers (code prefixes, code sets, label patterns, unit
conversions). Resolution against the source profile turns them into the exact
observed values to match, so a question about "diabetes" finds ICD-10, SNOMED
and free-text problem labels alike, and the answer can show precisely which
labels were included. Definitions are documentation-level phenotypes, not
adjudicated diagnoses or certified measure value sets.
"""
import re

# --- Condition concepts -------------------------------------------------------
# icd10: code prefixes. text: case-insensitive pattern over condition labels.
# exclude: pattern that removes a label even if `text` matches.
CONDITIONS = {
    'diabetes': {'label': 'Diabetes mellitus (type 1, type 2 or other specified; excludes prediabetes and gestational)',
        'icd10': ['E08', 'E09', 'E10', 'E11', 'E13'],
        'text': r'diabet|\bt[12]dm\b|\biddm\b|\bniddm\b',
        'exclude': r'prediabet|pre-diabet|family history|insipidus|gestational|\bgdm\b|screening'},
    'type_2_diabetes': {'label': 'Type 2 diabetes mellitus',
        'icd10': ['E11'], 'text': r'type 2 diabet|\bt2dm\b|diabetes mellitus type 2|\bniddm\b',
        'exclude': r'prediabet|family history'},
    'type_1_diabetes': {'label': 'Type 1 diabetes mellitus',
        'icd10': ['E10'], 'text': r'type 1 diabet|\bt1dm\b|diabetes mellitus type 1|\biddm\b', 'exclude': r'family history'},
    'prediabetes': {'label': 'Prediabetes (IFG, IGT, prediabetes; not R73.09 elevated A1c)', 'icd10': ['R73.01', 'R73.02', 'R73.03'], 'text': r'prediabet|pre-diabet'},
    'hypertension': {'label': 'Hypertension (essential/secondary; excludes pulmonary and pregnancy-specific)',
        'icd10': ['I10', 'I11', 'I12', 'I13', 'I15', 'I16'],
        'text': r'hypertension|\bhtn\b', 'exclude': r'pulmonary hypertension|preeclampsia|gestational|history of'},
    'heart_failure': {'label': 'Heart failure (any type)',
        'icd10': ['I50', 'I11.0', 'I13.0', 'I13.2'], 'text': r'heart failure|\bchf\b', 'exclude': r'family history'},
    'heart_failure_reduced_ef': {'label': 'Systolic heart failure / HFrEF',
        'icd10': ['I50.2', 'I50.4'], 'text': r'systolic.*(heart failure|chf)|\bhfref\b|reduced ejection'},
    'chronic_kidney_disease': {'label': 'Chronic kidney disease (any stage, incl. diabetic kidney disease and ESRD)',
        'icd10': ['N18', 'E08.2', 'E09.2', 'E10.2', 'E11.2', 'E13.2', 'I12', 'I13'],
        'text': r'chronic kidney disease|\bckd\b|end-stage renal|kidney due to diabet|diabetic kidney|diabetic nephropathy|t2dm with ckd'},
    'copd': {'label': 'COPD / emphysema / chronic obstructive bronchitis',
        'icd10': ['J43', 'J44'], 'text': r'\bcopd\b|chronic obstructive|emphysema'},
    'asthma': {'label': 'Asthma', 'icd10': ['J45'], 'text': r'asthma'},
    'atrial_fibrillation': {'label': 'Atrial fibrillation', 'icd10': ['I48'], 'text': r'atrial fibrillation|\bafib\b'},
    'coronary_artery_disease': {'label': 'Coronary artery / ischemic heart disease incl. prior MI or CABG',
        'icd10': ['I20', 'I21', 'I22', 'I23', 'I24', 'I25'],
        'text': r'ischemic heart|coronary artery|\bascvd\b|myocardial infarction|\bstemi\b|\bnstemi\b|non-st elevation mi|preinfarction|unstable angina'},
    'myocardial_infarction': {'label': 'Myocardial infarction (acute or history)',
        'icd10': ['I21', 'I22', 'I25.2'], 'text': r'myocardial infarction|\bstemi\b|\bnstemi\b|non-st elevation mi'},
    'stroke': {'label': 'Ischemic stroke / cerebral infarction', 'icd10': ['I63'], 'text': r'cerebral infarction|\bstroke\b'},
    'obesity': {'label': 'Obesity (BMI 30+), current',
        'icd10': ['E66.0', 'E66.1', 'E66.2', 'E66.8', 'E66.9', 'E66.01'],
        'text': r'obes|body mass index (30|40)', 'exclude': r'history of|overweight'},
    'hyperlipidemia': {'label': 'Hyperlipidemia / dyslipidemia', 'icd10': ['E78'],
        'text': r'hyperlipid|hypercholesterol|hypertriglycerid|dyslipid'},
    'depression': {'label': 'Depressive disorder', 'icd10': ['F32', 'F33', 'F34.1', 'F53'],
        'text': r'depress|\bmdd\b', 'exclude': r'screen'},
    'anxiety': {'label': 'Anxiety disorder', 'icd10': ['F41'], 'text': r'anxiety|\bgad\b|panic disorder'},
    'substance_use_disorder': {'label': 'Alcohol or drug use disorder', 'icd10': ['F10', 'F11', 'F12', 'F13', 'F14', 'F15', 'F16', 'F18', 'F19'],
        'text': r'alcoholism|\baud\b|drug abuse|use disorder|dependence', 'exclude': r'tobacco|nicotine'},
    'dementia': {'label': 'Dementia incl. Alzheimer disease', 'icd10': ['F01', 'F02', 'F03', 'G30'], 'text': r'alzheimer|dementia'},
    'cancer': {'label': 'Malignant neoplasm (active or history as documented)', 'icd10': ['C'],
        'text': r'malignant|carcinoma|leukemia|lymphoma|myeloma|metastatic'},
    'sepsis': {'label': 'Sepsis / septic shock', 'icd10': ['A40', 'A41', 'R65.2'], 'text': r'sepsis|septic'},
    'anemia': {'label': 'Anemia', 'icd10': ['D50', 'D51', 'D52', 'D53', 'D55', 'D56', 'D57', 'D58', 'D59', 'D60', 'D61', 'D62', 'D63', 'D64'],
        'text': r'anemia|anaemia'},
    'hypothyroidism': {'label': 'Hypothyroidism', 'icd10': ['E03'], 'text': r'hypothyroid'},
    'osteoporosis': {'label': 'Osteoporosis', 'icd10': ['M80', 'M81'], 'text': r'osteoporo'},
    'sleep_apnea': {'label': 'Sleep apnea', 'icd10': ['G47.3'], 'text': r'sleep apnea'},
    'hiv': {'label': 'HIV infection', 'icd10': ['B20', 'Z21'], 'text': r'\bhiv\b|human immunodeficiency'},
}

# --- Measurement concepts -----------------------------------------------------
# names: case-insensitive full-label pattern for labs `test` / vitals `measurement`.
# units: accepted unit spelling -> factor to canonical unit. wide: vitals columns.
_SAME = 1.0
MEASUREMENTS = {
    'hba1c': {'label': 'Hemoglobin A1c', 'unit': '%', 'loinc': ['4548-4', '17856-6', '4549-2', '41995-2'],
        'names': r'hb ?a1c|hemoglobin a1c.*|glycated hemoglobin|glycohemoglobin', 'units': {'%': _SAME}},
    'glucose': {'label': 'Glucose (any specimen timing)', 'unit': 'mg/dL', 'loinc': ['2345-7', '2339-0', '1558-6'],
        'names': r'glucose|fasting glucose.*|1-h postprandial.*', 'units': {'mg/dL': _SAME, 'mmol/L': 18.016}},
    'ldl': {'label': 'LDL cholesterol', 'unit': 'mg/dL', 'loinc': ['13457-7', '18262-6', '2089-1'],
        'names': r'ldl( cholesterol)?|cholesterol in ldl|ldl chol calc.*', 'units': {'mg/dL': _SAME, 'mmol/L': 38.67}},
    'hdl': {'label': 'HDL cholesterol', 'unit': 'mg/dL', 'loinc': ['2085-9'], 'names': r'hdl( cholesterol)?',
        'units': {'mg/dL': _SAME, 'mmol/L': 38.67}},
    'total_cholesterol': {'label': 'Total cholesterol', 'unit': 'mg/dL', 'loinc': ['2093-3'],
        'names': r'(total )?cholesterol', 'units': {'mg/dL': _SAME, 'mmol/L': 38.67}},
    'triglycerides': {'label': 'Triglycerides', 'unit': 'mg/dL', 'loinc': ['2571-8'], 'names': r'triglycerides?',
        'units': {'mg/dL': _SAME, 'mmol/L': 88.57}},
    'egfr': {'label': 'Estimated GFR (BSA-normalised)', 'unit': 'mL/min/1.73m2', 'loinc': ['33914-3', '48642-3', '48643-1', '62238-1', '98979-8'],
        # eGFR labels with the equation named (EGFR CKD-EPI 2021, eGFR (MDRD), estimated GFR ...) arrive without a LOINC from
        # some EHR extracts; 'egfr' alone stays an exact label so gene names (EGFR mutation) never match.
        'names': r'e-?gfr|e-?gfr[ ,(]*(ckd-epi|mdrd|cystatin|creatinine)\b[^/]*|estimated (gfr|glomerular filtration rate)\b.*|glomerular filtration rate',
        'units': {'mL/min/1.73m2': _SAME, 'mL/min/1.73m²': _SAME, 'mL/min/{1.73_m2}': _SAME}},
    'creatinine': {'label': 'Serum creatinine', 'unit': 'mg/dL', 'loinc': ['2160-0'], 'names': r'creatinine(, serum)?',
        'units': {'mg/dL': _SAME, 'umol/L': 1 / 88.42, 'µmol/L': 1 / 88.42}},
    'potassium': {'label': 'Potassium', 'unit': 'mmol/L', 'loinc': ['2823-3', '6298-4'], 'names': r'potassium|\bk\b',
        'units': {'mmol/L': _SAME, 'mEq/L': _SAME}},
    'sodium': {'label': 'Sodium', 'unit': 'mmol/L', 'loinc': ['2951-2'], 'names': r'sodium|\bna\b',
        'units': {'mmol/L': _SAME, 'mEq/L': _SAME}},
    'bun': {'label': 'Blood urea nitrogen', 'unit': 'mg/dL', 'loinc': ['3094-0'], 'names': r'bun|urea nitrogen',
        'units': {'mg/dL': _SAME}},
    'hemoglobin': {'label': 'Hemoglobin', 'unit': 'g/dL', 'loinc': ['718-7'], 'names': r'hemoglobin|hgb|\bhb\b',
        'units': {'g/dL': _SAME, 'g/L': 0.1}},
    'platelets': {'label': 'Platelet count', 'unit': '10^3/uL', 'loinc': ['777-3'], 'names': r'platelets?|plt',
        'units': {'10^3/uL': _SAME, '10*3/uL': _SAME, 'K/µL': _SAME, 'K/uL': _SAME}},
    'wbc': {'label': 'White blood cell count', 'unit': '10^3/uL', 'loinc': ['6690-2'], 'names': r'wbc|leukocytes|white blood cells?',
        'units': {'10^3/uL': _SAME, '10*3/uL': _SAME, 'K/µL': _SAME, 'K/uL': _SAME}},
    'uacr': {'label': 'Urine albumin/creatinine ratio', 'unit': 'mg/g', 'loinc': ['9318-7', '14959-1', '32294-1'],
        'names': r'urine albumin/creatinine ratio|microalbumin/creatinine( \[mass ratio\] in urine)?|microalb/creat ratio, random urine|uacr', 'units': {'mg/g': _SAME}},
    'nt_probnp': {'label': 'NT-proBNP', 'unit': 'pg/mL', 'loinc': ['33762-6'], 'names': r'nt-?probnp', 'units': {'pg/mL': _SAME}},
    'troponin': {'label': 'Troponin I (conventional and high-sensitivity)', 'unit': 'ng/L', 'loinc': ['10839-9', '89579-7'],
        'names': r'(high_sensitivity_)?troponin[ _]i.*', 'units': {'ng/L': _SAME, 'ng/mL': 1000.0}},
    'tsh': {'label': 'TSH', 'unit': 'mIU/L', 'loinc': ['3016-3'], 'names': r'tsh( 3rd generation)?|thyrotropin',
        'units': {'mIU/L': _SAME, 'µIU/mL': _SAME, 'uIU/mL': _SAME, 'm[IU]/L': _SAME}},
    'ferritin': {'label': 'Ferritin', 'unit': 'ng/mL', 'loinc': ['2276-4'], 'names': r'ferritin', 'units': {'ng/mL': _SAME}},
    'phq9': {'label': 'PHQ-9 depression score', 'unit': 'points', 'loinc': ['44261-6'], 'names': r'phq-?9', 'units': {'points': _SAME}},
    'phq2': {'label': 'PHQ-2 depression screen score', 'unit': 'points', 'loinc': ['55758-7'], 'names': r'phq-?2', 'units': {'points': _SAME}},
    'systolic_bp': {'label': 'Systolic blood pressure', 'unit': 'mmHg', 'names': r'bp_systolic|systolic.*',
        'units': {'mmHg': _SAME}, 'wide': ['blood_pressure_systolic', 'systolic_bp']},
    'diastolic_bp': {'label': 'Diastolic blood pressure', 'unit': 'mmHg', 'names': r'bp_diastolic|diastolic.*',
        'units': {'mmHg': _SAME}, 'wide': ['blood_pressure_diastolic', 'diastolic_bp']},
    'heart_rate': {'label': 'Heart rate', 'unit': 'beats/min', 'names': r'hr|heart_rate|pulse',
        'units': {'beats/min': _SAME, 'bpm': _SAME}, 'wide': ['heart_rate']},
    'weight': {'label': 'Body weight', 'unit': 'kg', 'names': r'weight_kg|weight', 'units': {'kg': _SAME, 'lb': 0.45359237},
        'wide': ['weight_kg']},
    'spo2': {'label': 'Oxygen saturation', 'unit': '%', 'names': r'spo2|oxygen saturation',
        'units': {'%': _SAME}, 'wide': ['oxygen_saturation_percent']},
}

# --- Pharmacy fills ----------------------------------------------------------------
# One shared definition of a picked-up fill, for every caller that counts fills from the semantic layer's medication table.
# Evidence: provider pharmacy dispenses, provider pharmacy claims and payer (NCPDP) pharmacy claims. Status: only rows whose
# status says the drug left the pharmacy ('prepared' and 'returned_to_stock' dispenses, 'rejected'/'reversed' claims are not fills).
# medication.fill_reversed = 1 marks a dispense later returned to stock (same fill_id) or a paid claim reversed by a B2.
FILL_EVIDENCE = ('dispensed', 'claim', 'payer_claim')
FILL_STATUSES = ('dispensed', 'paid', 'completed')
FILL_EVIDENCE_SQL = "('dispensed','claim','payer_claim')"


# Conditions whose laboratory-evidence rows count as the condition (Mike, 2026-10-21: diabetes and CKD). Rows
# flagged as not a diagnosis (rule-outs) stay excluded.
LAB_EVIDENCE_CONDITIONS = {'diabetes', 'type_1_diabetes', 'type_2_diabetes', 'chronic_kidney_disease'}


def split_scope(name):
    """'substance_use_disorder@F11' -> ('substance_use_disorder', ('F11',)); an unscoped name -> (name, None).
    A scoped condition name is the concept narrowed to a subset of its own ICD-10-CM codes (terminology.narrow): the term named
    only part of the concept's code list (opioid use disorder is F11, not every substance use disorder)."""
    base, _, rest = str(name).partition('@')
    codes = tuple(c for c in rest.split('+') if c)
    return base, (codes or None)


def scoped_name(concept, codes):
    return concept + '@' + '+'.join(codes)


def valid_scope(name):
    """The base is a condition concept and every scoped code lies inside that concept's own code list."""
    base, codes = split_scope(name)
    if base not in CONDITIONS: return False
    if codes is None: return '@' not in str(name)
    listed = [c.upper() for c in CONDITIONS[base]['icd10']]
    return all(re.fullmatch(r'[A-Z][0-9A-Z]{2}(?:\.[0-9A-Z]{1,4})?', c) and any(c == l or l.startswith(c) or c.startswith(l) for l in listed)
               for c in codes)


def condition_evidence_sql(names, tables):
    """Governed condition evidence: EHR diagnoses (plus unknown-flag problem-list rows) UNION counted payer-claim
    ICD-10 diagnoses matching the concepts' code prefixes. Returns a subquery of (patient_id, event_date, source).
    Decided by Mike 2026-10-21: claims-only members are part of the condition population.
    A scoped name (split_scope) reads only its narrowed codes: the EHR rows of the concept whose icd10 starts with one of them,
    and the payer-claim diagnoses with those prefixes."""
    lit = lambda v: "'" + str(v).replace("'", "''") + "'"
    parsed = [split_scope(n) for n in names]
    whole = [b for b, c in parsed if not c]
    narrowed = [(b, c) for b, c in parsed if c]
    parts = []
    if 'condition' in tables:
        diagnosis = 'is_diagnosis = 1'
        if 'diagnosis_basis' in tables['condition']:
            diagnosis += " OR (is_diagnosis IS NULL AND diagnosis_basis = 'ehr_problem_list')"
        dated = 'event_date' if 'event_date' in tables['condition'] else 'NULL AS event_date'
        which = []
        if whole: which.append('concept IN (' + ','.join(map(lit, whole)) + ')')
        if narrowed and 'icd10' not in tables['condition']:
            raise ValueError('A narrowed condition needs the condition.icd10 column')
        which += [f'(concept = {lit(b)} AND (' + ' OR '.join(f"icd10 LIKE {lit(x + '%')}" for x in c) + '))' for b, c in narrowed]
        where = which[0] if len(which) == 1 else '(' + ' OR '.join(which) + ')'
        parts.append("SELECT patient_id, " + dated + ", 'ehr_diagnosis' AS source FROM condition WHERE " + where + ' AND (' + diagnosis + ')')
        lab = sorted(set(whole) & LAB_EVIDENCE_CONDITIONS)
        if lab and 'diagnosis_basis' in tables['condition']:
            parts.append("SELECT patient_id, " + dated + ", 'laboratory_evidence' AS source FROM condition WHERE concept IN ("
                         + ','.join(map(lit, lab)) + ") AND is_diagnosis IS NULL AND diagnosis_basis = 'laboratory_evidence'")
    prefixes = sorted({p for b, c in parsed for p in (c or CONDITIONS.get(b, {}).get('icd10', []))})
    if prefixes and {'patient_id', 'icd10', 'service_from', 'counts_in_metrics'} <= set(tables.get('payer_claim_diagnosis', [])):
        parts.append("SELECT patient_id, service_from AS event_date, 'payer_claim_diagnosis' AS source FROM payer_claim_diagnosis "
                     'WHERE counts_in_metrics = 1 AND (' + ' OR '.join(f"icd10 LIKE {lit(p + '%')}" for p in prefixes) + ')')
    if not parts:
        return None
    return '(' + ' UNION ALL '.join(parts) + ')'


def picked_up_sql(alias='', reversed_column=True):
    """SQL predicate over the medication table: the row is a picked-up fill (see FILL_EVIDENCE / FILL_STATUSES)."""
    a = f'{alias}.' if alias else ''
    ev = ','.join(repr(e) for e in FILL_EVIDENCE); st = ','.join(repr(s) for s in FILL_STATUSES)
    return (f"({a}evidence IN ({ev}) AND ({a}status IN ({st}) OR ({a}status IS NULL AND {a}evidence <> 'dispensed'))"
            + (f" AND coalesce({a}fill_reversed, 0) = 0" if reversed_column else '') + ')')


# --- Encounter / utilisation concepts -------------------------------------------
# fields: stream -> {field: label pattern}. Patterns run over observed values only.
ENCOUNTERS = {
    'emergency_visit': {'label': 'Emergency department visit or registration',
        'fields': {'encounters': {'type': r'^(emergency|ed|er|ed .*|emergency .*)$'},
                   'adt': {'event': r'^emergency_registration$', 'patient_class': r'^emergency$'}}},
    'inpatient_admission': {'label': 'Inpatient admission / stay',
        'fields': {'encounters': {'type': r'^(inpatient.*|l&d admission)$'},
                   'adt': {'event': r'^admission$', 'patient_class': r'^inpatient$'}}},
    'observation_stay': {'label': 'Hospital observation stay',
        'fields': {'encounters': {'type': r'^observation$'},
                   'adt': {'event': r'^(observation_start|transfer_to_observation)$', 'patient_class': r'observation'}}},
    'urgent_care_visit': {'label': 'Urgent care visit', 'fields': {'encounters': {'type': r'^urgent ?_?care$'}}},
    'primary_care_visit': {'label': 'Primary care / office visit (incl. wellness and pediatric office)',
        'fields': {'encounters': {'type': r'^(primary[_ ]care|office|office visit|pcp|wellness|annual physical|pediatric_office|peds|geri primary care|ambulatory|youth_health_supervision|adolescent med(icine)?)$'}}},
    'specialist_visit': {'label': 'Specialist visit',
        'fields': {'encounters': {'type': r'^(specialty_followup|specialist_office|cardiology.*|nephrology|pulm.*|endo.*|neuro|ortho.*|med onc|hf clinic|.* clinic)$'}}},
    'telehealth_contact': {'label': 'Telephone or virtual encounter', 'fields': {'encounters': {'type': r'^(telephone|virtual)$'}}},
    'skilled_nursing_or_post_acute': {'label': 'SNF / home health / hospice encounter',
        'fields': {'encounters': {'type': r'^(snf|home_health|hospice)$'}}},
}

CONDITION_STREAMS = {'conditions': {'code': 'icd10', 'labels': ['description']},
                     'condition_assessments': {'code': 'icd10', 'labels': ['description', 'condition']},
                     'problem_lists': {'code': 'problem_code', 'labels': ['description']}}
MEASUREMENT_STREAMS = {'labs': {'name': 'test', 'alt_names': ['name'], 'unit': 'unit', 'value': 'value', 'code': 'code'},
                       'vitals': {'name': 'measurement', 'alt_names': [], 'unit': 'unit', 'value': 'value', 'code': None}}


def catalog():
    """Planner-facing list of concept ids and their meanings."""
    return {'conditions': {k: v['label'] for k, v in CONDITIONS.items()},
            'measurements': {k: f"{v['label']} [{v['unit']}]" for k, v in MEASUREMENTS.items()},
            'encounters': {k: v['label'] for k, v in ENCOUNTERS.items()}}


def is_concept(concept):
    return concept in CONDITIONS or concept in MEASUREMENTS or concept in ENCOUNTERS


def _labels(profile, stream, field):
    f = profile['streams'].get(stream, {}).get('fields', {}).get(field) or {}
    return f.get('values') or {}


# --- Evidence types ---------------------------------------------------------------------------------------------------
# Every record that matches a condition concept is tagged with the KIND of evidence it is, deterministically from its
# stream and fields (evidence_type below). A concept atom may restrict itself to some kinds: {'evidence': [...]}.
# Without that parameter a concept matches exactly what it always matched (the diagnosis streams in CONDITION_STREAMS);
# claim diagnoses, medication-list indications and note text are bound only when a question asks for them.
EVIDENCE_TYPES = ('coded_diagnosis', 'problem_list', 'claim_diagnosis', 'medication_indication', 'note_mention', 'lab_inferred')
EVIDENCE_MEANING = {
    'coded_diagnosis': 'a structured diagnosis entry (encounter diagnosis / condition record, coded or labelled)',
    'problem_list': 'an entry on the EHR problem list',
    'claim_diagnosis': 'a diagnosis code billed on a medical claim',
    'medication_indication': 'the indication written on a medication-list entry',
    'note_mention': 'an affirmed mention in clinical note text (negated, family-history and rule-out mentions excluded)',
    'lab_inferred': 'a condition record whose stated basis is laboratory evidence',
}
CLAIM_STREAMS = {'medical_claims': ('principal_diagnosis', 'diagnosis_codes', 'dx')}
INDICATION_STREAMS = {'medication_lists': 'indication'}
NOTE_STREAMS = {'notes': 'text', 'documents': 'text'}


def evidence_type(record, binding=None):
    """The kind of evidence one matched record is (EVIDENCE_TYPES), from its stream and fields; None for non-condition
    records (encounters, measurements). A binding that names its evidence (claims, indications, notes) decides it."""
    if binding and binding.get('evidence'): return binding['evidence']
    stream = record.get('source_table')
    if stream == 'problem_lists': return 'problem_list'
    if stream in ('conditions', 'condition_assessments'):
        basis = str(record.get('diagnosis_basis') or '').lower()
        if record.get('problem_type') == 'problem_list' or basis in ('ehr_problem_list', 'problem_list'): return 'problem_list'
        if basis in ('laboratory_evidence', 'lab_evidence', 'laboratory'): return 'lab_inferred'
        return 'coded_diagnosis'
    if stream in CLAIM_STREAMS: return 'claim_diagnosis'
    if stream in INDICATION_STREAMS: return 'medication_indication'
    if stream in NOTE_STREAMS: return 'note_mention'
    return None


def validate_evidence(evidence):
    if not isinstance(evidence, list) or not evidence or len(evidence) > len(EVIDENCE_TYPES) \
            or any(e not in EVIDENCE_TYPES for e in evidence) or len(set(evidence)) != len(evidence):
        raise ValueError(f'evidence must be a non-empty list of distinct types from {list(EVIDENCE_TYPES)}')
    return evidence


def _field_values(profile, stream, field):
    return {str(k) for k in _labels(profile, stream, field)}


def _fields(profile, stream):
    return profile['streams'].get(stream, {}).get('fields', {})


def default_evidence(profile):
    """Evidence kinds the default (unparameterised) condition bindings can produce on this source profile."""
    out = set()
    streams = profile['streams']
    if 'problem_lists' in streams: out.add('problem_list')
    for stream in ('conditions', 'condition_assessments'):
        if stream not in streams: continue
        out.add('coded_diagnosis')
        basis = {v.lower() for v in _field_values(profile, stream, 'diagnosis_basis')}
        if 'problem_list' in _field_values(profile, stream, 'problem_type') or basis & {'ehr_problem_list', 'problem_list'}:
            out.add('problem_list')
        if basis & {'laboratory_evidence', 'lab_evidence', 'laboratory'}: out.add('lab_inferred')
    return out


# Negation / family / hypothetical cues for free-text mentions (NegEx-style, deterministic). A cue counts when it sits
# in the same sentence before the mention with at most a few words between; a historical cue keeps the mention (a
# "history of diabetes" is evidence of diabetes) but is recorded as historical.
NEGATION_CUES = r'\b(?:no|not|denies|denied|without|negative for|free of|ruled out|rule out|r/o|rules out|absence of|never had)\b'
FAMILY_CUES = r'\b(?:family history|fhx|fh of|mother|father|sister|brother|sibling|parent|grandmother|grandfather|aunt|uncle)\b'
HYPOTHETICAL_CUES = r'\b(?:screen(?:ing)? for|risk (?:of|for)|evaluate for|concern for|possible|possibly|suspected|cannot exclude|if)\b'
HISTORICAL_CUES = r'\b(?:history of|h/o|hx of|prior|past|previous|resolved|remote)\b'


def mention_state(text, pattern, exclude=None):
    """-> 'affirmed' | 'historical' | 'negated' | 'family' | 'hypothetical' | None (no mention) for a concept's text
    pattern in free text. The first affirmed (else historical) mention decides; a mention whose own phrase matches the
    concept's exclude pattern (e.g. 'prediabetes' for diabetes) is not a mention."""
    if not isinstance(text, str) or not text: return None
    rx = re.compile(pattern, re.I)
    ex = re.compile(exclude, re.I) if exclude else None
    states = []
    for sentence in re.split(r'(?<=[.;!?\n])\s+', text):
        for m in rx.finditer(sentence):
            local = sentence[max(0, m.start() - 25):m.end() + 25]
            if ex and ex.search(local): continue
            before = sentence[:m.start()].lower()
            near = ' '.join(before.split()[-6:])
            if re.search(FAMILY_CUES, before): states.append('family')
            elif re.search(NEGATION_CUES, near): states.append('negated')
            elif re.search(HYPOTHETICAL_CUES, near) or sentence.rstrip().endswith('?'): states.append('hypothetical')
            elif re.search(HISTORICAL_CUES, near): states.append('historical')
            else: states.append('affirmed')
    if not states: return None
    for s in ('affirmed', 'historical', 'hypothetical', 'negated', 'family'):
        if s in states: return s
    return None


def _evidence_bindings(concept, profile, evidence):
    """Extra bindings for evidence kinds outside the default diagnosis streams (claims, indications, note text)."""
    d = CONDITIONS[concept]
    out = []
    if 'claim_diagnosis' in evidence:
        for stream, candidates in CLAIM_STREAMS.items():
            fields = [f for f in candidates if f in _fields(profile, stream)]
            if stream in profile['streams'] and fields:
                out.append({'stream': stream, 'code_fields': fields, 'prefixes': list(d['icd10']), 'system': 'ICD-10-CM',
                            'evidence': 'claim_diagnosis'})
    for kind, streams in (('medication_indication', INDICATION_STREAMS), ('note_mention', NOTE_STREAMS)):
        if kind not in evidence: continue
        for stream, field in streams.items():
            if stream in profile['streams'] and field in _fields(profile, stream):
                out.append({'stream': stream, 'text_field': field, 'pattern': d['text'], 'exclude': d.get('exclude'),
                            'system': 'free text' if kind == 'note_mention' else 'source label', 'evidence': kind,
                            **({'negation': True} if kind == 'note_mention' else {})})
    return out


def resolve_condition(concept, profile, evidence=None):
    d = CONDITIONS[concept]
    text = re.compile(d['text'], re.I)
    exclude = re.compile(d['exclude'], re.I) if d.get('exclude') else None
    bindings, included, excluded = [], {}, {}
    for stream, spec in CONDITION_STREAMS.items():
        if stream not in profile['streams']: continue
        codes = sorted(c for c in _labels(profile, stream, spec['code'])
                       if any(c.upper().startswith(p) for p in d['icd10']))
        if codes: bindings.append({'stream': stream, 'field': spec['code'], 'values': codes, 'system': 'ICD-10-CM'})
        for field in spec['labels']:
            labels = _labels(profile, stream, field)
            hits = []
            for label, n in labels.items():
                readable = str(label).replace('_', ' ')  # coded labels such as 'heart_failure' read as words
                if not text.search(readable): continue
                if exclude and exclude.search(readable): excluded[label] = n; continue
                hits.append(label); included[label] = n
            if hits: bindings.append({'stream': stream, 'field': field, 'values': sorted(hits), 'system': 'source label'})
    out = {'concept': concept, 'kind': 'condition', 'label': d['label'], 'bindings': bindings,
           'included_labels': included, 'excluded_labels': excluded,
           'default_evidence': sorted(default_evidence(profile), key=EVIDENCE_TYPES.index) if bindings else []}
    if evidence:
        evidence = validate_evidence(list(evidence))
        default = default_evidence(profile) if bindings else set()
        extra = _evidence_bindings(concept, profile, evidence)
        available = (default | {b['evidence'] for b in extra}) & set(evidence)
        # Default bindings stay only when a default kind is requested; record-level filtering does the rest.
        out['bindings'] = (bindings if default & set(evidence) else []) + extra
        out['evidence'] = {'requested': list(evidence), 'available': sorted(available, key=EVIDENCE_TYPES.index)}
    return out


def _measurement_codes(concept, stream, profile):
    """Code fields that identify the analyte whatever the local name ('MICROALB/CREAT RATIO, RANDOM URINE' with LOINC
    14959-1 or local code MALB/CR is a uACR): the library LOINCs plus the normalization dictionary's confident LOINCs, and
    the local lab codes the semantic layer maps to the concept. Only fields the stream actually carries. -> {field: [codes]}"""
    fields = (profile.get('streams', {}).get(stream) or {}).get('fields') or {}
    out = {}
    if (fields.get('loinc') or {}).get('populated'):
        loincs = set(MEASUREMENTS[concept].get('loinc') or [])
        try:
            from pathlib import Path
            import json
            entries = json.loads((Path(__file__).resolve().parents[3] / 'config/agere/normalization/labs.json').read_text())['entries']
            loincs |= {v['loinc'] for v in entries.values() if v.get('agere_concept') == concept and v.get('loinc')
                       and (v.get('confidence') or 0) >= 0.8}
        except (OSError, ValueError, KeyError):
            pass
        if loincs: out['loinc'] = sorted(loincs)
    if (fields.get('local_code') or {}).get('populated'):
        from .client_data import LOCAL_LAB_CODES
        local = sorted(code for code, (_, c) in LOCAL_LAB_CODES.items() if c == concept)
        if local: out['local_code'] = local
    return out


def code_match(binding, r):
    """True when a measurement record's code field identifies the binding's analyte (see _measurement_codes)."""
    for field, codes in (binding.get('codes') or {}).items():
        got = r.get(field)
        if isinstance(got, str) and (got.strip().upper() if field == 'local_code' else got.strip()) in codes: return True
    return False


def resolve_measurement(concept, profile):
    d = MEASUREMENTS[concept]
    pattern = re.compile(r'^(?:' + d['names'] + r')$', re.I)
    bindings, units_seen, unconvertible = [], {}, {}
    for stream, spec in MEASUREMENT_STREAMS.items():
        rows = [m for m in profile.get('measurements', {}).get(stream, []) if m['name'] and pattern.match(m['name'].strip())]
        names = sorted({m['name'] for m in rows})
        for m in rows:
            (units_seen if m['unit'] in d['units'] else unconvertible)[f"{m['name']} [{m['unit']}]"] = m['records']
        codes = _measurement_codes(concept, stream, profile)
        if names or codes:
            bindings.append({'stream': stream, 'name_fields': [spec['name'], *spec['alt_names']], 'names': names,
                             'unit_field': spec['unit'], 'value_field': spec['value'], **({'codes': codes} if codes else {})})
        if stream == 'vitals':
            fields = profile['streams'].get('vitals', {}).get('fields', {})
            for column in d.get('wide', []):
                if fields.get(column, {}).get('populated'):
                    bindings.append({'stream': 'vitals', 'value_field': column, 'fixed_unit': d['unit']})
                    units_seen[f"{column} [{d['unit']}]"] = fields[column]['populated']
    # Instrument totals carried as a named numeric field on another feed (health_risk_assessments.phq2_total): the concept is
    # observed there too, not only where a measurement row names it. Field name = the concept's name pattern + total/score.
    total = re.compile(r'^(?:' + d['names'] + r')_?(?:total|score|total_score)$', re.I)
    for stream, spec in sorted((profile.get('streams') or {}).items()):
        if stream in MEASUREMENT_STREAMS: continue
        for column, f in sorted((spec.get('fields') or {}).items()):
            if f.get('populated') and 'numeric_range' in f and total.match(column):
                bindings.append({'stream': stream, 'value_field': column, 'fixed_unit': d['unit']})
                units_seen[f"{stream}.{column} [{d['unit']}]"] = f['populated']
    return {'concept': concept, 'kind': 'measurement', 'label': d['label'], 'unit': d['unit'], 'units': d['units'],
            'bindings': bindings, 'included_series': units_seen, 'unconvertible_series': unconvertible}


def resolve_encounter(concept, profile):
    d = ENCOUNTERS[concept]
    bindings, included = [], {}
    for stream, fields in d['fields'].items():
        for field, pattern in fields.items():
            rx = re.compile(pattern, re.I)
            hits = sorted(v for v in _labels(profile, stream, field) if rx.search(v.strip()))
            if hits:
                bindings.append({'stream': stream, 'field': field, 'values': hits, 'system': 'source label'})
                included.update({f'{stream}.{field}={v}': _labels(profile, stream, field)[v] for v in hits})
    return {'concept': concept, 'kind': 'encounter', 'label': d['label'], 'bindings': bindings,
            'included_labels': included, 'excluded_labels': {}}


def resolve(concept, profile, evidence=None):
    if evidence and concept not in CONDITIONS: raise ValueError('evidence types apply to condition concepts only')
    if concept in ENCOUNTERS: return resolve_encounter(concept, profile)
    if concept in CONDITIONS: return resolve_condition(concept, profile, evidence)
    if concept in MEASUREMENTS: return resolve_measurement(concept, profile)
    raise ValueError(f'Unknown concept: {concept}')


def concept_for_code(code):
    """Map a literal code (ICD-10 prefix or LOINC) to the most specific library concept."""
    code = str(code).strip().upper()
    for concept, d in MEASUREMENTS.items():
        if code in d.get('loinc', []): return concept
    if len(code) < 3: return None
    # Most specific library prefix that the code falls under; a broader
    # question code (E11) must not be captured by a narrower one (E11.2).
    # Ties go to the narrower concept (fewer prefixes): E11 -> type_2_diabetes.
    best, rank = None, (0, 0)
    for concept, d in CONDITIONS.items():
        for p in d['icd10']:
            candidate = (len(p), -len(d['icd10']))
            if code.startswith(p) and candidate > rank:
                best, rank = concept, candidate
    return best


def concept_for_label(label):
    """Map a literal lab/vital label to a measurement concept, if any."""
    for concept, d in MEASUREMENTS.items():
        if re.match(r'^(?:' + d['names'] + r')$', str(label).strip(), re.I): return concept
    return None


def conditions_for_label(label):
    """Condition concepts whose label pattern matches a literal label (and whose exclude pattern does not), most specific
    first: the longest matched span wins ('kidney due to diabet' over 'diabet', 'type 2 diabet' over 'diabet'), ties go to
    the concept with fewer ICD-10 prefixes (myocardial_infarction before coronary_artery_disease)."""
    readable = str(label).replace('_', ' ').strip()
    hits = {}
    for c, d in CONDITIONS.items():
        if d.get('exclude') and re.search(d['exclude'], readable, re.I): continue
        spans = [m.end() - m.start() for m in re.finditer(d['text'], readable, re.I)]
        if spans: hits[c] = max(spans)
    return sorted(hits, key=lambda c: (-hits[c], len(CONDITIONS[c]['icd10']), c))


def condition_for_label(label):
    """The most specific condition concept for a literal label (conditions_for_label), or None."""
    hits = conditions_for_label(label)
    return hits[0] if hits else None


# --- Terminology fallback (second pinned encoder) ---------------------------------------------------------------------------
# For lab/vital and condition source labels that neither a code (LOINC / ICD-10) nor the concept library's label patterns
# (the aliases above) resolve, the second pinned encoder (local_encoders.PinnedSentenceEncoder, UltraBERT-Embed-150M, query
# prompt on the label side) proposes the nearest concept. Concept documents are '<concept id as words>: <library label>'.
# A proposal is accepted only at or above TERMINOLOGY_THRESHOLDS[kind]; below it the candidate and score are returned with
# concept None. A code or alias match is always returned first and is never replaced by the encoder.
# Calibration (tools/second_encoder/measure_integrated.py calibrate): the normalization dictionary's confident (>= 0.8)
# single-concept entries that no alias resolves, bake-off labels excluded - labs 35 mapped / 82 mapped-to-none, conditions
# 94 / 143. Each threshold is the lowest 0.05 step with zero accepted none-entries and zero wrong concepts: labs 0.55
# (accepts 19/35 mapped), conditions 0.45 (38/94). Cosines of right and wrong proposals overlap below that, so the
# fallback is conservative by design: below threshold it reports the candidate for review instead of binding it.
TERMINOLOGY_VERSION = 'terminology-fallback/1'
TERMINOLOGY_THRESHOLDS = {'lab': 0.55, 'condition': 0.45}
TERMINOLOGY_KINDS = ('lab', 'condition')
_TERM_VECTORS = {}


def terminology_targets(kind):
    if kind == 'lab': return {k: f"{k.replace('_', ' ')}: {v['label']}" for k, v in MEASUREMENTS.items()}
    if kind == 'condition': return {k: f"{k.replace('_', ' ')}: {v['label']}" for k, v in CONDITIONS.items()}
    raise ValueError(f'Unknown terminology kind: {kind}')


def terminology_encoder():
    """The second pinned encoder, or None (AGERE_TERMINOLOGY_ENCODER=off / AGERE_SECOND_ENCODER=off / absent / refused)."""
    import os
    if os.environ.get('AGERE_TERMINOLOGY_ENCODER', 'on') == 'off': return None
    try:
        from ...local_encoders import second_encoder
        return second_encoder()
    except Exception:  # noqa: BLE001 - fallback matcher only
        return None


def _target_vectors(encoder, kind):
    key = (encoder.identity, kind)
    if key not in _TERM_VECTORS:
        targets = terminology_targets(kind)
        names = list(targets)
        _TERM_VECTORS[key] = (names, encoder.encode_documents([targets[n] for n in names]))
    return _TERM_VECTORS[key]


def encoder_candidates(labels, kind, encoder, top=2):
    """[[(concept, cosine), ...top]] per label, nearest first."""
    names, matrix = _target_vectors(encoder, kind)
    sims = encoder.encode_queries([str(x) for x in labels]) @ matrix.T
    return [[(names[j], float(row[j])) for j in sorted(range(len(names)), key=lambda j: -row[j])[:top]] for row in sims]


def match_label(label, kind, *, code=None, encoder='auto', threshold=None):
    """Concept for one source label: exact code -> alias (library label pattern) -> encoder fallback.
    -> {'concept', 'method': 'code'|'alias'|'encoder', 'score', ...} or None when nothing matched and no encoder ran.
    An encoder proposal below threshold is returned with concept None and accepted False (candidate + score kept).
    kind='medication': ingredient words (drug_classes patterns) -> brand table (config/agere/drug_brands.json, RxNorm brand
    -> ingredient naming); the encoder is not used for medications (every measured encoder failed brand names)."""
    if kind == 'medication': return _match_medication(label)
    if kind not in TERMINOLOGY_KINDS: raise ValueError(f'Unknown terminology kind: {kind}')
    family = MEASUREMENTS if kind == 'lab' else CONDITIONS
    if code:
        c = concept_for_code(code)
        if c in family: return {'concept': c, 'method': 'code', 'score': 1.0, 'code': str(code)}
    if label is None or not str(label).strip(): return None
    if kind == 'lab':
        c = concept_for_label(label)
        if c: return {'concept': c, 'method': 'alias', 'score': 1.0}
    else:
        hits = conditions_for_label(label)
        if hits: return {'concept': hits[0], 'method': 'alias', 'score': 1.0, **({'also': hits[1:]} if hits[1:] else {})}
    encoder = terminology_encoder() if encoder == 'auto' else encoder
    if encoder is None: return None
    try:
        (best, score), *rest = encoder_candidates([label], kind, encoder)[0]
    except Exception:  # noqa: BLE001 - the fallback never blocks
        return None
    t = TERMINOLOGY_THRESHOLDS[kind] if threshold is None else threshold
    ok = score >= t
    return {'concept': best if ok else None, 'candidate': best, 'method': 'encoder', 'score': round(score, 4), 'threshold': t,
            'accepted': ok, 'runner_up': [rest[0][0], round(rest[0][1], 4)] if rest else None, 'version': TERMINOLOGY_VERSION,
            'encoder': encoder.identity[:16]}


def _match_medication(label):
    from . import drug_classes
    if label is None or not str(label).strip(): return None
    found = drug_classes.ingredients(label)
    if found:
        return {'concept': found[0], 'ingredients': found, 'classes': drug_classes.classify(label), 'method': 'ingredient', 'score': 1.0}
    hits = drug_classes.brand_lookup(label)
    if not hits: return None
    names = drug_classes.generic_name(label)
    return {'concept': names[0], 'ingredients': names, 'classes': drug_classes.brand_classes(label), 'brands': [h['brand'] for h in hits],
            'method': 'brand', 'score': 1.0, 'naming_standard': 'RxNorm (brand BN -> ingredient IN)'}
