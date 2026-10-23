"""Chart reader: single-patient questions over every structured source, the patient id recognizer, pronoun binding, the decision context for a
named patient, the note-question routing, and the cascade tier contract. Synthetic fixture warehouse only; no model, network or stack database."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_core.agere.clinical import chart_intents as CI
from clinical_core.agere.clinical import chart_notes as CN
from clinical_core.agere.clinical import chart_queries as Q
from clinical_core.agere.clinical import chart_reader as CR
from clinical_core.agere.clinical import decision_context as DC
from clinical_core.agere.clinical import patient_ids as PI
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood

AS_OF = '2030-05-27'
RICH = 'SYN-AAAAAAAA-AAA'     # living, rich chart
RICH2 = 'SYN-AAAAAAAA-AAB'    # shares a prefix with RICH
CLAIMS = 'WHP-BBBBBBBBBBBB'   # claims-only member
DEAD = 'P-D001'               # deceased
DEV = 'P-1001'                # development patient, member id below
TWIN_A, TWIN_B = 'P-2002', 'P2002'  # two ids that normalize to the same letters and digits


def build(path):
    with sqlite3.connect(Path(path) / 'semantic.sqlite3') as db:
        db.executescript(f'''
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('as_of','{AS_OF}');
        CREATE TABLE patient(patient_id TEXT, age_reported INT, sex_reported TEXT, coverage_type TEXT, coverage_status TEXT, pcp_clinician_id TEXT,
          pcp_practice_id TEXT, birth_date TEXT, race TEXT, ethnicity TEXT, preferred_language TEXT, interpreter_needed INT, marital_status TEXT, zip TEXT,
          county TEXT, state TEXT, age_latest INT, data_segment TEXT);
        INSERT INTO patient VALUES
          ('{RICH}',70,'F','medicare','active','CLIN-1','WH-PRACTICE-4','1960-02-02','White','Not Hispanic','English',0,'married','02134','25025','MA',70,'deep_chart'),
          ('{RICH2}',50,'M','commercial','active',NULL,NULL,'1980-02-02','White','Not Hispanic','English',0,NULL,'02134','25025','MA',50,'deep_chart'),
          ('{CLAIMS}',66,'M','medicaid','active',NULL,NULL,'1964-01-01','Black','Not Hispanic','Spanish',1,NULL,'02135','25025','MA',66,'claims_only'),
          ('{DEAD}',80,'M','medicare','inactive',NULL,'WH-PRACTICE-2','1950-01-01','White','Not Hispanic','English',0,NULL,'02136','25025','MA',80,'deep_chart'),
          ('{DEV}',60,'F','medicare','active',NULL,'WH-PRACTICE-1','1970-01-01','White','Not Hispanic','English',0,NULL,'02137','25025','MA',60,'deep_chart'),
          ('{TWIN_A}',40,'F','commercial','active',NULL,NULL,'1990-01-01',NULL,NULL,NULL,0,NULL,NULL,NULL,NULL,40,'deep_chart'),
          ('{TWIN_B}',40,'F','commercial','active',NULL,NULL,'1990-01-01',NULL,NULL,NULL,0,NULL,NULL,NULL,NULL,40,'deep_chart');
        CREATE TABLE death(patient_id TEXT PRIMARY KEY, death_date TEXT, death_source TEXT, sources TEXT, sources_disagree INT);
        INSERT INTO death VALUES ('{DEAD}','2029-03-23','payer','payer',0);
        CREATE TABLE organization(organization_id TEXT, display_name TEXT);
        INSERT INTO organization VALUES ('WH-PRACTICE-4','Synthetic care practice 5');
        CREATE TABLE enrollment_span(record_id TEXT, patient_id TEXT, member_id TEXT, payer_id TEXT, product TEXT, line_of_business TEXT, coverage_start TEXT,
          coverage_end TEXT, enrollment_reason TEXT, termination_reason TEXT, attribution_practice_id TEXT, attribution_npi TEXT, previous_member_id TEXT);
        INSERT INTO enrollment_span VALUES ('e1','{RICH}','MEM12345AB','WH-PAYER-1','medicare_ffs','medicare','2026-08-28',NULL,'baseline',NULL,'WH-PRACTICE-4',NULL,NULL),
          ('e2','{DEV}','2DX0U77CP79','WH-PAYER-1','medicare_ffs','medicare','2026-08-28',NULL,'baseline',NULL,'WH-PRACTICE-1',NULL,NULL),
          ('e3','{CLAIMS}','CLM9988776','WH-PAYER-2','medicaid','medicaid','2027-01-01',NULL,'baseline',NULL,'WH-PRACTICE-3',NULL,NULL);
        CREATE TABLE measurement(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, value_canonical REAL, unit_canonical TEXT, event_date TEXT,
          value_censor TEXT, result_current INT, excluded_reason TEXT);
        INSERT INTO measurement VALUES
          ('m1','{RICH}','labs','hba1c',7.2,'%','2029-11-01',NULL,1,NULL),('m2','{RICH}','labs','hba1c',7.9,'%','2030-02-01',NULL,1,NULL),
          ('m3','{RICH}','labs','hba1c',6.8,'%','2030-05-06',NULL,1,NULL),('m3x','{RICH}','claim_line','hba1c',99,'%','2030-05-20',NULL,1,NULL),
          ('m4','{RICH}','labs','egfr',58,'mL/min/1.73m2','2029-10-01',NULL,1,NULL),('m5','{RICH}','labs','egfr',52,'mL/min/1.73m2','2030-03-01',NULL,1,NULL),
          ('m6','{RICH}','vitals','systolic_bp',142,'mmHg','2030-04-01',NULL,1,NULL),('m7','{RICH}','vitals','diastolic_bp',88,'mmHg','2030-04-01',NULL,1,NULL),
          ('m8','{RICH}','vitals','weight',80.0,'kg','2029-06-28',NULL,1,NULL),('m9','{RICH}','vitals','weight',81.5,'kg','2030-04-16',NULL,1,NULL),
          ('m10','{RICH}','labs','ldl',130,'mg/dL','2028-01-01',NULL,1,NULL),('m11','{RICH}','labs','ldl',95,'mg/dL','2029-01-01',NULL,1,NULL),
          ('m12','{RICH}','labs','ldl',110,'mg/dL','2030-01-01',NULL,0,'superseded'),
          ('m13','{CLAIMS}','labs','hba1c',9.4,'%','2029-01-01',NULL,1,NULL),('m14','{DEAD}','labs','hba1c',8.8,'%','2028-12-01',NULL,1,NULL);
        CREATE TABLE measurement_latest(patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL, unit_canonical TEXT, same_day_values INT,
          same_day_min REAL, record_id TEXT, value_censor TEXT);
        INSERT INTO measurement_latest VALUES ('{RICH}','hba1c','2030-05-06',6.8,'%',1,6.8,'m3',NULL),('{RICH}','egfr','2030-03-01',52,'mL/min/1.73m2',1,52,'m5',NULL),
          ('{RICH}','systolic_bp','2030-04-01',142,'mmHg',1,142,'m6',NULL),('{RICH}','diastolic_bp','2030-04-01',88,'mmHg',1,88,'m7',NULL),
          ('{RICH}','weight','2030-04-16',81.5,'kg',1,81.5,'m9',NULL),('{RICH}','ldl','2029-01-01',95,'mg/dL',1,95,'m11',NULL),
          ('{CLAIMS}','hba1c','2029-01-01',9.4,'%',1,9.4,'m13',NULL),('{DEAD}','hba1c','2028-12-01',8.8,'%',1,8.8,'m14',NULL);
        CREATE TABLE medication_list_entry(patient_id TEXT, drug_key TEXT, name TEXT, rxcui TEXT, list_status TEXT, list_source TEXT, entries INT, first_listed TEXT,
          last_listed TEXT, last_fill_date TEXT, last_order_date TEXT, discrepancy TEXT, stale INT, record_id TEXT);
        INSERT INTO medication_list_entry VALUES
          ('{RICH}','lisinopril','lisinopril 10 MG Oral Tablet','1','active',NULL,1,'2027-01-01','2030-01-01','2030-04-01','2030-01-01',NULL,0,'ml1'),
          ('{RICH}','simvastatin','Simvastatin 20 MG Oral Tablet','2','discontinued',NULL,1,'2027-01-01','2029-01-01',NULL,NULL,NULL,0,'ml2'),
          ('{RICH}','warfarin','warfarin sodium 5 MG Oral Tablet','3','active',NULL,1,'2027-01-01','2030-01-01',NULL,NULL,'listed_active_no_fill_180d',1,'ml3'),
          ('{CLAIMS}','metformin','metformin 500 MG','4','active',NULL,1,'2027-01-01','2030-01-01',NULL,NULL,NULL,0,'ml4');
        CREATE TABLE fill(fill_id INT, patient_id TEXT, fill_date TEXT, days_supply INT, supply_end TEXT, class_key TEXT, drug_class TEXT, therapeutic_category TEXT,
          rxnorm_ingredient TEXT, drug_name TEXT, sources TEXT, record_ids TEXT);
        INSERT INTO fill VALUES (1,'{RICH}','2029-09-01',90,NULL,'statin','statin',NULL,NULL,'Atorvastatin','dispense','f1'),
          (2,'{RICH}','2029-12-01',90,NULL,'statin','statin',NULL,NULL,'Atorvastatin','dispense','f2'),(3,'{RICH}','2030-03-01',90,NULL,'statin','statin',NULL,NULL,'Atorvastatin','dispense','f3'),
          (4,'{RICH}','2030-04-01',30,NULL,'ace','ace_inhibitor',NULL,NULL,'Lisinopril','dispense','f4'),(5,'{RICH}','2027-01-01',30,NULL,'ace','ace_inhibitor',NULL,NULL,'Lisinopril','dispense','f5'),
          (6,'{DEV}','2027-05-05',30,NULL,'statin','statin',NULL,NULL,'Atorvastatin','dispense','f6');
        CREATE TABLE prescription(record_id TEXT, patient_id TEXT, drug_name TEXT, ordered_date TEXT, fill_status TEXT, first_fill_date TEXT, last_fill_date TEXT,
          quantity INT, days_supply INT, indication TEXT);
        INSERT INTO prescription VALUES ('rx1','{RICH}','lisinopril 10 mg tablet','2029-01-01','filled','2029-01-05','2030-04-01',30,30,NULL),
          ('rx2','{RICH}','furosemide 20 mg tablet','2029-02-01','not_filled',NULL,NULL,30,30,NULL),('rx3','{RICH}','metformin 500 mg tablet','2029-03-01','not_filled',NULL,NULL,30,30,NULL);
        CREATE TABLE returned_fill(fill_key TEXT, source TEXT, record_id TEXT, patient_id TEXT, event_date TEXT, drug_name TEXT, ndc TEXT, reason TEXT);
        INSERT INTO returned_fill VALUES ('rf1','pharmacy_dispenses','rfr1','{RICH}','2029-11-28','empagliflozin 10 mg tablet','1','patient_declined_collection');
        CREATE TABLE utilization_event(patient_id TEXT, kind TEXT, event_date TEXT, end_date TEXT, discharge_known INT, sources TEXT, in_ehr INT, source_records TEXT,
          record_id TEXT, organization_id TEXT, readmitted_30d INT, days_to_readmission INT);
        INSERT INTO utilization_event VALUES
          ('{RICH}','ed_visit','2030-01-10',NULL,NULL,'ehr',1,1,'u1','H1',NULL,NULL),('{RICH}','ed_visit','2029-08-10',NULL,NULL,'claim',0,1,'u2','H1',NULL,NULL),
          ('{RICH}','ed_visit','2028-02-01',NULL,NULL,'claim',0,1,'u3','H1',NULL,NULL),
          ('{RICH}','inpatient','2029-12-20','2029-12-27',1,'adt',1,1,'u4','H1',1,5),('{RICH}','inpatient','2029-06-01','2029-06-05',1,'adt',1,1,'u5','H1',NULL,NULL),
          ('{RICH}','inpatient','2028-03-01',NULL,0,'adt',1,1,'u6','H1',NULL,NULL),('{RICH}','observation','2029-09-09','2029-09-10',1,'adt',1,1,'u7','H1',NULL,NULL),
          ('{CLAIMS}','ed_visit','2030-02-02',NULL,NULL,'claim',0,1,'u8','H1',NULL,NULL);
        CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT, organization_id TEXT, readmitted_30d INT, days_to_readmission INT);
        INSERT INTO stay VALUES ('{RICH}','2029-12-20','2029-12-27','inpatient','H1',1,5),('{RICH}','2029-06-01','2029-06-05','inpatient','H1',0,NULL),('{CLAIMS}','2029-01-01','2029-01-03','inpatient','H1',0,NULL);
        CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type TEXT, type_concept TEXT, status TEXT, event_date TEXT, organization_id TEXT);
        INSERT INTO visit VALUES ('v1','{RICH}','Office visit','primary_care_visit','completed','2030-05-23','P4'),('v2','{RICH}','Telehealth','telehealth_contact','completed','2029-10-10','P4'),
          ('v3','{RICH}','ED','emergency_visit','completed','2030-01-10','H1'),('v4','{RICH}','Office visit','primary_care_visit',NULL,'2029-03-03','P4');
        CREATE TABLE appointment(appointment_id TEXT, patient_id TEXT, scheduled_date TEXT, final_status TEXT, outcome TEXT, purpose TEXT, organization_id TEXT);
        INSERT INTO appointment VALUES ('a1','{RICH}','2030-02-01','no_show','missed','follow_up','P4'),('a2','{RICH}','2029-02-01','no_show','missed','follow_up','P4'),
          ('a3','{RICH}','2030-03-01','fulfilled','kept','follow_up','P4'),('a4','{RICH}','2030-07-01','booked',NULL,'follow_up','P4');
        CREATE TABLE immunization(record_id TEXT, patient_id TEXT, vaccine TEXT, product TEXT, status TEXT, event_date TEXT, season TEXT, source TEXT);
        INSERT INTO immunization VALUES ('i1','{RICH}','influenza','Fluad','completed','2029-11-16','2029-2030','ehr'),('i2','{RICH}','influenza','Fluad','completed','2028-10-01','2028-2029','ehr'),
          ('i3','{RICH}','Tdap','Boostrix','completed','2025-01-01','2024-2025','ehr');
        CREATE TABLE cancer_screening(record_id TEXT, patient_id TEXT, test TEXT, result_status TEXT, event_date TEXT, result_date TEXT, birads_category INT, source TEXT);
        INSERT INTO cancer_screening VALUES ('cs1','{RICH}','bilateral_screening_mammography','final','2029-11-01','2029-11-03',2,'ehr'),
          ('cs2','{RICH}','bilateral_screening_mammography','claim_billed','2030-03-01',NULL,NULL,'claim');
        CREATE TABLE procedure_line(record_id TEXT, patient_id TEXT, service_date TEXT, cpt TEXT, counts_in_metrics INT);
        INSERT INTO procedure_line VALUES ('pl1','{RICH}','2029-05-05','45378',1);
        CREATE TABLE wellness_visit(record_id TEXT, patient_id TEXT, visit_type TEXT, awv_kind TEXT, status TEXT, event_date TEXT, source TEXT);
        INSERT INTO wellness_visit VALUES ('w1','{RICH}','medicare_annual_wellness','subsequent','completed','2029-10-26','ehr');
        CREATE TABLE allergy_entry(patient_id TEXT, substance TEXT, substance_received TEXT, reaction TEXT, no_known_allergies INT, status TEXT, first_recorded TEXT,
          last_confirmed TEXT, record_id TEXT);
        INSERT INTO allergy_entry VALUES ('{RICH}','amoxicillin','Amoxicillin','hives',0,'active','2027-01-01','2029-01-01','al1'),
          ('{RICH}','sulfa','Sulfa','rash',0,'active','2027-01-01','2029-01-01','al2'),('{CLAIMS}','no known allergies','NKDA',NULL,1,'active','2027-01-01','2029-01-01','al3');
        CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, icd10 TEXT, event_date TEXT, is_diagnosis INT, diagnosis_basis TEXT);
        INSERT INTO condition VALUES ('c1','{RICH}','conditions','hypertension','I10','2028-01-01',1,NULL),('c2','{RICH}','conditions','diabetes','E11.9','2027-01-01',0,NULL),
          ('c3','{CLAIMS}','conditions','diabetes','E11.9','2029-01-01',NULL,'laboratory_evidence');
        CREATE TABLE payer_claim_diagnosis(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, icd10 TEXT, counts_in_metrics INT);
        INSERT INTO payer_claim_diagnosis VALUES ('k1','{CLAIMS}','2029-03-01','I50.9',1);
        CREATE TABLE concept_patient(concept TEXT, kind TEXT, patient_id TEXT, records INT, first_date TEXT, last_date TEXT);
        INSERT INTO concept_patient VALUES ('hypertension','condition','{RICH}',2,'2028-01-01','2030-01-01'),('diabetes','condition','{CLAIMS}',1,'2029-01-01','2029-01-01'),
          ('heart_failure','condition','{CLAIMS}',1,'2029-03-01','2029-03-01');
        CREATE TABLE problem_list_entry(patient_id TEXT, problem_key TEXT, icd10 TEXT, label TEXT, clinical_status TEXT, first_recorded TEXT, last_confirmed TEXT, resolved_date TEXT, record_id TEXT);
        INSERT INTO problem_list_entry VALUES ('{RICH}','I10','I10','Essential hypertension','active','2027-01-01','2030-01-01',NULL,'pl_1'),
          ('{RICH}','M19','M19.90','Osteoarthritis','resolved','2027-01-01','2028-01-01','2028-06-01','pl_2');
        CREATE TABLE social_need(patient_id TEXT, need TEXT, positive INT, instrument TEXT, event_date TEXT, record_id TEXT);
        INSERT INTO social_need VALUES ('{RICH}','transport',1,'AHC','2029-12-13','sn1'),('{RICH}','transport',0,'AHC','2028-12-13','sn2'),('{RICH}','food',0,'AHC','2029-12-13','sn3'),
          ('{RICH}','cost_of_care',1,'AHC','2026-09-23','sn4');
        CREATE TABLE sdoh_vendor(record_id TEXT, patient_id TEXT, delivery_date TEXT, match_status TEXT, est_household_income_band TEXT, food_access_low INT,
          financial_stress_score REAL, social_isolation_score REAL, transportation_risk_score REAL, housing_instability_score REAL, broadband_access_score REAL);
        INSERT INTO sdoh_vendor VALUES ('sv1','{RICH}','2029-01-01','exact','50-75k',0,6.0,3.0,8.0,2.0,5.0);
        CREATE TABLE patient_svi(patient_id TEXT, county_fips TEXT, svi_overall REAL, svi_socioeconomic REAL, svi_household REAL, svi_minority_language REAL, svi_housing_transportation REAL);
        INSERT INTO patient_svi VALUES ('{RICH}','25025',82.5,70,60,50,40),('{CLAIMS}','25025',20.0,10,10,10,10);
        CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, claim_type TEXT, service_from TEXT, final_status TEXT, paid_cents INT, allowed_cents INT, charge_cents INT,
          patient_responsibility_cents INT, counts_in_metrics INT, record_id TEXT);
        INSERT INTO payer_claim VALUES ('pc1','{RICH}','professional','2030-01-01','paid',150000,200000,300000,10000,1,'r1'),('pc2','{RICH}','facility','2029-06-01','paid',50000,60000,90000,0,1,'r2'),
          ('pc3','{RICH}','professional','2028-01-01','paid',99900,99900,99900,0,1,'r3'),('pc4','{RICH}','professional','2030-02-01','paid',999900,0,0,0,0,'r4'),
          ('pc5','{RICH}','professional','2030-03-01','denied',0,0,10000,0,1,'r5');
        CREATE TABLE payer_pharmacy_claim(record_id TEXT, patient_id TEXT, fill_date TEXT, drug_name TEXT, days_supply INT, plan_paid_cents INT, patient_pay_cents INT, counts_as_fill INT);
        INSERT INTO payer_pharmacy_claim VALUES ('pp1','{RICH}','2030-04-01','lisinopril',30,1200,300,1),('pp2','{RICH}','2030-04-05','lisinopril',30,9999,0,0);
        CREATE TABLE smoking_status(patient_id TEXT, status TEXT, status_received TEXT, status_date TEXT, record_id TEXT);
        INSERT INTO smoking_status VALUES ('{RICH}','former','Quit 2020','2026-08-28','sm1');
        ''')


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        build(cls.tmp.name)
        cls.stack = SimpleNamespace(path=Path(cls.tmp.name), allow_live=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ask(self, text, **kw):
        return CR.ask(self.stack, text.replace('{P}', RICH), **kw)


class PatientIds(Base):
    def resolve(self, text, prior=None):
        return PI.resolve(self.stack.path, text, prior)

    def test_the_three_formats_and_variants_are_one_id(self):
        for text in (f'latest a1c for {RICH}', f'latest a1c for {RICH.lower()}', 'a1c for SYN_AAAAAAAA_AAA', 'a1c for SYNAAAAAAAAAAA', f"{RICH}'s a1c?",
                     'a1c for syn‑aaaaaaaa‑aaa'):
            r = self.resolve(text)
            self.assertEqual((r['status'], r['patient_id']), ('found', RICH), text)
        self.assertEqual(self.resolve('what is WHP-bbbbbbbbbbbb on?')['patient_id'], CLAIMS)
        self.assertEqual(self.resolve('labs for p-d001')['patient_id'], DEAD)
        self.assertEqual(self.resolve('labs for P1001')['patient_id'], DEV)

    def test_member_id_resolves_exactly_and_words_do_not(self):
        r = self.resolve('what is the plan for member MEM12345AB?')
        self.assertEqual((r['status'], r['patient_id'], r['how']), ('found', RICH, 'member_id'))
        self.assertEqual(self.resolve('which practice is the patient attributed to hospitalized')['status'], 'none')

    def test_never_guesses(self):
        unknown = self.resolve('a1c for SYN-ZZZZZZZZ-ZZZ')
        self.assertEqual(unknown['status'], 'none')  # not shaped like the hex format: an ordinary word
        unknown = self.resolve('a1c for SYN-12345678-ABC')
        self.assertEqual((unknown['status'], unknown['patient_id']), ('unknown', None))
        self.assertIn('not in this warehouse', unknown['message'])
        partial = self.resolve('a1c for SYN-AAAAAAAA')
        self.assertEqual((partial['status'], partial['patient_id']), ('ambiguous', None))
        self.assertIn(RICH, partial['message'])
        self.assertIn(RICH2, partial['message'])
        self.assertIsNone(CR.resolve_subject(self.stack, 'a1c for SYN-AAAAAAAA'))

    def test_ambiguous_normalized_ids_ask(self):
        self.assertEqual(self.resolve('a1c for P2002')['patient_id'], TWIN_B)  # an exact spelling of one id wins
        self.assertEqual(self.resolve('a1c for p-2002')['patient_id'], TWIN_A)
        both = PI.find(self.stack.path, 'a1c for P_2002')  # neither spelling: ask
        self.assertEqual(both[0]['status'], 'ambiguous')
        self.assertEqual(sorted(both[0]['candidates']), sorted([TWIN_A, TWIN_B]))
        self.assertIsNone(CR.resolve_subject(self.stack, 'a1c for P_2002'))

    def test_two_patients_in_one_question_are_not_a_chart_question(self):
        r = self.resolve(f'compare {RICH} with {CLAIMS}')
        self.assertEqual(r['status'], 'multiple')
        self.assertIsNone(CR.resolve_subject(self.stack, f'compare {RICH} with {CLAIMS}'))

    def test_pronouns_bind_the_prior_subject_only(self):
        prior = PI.make_subject(RICH)
        for text in ('is he on a statin?', 'her A1c', 'what was his last fill', 'when was the patient last admitted', 'did she get a flu shot'):
            r = self.resolve(text, prior)
            self.assertEqual((r['status'], r['patient_id'], r['how']), ('found', RICH, 'pronoun'), text)
            self.assertEqual(self.resolve(text)['status'], 'none')
        self.assertEqual(self.resolve('how many of them have diabetes patients', prior)['status'], 'none')  # a cohort "them" never binds
        self.assertEqual(PI.rewrite_pronouns('her A1c', RICH), f"{RICH}'s A1c")
        self.assertEqual(PI.rewrite_pronouns('is he on a statin?', RICH), f'is {RICH} on a statin?')
        self.assertEqual(PI.rewrite_pronouns('give her a flu shot', RICH).count(RICH), 1)

    def test_rebind_hook_sets_prior_and_rewrites_only_without_a_named_patient(self):
        PI.remember('conv-1', PI.make_subject(RICH, frame={'intent': 'latest_value', 'domain': 'lab'}))
        self.assertEqual(PI.rebind(self.stack.path, 'conv-1', 'and his last fill?'), f"and {RICH}'s last fill?")
        self.assertEqual(PI.current_prior()['patient_id'], RICH)
        self.assertEqual(PI.rebind(self.stack.path, 'conv-1', f'a1c for {CLAIMS} and his'), f'a1c for {CLAIMS} and his')  # names a patient: untouched
        self.assertEqual(PI.rebind(self.stack.path, None, 'her A1c'), 'her A1c')
        self.assertIsNone(PI.current_prior())

    def test_named_patient_detector_is_format_only(self):
        self.assertTrue(PI.names_a_patient(f'a1c for {RICH}'))
        self.assertFalse(PI.names_a_patient('how many patients with diabetes in the practice'))


class Structured(Base):
    def test_latest_lab_has_value_unit_date_source_and_receipt(self):
        a = self.ask('What is the latest HbA1c for {P}?')
        self.assertEqual((a['status'], a['answer_type'], a['value'], a['unit']), ('answered', 'value', 6.8, '%'))
        self.assertIn('2030-05-06', a['headline'])
        self.assertEqual(a['sources'][0]['record_id'], 'm3')
        self.assertEqual(a['patient_ids'], [RICH])
        self.assertEqual(a['decision_context']['lab_mode'], 'latest')
        self.assertTrue(a['read_back'].startswith('Latest HbA1c for ' + RICH))
        self.assertEqual(a['subject']['patient_id'], RICH)

    def test_latest_ignores_billed_claim_lines_and_superseded_results(self):
        self.assertEqual(self.ask('Highest HbA1c ever for {P}')['value'], 7.9)  # 99 is a claim line
        self.assertEqual(self.ask('What is the highest LDL ever for {P}?')['value'], 130)  # 110 is superseded
        self.assertEqual(self.ask('latest LDL for {P}')['value'], 95)

    def test_previous_value_trend_and_change(self):
        self.assertEqual(self.ask('What was the HbA1c result before the most recent one on {P}?')['value'], 7.9)
        t = self.ask('Is the diabetes control getting better or worse for {P}? Compare the last two A1c results.')
        self.assertEqual((t['answer_type'], t['value']), ('label', 'improving'))
        self.assertIn('Improving', t['headline'])
        e = self.ask('Has the eGFR of {P} been getting better or worse?')
        self.assertEqual(e['value'], 'worsening')
        w = self.ask('How much has the weight of {P} changed over the last year (first vs last reading in the window)?')
        self.assertEqual((w['answer_type'], w['value'], w['unit']), ('value', 1.5, 'kg'))
        one = self.ask('Has the potassium of {P} been going up?')
        self.assertEqual((one['answer_type'], one['value']), ('label', 'insufficient'))  # no potassium results: no direction is invented

    def test_blood_pressure_pairs_and_timeline_count_status(self):
        bp = self.ask("What's the blood pressure for {P}?")
        self.assertIn('142/88 mmHg', bp['headline'])
        t = self.ask('Show all the HbA1c results for {P}')
        self.assertEqual(t['count'], 3)
        self.assertEqual(self.ask('How many A1c tests has {P} had in the last 12 months?')['count'], 3)
        s = self.ask('Has {P} had a urine albumin/creatinine test in the last 12 months?')
        self.assertEqual((s['answer_type'], s['value']), ('yes_no', False))
        self.assertEqual(s['decision_context']['lab_mode'], 'any')

    def test_unmeasured_concept_is_stated_not_invented(self):
        a = self.ask("What's the BMI for {P}?")
        self.assertEqual(a['answer_type'], 'none')
        self.assertIn('not a measurement concept', a['headline'])

    def test_medication_list_status_and_fills(self):
        m = self.ask('List the active medications for {P}.')
        self.assertEqual(m['count'], 2)
        self.assertEqual(sorted(m['facts']['drugs']), ['lisinopril', 'warfarin'])
        on_ace = self.ask('Is {P} on an ACE inhibitor?')
        self.assertEqual((on_ace['answer_type'], on_ace['value']), ('yes_no', True))
        statin = self.ask('Is {P} on a statin?')
        self.assertIs(statin['value'], True)  # fills in the last 12 months, though the list entry is discontinued
        self.assertTrue(statin['facts']['fills_12m'] >= 1)
        self.assertIs(self.ask('Is {P} taking warfarin?')['value'], True)
        self.assertIs(self.ask('Is {P} on metformin?')['value'], False)
        last = self.ask('When did {P} last pick up a statin?')
        self.assertEqual((last['answer_type'], last['value']), ('date', '2030-03-01'))
        self.assertEqual(self.ask('How many lisinopril fills did {P} have in the last year?')['count'], 1)

    def test_pdc_and_prescriptions_and_returned_fills(self):
        p = self.ask('What is the proportion of days covered for statins for {P}?')
        self.assertEqual(p['answer_type'], 'value')
        self.assertTrue(0 < p['value'] <= 1)
        self.assertEqual(p['facts']['fills'], 3)
        rx = self.ask('Which prescriptions written for {P} were never filled?')
        self.assertEqual(sorted(rx['facts']['drugs']), ['furosemide 20 mg tablet', 'metformin 500 mg tablet'])
        r = self.ask('Which fills were returned for {P}?')
        self.assertEqual(r['count'], 1)

    def test_all_source_utilization(self):
        self.assertEqual(self.ask('How many ED visits has {P} had in the past year?')['count'], 2)
        self.assertEqual(self.ask('How many ED visits has {P} had?')['count'], 3)
        self.assertEqual(self.ask('How many hospital admissions did {P} have in 2029?')['count'], 2)
        d = self.ask('When was the most recent 2029 hospital admission of {P} discharged?')
        self.assertEqual(d['value'], '2029-12-27')
        open_stay = self.ask('When was the most recent admission of {P} in 2028 discharged?')
        self.assertEqual(open_stay['answer_type'], 'none')
        self.assertIn('no recorded discharge', open_stay['headline'])
        self.assertEqual(self.ask('How many observation stays has {P} had?')['count'], 1)
        r = self.ask('Was {P} ever readmitted within 30 days of a discharge?')
        self.assertIs(r['value'], True)
        self.assertEqual(self.ask(f'Was {CLAIMS} ever readmitted within 30 days?')['value'], False)
        self.assertEqual(self.ask(f'How many ED visits has {CLAIMS} had?')['count'], 1)  # claims-only member

    def test_visits_and_appointments(self):
        self.assertEqual(self.ask('How many visits has {P} had in 2029?')['count'], 2)  # a null status counts as completed; the ED visit is a utilization event
        self.assertEqual(self.ask('When was the last office visit of {P}?')['value'], '2030-05-23')
        self.assertEqual(self.ask('How many no-shows has {P} had in the past year?')['count'], 1)
        self.assertEqual(self.ask('When is the next appointment of {P}?')['value'], '2030-07-01')

    def test_immunization_screening_wellness(self):
        flu = self.ask('Did {P} get a flu shot this past season? If so, when?')
        self.assertEqual((flu['answer_type'], flu['value']), ('yes_no', True))
        self.assertIn('2029-11-16', flu['headline'])
        self.assertEqual(self.ask('When did {P} last get a flu shot?')['value'], '2029-11-16')
        mammo = self.ask("When was {P}'s last screening mammogram?")
        self.assertEqual(mammo['value'], '2029-11-01')  # the later billed row is not a result
        self.assertEqual(mammo['facts']['later_billed'], ['2030-03-01'])
        colo = self.ask('When did {P} last get a colonoscopy?')
        self.assertEqual(colo['answer_type'], 'none')
        self.assertIn('billed procedure', ' '.join(colo['notes']))
        self.assertEqual(self.ask('When was the last annual wellness visit of {P}?')['value'], '2029-10-26')

    def test_allergies_conditions_problems_smoking(self):
        al = self.ask('What allergies are recorded for {P}?')
        self.assertEqual(sorted(al['facts']['substances']), ['amoxicillin', 'sulfa'])
        self.assertIs(self.ask('Is {P} allergic to penicillin?')['value'], True)  # amoxicillin is a penicillin
        self.assertIs(self.ask('Is {P} allergic to latex?')['value'], False)
        self.assertEqual(self.ask(f'What allergies are recorded for {CLAIMS}?')['count'], 0)
        self.assertIs(self.ask('Does {P} have hypertension?')['value'], True)
        self.assertIs(self.ask('Does {P} have diabetes?')['value'], False)  # the only diabetes row is a rule-out
        self.assertIs(self.ask(f'Does {CLAIMS} have diabetes?')['value'], True)  # laboratory evidence counts for diabetes
        self.assertIs(self.ask(f'Does {CLAIMS} have heart failure?')['value'], True)  # a counted claim diagnosis
        self.assertEqual(self.ask("What's on the problem list of {P}?")['facts']['problems'], ['Essential hypertension'])
        self.assertEqual(self.ask('Does {P} smoke?')['value'], False)

    def test_social_and_claims_and_coverage(self):
        needs = self.ask('Does {P} have any positive social needs?')
        self.assertEqual(sorted(needs['facts']['needs']), ['cost_of_care', 'transport'])
        self.assertIs(self.ask('Is transportation a need for {P}?')['value'], True)
        self.assertIs(self.ask('Is food a need for {P}?')['value'], False)
        self.assertEqual(self.ask('What is the social vulnerability index for {P}?')['value'], 82.5)
        spend = self.ask('How much did the plan pay in claims for {P} over the last 12 months?')
        self.assertEqual((spend['answer_type'], spend['value'], spend['unit']), ('value', 1500.0 + 500.0, 'USD'))
        self.assertIsNone(spend['count'])
        self.assertEqual(self.ask('What was the total allowed amount for {P} in 2029?')['value'], 600.0)
        self.assertEqual(self.ask('How much did the pharmacy plan pay for {P} in the last year?')['value'], 12.0)
        cov = self.ask('What is the insurance of {P}?')
        self.assertEqual(cov['facts']['product'], 'medicare_ffs')
        self.assertEqual(self.ask('What is the member id of {P}?')['value'], 'MEM12345AB')
        prac = self.ask('Which practice is {P} attributed to?')
        self.assertEqual(prac['value'], 'WH-PRACTICE-4')
        self.assertIn('Synthetic care practice 5', prac['headline'])
        self.assertEqual(self.ask('How old is {P}?')['value'], 70)

    def test_summary_is_a_structured_brief_with_sections_and_sources(self):
        a = self.ask('Summarize {P}')
        self.assertEqual(a['answer_type'], 'summary')
        names = [s['section'] for s in a['sections']]
        for want in ('identity', 'coverage', 'medications', 'labs', 'influenza_vaccine', 'allergies', 'spend_12m'):
            self.assertIn(want, names)
        self.assertGreater(len(a['sources']), 5)
        self.assertEqual(self.ask('What should I know before seeing {P}?')['answer_type'], 'summary')
        self.assertIn('Latest results', a['text'])
        narrated = CR.ask(self.stack, f'Summarize {RICH}', narrative_model=lambda system, user, max_tokens: ('Fake narrative.', {'model': 'fake'}))
        self.assertTrue(narrated['narrative']['generated_from_facts'])
        self.assertNotIn('narrative', a)

    def test_every_answer_cites_sources_and_the_receipt(self):
        for text in ('latest HbA1c for {P}', 'List the active medications for {P}', 'How many ED visits has {P} had?', 'Is {P} allergic to penicillin?',
                     'When was the last office visit of {P}?', 'Does {P} have hypertension?'):
            a = self.ask(text)
            self.assertEqual(a['status'], 'answered', text)
            self.assertIn('decision_context', a)
            self.assertEqual(a['decision_context']['version'], DC.VERSION)
            self.assertTrue(a['read_back'], text)
            self.assertTrue(a['notes'] or a['sources'], text)


class DecisionContext(Base):
    def test_named_deceased_patient_is_answered_and_flagged(self):
        a = self.ask(f'What is the latest HbA1c for {DEAD}?')
        self.assertEqual((a['status'], a['value']), ('answered', 8.8))
        self.assertTrue(a['deceased']['deceased'])
        self.assertIn('deceased', a['headline'])
        self.assertIn('a named patient is answered even though', a['headline'])
        self.assertFalse(a['decision_context']['population']['living_only'])
        self.assertIn('named patient', a['decision_context']['population']['reason'])
        self.assertIn('kept although deceased', a['read_back'])
        self.assertIs(self.ask(f'Is {DEAD} alive?')['value'], False)
        self.assertIsNone(self.ask('latest HbA1c for {P}')['deceased'])

    def test_population_default_is_unchanged_for_cohort_questions(self):
        self.assertTrue(DC.population('How many patients have diabetes?')['living_only'])
        self.assertTrue(DC.population(f'How many patients are similar to {RICH}?')['living_only'])
        self.assertFalse(DC.population(f'What is the latest HbA1c for {RICH}?')['living_only'])
        self.assertFalse(DC.population('How many patients died last year?')['living_only'])

    def test_lab_mode_and_windows_follow_the_decision_context(self):
        self.assertEqual(self.ask('How many ED visits has {P} had in the past year?')['decision_context']['windows'][0]['kind'], 'rolling_12_months')
        w = CI.window_of('visits in the past year', AS_OF)
        self.assertEqual((w['start'], w['end'], w['start_exclusive']), ('2029-05-27', AS_OF, True))
        self.assertEqual(CI.window_of('admissions in 2029', AS_OF)['start'], '2029-01-01')
        self.assertEqual(CI.window_of('in the last 3 months', AS_OF)['start'], '2030-02-27')
        self.assertEqual(CI.window_of('during the flu season', AS_OF)['start'], '2029-08-01')
        self.assertIsNone(CI.window_of('how many admissions', AS_OF))


class Grammar(unittest.TestCase):
    def parse(self, text, **kw):
        return CI.parse(text, AS_OF, **kw)

    def test_domains_and_intents(self):
        cases = {
            'What is the latest eGFR?': ('latest_value', 'lab'), 'How many ED visits in 2029?': ('count_in_window', 'ed_visit'),
            'When was the last statin fill?': ('last_event', 'fill'), 'Is he on a statin?': ('status', 'medication'),
            'What medications is the patient on?': ('list', 'medication'), 'Any admissions this year?': ('status', 'inpatient'),
            'How much did claims cost last year?': ('sum', 'claims'), 'Was she ever readmitted?': ('status', 'readmission'),
            'Which prescriptions were never filled?': ('list', 'prescription'), 'What is the practice?': ('latest_value', 'practice'),
            'Summarize this patient': ('summary', 'summary'),
        }
        for text, (intent, domain) in cases.items():
            it = self.parse(text)
            self.assertEqual((it['intent'], it['domain']), (intent, domain), text)

    def test_an_elliptical_follow_up_inherits_and_a_named_patient_does_not(self):
        prior = {'intent': 'latest_value', 'domain': 'lab', 'concept': {'kind': 'lab', 'value': 'hba1c', 'concepts': ['hba1c']}, 'window': None}
        it = self.parse('and the one before that?', prior=prior)
        self.assertEqual((it['domain'], it['ordinal'], it['concept']['value']), ('lab', 2, 'hba1c'))
        prior_adm = {'intent': 'count_in_window', 'domain': 'inpatient', 'concept': None, 'window': CI.window_of('in 2029', AS_OF)}
        it = self.parse('when was the most recent one discharged?', prior=prior_adm)
        self.assertEqual((it['domain'], it['attribute'], it['window']['kind']), ('inpatient', 'discharge_date', 'calendar_year'))
        it = self.parse('when was the most recent one discharged?', prior=prior_adm, quotes=['SYN-X'])
        self.assertIsNone(it['window'])

    def test_unknowns_leave_gaps_for_system_one(self):
        it = self.parse('what are the numbers for the thing?')
        self.assertIn('domain', it['gaps'])
        it = self.parse('latest value of the foo test')
        self.assertIn('concept', it['gaps']) if it['domain'] == 'lab' else self.assertIn('domain', it['gaps'])


class SystemOneSlots(Base):
    def test_open_slots_are_chosen_from_closed_lists(self):
        asked = []

        def decide(question, options, evidence, *, as_of, order=None, task_key=None, allow_live=True, **kw):
            asked.append((task_key, sorted(options)))
            pick = {'chart_reader_domain': 'lab', 'chart_reader_concept_lab': 'egfr'}.get(task_key)
            if pick is None:
                return {'label': 'unknown', 'probabilities': {}, 'steps': []}
            return {'label': pick, 'probabilities': {pick: 0.99}, 'decided_by': 'jev', 'steps': [{'provider': 'jev', 'label': pick, 'probability': 0.99, 'cost_usd': 0.0002}]}
        a = self.ask('How are the kidneys of {P} doing lately?', decide=decide)
        self.assertEqual(a['status'], 'answered', a.get('headline'))
        self.assertEqual(a['intent']['source'], 'system_one')
        self.assertEqual(a['intent']['concept']['value'], 'egfr')
        self.assertIn(52, [a['value']])
        self.assertEqual([k for k, _ in asked][:2], ['chart_reader_domain', 'chart_reader_concept_lab'])
        self.assertIn('lab', dict(asked)['chart_reader_domain'])
        self.assertEqual(a['cost']['new_paid_api_calls'], 2)

    def test_unsettled_slots_decline_instead_of_guessing(self):
        none = lambda *a, **k: {'label': 'unknown', 'probabilities': {}, 'steps': []}  # noqa: E731
        a = self.ask('what are the numbers for the thing for {P}?', decide=none)
        self.assertEqual(a['status'], 'unsupported')
        self.assertIn('could not be typed', a['headline'])


class Clarification(Base):
    def test_ask_returns_a_clarification_never_an_answer(self):
        a = CR.ask(self.stack, 'latest a1c for SYN-AAAAAAAA')
        self.assertEqual(a['status'], 'needs_clarification')
        self.assertIn(RICH2, a['clarification'])
        self.assertEqual(CR.ask(self.stack, 'latest a1c for SYN-12345678-ABC')['status'], 'needs_clarification')
        self.assertEqual(CR.ask(self.stack, 'how many patients have diabetes')['status'], 'unsupported')

    def test_conversation_follow_ups_bind_the_subject_and_frame(self):
        first = CR.ask(self.stack, f'What is the latest HbA1c for {RICH}?', conversation_id='c-follow')
        self.assertEqual(first['subject']['frame']['domain'], 'lab')
        second = CR.ask(self.stack, 'and the one before that?', conversation_id='c-follow')
        self.assertEqual((second['patient_ids'], second['value']), ([RICH], 7.9))
        third = CR.ask(self.stack, 'is he on a statin?', conversation_id='c-follow')
        self.assertEqual(third['patient_ids'], [RICH])
        self.assertEqual(third['intent']['domain'], 'medication')
        other = CR.ask(self.stack, 'is she on a statin?', conversation_id='c-nobody')
        self.assertEqual(other['status'], 'unsupported')


class Spec(Base):
    def test_resolve_subject_and_answer_interfaces(self):
        s = CR.resolve_subject(self.stack, f'labs for {RICH}')
        self.assertEqual((s['patient_id'], s['quote']), (RICH, RICH))
        a = CR.answer(self.stack, {'subject': {'kind': 'patient', 'patient_id': RICH, 'quote': RICH},
                                   'chart': {'intent': 'latest_value', 'domain': 'lab', 'concept': {'kind': 'lab', 'value': 'egfr', 'concepts': ['egfr']}}},
                      as_of=AS_OF, question='latest eGFR')
        self.assertEqual((a['status'], a['value'], a['unit']), ('answered', 52, 'mL/min/1.73m2'))
        with self.assertRaises(ValueError):
            CR.validate_chart({'intent': 'telepathy', 'domain': 'lab'})
        self.assertEqual(CR.answer(self.stack, {'subject': {'kind': 'patient', 'patient_id': 'P-NOPE9'}}, question='x')['status'], 'unsupported')
        self.assertEqual(CR.answer(self.stack, {'question': 'x'}, question='x')['status'], 'unsupported')


class FakeNotes:
    """Stand-in for builder A's note_frames: records the predicates it was asked and returns canned readings."""
    FRAMES = {}

    def __init__(self, readings=None):
        self.calls, self.readings = [], readings or {}

    def validate_predicate(self, p):
        return p

    def answer_patient(self, stack, predicate, patient_id, *, as_of, ledger=None, allow_live=True):
        self.calls.append((predicate, patient_id))
        return self.readings.get((predicate['frame'], predicate['concept']['term']), {'status': 'not_mentioned', 'detail': {}, 'evidence': [], 'p': 0.9})


class Notes(Base):
    def test_routing_structured_versus_notes(self):
        it = lambda t: CI.parse(t, AS_OF)  # noqa: E731
        for text in ('What was the reason aspirin was stopped for SYN-1?', 'Did the patient accept statin therapy when it was discussed?',
                     'Do the notes say he has trouble getting to appointments?', 'Is a DNR order or code status documented?',
                     'What was the impression on the outside chest x-ray report?', 'How many falls did she report in the past year at the annual wellness visit?'):
            self.assertTrue(CN.is_note_question(text, it(text)), text)
        for text in ('What is the latest HbA1c?', 'How many ED visits in the past year?', 'List the active medications.', 'When was the last mammogram?',
                     'What allergies are recorded?', 'Is he on a statin?', 'How much did the plan pay in claims?'):
            self.assertFalse(CN.is_note_question(text, it(text)), text)

    def test_predicates_from_the_asking_sentence_with_a_window(self):
        p = CN.build('A 2029-08-05 note stopped lisinopril because of angioedema concern. Do later notes still list lisinopril?', AS_OF)
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]['frame'], p[0]['concept']['term']), ('medication_event', 'lisinopril'))
        self.assertEqual(p[0]['window']['start'], '2029-08-06')
        p = CN.build('What was the reason aspirin was stopped?', AS_OF)
        self.assertEqual((p[0]['_ask_detail'], p[0]['return_detail']), ({'event': 'stopped'}, ['reason']))
        p = CN.build('Did the patient accept statin therapy when it was discussed?', AS_OF)
        self.assertEqual((p[0]['frame'], p[0]['_ask_detail']), ('preference_refusal', {'decision': 'accepted'}))
        p = CN.build('Was acute coronary syndrome ruled out during the April 2027 admission, and what did the stress test show?', AS_OF)
        self.assertEqual({x['frame'] for x in p}, {'condition', 'procedure_test'})
        self.assertEqual(p[0]['window'], {'start': '2027-04-01', 'end': '2027-04-30', 'quote': 'April 2027'})
        p = CN.build('What follow-up did the 2027-10-28 discharge summary recommend, and where was the patient discharged to?', AS_OF)
        self.assertEqual((p[0]['window']['start'], p[0]['window']['end']), ('2027-10-28', '2027-10-28'))
        self.assertEqual(p[0]['return_detail'], ['disposition', 'timeframe', 'item'])

    def test_system_one_picks_the_frame_when_the_cues_are_silent(self):
        asked = []

        def chose(text, options, key):
            asked.append((key, sorted(options)))
            return 'symptom'
        p = CN.build('Anything in the notes about shortness of breath?', AS_OF, choose=chose)
        self.assertEqual(asked[0][0], 'chart_reader_note_frame')
        self.assertIn('symptom', asked[0][1])
        self.assertEqual(p[0]['frame'], 'symptom')
        self.assertEqual(p[0]['concept']['term'], 'shortness of breath')

    def test_note_answer_uses_note_frames_with_yes_no_lead_and_citations(self):
        evidence = [{'record_id': 'N-77', 'date': '2029-08-05', 'note_type': 'progress_note', 'quote': 'Stopped aspirin due to GI bleed.'}]
        nf = FakeNotes({('medication_event', 'aspirin'): {'status': 'documented', 'detail': {'event': 'stopped', 'reason': ['side_effect']}, 'evidence': evidence, 'p': 0.93}})
        a = CR.ask(self.stack, f'What was the reason aspirin was stopped for {RICH}?', nf=nf)
        self.assertEqual((a['status'], a['answer_type']), ('answered', 'note'))
        self.assertIn('side_effect', a['headline'])
        self.assertIn('N-77', a['headline'])
        self.assertEqual(a['sources'][0]['record_id'], 'N-77')
        self.assertEqual(a['intent']['domain'], 'notes')
        self.assertEqual(nf.calls[0][1], RICH)
        self.assertEqual(nf.calls[0][0]['frame'], 'medication_event')
        self.assertTrue(a['read_back'].startswith('What the notes say for ' + RICH))
        no = CR.ask(self.stack, f'Did {RICH} accept statin therapy when it was discussed?',
                    nf=FakeNotes({('preference_refusal', 'statin'): {'status': 'documented', 'detail': {'decision': ['declined']}, 'evidence': evidence, 'p': 0.9}}))
        self.assertTrue(no['headline'].startswith('No:'), no['headline'])
        yes = CR.ask(self.stack, f'Did {RICH} accept statin therapy when it was discussed?',
                     nf=FakeNotes({('preference_refusal', 'statin'): {'status': 'documented', 'detail': {'decision': ['accepted']}, 'evidence': evidence, 'p': 0.9}}))
        self.assertTrue(yes['headline'].startswith('Yes:'))
        none = CR.ask(self.stack, f'Is a DNR order or code status documented for {RICH}?', nf=FakeNotes())
        self.assertTrue(none['headline'].startswith('Not documented'))
        unsure = CR.ask(self.stack, f'Is a DNR order or code status documented for {RICH}?',
                        nf=FakeNotes({('care_process', 'code status'): {'status': 'unknown', 'detail': {}, 'evidence': [], 'p': None}}))
        self.assertTrue(unsure['headline'].startswith('Undecided'))  # an undecided patient is never read as "no"

    def test_an_elliptical_note_follow_up_reuses_the_frame_with_a_new_concept(self):
        nf = FakeNotes()
        first = CR.ask(self.stack, f'Does {RICH} report taking oxycodone?', nf=nf, conversation_id='n-conv')
        second = CR.ask(self.stack, 'and morphine?', nf=nf, conversation_id='n-conv')
        self.assertEqual(second['intent']['domain'], 'notes')
        self.assertEqual([(c[0]['frame'], c[0]['concept']['term']) for c in nf.calls], [('medication_event', 'oxycodone'), ('medication_event', 'morphine')])
        self.assertEqual(second['patient_ids'], [RICH])
        self.assertEqual(first['patient_ids'], [RICH])

    def test_a_later_notes_question_runs_the_anchor_first_for_its_date(self):
        ev = [{'record_id': 'N-1', 'date': '2029-09-01', 'note_type': 'progress_note', 'quote': 'Goals of care: comfort-focused.'}]
        nf = FakeNotes({('care_process', 'goals of care'): {'status': 'documented', 'detail': {}, 'evidence': ev, 'p': 0.9}})
        a = CR.ask(self.stack, f'{RICH} had a goals-of-care talk choosing comfort-focused care. Do the later notes still show new disease-directed medications being started?', nf=nf)
        self.assertEqual(nf.calls[0][0]['frame'], 'care_process')
        self.assertEqual(nf.calls[1][0]['window']['start'], '2029-09-02')
        self.assertIn('later notes read from 2029-09-02', a['headline'])


class RealNoteFrames(Base):
    def test_predicates_validate_against_note_frames_and_run_offline(self):
        from clinical_core.agere.clinical import note_frames as NF
        for text in ('What was the reason aspirin was stopped?', 'Did the patient accept statin therapy when it was discussed?',
                     'Do the notes say he has trouble getting to appointments?', 'Is a DNR order or code status documented?',
                     'What was the impression on the outside chest x-ray report?', 'How many falls did she report in the past year?',
                     'Was acute coronary syndrome ruled out during the April 2027 admission, and what did the stress test show?',
                     'What follow-up did the 2027-10-28 discharge summary recommend, and where was the patient discharged to?'):
            for pred in CN.build(text, AS_OF):
                NF.validate_predicate({k: v for k, v in pred.items() if not k.startswith('_')})  # raises ValueError when a field or option is wrong


class Tier(Base):
    def test_single_patient_question_yields_a_graph_and_a_receipt(self):
        graph, receipt = CR.tier(self.stack, f'What is the latest HbA1c for {RICH}?')
        node = graph['nodes'][0]
        self.assertEqual((node['operation'], node['inputs']), ('chart_reader', []))
        self.assertEqual(node['parameters']['patient_id'], RICH)
        self.assertEqual((receipt['route'], receipt['new_paid_api_calls'], receipt['frontier_called']), ('chart_reader', 0, False))
        self.assertEqual(receipt['intent']['domain'], 'lab')
        self.assertIn('chart_reader', receipt['components'])

    def test_declines_what_is_not_a_chart_question_and_clarifies_what_is_ambiguous(self):
        self.assertIsNone(CR.tier(self.stack, 'How many patients have diabetes?'))
        self.assertIsNone(CR.tier(self.stack, f'What is the latest HbA1c for {RICH}?', population=['a', 'b']))
        with self.assertRaises(QuestionNotUnderstood) as e:
            CR.tier(self.stack, 'latest a1c for SYN-AAAAAAAA')
        self.assertIn(RICH2, str(e.exception))
        with self.assertRaises(QuestionNotUnderstood):
            CR.tier(self.stack, 'latest a1c for SYN-12345678-ABC')

    def test_the_engine_runs_the_graph_node_and_states_its_own_headline(self):
        from clinical_core.agere.clinical import analysis_graph, chart_reader_op as OP, engine_suite
        self.assertEqual(engine_suite.MODULES['chart_reader'], 'chart_reader_op')
        graph, _ = CR.tier(self.stack, f'How many ED visits has {RICH} had in the past year?')
        analysis_graph.validate(graph, AS_OF)
        params = OP.validate(graph['nodes'][0]['parameters'])
        report = OP.execute(self.stack, None, params, AS_OF)
        answer = OP.answer(report, graph, graph['interpretation'])
        self.assertEqual((answer['answer_type'], answer['count'], answer['status']), ('count', 2, 'answered'))
        self.assertEqual(analysis_graph.headline(answer), answer['chart_headline'])
        self.assertIn('2 ED visits', answer['chart_headline'])
        yes = OP.answer(OP.execute(self.stack, None, OP.validate({'patient_id': RICH, 'question': f'Is {RICH} on a statin?'}), AS_OF), graph, 'q')
        self.assertEqual((yes['count'], yes['answer_type']), (1, 'table'))
        self.assertTrue(yes['value'].startswith('Yes'))
        for bad in ({'patient_id': 'nope', 'question': 'x'}, {'patient_id': RICH, 'question': ''}, {'patient_id': RICH, 'question': 'x', 'extra': 1}):
            with self.assertRaises(ValueError):
                OP.validate(bad)

    def test_conversation_summaries_carry_the_subject(self):
        from clinical_core.agere.clinical import conversation
        a = CR.ask(self.stack, f'latest HbA1c for {RICH}')
        plan, summary = conversation.summaries({'answer': a})
        self.assertEqual(summary['subject']['patient_id'], RICH)


if __name__ == '__main__':
    unittest.main()


class Admission(unittest.TestCase):
    """The chart reader's graph carries its typed request, so the literal check reads what was understood (2026-10-23)."""
    def test_typed_chart_carries_concept_ordering_and_flags(self):
        from clinical_core.agere.clinical.chart_reader import typed_chart
        t = typed_chart({'intent': 'latest_value', 'domain': 'lab', 'mode': 'latest', 'concept': {'kind': 'lab', 'value': 'hba1c', 'label': 'HbA1c'},
                         'flags': ['not_filled']})
        self.assertEqual(t['order_by'], [{'field': 'event_date', 'direction': 'desc'}])
        self.assertEqual((t['limit'], t['concept']['label'], t['flags']), (1, 'HbA1c', ['not_filled']))
        self.assertEqual(typed_chart({'domain': 'readmission', 'intent': 'status'})['measure']['within_days'], 30)

    def test_patient_id_digits_are_not_thresholds(self):
        from clinical_core.agere.clinical import requirement_ledger as RL
        q = 'What is the latest LDL for SYN-69C872CB-6B9?'
        self.assertFalse(any('number' in g for g in RL.gaps(q, '"patient_id": "SYN-69C872CB-6B9" "concept": "ldl" latest order_by event_date desc limit 1')))


class Followups(unittest.TestCase):
    def test_elliptical_follow_up_binds_the_prior_patient(self):
        from clinical_core.agere.clinical import patient_ids as PI
        for q in ('and the one before that?', 'what about the LDL?', 'same for last year'):
            self.assertTrue(PI.is_elliptical(q), q)
        for q in ('How many patients have diabetes?', 'and how many of all patients have diabetes in the population?'):
            self.assertFalse(PI.is_elliptical(q), q)


class PlaceholderPlan(unittest.TestCase):
    def test_empty_by_construction_is_declined(self):
        from clinical_core.agere.clinical import answer_checks as A
        v = {'accept': True, 'reasons': []}
        g = {'unmet_requirements': ['Please restate the full question.'], 'nodes': [{'parameters': {'query': 'SELECT patient_id FROM patient WHERE 1=0'}}]}
        self.assertEqual(A._placeholder(g, v), 'placeholder')
        self.assertFalse(v['accept'])
        self.assertIsNone(A._placeholder({'nodes': [{'parameters': {'query': 'SELECT 1 FROM patient WHERE age > 10'}}]}, {'accept': True, 'reasons': []}))
