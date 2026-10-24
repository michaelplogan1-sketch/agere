"""System One checks a spec against the question part by part (spec_verify.py); fake providers, no network."""
import unittest

from clinical_core.agere.clinical import request_language as L, spec_verify as V
from spec_test_support import Warehouse, FakeS1, AS_OF

Q = 'Which patients have heart failure but no SGLT2 inhibitor fill in the last year?'


class Verify(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse()
        self.addCleanup(self.w.close)
        self.spec = L.parse(self.w.catalog, Q)

    def run_with(self, s1, **kw):
        return V.verify(self.w.stack, Q, self.spec, self.w.catalog, decide=s1, ledger=None, **kw)

    def test_every_part_is_one_typed_question_with_its_read_back_as_evidence(self):
        s1 = FakeS1()
        out = self.run_with(s1)
        kinds = [p['kind'] for p in out['parts']]
        self.assertEqual(kinds[0], 'population')
        self.assertIn('window', kinds)
        self.assertEqual(kinds[-1], 'completeness')
        self.assertEqual(len(s1.calls), len(out['parts']))
        self.assertTrue(all(c['task'] == V.TASK for c in s1.calls))
        self.assertTrue(all(len(c['evidence']) == 1 and c['evidence'][0] for c in s1.calls))
        window = next(c for c in s1.calls if 'time window' in c['evidence'][0])
        self.assertIn('12 months ending 2030-05-27', window['evidence'][0])
        self.assertEqual(set(window['options']) - {'unknown'}, {'yes', 'partly', 'no'})

    def test_all_yes_confirms(self):
        out = self.run_with(FakeS1())
        self.assertEqual(out['verdict'], 'confirmed')
        self.assertTrue(out['confirmed'])
        self.assertEqual(out['failed'], [])
        self.assertGreater(out['score'], 0.9)
        self.assertGreater(out['calls'], 0)
        self.assertIn('Defaults applied', out['read_back'])

    def test_a_no_part_fails_the_spec_and_feeds_back_exactly_that_part(self):
        out = self.run_with(FakeS1({'no SGLT2 inhibitor fill': ('no', 0.92)}))
        self.assertEqual(out['verdict'], 'failed')
        self.assertFalse(out['confirmed'])
        self.assertTrue(out['failed'])
        feedback = V.feedback(out)
        self.assertTrue(feedback and all('SGLT2' in f['the_step_read_back_as'] for f in feedback))
        self.assertEqual({f['system_one_said'] for f in feedback}, {'no'})

    def test_partly_fails_only_when_confident(self):
        weak = self.run_with(FakeS1({'no SGLT2 inhibitor fill': ('partly', 0.75)}))
        self.assertEqual(weak['verdict'], 'unverified')  # below the partly bar: not shipped as confirmed, not a failure either
        strong = self.run_with(FakeS1({'no SGLT2 inhibitor fill': ('partly', 0.95)}))
        self.assertEqual(strong['verdict'], 'failed')

    def test_an_unsure_provider_leaves_the_part_unverified_never_confirmed(self):
        out = self.run_with(FakeS1(default=('yes', 0.5)))  # nobody cleared the bar
        self.assertEqual(out['verdict'], 'unverified')
        self.assertFalse(out['confirmed'])
        self.assertEqual(len(out['unverified']), len(out['parts']))

    def test_a_provider_error_is_unverified_not_a_crash(self):
        def boom(*a, **k): raise RuntimeError('provider down')
        out = self.run_with(boom)
        self.assertEqual(out['verdict'], 'unverified')
        self.assertTrue(all(p.get('error') for p in out['parts']))

    def test_the_summary_for_an_answer_is_compact(self):
        out = self.run_with(FakeS1())
        summary = V.summary(out)
        self.assertTrue(summary['confirmed'])
        self.assertEqual(set(summary['parts'][0]), {'id', 'kind', 'text', 'label', 'p', 'provider', 'status'})

    def test_parts_run_concurrently_but_boundedly(self):
        import threading, time
        live, peak = [0], [0]
        lock = threading.Lock()
        base = FakeS1()
        def slow(*a, **k):
            with lock:
                live[0] += 1; peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with lock: live[0] -= 1
            return base(*a, **k)
        out = self.run_with(slow, workers=3)
        self.assertTrue(out['confirmed'])
        self.assertGreater(peak[0], 1)
        self.assertLessEqual(peak[0], 3)


if __name__ == '__main__':
    unittest.main()
