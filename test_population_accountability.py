"""Question/cohort/work boundary checks with real accounting and outcome engines."""
import copy,json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import question_decision as q, outcomes as O, conditional_payment as P
import test_population_decision as D
import test_financial_accounts as F
import test_longitudinal as L

class PopulationAccountability(unittest.TestCase):
    prepare=D.PopulationDecision.prepare
    patch=D.PopulationDecision.patch
    stub_member_wrappers=D.PopulationDecision.stub_member_wrappers
    def setUp(self):
        D.PopulationDecision.setUp(self)
        for r in [F.snapshot(),F.source('pay','patient_payments',amount_cents=300,status='received'),F.source('unbound','medical_claims',account_id=None)]:
            self.f.save_record({**r,'patient_id':'p0'})
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            for key,raw in list(db.execute('SELECT id,payload FROM records')):
                r=json.loads(raw);r.update(synthetic=True,identity_status='matched');db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),key))
        self.events=[L.event(patient='p0'),L.event(patient='p0',rid='discharge',event='A03',day='2026-08-20')]
        self.snapshot=json.loads((self.path/'run.json').read_text())['snapshot']
        def connect(path):
            db,meta=L.connection(self.events);db.execute('CREATE TABLE patients(id TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO patients VALUES (?)',[(f'p{i}',) for i in range(6)])
            meta.update(snapshot=self.snapshot,source_digest='fixture',code_identity='fixture')
            return db,meta
        self.patch(O.ledger,'connect',side_effect=connect)
    def request(self,d):
        return {'decision_id':d['id'],'accountability_contract':'program_payment_evidence','scenario_id':'','outcome':{'contract':'admission_discharge_source_30d','index_from':'2026-01-01','index_through':self.req['as_of'],'available_by':self.req['as_of'],'filter':'all'}}
    def open(self,r,pid='p0'):
        return q.open_population_accountability(self.retriever,{'decision_id':r['decision_id'],'result_id':r['result_id'],'patient_id':pid})
    def work(self,d):
        self.stub_member_wrappers();m=q.match_population_member(self.retriever,{'decision_id':d['id'],'patient_id':'p0'})
        return q.queue_population_member(self.retriever,{'decision_id':d['id'],'match_id':m['result_id'],'option_id':m['matching']['options'][0]['id'],'actor':'fixture'})['task']
    def test_full_population_replay_finance_unknowns_and_work_context(self):
        d=self.prepare();t=self.work(d);r=q.population_accountability(self.retriever,self.request(d))
        self.assertEqual(r,q.population_accountability(self.retriever,self.request(d)))
        self.assertEqual(len(r['members']),6);self.assertEqual(r['financial']['selected_snapshot_totals_by_currency']['USD']['balance_cents'],700)
        self.assertEqual(r['financial']['unassigned_source_count'],1)
        self.assertEqual(r['work'][0]['task']['id'],t['id']);self.assertEqual(r['outcomes']['status_counts'],{'observed':1,'no_observed_index':5})
        self.assertIsNone(r['causal_savings_cents']);self.assertIsNone(r['attributable_program_payment_cents'])
        self.assertEqual(r['all_accounts_unlinked_to_program_work'],1)
        opened=self.open(r);self.assertTrue(opened['current']);self.assertFalse(opened['member_evidence']['outcome']['clinical_outcome_established'])
        self.assertTrue(all(x['status']=='unbound' for x in opened['member_evidence']['accountability']['requirements']))
    def test_rejects_changed_cutoff_filtered_denominator_and_cross_decision(self):
        d=self.prepare();req=self.request(d)
        for change in ({'available_by':'2026-09-01'},{'filter':'observed'},{'index_from':'2027-01-01'}):
            with self.assertRaises(ValueError):q.population_accountability(self.retriever,{**req,'outcome':{**req['outcome'],**change}})
        r=q.population_accountability(self.retriever,req)
        for change in ({'decision_id':'wrong'},{'patient_id':'outside'}):
            with self.assertRaises(ValueError):q.open_population_accountability(self.retriever,{'decision_id':d['id'],'result_id':r['result_id'],'patient_id':'',**change})
    def test_source_and_outcome_changes_make_saved_result_historical(self):
        d=self.prepare();r=q.population_accountability(self.retriever,self.request(d));self.events.append(L.event(patient='p0',rid='new',event='A03',day='2026-08-21'))
        self.assertFalse(self.open(r)['current']);self.assertIsNone(self.open(r)['member_evidence'])
        self.events.pop();self.assertTrue(self.open(r)['current'])
        self.f.save_record({**F.snapshot(balance_cents=900),'patient_id':'p0'})
        self.assertFalse(self.open(r)['current'])
    def test_changed_work_retains_historical_binding_and_new_reconciliation(self):
        d=self.prepare();t=self.work(d);r=q.population_accountability(self.retriever,self.request(d))
        q.event(self.retriever,{'decision_id':d['id'],'task_id':t['id'],'expected_revision':t['revision'],'request_id':'handoff','action':'prepare_handoff','actor':'fixture','note':'Internal only'})
        self.assertFalse(self.open(r)['current']);new=q.population_accountability(self.retriever,self.request(d))
        self.assertNotEqual(r['result_id'],new['result_id']);self.assertEqual(new['members'],r['members']);self.assertTrue(self.open(new)['current'])
        self.assertEqual(q.saved_followup(self.path,r['result_id']),r)
    def test_mid_assembly_change_does_not_save_mixed_receipt(self):
        d=self.prepare();original=q.population_accountability_inputs;calls=[]
        def altered(*a):
            result=original(*a);calls.append(1)
            if len(calls)>1:result[-1]['work_digest']='changed'
            return result
        with patch.object(q,'population_accountability_inputs',side_effect=altered),self.assertRaisesRegex(ValueError,'changed during'):
            q.population_accountability(self.retriever,self.request(d))
        self.assertFalse(any(f['schema']==q.POPULATION_ACCOUNTABILITY for f in q.reopen(self.retriever,d['id'])['followups']))
    def test_scenario_scope_is_exact_and_observed_amounts_never_netted(self):
        d=self.prepare();t=self.work(d);req={**self.request(d),'scenario_id':'scenario'}
        s={'id':'scenario','current':True,'stale_reason':None,'buyer_id':d['buyer_id'],'selected_task_ids':[t['id']],'patient_ids':['p0'],'terms_authority':'supplied_hypothetical_terms','comparisons':{'synthetic_fixture_only':{}}}
        with patch.object(P,'inspect',return_value=s):
            r=q.population_accountability(self.retriever,req);self.assertEqual(r['scenario_status'],'supplied_hypothetical_terms_only');self.assertFalse(r['observed_amounts_netted'])
        for change in ({'buyer_id':'other'},{'selected_task_ids':['other-question-task']},{'patient_ids':['p1']},{'current':False,'stale_reason':'changed'}):
            with patch.object(P,'inspect',return_value={**s,**change}),self.assertRaises(ValueError):q.population_accountability(self.retriever,req)
    def test_empty_cohort_stays_empty_and_unknowns_retained(self):
        self.req['cohort']={'all':[{'kind':'patient','field':'patient_id','op':'eq','value':'not-observed'}],'any':[],'exclude':[]}
        d=self.prepare();r=q.population_accountability(self.retriever,self.request(d))
        self.assertEqual(len(r['members']),0);self.assertEqual(r['financial']['accounts_evaluated'],0);self.assertEqual(r['selection'],d['selection'])
    def test_receipt_tampering_detected_on_member_open(self):
        d=self.prepare();r=q.population_accountability(self.retriever,self.request(d));member=r['members'][0]
        import zlib
        with O.storage(self.path) as db:
            raw=json.loads(zlib.decompress(db.execute('SELECT payload FROM receipts WHERE id=?',(member['outcome_receipt_id'],)).fetchone()[0]));raw['recorded_label']=999
            db.execute('UPDATE receipts SET payload=? WHERE id=?',(zlib.compress(json.dumps(raw).encode()),member['outcome_receipt_id']))
        self.assertFalse(self.open(r)['current'])
        self.assertFalse(self.open(r,'')['current'])

if __name__=='__main__':unittest.main()
