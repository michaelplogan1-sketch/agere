"""An aggregate reviewer cannot invent confidence or omit claim obligations."""
import copy,json,os,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import readable_json

class FrontierAggregateReview(unittest.TestCase):
    def fixture(self):
        state={'id':'summary','answer':{'headline':'Two records, no savings estimate.'},
            'all_final_rows':[{'count':2}], 'shared_evidence':[{'count':2}],
            'required_clinical_dependencies':[],
            'claim_manifest':[{'id':'headline','field':'headline','path':['answer','headline']},
                {'id':'count','field':'table.0.count','path':['all_final_rows',0,'count']}]}
        packet={'id':'summary','text':json.dumps(readable_json(state)),
            'claim_manifest':state['claim_manifest'],'dependency_complete':True,'source_context_complete':True}
        verdict={'coverage':{'label':'answers_fully','reason':'Count and missing savings input are explicit.','unaddressed_request_quotes':[]},
            'supported_claim_ids':['headline','count'],'findings':[],'joint_support':'supported','joint_reason':'Count is bound to its received source; savings remain unknown.'}
        return packet,verdict

    def run_review(self,packet,verdict):
        with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription','ANTHROPIC_API_KEY':'subscription-transport'}),\
             patch('clinical_core.agere.clinical.legacy_proposers._send',return_value=lambda _:None),\
             patch('clinical_core.agere.clinical.semantic_analyst.tool_loop',return_value={
                 'answer':verdict,'model':'authored-reviewer','new_paid_api_calls':0,'estimated_cost_usd':0}):
            return B.frontier_aggregate_review(SimpleNamespace(), 'Count records and estimate savings.',packet,ledger=None)

    def test_complete_review_preserves_machine_authority_without_probabilities(self):
        packet,verdict=self.fixture();out=self.run_review(packet,verdict)
        self.assertEqual(out['status'],'reviewed');self.assertIsNone(out['calibrated_probability'])
        self.assertFalse(out['clinical_qualification']);self.assertEqual(out['verdict'],verdict)

    def test_missing_duplicate_fabricated_claims_quotes_and_source_paths_fail_closed(self):
        for kind in ('omitted','duplicate','foreign_claim','fake_quote','fake_path','conflict','fake_label'):
            packet,verdict=self.fixture()
            if kind=='omitted':verdict['supported_claim_ids'].pop()
            elif kind=='duplicate':verdict['supported_claim_ids'].append('count')
            elif kind=='foreign_claim':verdict['supported_claim_ids'][1]='invented'
            elif kind=='fake_quote':verdict['coverage'].update(label='answers_partially',unaddressed_request_quotes=['in 2020'])
            elif kind=='fake_label':verdict['coverage']['label']='qualified'
            else:
                verdict['supported_claim_ids']=['headline'];verdict['findings']=[{'claim_id':'count','label':'contradicted','reason':'Mismatch','evidence_paths':['/invented'if kind=='fake_path'else'/shared_evidence/0/count']}]
                if kind=='fake_path':verdict['joint_support']='contradicted'
            with self.subTest(kind=kind):self.assertEqual(self.run_review(packet,verdict)['status'],'unavailable')

    def test_located_fault_remains_a_fault(self):
        packet,verdict=self.fixture();verdict['supported_claim_ids']=['headline']
        verdict.update(joint_support='contradicted',findings=[{'claim_id':'count','label':'contradicted',
            'reason':'Authored mismatch','evidence_paths':['/shared_evidence/0/count']}])
        self.assertEqual(self.run_review(packet,verdict)['verdict']['joint_support'],'contradicted')
        verdict['findings'][0]['evidence_paths']=['/packet_state/shared_evidence/0/count']
        self.assertEqual(self.run_review(packet,verdict)['status'],'reviewed')

    def test_incomplete_source_or_patient_dependency_never_dispatches(self):
        packet,verdict=self.fixture();packet['source_context_complete']=False
        with self.assertRaises(ValueError):self.run_review(packet,verdict)
        packet,verdict=self.fixture();packet['required_source_packet_ids']=['patient_A']
        with self.assertRaises(ValueError):self.run_review(packet,verdict)

class FrontierOverflowRouting(unittest.TestCase):
    def test_capacity_refusal_reaches_verifier_and_never_overrides_decided_fault(self):
        from test_native_column_projection import NativeColumnProjection
        from clinical_core.agere.clinical import system_one_gate as gate
        fixture=NativeColumnProjection(methodName='runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        question='Count machine receipts and unresolved statuses.'
        tools=SimpleNamespace(document_reviews=[],executed_results={})
        coverage={'label':'answers_fully','decided_by':'authored-coverage','calls':0,'cost_usd':0.}
        packet=B.support_packets(question,fixture.answer,fixture.calls,[],'2030-05-27')[0]
        verdict={'coverage':{'label':'answers_fully','reason':'All requested observations present.','unaddressed_request_quotes':[]},
            'supported_claim_ids':[c['id']for c in packet['claim_manifest']],'findings':[],
            'joint_support':'supported','joint_reason':'All scoped facts match the authored evidence.'}
        for mode in ('reviewed','unavailable','decided_fault'):
            support={'label':'unknown','decided_by':None,'packets':[{'id':'summary','input_error':'dependency_envelope_exceeded'}],
                'steps':[],'claim_feedback':[]}
            if mode=='decided_fault':support.update(label='contradicted',decided_by='typed-review',
                claim_feedback=[{'decided_by':'typed-review','label':'contradicted'}])
            result={'status':'unavailable'if mode=='unavailable'else'reviewed','verdict':verdict,
                'calls':1,'cost_usd':.1,'model':'authored-reviewer','packet_digest':'bound'}
            with self.subTest(mode=mode),patch.dict(os.environ,{'AGERE_FRONTIER_AGGREGATE_REVIEW':'on','AGERE_QUANTITATIVE_ROLE_CHECK':'off'}),patch.object(
                    gate,'ready',return_value=True),patch.object(gate,'check',return_value=coverage),patch.object(
                    gate,'s1',return_value=SimpleNamespace(decide=None)),patch.object(gate,'cost',return_value=(0,0.)),patch.object(
                    B,'check_support_packets',return_value=support),patch.object(B,'frontier_aggregate_review',return_value=result)as frontier:
                out=B.review_final(SimpleNamespace(),question,copy.deepcopy(fixture.answer),fixture.calls,tools,ledger=None,as_of='2030-05-27')
            if mode=='reviewed':
                self.assertEqual(frontier.call_count,1,out);self.assertEqual(out['status'],'confirmed',out)
                self.assertEqual(out['typed_support']['packets'][0]['input_error'],'dependency_envelope_exceeded')
                self.assertFalse(out['coverage']['clinical_qualification']);self.assertEqual(out['cost_usd'],.1)
            elif mode=='unavailable':
                self.assertEqual(frontier.call_count,1,out);self.assertEqual(out['repair_kind'],'verification_input_construction')
            else:self.assertEqual(frontier.call_count,0,out)

if __name__=='__main__':unittest.main()
