"""Counterexamples for four definition defects found by the 2026-10-24 static review (section 3): chart age, chart "on a medication", mixed-lab
share denominators, and ranking direction. Synthetic fixtures only (the chart and population fixtures of the neighbouring test modules); no model."""
import sqlite3
import unittest
from unittest.mock import patch

import test_chart_reader as TCR
import test_population_forms as TPF
from clinical_core.agere.clinical import insight_families as IF

SKEW = 'SYN-AAAAAAAA-AAC'   # born 1990-06-15, but the feed's age_latest says 31


class ChartDefinitions(TCR.Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with sqlite3.connect(TCR.Path(cls.tmp.name) / 'semantic.sqlite3') as db:
            db.execute(f"INSERT INTO patient VALUES ('{SKEW}',31,'F','commercial','active',NULL,NULL,'1990-06-15','White','Not Hispanic','English',0,NULL,'02134','25025','MA',31,'deep_chart')")
            # a list-only statin (never picked up) and a drug that is listed AND filled
            db.execute(f"INSERT INTO medication_list_entry VALUES ('{SKEW}','atorvastatin','Atorvastatin 20 MG','5','active',NULL,1,'2027-01-01','2030-01-01',NULL,'2030-01-01',NULL,0,'mlx1')")
            db.execute(f"INSERT INTO medication_list_entry VALUES ('{SKEW}','lisinopril','lisinopril 10 MG','6','active',NULL,1,'2027-01-01','2030-01-01','2030-04-01','2030-01-01',NULL,0,'mlx2')")
            db.execute(f"INSERT INTO fill VALUES (90,'{SKEW}','2030-04-01',30,NULL,'ace','ace_inhibitor',NULL,NULL,'Lisinopril','dispense','fx1')")

    def ask(self, text, **kw):
        return TCR.CR.ask(self.stack, text.replace('{P}', SKEW), **kw)

    def test_age_is_completed_years_from_the_birth_date_at_the_as_of_date(self):
        # born 1990-06-15, as of 2030-05-27 -> 39 (the 40th birthday is still ahead); age_latest holds 31
        a = self.ask('How old is {P}?')
        self.assertEqual(a['value'], 39)
        self.assertIn('39', a['headline'])
        self.assertNotIn('31', a['headline'])
        self.assertEqual(self.ask('What is the age of {P}?')['value'], 39)

    def test_on_a_medication_needs_a_picked_up_fill_not_just_a_list_entry(self):
        listed_only = self.ask('Is {P} on atorvastatin?')
        self.assertIs(listed_only['value'], False)
        self.assertTrue(listed_only['facts']['on_list'])
        self.assertEqual(listed_only['facts']['fills_12m'], 0)
        self.assertIn('listed or prescribed only', listed_only['headline'])
        self.assertIs(self.ask('Is {P} taking atorvastatin?')['value'], False)
        filled = self.ask('Is {P} on lisinopril?')
        self.assertIs(filled['value'], True)
        self.assertIn('fill', filled['headline'])

    def test_a_question_about_the_list_or_prescribing_is_still_answered_from_the_list(self):
        listed = self.ask('Is atorvastatin listed for {P}?')
        self.assertIs(listed['value'], True)
        self.assertIn('listed as prescribed', listed['headline'])
        self.assertEqual(self.ask('Is atorvastatin on the medication list for {P}?')['facts']['drugs'], ['atorvastatin'])
        names = self.ask('List the active medications for {P}.')['facts']['drugs']
        self.assertIn('atorvastatin', names)


class PopulationDefinitions(TPF.Base):
    def setUp(self):
        super().setUp()
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            # P20: hypertensive adult with a latest systolic of 150 and no diastolic reading at all
            db.execute("INSERT INTO patient VALUES ('P20','1970-01-01','F','commercial','commercial','PR3','deep_chart')")
            db.execute("INSERT INTO condition VALUES ('cx1','P20','hypertension','I10','2028-01-01',1,NULL)")
            db.execute("INSERT INTO measurement VALUES ('mx1','P20','systolic_bp','2030-01-10',150,1,NULL,'labs')")
            db.execute("INSERT INTO measurement_latest VALUES ('P20','systolic_bp','2030-01-10',150,NULL,1,150,'mx1',NULL)")
        self.catalog = TPF.R.Catalog.load(self.path)

    def test_a_share_whose_predicate_is_an_or_of_labs_has_the_population_as_its_denominator(self):
        a = self.answer('What percent of patients with hypertension do not have their blood pressure under control?')
        pop = [p for p in TPF.PATIENTS if TPF.living(p) and TPF.has_condition(p, 'hypertension') and 18 <= TPF.age_at(TPF.PATIENTS[p][0]) <= 85] + ['P20']
        bad = ['P20'] + [p for p in pop if (TPF.latest(p, 'systolic_bp') or 0) >= 140 or (TPF.latest(p, 'diastolic_bp') or 0) >= 90]
        self.assertEqual((a['numerator'], a['denominator']), (len(bad), len(pop)))
        self.assertLessEqual(a['numerator'], a['denominator'])
        self.assertIn('P20', a['patient_ids'])  # counted in the numerator, so it must be in the denominator too

    def test_a_share_of_one_lab_still_counts_only_patients_with_a_latest_result(self):
        pop = [p for p in TPF.PATIENTS if TPF.living(p) and TPF.has_condition(p, 'diabetes') and TPF.age_at(TPF.PATIENTS[p][0]) >= 18 and TPF.latest(p, 'hba1c') is not None]
        a = self.answer('What share of our adult diabetic patients have an A1c above 9 on their latest result?')
        self.assertEqual(a['denominator'], len(pop))

    def test_pmpm_ranking_carries_lowest_and_highest(self):
        high = self.answer('Which payers have the highest PMPM?')
        low = self.answer('Which payers have the lowest PMPM?')
        h, l = [r['pmpm_dollars'] for r in high['table']], [r['pmpm_dollars'] for r in low['table']]
        self.assertGreater(len(h), 1)
        self.assertEqual(h, sorted(h, reverse=True))
        self.assertEqual(l, sorted(l))
        self.assertIn('highest', high['headline'])
        self.assertIn('lowest', low['headline'])
        self.assertIn(f"${low['table'][0]['pmpm_dollars']:,.0f}", low['headline'])

    def test_practice_gap_direction_comes_from_the_question(self):
        self.assertEqual(IF.PracticeGap.parse('Which practices have the biggest diabetes control gap?', None)['params']['direction'], 'worst')
        self.assertEqual(IF.PracticeGap.parse('Which practices have the highest diabetes control?', None)['params']['direction'], 'high')
        self.assertEqual(IF.PracticeGap.parse('Which practices have the lowest diabetes control?', None)['params']['direction'], 'low')
        self.assertEqual(IF.PracticeGap.parse('Which payers have the most ED visits relative to size?', None)['params']['direction'], 'high')
        with patch.object(IF, 'MIN_PRACTICE_PATIENTS', 1):  # the fixture has no practice of fifteen patients
            high = self.answer('Which practices have the highest diabetes control?')
            low = self.answer('Which practices have the lowest diabetes control?')
            gap = self.answer('Which practices have the biggest diabetes control gap?')
        h, l, g = ([r['percent'] for r in x['table']] for x in (high, low, gap))
        self.assertGreater(len(h), 1)
        self.assertEqual(h, sorted(h, reverse=True))   # best control first
        self.assertEqual(l, sorted(l))                 # lowest value first
        self.assertEqual(g, sorted(g))                 # a "gap" is worst first: lowest control
        self.assertIn('highest on', high['headline'])
        self.assertIn('lowest on', low['headline'])
        self.assertIn('worst on', gap['headline'])


if __name__ == '__main__':
    unittest.main()
