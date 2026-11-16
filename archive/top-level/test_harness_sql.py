import json, random, sys, tempfile, unittest
from pathlib import Path
from tools.training import harness_sql as H

STACK = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v28-sample-0.30/stack')
SQL = "-- DEFINITION: x\nSELECT count(*) FROM measurement WHERE concept = 'potassium' AND unit IS NOT NULL"


class HarnessTests(unittest.TestCase):
    def test_pure_pieces(self):
        row = {'messages': [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'SCHEMA\n\nQ: How many?'},
                            {'role': 'assistant', 'content': '```sql\nSELECT 1\n```'}]}
        self.assertEqual(H.question_and_sql(row), ('How many?', 'SELECT 1'))
        self.assertIn('DISTINCT concept FROM measurement', H.probe_for(SQL))
        kinds = dict(H.faults(SQL, {}))
        self.assertIn("'potassiums'", kinds['wrong_value'])
        self.assertIsNone(H.probe_for('SELECT 1'))

    @unittest.skipUnless(STACK.exists(), 'active stack not present')
    def test_trajectories_use_real_environment_feedback(self):
        pair = {'split': 'train', 'question': 'How many potassium results were reported in a unit other than mmol/L?', 'sql': SQL}
        from clinical_core.agere.clinical import source_profile
        sys.path.insert(0, str(Path(H.ROOT)))
        import tools.bottling.evaluate as E
        profile = source_profile.load(STACK); as_of, roster = E.stack_facts(STACK)
        ts, why = H.trajectory(pair, STACK, profile, as_of, roster, random.Random(1), 2)
        if why: self.skipTest(why)
        kinds = {k for k, _ in ts}
        self.assertIn('clean', kinds)
        for kind, msgs in ts:
            self.assertTrue(msgs[-1]['content'].startswith('FINAL') or 'FINAL ```sql' in msgs[-1]['content'])
            self.assertIn(SQL, msgs[-1]['content'])
            if kind != 'clean':
                self.assertTrue(any(m['content'].startswith('ERROR:') for m in msgs if m['role'] == 'user'))
                self.assertIn('DIAGNOSE:', msgs[-1]['content'])


if __name__ == '__main__':
    unittest.main()
