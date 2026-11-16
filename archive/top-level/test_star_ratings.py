"""Star projection: measure stars vs cut points, weighted half-star overall, QBP, what-if path, P(>= target), route."""
import sqlite3
import unittest
from unittest import mock

from clinical_core.agere.clinical import star_ratings as S, engine_suite

AS_OF = '2030-05-27'
P = S.validate({})


class Scoring(unittest.TestCase):
    def test_measure_stars_both_directions(self):
        self.assertEqual(S.stars(81, (53, 63, 72, 80), 'higher'), 5)
        self.assertEqual(S.stars(62.9, (53, 63, 72, 80), 'higher'), 2)
        self.assertEqual(S.stars(40, (53, 63, 72, 80), 'higher'), 1)
        self.assertEqual(S.stars(9, (14, 12, 10, 8), 'lower'), 4)
        self.assertEqual(S.stars(20, (14, 12, 10, 8), 'lower'), 1)

    def test_overall_half_star_rounding(self):
        self.assertEqual(S.overall([(4, 3), (3, 1)]), (3.75, 4.0))
        self.assertEqual(S.overall([(4, 1), (3, 3)]), (3.25, 3.5))
        self.assertEqual(S.overall([(3, 1), (3, 1)]), (3.0, 3.0))

    def test_needed(self):
        self.assertEqual(S.needed(60, 100, 63, 'higher'), 3)
        self.assertEqual(S.needed(15, 100, 12, 'lower'), 3)
        self.assertEqual(S.needed(70, 100, 63, 'higher'), 0)

    def rates(self):
        return {'cbp': {'numerator': 70, 'denominator': 100}, 'bcs': {'numerator': 70, 'denominator': 100},
                'pcr_readmissions': {'numerator': 11, 'denominator': 100}}

    def test_rows_projection_and_path(self):
        rows = S.score_rows(self.rates(), {**P, 'closure_rate': 0.5})
        by = {r['measure']: r for r in rows}
        self.assertEqual((by['cbp']['stars'], by['bcs']['stars'], by['pcr_readmissions']['stars']), (3, 3, 3))
        self.assertEqual(by['cbp']['projected_stars'], 5)  # 70 + 0.5 x 30 gaps = 85%
        self.assertEqual(by['cbp']['patients_to_next_star'], 4)
        path = S.path_to_target(rows, 4.0)
        self.assertTrue(path['feasible'])
        self.assertEqual(path['reaches_overall'], 4.0)
        self.assertEqual(path['steps'][0]['measure'], 'pcr_readmissions')  # weight 3 for 1 avoided readmission beats cbp (3 per 4)

    def test_probability_monotone(self):
        rows = S.score_rows(self.rates(), P)
        lo = S.probability_at_least(rows, 4.0, 2000)
        hi = S.probability_at_least(rows, 3.0, 2000)
        self.assertLessEqual(lo, hi)
        self.assertGreater(S.probability_at_least(rows, 4.0, 2000, projected=True, closure_rate=0.9), lo)

    def test_supplied_cut_points(self):
        rows = S.score_rows({'cbp': {'numerator': 70, 'denominator': 100}}, S.validate({'cut_points': {'cbp': [10, 20, 30, 40]}}))
        self.assertEqual((rows[0]['stars'], rows[0]['cut_point_source']), (5, 'supplied'))

    def test_validate(self):
        for bad in ({'measures': ['nope']}, {'closure_rate': 2}, {'cut_points': {'cbp': [80, 70, 60, 50]}},
                    {'cut_points': {'pcr_readmissions': [8, 10, 12, 14]}}, {'x': 1}, {'population': 'kids'}):
            with self.assertRaises(ValueError): S.validate(bad)


class Run(unittest.TestCase):
    def test_run_with_qbp_and_exclusions(self):
        db = sqlite3.connect(':memory:')
        fake = ({'cbp': {'numerator': 80, 'denominator': 100}, 'bcs': {'numerator': 82, 'denominator': 100}},
                [{'measure': 'col', 'missing_inputs': ['x']}])
        with mock.patch.object(S, 'ma_members', return_value={'A': 12, 'B': 12}), mock.patch.object(S, 'rates', return_value=fake):
            r = S.run(db, None, S.validate({'benchmark_pmpm': 1000}), AS_OF)
        self.assertEqual(r['overall']['stars'], 4.5)  # cbp 4 (w3) + bcs 5 (w1) -> 4.25 -> 4.5
        self.assertEqual(r['qbp_current']['qbp_bonus_pct'], 0.05)
        self.assertEqual(r['qbp_current']['qbp_dollars'], round(0.05 * 1000 * 24, 2))
        self.assertEqual(r['qbp_current']['rebate_share'], 0.70)
        self.assertTrue(any('declined: col' in a for a in r['assumptions']))
        ans = S.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual((ans['status'], ans['number']), ('answered', 4.5))
        self.assertIn('QBP 5%', ans['value'])


class Route(unittest.TestCase):
    def test_routes(self):
        g, _ = engine_suite.compile_route('Probability our MA contract reaches 4 stars if we close 40% of open gaps', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['operation'], 'star_rating')
        self.assertEqual(g['nodes'][0]['parameters'], {'target_stars': 4.0, 'closure_rate': 0.4})
        S.validate(g['nodes'][0]['parameters'])
        self.assertIsNone(S.route('How are star ratings calculated?', {}, AS_OF))
        self.assertIsNone(S.route('How close are we to the 4-star cut point on Plan All-Cause Readmissions?', {}, AS_OF))
        self.assertIsNone(S.route('How many ED visits did we have?', {}, AS_OF))


if __name__ == '__main__':
    unittest.main()
