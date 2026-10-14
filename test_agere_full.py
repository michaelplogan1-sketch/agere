import json,tempfile,unittest,hashlib
from pathlib import Path
import numpy as np
from clinical_core.agere.warehouse import Warehouse,text_of
from clinical_core.agere.policy import Program,expression,contract_score
from clinical_core.agere.operators import medication,labs,trend,admission_episodes,patient_analysis
from clinical_core.agere.mathematics import benjamini_hochberg,aipw,allocate,facility_shrinkage,route_search
from clinical_core.agere.gateway import BudgetLedger,PersistentGateway
from clinical_core.agere.training import feature_text,numeric_features
from clinical_core.providers import EvidenceTask


def record(**kwargs):
    return {'record_id':'a','patient_id':'p','source_table':'medication_history','source_system':'sys','organization_id':'o',
            'event_date':'2026-09-01','available_date':'2026-09-02','synthetic':True,'identity_status':'matched',
            'drug_class':'mra','status':'dispensed','days_supply':30,'dispense_id':'d',**kwargs}

class WarehouseTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.w=Warehouse(self.root/'db')
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def load(self,rows):
        observed=self.root/'observed';observed.mkdir(exist_ok=True)
        (observed/'patients.jsonl').write_text(json.dumps({'patient_id':'p','synthetic':True})+'\n')
        (observed/'notes.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        spec={'synthetic':True,'as_of':'2026-09-30','files':{}}
        for f in observed.iterdir():spec['files']['observed/'+f.name]={'sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
        (self.root/'manifest.json').write_text(json.dumps(spec))
        return self.w.ingest(self.root)[0]
    def test_late_correction_does_not_erase_historical_record(self):
        rid=self.load([record(),record(record_id='b',supersedes_record_id='a',available_date='2026-10-02')])
        patients,report=self.w.snapshot(rid,'2026-09-30');self.assertEqual([r['record_id'] for r in patients['p']['records']],['a'])
        patients,_=self.w.snapshot(rid,'2026-10-03');self.assertEqual([r['record_id'] for r in patients['p']['records']],['b'])
    def test_identity_and_future_quarantine(self):
        rid=self.load([record(identity_status='mismatched'),record(record_id='b',event_date='2026-10-01')])
        p,s=self.w.snapshot(rid,'2026-09-30');self.assertEqual(s['visible_records'],0)
    def test_multiple_corrections_supersede_parent_and_stay_current(self):
        # v2.8 decision: several children of one parent (e.g. a claim void plus its replacement) are all current; the
        # parent is superseded; downstream version logic interprets them. Only cycles / cross-patient links quarantine.
        rid=self.load([record(),record(record_id='b',supersedes_record_id='a'),record(record_id='c',supersedes_record_id='a')])
        p,s=self.w.snapshot(rid,'2026-09-30');self.assertEqual(sorted(r['record_id'] for r in p['p']['records']),['b','c'])
        self.assertEqual(s['excluded'],{'superseded':1})
    def test_hash_mutation_rejected(self):
        self.load([record()]);(self.root/'observed/notes.jsonl').write_text('{}')
        with self.assertRaises(ValueError):self.w.ingest(self.root)
    def test_original_document_not_used(self):
        self.assertEqual(text_of(record(source_table='documents',original_text='secret',extracted_text='received')),'received')

class ClinicalTest(unittest.TestCase):
    def test_paid_history_duplicate_one_supply(self):
        a=record();b=record(record_id='b',source_table='pharmacy_claims',transaction_type='paid')
        out=medication([a,b],'mra','2026-09-30');self.assertEqual(len(out['supplies']),1);self.assertFalse(out['ingestion_established'])
    def test_reversal_does_not_erase_independent_fill_history(self):
        rs=[record(),record(record_id='r',source_table='pharmacy_claims',transaction_type='reversed')]
        self.assertEqual(medication(rs,'mra','2026-09-30')['status'],'supply_documented')
    def test_order_does_not_establish_supply(self):
        out=medication([record(source_table='medication_orders')],'mra','2026-09-30')
        self.assertEqual(out['status'],'supply_not_observed');self.assertFalse(out['missing_care_established'])
    def test_supply_end_excludes_expired(self):
        self.assertEqual(medication([record(days_supply=29)],'mra','2026-09-30')['supplies'],[])
    def test_creatinine_unit_conversion_and_unknown_unit(self):
        rs=[record(source_table='labs',test='creatinine',unit='umol/L',value=88.4),record(record_id='b',source_table='labs',test='creatinine',unit='mystery',value=10)]
        series=labs(rs,'creatinine');self.assertEqual(len(series),1);self.assertEqual(series[0]['value'],1)
    def test_no_trend_from_same_day(self):
        self.assertEqual(trend([{'date':'2026-01-01','value':i,'unit':'x'} for i in range(5)])['status'],'insufficient_distinct_dates')
    def test_adt_censoring(self):
        rs=[record(source_table='adt',event='A01'),record(record_id='b',source_table='adt',event='A03',event_date='2026-09-02')]
        self.assertEqual(admission_episodes(rs,'2026-09-03')[0]['status'],'right_censored')
    def test_area_proxy_not_financial_fact(self):
        r=record(source_table='purchased_sdoh',financial_risk=10)
        out=patient_analysis({'patient_id':'p'},[r],'2026-09-30',[])
        self.assertIsNone(out['clinical']['facts']['difficulty_paying'])

class PolicyTest(unittest.TestCase):
    def test_three_valued_logic(self):
        self.assertIsNone(expression({'not':{'eq':['absent',True]}},{}))
        self.assertIs(expression({'all':[{'eq':['absent',True]},{'eq':['a',1]}]},{'a':2}),False)
        self.assertIs(expression({'any':[{'eq':['absent',True]},{'eq':['a',1]}]},{'a':1}),True)
    def test_policy_expiration(self):
        p=Program({'id':'p','version':'1','effective_start':'2025-01-01','effective_end':'2025-12-31','source':'synthetic','purpose':'test','predicate':{'eq':['x',True]}})
        self.assertEqual(p.evaluate({'x':True},'2026-01-01')['status'],'not_effective')
    def test_unsupported_operator_no_eval(self):
        with self.assertRaises(ValueError):expression({'exec':'print(1)'},{})
    def test_contract_arithmetic(self):
        r=contract_score({'measures':[{'id':'m','target':.8,'direction':'higher','value_per_gap_closed_usd':10}]},[{'site':'s','measures':{'m':{'numerator':50,'denominator':100}}}])
        self.assertEqual(r[0]['measures'][0]['opportunities'],30)

class MathTest(unittest.TestCase):
    def test_bh_known_values(self):np.testing.assert_allclose(benjamini_hochberg([.01,.04,.03]),[.03,.04,.04])
    def test_aipw_constant_effect(self):
        a=np.tile([0,1],50);y=a*2+1
        r=aipw(y,a,np.full(100,.5),np.ones(100),np.full(100,3))
        self.assertEqual(r['effect'],2)
    def test_overlap_not_silently_clipped(self):
        self.assertEqual(aipw([0,1,0,1],[0,1,0,1],[.001,.5,.5,.5],[0]*4,[1]*4)['status'],'overlap_failure')
    def test_allocation_capacity_and_budget(self):
        r=allocate([{'id':'a','cost':4,'expected_benefit':3},{'id':'b','cost':6,'expected_benefit':5}],6,1)
        self.assertEqual(r['selected'],['b']);self.assertLessEqual(r['cost'],6)
    def test_shrinkage_not_casemix(self):
        r=facility_shrinkage([{'eligible':1,'events':0}])[0];self.assertGreater(r['posterior_rate'],0);self.assertFalse(r['risk_adjusted'])
    def test_router_abstains_small_samples(self):
        r=route_search([.99],[1],[0],[1],.05);self.assertEqual(r['selected']['accepted'],0)

class TrainingTest(unittest.TestCase):
    def test_feature_boundary(self):
        r=record(source_table='notes',text='Cash fill',family_id='SECRET',scenario='SECRET',target='SECRET')
        text=feature_text({'observed_records':[r],'as_of':'2026-09-30','target':'SECRET'})
        self.assertNotIn('SECRET',text);self.assertNotIn('record_id',text)
    def test_future_evidence_rejected(self):
        with self.assertRaises(ValueError):feature_text({'observed_records':[record(available_date='2027-01-01')],'as_of':'2026-09-30'})

class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'ledger'
    def tearDown(self):self.tmp.cleanup()
    def test_persistent_cap_and_failed_reservation(self):
        a=BudgetLedger(self.path,cap_usd=.5);a.reserve('a','jev','m');a.finish('a',error='HTTP 529');a.close()
        b=BudgetLedger(self.path,cap_usd=.5)
        with self.assertRaises(ValueError):b.reserve('b','jev','m')
        self.assertEqual(b.receipt()['remaining_reservation_usd'],0);b.close()
    def test_validate_before_replay(self):
        a=BudgetLedger(self.path);a.reserve('x','jev','m');a.finish('x',response={'ok':True})
        self.assertEqual(a.reserve('x','jev','m'),{'ok':True});a.close()
    def test_offline_perplexity_contract(self):
        a=BudgetLedger(self.path);g=PersistentGateway(a)
        result=g.classify('perplexity',EvidenceTask('s','2026-09-30',{'unknown':'unknown'},[]))
        self.assertEqual(result['request']['model'],'pplx-decider-v1.1-27b');self.assertEqual(a.receipt()['current_reserved_usd'],0);a.close()

if __name__=='__main__':unittest.main()

class SourceWorkflowTest(unittest.TestCase):
    def test_source_fact_can_resolve_without_fake_human_review(self):
        from clinical_core.agere.workflow import analyze
        r=record(source_table='financial',difficulty_paying_medical_bills=True)
        a=patient_analysis({'patient_id':'p'},[r],'2026-09-30',[])
        result=analyze([r],a,'2026-09-30')
        self.assertEqual(result['outcome'],'financial_support_eligibility_review')
        self.assertFalse(result['row_review_claimed'])
    def test_missing_financial_screen_remains_unknown(self):
        from clinical_core.agere.workflow import analyze
        a=patient_analysis({'patient_id':'p'},[],'2026-09-30',[])
        result=analyze([],a,'2026-09-30')
        self.assertEqual(result['status'],'unresolved')
    def test_later_rebill_restores_pharmacy_evidence(self):
        rows=[record(source_table='pharmacy_claims',transaction_type='reversed'),record(record_id='b',source_table='pharmacy_claims',transaction_type='paid',available_date='2026-09-03')]
        self.assertEqual(medication(rows,'mra','2026-09-30')['status'],'supply_documented')
    def test_clause_quote_tamper_rejected(self):
        from clinical_core.agere.policy import compile_document
        spec={'id':'p','version':'1','effective_start':'2024-01-01','effective_end':'2027-01-01','source':'fixture','purpose':'test','predicate':{'eq':['age',65]}}
        with self.assertRaises(ValueError):compile_document('age 65',spec,{'age':{'start':0,'end':3,'quote':'bad'}})
