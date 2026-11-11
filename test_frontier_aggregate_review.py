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

if __name__=='__main__':unittest.main()
