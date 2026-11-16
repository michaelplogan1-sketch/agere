"""Reported historical counts retain their note scope and explicit zeros."""
import unittest
from clinical_core.agere.clinical import chart_notes as C

class ReportedCounts(unittest.TestCase):
    def test_reported_count_accepts_history_in_the_requested_note_window(self):
        p=C.build('How many falls did the patient report in the past year at the annual wellness visit?', '2030-05-27')[0]
        self.assertIn('historical', p['statuses'])
        self.assertIn('annual_wellness_visit',p['note_types'])
        self.assertEqual(p['window']['end'],'2030-05-27')
        self.assertIn('count',p['return_detail'])
    def test_event_count_does_not_automatically_accept_historical_events(self):
        p=C.build('How many falls did the patient have in the past year?', '2030-05-27')[0]
        self.assertNotIn('historical',p['statuses'])
    def test_stated_count_wins_over_number_of_note_dates(self):
        for n in (0,2):
            e={'supports':True,'status':'historical','detail':{'count':str(n)},'date':'2029-11-26','note_type':'annual_wellness','record_id':'example'}
            result=C.count_events({'status':'historical','evidence':[e]})
            self.assertEqual(result['n'],n)
            self.assertIn('stated in the 2029-11-26',result['basis'])
    def test_undecided_reading_never_becomes_zero(self):
        self.assertIsNone(C.count_events({'status':'unknown','evidence':[]}))
