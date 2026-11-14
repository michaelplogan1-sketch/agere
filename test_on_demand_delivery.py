"""Request-scoped exports; tiny authored cases, no providers or whole-cohort PDF."""
import unittest
from unittest.mock import patch
from clinical_core.agere.deliverables import answer_context as A,saved_report as R,workflow as W
from clinical_core.agere.clinical import conversation as C
from test_saved_report import SavedReport

class OnDemandDelivery(unittest.TestCase):
    setUp=SavedReport.setUp
    prepare=SavedReport.prepare

    def test_ordinary_answer_saves_context_without_export_work(self):
        with patch.object(R,'create',side_effect=AssertionError('Unrequested export')),patch.object(W,'materialize',side_effect=AssertionError('Unrequested workflow')),patch.object(A,'compose_frontier',side_effect=AssertionError('Unrequested hosted view')):
            result=C.handle_compose(self.stack,{'question':'List patients with diabetes','scope':'warehouse'})
        self.assertEqual(result['answer']['status'],'answered')
        self.assertTrue(result['action_context']['id'])
        self.assertFalse(result.get('workflow'))
        self.assertFalse(list(self.path.rglob('artifact.*')))

    def test_new_html_request_does_not_inherit_old_pdf_intent(self):
        question,result=self.prepare()
        result['workflow'][0]['quote']='create a PDF report'
        context=A.capture(self.path,question+'; create a PDF report',result)
        with patch('clinical_core.agere.deliverables.patient_report_pdf.render',side_effect=AssertionError('Old PDF intent must not create a new export')) as render:
            report=R.create(self.path,{'context_id':context['id'],'template':'brief','request':'HTML review'})
        render.assert_not_called();self.assertNotIn('pdf',report['formats'])
        self.assertTrue((A.root(self.path)/context['id']/'deliverables'/report['id']/'artifact.html').is_file())
        self.assertFalse(list(self.path.rglob('artifact.pdf')))
        with self.assertRaisesRegex(ValueError,'not been generated'):R.read_format(self.path,context['id'],report['id'],'pdf')

    def test_quoted_current_request_controls_workflow_format(self):
        question,result=self.prepare()
        result['workflow'][0].update(title='Patient review',quote='create a PDF report')
        with patch.object(R,'create',wraps=R.create) as create,patch('clinical_core.agere.deliverables.patient_report_pdf.render',side_effect=RuntimeError('Authored renderer sentinel')) as render:
            out=W.fulfill(self.stack,question,result,generation='saved_report')
        self.assertEqual(create.call_args.args[1]['request'],'create a PDF report')
        render.assert_called_once();self.assertEqual(out['workflow'][0]['status'],'blocked')
        self.assertEqual(out['workflow'][0]['render_error']['type'],'RuntimeError')
        self.assertEqual(out.get('new_paid_api_calls',0),0)
        self.key=out['action_context']['id']
        with patch.object(R,'create',wraps=R.create) as create,patch('clinical_core.agere.deliverables.patient_report_pdf.render',side_effect=RuntimeError('Authored renderer sentinel')) as render:
            W.retry_deliverable(self.path,{'context_id':self.key,'index':0,'attempt':2},allow_live=False)
        self.assertEqual(create.call_args.args[1]['request'],'create a PDF report');render.assert_called_once()

if __name__=='__main__':unittest.main()
