"""Source attribution does not request grouped rows or remove date/number requirements."""
import unittest
from clinical_core.agere.clinical import analysis_graph as G, answer_shape as S, literal_check as L


class SourceQualifiers(unittest.TestCase):
    def test_scalar_counts_with_dated_or_typed_sources(self):
        for source in ('the May 2027 callback', 'the discharge summary', 'the latest clinical note',
                       'the outside radiology report', 'the 2027-05-14 report', 'records'):
            q = 'How many days did P-1 use the inhaler, per ' + source + '?'
            with self.subTest(source=source):
                self.assertEqual(G.requested_shape(q), 'count')
                self.assertEqual(S.requested(q)['shape'], 'count')
                self.assertNotIn('the question asks for a breakdown; the plan has no grouping',
                                 L.missing(q, L.executable_text({'returns':'count','window':{'start':'2027-05-01','end':'2027-05-31'},'days':14})))

    def test_other_shapes_keep_their_output(self):
        self.assertEqual(G.requested_shape('What was the average cost per the latest report?'), 'value')
        self.assertEqual(G.requested_shape('Which patients took the drug per the May note?'), 'patient_set')
        self.assertEqual(S.requested('Which patients took the drug per the May note?')['shape'], 'patient_set')

    def test_actual_groupings_survive_even_beside_a_source_qualifier(self):
        for q in ('How many visits per practice?', 'How many visits per insurer?',
                  'How many visits by payer, per the May report?',
                  'Give one row per note', 'How many claims per report?', 'How many events per callback?', 'Compare counts for each practice per the discharge summary'):
            with self.subTest(q=q): self.assertEqual(G.requested_shape(q), 'table')

    def test_original_scope_constraints_are_still_required(self):
        q = 'How many of the last 14 days did P-1 use the inhaler, per the May 2027 callback?'
        gaps = L.missing(q, 'count')
        self.assertTrue(any('14' in x for x in gaps))
        self.assertTrue(any('2027' in x for x in gaps))
        self.assertIn('month may is not in the plan', gaps)
        self.assertEqual(S.shape_wording(q), 'How many of the last 14 days did P-1 use the inhaler, using source?')
