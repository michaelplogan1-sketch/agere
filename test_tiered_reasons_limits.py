"""Model-free checks for limit statements that name no medication (tiered/reasons.py limits)."""
import unittest

from clinical_core.agere.tiered import reasons


def doc(text, date='2029-01-01'):
    return {'text': text, 'event_date': date}


class Limits(unittest.TestCase):
    def test_a_vital_sign_limit_on_therapy_is_found_without_a_medication_name(self):
        texts = {'R1': doc('Prior encounter on 2026-06-15 recorded: SBP 96 limits titration — frailty, fall last winter.'),
                 'R2': doc('Prior entry dated 2026-06-15: SBP 96 limits titration — frailty, fall last winter.', '2029-02-01')}
        found = reasons.limits(texts, [])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['pillar'], reasons.UNNAMED); self.assertEqual(sorted(found[0]['record_ids']), ['R1', 'R2'])
        self.assertIn('SBP 96 limits titration', found[0]['text']); self.assertEqual(found[0]['first'], '2029-01-01'); self.assertEqual(found[0]['last'], '2029-02-01')

    def test_other_limit_wordings(self):
        for text in ('Potassium 5.6 precludes further uptitration today.', 'Hypotension: unable to increase dose at this visit.',
                     'eGFR 24, hold escalation of therapy until repeat labs.', 'Heart rate 48; cannot titrate further.'):
            self.assertEqual(len(reasons.limits({'R': doc(text)}, [])), 1, text)

    def test_a_sign_or_a_therapy_word_alone_is_not_a_limit_statement(self):
        for text in ('Blood pressure 118/72 today, heart rate 70, potassium 4.2.', 'Plan: continue current therapy and recheck labs in 3 months.',
                     'SBP 96 this morning; patient feels well and is walking daily.', 'Dose increased at the last visit without difficulty.'):
            self.assertEqual(reasons.limits({'R': doc(text)}, []), [], text)

    def test_a_sentence_already_inside_a_medication_passage_is_not_repeated(self):
        text = 'Carvedilol 6.25 mg twice daily. SBP 92 limits titration of carvedilol at this time.'
        have = reasons.passages({'R': doc(text)})
        self.assertTrue(any('limits titration' in p['text'] for p in have))
        self.assertEqual(reasons.limits({'R': doc(text)}, have), [])


if __name__ == '__main__':
    unittest.main()
