"""Chart reader residual classes (2026-10-23): condition CONTROL questions mapped to their governing measures, HOW MANY over notes answered
as a count, and persistence after a dated event ("a 2029-08-05 note stopped X; do later notes still list X?"). General rules, synthetic
fixture warehouse (test_chart_reader.build), no model or network."""
import json
import unittest

from clinical_core.agere.clinical import chart_intents as CI
from clinical_core.agere.clinical import chart_notes as CN
from clinical_core.agere.clinical import chart_reader as CR
from clinical_core.agere.clinical import chart_reader_op as OP
from clinical_core.agere.clinical import requirement_ledger as RL
from clinical_core.agere.clinical.literal_check import executable_text, shape_misfit
from test_chart_reader import AS_OF, RICH, Base


class FakeByWindow:
    """note_frames stand-in keyed by (frame, concept, window start | None); records every predicate it was asked."""
    FRAMES = {}

    def __init__(self, readings):
        self.calls, self.readings = [], readings

    def validate_predicate(self, p):
        return p

    def answer_patient(self, stack, predicate, patient_id, *, as_of, ledger=None, allow_live=True):
        self.calls.append(predicate)
        key = (predicate['frame'], predicate['concept']['term'], (predicate.get('window') or {}).get('start'))
        return self.readings.get(key) or self.readings.get(key[:2]) or {'status': 'not_mentioned', 'detail': {}, 'evidence': [], 'p': 0.9}


def ev(rid, day, quote, detail=None, status='documented', supports=True):
    return {'record_id': rid, 'date': day, 'note_type': 'office_followup', 'quote': quote, 'status': status, 'detail': detail or {}, 'supports': supports}


def gaps_of(question, preds_or_chart):
    return RL.gaps(question, executable_text([preds_or_chart]))


class ConditionControl(Base):
    def test_the_map_is_data_with_a_source_and_only_real_measures(self):
        from clinical_core.agere.clinical.concepts import CONDITIONS, MEASUREMENTS
        raw = json.loads(CI.CONDITION_MEASURES_PATH.read_text())['conditions']
        for cond, spec in raw.items():
            self.assertIn(cond, CONDITIONS)
            self.assertTrue(spec['source'].strip(), cond)
            self.assertTrue(spec['measures'] and all(m in MEASUREMENTS for m in spec['measures']), cond)
        self.assertEqual({c: CI.condition_measures()[c]['measures'] for c in ('diabetes', 'hypertension', 'chronic_kidney_disease', 'heart_failure', 'hyperlipidemia')},
                         {'diabetes': ['hba1c'], 'hypertension': ['systolic_bp', 'diastolic_bp'], 'chronic_kidney_disease': ['egfr', 'uacr'],
                          'heart_failure': ['nt_probnp'], 'hyperlipidemia': ['ldl']})

    def test_a_condition_control_question_is_typed_as_its_measures_and_keeps_the_condition(self):
        for text, cond, measures, intent in (
                ('Is the diabetes control getting better or worse for SYN-AAAAAAAA-AAA?', 'diabetes', ['hba1c'], 'trend'),
                ('Is the hypertension getting worse for SYN-AAAAAAAA-AAA?', 'hypertension', ['systolic_bp', 'diastolic_bp'], 'trend'),
                ('Is the CKD improving for SYN-AAAAAAAA-AAA?', 'chronic_kidney_disease', ['egfr', 'uacr'], 'trend'),
                ('Is the heart failure controlled for SYN-AAAAAAAA-AAA?', 'heart_failure', ['nt_probnp'], 'latest_value'),
                ('Is the hyperlipidemia under control for SYN-AAAAAAAA-AAA?', 'hyperlipidemia', ['ldl'], 'latest_value')):
            it = CI.parse(text, AS_OF)
            self.assertEqual((it['domain'], it['intent'], it['concept']['condition'], it['concept']['concepts']), ('lab', intent, cond, measures), text)
            self.assertIn('condition_control', it['flags'])
            self.assertEqual(it['gaps'], [])

    def test_a_lab_question_that_names_its_condition_keeps_both(self):
        it = CI.parse('Is the diabetes control getting better or worse for SYN-AAAAAAAA-AAA? Compare the last two A1c results.', AS_OF)
        self.assertEqual((it['concept']['value'], it['concept']['condition'], it['intent']), ('hba1c', 'diabetes', 'trend'))
        self.assertNotIn('condition_control', it['flags'])

    def test_a_condition_question_that_is_not_about_control_stays_a_condition_question(self):
        it = CI.parse('Does SYN-AAAAAAAA-AAA have diabetes?', AS_OF)
        self.assertEqual(it['domain'], 'condition')

    def test_the_trend_is_answered_from_the_governing_measure(self):
        a = self.ask('Is the diabetes control getting better or worse for {P}?')  # no A1c in the words
        self.assertEqual((a['answer_type'], a['value']), ('label', 'improving'))
        self.assertIn('HbA1c', a['headline'])
        ckd = self.ask('Is the CKD getting better or worse for {P}?')  # eGFR 58 -> 52; no UACR on file
        self.assertEqual(ckd['value'], 'worsening')
        self.assertIn('eGFR', ckd['headline'])
        self.assertIn('urine albumin/creatinine ratio', ckd['headline'])
        self.assertTrue(any('KDIGO' in n for n in ckd['notes']))
        htn = self.ask('Is the hypertension controlled for {P}?')
        self.assertEqual(htn['answer_type'], 'value')
        self.assertEqual(htn['facts']['systolic'], 142)

    def test_the_typed_chart_carries_the_condition_and_the_measure(self):
        for text in ('Is the diabetes control getting better or worse for SYN-AAAAAAAA-AAA?',
                     'Is the diabetes control getting better or worse for SYN-AAAAAAAA-AAA? Compare the last two A1c results.',
                     'Is the CKD improving for SYN-AAAAAAAA-AAA?', 'Is the hyperlipidemia under control for SYN-AAAAAAAA-AAA?'):
            it = CI.parse(text, AS_OF)
            chart = CR.typed_chart(it)
            self.assertEqual(gaps_of(text, chart), [], text)
        chart = CR.typed_chart(CI.parse('Is the diabetes control getting better or worse for SYN-AAAAAAAA-AAA? Compare the last two A1c results.', AS_OF))
        self.assertEqual((chart['concept']['condition'], chart['concept']['value'], chart['limit']), ('diabetes', 'hba1c', 2))


class HowManyInNotes(Base):
    FALLS = ('function_cognition', 'falls')

    def ask_notes(self, text, readings):
        return CR.ask(self.stack, text.replace('{P}', RICH), nf=FakeByWindow(readings))

    def test_a_number_the_note_states_is_the_count(self):
        e = ev('N-1', '2029-11-26', 'Falls in the past year: 2.', {'count': 2})
        a = self.ask_notes('How many falls did {P} report in the past year at the annual wellness visit?',
                           {self.FALLS: {'status': 'documented', 'detail': {'count': 2}, 'evidence': [e], 'p': 0.9, 'holds': True}})
        self.assertEqual((a['answer_type'], a['count'], a['value']), ('count', 2, 2))
        self.assertIn('N-1', a['headline'])
        self.assertIsNone(a['facts']['lead'])
        self.assertIn('stated in the 2029-11-26', a['facts']['count_basis'])

    def test_without_a_stated_number_the_count_is_the_distinct_documented_occurrences(self):
        evs = [ev('N-1', '2029-01-10', 'Counseled on smoking cessation.'), ev('N-2', '2029-04-02', 'Smoking cessation counseling provided.'),
               ev('N-2b', '2029-04-02', 'Same visit, duplicate record.'), ev('N-3', '2029-09-30', 'Advised to quit smoking; counseled.')]
        nf = FakeByWindow({('care_process', 'smoking', None): {'status': 'documented', 'detail': {}, 'evidence': evs, 'p': 0.9, 'holds': True}})
        a = CR.ask(self.stack, f'How many times was {RICH} counseled on smoking?', nf=nf)
        self.assertEqual((a['status'], a['answer_type'], a['count']), ('answered', 'count', 3))
        self.assertEqual([p['frame'] for p in nf.calls], ['care_process'])  # one frame, one answer
        self.assertIn('one per note date', a['facts']['count_basis'])

    def test_nothing_documented_is_zero_and_an_undecided_reading_is_not_a_count(self):
        zero = self.ask_notes('How many falls did {P} report in the past year at the annual wellness visit?', {})
        self.assertEqual((zero['answer_type'], zero['count']), ('count', 0))
        unsure = self.ask_notes('How many falls did {P} report in the past year at the annual wellness visit?',
                                {self.FALLS: {'status': 'unknown', 'detail': {}, 'evidence': [], 'p': None, 'holds': None}})
        self.assertNotEqual(unsure['answer_type'], 'count')

    def test_the_count_passes_the_answer_shape_check_and_a_yes_no_does_not_become_one(self):
        q = f'How many falls did {RICH} report in the past year at the annual wellness visit?'
        e = ev('N-1', '2029-11-26', 'Falls in the past year: 2.', {'count': 2})
        out = CR.ask(self.stack, q, nf=FakeByWindow({self.FALLS: {'status': 'documented', 'detail': {'count': 2}, 'evidence': [e], 'p': 0.9, 'holds': True}}))
        merged = OP.answer({'answer_contract': out, 'patient_ids': [RICH]}, {'coverage': 'supported_observation'}, q)
        self.assertEqual(merged['answer_type'], 'count')
        self.assertIsNone(shape_misfit(q, merged))
        yn = CR.ask(self.stack, f'Is a fall mentioned anywhere in {RICH}\'s chart?', nf=FakeByWindow({}))
        self.assertNotEqual(yn['answer_type'], 'count')
        self.assertTrue(yn['headline'].startswith('Not documented'))

    def test_counseling_is_a_note_question(self):
        text = f'How many times was {RICH} counseled on smoking?'
        self.assertTrue(CN.is_note_question(text, CI.parse(text, AS_OF)))
        self.assertTrue(CN.build(text, AS_OF)[0]['_count'])


class PersistenceAfterAnEvent(Base):
    Q = 'A 2029-08-05 note stopped lisinopril for {P} because of angioedema concern. Do later notes still list lisinopril?'

    def run_q(self, readings, text=None):
        nf = FakeByWindow(readings)
        return CR.ask(self.stack, (text or self.Q).replace('{P}', RICH), nf=nf), nf

    def test_the_dated_note_is_the_anchor_and_later_notes_are_read_for_the_drug_as_active(self):
        stop = ev('N-A', '2029-08-05', 'Lisinopril stopped for angioedema concern.', {'event': 'stopped'})
        later = ev('N-B', '2029-12-07', 'Medications: lisinopril 10 mg daily.', {'event': 'continued', 'adherence': 'not_stated'})
        a, nf = self.run_q({('medication_event', 'lisinopril', '2029-08-05'): {'status': 'documented', 'detail': {'event': 'stopped'}, 'evidence': [stop], 'p': 0.9, 'holds': True},
                            ('medication_event', 'lisinopril', '2029-08-06'): {'status': 'documented', 'detail': {'event': 'continued'}, 'evidence': [later], 'p': 0.9, 'holds': True}})
        self.assertEqual([(c['window']['start'], c['window']['end']) for c in nf.calls], [('2029-08-05', '2029-08-05'), ('2029-08-06', AS_OF)])
        self.assertTrue(a['headline'].startswith('Yes:'), a['headline'])
        self.assertIn('N-B', a['headline'])
        self.assertIn('stopped in the 2029-08-05 note (N-A)', a['headline'])
        self.assertEqual(a['intent']['yes_no'], True)

    def test_later_notes_that_only_end_the_drug_answer_no(self):
        stop = ev('N-A', '2029-08-05', 'Lisinopril stopped.', {'event': 'stopped'})
        again = ev('N-C', '2029-10-01', 'Lisinopril remains discontinued.', {'event': 'stopped'})
        a, _ = self.run_q({('medication_event', 'lisinopril', '2029-08-05'): {'status': 'documented', 'detail': {'event': 'stopped'}, 'evidence': [stop], 'p': 0.9, 'holds': True},
                           ('medication_event', 'lisinopril', '2029-08-06'): {'status': 'documented', 'detail': {'event': 'stopped'}, 'evidence': [again], 'p': 0.9, 'holds': True}})
        self.assertTrue(a['headline'].startswith('No:'), a['headline'])
        self.assertIn('N-C', a['headline'])

    def test_no_later_note_lists_it_is_no_and_an_undecided_reading_is_undecided(self):
        a, _ = self.run_q({})
        self.assertTrue(a['headline'].startswith('No:'), a['headline'])
        u, _ = self.run_q({('medication_event', 'lisinopril', '2029-08-06'): {'status': 'unknown', 'detail': {}, 'evidence': [], 'p': None, 'holds': None}})
        self.assertTrue(u['headline'].startswith('Undecided'), u['headline'])

    def test_after_an_event_without_a_date_anchors_on_the_stopping_note(self):
        stop = ev('N-A', '2029-03-01', 'Aspirin stopped.', {'event': 'stopped'})
        nf = FakeByWindow({('medication_event', 'aspirin', None): {'status': 'documented', 'detail': {'event': 'stopped'}, 'evidence': [stop], 'p': 0.9, 'holds': True}})
        a = CR.ask(self.stack, f'After aspirin was stopped for {RICH}, is aspirin still listed in the later notes?', nf=nf)
        self.assertEqual(nf.calls[0]['window'] if 'window' in nf.calls[0] else None, None)  # the anchor reads every note
        self.assertEqual(nf.calls[1]['window']['start'], '2029-03-02')
        self.assertIn('later notes read from 2029-03-02', a['headline'])

    def test_a_started_medications_question_is_not_a_persistence_question(self):
        p = CN.build(f'{RICH} had a goals-of-care talk choosing comfort-focused care. Do the later notes still show new disease-directed medications being started?', AS_OF)
        self.assertFalse(p[0].get('_persist'))

    def test_the_typed_chart_carries_the_anchor_date_so_the_literal_check_passes(self):
        q = self.Q.replace('{P}', RICH)
        chart = CR.typed_chart({'intent': 'status', 'domain': 'notes', 'question': q}, CN.build(q, AS_OF), q)
        self.assertIn('2029-08-05', chart['anchor_dates'])
        self.assertTrue(chart['asks_persistence'])
        self.assertEqual(gaps_of(q, chart), [])

    def test_iso_dates_are_dates_not_year_ranges(self):
        from clinical_core.agere.clinical import local_normalizer as N
        self.assertEqual(N.numbers('A 2029-08-05 note stopped it'), {'2029', '8', '5'})
        self.assertEqual(N.numbers('in 2029-30 and 2029-12-31'), {'2029', '2030', '12', '31'})


if __name__ == '__main__':
    unittest.main()
