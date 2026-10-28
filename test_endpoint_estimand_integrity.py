"""Authored endpoint-support and estimand identities; no operational or question-bank inputs."""
import unittest
from unittest.mock import patch
import numpy as np
from clinical_core.agere.clinical import survival as S, target_trial as T, causal_hte as C, uplift as U, analysis_graph as G
from test_survival_bounds import BoundedThreshold
import test_financial_time_integrity as F


class HorizonSupport(unittest.TestCase):
    def test_censored_tail_is_unknown_but_supported_earlier_endpoint_remains(self):
        rows = S.kaplan_meier([3] * 100, [0] * 100)
        self.assertEqual(S.km_at(rows, 365), (None, None, None, 0))
        self.assertEqual(S.km_at(rows, 3), (1, 1, 1, 100))
        self.assertEqual(T.weighted_km(np.full(100, 3), np.zeros(100), np.ones(100), 365)[0], None)

    def test_partial_events_do_not_identify_tail(self):
        times, events = [2] * 20 + [3] * 80, [1] * 20 + [0] * 80
        self.assertIsNone(S.km_at(S.kaplan_meier(times, events), 365)[0])
        self.assertIsNone(T.weighted_km(np.array(times), np.array(events), np.ones(100), 365)[0])

    def test_full_followup_and_absorbing_all_events_controls(self):
        for times, events, survival, at_risk in [([2] * 20 + [365] * 80, [1] * 20 + [0] * 80, .8, 80),
                                                   ([2] * 100, [1] * 100, 0, 0)]:
            value = S.km_at(S.kaplan_meier(times, events), 365)
            self.assertAlmostEqual(value[0], survival); self.assertEqual(value[3], at_risk)
            risk, _ = T.weighted_km(np.array(times), np.array(events), np.ones(100), 365)
            self.assertAlmostEqual(risk, 1 - survival)

    def test_zero_weight_followup_cannot_support_a_tail(self):
        r = S.horizon_support([3, 365], [0, 0], 365, [1, 0])
        self.assertFalse(r['supported']); self.assertEqual(r['last_followup_days'], 3)

    def test_treatment_line_reader_keeps_unknown_endpoint_and_supported_curve(self):
        from clinical_core.agere.clinical import line_of_therapy as L
        r = L._km_summary([3] * 100, [0] * 100, points=(3, 365))
        self.assertEqual(r['still_on_day_3'], 1)
        self.assertIsNone(r['still_on_day_365']); self.assertEqual(r['ci95_day_365'], [None, None])
        self.assertTrue(r['curve']); self.assertFalse(r['horizon_support'][365]['supported'])

    def estimate(self, durations):
        a = np.repeat([1, 0], 100)
        outcomes = [('event', t, 0) for t in durations]
        with patch.object(T, 'logistic', return_value=np.full(200, .5)):
            return T.estimate([{}] * 200, np.zeros((200, 1)), a, outcomes,
                              {'type': 'admission', 'horizon_days': 365},
                              {'adjustment': 'overlap', 'caliper': .2, 'seed': 7, 'bootstrap': 50}, np.random.default_rng(7))

    def test_bootstrap_cannot_turn_missing_tail_into_zero_risk(self):
        r = self.estimate([3] * 200)
        self.assertEqual(r['status'], 'partial'); self.assertIsNone(r['risk_treated'])
        self.assertEqual(r['risk_difference_ci95'], [None, None]); self.assertEqual(r['bootstrap_draws'], 0)
        self.assertTrue(r['curves']['treated']); self.assertEqual(r['horizon_support']['treated']['at_risk'], 0)

    def test_known_arm_retained_without_a_comparative_estimate(self):
        r = self.estimate([3] * 100 + [365] * 100)
        self.assertEqual(r['risk_comparator'], 0); self.assertIsNone(r['risk_treated'])
        report = {'analyses': {'intention_to_treat': r}, 'arm_labels': ['A', 'B'], 'arms': ['a', 'b'],
                  'sample': {'a': 100, 'b': 100}, 'outcome_label': 'admission', 'inference': 'not_supported', 'blocking_warnings': []}
        self.assertIn('risk unknown', T.reading(report))
        self.assertEqual(T.result_rows(report)[0]['horizon_support'], r['horizon_support'])


class SurvivalOutput(unittest.TestCase):
    setUp = BoundedThreshold.setUp
    add = BoundedThreshold.add
    params = BoundedThreshold.params
    run_report = BoundedThreshold.run_report
    def test_no_late_followup_propagates_unknown_through_answer(self):
        for pid in ('A', 'B'):
            self.add('base-' + pid, pid, '2030-01-01', 5)
            self.add('last-' + pid, pid, '2030-01-04', 6)
        r = self.run_report()
        self.assertEqual(r['status'], 'partial'); self.assertTrue(r['unsupported_horizons'])
        self.assertTrue(all(row['cumulative_incidence'] is None for row in r['table']))
        self.assertIn('risk unknown', S.summary(r))
        plan = {'nodes': [{'id': 's', 'operation': 'survival', 'parameters': {}}], 'coverage': 'supported_observation', 'unmet_requirements': []}
        a = G.build_answer_core(plan, [{'result': r}], {}, 'Show 30-day risk')
        self.assertEqual(a['status'], 'partial'); self.assertTrue(a['missing'])
        self.assertEqual(a['overall']['unsupported_horizons'], r['unsupported_horizons'])


class TreatmentOutput(unittest.TestCase):
    setUp = F.TreatmentSnapshot.setUp
    fill = F.TreatmentSnapshot.fill

    def test_partial_followup_preserves_line_table_and_unknowns_in_answer(self):
        from clinical_core.agere.clinical import line_of_therapy as L
        r = L.run(self.db, None, self.params, '2030-01-31')
        self.assertEqual(r['status'], 'partial'); self.assertEqual(len(r['lines']), 1)
        self.assertIsNone(r['lines'][0]['next_line_by_day_365'])
        plan = {'nodes': [{'id': 'l', 'operation': 'line_of_therapy', 'parameters': {}}], 'coverage': 'supported_observation', 'unmet_requirements': []}
        a = G.build_answer_core(plan, [{'result': r}], {}, 'Describe treatment lines and one-year persistence')
        self.assertEqual(a['status'], 'partial'); self.assertEqual(a['answer_type'], 'table')
        self.assertTrue(a['table']); self.assertTrue(a['missing'])


class Estimand(unittest.TestCase):
    def fixture(self, tau=(1., -2.), propensity=(.5, .1)):
        e = np.repeat(propensity, 1000); effects = np.repeat(tau, 1000)
        t = np.concatenate([np.r_[np.ones(round(v * 1000)), np.zeros(1000 - round(v * 1000))] for v in propensity])
        return t, e, effects, t * effects, e * effects

    def test_projection_can_reverse_population_ate_and_must_be_labelled(self):
        t, e, tau, y, m = self.fixture()
        r = C.plr_projection(y, t, m, e)
        self.assertAlmostEqual(r['estimate'], 7 / 34); self.assertGreater(r['ci95'][0], 0)
        aipw = tau + t * (y - tau) / e - (1 - t) * y / (1 - e)
        self.assertAlmostEqual(aipw.mean(), -.5)
        self.assertFalse(r['population_ate']); self.assertIn('variance_weighted', r['estimand'])

    def test_constant_effect_or_propensity_controls(self):
        for tau, propensity in [((3, 3), (.5, .1)), ((1, -2), (.5, .5))]:
            t, e, effects, y, m = self.fixture(tau, propensity)
            self.assertAlmostEqual(C.plr_projection(y, t, m, e)['estimate'], effects.mean())

    def test_uplift_keeps_aipw_ate_and_projection_separate(self):
        t, e, tau, y, m = self.fixture()
        X = np.repeat([[0.], [1.]], 1000, axis=0)
        with patch.object(C, 'nuisances', return_value=(10 + m, e)), patch.object(C, 'r_learner', return_value=tau):
            r = U.analyze(X, t, 10 + y, family='gaussian', learners=['dml'], primary='dml', direction='increase', bootstrap=30)
        self.assertAlmostEqual(r['ate_benefit']['estimate'], -.5, delta=.02)
        self.assertEqual(r['ate_benefit']['estimand'], 'average_treatment_effect')
        self.assertEqual(r['ate_benefit']['n'], 2000)
        self.assertAlmostEqual(r['plr_projection_benefit']['estimate'], 7 / 34)
        self.assertFalse(r['plr_projection_benefit']['population_ate'])
        self.assertNotIn('dml_ate_benefit', r)


if __name__ == '__main__': unittest.main()
