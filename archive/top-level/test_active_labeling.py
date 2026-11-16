import unittest
from clinical_core.agere.clinical import active_labeling as al


class ActiveLabelingTests(unittest.TestCase):
    def pool(self):
        items = [{'unit_id': f'a{i}', 'text': 'patient reports chest pain on exertion today'} for i in range(20)]   # near-duplicates
        items += [{'unit_id': f'b{i}', 'text': f'unique note number {i} about topic{i} and thing{i}'} for i in range(20)]
        items += [{'unit_id': f'c{i}', 'text': 'routine callback left'} for i in range(10)]
        return items

    def test_uncertain_and_diverse_beat_duplicates_and_audit_is_confident(self):
        unc = lambda x: 0.9 if x['unit_id'][0] in 'ab' else 0.05
        chosen, rep = al.select(self.pool(), 20, unc)
        ids = [c['unit_id'] for c in chosen]
        self.assertEqual(len(ids), 20)
        self.assertLessEqual(sum(i.startswith('a') for i in ids), 3)       # near-duplicates are worth ~one label
        self.assertGreaterEqual(sum(i.startswith('b') for i in ids), 12)
        audit = [c for c in chosen if c['why'] == 'audit']
        self.assertEqual(len(audit), 2)
        self.assertTrue(all(c['uncertainty'] < 0.5 for c in audit))
        self.assertEqual(rep['by_why']['audit'], 2)

    def test_skips_labelled_and_never_exceeds_pool(self):
        chosen, rep = al.select(self.pool(), 1000, lambda x: 0.5, labelled={'a0', 'a1'})
        self.assertEqual(len(chosen), 48)
        self.assertNotIn('a0', {c['unit_id'] for c in chosen})

    def test_margin_and_entropy(self):
        self.assertAlmostEqual(al.margin_uncertainty([0.5, 0.5]), 1.0)
        self.assertAlmostEqual(al.margin_uncertainty([0.99, 0.01]), 0.02)
        self.assertAlmostEqual(al.entropy([0.5, 0.5]), 1.0)
        self.assertEqual(al.entropy([1.0, 0.0]), 0.0)


if __name__ == '__main__':
    unittest.main()
