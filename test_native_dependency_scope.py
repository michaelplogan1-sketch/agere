"""Patient checks use final native dependencies without discarding document context."""
import copy,json,unittest
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json


def sql(view, query, rows):
    return {'name':'query_sql','input':{'sql':query},'result':{
        'result_view':view,'columns':list(rows[0]),'rows':copy.deepcopy(rows)}}


class NativeDependencyScope(unittest.TestCase):
    def fixture(self):
        old=[{'patient_id':'A','claims':12,'allowed_cents':9000}]
        rows=[{'patient_id':'A','claims':3,'allowed_cents':1200}]
        calls=[sql('payer_diagnostic','SELECT * FROM payer_claim',old),
               sql('provider_base',"SELECT * FROM claim WHERE claim_source='medical_claims'",rows),
               sql('final','SELECT * FROM provider_base',rows)]
        return {'table':copy.deepcopy(rows),'table_from_tool':'final','headline':'Provider observations'},calls

    def packets(self,answer,calls,reviews=None):
        packets=B.support_packets('Show provider claims by patient.',answer,calls,reviews or[],'2030-05-27')
        return {p['id']:(p,uncompact_json(json.loads(p['text'])))for p in packets}

    def test_unrelated_patient_sql_does_not_enter_the_local_dependency(self):
        answer,calls=self.fixture();original=copy.deepcopy(calls)
        packets=self.packets(answer,calls);local=packets['patient_A'][1]
        self.assertEqual(local['evidence'],answer['table'])
        self.assertEqual({c['result_view']for c in local['native_calculation_contracts']},{'provider_base','final'})
        self.assertNotIn('payer_diagnostic',json.dumps(local))
        self.assertEqual(packets['summary'][1]['native_bindings'][0]['result_view'],'payer_diagnostic')
        self.assertEqual(calls,original)

    def test_unused_sql_changes_do_not_invalidate_local_check_but_global_receipt_changes(self):
        answer,calls=self.fixture();before=self.packets(answer,calls)
        calls[0]['result']['rows'][0]['allowed_cents']=99999
        after=self.packets(answer,calls)
        self.assertEqual(before['patient_A'][0],after['patient_A'][0])
        self.assertNotEqual(before['summary'][0]['text'],after['summary'][0]['text'])

    def test_identical_values_do_not_reintroduce_unrelated_origin_contracts(self):
        answer,calls=self.fixture();calls[0]['result']['rows']=copy.deepcopy(answer['table'])
        local=self.packets(answer,calls)['patient_A'][1]
        views={o['result_view']for binding in local['evidence_provenance']for o in binding['origins']}
        self.assertEqual(views,{'provider_base','final'})

    def test_changed_amount_date_and_source_in_required_dependency_remain_visible(self):
        answer,calls=self.fixture();before=self.packets(answer,calls)['patient_A'][0]['text']
        for change in ('amount','date','source'):
            altered=copy.deepcopy(calls)
            if change=='amount':altered[1]['result']['rows'][0]['allowed_cents']=1201
            elif change=='date':altered[1]['result']['rows'][0]['service_date']='2030-04-01'
            else:altered[1]['input']['sql']='SELECT * FROM another_received_source'
            self.assertNotEqual(before,self.packets(answer,altered)['patient_A'][0]['text'])

    def test_candidate_keeps_superseded_composed_drafts_out_of_the_patient_packet(self):
        import os
        from unittest.mock import patch
        answer,calls=self.fixture()
        draft=[{'patient_id':'A','claims':3,'documentary_findings':'superseded draft statement'}]
        calls.insert(2,{'name':'compose_table','input':{'base':'provider_base','keys':['patient_id'],'joins':[]},
            'result':{'result_view':'old_draft','columns':list(draft[0]),'rows':copy.deepcopy(draft)}})
        original=copy.deepcopy(calls)
        with patch.dict(os.environ,{'AGERE_PACKET_COMPOSE_CLOSURE':'off'}):before=self.packets(answer,calls)['patient_A']
        with patch.dict(os.environ,{'AGERE_PACKET_COMPOSE_CLOSURE':'on'}):after=self.packets(answer,calls)['patient_A']
        self.assertIn('superseded draft statement',json.dumps(before[1]['evidence']))   # today's behavior
        self.assertEqual(after[1]['evidence'],answer['table'])
        self.assertNotIn('old_draft',json.dumps(after[1]))
        # Same claims, final rows and completeness; the draft stays in its native receipt.
        for key in ('claim_manifest','source_context_complete','dependency_complete','result_view'):self.assertEqual(before[0][key],after[0][key])
        self.assertEqual(before[1]['answer_rows'],after[1]['answer_rows']);self.assertEqual(calls,original)
        # A composed table inside the final view's lineage is still supplied.
        chained=copy.deepcopy(calls);chained[3]['input']['sql']='SELECT * FROM old_draft'
        with patch.dict(os.environ,{'AGERE_PACKET_COMPOSE_CLOSURE':'on'}):
            self.assertIn('superseded draft statement',json.dumps(self.packets(answer,chained)['patient_A'][1]['evidence']))
            # Unknown final lineage stays conservative.
            unknown=dict(answer,table_from_tool='unavailable')
            self.assertIn('superseded draft statement',json.dumps(self.packets(unknown,calls)['patient_A'][1]['evidence']))

    def test_unknown_final_lineage_conservatively_retains_all_observations(self):
        answer,calls=self.fixture();answer['table_from_tool']='unavailable'
        local=self.packets(answer,calls)['patient_A'][1]
        self.assertEqual(len(local['evidence']),2)
        self.assertIn('payer_diagnostic',json.dumps(local))

    def test_documents_and_machine_source_reviews_remain_even_without_sql_dependency(self):
        answer,calls=self.fixture()
        doc={'record_id':'n1','patient_id':'A','date':'2030-05-01','text':'The source records a historical hold; current use is not established.'}
        calls.append({'name':'read_document','input':{'record_id':'n1'},'result':{**doc,'result_view':'document_context'}})
        finding={'patient_id':'A','review_json':'{"reason":[{"record_id":"n1","status":"historical"}]}'}
        review={'questions':{'reason':'Why was treatment held?'},'manifest':[{'patient_id':'A','record_id':'n1'}],'rows':[finding]}
        local=self.packets(answer,calls,[review])['patient_A'][1]
        self.assertIn(doc['text'],json.dumps(local))
        self.assertIn({**finding,'review_json':json.loads(finding['review_json'])},local['evidence'])
        self.assertEqual(local['review_scope'][0]['questions'],review['questions'])

    def test_necessary_clinical_fields_and_complete_claim_manifest_are_unchanged(self):
        answer,calls=self.fixture()
        finding={'record_id':'n1','date':'2030-04-01','quote':'No ongoing assignment is recorded.','status':'unknown'}
        for rows in (answer['table'],calls[1]['result']['rows'],calls[2]['result']['rows']):rows[0]['clinical_role']=copy.deepcopy(finding)
        packet,local=self.packets(answer,calls)['patient_A']
        self.assertEqual(local['answer_rows'],answer['table'])
        self.assertIn(answer['table'][0],local['evidence'])
        self.assertEqual({c['field']for c in packet['claim_manifest']},{'claims','allowed_cents','clinical_role'})
        self.assertEqual(next(row['clinical_role']for row in local['evidence']if 'clinical_role'in row),finding)


if __name__=='__main__':unittest.main()
