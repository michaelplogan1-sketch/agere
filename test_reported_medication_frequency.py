"""Explicit documentary use counts retain denominator, provenance, zero and uncertainty."""
import unittest
from clinical_core.agere.clinical import chart_notes as C

PRED = {'concept': {'term': 'fluticasone'}, 'frame': 'medication_event'}
WANT = {'period': 14, 'unit': 'day'}


def evidence(text, date='2027-05-13', record='R1'):
    return {'quote': text, 'date': date, 'record_id': record, 'note_type': 'callback'}


def read(*texts):
    return C.reported_frequency(PRED, {'status': 'denied', 'evidence': [evidence(t) if isinstance(t, str) else t for t in texts]}, WANT)


class ReportedFrequency(unittest.TestCase):
    def test_zero_is_an_explicit_report_not_missing_evidence(self):
        r = read('fluticasone HFA: reports use on 0 of 14 days; 120 actuations left.')
        self.assertEqual((r['count'], r['period'], r['unit']), (0, 14, 'day'))
        self.assertEqual(r['evidence'][0]['record_id'], 'R1')
        self.assertIsNone(read())
        self.assertIsNone(read('fluticasone: no use history documented.'))

    def test_different_metrics_drugs_periods_and_units_do_not_answer_use(self):
        for quote in ('fluticasone: requests refills on 3 of 14 days.',
                      'albuterol: reports use on 3 of 14 days.',
                      'fluticasone: none taken; albuterol: reports use on 3 of 14 days.',
                      'fluticasone and albuterol: reports use on 3 of 14 days.',
                      'fluticasone: used 0 of 14 days last month; now reports use on 7 of 14 days.',
                      'fluticasone: reports use on 3 of 30 days.',
                      'fluticasone: reports use on 3 of 14 weeks.',
                      'fluticasone: reports use on 15 of 14 days.',
                      'fluticasone: used about 3 of 14 days.',
                      'fluticasone: reports use on 2-3 of 14 days.',
                      'fluticasone: instructed to use on 14 of 14 days.',
                      'fluticasone: patient did not use on 3 of 14 days.'):
            with self.subTest(quote=quote): self.assertIsNone(read(quote))

    def test_latest_report_decides_and_equal_date_conflict_stays_open(self):
        old = evidence('fluticasone: reports use on 3 of 14 days.', '2027-05-10', 'R-old')
        new = evidence('fluticasone: reports use on 0 of 14 days.', '2027-05-13', 'R-new')
        self.assertEqual(read(old, new)['count'], 0)
        self.assertIsNone(read(new, {**old, 'date': new['date']}))
        self.assertIsNone(read(new, {**old, 'date': None}))
        ambiguous = evidence('fluticasone: used 0 of 14 days last month; now reports use on 7 of 14 days.', '2027-05-14', 'R-conflict')
        self.assertIsNone(read(old, new, ambiguous))

    def test_fraction_requires_an_actual_citation(self):
        item = evidence('fluticasone: reports use on 3 of 14 days.')
        self.assertIsNone(read({**item, 'record_id': None}))
        self.assertIsNone(C.reported_frequency(PRED, {'status': 'other_person', 'evidence': [item]}, WANT))

    def test_render_returns_the_cited_count_and_removes_internal_metadata(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        pred = {**PRED, 'statuses': ['documented'], '_reported_frequency': WANT}
        item = evidence('fluticasone HFA: reports use on 0 of 14 days; 120 actuations left.')
        nf = SimpleNamespace(answer_patient=Mock(return_value={'status': 'denied', 'evidence': [item]}))
        core = C.render(None, [pred], 'P-1', '2030-05-27', nf=nf, allow_live=False)
        self.assertEqual((core['answer_type'], core['value'], core['count']), ('count', 0, 0))
        self.assertEqual(core['sources'][0]['record_id'], 'R1')
        self.assertNotIn('_reported_frequency', nf.answer_patient.call_args.args[1])
        self.assertEqual(pred['_reported_frequency'], WANT)

    def test_request_carries_recall_period_without_guessing(self):
        self.assertEqual(C.reported_frequency_request('How many of the last 14 days did she say she used it?'), WANT)
        self.assertEqual(C.reported_frequency_request('How many of the last fourteen days did she use it?'), WANT)
        self.assertEqual(C.reported_frequency_request('How many of the previous 7 doses?'), {'period': 7, 'unit': 'dose'})
        self.assertIsNone(C.reported_frequency_request('How many days did she use it?'))
