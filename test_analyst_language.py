"""Analyst wording (2026-10-24, independent dev set): measure names and their restating words, age-group nouns, deaths in a year, outcome verbs,
period wording shared across alternatives, named place-of-service claims, active coverage, a positive depression screen, event shares ("followed by
... within 7 days"), spelling, and the insight shapes (readmission drivers, concentration, evidence gaps ...). Each question is read by the free
grammar or the engine forms, compiled by the shared compiler and executed on a small warehouse whose answers are worked out by hand in the comments."""
import sqlite3
import unittest

from clinical_core.agere.clinical import request_plan as R, request_language as L, request_vocabulary as V, sql_query, phrasing, population_engines

from test_composition_language import CompositionFixture


class AnalystFixture(CompositionFixture):
    def setUp(self):
        super().setUp()
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
            INSERT INTO patient VALUES ('K','2020-01-01','F','medicaid','WH-PRACTICE-1','deep_chart');
            INSERT INTO condition VALUES ('K','asthma','J45.9',1,'2028-01-01');
            INSERT INTO payer_claim VALUES ('E','P1','in_network',5000,1,'2029-05-01','paid','professional','20'),
                                           ('E','P1','in_network',4000,1,'2029-05-02','reversed','professional','11'),
                                           ('E','P1','in_network',3000,1,'2028-05-02','recouped','professional','11');
            CREATE TABLE enrollment_span(patient_id TEXT, coverage_start TEXT, coverage_end TEXT);
            INSERT INTO enrollment_span VALUES ('A','2020-01-01',NULL), ('B','2020-01-01','2029-12-31'), ('C','2020-01-01','2031-01-01'), ('D','2031-06-01',NULL);
            CREATE TABLE measurement_latest(patient_id TEXT, concept TEXT, value_canonical REAL, event_date TEXT, record_id TEXT);
            INSERT INTO measurement_latest VALUES ('A','phq9',12,'2029-10-01','r1'), ('B','phq9',4,'2029-10-01','r2'), ('C','phq9',10,'2029-10-01','r3');
            CREATE TABLE immunization(patient_id TEXT, vaccine TEXT, status TEXT, event_date TEXT);
            CREATE TABLE visit(patient_id TEXT, type_concept TEXT, event_date TEXT, encounter_id TEXT);
            INSERT INTO visit VALUES ('A','primary_care_visit','2029-08-05','e1'), ('B','primary_care_visit','2029-04-20','e2'), ('D','telehealth_contact','2029-07-02','e3');
            ''')
        self.catalog = R.Catalog.load(self.path)


class AgeGroupsDeathsAndOutcomes(AnalystFixture):
    # living patients A B C D E F G K (H is deceased on 2029-12-01); K is born 2020
    def test_died_in_a_year_reads_the_death_record_by_its_own_date(self):
        self.assertEqual(self.count('How many patients died in 2029?'), 1)   # H
        self.assertEqual(self.count('How many patients died in 2028?'), 0)
        self.assertEqual(self.count('How many patients have died?'), 1)

    def test_children_are_the_patients_under_eighteen(self):
        self.assertEqual(self.count('How many children carry an asthma dx?'), 1)  # K
        self.assertEqual(self.count('How many kids have asthma?'), 1)

    def test_sixty_five_and_up_is_an_age_floor(self):
        self.assertEqual(self.count('How many patients are 65 and up?'), self.count('How many patients are 65 or older?'))

    def test_ended_as_is_the_status_verb(self):
        self.assertEqual(self.count('How many appointments ended as no-shows in 2029?'), 3)  # 1, 2, 5

    def test_a_period_after_two_alternatives_covers_both(self):
        # counted claims dated 2029 whose final status is denied or reversed: B denied (2029-09-01), E reversed (2029-05-02)
        self.assertEqual(self.count('How many claims were denied or reversed for dates of service in 2029?'), 2)
        self.assertEqual(self.count('How many claims were reversed or recouped in 2028?'), 1)

    def test_a_named_place_of_service_selects_its_claims(self):
        self.assertEqual(self.count('How many urgent care claims (place of service 20) did we have in 2029?'), 1)
        self.assertEqual(self.count('How many urgent care claims did we have in 2029?'), 1)

    def test_active_coverage_is_an_open_span_on_the_as_of_date(self):
        self.assertEqual(self.count("How many members have active coverage as of today's data date?"), 2)  # A (open), C (ends 2031); B ended, D starts later

    def test_positive_depression_screen_is_the_published_cut_off(self):
        self.assertEqual(self.count('How many patients have a positive depression screen (PHQ-9 of 10+) on their latest result?'), 2)  # A 12, C 10
        self.declines('How many patients have a positive depression screen (PHQ-9 of 15+) on their latest result?')  # a different cut-off is a different question

    def test_event_share_followed_by_a_visit_within_days(self):
        # 2029 ED visits: A 08-01 (office visit 08-05), B 03-01 (visit 04-20: too late), D 07-01 (telehealth 07-02, not an office visit)
        value = self.run_q('What share of 2029 ED visits had an office visit within 7 days?')
        self.assertAlmostEqual(value, 100 / 3, places=1)
        value = self.run_q('What share of 2029 ED visits were followed by an office, primary care, specialty or telehealth visit within 7 days?')
        self.assertAlmostEqual(value, 200 / 3, places=1)  # A and D

    def test_a_measure_for_each_named_group(self):
        rows = self.run_q('Average SVI for commercial, Medicare and Medicaid patients')
        by = {r['payer_type']: round(r['avg_svi_overall'], 1) for r in rows}
        self.assertEqual(by, {'commercial': 15.0, 'medicare': 78.8, 'medicaid': 76.0})  # B E / A C F G H / D
        self.declines('Average SVI for commercial, Medicare and Frobnicated patients')

    def test_the_second_group_may_be_named_alone(self):
        rows = self.run_q('What share of brand pharmacy claims were rejected in 2029, versus generic?')
        by = {r['formulary_tier']: round(r['percent'], 1) for r in rows}
        self.assertEqual(by, {'brand': 50.0, 'generic': 33.3})  # brand: C rejected, A paid; generic: B rejected, A and B paid

    def test_the_decision_context_reads_patient_attribute_summaries_as_patient_grain(self):
        from clinical_core.agere.clinical import decision_context as DC
        self.assertTrue(DC.patient_grain('SELECT AVG(s.svi_overall) FROM patient_svi s JOIN patient p ON p.patient_id = s.patient_id'))
        self.assertTrue(DC.patient_grain('SELECT AVG(age) FROM patient'))
        self.assertFalse(DC.patient_grain('SELECT SUM(c.paid_cents) FROM payer_claim c JOIN patient p ON p.patient_id = c.patient_id'))

    def test_followed_by_is_not_a_grouping(self):
        self.assertEqual(self.count('How many 2029 ED visits were followed by a primary care visit within 7 days?'), 1)


class MeasureWording(AnalystFixture):
    def plan(self, q):
        return L.parse(self.catalog, q)

    def metric(self, q):
        return self.plan(q)['analyses'][0]['parameters']['id']

    def test_library_aliases_name_the_governed_definitions(self):
        names = V.aliases()
        self.assertEqual(names['statin therapy for patients with diabetes'], 'measure.spd_statin_diabetes')
        self.assertEqual(names['pqi 90'], 'measure.pqi_90_overall_composite')
        self.assertEqual(names['pdc for statins'], 'measure.pdc_statins')
        self.assertEqual(self.metric('What was our kidney health evaluation rate in 2029?'), 'kidney_health_evaluation_diabetes')
        self.assertEqual(self.metric('Statin therapy for patients with diabetes rate in 2029'), 'measure.spd_statin_diabetes')
        self.assertEqual(self.metric('How are we doing on colorectal cancer screening in 2029?'), 'measure.col')

    def test_measure_kind_and_own_population_words_are_consumed(self):
        for q in ('Kidney health evaluation in diabetes in 2029', 'What share of diabetics got a kidney health evaluation in 2029?'):
            self.assertEqual(self.metric(q), 'kidney_health_evaluation_diabetes')

    def test_a_restated_definition_is_consumed_only_when_the_definition_states_it(self):
        q = 'Kidney health evaluation in diabetes: share of diabetics 18 to 85 who got BOTH an eGFR and a urine albumin-creatinine ratio in 2029'
        self.assertEqual(self.metric(q), 'kidney_health_evaluation_diabetes')
        R.compile_request(self.catalog, q, self.plan(q))
        for wrong in ('Kidney health evaluation in diabetes: share of diabetics 18 to 65 who got BOTH an eGFR and a urine albumin-creatinine ratio in 2029',
                      'HbA1c poor control: share of diabetics 18-75 whose most recent 2029 A1c is under 8'):
            with self.assertRaises(R.PlanError):
                self.plan(wrong)

    def test_a_window_the_definition_states_is_consumed(self):
        self.assertEqual(self.metric('7-day follow-up after hospital discharge in 2029'), 'post_discharge_followup_7d')
        with self.assertRaises(R.PlanError):
            self.plan('Follow-up after ED visit within 30 days in 2029')

    def test_numbers_of_the_definition_are_carried_by_its_digest(self):
        q = 'Kidney health evaluation in diabetes: share of diabetics 18 to 85 who got BOTH an eGFR and a urine albumin-creatinine ratio in 2029'
        R.compile_request(self.catalog, q, self.plan(q))   # 18 and 85 are the measure's own age band
        plan = self.plan(q)
        plan['analyses'][0]['quote'] = [*plan['analyses'][0]['quote']]
        self.assertEqual(V.gloss_unaccounted('18 to 65', V.definitions()['kidney_health_evaluation_diabetes']), ['65'])

    def test_period_bounded_lab_reads_ignore_the_latest_result_rule(self):
        sql = V.period_lab_reads("SELECT 1 FROM p WHERE EXISTS (SELECT 1 FROM measurement m WHERE m.patient_id = p.patient_id AND m.concept = 'egfr' AND m.event_date BETWEEN '2029-01-01' AND '2029-12-31')")
        self.assertIn('lab_rule_result.patient_id', sql)
        self.assertNotIn(' m.', sql)
        untouched = "SELECT 1 FROM measurement m WHERE m.concept = 'egfr'"
        self.assertEqual(V.period_lab_reads(untouched), untouched)


class SpellingAndPresence(unittest.TestCase):
    def test_one_edit_neighbours(self):
        self.assertTrue(phrasing._one_edit('admisions', 'admissions'))
        self.assertTrue(phrasing._one_edit('pateints', 'patients'))
        self.assertFalse(phrasing._one_edit('patients', 'patients'))
        self.assertFalse(phrasing._one_edit('patients', 'practices'))

    def test_a_misspelt_domain_word_is_corrected_and_real_words_are_not(self):
        self.assertEqual(phrasing.fix_spelling('Number of pateints with two or more inpatient admisions in 2029'),
                         'Number of patients with two or more inpatient admissions in 2029')
        self.assertEqual(phrasing.fix_spelling('How many patients with a transportation barrier'), 'How many patients with a transportation barrier')
        self.assertEqual(phrasing.fix_spelling('Which cohort followed the protocol'), 'Which cohort followed the protocol')

    def test_lab_presence_in_a_period_is_not_a_threshold(self):
        window = {'op': 'and', 'args': [{'field': 'measurement.event_date', 'op': '>=', 'value': '2029-01-01', 'quote': 'x'},
                                        {'field': 'measurement.event_date', 'op': '<', 'value': '2030-01-01', 'quote': 'x'}], 'quote': 'x'}
        present = {'op': 'and', 'args': [{'field': 'measurement.concept', 'op': '=', 'value': 'egfr', 'quote': 'x'},
                                         {'field': 'measurement.value_canonical', 'op': 'not_null', 'value': None, 'quote': 'x'}, window], 'quote': 'x'}
        self.assertTrue(R.lab_presence_in_period(present))
        threshold = {'op': 'and', 'args': [*present['args'], {'field': 'measurement.value_canonical', 'op': '>', 'value': 9, 'quote': 'x'}], 'quote': 'x'}
        self.assertFalse(R.lab_presence_in_period(threshold))
        self.assertFalse(R.lab_presence_in_period({'field': 'measurement.concept', 'op': '=', 'value': 'egfr', 'quote': 'x'}))


class InsightShapes(AnalystFixture):
    def family(self, q):
        got = population_engines.interpret(self.catalog, q)
        return got[1]['family'] if got else None

    def test_shapes_are_recognised(self):
        self.assertEqual(self.family("What's behind our readmission rate? Where are the 30-day bounce-backs coming from?"), 'readmission_drivers')
        self.assertEqual(self.family('Is our ED volume driven by a small group of patients? How concentrated is it?'), 'use_concentration')
        self.assertEqual(self.family('Why did hospital admissions jump in 2029 compared with 2028?'), 'yoy_trend')
        self.assertEqual(self.family('Which month of 2029 was our most expensive for medical claims, and which was the cheapest?'), 'period_series')
        self.assertEqual(self.family('Where are claims getting denied — which place of service has the worst denial rate in 2029?'), 'outcome_rates')
        self.assertEqual(self.family("Which payer's pharmacy claims bounce the most, and what's the usual reason?"), 'outcome_rates')
        self.assertEqual(self.family('Do patients with a transportation barrier actually miss more visits than everyone else?'), 'need_gap')
        self.assertEqual(self.family('What did our ten most expensive members cost in total for 2029?'), 'top_spenders')
        self.assertEqual(self.family('How much of our 2029 medical spend is hospital inpatient, and what does an average inpatient claim run us?'), 'spend_mix')
        self.assertEqual(self.family("Is anyone lagging on flu shots — break it out commercial, Medicare and Medicaid?"), 'evidence_gap')
        self.assertEqual(self.family('How much more do patients with three or more chronic conditions cost than the rest (diabetes, hypertension, CKD, heart failure, COPD)?'), 'comorbidity_cost')

    def test_group_comparison_condition_and_segment_shapes(self):
        self.assertEqual(self.family('Do patients who screened positive for food insecurity use the ED more than everyone else?'), 'group_vs_rest')
        self.assertEqual(self.family('Do diabetics get admitted more than everyone else?'), 'group_vs_rest')
        self.assertEqual(self.family('Which chronic conditions account for the most of our ED visits in the last year?'), 'condition_drivers')
        self.assertEqual(self.family('Is 30-day readmission a bigger problem for our Medicare patients than for commercial in 2029?'), 'segment_readmission')
        self.assertIsNone(self.family('Is 30-day readmission a bigger problem for our Medicare patients than for the moon in 2029?'))

    def test_a_rising_risk_label_alone_is_not_guessed(self):
        self.assertIsNone(self.family("Who's rising risk here?"))
        self.assertEqual(self.family("Who's rising risk here? I mean two or more ED visits in the last year but no hospital admission yet — how many, and what do they cost?"), 'cohort_defined')

    def test_a_number_the_form_does_not_compile_declines_it(self):
        self.assertIsNone(self.family("What's behind our 90-day readmission rate?"))   # the readmission flag is the 30-day window
        self.assertIsNone(self.family('What did our ten most expensive members cost in total for 2029 among women?'))

    def test_a_stated_direction_is_checked_against_the_numbers(self):
        from clinical_core.agere.clinical import insight_families as F
        spec = F.YoyTrend.parse('Why did hospital admissions jump in 2029 compared with 2028?', self.catalog)
        self.assertEqual((spec['params']['y0'], spec['params']['y1'], spec['params']['asserted']), (2028, 2029, 'up'))
        self.assertIsNone(F.YoyTrend.parse('Why did hospital admissions look odd in 2029 compared with 2028?', self.catalog))


if __name__ == '__main__':
    unittest.main()
