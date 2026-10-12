"""Focused source-scope and unknown-time checks for new execution behavior."""
import json,sqlite3,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from clinical_core.agere.clinical.cohorts import evaluate_patient,validate,empty,select
from clinical_core.agere.clinical.clinical_events import bind_event,window_state,build,validate_window
from clinical_core.agere.clinical.analytic_registry import compute_all,execute
from clinical_core.agere.clinical.stack_planner import validate as validate_plan
from clinical_core.agere.clinical.medication_reconciliation import reconcile

ASOF='2026-09-30'
def window(basis='all_observed',start='',end=''):return dict(basis=basis,start=start,end=end)
def record(i,patient='p',stream='labs',**kw):
    return dict(record_id=i,patient_id=patient,source_table=stream,event_date='2026-09-01',
                available_date='2026-09-02',status='final',test='egfr',code='egfr',value=50,
                unit='mL/min/1.73m2',organization_id='a',**kw)
def atom(stream='labs',conditions=None,w=None):
    return {'kind':'record','stream':stream,'conditions':conditions or [],'window':w or window()}
def condition(field,op,value):return dict(field=field,op=op,value=value)

class CohortContracts(unittest.TestCase):
    def test_same_record_join_required(self):
        spec={**empty(),'all':[atom(conditions=[condition('organization_id','eq','a'),condition('test','eq','egfr')])]}
        rs=[{**record('a'),'test':'potassium'},{**record('b'),'organization_id':'b'}]
        self.assertIs(evaluate_patient(spec,{'patient_id':'p'},rs,ASOF)[0],False)
    def test_unknown_phys_date_not_replaced_by_recent_note_date(self):
        spec={**empty(),'all':[atom(w=window('clinical','2026-08-01',ASOF))]}
        state,traces=evaluate_patient(spec,{'patient_id':'p'},[record('old')],ASOF)
        self.assertIsNone(state);self.assertEqual(traces[0]['unknown_record_count'],1)
    def test_arrival_and_clinical_dates_are_distinct(self):
        r=record('old',collection_date='2025-01-01')
        self.assertFalse(window_state(r,window('clinical','2026-01-01',ASOF),ASOF))
        self.assertTrue(window_state(r,window('arrival','2026-01-01',ASOF),ASOF))
    def test_conflicting_explicit_dates_remain_unknown(self):
        e=bind_event(record('x',collection_date='2026-01-01',specimen_date='2026-01-02'),ASOF)
        self.assertEqual(e['clinical_date_status'],'conflicting_explicit_dates');self.assertIsNone(e['clinical_date'])
    def test_only_redundant_snapshot_cutoff_can_normalize(self):
        self.assertEqual(validate_window(window('all_observed','',ASOF),ASOF),window())
        for w in (window('all_observed','','2026-06-30'),window('all_observed','2026-01-01',ASOF)):
            with self.assertRaises(ValueError):validate_window(w,ASOF)
    def test_age_uses_dob_not_stale_roster_age(self):
        p={'patient_id':'p','birth_date':'1961-10-01','age_at_cutoff':65}
        spec={**empty(),'all':[dict(kind='patient',field='age',op='ge',value=65)]}
        self.assertFalse(evaluate_patient(spec,p,[],ASOF)[0])
    def test_unknown_exclusion_does_not_qualify_patient(self):
        spec={**empty(),'exclude':[atom(w=window('clinical','2026-01-01',ASOF))]}
        self.assertIsNone(evaluate_patient(spec,{'patient_id':'p'},[record('x')],ASOF)[0])
    def test_absence_is_labeled_observed_nonmatch(self):
        state,traces=evaluate_patient({**empty(),'all':[atom()]},{'patient_id':'p'},[],ASOF)
        self.assertFalse(state);self.assertIn('not absence',traces[0]['nonmatch_meaning'])
    def test_truth_hidden_text_and_future_constraints_rejected(self):
        for a in [atom(conditions=[condition('original_text','eq','truth')]),atom(w=window('source_event','2027-01-01',''))]:
            with self.assertRaises(ValueError):validate({**empty(),'all':[a]},ASOF)
    def test_record_membership_not_substring(self):
        a=atom('medical_claims',[condition('dx','contains','N18.3')])
        self.assertFalse(evaluate_patient({**empty(),'all':[a]},{'patient_id':'p'},[record('x',stream='medical_claims',dx=['N18.31'])],ASOF)[0])
    def test_full_snapshot_workflow_rejects_window(self):
        plan={'interpretation':'HF','steps':[{'operation':'heart_failure_review','query':'','patient_id':'','drug_class':'','view':'all','purpose':'review'}],
              'coverage':'partial','unmet_requirements':['time'],'cohort':empty(),'analysis_window':window('clinical','2026-01-01',ASOF)}
        with self.assertRaises(ValueError):validate_plan(plan,ASOF)
    def test_planner_does_not_duplicate_schema_or_exceed_request_bound(self):
        from clinical_core.agere.clinical.stack_planner import propose
        def capture(body,*args,**kwargs):
            self.assertLess(len(json.dumps(body).encode()),20000)
            prompt=json.loads(body['messages'][0]['content'])
            self.assertNotIn('schema',prompt['cohort_contract'])
            return None
        with patch('clinical_core.agere.clinical.stack_planner.send',side_effect=capture):
            propose('Describe the selected cohort.',Path('.'),None,snapshot={'as_of':ASOF},
                    observed_values={'roster':{'coverage':{'medicare':100}},'encounter_organizations':{},'documented_concepts':[]})

class ExecutionScope(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        (self.path/'run.json').write_text(json.dumps({'synthetic':True,'patients':2,'source_digest':'s','source_run':'r','snapshot':{'as_of':ASOF}}))
        db=sqlite3.connect(self.path/'stack.sqlite3')
        db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT); CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT,PRIMARY KEY(patient,operator)); CREATE TABLE result_cache(id TEXT PRIMARY KEY,payload TEXT);')
        with db:
            for pid,region in [('p','west'),('q','east')]:
                db.execute('INSERT INTO patients VALUES (?,?)',(pid,json.dumps({'patient_id':pid,'region':region})))
                r=record(pid,patient=pid);db.execute('INSERT INTO records VALUES (?,?,?,?)',(pid,pid,'labs',json.dumps(r)))
        db.close();build(self.path);compute_all(self.path)
    def tearDown(self):self.tmp.cleanup()
    def test_operator_filters_denominator_and_evidence(self):
        spec={**empty(),'all':[dict(kind='patient',field='region',op='eq',value='west')]}
        r=execute(self.path,'kidney_trajectory',cohort=spec)
        self.assertEqual(r['summary']['patients'],1);self.assertEqual([x['patient_id'] for x in r['patients']],['p'])
        self.assertEqual(r['cohort']['counts'],dict(evaluated=2,matched=1,not_matched=1,unknown=0))
    def test_empty_membership_never_falls_back_to_population(self):
        spec={**empty(),'all':[dict(kind='patient',field='region',op='eq',value='nowhere')]}
        r=execute(self.path,'clinical_timeline',cohort=spec)
        self.assertEqual(r['summary']['patients'],0);self.assertEqual(r['patients'],[])
    def test_unknown_date_cohort_and_cache_dimension(self):
        spec={**empty(),'all':[atom(w=window('clinical','2026-01-01',ASOF))]}
        r=select(self.path,spec);self.assertEqual(r['counts']['unknown'],2);self.assertFalse(r['patient_ids'])
        self.assertTrue(select(self.path,spec)['cache_hit']);self.assertEqual(select(self.path,empty())['counts']['matched'],2)
    def test_clinical_window_keeps_unknown_date_accounting(self):
        r=execute(self.path,'kidney_trajectory',analysis_window=window('clinical','2026-01-01',ASOF))
        self.assertEqual(r['date_scope']['records_with_unknown_date'],2)
        self.assertTrue(all(not x['measurements']['egfr']['series'] for x in r['patients']))
    def test_timeline_preserves_unknown_window_events(self):
        r=execute(self.path,'clinical_timeline',analysis_window=window('clinical','2026-01-01',ASOF))
        self.assertEqual(r['patients'][0]['summary']['unresolved_or_future_events'],1)
        self.assertEqual(len(r['patients'][0]['unknown_window_events']),1)
    def test_scoped_patient_results_reused_across_views_and_cohorts(self):
        w=window('clinical','2026-01-01',ASOF)
        first=execute(self.path,'kidney_trajectory',analysis_window=w)
        self.assertEqual(first['compute']['new_patient_computations'],2)
        spec={**empty(),'all':[dict(kind='patient',field='region',op='eq',value='west')]}
        next_result=execute(self.path,'kidney_trajectory',analysis_window=w,cohort=spec,view='missing_inputs')
        self.assertEqual(next_result['compute']['patient_cache_hits'],1)
        self.assertEqual(next_result['compute']['new_patient_computations'],0)

class MedicationLifecycle(unittest.TestCase):
    def dispense(self,i,stream='pharmacy_claims',**changes):
        r=record(i,stream=stream,recorded_date='2026-09-02',drug_class='bb',drug='metoprolol',dose='25 mg daily',
                 dispense_id='d',fill_date='2026-09-01',days_supply=30,transaction_type='paid')
        return {**r,'status':'dispensed',**changes}
    def test_supply_uses_dispense_date_not_recent_document_date(self):
        r=reconcile([self.dispense('a',fill_date='2025-01-01')],'bb',ASOF)
        self.assertFalse(r['supplies']);self.assertEqual(len(r['historical_supplies']),1)
    def test_late_arrival_does_not_reorder_paid_reversal(self):
        paid=self.dispense('a',available_date='2026-09-29')
        reversal=self.dispense('b',transaction_type='reversed',recorded_date='2026-09-10')
        r=reconcile([paid,reversal],'bb',ASOF)
        self.assertFalse(r['supplies']);self.assertEqual(r['dispense_lifecycle'][0]['status'],'reversed')
    def test_history_mirror_cannot_override_reversal(self):
        a=self.dispense('a',transaction_type='reversed',recorded_date='2026-09-10')
        h=self.dispense('h',stream='medication_history',dispense_date='2026-09-01')
        r=reconcile([a,h],'bb',ASOF)
        self.assertFalse(r['supplies']);self.assertEqual(r['status'],'supply_unresolved')
        self.assertEqual(r['source_disagreements'][0]['reason'],'history_and_claim_adjudication_disagree')
    def test_same_day_contradiction_and_missing_fill_date_visible(self):
        a=self.dispense('a');b=self.dispense('b',transaction_type='reversed')
        r=reconcile([a,b],'bb',ASOF)
        self.assertFalse(r['supplies']);self.assertEqual(r['dispense_lifecycle'][0]['transaction']['state'],'conflicting_latest_transactions')
        r=reconcile([self.dispense('c',fill_date=None)],'bb',ASOF)
        self.assertEqual(r['status'],'supply_unresolved');self.assertTrue(r['excluded_dispense_observations'])
    def test_rejection_is_known_transaction_not_an_invented_conflict(self):
        r=reconcile([self.dispense('x',transaction_type='rejected',status='rejected',dispense_id=None)],'bb',ASOF)
        self.assertEqual(r['dispense_lifecycle'][0]['status'],'rejected')
        self.assertFalse(r['source_disagreements']);self.assertFalse(r['missing_care_established'])

if __name__=='__main__':unittest.main()
