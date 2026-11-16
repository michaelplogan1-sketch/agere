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
        # an episode of several rows: the summary names an admission date inside it
        self.assertEqual(readmission.which_stay('Admission date: 2030-04-24 Discharge date: 2030-04-29 Principal diagnosis: COPD', '2030-04-30', *STAYS), 'stay before the readmission')
        self.assertEqual(readmission.which_stay('Admission date: 2030-05-08 Discharge date: 2030-05-10 Principal diagnosis: COPD', '2030-05-11', *STAYS), 'readmission')

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

    def test_the_firmest_statement_of_a_reason_wins_and_a_bare_code_is_named(self):
        self.assertEqual(readmission.stated_reason('Admission date: 2030-05-07 Principal diagnosis: Opioid overdose Hospital course: Responded.'), (0, 'Opioid overdose'))
        self.assertEqual(readmission.stated_reason('Admitted with fluid overload; diuresed with IV furosemide.'), (1, 'fluid overload'))
        self.assertEqual(readmission.stated_reason('67 y/o presented to the ED with drug overdose. Admitted for further management.'), (2, 'drug overdose'))
        self.assertIsNone(readmission.stated_reason('Stable for discharge with primary care follow up.'))
        self.assertEqual(readmission._label('i50.9'), 'I50.9 (heart failure)'); self.assertEqual(readmission._label('Fluid Overload'), 'fluid overload')

    def test_population_patterns_are_counted_by_code_and_grouped_by_system_one(self):
        from clinical_core.agere.tiered import store
        columns = ['patient_id', 'readmit_admit', 'index_admit', 'index_discharge', 'readmit_discharge', 'visit7', 'record_id', 'event_date', 'document_type']
        rows = [['P-1', '2030-05-07', '2030-04-23', '2030-04-29', '2030-05-10', 0, 'A', '2030-04-29', 'discharge_summary'], ['P-1', '2030-05-07', '2030-04-23', '2030-04-29', '2030-05-10', 0, 'B', '2030-05-10', 'discharge_summary'],
                ['P-2', '2030-06-01', '2030-05-20', '2030-05-22', '2030-06-03', 1, 'C', '2030-05-22', 'discharge_summary'], ['P-2', '2030-06-01', '2030-05-20', '2030-05-22', '2030-06-03', 1, 'D', '2030-06-03', 'discharge_summary'],
                ['P-3', '2030-07-01', '2030-06-20', '2030-06-22', '2030-07-02', 0, None, None, None]]
        texts = {'A': doc('Admitted with fluid overload; diuresed. Primary follow up recommended around 2030-05-02.', '2030-04-29'), 'B': doc('Principal problem this admission: I50.9', '2030-05-10'),
                 'C': doc('Admitted with community-acquired pneumonia; improved.', '2030-05-22'), 'D': doc('Admission date: 2030-06-01 Principal diagnosis: Closed fracture of femoral neck', '2030-06-03')}
        groups = {'fluid overload': 'heart_failure', 'I50.9': 'heart_failure', 'community-acquired pneumonia': 'respiratory_infection', 'Closed fracture of femoral neck': 'injury'}
        with mock.patch.object(store, 'run', lambda sql, params=None, db=None: (columns, rows)), mock.patch.object(store, 'texts', lambda ids, db: {i: texts[i] for i in ids}), \
                mock.patch.object(readmission, 'group_of', lambda reason, ledger=None: (groups[reason], 0.0001)):
            cols, out, validation, steps, evidence = readmission.patterns('q', 'SELECT 1', {}, {}, None)
            listed = readmission.case_list('q', 'SELECT 1', {}, {'only': 'instruction_no_visit'}, None)[1]
            everyone = readmission.case_list('q', 'SELECT 1', {}, {}, None)[1]
            nothing = readmission.case_list('q', 'SELECT 1', {}, {'only': 'same_problem'}, None)
        got = {r[0]: r[1] for r in out}
        self.assertEqual(got['readmissions'], 3); self.assertEqual(got['a reason for the readmission is stated in the record'], 2)
        self.assertEqual(got['no document states why the patient was readmitted'], 1)
        self.assertEqual(got['the prior discharge summary gives a follow-up instruction'], 1)
        self.assertEqual(got['a follow-up instruction was given and no outpatient visit followed within 7 days'], 1)
        self.assertEqual(got['no follow-up instruction on record and no outpatient visit within 7 days'], 1)  # P-3: nothing on record at all
        self.assertEqual(got['same problem group as the stay before it'], 1); self.assertEqual(got['a different problem group from the stay before it'], 1)
        self.assertEqual(got['readmitted for: heart failure or fluid overload'], 1); self.assertEqual(got['readmitted for: fall, fracture or injury'], 1)
        self.assertTrue(validation['ok']); self.assertEqual({e['quote'] for e in evidence}, {'I50.9', 'Closed fracture of femoral neck'})
        self.assertEqual([(r[0], r[2], r[3], r[4], r[6], r[8]) for r in listed], [('P-1', '2030-05-07', 8, 'I50.9', 'same problem group', 'no')])
        self.assertIn('Primary follow up recommended', listed[0][7])
        self.assertEqual([r[0] for r in everyone], ['P-1', 'P-2', 'P-3']); self.assertEqual(everyone[2][4], 'not stated in any document')
        self.assertEqual([r[0] for r in nothing[1]], ['P-1']); self.assertTrue(nothing[2]['ok'])


class EachStayOnce(unittest.TestCase):
    def test_rows_that_repeat_overlap_or_continue_one_stay_are_one_episode(self):
        import sqlite3
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE stay (patient_id, admit_date, discharge_date, type, organization_id)')
        db.executemany('INSERT INTO stay VALUES (?, ?, ?, ?, ?)', [
            ('P1', '2029-03-01', '2029-03-07', 'inpatient', 'A'),
            ('P1', '2029-03-13', None, 'inpatient', 'A'),            # the readmission, written with no discharge date ...
            ('P1', '2029-03-20', '2029-03-20', 'inpatient', 'A'),    # ... and again as a same-day row on the day it ended
            ('P2', '2029-05-01', '2029-05-01', 'inpatient', 'A'),    # a real same-day stay: no open row before it
            ('P3', '2029-06-01', None, 'inpatient', 'A'),            # still in hospital
            ('P4', '2029-07-20', '2029-07-24', 'inpatient', 'A'),
            ('P4', '2029-08-18', '2029-08-23', 'inpatient', 'A'),    # readmitted 25 days later ...
            ('P4', '2029-08-23', '2029-08-27', 'inpatient', 'B'),    # ... and moved the day that stay ended: the same episode
            ('P4', '2029-08-19', '2029-08-20', 'inpatient', 'A'),    # a second source's copy inside it
            ('P4', '2029-09-10', '2029-09-12', 'inpatient', 'A')])   # a second readmission, 14 days after the episode ended
        rows = db.execute('WITH ' + recipes._STAYS.rstrip(', ') + ' SELECT patient_id, admit_date, discharge_date FROM st ORDER BY 1, 2').fetchall()
        self.assertEqual(rows, [('P1', '2029-03-01', '2029-03-07'), ('P1', '2029-03-13', '2029-03-20'), ('P2', '2029-05-01', '2029-05-01'), ('P3', '2029-06-01', None),
                                ('P4', '2029-07-20', '2029-07-24'), ('P4', '2029-08-18', '2029-08-27'), ('P4', '2029-09-10', '2029-09-12')])
        pairs = db.execute(recipes._READMIT_PAIRS + 'SELECT patient_id, readmit_admit, index_discharge FROM pairs', {'year': '2029'}).fetchall()
        self.assertEqual(sorted(pairs), [('P1', '2029-03-13', '2029-03-07'),   # one readmission, six days after discharge, not two
                                         ('P4', '2029-08-18', '2029-07-24'), ('P4', '2029-09-10', '2029-08-27')])

    def test_the_instruction_is_quoted_whole_from_a_labelled_field_or_a_sentence(self):
        long = 'Discharge medications: ' + '; '.join(['metformin 500 mg tablet twice daily'] * 12) + ' Disposition: snf Follow-up: PCP within 7 days.'
        self.assertEqual(readmission.follow_up_instruction(long), 'Follow-up: PCP within 7 days.')
        self.assertEqual(readmission.follow_up_instruction('Stable. Primary folow up recommended around 2028-12-28. Diet as tolerated.'), 'Primary folow up recommended around 2028-12-28.')
        self.assertIsNone(readmission.follow_up_instruction('Stable for discharge. Diet as tolerated.'))

    def test_a_misspelled_follow_up_instruction_is_still_an_instruction(self):
        for said in ('Primary folow up recommended around 2028-12-28.', 'Follow-up with cardiology in one week.', 'follow up in clinic', 'FOLLLOW UP arranged'):
            self.assertTrue(readmission.FOLLOW_UP.search(said), said)
        self.assertFalse(readmission.FOLLOW_UP.search('The fellow updated the plan.'))

    def test_barrier_sentences_keep_a_dose_with_a_decimal_point_whole(self):
        note = ('Seen in clinic. Did not increase {28 (norethindrone 0.35 MG Oral Tablet) } Pack today because the current directions and actual use still need confirmation. '
                'Weight stable. Says the medication ran out.')
        self.assertEqual(readmission.barrier_sentences(note), ['Did not increase {28 (norethindrone 0.35 MG Oral Tablet) } Pack today because the current directions and actual use still need confirmation.',
                                                               'Says the medication ran out.'])


class NoLabsOnRecord(unittest.TestCase):
    def test_a_patient_with_no_lab_results_is_answered_by_the_recipe_not_sent_up_the_ladder(self):
        columns, rows = ['period', 'concept', 'results', 'lowest', 'highest', 'last_date'], [['readmission', '(any lab or vital)', 0, None, None, '']]
        failed = lambda **kw: [c['name'] for c in recipes.validate('q', 'SELECT 1', {}, columns, rows, **kw)['checks'] if not c['ok']]
        self.assertIn('no_all_null_column', failed())                                   # the general rule still holds
        self.assertNotIn('no_all_null_column', failed(nullable=recipes.RECIPES['readmission_labs']['nullable']))


class Drivers(unittest.TestCase):
    def test_wording_for_the_emergency_threshold_and_discharge_destination(self):
        self.assertTrue(readmission.ED_LOW_YIELD.search('78 y/o presented to the ED with pneumonia. Evaluated; workup unremarkable. Admitted for further management.'))
        self.assertFalse(readmission.ED_LOW_YIELD.search('Labs and CT unremarkable. Treated and released with instructions to follow up.'))
        self.assertTrue(readmission.FACILITY.search('skilled nursing facility')); self.assertTrue(readmission.FACILITY.search('snf'))
        self.assertFalse(readmission.FACILITY.search('home health')); self.assertTrue(readmission.HOME_HEALTH.search('home_health'))
        self.assertEqual(set(readmission.DRIVERS), set(readmission.DRIVER_DEFINITIONS))


class CallQueue(unittest.TestCase):
    def test_an_empty_queue_is_a_declared_answer_and_the_recipes_match_their_wording(self):
        self.assertEqual(recipes.match('Who is due a bridge call on 2029-08-14?')[0], 'bridge_call_queue')
        self.assertTrue(recipes.RECIPES['bridge_call_queue']['empty'])
        self.assertEqual(recipes.match('What is driving the readmissions in 2029?')[0], 'readmission_drivers_summary')


if __name__ == '__main__':
    unittest.main()
