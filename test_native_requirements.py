"""Source-account composition, uncertainty, dependency changes and cache reuse."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere.clinical import native_requirements as nr, warehouse_interpretation as w
from clinical_core.agere.clinical import evidence_requirements as er, requirement_work as rw
from clinical_core.agere.clinical.analytic_registry import code_identity
from test_warehouse_interpretation import record,ASOF
from test_source_state import interpreted
from test_assertion_reconciliation import source


def predicate(**changes):
    return {'kind':'native_account','relation':'reported_use','where':[],
            'conditions':[{'field':'reported_intent','op':'eq','value':'not_using'}],'quantifier':'any',**changes}


def report(records,pid='P-1'):
    return w.interpret({'patient_id':pid},records,ASOF)


def use(rid,**kw):
    return record(rid,'medication_followup_reports',**{'medication_name':'drug A','pharmacy_prescription_id':'rx-A',
                  'reported_intent':'not_using',**kw})


class NativeProjectionTests(unittest.TestCase):
    def test_empty_is_unknown_for_both_quantifiers(self):
        for q in ('all','any'):
            self.assertIsNone(nr.project(report([]),predicate(quantifier=q))['predicate_result'])
            self.assertIsNone(nr.project(None,predicate(quantifier=q),patient_id='P-1')['predicate_result'])

    def test_newer_other_drug_does_not_erase_nonuse(self):
        rows=[use('a',event_date='2028-08-01'),use('b',medication_name='drug B',pharmacy_prescription_id='rx-B',reported_intent='using')]
        self.assertIs(nr.project(report(rows),predicate())['predicate_result'],True)
        self.assertIs(nr.project(report(rows),predicate(quantifier='all'))['predicate_result'],False)
        rows.append(use('c',event_date='2028-09-02',reported_intent='using'))
        self.assertIs(nr.project(report(rows),predicate())['predicate_result'],False)

    def test_scope_cannot_discard_same_account_conflicting_or_undated_report(self):
        p=predicate(where=[{'field':'medication_name','op':'eq','value':'drug A'}])
        for date in (None,'2028-09-01'):
            rows=[use('a'),use('b',medication_name='drug B',reported_intent='using',event_date=date)]
            result=nr.project(report(rows),p)
            self.assertIsNone(result['predicate_result']);self.assertEqual(len(result['selected_assertions']),2)
            self.assertEqual(len(result['pending_accounts']),1)

    def test_scope_excludes_known_other_entity_but_missing_field_stays_unknown(self):
        p=predicate(where=[{'field':'medication_name','op':'eq','value':'drug A'}])
        result=nr.project(report([use('b',medication_name='drug B')]),p)
        self.assertIsNone(result['predicate_result']);self.assertEqual(len(result['excluded_accounts']),1)
        rows=[use('a'),use('b',medication_name=None,pharmacy_prescription_id='rx-B')]
        result=nr.project(report(rows),p)
        self.assertIs(result['predicate_result'],True);self.assertEqual(result['pending_accounts'][0]['missing_fields'],['medication_name'])

    def test_missing_care_link_has_specific_dependency_not_false(self):
        p=predicate(relation='outside_service_documentation',conditions=[{'field':'performed_service_documented','op':'eq','value':True}])
        rows=[record('outside','outside_report_status',referral_record_id='ref',performed_service_documented=True)]
        result=nr.project(report(rows),p)
        self.assertIsNone(result['predicate_result'])
        self.assertEqual(result['pending_accounts'][0]['source_dependencies'][0]['record_id'],'ref')
        rows.append(record('ref','referrals'))
        self.assertIs(nr.project(report(rows),p)['predicate_result'],True)

    def test_typed_boolean_and_nested_document_field(self):
        p=predicate(relation='reported_affordability',conditions=[{'field':'difficulty_paying_medical_bills','op':'eq','value':True}])
        for value,expected in [(True,True),(False,False),(1,None),('true',None),(None,None)]:
            r=record('a','financial_screens',difficulty_paying_medical_bills=value)
            self.assertIs(nr.project(report([r]),p)['predicate_result'],expected)
        p=predicate(relation='transcribed_document',conditions=[{'field':'extracted_treatment_fields.cpr','op':'eq','value':'attempt'}])
        r=record('a','advance_care_documents',extracted_treatment_fields={'cpr':'attempt'})
        out=nr.project(report([r]),p);self.assertIs(out['predicate_result'],True)
        self.assertFalse(out['current_clinical_state_established'])

    def test_unmapped_or_executable_clauses_rejected(self):
        for p in [predicate(relation='diagnosis'),predicate(conditions=[]),predicate(where=[{'field':'medication_name','op':'sql','value':'x'}]),
                  predicate(conditions=[{'field':'unmapped','op':'eq','value':True}])]:
            with self.assertRaises(ValueError):nr.validate(p)

    def test_cross_patient_and_duplicate_evidence_rejected(self):
        r=report([use('a')]);r['statements'].append(copy.deepcopy(r['statements'][0]))
        with self.assertRaises(ValueError):nr.project(r,predicate())
        with self.assertRaises(ValueError):nr.project(report([use('a')]),predicate(),patient_id='P-2')

    def test_mixed_contract_composes_native_report_and_reviewed_source(self):
        spec=er.definition('affordability_report')
        spec['bindings']['nonuse']=er.definition('native_medication_nonuse')['bindings']['nonuse']
        spec['expression']={'all':['barrier','nonuse']}
        legacy=interpreted([source()]);pid=legacy['patient_id']
        native=report([use('a',patient_id=pid)],pid=pid)
        out=er.evaluate(legacy,spec,native_report=native)
        self.assertEqual(out['status'],'supported');self.assertFalse(out['clinical_eligibility_established'])
        native=report([use('a',patient_id=pid,event_date=None)],pid=pid)
        out=er.evaluate(legacy,spec,native_report=native)
        self.assertEqual(out['status'],'unresolved');self.assertTrue(out['acquisition'][0]['can_resolve_alone'])


class NativePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'run.json').write_text(json.dumps({'synthetic':True,'source_digest':'fixture','snapshot':{'as_of':ASOF}}))
        (self.root/'operators.json').write_text(json.dumps({'operators':['warehouse_interpretation'],'code_identity':code_identity()}))
        self.db=sqlite3.connect(self.root/'stack.sqlite3')
        self.db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT); CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT, PRIMARY KEY(patient,operator));')
        for pid in ('P-1','P-2'):self.db.execute('INSERT INTO patients VALUES (?,?)',(pid,'{}'))
        self.save('P-1',[use('a')]);self.save('P-2',[])
        self.p={'contract':'native_medication_nonuse','filter':'supported'}

    def tearDown(self):self.db.close();self.tmp.cleanup()

    def save(self,pid,rows):
        for r in rows:self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',(r['record_id'],pid,r['source_table'],json.dumps(r)))
        self.db.execute('INSERT OR REPLACE INTO analyses VALUES (?,?,?)',(pid,'warehouse_interpretation',json.dumps(report(rows,pid))))
        self.db.commit()

    def test_changed_account_only_recomputes_affected_patient_and_expires_context(self):
        yes,unknown,first=er.execute(self.root,['P-1','P-2'],self.p)
        self.assertEqual(yes,{'P-1'});self.assertEqual(unknown,{'P-2'})
        key=next(x['id'] for x in first['patients'] if x['patient_id']=='P-1')
        self.assertEqual(er.inspect(self.root,key)['status'],'supported')
        nodes=[{'id':'r','parameters':self.p}]
        contexts=rw.contexts(self.root,['P-1'],nodes,{'r':er.snapshot(self.root,self.p)},evaluated_patients={'r':{'P-1','P-2'}})
        rw.verify(self.root,list(contexts.values()))
        self.save('P-1',[use('a',reported_intent='using')])
        yes,unknown,second=er.execute(self.root,['P-1','P-2'],self.p)
        self.assertFalse(yes);self.assertEqual(unknown,{'P-2'})
        self.assertEqual(second['processing_counts'],{'patient_contracts_computed':1,'patient_cache_hits':1})
        with self.assertRaises(ValueError):er.inspect(self.root,key)
        with self.assertRaises(ValueError):rw.verify(self.root,list(contexts.values()))

    def test_all_and_empty_preserve_membership_and_no_interpretation_call(self):
        yes,unknown,result=er.execute(self.root,['P-1','P-2'],{**self.p,'filter':'all'})
        self.assertEqual(yes,{'P-1','P-2'});self.assertFalse(unknown)
        self.assertFalse(result['interpretation_processing_counts'])
        self.assertFalse((self.root/'interpretation.sqlite3').exists())
        yes,unknown,result=er.execute(self.root,[],self.p)
        self.assertFalse(yes|unknown);self.assertEqual(result['patients_evaluated'],0)
        with self.assertRaises(ValueError):er.execute(self.root,['invented'],self.p)

    def test_tampered_source_field_is_rejected_on_inspection(self):
        _,_,out=er.execute(self.root,['P-1'],self.p)
        row=use('a',reported_intent='using')
        self.db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(row),'a'));self.db.commit()
        with self.assertRaisesRegex(ValueError,'hash changed'):er.inspect(self.root,out['patients'][0]['id'])

    def test_native_source_unavailable_remains_unknown(self):
        (self.root/'operators.json').unlink()
        yes,unknown,out=er.execute(self.root,['P-1'],self.p)
        self.assertFalse(yes);self.assertEqual(unknown,{'P-1'})
        row=er.inspect(self.root,out['patients'][0]['id'])
        self.assertFalse(row['bindings']['nonuse']['state']['source_coverage']['native_report_available'])

    def test_linked_record_changed_after_assessment_is_rejected(self):
        rows=[record('o','outside_report_status',referral_record_id='ref',performed_service_documented=True),record('ref','referrals')]
        self.save('P-1',rows)
        _,_,out=er.execute(self.root,['P-1'],{'contract':'native_outside_service','filter':'all'})
        self.db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps({**rows[1],'status':'changed'}),'ref'));self.db.commit()
        with self.assertRaisesRegex(ValueError,'hash changed'):er.inspect(self.root,out['patients'][0]['id'])


if __name__=='__main__':unittest.main()
