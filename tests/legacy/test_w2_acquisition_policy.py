import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_core.agere.clinical import acquisition_policy as A, engine_suite


def stack_dir(d):
    db = sqlite3.connect(Path(d) / 'learning.sqlite3')
    db.executescript("""CREATE TABLE outcomes(question TEXT, route TEXT, status TEXT, reason TEXT, paid_calls INTEGER, usd REAL, created REAL);
    CREATE TABLE corrections(question TEXT, note TEXT, expected TEXT, created REAL);
    CREATE TABLE quarantined(question TEXT, plan_kind TEXT, plan TEXT, signature TEXT, route TEXT, agreement TEXT, controls TEXT, created REAL, context TEXT, provenance TEXT);""")
    rows = []
    for i in range(60):  # counts: the free compiler answers 40 of 60; escalation answers what is left at 5 cents
        rows.append((f'how many patients have condition {i}', 'local_compiler', 'answered' if i < 40 else 'escalated', '', 0, 0.0, 0))
        if i >= 40: rows.append((f'how many patients have condition {i}', 'hosted_verified_sql', 'answered', '', 3, 0.05, 0))
    for i in range(30):  # cost questions: the free compiler never answers; escalation answers 9 of 30, at 40 cents a try
        rows.append((f'total allowed cost for group {i}', 'local_compiler', 'escalated', '', 0, 0.0, 0))
        rows.append((f'total allowed cost for group {i}', 'hosted_verified_sql', 'answered' if i < 9 else 'escalated', '', 5, 0.40, 0))
    db.executemany('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?)', rows); db.commit(); db.close()


class Acquisition(unittest.TestCase):
    def test_policy_orders_free_first_and_can_decline_to_spend(self):
        with tempfile.TemporaryDirectory() as d:
            stack_dir(d)
            r = A.execute(SimpleNamespace(path=Path(d)), None, {}, '2030-06-30')
            pol = {x['family']: x for x in r['policy']}
            self.assertEqual(pol['count']['try_order'][0], 'local_compiler')
            self.assertIn('hosted_verified_sql', pol['count']['try_order'])
            self.assertIsNone(pol['count']['cumulative_success'])  # legacy records do not establish episode/source pairing
            self.assertIn('not identified', pol['count']['sequence_evidence']['reason'])
            self.assertIsNone(pol['count']['coverage_bounds'])
            self.assertEqual(pol['cost_spend']['try_order'], ['hosted_verified_sql'])  # the only operation that ever answered
            hosted = next(x for x in r['table'] if x['family'] == 'cost_spend' and x['operation'] == 'hosted_verified_sql')
            self.assertEqual(hosted['answered'], 9); self.assertAlmostEqual(hosted['mean_usd'], 0.4)
            self.assertGreater(hosted['success_ci95'][1], hosted['success'])

    def test_a_paid_operation_below_the_floor_is_not_recommended(self):
        with tempfile.TemporaryDirectory() as d:
            db = sqlite3.connect(Path(d) / 'learning.sqlite3')
            db.execute('CREATE TABLE outcomes(question TEXT, route TEXT, status TEXT, reason TEXT, paid_calls INTEGER, usd REAL, created REAL)')
            db.executemany('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?)', [(f'average cost for case {i}', 'frontier_escalation', 'answered' if i == 0 else 'escalated', '', 4, 1.0, 0) for i in range(40)])
            db.commit(); db.close()
            r = A.execute(SimpleNamespace(path=Path(d)), None, {'floor': 0.2}, '2030-06-30')
            self.assertEqual(r['policy'][0]['try_order'], [])
            self.assertIn('no further computation', r['policy'][0]['decision'])

    def test_empty_stack_says_so_and_is_registered_and_routed(self):
        with tempfile.TemporaryDirectory() as d:
            r = A.execute(SimpleNamespace(path=Path(d)), None, {}, '2030-06-30')
            self.assertIn('No recorded outcomes', A.headline(r))
        self.assertIn('acquisition_policy', engine_suite.contracts())
        self.assertTrue(engine_suite.needs_stack('acquisition_policy'))
        graph, info = engine_suite.compile_route('Where should we spend evidence acquisition effort?', {}, '2030-06-30')
        self.assertEqual(graph['nodes'][0]['operation'], 'acquisition_policy')
        self.assertIsNone(engine_suite.compile_route('How many patients have diabetes?', {}, '2030-06-30'))

    def test_validate_rejects_unknown_parameters(self):
        with self.assertRaises(ValueError): A.validate({'family': 'nope'})
        with self.assertRaises(ValueError): A.validate({'x': 1})


if __name__ == '__main__':
    unittest.main()
