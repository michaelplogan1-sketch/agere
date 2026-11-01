"""Observed patient rows must not manufacture additional answer claims."""
import copy,json,unittest
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json

def call(view,rows):
    return {'name':'query_sql','input':{'sql':'SELECT received_source'},'result':{
        'result_view':view,'columns':list(rows[0])if rows else[], 'rows':rows,'read_views':[]}}

def state(packet):return uncompact_json(json.loads(packet['text']))

class FinalClaimSupport(unittest.TestCase):
    def packets(self,answer,calls):return B.support_packets('Received observations',answer,calls,[],'2030-05-27')
    def test_aggregate_does_not_verify_empty_claims_for_unreferenced_diagnostic_patients(self):
        calls=[call('sample',[{'patient_id':'P-1001','currency':'USD'}]),call('total',[{'cash_dollars':12}])]
        before=copy.deepcopy(calls);answer={'headline':'Received cash','table':[{'cash_dollars':12}],'table_from_tool':'total'}
        packets=self.packets(answer,calls)
        self.assertEqual([p['id']for p in packets],['summary']);self.assertEqual(calls,before)
        self.assertEqual(len(state(packets[0])['native_bindings']),2)
        self.assertEqual(state(packets[0])['all_final_rows'],answer['table'])
    def test_final_patient_keeps_all_its_observations_and_complete_claims(self):
        calls=[call('sample',[{'patient_id':'A','reason':'received A'},{'patient_id':'B','reason':'diagnostic B'}])]
        row={'patient_id':'A','reason':'received A'};packets=self.packets({'table':[row]},calls)
        self.assertEqual([p['id']for p in packets],['summary','patient_A'])
        local=state(packets[1]);self.assertEqual(local['answer_rows'],[row]);self.assertEqual(local['evidence'],[row])
        self.assertEqual(local['claim_manifest'][0]['field'],'reason')
        self.assertEqual(packets[1]['source_dependency_patient_id'],'A')
    def test_global_patient_set_retains_native_membership_observations(self):
        rows=[{'patient_id':'A'},{'patient_id':'B'}];packets=self.packets({'patient_ids':['A','B']},[call('members',rows)])
        self.assertEqual([p['id']for p in packets],['summary'])
        observed=state(packets[0])['global_patient_evidence'];self.assertEqual([r for p in observed for r in p['native_observations']],rows)
    def test_global_citation_keeps_actual_context_without_an_empty_local_packet(self):
        doc={'record_id':'N','patient_id':'B','date':'2030-01-01','text':'Actual received statement.'}
        answer={'table':[{'n':1}],'sources':[{'record_id':'N','date':'2030-01-01','quote':'received statement'}]}
        packets=self.packets(answer,[{'name':'read_document','input':{'record_id':'N'},'result':doc}])
        self.assertEqual([p['id']for p in packets],['summary'])
        self.assertEqual(state(packets[0])['cited_source_evidence'][0]['executed_source_document'],doc)
    def test_opaque_packet_label_never_invents_or_rewrites_a_patient_identifier(self):
        support={'packets':[{'id':'patient_patient_A','label':'unknown'},{'id':'patient_B','label':'unknown'},
            {'id':'summary','label':'supported','decided_by':'fixture'}]}
        out=B.unresolved_support_bindings(support,[{'id':'patient_patient_A','source_dependency_patient_id':'patient_A'}])
        self.assertEqual(out,[{'packet_id':'patient_patient_A','patient_id':'patient_A'},{'packet_id':'patient_B'}])

if __name__=='__main__':unittest.main()
