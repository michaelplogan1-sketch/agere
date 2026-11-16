import json
import random
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import changepoint_qualification as CQ


class ChangepointQualificationTests(unittest.TestCase):
    def test_simulation_scenarios(self):
        rng = random.Random(1)
        shift = CQ.simulate('shift', rng)
        self.assertIsNotNone(shift['change'])
        self.assertGreaterEqual(shift['change'], CQ.WARMUP)
        gap = CQ.simulate('gap', rng)
        self.assertIn(0, gap['counts'][CQ.WARMUP:])
        lag = CQ.simulate('lag', rng)
        obs = CQ.observed(lag, 40)
        self.assertLessEqual(obs[40], lag['counts'][40])

    def test_gap_rule_marks_zero_weeks_not_ordinary_noise(self):
        counts = [50] * 30 + [0, 0] + [48, 52]
        self.assertEqual(CQ.gap_weeks(counts, 50), {30, 31})
        self.assertEqual(CQ.gap_weeks([45, 55, 50, 47], 50), set())

    def test_detectors_rise_on_a_level_shift(self):
        z = [0.0] * 20 + [2.0] * 10
        for fn in (CQ.cusum_stat, CQ.page_hinkley_stat, CQ.ewma_stat):
            s = fn(z)
            self.assertLess(s[19], s[-1])
        times = list(range(30)); vals = [50.0] * 25 + [80.0] * 5
        self.assertGreater(CQ.bb_stat(times, vals, 50.0), 3)
        self.assertEqual(CQ.bb_stat(times, [50.0] * 30, 50.0), 0.0)

    def test_calibration_respects_budget(self):
        null = [{t: float(i) for t in range(3)} for i in range(10)]
        h = CQ.calibrate(null, 0.2)
        self.assertLessEqual(sum(max(p.values()) > h for p in null), 2)

    def test_qualify_small_run_and_engine_note(self):
        doc = CQ.qualify(seed=3, n=12, budget=0.2)
        self.assertEqual(len(doc['results']), len(CQ.METHODS) * len(CQ.VARIANTS))
        self.assertIn(doc['qualified_method'], CQ.METHODS)
        gap = {(r['method'], r['variant']): r['gap_false_alarm_rate'] for r in doc['results']}
        self.assertLessEqual(gap[('cusum', 'gap_aware')], gap[('cusum', 'naive')])  # gap awareness removes gap alarms
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'q.json'; p.write_text(json.dumps(doc))
            loaded = CQ.verdict(p)
            self.assertIn('Change-point qualification', CQ.note(loaded))
            sig = CQ.chart_signals([{'month': f'2029-{i:02d}', 'z': 0.0 if i < 7 else 4.0} for i in range(1, 13)], doc=loaded)
            self.assertTrue(sig['signal_months'])
        self.assertIsNone(CQ.verdict('/nonexistent/x.json'))

    def test_decide_keep_rule(self):
        mk = lambda m, v, d, g: {'method': m, 'variant': v, 'median_delay_weeks': d, 'gap_false_alarm_rate': g, 'threshold': 1,
                                 'false_alarm_rate': 0.1, 'lag_false_alarm_rate': 0.1, 'detection_rate': 0.9}
        base = [mk('cusum', 'naive', 5, 0.5), mk('cusum', 'gap_aware', 5, 0.05), mk('page_hinkley', 'naive', 6, 0.5),
                mk('page_hinkley', 'gap_aware', 6, 0.05), mk('ewma', 'naive', 4.5, 0.05), mk('ewma', 'gap_aware', 4.5, 0.05)]
        fast = base + [mk('bayesian_blocks', 'naive', 3, 0.6), mk('bayesian_blocks', 'gap_aware', 3, 0.05)]
        d = CQ.decide(fast, 0.1)
        self.assertEqual((d['qualified_method'], d['qualified_variant']), ('bayesian_blocks', 'gap_aware'))
        slow = base + [mk('bayesian_blocks', 'naive', 4.5, 0.6), mk('bayesian_blocks', 'gap_aware', 4.5, 0.05)]
        self.assertEqual(CQ.decide(slow, 0.1)['qualified_method'], 'cusum')  # 10% faster does not meet the 20% rule


if __name__ == '__main__':
    unittest.main()
