import sqlite3
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import note_open as O, note_evidence as E, chart_notes as C

class DocumentScope(unittest.TestCase):
    def test_cxr_report_carries_the_same_document_kind_as_chest_xray(self):
        for q in ('outside CXR report', 'outside chest x-ray report'):
            cue = O.source_cues(q)
            self.assertEqual(cue['note_types'], ['radiology_report'])
            self.assertEqual(C.build('What was the impression on the '+q+' for P-1001?', '2030-05-27')[0]['note_types'], ['radiology_report'])

    def test_generic_import_titles_use_report_headers_not_passing_mentions(self):
        rows = [
            ('outside', 'other_text', 'imported_documents', 'Outside Records', '2027-09-03',
             'EXAM: XRAY FINDINGS: See impression. IMPRESSION:Possible right lower lobe infiltrate.', 'Outside Records'),
            ('ed', 'other_text', 'received_hospital_summaries', 'ED summary', '2029-04-15',
             'Presented with dyspnea. BNP elevated, CXR with vascular congestion; IV diuretic given.', 'ED summary'),
            ('discharge', 'other_text', 'received_hospital_summaries', 'Discharge summary', '2028-03-02',
             'Hospital course: CXR with infiltrate. Discharged home.', 'Discharge summary')]
        db = sqlite3.connect(':memory:')
        try:
            with patch.object(E, '_texts', return_value=rows):
                hits = E.retrieve(db, 'P-1001', {'term': 'chest x-ray'}, as_of='2030-05-27',
                                  note_types=['radiology_report'])
            self.assertEqual({h['record_id'] for h in hits}, {'outside'})
        finally: db.close()

    def test_ed_note_with_a_test_mention_is_not_a_radiology_report(self):
        self.assertFalse(O.matches_document_type(['radiology_report'], 'ED summary', 'CXR clear. Return if worse.'))
        self.assertFalse(O.matches_document_type(['radiology_report'], 'ED summary', 'EXAM: XRAY FINDINGS: clear. IMPRESSION: no acute disease.'))
        self.assertFalse(O.matches_document_type(['radiology_report'], 'ED summary', 'EXAM: lungs clear. CXR pending. IMPRESSION: dyspnea.'))
        self.assertTrue(O.matches_document_type(['radiology_report'], 'Radiology Report', 'Chest film normal.'))
