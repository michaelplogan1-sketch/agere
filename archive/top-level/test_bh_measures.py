"""Behavioral-health measures (HEDIS FUH, FUM, IET approximations) on a small hand-checkable store (synthetic).

Period 2028-01-01..2028-12-31, everyone enrolled from 2027-01-01.
  h1 inpatient F32.9 05-01..05-03, psychotherapy CPT 90834 on 05-08          -> FUH 7 met, FUH 30 met
  h2 inpatient F20.9 06-01..06-05, psychotherapy CPT 90837 on 06-20          -> FUH 7 not met, FUH 30 met
  h3 inpatient I50.9 (not mental illness)                                    -> not in FUH
  e1 ED F41.1 on 03-01, visit coded F41.1 on 03-05                           -> FUM 7 met (governed ED definition index)
  e2 ED J06.9 (not mental illness)                                           -> not in FUM
  s1 SUD F10.20 at a visit 04-01 (none in prior 194 days), visits coded F10.20 on 04-05, 04-20, 05-10 -> IET initiated + engaged
  s2 SUD F11.20 on 07-01 and an earlier F11.20 on 03-01 (episode 03-01 is new; 07-01 is not), nothing after 03-01 -> not initiated
"""
import unittest

from clinical_core.agere.clinical import measures, semantic_layer
from test_aggregate_explain import build
from test_measures import cov, line

PERIOD = {'start': '2028-01-01', 'end': '2028-12-31'}


def enc(pid, eid, kind, day, icd10, end=None):
    return (pid, 'encounters', {'type': kind, 'encounter_id': eid, 'event_date': day, 'organization_id': 'H1',
                                **({'end_time': end + 'T10:00:00'} if end else {}),
                                'diagnoses': [{'rank': 1, 'icd10': icd10, 'type': 'principal'}]})


class BehavioralHealthMeasures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pids = ('h1', 'h2', 'h3', 'e1', 'e2', 's1', 's2')
        patients = {p: {'age_reported': 45, 'sex_reported': 'F', 'birth_date': '1983-01-01'} for p in pids}
        recs = [cov(p) for p in pids]
        recs += [enc('h1', 'S1', 'inpatient', '2028-05-01', 'F32.9', '2028-05-03'), line('h1', 'C1', '90834', '2028-05-08'),
                 enc('h2', 'S2', 'inpatient', '2028-06-01', 'F20.9', '2028-06-05'), line('h2', 'C2', '90837', '2028-06-20'),
                 enc('h3', 'S3', 'inpatient', '2028-07-01', 'I50.9', '2028-07-03')]
        recs += [enc('e1', 'ED1', 'emergency', '2028-03-01', 'F41.1'), enc('e1', 'V1', 'primary_care', '2028-03-05', 'F41.1'),
                 enc('e2', 'ED2', 'emergency', '2028-03-01', 'J06.9')]
        recs += [enc('s1', f'A{i}', 'primary_care', d, 'F10.20') for i, d in enumerate(('2028-04-01', '2028-04-05', '2028-04-20', '2028-05-10'))]
        recs += [enc('s2', 'B1', 'primary_care', '2028-03-01', 'F11.20'), enc('s2', 'B2', 'primary_care', '2028-07-01', 'F11.20')]
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)

    def nd(self, mid):
        o = measures.evaluate(self.path, mid, PERIOD, as_of='2029-08-15', output='gaps')
        self.assertFalse(o['declined'], o.get('missing_inputs'))
        return o['overall']['numerator'], o['overall']['denominator']

    def test_library_has_valid_cited_bh_measures(self):
        lib = measures.library()
        for mid in ('fuh_mental_illness_7day', 'fuh_mental_illness_30day', 'fum_ed_mental_illness_7day', 'fum_ed_mental_illness_30day',
                    'iet_initiation', 'iet_engagement'):
            self.assertIn(mid, lib)
            self.assertEqual(lib[mid]['status'], 'approximation')
            self.assertTrue(any('ncqa.org' in c['url'] for c in lib[mid]['citations']))
        self.assertEqual(lib['fum_ed_mental_illness_7day']['index']['type'], 'ed_visit')  # the governed ED definition
        self.assertIn('fuh_mental_illness_7day', measures.measure_sets()['behavioral_health']['measures'])

    def test_aliases_route_questions(self):
        self.assertEqual(measures.match('What is our FUH 7-day follow-up rate?')[0], 'fuh_mental_illness_7day')
        self.assertEqual(measures.match('FUM 30-day rate by practice')[0], 'fum_ed_mental_illness_30day')
        self.assertEqual(measures.match('How many members had an alcohol or drug treatment initiation within 14 days (IET)?')[0], 'iet_initiation')

    def test_fuh(self):
        self.assertEqual(self.nd('fuh_mental_illness_7day'), (1, 2))
        self.assertEqual(self.nd('fuh_mental_illness_30day'), (2, 2))

    def test_fum_indexes_on_the_governed_ed_definition(self):
        self.assertEqual(self.nd('fum_ed_mental_illness_7day'), (1, 1))

    def test_iet_new_episode_initiation_engagement(self):
        self.assertEqual(self.nd('iet_initiation'), (1, 2))   # s1 initiated 04-05; s2's new episode 03-01 never treated
        self.assertEqual(self.nd('iet_engagement'), (1, 2))   # s1: SUD visits on 04-05, 04-20, 05-10


    def test_library_measure_engine_and_exact_routing(self):
        from clinical_core.agere.clinical import library_measure as L, analysis_graph, economical_planner
        g = L.local_question('What is our FUH 7-day follow-up rate in 2028?', '2029-08-15')
        p = g['nodes'][0]['parameters']
        self.assertEqual((p['measure'], p['follow_up_days'], p['period']['start']), ('fuh_mental_illness_7day', 7, '2028-01-01'))
        rep = L.execute(self.path, None, p, '2029-08-15')
        a = L.answer(rep, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual((a['measure']['numerator'], a['measure']['denominator'], a['value']), (1, 2, 50.0))
        gaps = L.answer(L.execute(self.path, None, {**p, 'output': 'gaps'}, '2029-08-15'), {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(gaps['patient_ids'], ['h2'])
        fum = L.local_question('What share of ED visits for mental illness had follow-up within 7 days in 2028?', '2029-08-15')
        self.assertEqual(fum['nodes'][0]['parameters']['measure'], 'fum_ed_mental_illness_7day')
        iet = L.local_question('IET initiation rate for alcohol or drug use disorder in 2028', '2029-08-15')
        self.assertEqual(iet['nodes'][0]['parameters']['measure'], 'iet_initiation')
        self.assertIsNone(L.local_question('What is our FUH 7-day follow-up rate for diabetics?', '2029-08-15'))  # qualifier kept
        self.assertIsNone(L.local_question('What is the diabetes A1c poor control rate?', '2029-08-15'))  # other measures keep their routes
        self.assertIn('library_measure', analysis_graph.CLINICAL_LOGIC)
        graph, recipe = economical_planner.local_plan('What is our FUH 7-day follow-up rate in 2028?', '2029-08-15')
        self.assertEqual(recipe, 'exact_foundation_library_measure')


if __name__ == '__main__':
    unittest.main()
