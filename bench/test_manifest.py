import json
from pathlib import Path
import tempfile
import unittest

from bench import manifest


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.q = d / 'questions.jsonl'; self.q.write_text('{"id": "A-1"}\n')
        self.scorer = d / 'eval_scorer.py'; self.scorer.write_text("SCORER_VERSION = 'scorer/3'\n")
        self.data = d / 'semantic.sqlite3'; self.data.write_bytes(b'data-v1')
        self.docs = d / 'docs'; self.docs.mkdir(); (self.docs / 'a.md').write_text('alpha')

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **kw):
        args = dict(questions_file=self.q, scorer_files=[self.scorer], data=[self.data], docs=[self.docs],
                    model_settings={'model': 'claude-haiku-5-5', 'max_tools': 8}, cache_policy='off', system='frontier_semantic')
        args.update(kw)
        return manifest.build(**args)


class ManifestPins(Fixture):
    def test_pins_every_input(self):
        m = self.build()
        p = m['pinned']
        self.assertEqual(m['schema'], manifest.SCHEMA)
        self.assertEqual(p['scorer']['declared_version'], {'eval_scorer.py': 'scorer/3'})
        self.assertEqual(p['data'][0]['hashed'], 'file')
        self.assertEqual(p['docs'][0]['files'], 1)
        self.assertEqual(p['cache_policy'], 'off')
        self.assertIn('head', p['code'])
        self.assertEqual(m['pinned_sha256'], manifest.sha256_json(p))

    def test_same_inputs_are_comparable(self):
        self.assertEqual(manifest.diff(self.build(), self.build()), [])

    def test_any_changed_input_is_named(self):
        base = self.build()
        (self.docs / 'a.md').write_text('beta')
        self.data.write_bytes(b'data-v2')
        self.scorer.write_text("SCORER_VERSION = 'scorer/4'\n")
        changed = manifest.diff(base, self.build(model_settings={'model': 'claude-opus-5-5', 'max_tools': 8}, cache_policy='replay_allowed'))
        self.assertEqual(changed, ['cache_policy', 'data', 'docs', 'model_settings', 'scorer'])

    def test_compare_separates_the_ruler_from_the_contestant(self):
        base = self.build()
        (self.docs / 'a.md').write_text('beta')
        c = manifest.compare(base, self.build(model_settings={'model': 'claude-opus-5-5', 'max_tools': 8}))
        self.assertEqual(c, {'comparable': True, 'measurement_changed': [], 'contestant_changed': ['docs', 'model_settings']})
        self.scorer.write_text("SCORER_VERSION = 'scorer/4'\n")
        c = manifest.compare(base, self.build())
        self.assertFalse(c['comparable'])
        self.assertEqual(c['measurement_changed'], ['scorer'])

    def test_unknown_cache_policy_is_refused(self):
        with self.assertRaises(ValueError):
            self.build(cache_policy='whatever')

    def test_missing_data_is_refused(self):
        with self.assertRaises(FileNotFoundError):
            self.build(data=[self.data.with_name('absent.sqlite3')])

    def test_write_and_hash_cache(self):
        cache = {}
        m = self.build(cache=cache)
        self.assertTrue(cache)
        path = manifest.write(self.tmp.name, m)
        self.assertEqual(json.loads(path.read_text())['pinned_sha256'], m['pinned_sha256'])


class CostsFailClosed(unittest.TestCase):
    def test_unknown_cost_is_never_summed_as_zero(self):
        s = manifest.cost_summary([{'id': 'a', 'estimated_cost_usd': 0.02}, {'id': 'b', 'estimated_cost_usd': None}])
        self.assertIsNone(s['total_usd'])
        self.assertFalse(s['complete'])
        self.assertEqual((s['known_paid_usd'], s['unknown_cost_rows'], s['unknown_cost_ids']), (0.02, 1, ['b']))
        self.assertIn('UNKNOWN', manifest.format_cost(s))

    def test_invalid_costs_are_unknown(self):
        for bad in ('0.01', -1, float('nan'), True):
            self.assertEqual(manifest.cost_class({'estimated_cost_usd': bad}), 'unknown', bad)

    def test_incomplete_product_usage_is_unknown_even_with_a_number(self):
        self.assertEqual(manifest.cost_class({'estimated_cost_usd': 0.0, 'cost_scope': 'incomplete_product_usage'}), 'unknown')

    def test_cached_replays_are_reported_apart(self):
        rows = [{'id': 'a', 'estimated_cost_usd': 0.03},
                {'id': 'b', 'estimated_cost_usd': 0.0, 'new_paid_api_calls': 0},
                {'id': 'c', 'cached': True, 'estimated_cost_usd': 0.0}]
        s = manifest.cost_summary(rows)
        self.assertEqual((s['paid_rows'], s['replayed_rows'], s['total_usd']), (1, 2, 0.03))

    def test_rows_not_run_are_neither_paid_nor_unknown(self):
        s = manifest.cost_summary([{'id': 'a', 'error': 'skipped: budget reached', 'estimated_cost_usd': None},
                                   {'id': 'b', 'estimated_cost_usd': 0.01}])
        self.assertEqual((s['not_run_rows'], s['total_usd']), (1, 0.01))

    def test_unmetered_subagent_rows_are_unknown(self):
        s = manifest.cost_summary([{'id': 'a', 'status': 'answered'}])
        self.assertIsNone(s['total_usd'])


if __name__ == '__main__':
    unittest.main()
