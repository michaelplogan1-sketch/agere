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

    def test_typed_planner_receives_the_same_registered_financial_contract(self):
        from clinical_core.agere.clinical import shared_planner as S,request_plan as R
        question=('Reconcile recorded payments, refunds and remaining balances for accounts active during 2029. '
                  'Show conservation checks in integer cents, patient/account attribution, lifecycle dates and unresolved links.')
        view=S.schema_prompt_view(S.schema_slice(R.Catalog.load(self.path),question))
        actual=view['selected_operator_contracts']['financial_accounts']
        expected=A.describe('financial_accounts',path=self.path)
        self.assertEqual(actual['financial_source_contract'],expected['financial_source_contract'])
        self.assertEqual(actual['native_output_contract'],expected['native_output_contract'])
        self.assertEqual(actual['financial_source_contract']['identities'],F.mapping()['identities'])

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

    def test_account_reconciliation_preserves_deceased_and_duplicate_record_subjects(self):
        from clinical_core.agere.clinical import decision_context as D
        with sqlite3.connect(self.path/'semantic.sqlite3')as db:
            db.execute("INSERT INTO death(patient_id,death_date) VALUES ('p01','2029-09-01')")
            db.execute("UPDATE patient SET data_segment='duplicate_chart' WHERE patient_id='p00'")
        question=('Reconcile recorded payments, refunds and remaining balances for accounts active during 2029. '
                  'Show conservation checks in integer cents, patient/account attribution and unresolved links. '
                  'Do not treat account balance as proof of patient payment.')
        for suffix,expected in [('',set(self.ids)),(' For living patients only.',set(self.ids)-{'p01'})]:
            with self.subTest(suffix=suffix),D.scope(question+suffix,AS_OF):
                t=self.tools();t.question=question+suffix
                result=self.finance(t)
                self.assertEqual({r['patient_id']for r in result['rows']},expected)
                accounts=t({'name':'query_sql','input':result['native_projections']['accounts']['query_sql']})['rows']
                self.assertEqual({r['patient_id']for r in accounts},{'p00','p01'}&expected)

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


class FinancialAccountConsumer(unittest.TestCase):
    setUpBase=documentary_fixture.DocumentaryHandoff.setUp
    setUp=FinancialDocumentaryTools.setUp
    tools=documentary_fixture.DocumentaryHandoff.tools
    finance=FinancialDocumentaryTools.finance

    def add(self,raw):
        with sqlite3.connect(self.path/'stack.sqlite3')as db:
            db.execute('INSERT INTO records VALUES (?,?,?,?)',(raw['record_id'],raw['patient_id'],raw['source_table'],json.dumps(raw)))

    def graph(self,year=2029):
        from clinical_core.agere.clinical import request_plan as R,shared_planner as S,semantic_layer
        # Native source metadata, not a manually supplied expected account set.
        semantic_layer.build(self.path)
        q=f'Reconcile recorded accounts active during {year}. Show patient/account attribution and lifecycle dates.'
        spec={'source':{'table':'record','stream':'patient_billing_accounts','quote':'accounts'},
              'select':[{'kind':'count_distinct','field':'record.patient_id','alias':'patients','quote':'patient/account attribution'}],
              'where':{'op':'and','args':[{'op':'>=','field':'record.event_date','value':f'{year}-01-01','quote':f'active during {year}'},
                  {'op':'<','field':'record.event_date','value':f'{year+1}-01-01','quote':f'active during {year}'}],'quote':f'active during {year}'},
              'group_by':[],'order_by':[],'limit':None,'analyses':[{'operation':'financial_accounts','parameters':{'available_by':AS_OF},
                    'quote':q}],'unresolved':[]}
        # The documentary fixture deliberately keeps a small profile. The
        # source rows/typed record schema remain real for the compiled selector.
        catalog=R.Catalog.load(self.path)
        profile=json.loads((self.path/'source-profile.json').read_text())
        profile['source_digest']=catalog.identity['source_digest']
        profile['streams']['patient_billing_accounts']={'fields':{'account_id':{},'event_date':{}}}
        (self.path/'source-profile.json').write_text(json.dumps(profile))
        catalog=R.Catalog.load(self.path)
        return q,S.graph_for(R.compile_request(catalog,q,spec))

    def execute(self,q,g):
        from clinical_core.agere.clinical import analysis_graph as G,decision_context as D
        with D.scope(q,AS_OF):return G.execute(self.stack,g,question=q)

    def test_complete_account_scope_and_lifecycle_do_not_expand_to_all_patient_accounts(self):
        from test_financial_accounts import source
        raw=snapshot('old-stock','2028-08-01');raw.update(patient_id='p00',account_id='older-account')
        self.add(raw)
        # Equal account labels in different patients remain different keys.
        with sqlite3.connect(self.path/'stack.sqlite3')as db:
            row=json.loads(db.execute("SELECT payload FROM records WHERE id='stock-p01'").fetchone()[0])
            row['account_id']='account-p00';db.execute("UPDATE records SET payload=? WHERE id='stock-p01'",(json.dumps(row),))
        for rid,day,fields in [
            ('pay-before','2029-07-15',dict(amount_cents=60)),
            ('pay-after','2029-08-02',dict(amount_cents=40)),
            ('pay-eur','2029-07-16',dict(amount_cents=25,currency='EUR')),
            ('pay-bad','2029-07-17',dict(amount_cents='5')),
            ('pay-undated','2029-08-03',dict(amount_cents=50,event_date=None,event_time=None))]:
            tx=source(rid,'patient_payments',day=day,account_id='account-p00',status='received',**fields)
            tx['patient_id']='p00';self.add(tx)
        q,g=self.graph();self.finance(self.tools())  # prewarm source interpretation
        with patch.object(F,'project',side_effect=AssertionError('Do not recompute passing financial sources')):
            result=self.execute(q,g)
        answer=result['answer'];rows=answer['table'];scope=answer['overall']['financial_account_scope']
        self.assertEqual({(r['patient_id'],r['account_id'])for r in rows},{('p00','account-p00'),('p01','account-p00')})
        self.assertEqual(scope['input_accounts'],3);self.assertEqual(scope['excluded_input_accounts'],1)
        self.assertEqual(scope['selected_source_records'],2);self.assertFalse(answer['actionable_cohort'])
        row=next(r for r in rows if r['patient_id']=='p00')
        self.assertEqual(row['first_reported_source_date'],'2029-07-15')
        self.assertEqual(row['last_reported_source_date'],'2029-08-02')
        self.assertEqual(row['last_received_date'],'2029-08-03');self.assertEqual(row['unknown_source_date_records'],1)
        flow=row['received_transactions']['patient_payments']
        self.assertEqual(flow['by_currency'],{'USD':{'known_received_cents':150,'records':3},'EUR':{'known_received_cents':25,'records':1}})
        self.assertEqual(flow['unresolved_records'],[{'record_id':'pay-bad','reason':'noninteger_or_missing_amount'}])
        self.assertFalse(flow['capture_complete']);self.assertEqual(flow['unknown_event_date_records'],1)
        self.assertEqual(row['snapshot_received_cents'],100)
        self.assertTrue(all(e['residual_cents']==0 for e in row['equations']))
        self.assertEqual({r['patient_id']for r in scope['patient_coverage']},{'p00','p01'})
        self.assertEqual(answer['patient_ids'],['p00','p01'])
        from clinical_core.agere.deliverables import answer_context as C
        with patch.object(F,'project',side_effect=AssertionError('Saving or follow-up must not reprocess financial sources')):
            saved=C.capture(self.path,q,result)
            reopened=C.load(self.path,saved['id'],current=True)
            self.assertEqual(reopened['answer'],answer)
            self.assertEqual(set(reopened['patient_ids']),{'p00','p01'})
            self.assertFalse(C.supports_patient_work(reopened))
            follow=C.followup(self.path,saved['id'],'show me the patient list')
            self.assertEqual(follow['answer']['table'],rows)
            self.assertEqual(follow['new_paid_api_calls'],0)

    def test_explicit_empty_record_scope_is_not_replaced_by_patient_receipts(self):
        q,g=self.graph(2027)
        answer=self.execute(q,g)['answer']
        self.assertEqual(answer['table'],[]);self.assertEqual(answer['patient_ids'],[])
        self.assertEqual(answer['overall']['financial_account_scope']['presented_accounts'],0)

    def test_unknown_snapshot_and_unassigned_sources_stay_explicit(self):
        from test_financial_accounts import source
        with sqlite3.connect(self.path/'stack.sqlite3')as db:
            raw=json.loads(db.execute("SELECT payload FROM records WHERE id='stock-p00'").fetchone()[0])
            raw['event_time']='2029-08-02T01:00:00+00:00'
            db.execute("UPDATE records SET payload=? WHERE id='stock-p00'",(json.dumps(raw),))
        raw=source('unassigned','patient_payments','2029-08-01',account_id=None,amount_cents=25,status='received')
        raw['patient_id']='p00';self.add(raw)
        q,g=self.graph();a=self.execute(q,g)['answer']
        row=next(r for r in a['table']if r['patient_id']=='p00')
        self.assertIsNone(row['snapshot_balance_cents']);self.assertEqual(row['equations'],[])
        self.assertEqual(row['account_status'],'unresolved_snapshot')
        sources=a['overall']['financial_account_scope']['unassigned_sources']
        self.assertEqual([x['source']['record_id']for x in sources],['unassigned'])


if __name__=='__main__':unittest.main()
