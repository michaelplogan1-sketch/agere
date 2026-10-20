"""Regression for briefs whose prefix getters stopped rendering after static acceptance."""
import unittest
from clinical_core.agere.deliverables.receipts import Bundle
from clinical_core.agere.deliverables.runtime import wrap
from clinical_core.agere.deliverables.verify import verify


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


if __name__ == '__main__': unittest.main()
