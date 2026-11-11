"""Complete dependency fallback preserves abstention, fault and cost semantics."""
import copy,json,os,unittest,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B
from test_complete_claim_composition import packet


class SourceReview(unittest.TestCase):
    def verdict(self,p):
        return {'supported_claim_ids':[c['id']for c in p['claim_manifest']],
            'findings':[],'joint_support':'supported','joint_reason':'Dated consultation and medication statement match the source; current care remains unknown.'}

    def reviewer(self,p,ledger=None):
        with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription','ANTHROPIC_API_KEY':'subscription-transport'}),patch(
            'clinical_core.agere.clinical.legacy_proposers._send',return_value=lambda _:None),patch(
            'clinical_core.agere.clinical.semantic_analyst.tool_loop',return_value={
                'answer':self.verdict(p),'model':'authored-reviewer','new_paid_api_calls':1,'estimated_cost_usd':None}):
            return B.frontier_source_review(SimpleNamespace(),'Report dated care roles and medication statements.',p,ledger=ledger)

    def test_whole_dependency_reaches_frontier_once_and_global_uncertainty_remains(self):
        p=packet();summary={**p,'id':'summary','claim_manifest':[],'patient_dependency_bound':False}
        for global_label in ('supported','unknown'):
            def typed(q,options,evidence,**kw):
                label=global_label if evidence[0]['id']=='summary'else'unknown'
                return {'label':label,'decided_by':'authored'if label=='supported'else None,'steps':[]}
            from clinical_core.agere.gateway import BudgetLedger
            with tempfile.TemporaryDirectory()as folder,patch.object(self,'reviewer',wraps=self.reviewer)as frontier:
                ledger=BudgetLedger(Path(folder)/'attempts.sqlite3')
                try:out=B.check_support_packets([summary,p],typed,as_of=p['date'],ledger=ledger,frontier_decider=frontier)
                finally:ledger.close()
            self.assertEqual(frontier.call_count,1);self.assertEqual(out['label'],global_label)
            result=out['packets'][1]
            self.assertEqual(result['label'],'supported');self.assertEqual(result['typed_review']['label'],'unknown')
            self.assertIsNone(result['calibrated_probability']);self.assertFalse(result['clinical_qualification'])
            self.assertEqual(out['frontier_review_calls'],1);self.assertFalse(out['frontier_review_cost_known'])
            self.assertIsNone(out['frontier_review_cost_usd'])

    def test_deciding_fault_missing_evidence_native_repair_and_partition_do_not_escalate(self):
        base={'id':'patient_A','label':'unknown','decided_by':None}
        changes=[({}, {'label':'contradicted','decided_by':'jev'}),({}, {'label':'supported','decided_by':'jev'}),
            ({'dependency_complete':False},{}),({'source_context_complete':False},{}),
            ({'required_source_packet_ids':['child']},{}),({'source_dependency_parent_id':'parent'},{}),
            ({},{'status':'requires_native_program_repair'}),({},{'status':'awaiting_semantic_dependency'}),
            ({},{'routed_judgment':{'label':'contradicted','decided_by':'jev'}}),
            ({},{'claim_feedback':[{'label':'wrong_role','decided_by':'jev'}]})]
        for source_change,review_change in changes:
            p={**packet(),**source_change};old={**base,**review_change}
            with patch.object(self,'reviewer',wraps=self.reviewer)as frontier:
                out,r=B.resolve_source_abstention(p,old,frontier,ledger=None)
            self.assertEqual(out,old);self.assertIsNone(r);frontier.assert_not_called()

    def test_source_fault_is_localized_and_invalid_or_unavailable_output_stays_unknown(self):
        p=packet();good=self.verdict(p);fault=copy.deepcopy(good)
        fault.update(supported_claim_ids=['claim_1'],joint_support='contradicted',findings=[{
            'claim_id':'claim_0','label':'contradicted','reason':'Authored fault control',
            'evidence_paths':['/source_contexts/0/text']}])
        for kind,verdict in [('fault',fault),('omitted',{**good,'supported_claim_ids':[]}),('unavailable',{})]:
            with patch.object(self,'verdict',return_value=verdict):
                out,review=B.resolve_source_abstention(p,{'id':p['id'],'label':'unknown','decided_by':None},self.reviewer,ledger=None)
            self.assertEqual(out['label'],'contradicted'if kind=='fault'else'unknown')
            if kind=='fault':self.assertEqual(out['claim_feedback'][0]['path'],['answer_rows',0,'relationship'])

    def test_wrong_patient_or_embedded_source_gap_never_dispatches(self):
        for key,value in [('patient_id','B'),('source_context_gaps',['missing note'])]:
            from clinical_core.agere.deliverables.receipts import uncompact_json
            p=packet();state=uncompact_json(json.loads(p['text']));state[key]=value;p['text']=json.dumps(state)
            with self.assertRaises(ValueError):self.reviewer(p)

    def test_actual_subscription_limit_is_propagated(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        def limited(*a,**kw):raise ProviderUnavailable('subscription limit',kind='rate_limit',attempts=[{'status':'failed'}])
        with self.assertRaises(ProviderUnavailable):
            B.resolve_source_abstention(packet(),{'label':'unknown','decided_by':None},limited,ledger=None)


if __name__=='__main__':unittest.main()
