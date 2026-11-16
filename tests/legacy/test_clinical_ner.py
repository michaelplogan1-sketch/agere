import unittest
from clinical_core.agere.clinical import clinical_ner as N, note_facts


class NerTests(unittest.TestCase):
    def test_spans_expand_to_whole_words_and_mapping(self):
        ents = N.entities('Patient has orthopnea and chest pain. Furosemide was stopped.')
        texts = {e['text'].lower() for e in ents}
        self.assertTrue(any('furosemide' == t for t in texts), texts)   # sub-word pieces expanded, not 'mide'
        txt = 'Patient has orthopnea and chest pain. Furosemide was stopped.'
        for e in ents:   # every span starts and ends on a word boundary
            self.assertTrue(e['start'] == 0 or not txt[e['start'] - 1].isalnum()); self.assertTrue(e['end'] == len(txt) or not txt[e['end']].isalnum())

    def test_candidates_use_negation_and_skip_hypothetical(self):
        t = 'Denies any chest pain today.'; i = t.index('chest pain')
        ents = [{'kind': 'disease', 'text': 'chest pain', 'start': i, 'end': i + 10, 'score': 0.99}]
        f = N.candidates(t, None, None, ents)
        self.assertEqual([(x['value'], x['negated']) for x in f], [('chest_pain', True)])
        t2 = 'Return if chest pain develops.'; j = t2.index('chest pain')
        self.assertEqual(N.candidates(t2, None, None, [{'kind': 'disease', 'text': 'chest pain', 'start': j, 'end': j + 10, 'score': .99}]), [])

    def test_qualifier_fill_from_chem_span_and_admission_gate(self):
        t = 'Patient says not taking furosemide.'
        i = t.index('furosemide')
        ents = [{'kind': 'chem', 'text': 'furosemide', 'start': i, 'end': i + 10, 'score': 0.99}]
        facts = [{'fact_type': 'medication_nonadherence', 'value': 'unspecified', 'qualifier': None, 'span_start': 8, 'span_end': 20, 'negated': False}]
        self.assertEqual(N.qualifiers(t, facts, ents), 1)
        self.assertEqual(facts[0]['qualifier'], 'furosemide')
        # nothing is added unless the type is admitted (admission file / allowed override)
        self.assertEqual(N.augment('Denies chest pain.', [], allowed=[]), [])
        self.assertIn('medication_qualifier', N.admitted())     # measured 14/14 against the Sonnet labels

    def test_extract_runs_with_ner_wired(self):
        facts = note_facts.extract('Reports use on 4 of the last 7 days.', 'notes', 'text')
        self.assertTrue(all('fact_type' in f for f in facts))


if __name__ == '__main__':
    unittest.main()
