"""Financial stocks/flows, clock ambiguity, provenance and population integration."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import financial_accounts as F
from clinical_core.agere.clinical.graph_workbench import financial_examples
from clinical_core.agere.clinical.economical_planner import local_plan
from clinical_core.agere.clinical.analysis_graph import execute,validate
import test_analysis_graph as G


def source(rid,stream='patient_billing_accounts',day='2026-08-01',**fields):
    return {'record_id':rid,'patient_id':'p','source_table':stream,'organization_id':'hospital','account_id':'a',
        'event_date':day,'event_time':day+'T12:00:00+00:00','available_date':day,'currency':'USD',
        'synthetic':True,'identity_status':'matched','_warehouse_source':{'adapter':F.mapping()['adapter']},**fields}


def snapshot(rid='s',day='2026-08-02',**values):
    amounts={f:0 for f in F.mapping()['snapshot_amounts']}
    return source(rid,day=day,**{**amounts,'billed_cents':1000,'patient_responsibility_cents':1000,'received_cents':300,'balance_cents':700,**values})


def account(rows,cutoff='2026-09-30'):return F.project('p',rows,cutoff)['accounts'][0]


class Financial(unittest.TestCase):
    def test_latest_stock_not_sum_and_claim_decision_not_cash(self):
        rows=[snapshot('old','2026-08-01',received_cents=0,balance_cents=1000),snapshot(),
            source('paid','patient_payments',amount_cents=300,status='received'),
            source('claim','medical_claims',payer_paid_cents=20000)]
        a=account(rows);self.assertEqual(a['snapshot_count'],2);self.assertEqual(a['snapshot_amounts']['balance_cents'],700)
        self.assertTrue(all(e['status']=='balanced' for e in a['equations']))
        self.assertEqual(a['cash_comparisons'][0]['received_transactions_cents'],300)
        self.assertEqual(a['cash_comparisons'][2]['received_transactions_cents'],0)
        self.assertEqual(a['status'],'reconciled_received_records')

    def test_refund_and_credit_identity(self):
        a=account([snapshot(received_cents=1500,refunded_cents=200,balance_cents=0,patient_credit_cents=300),
            source('p','patient_payments',amount_cents=1500,status='received'),source('r','patient_refunds',amount_cents=200,status='issued')])
        self.assertEqual(a['status'],'reconciled_received_records')

    def test_late_cash_not_available_and_later_source_not_in_snapshot(self):
        a=account([snapshot(),source('late','patient_payments',amount_cents=300,status='received',available_date='2026-09-01'),
            source('future','patient_payments',day='2026-08-03',amount_cents=200,status='received')],cutoff='2026-08-10')
        c=a['cash_comparisons'][0];self.assertEqual(c['difference_cents'],300)
        self.assertEqual(c['excluded'][0]['reason'],'after_selected_snapshot')

    def test_conflicting_same_time_and_undated_snapshot_abstain(self):
        for other in [snapshot('conflict',received_cents=200,balance_cents=800),snapshot('nodate',event_date=None)]:
            a=account([snapshot(),other]);self.assertEqual(a['status'],'unresolved_snapshot');self.assertIsNone(a['snapshot_amounts'])
            if other.get('event_date') is None:self.assertEqual(a['issues'][0]['sources'][0]['record_id'],'nodate')

    def test_equal_copies_do_not_double_snapshots(self):
        a=account([snapshot(),snapshot('copy')]);self.assertEqual(len(a['selected_snapshots']),2)
        self.assertEqual(a['snapshot_amounts']['billed_cents'],1000)

    def test_currency_mismatch_and_noninteger_amount_unresolved(self):
        for kw in [{'currency':'EUR'},{'amount_cents':300.0},{'amount_cents':True},{'amount_cents':-300}]:
            tx=source('x','patient_payments',amount_cents=300,status='received');tx.update(kw)
            a=account([snapshot(),tx]);self.assertEqual(a['cash_comparisons'][0]['status'],'unresolved')

    def test_payer_currency_inherits_exact_account_and_reversals_stay_signed(self):
        rows=[snapshot(payer_received_cents=150),source('x','payer_cash_allocations',allocated_cents=200),source('rev','payer_cash_allocations',allocated_cents=-50)]
        for r in rows[1:]:r.pop('currency')
        c=account(rows)['cash_comparisons'][2];self.assertEqual(c['received_transactions_cents'],150)
        self.assertEqual(c['included'][0]['currency_basis'],'inherited_from_explicit_account_binding')

    def test_same_day_without_time_and_future_snapshots_do_not_invent_order(self):
        tx=source('x','patient_payments',day='2026-08-02',amount_cents=300,status='received',event_time=None)
        self.assertEqual(account([snapshot(),tx])['cash_comparisons'][0]['status'],'unresolved')
        a=account([snapshot(),snapshot('future','2026-10-01',available_date='2026-08-02')]);self.assertEqual(a['status'],'unresolved_snapshot')

    def test_parent_link_account_inheritance_retains_evidence_and_cutoff(self):
        parent=source('sub','medical_claim_submissions');line=source('line','medical_claim_lines',account_id=None,submission_record_id='sub')
        a=account([snapshot(),parent,line]);self.assertEqual(a['account_binding_evidence'][0]['target']['record_id'],'sub')
        parent['available_date']='2026-10-01';r=F.project('p',[snapshot(),parent,line],'2026-09-30')
        self.assertEqual(r['unassigned_sources'][0]['source']['record_id'],'line')

    def test_cross_account_link_and_cross_patient_record_not_accepted(self):
        parent=source('sub','medical_claim_submissions',account_id='b');line=source('line','medical_claims',submission_record_id='sub')
        a=account([snapshot(),parent,line]);self.assertIn('linked_record_different_account',[i['reason'] for i in a['issues']])
        parent['patient_id']='q'
        with self.assertRaisesRegex(ValueError,'same-patient'):account([snapshot(),parent])

    def test_inactive_payment_report_is_not_zero_as_actual_payment(self):
        a=account([snapshot(),source('x','patient_payments',amount_cents=300,status='pending')]);c=a['cash_comparisons'][0]
        self.assertEqual(c['received_transactions_cents'],0);self.assertEqual(c['status'],'differs_from_received_transactions')
        self.assertFalse(c['capture_complete'])


class Persistence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.p={'available_by':'2026-09-30'}
        (self.root/'run.json').write_text(json.dumps({'snapshot':{'as_of':'2026-09-30'},'synthetic':True,'source_digest':'fixture'}))
        db=sqlite3.connect(self.root/'stack.sqlite3');db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT); CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT);')
        for pid in ['p','q']:db.execute('INSERT INTO patients VALUES (?,?)',(pid,json.dumps({'patient_id':pid})))
        for r in [snapshot(),source('pay','patient_payments',amount_cents=300,status='received')]:db.execute('INSERT INTO records VALUES (?,?,?,?)',(r['record_id'],r['patient_id'],r['source_table'],json.dumps(r)))
        db.commit();db.close()
    def tearDown(self):self.tmp.cleanup()

    def test_aggregate_counts_once_and_changed_patient_only(self):
        prep=F.prepare(self.root,self.p);r=F.execute(self.root,['p','q'],self.p,prepared=prep)
        self.assertEqual(r['patients_evaluated'],2);self.assertEqual(r['accounts_evaluated'],1)
        self.assertEqual(r['selected_snapshot_totals_by_currency']['USD']['billed_cents'],1000)
        self.assertEqual(F.execute(self.root,['p','q'],self.p,prepared=prep)['processing_counts']['patient_cache_hits'],2)
        key=F.receipt_id(prep,'p');db=sqlite3.connect(self.root/'stack.sqlite3')
        db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(snapshot(balance_cents=1)),'s'));db.commit();db.close()
        fresh=F.prepare(self.root,self.p);self.assertNotEqual(key,F.receipt_id(fresh,'p'));self.assertEqual(F.receipt_id(prep,'q'),F.receipt_id(fresh,'q'))
        with self.assertRaisesRegex(ValueError,'changed'):F.inspect(self.root,key)


class Graph(unittest.TestCase):
    setUp=G.GraphExecution.setUp
    tearDown=G.GraphExecution.tearDown
    @patch('clinical_core.agere.clinical.graph_reports.attach', new=lambda path,op,patients,parameters,prep,result,selection: result)
    @patch('clinical_core.agere.clinical.graph_reports.dependencies', new=lambda path,graph: {})
    def test_question_compiles_and_full_membership_executes(self):
        for ex in financial_examples():
            plan,route=local_plan(ex['question'],'2026-09-30');self.assertEqual(route,'registered_example');validate(plan,'2026-09-30')
        g=financial_examples()[0]['graph']
        with patch.object(F,'prepare',return_value={'identity':'financial'}),patch.object(F,'execute',return_value={'patients_evaluated':6}) as run:
            result=execute(self.stack,g);self.assertEqual(len(run.call_args.args[1]),6)
            self.assertTrue(execute(self.stack,g)['cache_hit'])
            self.assertEqual(result['results'][-1]['result']['input_membership']['counts']['matched'],6)
        g['nodes'].append(G.node('work','worklist',['accounts'],{'program':'affordability'}))
        with self.assertRaisesRegex(ValueError,'membership'):validate(g,'2026-09-30')


if __name__=='__main__':unittest.main()
