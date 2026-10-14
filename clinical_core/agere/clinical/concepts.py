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
        'names': r'ldl( cholesterol)?', 'units': {'mg/dL': _SAME, 'mmol/L': 38.67}},
    'hdl': {'label': 'HDL cholesterol', 'unit': 'mg/dL', 'loinc': ['2085-9'], 'names': r'hdl( cholesterol)?',
        'units': {'mg/dL': _SAME, 'mmol/L': 38.67}},
    'total_cholesterol': {'label': 'Total cholesterol', 'unit': 'mg/dL', 'loinc': ['2093-3'],
        'names': r'(total )?cholesterol', 'units': {'mg/dL': _SAME, 'mmol/L': 38.67}},
    'triglycerides': {'label': 'Triglycerides', 'unit': 'mg/dL', 'loinc': ['2571-8'], 'names': r'triglycerides?',
        'units': {'mg/dL': _SAME, 'mmol/L': 88.57}},
    'egfr': {'label': 'Estimated GFR (BSA-normalised)', 'unit': 'mL/min/1.73m2', 'loinc': ['33914-3', '48642-3', '48643-1', '62238-1', '98979-8'],
        'names': r'egfr|glomerular filtration rate',
        'units': {'mL/min/1.73m2': _SAME, 'mL/min/1.73m²': _SAME, 'mL/min/{1.73_m2}': _SAME}},
    'creatinine': {'label': 'Serum creatinine', 'unit': 'mg/dL', 'loinc': ['2160-0'], 'names': r'creatinine',
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
        'names': r'urine albumin/creatinine ratio|microalbumin/creatinine|uacr', 'units': {'mg/g': _SAME}},
    'nt_probnp': {'label': 'NT-proBNP', 'unit': 'pg/mL', 'loinc': ['33762-6'], 'names': r'nt-?probnp', 'units': {'pg/mL': _SAME}},
    'troponin': {'label': 'Troponin I (conventional and high-sensitivity)', 'unit': 'ng/L', 'loinc': ['10839-9', '89579-7'],
        'names': r'(high_sensitivity_)?troponin[ _]i.*', 'units': {'ng/L': _SAME, 'ng/mL': 1000.0}},
    'tsh': {'label': 'TSH', 'unit': 'mIU/L', 'loinc': ['3016-3'], 'names': r'tsh|thyrotropin',
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


def resolve_condition(concept, profile):
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
    return {'concept': concept, 'kind': 'condition', 'label': d['label'], 'bindings': bindings,
            'included_labels': included, 'excluded_labels': excluded}


def resolve_measurement(concept, profile):
    d = MEASUREMENTS[concept]
    pattern = re.compile(r'^(?:' + d['names'] + r')$', re.I)
    bindings, units_seen, unconvertible = [], {}, {}
    for stream, spec in MEASUREMENT_STREAMS.items():
        rows = [m for m in profile.get('measurements', {}).get(stream, []) if m['name'] and pattern.match(m['name'].strip())]
        names = sorted({m['name'] for m in rows})
        for m in rows:
            (units_seen if m['unit'] in d['units'] else unconvertible)[f"{m['name']} [{m['unit']}]"] = m['records']
        if names:
            bindings.append({'stream': stream, 'name_fields': [spec['name'], *spec['alt_names']], 'names': names,
                             'unit_field': spec['unit'], 'value_field': spec['value']})
        if stream == 'vitals':
            fields = profile['streams'].get('vitals', {}).get('fields', {})
            for column in d.get('wide', []):
                if fields.get(column, {}).get('populated'):
                    bindings.append({'stream': 'vitals', 'value_field': column, 'fixed_unit': d['unit']})
                    units_seen[f"{column} [{d['unit']}]"] = fields[column]['populated']
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


def resolve(concept, profile):
    if concept in ENCOUNTERS: return resolve_encounter(concept, profile)
    if concept in CONDITIONS: return resolve_condition(concept, profile)
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
