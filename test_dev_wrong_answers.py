"""Dev-set wrong answers (2026-10-24): four root causes, each pinned on a small synthetic warehouse (the test_population_forms fixture plus a few rows).

  1. a care-gap count ("diabetics haven't had an A1c") wrote its own death clause, which made the living filter stand down, so a second chart of
     a person was counted (b3-04, b3-21);
  2. an event relation counted for a population (no-shows by practice) was not restricted to living patients, and a top-N cut at a tie had no
     deterministic order (b3-29);
  3. the engine forms read condition evidence without the measurement table, so the diabetes / CKD lab rule never added anyone (b5-08, b5-24, b5-26);
  4. the preventive-gap labels named an age band the governed measure does not use.
Expected numbers are computed here in plain Python from the rows, never copied from the SQL under test.
"""
import sqlite3
import unittest

from clinical_core.agere.clinical import decision_context as DC
from clinical_core.agere.clinical import request_plan as R
from test_population_forms import APPTS, AS_OF, DEAD, DUP, LABS, PATIENTS, Base, age_at, has_condition, in_win, latest, living


class Extra(Base):
    """Base plus: P11 a second chart of a person (diabetes diagnosis, no A1c ever); P05 (deceased) no-shows at PR2; P06 an A1c of 7.2 with no diabetes
    diagnosis (the lab rule makes P06 a diabetic); PR1 and PR2 end up tied on no-shows."""

    def setUp(self):
        super().setUp()
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.execute("INSERT INTO patient VALUES ('P11','1962-02-02','F','commercial','commercial','PR2','duplicate_chart')")
            db.execute("INSERT INTO condition VALUES ('cx1','P11','diabetes','E11.9','2028-01-01',1,NULL)")
            for i in range(3):
                db.execute("INSERT INTO appointment VALUES ('P05',?, 'no_show','no_show','PR2',5)", (f'2029-1{i}-05',))
            db.execute("INSERT INTO measurement VALUES ('mx1','P06','hba1c','2029-09-09',7.2,1,NULL,'labs')")
            db.execute("INSERT INTO measurement_latest VALUES ('P06','hba1c','2029-09-09',7.2,NULL,1,7.2,'mx1',NULL)")
            for d in ('2030-02-01', '2030-03-01'):  # PR1 now has 3 no-shows, the same as PR2's living 3
                db.execute("INSERT INTO appointment VALUES ('P01',?, 'no_show','no_show','PR1',5)", (d,))
        self.catalog = R.Catalog.load(self.path)


class CareGapCountsEachPersonOnce(Extra):
    def test_a_second_chart_is_not_a_care_gap(self):
        diab = [p for p in PATIENTS if living(p) and has_condition(p, 'diabetes')]
        want = len([p for p in diab if not any(c == 'hba1c' and q == p and in_win(d) for q, c, d, v in LABS)])
        self.assertEqual(self.value("How many diabetics haven't had an A1c in the past year?"), want)  # P11 (duplicate chart, no A1c) is not counted
        a = self.answer("How many diabetics haven't had an A1c in the past year?")
        self.assertNotIn('P11', a['patient_ids'])
        self.assertNotIn('P05', a['patient_ids'])

    def test_the_care_gap_query_states_the_counted_rule_once(self):
        a = self.answer("How many diabetics haven't had an A1c in the past year?")
        sql = a['query_evidence']['query']
        self.assertIn('duplicate_chart', sql)


class EventRowsAreLivingPatientsAndTiesAreStable(Extra):
    def test_no_shows_of_a_deceased_patient_are_not_counted(self):
        a = self.answer('Which practices had the most no-shows in the last year?')
        by = {}
        for p, d, s_, lead in APPTS:
            if s_ == 'no_show' and in_win(d) and living(p): by['PR1' if p in ('P01', 'P02') else 'PR2'] = by.get('PR1' if p in ('P01', 'P02') else 'PR2', 0) + 1
        by['PR1'] += 2  # the two no-shows added in setUp
        self.assertEqual({r['organization_id']: r['count'] for r in a['table']}, by)  # P05's three PR2 no-shows are out

    def test_a_cut_at_a_tie_takes_the_first_group_by_id_every_time(self):
        for _ in range(3):
            a = self.answer('Which practice had the most no-shows in the last year?')
            self.assertEqual([r['organization_id'] for r in a['table']], ['PR1'])  # PR1 and PR2 tie at 3: ties break by the group id

    def test_the_event_rule_is_the_decision_contexts_not_a_special_case(self):
        self.assertIn('appointment', DC.EVENT_GRAIN_TABLES)
        self.assertNotIn('payer_claim', DC.EVENT_GRAIN_TABLES)  # money is decided by the question's own words (NON_PATIENT_GRAIN)
        self.assertEqual(self.value('How many claims were denied for our payer report?') is not None, True)


class EngineCohortsCarryTheLabRule(Extra):
    def test_a_diabetic_by_lab_rule_is_in_the_outreach_population(self):
        q = 'Who should we prioritize for diabetes outreach?'
        a = self.answer(q)
        facts = {f['id']: f for f in a['insight']['facts']}

        def diabetic(p):
            return has_condition(p, 'diabetes') or any(c == 'hba1c' and q_ == p and v >= 6.5 for q_, c, d, v in LABS + [('P06', 'hba1c', '2029-09-09', 7.2)])
        pop = [p for p in PATIENTS if living(p) and diabetic(p) and age_at(PATIENTS[p][0]) >= 18]
        self.assertIn('P06', pop)
        self.assertEqual(facts['population']['value'], len(pop))
        lab = lambda p: [v for q_, c, d, v in LABS + [('P06', 'hba1c', '2029-09-09', 7.2)] if q_ == p and c == 'hba1c' and in_win(d)]
        self.assertEqual(facts['nolab']['value'], len([p for p in pop if not lab(p)]))


class PreventiveLabelsStateTheMeasureAsDefined(unittest.TestCase):
    def test_labels_come_from_the_measure_definition(self):
        from clinical_core.agere.clinical import insight_families as IF
        from clinical_core.agere.clinical import measures as M
        lib = M.library()
        bcs = IF.measure_label(lib['bcs'], 'Breast cancer screening')
        col = IF.measure_label(lib['col'], 'Colorectal cancer screening')
        self.assertIn(f"{lib['bcs']['denominator']['age']['min']}-{lib['bcs']['denominator']['age']['max']}", bcs)
        self.assertNotIn('50-74', bcs)  # the governed measure reads women 42-74
        self.assertIn(f"{lib['col']['denominator']['age']['min']}-{lib['col']['denominator']['age']['max']}", col)


if __name__ == '__main__':
    unittest.main()
