"""Shared source routing preserves exact evidence and escalates only uncertainty."""
import copy,json,unittest
from unittest.mock import Mock
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json
from clinical_core.contracts import digest
from test_complete_claim_composition import packet,fields


class SourceObligations(unittest.TestCase):
    def test_frontier_interruption_keeps_completed_typed_fields(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        p=packet();memory={}
        def partial(task,named,evidence,**kw):
            r=fields(task,named,evidence,**kw)
            r['judgments']['claim_1'].update(label='unknown',decided_by=None,probability=.5)
            return r
        with self.assertRaises(ProviderUnavailable) as caught:
            B.check_support_packets([p],Mock(side_effect=AssertionError('Redundant joint vote')),
                as_of=p['date'],ledger=None,field_decider=partial,prior_field_reviews=memory,
                frontier_decider=Mock(side_effect=ProviderUnavailable('authored subscription limit',kind='rate_limit')))
        self.assertEqual(len(B.bound_field_support(p,memory.get(digest(p['text'])))),1)
        support=caught.exception.agere_partial_source_review
        self.assertFalse(support['all_dependencies_checked']);self.assertEqual(support['label'],'unknown')
        self.assertFalse(support['frontier_review_cost_known']);self.assertEqual(len(support['interruptions']),1)

    def test_later_field_batch_failure_retains_first_batch_without_acceptance(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        p=packet({'patient_id':'A',**{'field_'+str(i):'Received text'for i in range(65)}});memory={};seen=[]
        def reader(task,named,evidence,**kw):
            seen.append(len(named))
            if len(seen)>1:raise ProviderUnavailable('authored provider interruption',kind='rate_limit')
            return fields(task,named,evidence,**kw)
        with self.assertRaises(ProviderUnavailable) as caught:
            B.check_support_packets([p],Mock(side_effect=AssertionError('No joint call')),
                as_of=p['date'],ledger=None,field_decider=reader,prior_field_reviews=memory)
        self.assertEqual(seen,[64,1]);self.assertEqual(len(B.bound_field_support(p,memory[digest(p['text'])])),64)
        self.assertEqual(caught.exception.agere_partial_source_review['packets'][0]['field_review']['unchecked_count'],1)

    def test_interruption_drains_started_work_but_dispatches_no_new_packet(self):
        import threading,os
        from unittest.mock import patch
        observed=threading.Event();started=threading.Event();seen=[];memory={}
        class Interrupted(RuntimeError):
            @property
            def kind(self):
                if threading.current_thread()is threading.main_thread():observed.set()
                return 'rate_limit'
        packets=[]
        for i in range(4):
            p=packet(source='Complete authored source '+str(i));p['id']='packet_'+str(i);packets.append(p)
        def reader(task,named,evidence,**kw):
            ident=evidence[0]['id'];seen.append(ident)
            if ident=='packet_1':
                self.assertTrue(started.wait(2));raise Interrupted('authored failure')
            if ident=='packet_2':
                started.set();self.assertTrue(observed.wait(2))
            return fields(task,named,evidence,**kw)
        with patch.dict(os.environ,{'AGERE_SYSTEM_ONE_WORKERS':'2'}),self.assertRaises(Interrupted)as caught:
            B.check_support_packets(packets,Mock(side_effect=AssertionError('No joint call')),
                as_of=packets[0]['date'],ledger=None,field_decider=reader,prior_field_reviews=memory)
        support=caught.exception.agere_partial_source_review
        self.assertEqual(set(seen),{'packet_0','packet_1','packet_2'})
        self.assertEqual(support['unchecked_packet_ids'],['packet_3'])
        self.assertEqual(support['packets'][2]['label'],'supported')
        self.assertEqual(len(memory),2)

    def test_saved_draft_reviewer_interruption_reaches_actor_recovery_without_generation(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from clinical_core.agere.clinical import semantic_analyst as S,system_one_gate as G
        from clinical_core.agere.gateway import ProviderUnavailable
        p=packet();tools=SimpleNamespace(read={},document_reviews=[],source_records={})
        candidate={'status':'partial','answer_type':'composite','headline':'Retained source account.',
            'facts':{},'sources':[],'table':[],'limitations':[]}
        def reader(task,named,evidence,**kw):
            r=fields(task,named,evidence,**kw);r['judgments']['claim_1'].update(label='unknown',decided_by=None,probability=.5)
            r['steps']=[{'provider':'authored','status':'answered','cost_usd':.03,'attempt_id':'fixture-only'}];return r
        s1=SimpleNamespace(judge_fields=reader,decide=Mock(side_effect=AssertionError('No joint call')))
        def review(a,calls):return B.review_final(SimpleNamespace(path=None),'Show source account',a,calls,tools,ledger=None,as_of=p['date'])
        with patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=s1),patch.object(G,'check',return_value={
            'label':'answers_fully','decided_by':'authored','calls':0,'cost_usd':0}),patch.object(B,'support_packets',return_value=[p]),patch.object(
            B,'frontier_source_review',side_effect=ProviderUnavailable('authored subscription limit',kind='rate_limit')),patch.dict(
            'os.environ',{'AGERE_FRONTIER_SOURCE_REVIEW':'on'}):
            run=S.tool_loop('Show source account','system',[],Mock(),send=Mock(side_effect=AssertionError('No new actor call')),
                model='authored',max_tools=None,review=review,initial_answer=candidate)
        self.assertEqual(run['review_stopped'],'review_interrupted')
        self.assertEqual(run['answer']['status'],'partial');self.assertEqual(run['answer']['verification'],'unverified')
        self.assertFalse(run['answer_reviews'][0]['cost_known']);self.assertIsNone(run['answer_reviews'][0]['cost_usd'])
        self.assertEqual(run['answer_reviews'][0]['known_cost_usd'],.03)
        self.assertEqual(run['answer_reviews'][0]['support']['packets'][0]['field_review']['supported_count'],1)

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
