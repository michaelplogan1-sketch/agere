"""Outreach bandit engine: Thompson sampling over channel x segment arms from the logged outreach outcomes."""
import json, sqlite3, tempfile, unittest
from pathlib import Path

from clinical_core.agere.clinical import engine_suite as E, outreach_learning as L

AS_OF = '2030-05-27'


def build(path, n_phone=(30, 60), n_text=(5, 40), n_mail=(2, 30)):
    """Synthetic outreach log: phone engages 30/60, text 5/40, mail 2/30 (engaged vs other outcomes)."""
    db = sqlite3.connect(Path(path)/'semantic.sqlite3')
    db.executescript("CREATE TABLE meta(key, value); CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, "
                     "event_date TEXT, recorded_date TEXT, available_date TEXT, organization_id TEXT, payload TEXT);")
    rows, k = [], 0
    for channel, (hits, n) in (('phone', n_phone), ('text', n_text), ('mail', n_mail)):
        for j in range(n):
            k += 1
            outcome = 'engaged' if j < hits else ('declined' if j % 2 else 'no_answer')
            rows.append((f'r{k}', f'P-{k}', 'outreach_attempts', '2029-01-01', None, None, 'CM',
                         json.dumps({'channel': channel, 'outcome': outcome, 'program': 'ccm', 'attempt_number': 1 + j % 2,
                                     'eligibility_risk_score': 1.5 if j % 3 else 3.5})))
    rows.append(('future', 'P-X', 'outreach_attempts', '2031-01-01', None, None, 'CM', json.dumps({'channel': 'mail', 'outcome': 'engaged'})))
    db.executemany('INSERT INTO record VALUES(?,?,?,?,?,?,?,?)', rows)
    db.commit()
    return db


class Bandit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = build(self.tmp.name); self.addCleanup(self.db.close)

    def test_posteriors_best_arm_and_thompson_allocation(self):
        r = L.run(self.db, None, L.validate({'next_batch': 300}), AS_OF)
        self.assertEqual(r['attempts'], 130)  # future attempt excluded
        best = r['arms'][0]
        self.assertEqual((best['channel'], best['engaged'], best['attempts']), ('phone', 30, 60))
        self.assertGreater(best['p_best'], 0.95)
        self.assertAlmostEqual(best['posterior_mean'], 31 / 62, places=3)
        self.assertEqual(sum(r['allocation'].values()), 300)
        self.assertGreater(r['allocation']['phone|all'], 250)
        self.assertIsNone(r['simulation'])

    def test_segments_and_simulated_continuation_is_labelled(self):
        r = L.run(self.db, None, L.validate({'segment_by': 'risk_tier', 'simulate_rounds': 10, 'simulate_batch': 20}), AS_OF)
        self.assertEqual({a['segment'] for a in r['arms']}, {'low', 'high'})
        self.assertEqual(r['simulation']['label'], 'simulated outcomes')
        self.assertEqual(len(r['simulation']['curve']), 10)
        self.assertGreaterEqual(r['simulation']['cumulative_regret'], 0)
        a = L.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertIn('Simulated outcomes', a['value'])
        self.assertTrue(any('synthetic' in x for x in a['limitations']))

    def test_response_rates_feed_and_population_scope(self):
        rates = L.response_rates(self.db)
        self.assertAlmostEqual(rates[('phone', 'all')], 31 / 62)
        r = L.run(self.db, ['P-1', 'P-2'], L.validate({}), AS_OF)
        self.assertEqual(r['attempts'], 2)
        empty = L.run(self.db, ['nobody'], L.validate({}), AS_OF)
        self.assertEqual(empty['status'], 'no_outcomes')
        self.assertIn('nothing to learn', L.answer(empty, {}, 'q')['value'])

    def test_validate(self):
        for bad in ({'segment_by': 'zip'}, {'next_batch': 0}, {'x': 1}, {'simulate_rounds': 500}):
            with self.assertRaises(ValueError): L.validate(bad)
        self.assertEqual(L.validate({})['segment_by'], 'none')

    def test_execute_reads_semantic_db(self):
        self.db.commit()
        r = L.execute(self.tmp.name, None, {}, AS_OF)
        self.assertEqual(r['arms'][0]['channel'], 'phone')

    def test_route(self):
        g, info = E.compile_route('Which outreach channel works best for engaging members, by risk tier?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['operation'], 'outreach_bandit')
        self.assertEqual(g['nodes'][0]['parameters'], {'segment_by': 'risk_tier'})
        g, _ = E.compile_route('Run a bandit over our outreach channels for the next 150 contacts and simulate regret', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters'], {'next_batch': 150, 'simulate_rounds': 20})
        for q in ('Who benefits most from care management outreach?', 'Our nurses can make 30 calls this week - who should they call?',
                  'Allocate outreach across phone and text given 20 staff hours a week', 'What is a multi-armed bandit?'):
            r = L.route(q, {}, AS_OF)
            self.assertIsNone(r, q)


if __name__ == '__main__':
    unittest.main()
