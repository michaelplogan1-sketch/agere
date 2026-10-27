import sqlite3
import json
import unittest

import numpy as np

from clinical_core.agere.clinical import off_policy as O


def world(n=3000, seed=0):
    """Logged outreach with a known logging policy: high-risk patients are mostly phoned; phone works best for high risk,
    text for low risk. Returns decision rows."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        score = float(rng.uniform(0.5, 4.5))
        high = score >= 2.5
        p = [0.1, 0.7, 0.2] if high else [0.3, 0.2, 0.5]  # mail, phone, text
        ch = ['mail', 'phone', 'text'][rng.choice(3, p=p)]
        rate = {'mail': 0.1, 'phone': 0.45 if high else 0.15, 'text': 0.15 if high else 0.4}[ch]
        rows.append({'record_id': f'R{i}', 'patient_id': f'P{i}', 'date': '2027-01-01', 'channel': ch, 'responded': bool(rng.random() < rate),
                     'risk_score': score, 'risk_tier': 'high' if score >= 3 else 'medium' if score >= 2 else 'low', 'repeat': False,
                     'program': 'ccm', 'segment': 'all'})
    return rows


def true_value(policy_rows):
    pass


class OffPolicyTest(unittest.TestCase):
    def test_dr_recovers_known_policy_values(self):
        rows = world()
        p = O.validate({'bootstrap': 100})
        r = O.evaluate(rows, p)
        by = {x['policy']: x for x in r['policies']}
        # true value of the logged policy ~ observed rate
        self.assertAlmostEqual(by['logged']['dr'], r['logged_value'], delta=0.02)
        # uniform policy true value: mean over x of mean_a rate(a, x)
        hi = np.mean([x['risk_score'] >= 2.5 for x in rows])
        uni = hi * (0.1 + 0.45 + 0.15) / 3 + (1 - hi) * (0.1 + 0.15 + 0.4) / 3
        self.assertAlmostEqual(by['uniform']['dr'], uni, delta=0.03)
        # the reward-model greedy policy (phone for high, text for low) truly yields ~0.425 and beats the logged policy
        self.assertGreater(by['model_greedy']['dr'], r['logged_value'] + 0.03)
        self.assertIsNone(r['recommendation'])  # adaptive target policy still needs independent validation
        self.assertEqual(r['candidate_for_independent_validation']['policy'], 'model_greedy')
        self.assertGreater(r['candidate_for_independent_validation']['gain_ci95'][0], 0)
        for x in r['policies']:
            self.assertLessEqual(x['dr_ci95'][0], x['dr']); self.assertGreaterEqual(x['dr_ci95'][1], x['dr'])
            self.assertIn('dr_os', x)

    def test_no_recommendation_without_demonstrated_gain(self):
        rng = np.random.default_rng(3)
        rows = world(400, 5)
        for x in rows: x['responded'] = bool(rng.random() < 0.3)  # no channel effect at all
        r = O.evaluate(rows, O.validate({'bootstrap': 100, 'policies': ['uniform', 'best_arm']}))
        self.assertIsNone(r['recommendation'])
        self.assertIn('independent target-policy validation is required', O.headline({**r, 'patients': 400, 'period': {'start': 'a', 'end': 'b'}}))

    def test_insufficient_log_is_stated(self):
        r = O.evaluate(world(10), O.validate({}))
        self.assertEqual(r['status'], 'insufficient_log')
        self.assertIn('at least 20', O.headline(r))

    def test_validate(self):
        p = O.validate({'policies': ['thompson']})
        self.assertEqual(p['policies'][0], 'logged')
        for bad in ({'policies': ['nope']}, {'bootstrap': -1}, {'clip': 0.5}, {'x': 1}):
            with self.assertRaises(ValueError): O.validate(bad)

    def test_execute_from_records_and_route(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, payload TEXT)')
        for x in world(300, 7):
            db.execute('INSERT INTO record VALUES (?,?,?,?,?)', (x['record_id'], x['patient_id'], 'outreach_attempts', '2027-01-01',
                       json.dumps({'channel': x['channel'], 'outcome': 'engaged' if x['responded'] else 'declined',
                                   'eligibility_risk_score': x['risk_score'], 'attempt_number': 1, 'program': 'ccm'})))
        rep = O.run(db, None, O.validate({'bootstrap': 50}), '2030-01-01')
        self.assertEqual(rep['status'], 'evaluated')
        ans = O.answer(rep, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(ans['answer_type'], 'table'); self.assertIn('Doubly robust', ans['value'])
        chk = O.check_recommendation(db, bootstrap=50)
        self.assertIn('thompson', chk['policies']); self.assertTrue(O.describe_check(chk).startswith('Off-policy check'))
        g, info = O.route('Would the bandit policy have done better than what we did?', None, '2030-01-01')
        self.assertEqual(g['nodes'][0]['operation'], 'policy_evaluation')
        self.assertEqual(g['nodes'][0]['parameters']['policies'], ['logged', 'thompson'])
        self.assertIsNotNone(O.route('doubly robust estimate of outreach policy value', None, '2030-01-01'))
        self.assertIsNone(O.route('Which outreach channel gets the most patients to engage?', None, '2030-01-01'))
        self.assertIsNone(O.route('what is off-policy evaluation', None, '2030-01-01'))
        for t in O.TEMPLATES: self.assertIsNotNone(O.route(t, None, '2030-01-01'), t)


if __name__ == '__main__':
    unittest.main()
