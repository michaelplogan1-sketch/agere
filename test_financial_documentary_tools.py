"""Recorded account stocks do not establish self-reported hardship.

Authored source fixtures through the real financial/state/native tool boundary;
no protected references, provider calls or clinical inference.
"""
import json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import analysis_tools as A,financial_accounts as F
import test_documentary_tool_handoff as documentary_fixture
from test_financial_accounts import snapshot
from test_aggregate_explain import AS_OF


class FinancialDocumentaryTools(unittest.TestCase):
    setUpBase=documentary_fixture.DocumentaryHandoff.setUp
    tools=documentary_fixture.DocumentaryHandoff.tools
    def setUp(self):
        self.setUpBase()
        with sqlite3.connect(self.path/'stack.sqlite3')as db:
            db.execute('CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT)')
            for pid,amounts in [('p00',dict(billed_cents=100,patient_responsibility_cents=100,received_cents=100,balance_cents=0)),
                                ('p01',dict(billed_cents=1000,patient_responsibility_cents=1000,received_cents=600,balance_cents=400))]:
                raw=snapshot(day='2029-08-01',**amounts);raw.update(patient_id=pid,record_id='stock-'+pid,account_id='account-'+pid,available_date='2029-08-01')
                db.execute('INSERT INTO records VALUES (?,?,?,?)',(raw['record_id'],pid,raw['source_table'],json.dumps(raw)))

    def finance(self,t):
        return t({'name':'execute_analysis','input':{'operation':'financial_accounts','parameters':{'available_by':AS_OF}}})

    def test_full_financial_population_and_documentary_membership_are_composable(self):
        t=self.tools();menu=t({'name':'list_analyses','input':{}})
        self.assertIn('financial_accounts',[r['operation']for r in menu['analyses']])
        schema=t({'name':'describe_analysis','input':{'operation':'financial_accounts'}})['parameters']
        self.assertIn('available_by',schema['properties'])
        financial=self.finance(t);self.assertEqual(len(financial['rows']),12)
        report=financial['engine_result']['results'][-1]['result'];self.assertEqual(len(report['review_members']),12)
        state=t({'name':'execute_analysis','input':{'operation':'source_state','parameters':{'streams':['financial','notes'],
              'concept':'financial hardship','relation':'reported_barrier','clock':'source_statement_date','filter':'all'}}})
        query="SELECT f.patient_id,json_extract(f.documentary_receipt,'$.accounts[0].snapshot_amounts.balance_cents') AS recorded_balance_cents,json_extract(s.documentary_receipt,'$.value') AS documented_hardship_polarity FROM "+financial['result_view']+' f LEFT JOIN '+state['result_view']+" s ON s.patient_id=f.patient_id WHERE f.patient_id IN ('p00','p01','p02') ORDER BY f.patient_id"
        joined=t({'name':'query_sql','input':{'sql':query}})
        self.assertEqual(joined['rows'],[
            {'patient_id':'p00','recorded_balance_cents':0,'documented_hardship_polarity':'affirmed'},
            {'patient_id':'p01','recorded_balance_cents':400,'documented_hardship_polarity':'negated'},
            {'patient_id':'p02','recorded_balance_cents':None,'documented_hardship_polarity':None}])

    def test_inherited_subset_and_empty_financial_population_never_widen(self):
        result=self.finance(self.tools(['p00','p02']))
        self.assertEqual([r['patient_id']for r in result['rows']],['p00','p02'])
        t=self.tools(['p00'])
        with self.assertRaisesRegex(ValueError,'outside this request'):
            t({'name':'execute_analysis','input':{'operation':'financial_accounts','parameters':{'available_by':AS_OF},'patient_ids':['p01']}})
        empty=t({'name':'execute_analysis','input':{'operation':'financial_accounts','parameters':{'available_by':AS_OF},'patient_ids':[]}})
        self.assertEqual(empty['rows'],[])

    def test_future_cutoff_is_rejected_before_preparing_financial_sources(self):
        with patch.object(F,'prepare',side_effect=AssertionError('No preparation for future cutoff')):
            with self.assertRaises(ValueError):
                self.tools()({'name':'execute_analysis','input':{'operation':'financial_accounts','parameters':{'available_by':'2035-01-01'}}})

    def test_missing_financial_receipt_does_not_become_zero_balance(self):
        result=self.finance(self.tools());report=result['engine_result']['results'][-1]['result']
        with F.storage(self.path)as db:db.execute('DELETE FROM receipts WHERE id=?',(report['review_members'][0]['receipt_id'],))
        with self.assertRaisesRegex(ValueError,'Missing documentary'):A.documentary_rows(self.path,'financial_accounts',report)


if __name__=='__main__':unittest.main()
