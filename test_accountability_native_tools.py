"""Payment prerequisites remain unbound through the complete native tool lane."""
import copy,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import analysis_tools as A,program_accountability as P,financial_accounts as F
import test_documentary_tool_handoff as fixture
from test_aggregate_explain import AS_OF


class AccountabilityTools(unittest.TestCase):
    def setUp(self):
        fixture.DocumentaryHandoff.setUp(self)
        with sqlite3.connect(self.path/'stack.sqlite3')as db:
            db.execute('CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT)')
    tools=fixture.DocumentaryHandoff.tools

    def execute(self,t,**extra):
        return t({'name':'execute_analysis','input':{'operation':'accountability_review',
            'parameters':{'contract':'program_payment_evidence','available_by':AS_OF},**extra}})

    def test_discover_describe_and_compose_all_members_without_multiplying_global_gaps(self):
        t=self.tools();menu=t({'name':'list_analyses','input':{}})
        self.assertIn('accountability_review',[r['operation']for r in menu['analyses']])
        schema=t({'name':'describe_analysis','input':{'operation':'accountability_review'}})['parameters']
        self.assertIn('program_payment_evidence',schema['properties']['contract']['enum'])
        result=self.execute(t);self.assertEqual(len(result['rows']),12)
        report=result['engine_result']['results'][-1]['result']
        self.assertEqual(len(report['patients']),6);self.assertEqual(len(report['review_members']),12)
        self.assertEqual(len(report['contract_requests']),3)
        self.assertEqual(report['request_counts']['program_input_review'],24)
        query="SELECT patient_id,json_extract(documentary_receipt,'$.payment_readiness') AS payment_readiness,json_extract(documentary_receipt,'$.payment_entitlement_established') AS entitlement FROM "+result['result_view']+' ORDER BY patient_id'
        rows=t({'name':'query_sql','input':{'sql':query}})['rows']
        self.assertEqual([r['patient_id']for r in rows],self.ids)
        self.assertTrue(all(r['payment_readiness']=='unresolved'and r['entitlement']==0 for r in rows))
        self.assertTrue(all(req['status']=='unbound'for r in result['rows']for req in r['documentary_receipt']['requirements']))

    def test_empty_scope_future_cutoff_and_foreign_patient_do_not_execute(self):
        t=self.tools(['p00'])
        self.assertEqual(self.execute(t,patient_ids=[])['rows'],[])
        with self.assertRaisesRegex(ValueError,'outside this request'):self.execute(t,patient_ids=['p01'])
        with patch.object(P,'prepare',side_effect=AssertionError('Do not prepare invalid future cutoff')):
            with self.assertRaises(ValueError):
                t({'name':'execute_analysis','input':{'operation':'accountability_review','parameters':{'contract':'program_payment_evidence','available_by':'2035-01-01'}}})

    def test_missing_or_mutated_receipt_cannot_become_payment_readiness(self):
        result=self.execute(self.tools());report=result['engine_result']['results'][-1]['result']
        changed=copy.deepcopy(report);changed['review_members'][0]['status']='ready'
        with self.assertRaisesRegex(ValueError,'readiness differs'):A.documentary_rows(self.path,'accountability_review',changed)
        with P.storage(self.path)as db:
            # Tests disable only the immutable deletion guard to simulate loss.
            db.execute('DROP TRIGGER receipt_no_delete')
            db.execute('DELETE FROM receipts WHERE id=?',(report['review_members'][0]['receipt_id'],))
        with self.assertRaisesRegex(ValueError,'Missing documentary'):A.documentary_rows(self.path,'accountability_review',report)


if __name__=='__main__':unittest.main()
