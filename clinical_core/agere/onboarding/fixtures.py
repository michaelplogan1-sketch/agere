"""Small synthetic fixtures with known truth for matcher tests.

`clarity_like` writes an Epic-Clarity-flavoured schema (upper-case names, PAT_ID/CSN keys,
datetime CONTACT_DATEs, dollar amounts, capitalised statuses, a diagnosis master table
that must NOT be mapped as patient conditions). All values are generated; no real codes
beyond public code-system examples are implied to belong to anyone.
"""
from pathlib import Path
import random
import sqlite3

DDL = """
CREATE TABLE PATIENT(PAT_ID TEXT PRIMARY KEY, PAT_MRN_ID TEXT, PAT_NAME TEXT, BIRTH_DATE TEXT, SEX TEXT, ETHNIC_GROUP TEXT,
  PATIENT_RACE TEXT, LANGUAGE TEXT, ZIP TEXT, STATE_ABBR TEXT);
CREATE TABLE CLARITY_DEP(DEPARTMENT_ID INTEGER PRIMARY KEY, DEPARTMENT_NAME TEXT, DEPT_TYPE TEXT, DEPT_NPI TEXT, TAX_ID TEXT,
  DEPT_CITY TEXT, DEPT_STATE TEXT, DEPT_ZIP TEXT);
CREATE TABLE CLARITY_SER(PROV_ID TEXT PRIMARY KEY, PROV_NAME TEXT, PROV_TYPE TEXT, SPECIALTY_NAME TEXT, NPI TEXT, DEPARTMENT_ID INTEGER);
CREATE TABLE PAT_ENC(PAT_ENC_CSN_ID INTEGER PRIMARY KEY, PAT_ID TEXT, CONTACT_DATE TEXT, ENC_TYPE_NAME TEXT, ENC_STATUS TEXT,
  VISIT_PROV_ID TEXT, DEPARTMENT_ID INTEGER, HOSP_DISCH_TIME TEXT, DISCH_DISP_NAME TEXT);
CREATE TABLE CLARITY_EDG(DX_ID INTEGER PRIMARY KEY, DX_NAME TEXT, CURRENT_ICD10_LIST TEXT);
CREATE TABLE PAT_ENC_DX(PAT_ENC_CSN_ID INTEGER, LINE INTEGER, PAT_ID TEXT, DX_ID INTEGER, ICD10_CODE TEXT, DX_NAME TEXT,
  CONTACT_DATE TEXT, PRIMARY_DX_YN TEXT);
CREATE TABLE ORDER_RESULTS(ORDER_PROC_ID INTEGER, LINE INTEGER, PAT_ID TEXT, COMPONENT_NAME TEXT, COMPONENT_LOINC TEXT,
  ORD_NUM_VALUE REAL, REFERENCE_UNIT TEXT, RESULT_DATE TEXT, RESULT_STATUS TEXT, RESULT_FLAG TEXT);
CREATE TABLE ORDER_MED(ORDER_MED_ID INTEGER PRIMARY KEY, PAT_ID TEXT, DESCRIPTION TEXT, RXCUI TEXT, ORDERING_DATE TEXT, ORDER_STATUS TEXT);
CREATE TABLE AP_CLAIM(CLM_ID TEXT PRIMARY KEY, PAT_ID TEXT, SVC_DATE_FROM TEXT, CLAIM_STATUS TEXT, TOT_BILLED_AMT REAL,
  TOT_ALLOWED_AMT REAL, TOT_PAID_AMT REAL, PAT_RESP_AMT REAL, PROCESSED_DATE TEXT, PAYOR_NAME TEXT);
CREATE TABLE AP_CLAIM_PX(CLM_ID TEXT, LINE INTEGER, PROC_CODE TEXT, QTY REAL, LINE_BILLED_AMT REAL, SVC_DATE TEXT, REV_CODE TEXT);
CREATE TABLE COVERAGE(COVERAGE_ID INTEGER PRIMARY KEY, PAT_ID TEXT, FIN_CLASS_NAME TEXT, CVG_EFF_DT TEXT, CVG_TERM_DT TEXT);
"""

TRUTH = {
    'patient': ('PATIENT', {'patient_id': 'PAT_ID', 'birth_date': 'BIRTH_DATE', 'sex_reported': 'SEX', 'ethnicity': 'ETHNIC_GROUP',
                            'race': 'PATIENT_RACE', 'preferred_language': 'LANGUAGE', 'zip': 'ZIP', 'state': 'STATE_ABBR'}),
    'organization': ('CLARITY_DEP', {'organization_id': 'DEPARTMENT_ID', 'display_name': 'DEPARTMENT_NAME', 'organization_type': 'DEPT_TYPE',
                                     'npi': 'DEPT_NPI', 'tin': 'TAX_ID', 'city': 'DEPT_CITY', 'state': 'DEPT_STATE', 'postal_code': 'DEPT_ZIP'}),
    'clinician': ('CLARITY_SER', {'clinician_id': 'PROV_ID', 'specialty': 'SPECIALTY_NAME', 'npi': 'NPI', 'organization_id': 'DEPARTMENT_ID'}),
    'visit': ('PAT_ENC', {'encounter_id': 'PAT_ENC_CSN_ID', 'patient_id': 'PAT_ID', 'event_date': 'CONTACT_DATE', 'type': 'ENC_TYPE_NAME',
                          'status': 'ENC_STATUS', 'attending_clinician_id': 'VISIT_PROV_ID', 'organization_id': 'DEPARTMENT_ID',
                          'end_date': 'HOSP_DISCH_TIME', 'disposition': 'DISCH_DISP_NAME'}),
    'condition': ('PAT_ENC_DX', {'patient_id': 'PAT_ID', 'icd10': 'ICD10_CODE', 'label': 'DX_NAME', 'event_date': 'CONTACT_DATE'}),
    'measurement': ('ORDER_RESULTS', {'patient_id': 'PAT_ID', 'name': 'COMPONENT_NAME', 'loinc': 'COMPONENT_LOINC', 'value': 'ORD_NUM_VALUE',
                                      'unit': 'REFERENCE_UNIT', 'event_date': 'RESULT_DATE', 'result_status': 'RESULT_STATUS',
                                      'abnormal_flag': 'RESULT_FLAG'}),
    'medication': ('ORDER_MED', {'patient_id': 'PAT_ID', 'drug_name': 'DESCRIPTION', 'rxnorm': 'RXCUI', 'event_date': 'ORDERING_DATE',
                                 'status': 'ORDER_STATUS'}),
    'claim': ('AP_CLAIM', {'claim_id': 'CLM_ID', 'patient_id': 'PAT_ID', 'service_date': 'SVC_DATE_FROM', 'status': 'CLAIM_STATUS',
                           'billed_cents': 'TOT_BILLED_AMT', 'allowed_cents': 'TOT_ALLOWED_AMT', 'payer_paid_cents': 'TOT_PAID_AMT',
                           'patient_responsibility_cents': 'PAT_RESP_AMT', 'event_date': 'PROCESSED_DATE', 'payer': 'PAYOR_NAME'}),
    'claim_line': ('AP_CLAIM_PX', {'claim_id': 'CLM_ID', 'line_number': 'LINE', 'service_code': 'PROC_CODE', 'units': 'QTY',
                                   'charge_cents': 'LINE_BILLED_AMT', 'service_date': 'SVC_DATE', 'revenue_code': 'REV_CODE'}),
    'coverage': ('COVERAGE', {'patient_id': 'PAT_ID', 'coverage_type': 'FIN_CLASS_NAME', 'event_date': 'CVG_EFF_DT'}),
}
UNMAPPED_TABLES = ['CLARITY_EDG']

LABS = [('HEMOGLOBIN A1C', '4548-4', '%', 5.0, 11.0), ('GLUCOSE', '2345-7', 'mg/dL', 70, 260), ('CREATININE', '2160-0', 'mg/dL', 0.6, 2.5),
        ('POTASSIUM', '2823-3', 'mmol/L', 3.2, 5.9), ('LDL CHOLESTEROL', '13457-7', 'mg/dL', 50, 190)]
DX = [('E11.9', 'Type 2 diabetes mellitus without complications'), ('I10', 'Essential (primary) hypertension'),
      ('E78.5', 'Hyperlipidemia, unspecified'), ('N18.3', 'Chronic kidney disease, stage 3'), ('J45.909', 'Unspecified asthma')]
MEDS = [('METFORMIN 500 MG TABLET', '861007'), ('LISINOPRIL 10 MG TABLET', '314076'), ('ATORVASTATIN 40 MG TABLET', '617311'),
        ('INSULIN GLARGINE 100 UNIT/ML', '274783')]
CPT = ['99213', '99214', '99203', '80053', '83036', 'G0438', '93000']


def _npi(rng):
    from .codes import luhn_ok
    while True:
        base = '1' + ''.join(str(rng.randint(0, 9)) for _ in range(8))
        for d in '0123456789':
            if luhn_ok('80840' + base + d): return base + d


def clarity_like(path, n_patients=40, seed=7):
    rng = random.Random(seed)
    path = Path(path)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript(DDL)
    deps = [(100 + i, f'DEPT {name}', kind, _npi(rng), f'{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}', 'Springfield', 'IL',
             f'627{i:02d}') for i, (name, kind) in enumerate([('FAMILY MED', 'Clinic'), ('CARDIOLOGY', 'Clinic'), ('ED', 'Emergency'),
                                                              ('MED SURG', 'Inpatient'), ('LAB', 'Laboratory')])]
    db.executemany('INSERT INTO CLARITY_DEP VALUES (?,?,?,?,?,?,?,?)', deps)
    provs = [(f'E{1000 + i}', f'PROVIDER {i}', 'Physician', rng.choice(['Family Medicine', 'Cardiology', 'Emergency Medicine', 'Internal Medicine']),
              _npi(rng), rng.choice(deps)[0]) for i in range(12)]
    db.executemany('INSERT INTO CLARITY_SER VALUES (?,?,?,?,?,?)', provs)
    db.executemany('INSERT INTO CLARITY_EDG VALUES (?,?,?)', [(9000 + i, name, code) for i, (code, name) in enumerate(DX)])
    csn, order, med, clm = 700000, 30000, 50000, 0
    for i in range(n_patients):
        pid = f'Z{100200 + i * 13}'
        db.execute('INSERT INTO PATIENT VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (pid, f'MRN{500000 + i}', f'PATIENT {i}', f'{rng.randint(1935, 2005)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d} 00:00:00',
                    rng.choice(['Female', 'Male']), rng.choice(['Not Hispanic or Latino', 'Hispanic or Latino']),
                    rng.choice(['White', 'Black or African American', 'Asian', 'Other']), rng.choice(['English', 'Spanish']),
                    f'627{rng.randint(10, 99)}', 'IL'))
        db.execute('INSERT INTO COVERAGE VALUES (?,?,?,?,?)', (i + 1, pid, rng.choice(['Commercial', 'Medicare', 'Medicaid']),
                                                               f'2027-0{rng.randint(1, 9)}-01 00:00:00', None))
        for _ in range(rng.randint(2, 6)):
            csn += 1
            d = f'2028-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}'
            kind = rng.choice(['Office Visit', 'Office Visit', 'Telephone', 'Hospital Encounter', 'Emergency'])
            dep = {'Hospital Encounter': 103, 'Emergency': 102}.get(kind, rng.choice([100, 101]))
            disch = f'{d} 15:00:00' if kind == 'Hospital Encounter' else None
            db.execute('INSERT INTO PAT_ENC VALUES (?,?,?,?,?,?,?,?,?)', (csn, pid, f'{d} 00:00:00', kind, rng.choice(['Completed', 'Completed', 'Canceled']),
                                                                          rng.choice(provs)[0], dep, disch, 'Home' if disch else None))
            for line, (code, name) in enumerate(rng.sample(DX, rng.randint(1, 2)), 1):
                db.execute('INSERT INTO PAT_ENC_DX VALUES (?,?,?,?,?,?,?,?)', (csn, line, pid, 9000 + DX.index((code, name)), code, name,
                                                                               f'{d} 00:00:00', 'Y' if line == 1 else 'N'))
            order += 1
            for line, (name, loinc, unit, lo, hi) in enumerate(rng.sample(LABS, 3), 1):
                db.execute('INSERT INTO ORDER_RESULTS VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (order, line, pid, name, loinc, round(rng.uniform(lo, hi), 1), unit, f'{d} 09:30:00', 'Final', rng.choice([None, None, 'High', 'Low'])))
            if rng.random() < 0.6:
                med += 1
                name, rx = rng.choice(MEDS)
                db.execute('INSERT INTO ORDER_MED VALUES (?,?,?,?,?,?)', (med, pid, name, rx, f'{d} 10:00:00', rng.choice(['Sent', 'Completed', 'Discontinued'])))
            clm += 1
            cid = f'C{2028000000 + clm}'
            billed = round(rng.uniform(80, 4000), 2)
            allowed = round(billed * rng.uniform(0.4, 0.8), 2)
            status = rng.choice(['Paid', 'Paid', 'Paid', 'Denied'])
            paid = round(allowed * 0.8, 2) if status == 'Paid' else 0.0
            db.execute('INSERT INTO AP_CLAIM VALUES (?,?,?,?,?,?,?,?,?,?)', (cid, pid, d, status, billed, allowed, paid, round(allowed - paid, 2) if status == 'Paid' else 0.0,
                                                                             f'2028-12-{rng.randint(1, 28):02d}', rng.choice(['BLUE CROSS', 'MEDICARE PART B', 'STATE MEDICAID'])))
            n_lines = rng.randint(1, 3)
            for line in range(1, n_lines + 1):
                db.execute('INSERT INTO AP_CLAIM_PX VALUES (?,?,?,?,?,?,?)', (cid, line, rng.choice(CPT), 1.0, round(billed / n_lines, 2), d,
                                                                              rng.choice([None, None, '0450', '0300'])))
    db.commit(); db.close()
    return TRUTH
