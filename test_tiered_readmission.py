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
        fields = [f for f in found if f['by_code'] and f['kind'] == 'principal diagnosis']
        self.assertEqual([(f['stay'], f['text']) for f in fields], [('readmission', 'Principal diagnosis: Opioid overdose')])
        loose = [f for f in found if not f['by_code']]
        self.assertEqual({f['record_id'] for f in loose}, {'R2'}); self.assertTrue(any('presented to the ED with drug overdose' in f['text'] for f in loose))
        self.assertFalse(any(f['record_id'] == 'R3' for f in found))  # treated and released: not an admission

    def test_the_question_splits_into_three_recipe_questions_with_or_without_a_date(self):
        with mock.patch.object(split, 'latest_readmission', lambda pid: '2030-05-07'):
            parts, how = split.known('Why was patient WHP-85D78D3616CC readmitted?')
        self.assertEqual(how['date_from'], 'most recent readmission on record'); self.assertEqual(len(parts), 5); self.assertTrue(all('2030-05-07' in p for p in parts))
        parts, how = split.known('Why did patient WHP-85D78D3616CC come back to the hospital on 2030-02-21?')
        self.assertEqual(how['date_from'], 'question'); self.assertTrue(all('2030-02-21' in p for p in parts))
        with mock.patch.object(split, 'latest_readmission', lambda pid: None):
            self.assertIsNone(split.known('Why was patient P-NONE readmitted?'))
        self.assertIsNone(split.known('Why was the readmission rate higher in 2029?'))

    def test_recipe_patterns_and_the_date_parameter(self):
        with mock.patch.object(split, 'latest_readmission', lambda pid: '2030-05-07'):
            parts, _ = split.known('Review the readmission of patient P-1 on 2030-05-07: why were they readmitted and did they get follow-up?')
        self.assertEqual(len(parts), 5)
        for rid, q in zip(('readmission_stays', 'readmission_chart_statements', 'readmission_follow_up', 'readmission_risk_factors', 'readmission_labs'), parts):
            self.assertTrue(any(__import__('re').fullmatch(p, q.rstrip('?').lower()) for p in recipes.RECIPES[rid]['patterns']), rid)
        self.assertIn(':readmit_date', recipes.RECIPES['readmission_stays']['sql'])

    def test_instructions_imaging_and_directives_are_quoted_with_the_right_scope(self):
        texts = {'D1': doc('Admitted with fluid overload; diuresed. Disposition: Home today. Primary follow up recommended around 2030-05-02. Principal problem this admission: I50.', '2030-04-29'),
                 'X1': doc('EXAM: XRAY\nFINDINGS: See impression.\nIMPRESSION: Mild pulmonary edema. Electronically signed', '2030-05-08'),
                 'OLD': doc('Clinic note. Code status: DNR/comfort care. Family at bedside.', '2027-01-01')}
        kinds = {'D1': ('clinical_note', 'discharge_summary'), 'X1': ('scanned_document', 'Radiology Report'), 'OLD': ('clinical_note', 'office_followup')}
        found = readmission.statements(texts, STAYS, kinds)
        by = lambda kind: [(f['stay'], f['text']) for f in found if f['kind'] == kind]
        self.assertEqual(by('follow-up instruction'), [('stay before the readmission', 'Primary follow up recommended around 2030-05-02.')])
        self.assertEqual(by('principal problem this admission'), [('stay before the readmission', 'Principal problem this admission: I50')])
        self.assertEqual(by('imaging impression'), [('readmission', 'IMPRESSION: Mild pulmonary edema.')])
        self.assertEqual(by('advance directive or code status'), [('whole record', 'Code status: DNR/comfort care.')])  # found although far outside the window
        self.assertTrue(any(k == 'disposition' for k in (f['kind'] for f in found)))

    def test_the_summary_is_written_by_code_and_keeps_each_statement_with_its_stay(self):
        from clinical_core.agere.tiered import app
        answers = [{'table': [{'stay': 'stay before the readmission', 'admit_date': '2030-04-23', 'discharge_date': '2030-04-29', 'days_after_prior_discharge': -1},
                              {'stay': 'readmission', 'admit_date': '2030-05-07', 'discharge_date': '2030-05-10', 'days_after_prior_discharge': 8}]},
                   {'table': [{'stay': 'readmission', 'stated_as': 'principal diagnosis', 'passage': 'Principal diagnosis: Opioid overdose'},
                              {'stay': 'stay before the readmission', 'stated_as': 'principal diagnosis', 'passage': 'Principal diagnosis: Cellulitis of lower extremity'},
                              {'stay': 'another stay', 'stated_as': 'principal diagnosis', 'passage': 'Principal diagnosis: Acute on chronic heart failure'}]},
                   {'table': [{'item': 'outpatient visit within 7 days of discharge', 'on_record': 'no', 'first_date': ''}]},
                   {'table': [{'factor': 'age', 'on_record': '67'}, {'factor': 'chronic conditions on record', 'on_record': '3'}, {'factor': 'inpatient admissions in the prior 12 months', 'on_record': '2'},
                              {'factor': 'emergency visits in the prior 6 months', 'on_record': '4'}, {'factor': 'positive social-need screen: food', 'on_record': '2030-01-01'}]},
                   {'table': [{'period': 'readmission', 'concept': '(any lab or vital)', 'results': 0}]}]
        text = app._readmission_headline(answers)
        self.assertIn('Admitted 2030-05-07, discharged 2030-05-10, 8 days after discharge', text)
        self.assertIn('Why admitted, as the record states it: "Principal diagnosis: Opioid overdose"', text)
        self.assertIn('The stay before it: "Principal diagnosis: Cellulitis of lower extremity"', text)
        self.assertNotIn('heart failure', text)  # a statement about another stay is not offered as the reason
        self.assertIn('no outpatient visit is on record within 7 days', text); self.assertIn('positive social-need screens for food', text)
        self.assertIn('No lab or vital result is on record during the readmission', text); self.assertIn('No advance directive or code status is documented', text)
        self.assertIn('No document states why', app._readmission_headline([answers[0], {'table': []}, {'table': []}]))


if __name__ == '__main__':
    unittest.main()
