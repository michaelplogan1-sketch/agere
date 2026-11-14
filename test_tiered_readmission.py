"""Model-free checks for the readmission path: admission statements found by code and assigned to the right stay."""
import unittest
from unittest import mock

from clinical_core.agere.tiered import readmission, recipes, split

STAYS = ('2030-04-23', '2030-04-29', '2030-05-07', '2030-05-10')  # index admit, index discharge, readmit admit, readmit discharge


def doc(text, date):
    return {'text': text, 'event_date': date}


class Readmission(unittest.TestCase):
    def test_a_document_is_assigned_by_the_admission_date_it_states_then_by_its_own_date(self):
        which = lambda text, when: readmission.which_stay(text, when, *STAYS)
        self.assertEqual(which('Admission date: 2030-05-07 Discharge date: 2030-05-10 Principal diagnosis: Opioid overdose', '2030-05-12'), 'readmission')
        self.assertEqual(which('Admission date: 2030-04-23 Discharge date: 2030-04-29 Principal diagnosis: Cellulitis', '2030-04-30'), 'stay before the readmission')
        self.assertEqual(which('Admission date: 2030-04-16 Discharge date: 2030-04-27 Principal diagnosis: Heart failure', '2030-04-30'), 'another stay')  # a decoy dated in the window
        self.assertEqual(which('presented to the ED with drug overdose. Admitted.', '2030-05-10'), 'readmission')
        self.assertEqual(which('Clinic follow-up note.', '2030-05-04'), 'between the two stays')
        self.assertEqual(which('Discharge paperwork.', '2030-05-01'), 'stay before the readmission')  # a discharge document can arrive a few days late
        self.assertEqual(readmission.which_stay('ED summary.', '2030-05-08', '', '', '2030-05-07', ''), 'readmission')  # no prior stay on record

    def test_labelled_fields_are_quoted_by_code_and_loose_sentences_are_left_for_a_reader(self):
        texts = {'R1': doc('Admission date: 2030-05-07 Discharge date: 2030-05-10 Principal diagnosis: Opioid overdose Hospital course: Responded to naloxone. Disposition: home', '2030-05-12'),
                 'R2': doc('Readmission within 30 days of prior discharge. 67 y/o presented to the ED with drug overdose. Evaluated; workup unremarkable. Admitted for further management.', '2030-05-10'),
                 'R3': doc('67 y/o presented to the ED with shortness of breath. IV diuretic given. Treated and released with instructions to follow up.', '2030-04-25')}
        found = readmission.statements(texts, STAYS)
        fields = [f for f in found if f['by_code']]
        self.assertEqual([(f['stay'], f['text']) for f in fields], [('readmission', 'Principal diagnosis: Opioid overdose')])
        loose = [f for f in found if not f['by_code']]
        self.assertEqual({f['record_id'] for f in loose}, {'R2'}); self.assertTrue(any('presented to the ED with drug overdose' in f['text'] for f in loose))
        self.assertFalse(any(f['record_id'] == 'R3' for f in found))  # treated and released: not an admission

    def test_the_question_splits_into_three_recipe_questions_with_or_without_a_date(self):
        with mock.patch.object(split, 'latest_readmission', lambda pid: '2030-05-07'):
            parts, how = split.known('Why was patient WHP-85D78D3616CC readmitted?')
        self.assertEqual(how['date_from'], 'most recent readmission on record'); self.assertEqual(len(parts), 3); self.assertTrue(all('2030-05-07' in p for p in parts))
        parts, how = split.known('Why did patient WHP-85D78D3616CC come back to the hospital on 2030-02-21?')
        self.assertEqual(how['date_from'], 'question'); self.assertTrue(all('2030-02-21' in p for p in parts))
        with mock.patch.object(split, 'latest_readmission', lambda pid: None):
            self.assertIsNone(split.known('Why was patient P-NONE readmitted?'))
        self.assertIsNone(split.known('Why was the readmission rate higher in 2029?'))

    def test_recipe_patterns_and_the_date_parameter(self):
        for rid, q in (('readmission_stays', "What are patient P-1's readmission on 2030-05-07 and the stay before it?"),
                       ('readmission_reasons_in_documents', "What do patient P-1's documents state about why the patient was admitted on 2030-05-07?"),
                       ('readmission_interval_events', "What is on record for patient P-1 between the prior discharge and the admission on 2030-05-07?")):
            self.assertTrue(any(__import__('re').fullmatch(p, q.rstrip('?').lower()) for p in recipes.RECIPES[rid]['patterns']), rid)
        self.assertIn(':readmit_date', recipes.RECIPES['readmission_stays']['sql'])

    def test_the_summary_is_written_by_code_and_keeps_each_statement_with_its_stay(self):
        from clinical_core.agere.tiered import app
        answers = [{'table': [{'stay': 'stay before the readmission', 'admit_date': '2030-04-23', 'discharge_date': '2030-04-29', 'days_after_prior_discharge': -1},
                              {'stay': 'readmission', 'admit_date': '2030-05-07', 'discharge_date': '2030-05-10', 'days_after_prior_discharge': 8}]},
                   {'table': [{'stay': 'readmission', 'stated_as': 'principal diagnosis', 'passage': 'Principal diagnosis: Opioid overdose'},
                              {'stay': 'stay before the readmission', 'stated_as': 'principal diagnosis', 'passage': 'Principal diagnosis: Cellulitis of lower extremity'},
                              {'stay': 'another stay', 'stated_as': 'principal diagnosis', 'passage': 'Principal diagnosis: Acute on chronic heart failure'}]},
                   {'table': [{'event': 'pharmacy fills', 'on_record': 0}]}]
        text = app._readmission_headline(answers)
        self.assertIn('Admitted 2030-05-07, discharged 2030-05-10, 8 days after discharge', text)
        self.assertIn('For this admission the record states: "Principal diagnosis: Opioid overdose"', text)
        self.assertIn('For the stay before it the record states: "Principal diagnosis: Cellulitis of lower extremity"', text)
        self.assertNotIn('heart failure', text)  # a statement about another stay is not offered as the reason
        self.assertIn('No document states why', app._readmission_headline([answers[0], {'table': []}, {'table': []}]))


if __name__ == '__main__':
    unittest.main()
