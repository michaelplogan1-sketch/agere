"""Regression for briefs whose prefix getters stopped rendering after static acceptance."""
import unittest
import copy, importlib
from clinical_core.agere.deliverables.receipts import Bundle
from clinical_core.agere.deliverables.runtime import wrap
from clinical_core.agere.deliverables.verify import verify
from clinical_core.agere.deliverables.receipts import native_binding_path
C = importlib.import_module('clinical_core.agere.deliverables.compose')


class FrozenLookupPaths(unittest.TestCase):
    def setUp(self):
        self.bundle = Bundle({'frozen_evidence': True})
        self.rid = self.bundle.add('saved_answer', {}, {
            'answer': {'table': [{'payment': -246450}], 'warnings': []},
            'source': {'as_of': '2030-05-27'}, 'workflow': [], 'stale': False, 'year': 2026,
        })

    def check(self, script):
        return verify(wrap('<p id="result"></p><script>'+script+'</script>', self.bundle, 'Brief'), self.bundle)

    def test_actual_wrong_root_prefix_pattern_is_rejected(self):
        report = self.check("var A='"+self.rid+":answer.',ga=function(p){return AGERE.get(A+p)};ga('source.as_of');")
        self.assertFalse(report['passed'])
        self.assertIn('dynamic_data_lookup', [v['kind'] for v in report['violations']])

    def test_missing_literal_path_is_rejected(self):
        for path in ('answer.source.as_of', 'answer.workflow', 'answer.table.1.payment', 'answer.table.0.missing'):
            with self.subTest(path=path):
                report = self.check("AGERE.get('"+self.rid+':'+path+"');")
                self.assertIn('script_binding_unresolved', [v['kind'] for v in report['violations']])

    def test_known_literal_paths_and_null_like_values_work(self):
        script = ';'.join("AGERE.get('"+self.rid+':'+p+"')" for p in ('answer.table.0.payment','answer.warnings','source.as_of','workflow','stale'))
        report = self.check(script)
        self.assertTrue(report['passed'], report['violations'])

    def test_literal_prefix_followed_by_concatenation_is_not_accepted(self):
        report = self.check("var field='source.as_of';AGERE.get('"+self.rid+":'+field);")
        self.assertIn('dynamic_data_lookup', [v['kind'] for v in report['violations']])

    def test_browser_title_year_must_exist_in_evidence(self):
        good = verify(wrap('<p>Scenario</p>', self.bundle, 'Settlement 2026'), self.bundle)
        bad = verify(wrap('<p>Scenario</p>', self.bundle, 'Settlement 2099'), self.bundle)
        self.assertTrue(good['passed'], good['violations'])
        self.assertIn('title_number_not_in_data', [v['kind'] for v in bad['violations']])

    def test_body_year_still_needs_explicit_binding(self):
        report = verify(wrap('<p>Settlement 2026</p>', self.bundle, 'Settlement'), self.bundle)
        self.assertIn('unbound_number', [v['kind'] for v in report['violations']])

    def test_exact_compact_column_addresses_heal_to_native_paths_without_value_change(self):
        b = Bundle({'frozen_evidence': True})
        rid = b.add('saved_answer', {}, {'measures': [
            {'name': 'observed costs', 'sources': {'total': {'value': 17}}},
            {'name': 'observed visits', 'sources': {'total': {'value': None}}}]})
        before = copy.deepcopy(b.to_json())
        old = rid + ':measures.0.1.total.value'
        native = rid + ':measures.0.sources.total.value'
        fragment = f'<span data-bind="{old}"></span><script>AGERE.get(\'{old}\');</script>'
        healed, repairs = C._native_bindings(fragment, b)
        self.assertEqual(native_binding_path(b, old), native)
        self.assertEqual([r['to'] for r in repairs], [native, native])
        self.assertTrue(verify(wrap(healed, b, 'Observed evidence'), b)['passed'])
        self.assertEqual(b.to_json(), before)
        self.assertEqual(b.get(native), 17)
        self.assertIsNone(b.get(native_binding_path(b, rid + ':measures.1.1.total.value')))
        self.assertEqual(C._native_bindings(healed, b), (healed, []))

    def test_aliases_cannot_guess_unknown_fields_heterogeneous_rows_or_source_roots(self):
        b = Bundle({'frozen_evidence': True})
        rid = b.add('saved_answer', {}, {'rows': [{'count': 17}, {'other': 23}],
                                       'one': [{'count': 17}], 'object': {'count': 17}})
        for path in ('rows.0.0', 'one.0.0', 'object.0', 'rows.0.unknown', 'wrong.rows.0.count'):
            with self.subTest(path=path):
                old = rid + ':' + path
                with self.assertRaises(KeyError): native_binding_path(b, old)
                fragment = f'<span data-bind="{old}"></span>'
                self.assertEqual(C._native_bindings(fragment, b), (fragment, []))
                self.assertFalse(verify(wrap(fragment, b, 'Observed evidence'), b)['passed'])

    def test_literal_native_numeric_keys_and_arrays_are_never_reinterpreted(self):
        b = Bundle({'frozen_evidence': True})
        rid = b.add('saved_answer', {}, {'rows': [{'count': 17, '0': 23}, {'count': 31, '0': 41}],
                                       'array': [[2, 3], [5, 7]]})
        for tail, expected in [('rows.0.0', 23), ('array.0.1', 3), ('rows.1.count', 31)]:
            path = rid + ':' + tail
            self.assertEqual(native_binding_path(b, path), path)
            self.assertEqual(b.get(path), expected)

    def test_unknown_column_indexes_and_dynamic_lookups_remain_rejected(self):
        b = Bundle({'frozen_evidence': True})
        rid = b.add('saved_answer', {}, {'rows': [{'count': 17}, {'count': 23}]})
        for tail in ('rows.0.9', 'rows.9.0', 'rows.0.0.unknown'):
            with self.assertRaises(KeyError): native_binding_path(b, rid + ':' + tail)
        fragment = "<script>var p='rows.0.0';AGERE.get('" + rid + ":'+p);</script>"
        healed, repairs = C._native_bindings(fragment, b)
        self.assertEqual((healed, repairs), (fragment, []))
        self.assertIn('dynamic_data_lookup', [v['kind'] for v in verify(wrap(healed, b, 'Observed evidence'), b)['violations']])


if __name__ == '__main__': unittest.main()
