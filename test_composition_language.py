"""Composition constructs of the shared spec (2026-10-24): condition lists, negation, exclusive-or, k-of-n, neither/nor, event chains, shared
scope, grouping with named categories, cohort groups, rates per patient, rankings in both directions, contrasts, claims and pharmacy measure
clauses, area vulnerability, missed appointments. Each question is read by the free grammar, compiled by the shared compiler and executed on a
small warehouse whose answers are worked out by hand in the comments."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import request_plan as R, request_language as L, sql_query, spec_readback

AS_OF = '2030-05-27'


class CompositionFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript(f'''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','{AS_OF}');
            CREATE TABLE patient(patient_id TEXT, birth_date TEXT, sex_reported TEXT, coverage_type TEXT, pcp_practice_id TEXT, data_segment TEXT);
            INSERT INTO patient VALUES
              ('A','1950-01-01','F','medicare','WH-PRACTICE-0','deep_chart'), ('B','1990-01-01','M','commercial','WH-PRACTICE-0','deep_chart'),
              ('C','1960-01-01','F','dual','WH-PRACTICE-1','deep_chart'),    ('D','1970-01-01','M','medicaid','WH-PRACTICE-1','deep_chart'),
              ('E','2000-01-01','F','commercial','WH-PRACTICE-1','deep_chart'), ('F','1985-01-01','M','medigap','WH-PRACTICE-0','deep_chart'),
              ('G','1955-01-01','F','medicare','WH-PRACTICE-0','deep_chart'),  ('H','1940-01-01','M','medicare','WH-PRACTICE-0','deep_chart');
            CREATE TABLE death(patient_id TEXT, death_date TEXT);
            INSERT INTO death VALUES ('H','2029-12-01');
            CREATE TABLE condition(patient_id TEXT, concept TEXT, icd10 TEXT, is_diagnosis INTEGER, event_date TEXT);
            INSERT INTO condition VALUES
              ('A','copd','J44.9',1,'2028-01-01'), ('A','heart_failure','I50.9',1,'2028-01-01'), ('B','copd','J44.9',1,'2028-01-01'),
              ('C','heart_failure','I50.9',1,'2028-01-01'), ('C','chronic_kidney_disease','N18.3',1,'2028-01-01'),
              ('D','diabetes','E11.9',1,'2028-01-01'), ('D','hypertension','I10',1,'2028-01-01'), ('D','chronic_kidney_disease','N18.3',1,'2028-01-01'),
              ('E','hypertension','I10',1,'2028-01-01'), ('F','asthma','J45.9',1,'2028-01-01'),
              ('G','asthma','J45.9',1,'2028-01-01'), ('G','copd','J44.9',1,'2028-01-01'), ('H','copd','J44.9',1,'2028-01-01'),
              ('B','diabetes','E11.9',1,'2028-01-01'), ('A','diabetes','E11.9',1,'2028-01-01');
            CREATE TABLE medication(patient_id TEXT, drug_class TEXT, evidence TEXT, status TEXT, fill_reversed INTEGER, event_date TEXT);
            INSERT INTO medication VALUES
              ('A','statin','dispensed','dispensed',0,'2029-10-01'), ('B','insulin','dispensed','dispensed',0,'2029-10-01'),
              ('D','antiplatelet','dispensed','dispensed',0,'2029-10-01'), ('A','glp1_agonist','dispensed','dispensed',0,'2029-12-01');
            CREATE TABLE utilization_event(patient_id TEXT, kind TEXT, event_date TEXT, sources TEXT, in_ehr INTEGER);
            INSERT INTO utilization_event VALUES
              ('A','ed_visit','2029-08-01','adt',1), ('A','ed_visit','2030-01-01','adt',1), ('B','ed_visit','2029-03-01','adt',1),
              ('B','ed_visit','2030-02-01','adt',1), ('C','inpatient','2029-09-01','adt',1), ('D','ed_visit','2029-07-01','adt',1),
              ('C','ed_visit','2030-03-01','adt',1), ('D','inpatient','2030-01-15','adt',1);
            CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT, discharge_known INTEGER);
            INSERT INTO stay VALUES ('C','2029-09-01','2029-09-05','inpatient',1), ('D','2030-01-15','2030-01-18','inpatient',1);
            CREATE TABLE payer_claim(patient_id TEXT, payer_id TEXT, network_status TEXT, paid_cents INTEGER, counts_in_metrics INTEGER,
                                     service_from TEXT, final_status TEXT, claim_type TEXT, place_of_service TEXT);
            INSERT INTO payer_claim VALUES
              ('A','P1','in_network',10000,1,'2029-06-01','paid','professional','11'), ('A','P1','out_of_network',30000,1,'2029-07-01','paid','professional','23'),
              ('B','P2','in_network',20000,1,'2029-08-01','paid','institutional',NULL), ('C','P2','out_of_network',50000,1,'2028-08-01','paid','professional','11'),
              ('B','P2','in_network',0,1,'2029-09-01','denied','professional','11'), ('D','P1','in_network',70000,0,'2029-09-01','paid','professional','11');
            CREATE TABLE payer_pharmacy_claim(patient_id TEXT, pharmacy_type TEXT, claim_status TEXT, counts_as_fill INTEGER, plan_paid_cents INTEGER,
                                              formulary_tier TEXT, fill_date TEXT, days_supply INTEGER);
            INSERT INTO payer_pharmacy_claim VALUES
              ('A','retail_pharmacy','paid',1,2000,'generic','2029-10-01',30), ('A','mail_order_pharmacy','paid',1,90000,'brand','2029-11-01',90),
              ('B','retail_pharmacy','rejected',0,0,'generic','2029-10-05',30), ('B','retail_pharmacy','paid',1,3000,'generic','2029-12-05',30),
              ('C','mail_order_pharmacy','rejected',0,0,'brand','2029-10-07',30);
            CREATE TABLE patient_svi(patient_id TEXT, svi_overall REAL);
            INSERT INTO patient_svi VALUES ('A',80),('B',10),('C',90),('D',76),('E',20),('F',50),('G',75),('H',99);
            CREATE TABLE social_need(patient_id TEXT, need TEXT, positive INTEGER, event_date TEXT);
            INSERT INTO social_need VALUES ('A','food',1,'2029-10-01'),('A','transport',1,'2029-10-01'),('B','food',1,'2029-10-01'),('C','food',0,'2029-10-01'),
              ('D','transport',1,'2029-10-01'),('D','food',1,'2029-11-01'),('D','housing',1,'2029-11-01');
            CREATE TABLE appointment(appointment_id TEXT, patient_id TEXT, scheduled_date TEXT, final_status TEXT);
            INSERT INTO appointment VALUES ('1','A','2029-10-01','no_show'),('2','A','2029-11-01','no_show'),('3','B','2029-10-01','fulfilled'),
              ('4','D','2027-10-01','no_show'),('5','D','2029-12-01','no_show');
            ''')
        self.catalog = R.Catalog.load(self.path)

    def contract(self, q):
        return R.compile_request(self.catalog, q, L.parse(self.catalog, q, bound=False))

    def run_q(self, q):
        c = self.contract(q)
        r = sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.catalog.target)
        return r.get('value', r.get('rows'))

    def count(self, q):
        v = self.run_q(q)
        return v if not isinstance(v, list) else v[0][next(iter(v[0]))]

    def declines(self, q):
        with self.assertRaises(R.PlanError):
            self.contract(q)


class ConditionComposition(CompositionFixture):
    # living patients: A B C D E F G (H is deceased). copd: A B G; heart failure: A C; ckd: C D; diabetes: A B D; hypertension: D E; asthma: F G
    def test_both_a_and_b(self):
        self.assertEqual(self.count('How many patients carry both a COPD and a heart failure diagnosis?'), 1)  # A

    def test_a_but_not_b(self):
        self.assertEqual(self.count('Number of hypertensive patients who have no diabetes?'), 1)  # E

    def test_either_one_with_a_parenthetical(self):
        self.assertEqual(self.count('Count the patients that have copd or heart failure (either one).'), 4)  # A B C G

    def test_comma_list_all_at_once(self):
        self.assertEqual(self.count('How many of our patients have diabetes, hypertension and chronic kidney disease all at once?'), 1)  # D

    def test_exclusive_or(self):
        self.assertEqual(self.count('How many patients have asthma or COPD but not both?'), 3)  # F (asthma), A B (copd); G has both

    def test_who_has_and_is_not_flagged(self):
        c = self.contract('Who has heart failure and copd but is not flagged with kidney disease?')
        ids = sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.catalog.target)['patient_ids']
        self.assertEqual(ids, ['A'])  # heart failure A C, copd A B G, ckd C D

    def test_at_least_two_of_three(self):
        # A has diabetes and heart failure, C has CKD and heart failure, D has diabetes and CKD
        self.assertEqual(self.count('How many patients have at least two of these three: diabetes, CKD, heart failure?'), 3)

    def test_neither_nor_conditions(self):
        self.assertEqual(self.count('Among patients with copd, how many have neither diabetes nor hypertension on record?'), 1)  # G (A and B have diabetes)

    def test_neither_nor_medications(self):
        self.assertEqual(self.count('How many patients with diabetes are on neither a statin nor an antiplatelet?'), 1)  # B (A statin, D antiplatelet)

    def test_a_threshold_k_of_n_op_compiles_and_reads_back(self):
        plan = L.parse(self.catalog, 'How many patients have at least two of these three: diabetes, CKD, heart failure?')
        self.assertEqual(plan['where']['op'], 'at_least_of')
        self.assertIn('at least 2 of', spec_readback.predicate_text(plan['where']))

    def test_at_least_of_rejects_a_threshold_above_its_operands(self):
        plan = L.parse(self.catalog, 'How many patients have at least two of these three: diabetes, CKD, heart failure?')
        plan['where']['n'] = 4
        with self.assertRaises(R.PlanError):
            R.compile_request(self.catalog, 'How many patients have at least two of these three: diabetes, CKD, heart failure?', plan)

    def test_unrecognised_wording_is_declined(self):
        self.declines('How many patients carry both a COPD and a frobnicated diagnosis?')


class EventComposition(CompositionFixture):
    # ED visits in the rolling year (2029-05-28 .. 2030-05-27): A 2, B 1 (2030-02-01), C 1, D 1; B 2029-03-01 is outside. inpatient: C 2029-09-01, D 2030-01-15
    def test_distinct_patients_at_least_once(self):
        self.assertEqual(self.count('How many distinct patients went to the ED at least once in calendar year 2029?'), 3)  # A B D

    def test_both_events_share_one_window(self):
        self.assertEqual(self.count('How many patients had both an ED visit and an inpatient admission in the past year?'), 2)  # C D

    def test_event_in_one_window_but_not_at_all_in_another(self):
        self.assertEqual(self.count('How many patients visited the ED in 2029 but not at all in the last 12 months?'), 0)  # B 2029-03 but 2030-02 too

    def test_total_events_counts_rows(self):
        self.assertEqual(self.count('Total ED visits among our patients over the past 12 months?'), 5)

    def test_shared_scope_in_a_program(self):
        program = L.parse_program(self.catalog, 'For diabetic patients, how many have a copd diagnosis, and how many have a heart failure diagnosis?')
        self.assertEqual(len(program['nodes']), 2)
        self.assertEqual(program['nodes'][1]['quote'][0], 'For diabetic patients,')

    def test_shared_trailing_period_in_a_program(self):
        program = L.parse_program(self.catalog, 'How many ED visits and how many inpatient admissions in 2029?')
        self.assertEqual(program['nodes'][0]['quote'], ['How many ED visits', 'in 2029'])


class GroupingComposition(CompositionFixture):
    def test_by_a_column_with_for_each(self):
        self.assertEqual(self.run_q('How many diabetic patients are there for each sex?'),
                         [{'sex_reported': 'F', 'patients': 1}, {'sex_reported': 'M', 'patients': 2}])

    def test_age_bands_named_in_the_question(self):
        rows = self.run_q('Break hypertensive patients out by age: under 40, 40 to 64, and 65 and older.')
        self.assertEqual({r['age_band']: r['patients'] for r in rows}, {'under_40': 1, '40_to_64': 1})  # E (30), D (60); nobody 65+

    def test_payer_groups_merge_dual_and_medigap_into_medicare(self):
        rows = self.run_q('Patients by payer type - commercial, Medicare (including dual and Medigap) and Medicaid.')
        self.assertEqual({r['payer_type']: r['patients'] for r in rows}, {'commercial': 2, 'medicare': 4, 'medicaid': 1})  # B E | A C F G | D

    def test_a_medicare_note_that_names_only_one_is_declined(self):
        self.declines('Patients by payer type - commercial, Medicare (including dual) and Medicaid.')

    def test_fronted_grouping(self):
        rows = self.run_q('For each primary-care practice, how many patients had an ED visit in the last 12 months?')
        key = next(k for k in rows[0] if 'practice' in k)
        self.assertEqual({r[key]: r['patients'] for r in rows}, {'WH-PRACTICE-0': 2, 'WH-PRACTICE-1': 2})  # A B | C D

    def test_a_cohort_group_overlaps(self):
        c = self.contract('How do ED visits per 1,000 patients over the last year compare between COPD patients and heart failure patients?')
        rows = sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.catalog.target)['rows']
        by = {x['group']: x for x in rows}
        self.assertEqual(by['copd']['patients'], 3)   # A B G; patient A is also in heart failure
        self.assertEqual(by['heart_failure']['patients'], 2)  # A C
        self.assertAlmostEqual(by['copd']['rate'], 1000 * 3 / 3, 1)  # A 2 + B 1 + G 0
        self.assertAlmostEqual(by['heart_failure']['rate'], 1000 * 3 / 2, 1)  # A 2 + C 1

    def test_cohort_group_needs_two_to_twelve_groups(self):
        q = 'Break hypertensive patients out by age: under 40, 40 to 64, and 65 and older.'
        plan = L.parse(self.catalog, q)
        plan['select'][0]['groups'] = plan['select'][0]['groups'][:1]
        with self.assertRaises(R.PlanError): R.compile_request(self.catalog, q, plan)

    def test_cohort_group_reads_back(self):
        plan = L.parse(self.catalog, 'Break hypertensive patients out by age: under 40, 40 to 64, and 65 and older.')
        self.assertIn('under_40', spec_readback.measure_text(plan['select'][0], plan))


class RateAndRank(CompositionFixture):
    def test_rate_per_thousand_counts_patients_without_events(self):
        v = self.run_q('ED visits per 1,000 patients over the last 12 months')
        self.assertAlmostEqual(v, 1000 * 5 / 7, 1)  # 5 ED visits, 7 living patients

    def test_top_n_by_a_rate(self):
        rows = self.run_q('Top two practices by ED visits per 1,000 patients over the last 12 months (patients = living patients of the practice).')
        key = next(k for k in rows[0] if 'practice' in k)
        # practice 0: A B F G (4 patients, 3 visits); practice 1: C D E (3 patients, 2 visits)
        self.assertEqual([r[key] for r in rows], ['WH-PRACTICE-0', 'WH-PRACTICE-1'])
        self.assertAlmostEqual(rows[0]['rate'], 1000 * 3 / 4, 1)

    def test_both_directions(self):
        key = lambda rows: next(k for k in rows[0] if 'practice' in k)
        most = self.run_q('Which practice has the most diabetic patients?')
        fewest = self.run_q('And which practice has the fewest diabetic patients?')
        self.assertEqual(most[0][key(most)], 'WH-PRACTICE-0')  # A B vs D
        self.assertEqual(fewest[0][key(fewest)], 'WH-PRACTICE-1')


class ContrastComposition(CompositionFixture):
    def test_trailing_versus_on_claims(self):
        rows = self.run_q("What's the average paid amount per medical claim in 2029, out of network versus in network?")
        got = {r['network_status']: r['avg_paid_cents'] for r in rows}
        self.assertEqual(got, {'in_network': 15000, 'out_of_network': 30000})  # paid claims only, counted rows: in (10000, 20000), out (30000)

    def test_compare_a_rate_between_pharmacy_types(self):
        rows = self.run_q('Compare the rejected-claim rate for mail-order versus retail pharmacies in 2029.')
        got = {r['pharmacy_type']: r['percent'] for r in rows}
        self.assertEqual(got, {'mail_order_pharmacy': 50.0, 'retail_pharmacy': 33.33})  # mail 1 paid + 1 rejected; retail 2 paid + 1 rejected

    def test_brand_versus_generic_on_average(self):
        rows = self.run_q('How much does the plan pay per fill on brand drugs versus generics in 2029 on average?')
        self.assertEqual({r['formulary_tier']: r['avg_plan_paid_cents'] for r in rows}, {'brand': 90000, 'generic': 2500})

    def test_group_share_for_named_groups(self):
        rows = self.run_q('What share of commercial, Medicare and Medicaid patients live in high-SVI (75+) areas?')
        got = {r['group']: r['percent'] for r in rows}
        self.assertEqual(got, {'commercial': 0.0, 'medicare': 75.0, 'medicaid': 100.0})  # B E | A C F G (80, 90, 50, 75) | D (76)

    def test_a_contrast_with_a_word_nothing_reads_is_declined(self):
        self.declines('Compare the rejected-claim rate for mail-order versus retail pharmacies in 2029 with frobs.')


class ClaimsComposition(CompositionFixture):
    def test_money_over_a_rolling_window(self):
        self.assertEqual(self.run_q('How much have we paid on medical claims over the last 12 months?'), 60000)  # 10000 + 30000 + 20000; the denied 0; D is uncounted

    def test_two_years_group_by_year(self):
        rows = self.run_q('What was total medical paid in 2028 and in 2029?')
        self.assertEqual({r['year']: r['paid_cents'] for r in rows}, {'2028': 50000, '2029': 60000})

    def test_paid_fills(self):
        self.assertEqual(self.count('How many pharmacy fills were paid in 2029?'), 3)

    def test_share_of_fills_by_a_condition(self):
        v = self.run_q('What share of fills in the last year came through mail order?')
        self.assertAlmostEqual(v, 100 * 1 / 3, 1)  # 3 fills counted, 1 mail order

    def test_top_payers_by_paid(self):
        rows = self.run_q('Top two payers by 2029 paid amount, with the totals.')
        self.assertEqual([(r['payer_id'], r.get('value', r.get('paid_cents'))) for r in rows], [('P1', 40000), ('P2', 20000)])  # D's claim is not counted


class SocialComposition(CompositionFixture):
    def test_area_vulnerability_with_a_stated_number(self):
        self.assertEqual(self.count('How many diabetics live in high-vulnerability areas (SVI 75 or above)?'), 2)  # A (80), D (76); B 10

    def test_low_area_needs_its_side(self):
        self.declines('How many diabetics live in low-SVI (25) areas?')

    def test_missed_appointments(self):
        self.assertEqual(self.count('How many patients missed at least one appointment in the last year?'), 2)  # A, D (2029-12-01)

    def test_missed_two_or_more(self):
        self.assertEqual(self.count('How many patients missed at least two appointments in the last year?'), 1)  # A

    def test_two_or_more_different_needs(self):
        self.assertEqual(self.count('How many patients have screened positive for two or more different social needs?'), 2)  # A (2), D (3)

    def test_each_need(self):
        rows = self.run_q('How many patients screened positive for each social need?')
        self.assertEqual({r['need']: r['patients'] for r in rows}, {'food': 3, 'transport': 2, 'housing': 1})


if __name__ == '__main__':
    unittest.main()
