"""Grouped financial/operational results do not imply patient treatment gaps."""
import gzip,json,tempfile,unittest
from pathlib import Path
from pypdf import PdfReader
from clinical_core.agere.deliverables.patient_report_pdf import render


class ReportScope(unittest.TestCase):
    def test_grouped_values_and_unknowns_survive_without_inventing_care_requirements(self):
        context={'question':'Recorded amounts by payer.','source':{'as_of':'2030-05-27'},
            'answer':{'status':'answered','headline':'Recorded amounts.',
                'table':[{'payer_id':'PAYER','recorded_cents':105,'missing_amounts':None}],
                'limitations':['Recorded amounts are not realized savings.']}}
        with tempfile.TemporaryDirectory()as tmp:
            f=Path(tmp)/'report.pdf';render(context,f);pdf=PdfReader(f)
            text='\n'.join(page.extract_text()for page in pdf.pages)
            saved=json.loads(gzip.decompress(pdf.attachments['Agere-saved-answer.json.gz'][0]))
        self.assertEqual(saved,context)
        for value in ['ANALYSIS REVIEW','RESULT RECORD','PAYER','105','Unknown / not supplied','not realized savings']:
            self.assertIn(value,text)
        for value in ['PATIENT RECORD','No individualized recommendation','unresolved output requirement']:
            self.assertNotIn(value,text)

    def test_patient_records_keep_supplied_unknowns_without_inventing_a_missing_requirement(self):
        context={'question':'List received patient records.','source':{'as_of':'2030-05-27'},
            'answer':{'status':'partial','missing':['Current tolerance not supplied'],
                'table':[{'patient_id':'A','documented_value':None}]}}
        with tempfile.TemporaryDirectory()as tmp:
            f=Path(tmp)/'report.pdf';render(context,f);text='\n'.join(p.extract_text()for p in PdfReader(f).pages)
        self.assertIn('PATIENT RECORD',text);self.assertIn('Current tolerance not supplied',text)
        self.assertNotIn('unresolved output requirement',text)


if __name__=='__main__':unittest.main()
