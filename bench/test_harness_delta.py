import unittest

from bench import harness_delta as H


class Delta(unittest.TestCase):
    def test_pass_at_k(self):
        self.assertEqual(H.pass_at_k([1, 0, 0, 0], 1), .25)
        self.assertEqual(H.pass_at_k([1, 0, 0, 0], 4), 1.0)
        self.assertEqual(H.pass_at_k([0, 0, 0], 2), 0.0)

    def test_extra_compute_alone_is_not_transferable(self):
        ids = [f'q{i}' for i in range(60)]
        # harness spends 3 calls and gets 2/3 right; the bare arm gets 1/3 per attempt (3 attempts) so best-of-3 matches it
        harness = {i: {'score': 1 if n % 3 else 0, 'calls': 3} for n, i in enumerate(ids)}
        bare = {i: {'scores': [1 if (n + a) % 3 == 0 else 0 for a in range(3)], 'calls': 1} for n, i in enumerate(ids)}
        out = H.compare(harness, {'bare': bare})
        self.assertEqual(out['controls']['bare']['k'], 3)
        self.assertEqual(out['controls']['bare']['score_pass_at_k'], 1.0)
        self.assertEqual(out['matched_delta']['verdict'], 'not distinguishable from the strongest control')

    def test_real_gain_and_shortcut_share(self):
        ids = [f'q{i}' for i in range(80)]
        harness = {i: {'score': 1, 'calls': 2} for i in ids}
        bare = {i: {'scores': [0, 0], 'calls': 1} for i in ids}
        copy = {i: {'score': 1 if n < 20 else 0, 'calls': 0} for n, i in enumerate(ids)}
        out = H.compare(harness, {'bare': bare, 'copy': copy})
        self.assertEqual(out['controls']['copy']['shortcut_share'], .25)
        self.assertEqual(out['matched_delta']['against'], 'copy'); self.assertEqual(out['matched_delta']['verdict'], 'transferable')

    def test_missing_ids_refused(self):
        with self.assertRaises(ValueError): H.compare({'a': {'score': 1}}, {'c': {}})


if __name__ == '__main__':
    unittest.main()
