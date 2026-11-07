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

    def test_native_conservation_preserves_refunds_credits_and_separate_cash_observations(self):
        with sqlite3.connect(self.path/'stack.sqlite3')as db:
            raw=json.loads(db.execute('SELECT payload FROM records WHERE id=?',('stock-p01',)).fetchone()[0])
            raw.update(received_cents=1500,refunded_cents=200,balance_cents=0,patient_credit_cents=300)
            db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(raw),'stock-p01'))
        t=self.tools();description=t({'name':'describe_analysis','input':{'operation':'financial_accounts'}})
        self.assertEqual(description['financial_source_contract']['identities'],F.mapping()['identities'])
        result=self.finance(t)
        accounts=t({'name':'query_sql','input':result['native_projections']['accounts']['query_sql']})['rows']
        self.assertEqual(len(accounts),2)
        account=next(r for r in accounts if r['patient_id']=='p01')
        self.assertEqual(account['snapshot_refunded_cents'],200)
        self.assertEqual(account['snapshot_patient_credit_cents'],300)
        self.assertEqual(account['snapshot_patient_responsibility_cents']-account['snapshot_received_cents']-account['snapshot_balance_cents'],-500)
        identities=t({'name':'query_sql','input':result['native_projections']['identities']['query_sql']})['rows']
        self.assertEqual(len(identities),4)
        self.assertTrue(all(r['residual_cents']==0 and r['identity_status']=='balanced'for r in identities))
        self.assertTrue(all(r['patient_id']in ('p00','p01')and r['account_id']=='account-'+r['patient_id']for r in identities))
        cash=t({'name':'query_sql','input':result['native_projections']['cash_comparisons']['query_sql']})['rows']
        paid=next(r for r in cash if r['patient_id']=='p01'and r['cash_stream']=='patient_payments')
        self.assertEqual(paid['comparison_status'],'differs_from_received_transactions')
        self.assertFalse(json.loads(paid['complete_cash_comparison'])['capture_complete'])

    def test_native_coverage_keeps_no_account_and_unassigned_source_cases(self):
        from test_financial_accounts import source
        raw=source('unassigned','patient_payments',day='2029-08-01',account_id=None,amount_cents=25,status='received')
        raw['patient_id']='p02'
        with sqlite3.connect(self.path/'stack.sqlite3')as db:
            db.execute('INSERT INTO records VALUES (?,?,?,?)',(raw['record_id'],'p02',raw['source_table'],json.dumps(raw)))
        t=self.tools();result=self.finance(t)
        coverage=t({'name':'query_sql','input':result['native_projections']['patient_coverage']['query_sql']})['rows']
        self.assertEqual(len(coverage),12)
        case=next(r for r in coverage if r['patient_id']=='p02')
        self.assertEqual(case['recorded_accounts'],0);self.assertEqual(case['unassigned_sources'],1)
        unresolved=t({'name':'query_sql','input':result['native_projections']['unassigned_sources']['query_sql']})['rows']
        self.assertEqual(len(unresolved),1)
        self.assertEqual(json.loads(unresolved[0]['unresolved_source'])['source']['record_id'],'unassigned')
        self.assertNotIn('p02',{r['patient_id']for r in t({'name':'query_sql','input':result['native_projections']['identities']['query_sql']})['rows']})
        for bad in ['agere_result_0','record','agere_result_1; DROP TABLE patient']:
            with self.assertRaises(ValueError):A.financial_projections(bad)


if __name__=='__main__':unittest.main()
