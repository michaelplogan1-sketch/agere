"""Unused diagnostic reviews cannot swamp final-field source verification."""
import copy, json, unittest
from clinical_core.agere.clinical import backstop_agent as B, document_review as D
from clinical_core.agere.deliverables.receipts import uncompact_json
from clinical_core.contracts import digest


class NativeReviewScope(unittest.TestCase):
    def fixture(self):
        question={'question':'Why was MRI deferred?',
                  'entity':{'kind':'service','id':'mri','aliases':['MRI']},
                  'relation':'reason_for_deferral','subject':'patient'}
        quote='MRI deferred while the appointment is verified.'
        doc={'patient_id':'A','record_id':'r','date':'2030-01-01',
             'text':quote+' Full source context, including opposing observations.'}
        finding=D.relation_finding('A','reason',question,doc,
            {'start':0,'end':len(quote),'text':quote},
            {'polarity':'affirmed','temporality':'current','subject':'patient'},'authored_test')
        used={'patient_id':'A','source_findings_json':json.dumps([finding]),
              'review_unresolved_json':'[]','review_status':'machine_review'}
        unused={'patient_id':'A','source_findings_json':'[]',
                'review_unresolved_json':json.dumps([{'record_id':'other-'+str(i),
                    'field':'different_question','status':'unknown','reason':'Unresolved source interpretation.'}
                    for i in range(3000)]),'review_status':'partial'}
        def review(view,row):
            return {'name':'review_documents','input':{'patient_ids':['A'],'questions':{'reason':question}},
                    'result':{'result_view':view,'columns':list(row),'rows':[row]}}
        final={'patient_id':'A','findings':[finding]}
        calls=[review('agere_result_1',used),review('agere_result_2',unused),
               {'name':'query_sql','input':{'sql':'SELECT patient_id, source_findings_json FROM agere_result_1'},
                'result':{'result_view':'agere_result_3','columns':list(final),'rows':[final]}}]
        reviews=[{'questions':{'reason':question},'manifest':[{'patient_id':'A','unknown':i==1}],
                  'rows':[copy.deepcopy(row)]}for i,row in enumerate((used,unused))]
        answer={'headline':'Dated documentary observation.','facts':{},'table':[final],
                'table_binding':{'result_view':'agere_result_3'}}
        return calls,reviews,answer,doc

    def state(self,calls,reviews,answer,doc):
        packets=B.support_packets('Compare source-bound observations.',answer,calls,reviews,
                                  '2030-05-27',source_documents={'r':doc})
        return uncompact_json(json.loads(next(p['text']for p in packets if p['id']=='patient_A'))),packets

    def test_unused_complete_review_is_native_history_not_another_final_claim(self):
        calls,reviews,answer,doc=self.fixture();before=copy.deepcopy((calls,reviews,answer,doc))
        state,packets=self.state(calls,reviews,answer,doc)
        self.assertFalse(any(r.get('review_status')=='partial'for r in state['evidence']))
        self.assertTrue(any(r.get('review_status')=='machine_review'for r in state['evidence']))
        self.assertEqual(state['answer_rows'],answer['table']);self.assertEqual(state['source_contexts'],[doc])
        self.assertEqual(state['claim_manifest'],[{'id':'claim_0','field':'findings','path':['answer_rows',0,'findings']}])
        self.assertEqual(len(state['review_scope']),2)
        refs=state['unbound_review_receipts'];self.assertEqual(refs['authority'],'native_lineage_only')
        self.assertEqual(refs['required_result_views'],['agere_result_1','agere_result_3'])
        row=calls[1]['result']['rows'][0]
        self.assertEqual(refs['rows'][0]['row_digest'],digest({**row,'source_findings_json':[],
            'review_unresolved_json':json.loads(row['review_unresolved_json'])}))
        self.assertEqual((calls,reviews,answer,doc),before)
        self.assertTrue(all(p['bytes']<230000 for p in packets))
        # Native lineage never turns an unresolved semantic judgment into support.
        out=B.check_support_packets(packets,lambda *a,**k:{'label':'unknown','decided_by':None},
                                    as_of='2030-05-27',ledger=None)
        self.assertEqual(out['label'],'unknown');self.assertIsNone(out['decided_by'])

    def test_a_consumed_review_keeps_every_unresolved_span(self):
        calls,reviews,answer,doc=self.fixture()
        calls[-1]['input']['sql']+=' JOIN agere_result_2 USING(patient_id)'
        state,_=self.state(calls,reviews,answer,doc)
        row=next(r for r in state['evidence']if r.get('review_status')=='partial')
        self.assertEqual(row['review_unresolved_json'],json.loads(calls[1]['result']['rows'][0]['review_unresolved_json']))
        self.assertNotIn('unbound_review_receipts',state)

    def test_unknown_final_lineage_preserves_all_reviews(self):
        calls,reviews,answer,doc=self.fixture();answer['table_binding']['result_view']='unreceived'
        state,_=self.state(calls,reviews,answer,doc)
        self.assertTrue(any(r.get('review_status')=='partial'for r in state['evidence']))
        self.assertNotIn('unbound_review_receipts',state)

    def test_detached_or_changed_review_row_stays_conservative(self):
        for change in ('detached','changed'):
            calls,reviews,answer,doc=self.fixture()
            if change=='detached':calls.pop(1)
            else:reviews[1]['rows'][0]['review_status']='changed_unbound'
            state,_=self.state(calls,reviews,answer,doc)
            expected='partial'if change=='detached'else'changed_unbound'
            with self.subTest(change=change):self.assertTrue(any(r.get('review_status')==expected for r in state['evidence']))

    def test_patient_prose_basis_retains_additional_native_review(self):
        calls,reviews,answer,doc=self.fixture()
        answer['computed_prose']=[{'field':'patient_reason','entity_key':{'patient_id':'A'},
            'basis':[{'result_view':'agere_result_2','row':0}], 'sources':{}}]
        state,_=self.state(calls,reviews,answer,doc)
        self.assertTrue(any(r.get('review_status')=='partial'for r in state['evidence']))
        self.assertNotIn('unbound_review_receipts',state)

    def test_direct_document_without_native_view_is_never_hidden(self):
        calls,reviews,answer,doc=self.fixture()
        calls.append({'name':'read_document','input':{'record_id':'other'},
                      'result':{'patient_id':'A','record_id':'other','date':'2030-01-02','text':'Opposing report.'}})
        state,_=self.state(calls,reviews,answer,doc)
        self.assertTrue(any(r.get('tool')=='read_document'and r.get('result',{}).get('record_id')=='other'
                            and r['result']['text']=='Opposing report.'for r in state['evidence']))


if __name__=='__main__':unittest.main()
