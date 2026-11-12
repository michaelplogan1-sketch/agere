"""Shared source routing preserves exact evidence and escalates only uncertainty."""
import copy,json,unittest
from unittest.mock import Mock
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json
from clinical_core.contracts import digest
from test_complete_claim_composition import packet,fields


class SourceObligations(unittest.TestCase):
    def test_resolved_fields_reuse_without_another_provider_attempt(self):
        p=packet();memory={};whole=Mock(side_effect=AssertionError('Redundant joint call'))
        reader=Mock(side_effect=fields)
        first=B.check_support_packets([p],whole,as_of=p['date'],ledger=None,
            field_decider=reader,prior_field_reviews=memory)
        second=B.check_support_packets([p],whole,as_of=p['date'],ledger=None,
            field_decider=reader,prior_field_reviews=memory)
        self.assertEqual(first['label'],second['label']);self.assertEqual(second['label'],'supported')
        self.assertEqual(reader.call_count,1);whole.assert_not_called()
        self.assertEqual(second['packets'][0]['field_review']['reused_supported_count'],2)
        self.assertEqual(second['steps'],[])

    def test_changed_source_question_date_or_value_invalidates_prior_fields(self):
        p=packet();review=B.localize_support_failure(p,fields,as_of=p['date'],ledger=None)['field_review']
        self.assertEqual(len(B.bound_field_support(p,review)),2)
        for kind in ('source','question','date','field_value','patient','result','threshold','duplicate'):
            q=copy.deepcopy(p);r=copy.deepcopy(review);state=uncompact_json(json.loads(q['text']))
            if kind=='source':state['source_contexts'][0]['text']='Different received source'
            elif kind=='question':state['question']='Different relationship question'
            elif kind=='date':q['date']='2029-01-01'
            elif kind=='patient':q['source_dependency_patient_id']='B'
            elif kind=='result':q['result_view']='changed'
            elif kind=='field_value':state['answer_rows'][0]['relationship']='Ongoing cardiologist'
            elif kind=='threshold':r['supported_claims'][0]['verification_observation']['model_judgment']['probability']=.79
            elif kind=='duplicate':r['supported_claims'][1]=r['supported_claims'][0]
            if kind in ('source','question','field_value'):q['text']=json.dumps(state)
            with self.subTest(kind=kind):self.assertEqual(B.bound_field_support(q,r),[])

    def test_frontier_receives_only_unresolved_claim_with_all_sources_unchanged(self):
        p=packet();seen=[];memory={}
        def partial(task,named,evidence,**kw):
            r=fields(task,named,evidence,**kw)
            r['judgments']['claim_1'].update(label='unknown',decided_by=None,probability=.5)
            return r
        def frontier(target,ledger):
            seen.append(target)
            self.assertEqual([c['id']for c in target['claim_manifest']],['claim_1'])
            original=uncompact_json(json.loads(p['text']));state=uncompact_json(json.loads(target['text']))
            self.assertEqual(state['source_contexts'],original['source_contexts'])
            self.assertEqual(state['answer_rows'],original['answer_rows'])
            self.assertEqual(state['resolved_source_claims']['original_packet_digest'],digest(p['text']))
            return {'status':'reviewed','packet_digest':digest(target['text']),'calls':1,'cost_usd':None,
                'model':'authored','verdict':{'supported_claim_ids':['claim_1'],'findings':[],
                'joint_support':'supported','joint_reason':'All dated fields remain consistent.'}}
        out=B.check_support_packets([p],Mock(side_effect=AssertionError('Redundant joint vote')),
            as_of=p['date'],ledger=None,field_decider=partial,frontier_decider=frontier,prior_field_reviews=memory)
        self.assertEqual(len(seen),1);self.assertEqual(out['label'],'supported')
        self.assertEqual(out['packets'][0]['supported_claim_count'],2)
        self.assertEqual(out['packets'][0]['frontier_review']['reused_supported_claim_ids'],['claim_0'])
        self.assertFalse(out['frontier_review_cost_known'])
        self.assertFalse(out['packets'][0]['clinical_qualification'])
        self.assertEqual(len(B.bound_field_support(p,memory[digest(p['text'])])),1)

    def test_located_fault_and_incomplete_source_cannot_escalate_to_acceptance(self):
        for incomplete in (False,True):
            p=packet();p['source_context_complete']=not incomplete
            def fault(task,named,evidence,**kw):
                r=fields(task,named,evidence,**kw)
                r['judgments']['claim_1'].update(label='wrong_subject')
                return r
            frontier=Mock(side_effect=AssertionError('Unresolved evidence is not a vote'))
            out=B.check_support_packets([p],Mock(side_effect=AssertionError('Unexpected joint call')),
                as_of=p['date'],ledger=None,field_decider=fault,frontier_decider=frontier)
            self.assertNotEqual(out['label'],'supported');frontier.assert_not_called()


if __name__=='__main__':unittest.main()
