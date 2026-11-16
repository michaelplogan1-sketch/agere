"""Answer time budget: start/remaining/expired, best executed candidate, partial marking, env defaults."""
import math
import os
import threading
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import answer_budget as AB


class Clock:
    def __init__(self): self.t = 100.0
    def __call__(self): return self.t


class Budget(unittest.TestCase):
    def test_default_and_env(self):
        for raw, want in (('', math.inf), ('45', 45.0), ('off', math.inf), ('0', math.inf), ('junk', math.inf), ('-3', math.inf), ('nan', math.inf)):
            with patch.dict(os.environ, {'AGERE_ANSWER_BUDGET_SECONDS': raw}):
                self.assertEqual(AB.default_seconds(), want, raw)

    def test_start_remaining_expired(self):
        clock = Clock()
        b = AB.AnswerBudget(clock=clock).start(10)
        self.assertEqual(b.remaining(), 10); self.assertFalse(b.expired()); self.assertTrue(b.allows(5))
        clock.t += 6
        self.assertAlmostEqual(b.remaining(), 4); self.assertFalse(b.allows(5)); self.assertTrue(b.allows())
        clock.t += 4
        self.assertTrue(b.expired()); self.assertEqual(b.remaining(), 0.0); self.assertFalse(b.allows())

    def test_unstarted_and_unlimited_never_expire(self):
        self.assertFalse(AB.AnswerBudget().expired()); self.assertEqual(AB.AnswerBudget().remaining(), math.inf)
        clock = Clock(); b = AB.AnswerBudget(clock=clock).start(0); clock.t += 1e9
        self.assertFalse(b.expired())

    def test_best_candidate_and_partial(self):
        clock = Clock(); b = AB.AnswerBudget(clock=clock).start(5)
        self.assertIsNone(b.best())
        b.offer('first', score=1, label='bottled_sql', reasons=['shape: expected a table'])
        b.offer('better', score=2, label='hosted_verified_sql', reasons=['shape: expected a count'])
        b.offer('tie-later', score=2, label='later')
        b.skip('hybrid_analysts'); clock.t += 6
        self.assertEqual(b.best(), 'better')
        out = b.partial({'interpreter': 'hosted_verified_sql', 'limitations': ['x']})
        self.assertTrue(out['partial']); self.assertEqual(out['partial_reason'], 'answer_time_budget')
        self.assertEqual(out['limitations'][0], 'x'); self.assertIn('hosted_verified_sql', out['limitations'][1])
        self.assertIn('hybrid_analysts', out['limitations'][1])
        r = out['answer_budget']
        self.assertEqual((r['budget_seconds'], r['expired'], r['not_tried'], r['partial_from']), (5.0, True, ['hybrid_analysts'], 'hosted_verified_sql'))

    def test_restart_clears_candidates(self):
        b = AB.AnswerBudget().start(5); b.offer('x'); b.start(5)
        self.assertIsNone(b.best())

    def test_module_start_sets_current_per_thread(self):
        b = AB.start(30)
        self.assertIs(AB.current(), b); self.assertFalse(AB.expired()); self.assertLessEqual(AB.remaining(), 30)
        seen = []
        t = threading.Thread(target=lambda: seen.append(AB.current().remaining())); t.start(); t.join()
        self.assertEqual(seen, [math.inf])  # another thread (question) does not inherit this budget


if __name__ == '__main__':
    unittest.main()
