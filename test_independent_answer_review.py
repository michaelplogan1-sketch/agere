"""User acceptance belongs to an exact analysis, not changed facts or executed care."""
import copy, importlib, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from clinical_core.agere.deliverables.receipts import Bundle, fingerprint, render_projection
C=importlib.import_module('clinical_core.agere.deliverables.compose')

class IndependentReview(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ws=SimpleNamespace(dir=Path(self.tmp.name),slug='authored')
        self.saved={'id':'ANS-authored','answer':{'status':'partial','visits':17,'missing':['Eligibility unavailable']},
                    'source':{'as_of':'2031-02-01','digest':'authored'},'context_policy_identity':'authored-policy'}
        self.b=Bundle({'frozen_evidence':True,'deliverable_purpose':'proposal'})
        self.rid=self.b.add('saved_answer',{'context_id':'ANS-authored'},self.saved)
        self.review={'schema':'independent-answer-review/1','accepted':True,'reviewer':'Authoring physician',
                     'verbatim':'I independently reviewed these results; they are correct. No further clinician review.',
                     'recorded_at':'2031-02-02','context_id':'ANS-authored',
                     'answer_fingerprint':fingerprint(self.saved['answer']),'source_fingerprint':fingerprint(self.saved['source']),
                     'policy_identity':self.saved['context_policy_identity']}
        self.file=Path(self.ws.dir)/'answer-review.json';self.file.write_text(json.dumps(self.review))

    def test_acceptance_is_preserved_separately_from_machine_status_and_missing_facts(self):
        actual=C._bound_user_review(self.ws,self.b);self.assertEqual(actual,self.review)
        self.b.meta['user_answer_review']=actual;packet=render_projection(self.b)
        status=packet['current_saved_status'][0]
        self.assertEqual(status['independent_user_review'],self.review)
        self.assertEqual(status['answer_status'],'partial')
        self.assertEqual(self.b.results[self.rid]['answer']['missing'],['Eligibility unavailable'])
        writer='\n'.join(b['text']for b in C._system_blocks(self.b))
        self.assertIn('do not run a new clinical assessment',writer)
        self.assertIn('another clinician review',writer)
        self.assertIn('unavailable eligibility',writer)

    def test_changed_answer_source_policy_or_context_cannot_inherit_acceptance(self):
        for kind in ('answer','source','policy','context'):
            with self.subTest(kind=kind):
                b=Bundle.from_json(copy.deepcopy(self.b.to_json()));saved=b.results[self.rid]
                if kind=='answer':saved['answer']['visits']=23
                if kind=='source':saved['source']['digest']='changed'
                if kind=='policy':saved['context_policy_identity']='changed'
                if kind=='context':saved['id']='other'
                self.assertIsNone(C._bound_user_review(self.ws,b))

    def test_malformed_missing_or_nonaccepted_review_never_confers_acceptance(self):
        for value in ({},dict(self.review,accepted=False),dict(self.review,answer_fingerprint='bad'),dict(self.review,policy_identity=None)):
            self.file.write_text(json.dumps(value));self.assertIsNone(C._bound_user_review(self.ws,self.b))
        self.file.write_text('malformed');self.assertIsNone(C._bound_user_review(self.ws,self.b))
        self.file.unlink();self.assertIsNone(C._bound_user_review(self.ws,self.b))

    def test_existing_technical_presentation_check_honors_the_bound_review(self):
        self.b.meta['user_answer_review']=C._bound_user_review(self.ws,self.b);seen=[]
        def send(body):
            seen.append(body)
            return {'content':[{'type':'text','text':'{"coverage":"complete","support":"supported","issues":[]}'}]},{'cache_hit':True}
        out=C._review_artifact('<p>Proposed design with known gaps</p>',self.b,'Design a service',None,send,C.Ledger(),{'passed':True})
        self.assertTrue(out['passed']);self.assertIn('do not run a new clinical assessment',seen[0]['system'])
        self.assertIn('independent_user_review',seen[0]['messages'][0]['content'])
        self.assertIn('Eligibility unavailable',seen[0]['messages'][0]['content'])

    def test_accepted_delivery_keeps_complete_answer_and_attaches_raw_derivation_without_reassessment(self):
        raw={'tool_calls':[{'name':'read_document','result':{'text':'Full original document.'}}],
             'answer_reviews':[{'feedback':'A failed OLD draft check'}]}
        self.b.results[self.rid]['investigation']=raw
        self.b.results[self.rid]['planner_receipt']={'raw_derivation':copy.deepcopy(raw)}
        self.b.results[self.rid]['graph']={'nodes':[{'query':'SELECT observed FROM received'}]}
        self.b.meta['user_answer_review']=C._bound_user_review(self.ws,self.b)
        before=copy.deepcopy(self.b.to_json());packet=render_projection(self.b);v=packet['data'][0]['data']
        self.assertEqual(v['answer'],self.saved['answer'])
        self.assertEqual(len(packet['accepted_analysis_provenance']),3)
        for ref in packet['accepted_analysis_provenance']:
            original=self.b.get(ref['native_path'])
            self.assertEqual(fingerprint(original),ref['sha256'])
        self.assertEqual(self.b.to_json(),before)
        self.assertNotIn('A failed OLD draft check',json.dumps(packet))
        self.assertIn('do not reopen that accepted clinical assessment',packet['instructions'])

    def test_unbound_or_changed_acceptance_cannot_hide_current_raw_clinical_evidence(self):
        raw={'tool_calls':[{'result':{'text':'Full original document.'}}]}
        self.b.results[self.rid]['investigation']=raw
        self.b.meta['user_answer_review']=dict(self.review,answer_fingerprint='mismatched')
        packet=render_projection(self.b)
        self.assertEqual(packet['accepted_analysis_provenance'],[])
        self.assertEqual(packet['data'][0]['data']['investigation'],raw)
        self.assertIsNone(packet['current_saved_status'][0]['independent_user_review'])

if __name__=='__main__':unittest.main()
