import unittest

from bench import interactive_run as I


def res(ids=None, count=None, status='answered'):
    return {'answer': {'status': status, 'patient_ids': ids, 'count': count}}


class Judge(unittest.TestCase):
    def test_chains_file_is_valid(self):
        chains = I.load()
        self.assertGreaterEqual(len(chains), 5)
        self.assertEqual({c['failure_mode'] for c in chains}, {'FM-20', 'FM-21', 'FM-22'})

    def test_slot_kept_uses_independent_oracle(self):
        first, women = I.observe(res(['a', 'b', 'c'])), I.observe(res(['a', 'c']))
        t = {'check': 'slot_kept', 'of': 0}
        self.assertTrue(I.judge(t, women, first, {'a', 'c', 'z'})[0])
        self.assertFalse(I.judge(t, I.observe(res(['a', 'b'])), first, {'a', 'c'})[0])  # dropped the slot filter
        self.assertFalse(I.judge(t, I.observe(res(['a', 'c', 'z'])), first, {'a', 'c', 'z'})[0])  # ignored the earlier population

    def test_stale_reuse_and_repeat(self):
        a, b = I.observe(res(count=10)), I.observe(res(count=10))
        self.assertFalse(I.judge({'check': 'no_stale_reuse', 'of': 0}, b, a)[0])
        self.assertTrue(I.judge({'check': 'reuse_consistent', 'of': 0}, b, a)[0])
        self.assertFalse(I.judge({'check': 'reuse_consistent', 'of': 0}, I.observe(res(count=11)), a)[0])

    def test_errors_and_empty_fail(self):
        a = I.observe(res(count=3))
        self.assertFalse(I.judge({'check': 'reuse_consistent', 'of': 0}, I.observe(exc=RuntimeError('x')), a)[0])
        self.assertFalse(I.judge({'check': 'reuse_consistent', 'of': 0}, I.observe(res(status='unknown')), a)[0])

    def test_run_chains_end_to_end_with_a_fake_stack(self):
        chain = [{'id': 'c', 'failure_mode': 'FM-20', 'turns': [{'q': 'hf'}, {'q': 'women', 'check': 'slot_kept', 'of': 0, 'intersect_sql': 'F'}]}]
        answers = {'hf': res(['a', 'b']), 'women': res(['a'])}
        rows = I.run_chains(chain, lambda cid, q: answers[q], lambda sql: {'a'})
        self.assertTrue(rows[1]['passed']); self.assertEqual(I.summarize(rows)['chains_fully_passed'], 1)
        def boom(cid, q): raise RuntimeError('down')
        self.assertFalse(I.run_chains(chain, boom, lambda s: set())[1]['passed'])


if __name__ == '__main__':
    unittest.main()
