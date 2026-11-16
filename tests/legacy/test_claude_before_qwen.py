"""With Claude available, the local Qwen planners follow the quick Claude planners; without Claude the order is unchanged."""
import os
import unittest
from unittest import mock

from clinical_core.agere.clinical import proposers as P


class Order(unittest.TestCase):
    def reorder(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            return P.Cascade.qwen_after_quick_claude(None, P.ORDER)

    def test_qwen_waits_for_quick_claude(self):
        order = self.reorder({'ANTHROPIC_API_KEY': 'x'})
        self.assertEqual(sorted(order), sorted(P.ORDER))  # a permutation: nothing removed
        last_quick = max(order.index(t) for t in P.QUICK_CLAUDE if t in order)
        for t in P.LOCAL_QWEN:
            if t in order: self.assertGreater(order.index(t), last_quick, t)
        self.assertLess(order.index('local_compiler'), order.index('metric_router'))  # free deterministic tiers still first
        self.assertLess(order.index('s1_metric_router'), order.index('metric_router'))  # System One still before Claude

    def test_unchanged_without_claude(self):
        self.assertEqual(list(self.reorder({})), list(P.ORDER))
        self.assertEqual(list(self.reorder({'ANTHROPIC_API_KEY': 'x', 'AGERE_CLAUDE': 'off'})), list(P.ORDER))
        self.assertEqual(list(self.reorder({'ANTHROPIC_API_KEY': 'x', 'AGERE_QWEN_BEFORE_CLAUDE': 'on'})), list(P.ORDER))


if __name__ == '__main__':
    unittest.main()
