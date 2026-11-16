"""Order-book queue (price-time priority, capacity matching) and count-min / HyperLogLog sketches."""
import random
import unittest

from clinical_core.agere.work_queue import OrderBook, CountMinSketch, HyperLogLog, profile_feed


class Book(unittest.TestCase):
    def test_price_time_priority(self):
        b = OrderBook()
        for k, p in (('a', 1), ('b', 3), ('c', 3), ('d', 2)): b.add(k, p)
        self.assertEqual([k for k, _ in b.ordered()], ['b', 'c', 'd', 'a'])  # equal priority keeps arrival order
        self.assertEqual(b.pop()[0], 'b')

    def test_amend_loses_time_priority_unchanged_keeps_it(self):
        b = OrderBook()
        b.add('x', 5); b.add('y', 5); b.amend('x', 5)
        self.assertEqual(b.peek()[0], 'x')
        b.amend('x', 6); b.amend('x', 5)  # re-priced twice: now behind y
        self.assertEqual([k for k, _ in b.ordered()], ['y', 'x'])

    def test_cancel_and_lazy_deletion(self):
        b = OrderBook()
        for i in range(100): b.add(i, i)
        for i in range(50, 100): b.cancel(i)
        self.assertEqual(len(b), 50); self.assertEqual(b.pop()[0], 49)

    def test_match_fills_around_a_blocked_head(self):
        b = OrderBook()
        b.add('top', 9, 'full'); b.add('mid', 5, 'open'); b.add('low', 1, 'open'); b.add('free', 0)
        filled, blocked = b.match({'full': 0, 'open': 1})
        self.assertEqual(filled, ['mid', 'free']); self.assertEqual(blocked, ['top', 'low'])  # no head-of-line blocking; resting stay
        self.assertEqual(len(b), 2); self.assertEqual(b.depth()[0]['priority'], 9)

    def test_match_limit(self):
        b = OrderBook()
        for i in range(10): b.add(i, i)
        self.assertEqual(b.match({}, limit=3)[0], [9, 8, 7])


class Sketches(unittest.TestCase):
    def test_count_min_never_undercounts_and_stays_in_bound(self):
        rnd, truth = random.Random(1), {}
        cm = CountMinSketch(eps=0.001, delta=0.01)
        for _ in range(50000):
            k = f'k{int(rnd.paretovariate(1.2))}'
            truth[k] = truth.get(k, 0) + 1; cm.add(k)
        over = [cm.count(k) - n for k, n in truth.items()]
        self.assertGreaterEqual(min(over), 0)
        self.assertLessEqual(sorted(over)[int(len(over) * .99)], cm.error_bound()['overcount_at_most'])

    def test_hll_accuracy_and_merge(self):
        a, b = HyperLogLog(), HyperLogLog()
        for i in range(60000): a.add(f'p{i}')
        for i in range(30000, 90000): b.add(f'p{i}')
        self.assertLess(abs(a.count() - 60000) / 60000, 4 * a.relative_error())
        self.assertLess(abs(a.merge(b).count() - 90000) / 90000, 4 * a.relative_error())
        small = HyperLogLog()
        for i in range(37): small.add(i)
        self.assertEqual(small.count(), 37)

    def test_profile_feed(self):
        rows = [('lab', f'p{i % 100}') for i in range(1000)] + [('rx', f'p{i}') for i in range(10)]
        cm, hll = profile_feed(rows, lambda r: r[0], lambda r: r[1])
        self.assertEqual((cm.count('lab'), cm.count('rx')), (1000, 10)); self.assertEqual((hll['lab'].count(), hll['rx'].count()), (100, 10))


if __name__ == '__main__':
    unittest.main()
