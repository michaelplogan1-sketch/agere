import unittest
from clinical_core.agere.clinical import chart_notes as C, system_one_gate as G, note_open as O

class CheckEvidenceContext(unittest.TestCase):
    def pred(self):
        return {'concept': {'term': 'chest x-ray'}, 'return_detail': ['result_text', 'result']}

    def test_impression_keeps_negation_and_uncertainty_ahead_of_letterhead(self):
        quote = ('Facility name and address\n' * 30 +
                 'EXAM: XRAY FINDINGS: See impression. IMPRESSION: No focal consolidation. Cannot exclude early infiltrate.')
        focused = C.focus_quote(quote, self.pred(), limit=180)
        self.assertTrue(focused.startswith('IMPRESSION:'))
        self.assertIn('No focal consolidation', focused)
        self.assertIn('Cannot exclude early infiltrate', focused)
        self.assertIn(focused, quote)

    def test_checker_sees_each_conflicting_source_not_only_row_labels(self):
        result = {'answer': {'answer_type': 'table', 'headline': 'Multiple matching reports',
                  'intent': {'note': [self.pred()]},
                  'table': [{'record_id': 'old'}, {'record_id': 'new'}],
                  'sources': [{'record_id': 'old', 'date': '2028-01-01',
                               'quote': 'Letterhead ' * 30 + 'IMPRESSION: Right lower lobe infiltrate.'},
                              {'record_id': 'new', 'date': '2029-01-01',
                               'quote': 'Letterhead ' * 30 + 'IMPRESSION: No acute cardiopulmonary process.'}]}}
        text = G.render('What was the CXR impression?', {'nodes': []}, result)
        self.assertIn('Right lower lobe infiltrate', text)
        self.assertIn('No acute cardiopulmonary process', text)
        self.assertIn('untrusted source material', text)
        self.assertLessEqual(len(text), 1800)

    def test_generic_report_title_with_all_types_does_not_crash(self):
        self.assertTrue(O.matches_document_type(list(O.NOTE_TYPES), 'generic',
                        'EXAM: XRAY FINDINGS: clear. IMPRESSION: clear.'))

    def test_non_chart_intent_still_renders_its_sources(self):
        text = G.render('q', None, {'answer': {'intent': 'legacy',
                        'sources': [{'record_id': 'r', 'quote': 'Source account.'}]}})
        self.assertIn('Source account.', text)
