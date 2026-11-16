import json, tempfile, unittest
from pathlib import Path
import numpy as np
from clinical_core.agere.clinical import vcache


class VCacheTests(unittest.TestCase):
    def test_fit_from_labelled_cases_gives_entry_cuts(self):
        rows, ident = vcache.cases_from_file()
        doc = vcache.fit(rows, encoder_identity=ident, global_threshold=0.9)
        self.assertGreater(doc['observations'], 100)
        cuts = [e['threshold'] for e in doc['entries'].values() if e['threshold'] is not None]
        self.assertTrue(cuts)
        self.assertTrue(all(c >= vcache.FLOOR for c in cuts))
        self.assertGreater(len(set(round(c, 2) for c in cuts)), 1)  # entries differ
        self.assertEqual(doc['global']['basis'], 'rule_of_three')

    def test_entry_without_evidence_keeps_global(self):
        with tempfile.TemporaryDirectory() as d:
            t, r = vcache.entry_threshold(d, 'never seen question', 0.91)
            self.assertEqual(t, 0.91)

    def test_online_error_raises_cut(self):
        with tempfile.TemporaryDirectory() as d:
            base = 0.91
            for s in (0.95, 0.96, 0.97): vcache.observe(d, 'q a', s, True, 'test')
            # prior needs a fitted global; with no table the default stands
            vcache.observe(d, 'q a', 0.93, False, 'user_correction')
            t, r = vcache.entry_threshold(d, 'q a', base)
            self.assertGreaterEqual(t, 0.9325)  # above the observed error similarity

    def test_correction_logs_error_for_reused_entry(self):
        with tempfile.TemporaryDirectory() as d:
            vcache.log_reuse(d, 'new q', 'old q', 0.92, {'query': 'select 1'})
            self.assertEqual(vcache.on_correction(d, 'new q'), 1)
            self.assertEqual(vcache.online_observations(d, 'old q')[0]['correct'], False)
            vcache.log_reuse(d, 'q2', 'old q', 0.95, {'query': 'select 1'})
            vcache.on_verified(d, 'q2', {'query': 'select 2'})
            self.assertEqual(len(vcache.online_observations(d, 'old q')), 2)

    def test_disabled(self):
        import os
        os.environ['AGERE_VCACHE'] = 'off'
        try:
            self.assertEqual(vcache.entry_threshold('/nonexistent', 'x', 0.9)[0], 0.9)
        finally: del os.environ['AGERE_VCACHE']

    def test_lookup_uses_entry_cut(self):
        from clinical_core.agere.clinical import semantic_cache, learning
        class Enc:
            identity = 'dummy'
            def encode(self, qs):
                # first = new question; others = candidates, cosine ~0.80 with the first
                out = [[1.0, 0.0]]
                for _ in qs[1:]: out.append([0.8, 0.6])
                return np.array(out)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'run.json').write_text(json.dumps({'source_digest': 'fixture'}))
            q = 'How many patients with diabetes had an A1c above 9 in 2028?'
            learning.record_verified(d, q, 'sql', {'query': 'select 1'}, {}, 'r', [], 'x')
            para = 'how many pts w/ diabetes had A1c > 9 in 2028'
            hit, rc = semantic_cache.lookup(d, para, Enc())
            self.assertIn('vcache', rc)
            if hit is None:  # below the global cut -> a correction raises nothing; ensure the receipt carries both cuts
                self.assertIn('global_threshold', rc)
            else:
                vcache.log_reuse(d, para, q, 0.8, {'query': 'select 1'})
            hit2, rc2 = semantic_cache.lookup(d, para, Enc(), threshold=0.5)  # pinned: vcache off the path
            self.assertNotIn('vcache', rc2)
            self.assertEqual(hit2['plan'], {'query': 'select 1'})


if __name__ == '__main__':
    unittest.main()
