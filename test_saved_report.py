"""Shared answer-to-report contracts, without provider calls or question banks."""
import copy
import os
import json
import re
import unittest
from unittest.mock import Mock, patch
import test_shared_planner as F
from clinical_core.agere.clinical import conversation as C
from clinical_core.agere.deliverables import answer_context as A, saved_report as R, workflow as W
import test_answer_workflow as AW


class SavedReport(unittest.TestCase):
    setUp = F.SharedPlanner.setUp

    def prepare(self, partial=False):
        f=AW.AnswerWorkflow();f.__dict__.update(self.__dict__)
        question,_,result,_=f.composed(base='List patients with diabetes')
        result['workflow']=[result['workflow'][0]]
        if partial:
            result['answer']['status']='partial'
            result['answer']['missing']=['Medication history unavailable']
        return question,result

    def test_normal_compose_can_choose_zero_call_report_and_keep_analysis_cost(self):
        question,result=self.prepare()
        from clinical_core.agere.gateway import BudgetLedger
        ledger_path=self.path/'provider-ledger.sqlite3'
        def compose(*args,**kwargs):
            ledger=BudgetLedger(ledger_path)
            try:
                ledger.reserve('authored-analysis-call','claude','claude-haiku-5-5')
                ledger.finish('authored-analysis-call',response={'content':[],'usage':{'input_tokens':1000,'output_tokens':100}},seconds=.1)
            finally:ledger.close()
            return {**copy.deepcopy(result),'new_paid_api_calls':1}
        self.stack.compose=compose
        self.stack.allow_live=True
        with patch.dict(os.environ,{'AGERE_LEDGER_PATH':str(ledger_path)}),patch('clinical_core.agere.deliverables.answer_context.compose_frontier',side_effect=AssertionError('No paid renderer')):
            out=C.handle_compose(self.stack,{'question':question,'scope':'warehouse','delivery_generation':'saved_report'})
        step=out['workflow'][0]
        self.assertEqual(step['status'],'ready');self.assertEqual(step['generation'],'saved_report')
        self.assertEqual(step['new_paid_api_calls'],0)
        self.assertEqual(out['cost_receipt']['paid_calls'],1)
        self.assertGreater(out['cost_receipt']['usd'],0)
        spec=json.loads((A.root(self.path)/out['action_context']['id']/'deliverables'/step['deliverable_id']/'spec.json').read_text())
        self.assertEqual(spec['verification']['rows'],sum(len(a.get('table') or []) for a in R.sections(result['answer'])))
        self.assertFalse(spec['mockup'])

    def test_partial_result_remains_partial_with_full_evidence_and_no_action_claim(self):
        question,result=self.prepare(partial=True)
        out=W.fulfill(self.stack,question,result,generation='saved_report')
        step=out['workflow'][0];folder=A.root(self.path)/out['action_context']['id']/'deliverables'/step['deliverable_id']
        body=(folder/'artifact.html').read_text()
        payload=json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>',body,re.S).group(1))
        self.assertEqual(payload['answer']['status'],'partial')
        self.assertEqual(payload['answer'],result['answer'])
        self.assertIn('Medication history unavailable',body)
        self.assertIn('No model call',body)

    def test_source_text_cannot_inject_html_script_or_replace_template_markers(self):
        question,result=self.prepare();result['answer']['limitations']=['</script><img src=x onerror=alert(1)> %%STATUS%%']
        c=A.capture(self.path,question,result)
        d=R.create(self.path,{'context_id':c['id'],'template':'brief','request':'%%STATUS%% <svg onload=alert(1)>'})
        self.assertNotIn('<img src=x',d['html']);self.assertNotIn('<svg onload',d['html'])
        self.assertIn('%%STATUS%%',d['html'])
        self.assertEqual(d['html'].count('</script>'),2)

    def test_current_source_validation_rejects_stale_answer_without_a_provider(self):
        question,result=self.prepare();c=A.capture(self.path,question,result)
        with patch.object(A,'source_identity',return_value={'changed':True}),self.assertRaises(ValueError):
            R.create(self.path,{'context_id':c['id'],'template':'brief','request':'Saved report'})

    def test_mode_is_explicit_and_unknown_values_are_rejected_before_compose(self):
        self.assertEqual(C.compose_shape({'question':'Count claims','delivery_generation':'frontier'})[:2],('Count claims','auto'))
        with self.assertRaises(ValueError):C.compose_shape({'question':'Count claims','delivery_generation':'silently_cap_cost'})

    def test_pdf_is_real_source_bound_and_contains_complete_saved_context(self):
        from pypdf import PdfReader
        import io
        question,result=self.prepare(partial=True);c=A.capture(self.path,question,result)
        d=R.create(self.path,{'context_id':c['id'],'template':'explore','request':'Interactive dashboard and PDF'})
        raw,mime,name=R.read_format(self.path,c['id'],d['id'],'pdf')
        self.assertTrue(raw.startswith(b'%PDF-'));self.assertEqual(mime,'application/pdf')
        pdf=PdfReader(io.BytesIO(raw));embedded=json.loads(pdf.attachments['Agere-saved-answer.json'][0])
        saved=A.load(self.path,c['id'])
        self.assertEqual(embedded['answer'],saved['answer']);self.assertEqual(embedded['source'],saved['source'])
        self.assertIn('Medication history unavailable','\n'.join(p.extract_text()for p in pdf.pages))
        self.assertEqual(d['new_paid_api_calls'],0)

    def test_pdf_download_rejects_tampering_and_unrecorded_formats(self):
        question,result=self.prepare();c=A.capture(self.path,question,result)
        d=R.create(self.path,{'context_id':c['id'],'template':'brief','request':'PDF'})
        f=A.root(self.path)/c['id']/'deliverables'/d['id']/'artifact.pdf';f.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'bytes differ'):R.read_format(self.path,c['id'],d['id'],'pdf')
        with self.assertRaises(ValueError):R.read_format(self.path,c['id'],d['id'],'../../answer.json')

    def test_supporting_notes_and_medications_survive_final_table_projection(self):
        from clinical_core.agere.deliverables.patient_evidence import projection
        c={'investigation':{'document_reviews':[{'questions':{'reason':'Why?'},'rows':[{'patient_id':'EXAMPLE','review_status':'needs_review',
            'documents_reviewed':2,'review_json':json.dumps({'reason':[{'value':'Cost documented','record_id':'REC','date':'2030-01-01','quote':'Could not afford.'}]})}]}],
            'tool_calls':[{'name':'query_sql','result':{'result_view':'rows_1','columns':['patient_id','drug_name','latest_fill_date'],
                'rows':[['EXAMPLE','Authored medication','2030-01-02']]}}]}}
        out=projection(c)
        self.assertEqual(out['notes']['EXAMPLE'][0]['findings']['reason'][0]['quote'],'Could not afford.')
        self.assertEqual(out['supporting_tables'][0]['rows'][0]['drug_name'],'Authored medication')

    def test_same_context_pdf_rendering_is_deterministic(self):
        from clinical_core.agere.deliverables.patient_report_pdf import render
        question,result=self.prepare();c=A.capture(self.path,question,result)
        first=self.path/'first.pdf';second=self.path/'second.pdf'
        saved=A.load(self.path,c['id'])
        render(saved,first);render(saved,second);self.assertEqual(first.read_bytes(),second.read_bytes())


if __name__=='__main__':unittest.main()
