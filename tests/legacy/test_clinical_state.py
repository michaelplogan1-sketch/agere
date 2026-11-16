"""Behavior of source-bound state and clinical evidence prerequisite contracts."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere.clinical import clinical_state as c
from clinical_core.agere.clinical.state_contracts import definition,evaluate

ASOF='2026-09-30'

def obs(value,day='2026-09-01',**kw):
    return dict(value=value,source_date=day,clinical_date=None,arrival_date='2026-09-02',
                assertion='affirmed',subject='patient',temporality='unspecified',source=[],**kw)


def record(pid='p',rid='r',table='financial',**kw):
    return dict(patient_id=pid,record_id=rid,source_table=table,synthetic=True,identity_status='matched',
                event_date='2026-09-01',available_date='2026-09-02',**kw)

class StateSemantics(unittest.TestCase):
    def test_negative_is_known_and_absence_is_unknown(self):
        self.assertIs(c.resolve([obs(False)],ASOF)['value'],False)
        self.assertEqual(c.resolve([obs(False)],ASOF)['status'],'documented')
        self.assertEqual(c.resolve([],ASOF)['status'],'unobserved')
        spec={'requirements':[{'binding':'x','label':'test','accept':'known'}]}
        self.assertEqual(evaluate(spec,{'x':c.resolve([obs(False)],ASOF)})['status'],'requirements_documented')
        spec['requirements'][0]['accept']='true'
        self.assertEqual(evaluate(spec,{'x':c.resolve([obs(False)],ASOF)})['status'],'documented_requirement_not_satisfied')

    def test_conflict_not_outvoted_by_copies(self):
        a=obs(True);b=obs(False)
        self.assertEqual(c.resolve([a,a,a,b],ASOF)['status'],'conflicting')

    def test_missing_clinical_date_never_uses_source_clock(self):
        a=obs(True)
        self.assertEqual(c.resolve([a],ASOF,dated=True)['status'],'undated')
        dated=obs(False);dated['clinical_date']='2026-08-01'
        self.assertEqual(c.resolve([dated,a],ASOF,dated=True)['status'],'undated')

    def test_wrong_subject_historical_future_arrival_excluded(self):
        for key,value in [('subject','family'),('temporality','historical'),('arrival_date','2026-10-01'),('assertion','uncertain')]:
            a=obs(True);a[key]=value
            self.assertEqual(c.resolve([a],ASOF)['status'],'historical_or_excluded')

    def test_late_arrival_does_not_replace_later_observation(self):
        earlier=obs(False,'2026-01-01');earlier['arrival_date']='2026-09-29'
        self.assertIs(c.resolve([earlier,obs(True)],ASOF)['value'],True)

    def test_dose_notation_does_not_infer_tolerance_or_use(self):
        dose=c.normalize_dose('12.5 mg twice daily')
        self.assertEqual(dose['nominal_daily_amount'],25)
        self.assertFalse(dose['actual_use_established']);self.assertFalse(dose['dose_equivalence_or_target_assessed'])
        for raw in ['24/26 mg twice daily','10 mg PRN','100 units daily','0 mg daily','unknown']:
            self.assertEqual(c.normalize_dose(raw)['status'],'unresolved')

    def test_not_observed_taking_is_not_not_taking(self):
        r=record(table='medication_history',drug_class='bb',status='dispensed',drug='metoprolol',
                 dose='50 mg daily',dispense_date='2026-09-01',days_supply=90,medication_taking_observed=False)
        out=c.patient_state({'patient_id':'p'},[r],[],ASOF)
        self.assertEqual(out['bindings']['therapy.bb.use']['status'],'unknown_semantics')
        self.assertIsNone(out['bindings']['therapy.bb.use']['value'])
        self.assertTrue(out['therapy'][0]['taking_observation_flags'])
        self.assertEqual(out['therapy'][0]['supply']['status'],'supply_documented')

    def test_source_quote_and_patient_validation(self):
        r=record(table='notes',text='EF 35%');f={'fact_id':'f','patient_id':'p','concept':'ef','value':{'lower':35,'upper':35},
            'assertion':'affirmed','subject':'patient','temporality':'current','method':'context_rules',
            'evidence':[{'record_id':'r','field':'text','quote':'EF 35%','start':0,'end':6}]}
        a={'operator':'heart_failure_review','source_bound_facts':[f]}
        good=c.bound_facts([a],[r],'p')['ef'][0]
        self.assertEqual(good['source'][0]['record_hash'],c.digest(r));self.assertIsNone(good['clinical_date'])
        for field,value in [('patient_id','other'),('evidence',[{'record_id':'r','field':'text','quote':'EF 65%','start':0,'end':6}])]:
            bad=copy.deepcopy(a);bad['source_bound_facts'][0][field]=value
            with self.assertRaises(ValueError):c.bound_facts([bad],[r],'p')

    def test_copy_uses_original_source_date(self):
        r=record(table='notes',text='EF 35%');new={**r,'record_id':'r2','event_date':'2026-09-25','available_date':'2026-09-26'}
        f={'fact_id':'f','patient_id':'p','concept':'ef','value':35,'assertion':'affirmed','subject':'patient','temporality':'current','method':'context_rules',
            'evidence':[{'record_id':rid,'field':'text','quote':'EF 35%','start':0,'end':6} for rid in ['r2','r']]}
        a={'source_bound_facts':[f]}
        out=c.bound_facts([a,a],[r,new],'p')['ef']
        self.assertEqual(len(out),1);self.assertEqual(out[0]['source_date'],'2026-09-01')

    def test_unknown_contract_cannot_imply_eligibility(self):
        with self.assertRaises(ValueError):definition('arbitrary')
        out=evaluate(definition('affordability'),{})
        self.assertEqual(out['status'],'requirements_unresolved');self.assertFalse(out['clinical_eligibility_established'])
        self.assertEqual(len(out['unresolved_bindings']),4)

    def test_dated_renal_value_and_referral_order_not_attendance(self):
        rows=[record(table='labs',test='egfr',value=45,unit='ml/min/1.73m2',status='final',collection_date='2026-09-01'),
              record(rid='r2',table='referrals',service='nephrology',status='completed')]
        out=c.patient_state({'patient_id':'p'},rows,[],ASOF)
        self.assertEqual(out['bindings']['renal.egfr']['status'],'documented')
        self.assertEqual(out['bindings']['nephrology_attendance']['status'],'unobserved')

class Artifact(unittest.TestCase):
    def test_build_attach_subset_unknown_empty_and_stale_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'state'
            (root/'run.json').write_text(json.dumps({'synthetic':True,'source_run':'source','source_digest':'source-digest','snapshot':{'as_of':ASOF}}))
            db=sqlite3.connect(root/'stack.sqlite3');db.executescript('CREATE TABLE patients(id TEXT,payload TEXT); CREATE TABLE records(id TEXT,patient TEXT,payload TEXT); CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT);')
            for pid in ('p','q'):
                db.execute('INSERT INTO patients VALUES (?,?)',(pid,json.dumps({'patient_id':pid})))
                r=record(pid=pid,rid=pid,difficulty_paying_medical_bills=False)
                db.execute('INSERT INTO records VALUES (?,?,?)',(pid,pid,json.dumps(r)))
            db.commit();db.close()
            receipt=c.build(root,out);self.assertEqual(receipt['patients'],2)
            (root/'clinical-state-link.json').write_text(json.dumps({'path':str(out)}))
            result=c.review(root,['p'],'affordability')
            self.assertEqual(result['patients_evaluated'],1)
            self.assertEqual(result['requirement_counts']['financial_report'],{'documented':1})
            self.assertEqual(c.review(root,[])['patients_evaluated'],0)
            with self.assertRaises(ValueError):c.review(root,['missing'])
            with self.assertRaises(ValueError):c.build(root,out)
            db=sqlite3.connect(root/'stack.sqlite3');db.execute('INSERT INTO analyses VALUES (?,?,?)',('p','heart_failure_review','{}'));db.commit();db.close()
            with self.assertRaisesRegex(ValueError,'stale'):c.review(root,['p'])

if __name__=='__main__':unittest.main()
