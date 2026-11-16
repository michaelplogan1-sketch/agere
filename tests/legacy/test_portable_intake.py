import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere import portable_source as A
from clinical_core.agere.warehouse import Warehouse
from clinical_core.agere.clinical import portable_intake as P


class PortableIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.x=P.example(None,{})
    def tearDown(self):self.tmp.cleanup()
    def complete(self):
        self.x['mapping']['version']='literal-v2'
        self.x['mapping']['kinds']['chemistry']['fields']['collection_date']='sample_day'
        return self.x
    def preview(self):return P.preview(self.path,self.x)
    def records(self):return self.x['package']['tables']['observations']
    def test_full_denominator_and_clock_exclusions(self):
        r=self.preview();self.assertEqual(r['summary']['roster_patients'],4)
        self.assertEqual(r['summary']['patients_without_visible_records'],1)
        self.assertEqual(r['snapshot']['excluded'],{'future':1,'late_arrival':1,'identity_quarantine':1,'superseded':1})
        self.assertEqual(r['summary']['received_records'],15);self.assertEqual(r['summary']['visible_records'],9)
        self.assertEqual(r['snapshot']['records_with_unknown_event_date'],1)
    def test_mapping_gap_differs_from_unobserved(self):
        r=self.preview();self.assertEqual(r['patients'][3]['evaluation']['requirements'][0]['state']['reason'],'portable_source_mapping_incomplete')
        self.complete();r=self.preview();self.assertEqual(r['patients'][3]['evaluation']['requirements'][0]['state']['status'],'unobserved')
    def test_correction_and_explicit_clock(self):
        self.complete();r=self.preview()['patients'][0]['evaluation']['requirements'][0]['state']
        self.assertEqual(r['status'],'documented');self.assertEqual(r['selected_date'],'2026-09-09')
        self.assertEqual(r['value']['value'],33);self.assertFalse(r['current_clinical_state_established'])
    def test_same_date_disagreement_not_averaged(self):
        self.complete();r=self.preview()['patients'][2]['evaluation']['requirements'][0]['state']
        self.assertEqual(r['status'],'conflicting');self.assertIsNone(r['value'])
    def test_undated_clinical_measurement_not_invented(self):
        self.complete();r=self.preview()['patients'][2]['evaluation']['requirements'][2]['state']
        self.assertEqual(r['status'],'unknown_semantics');self.assertIsNone(r['value'])
    def test_unsupported_clinical_context_remains_unresolved(self):
        self.complete();r=self.preview();self.assertTrue(all(p['evaluation']['status']=='requirements_unresolved' for p in r['patients']))
        self.assertFalse(r['clinical_eligibility_established'])
    def test_exact_false_affordability_is_documentation(self):
        self.x['contract']='affordability';r=self.preview()['patients'][0]['evaluation']['requirements'][0]
        self.assertEqual(r['result'],'satisfied');self.assertIs(r['state']['value'],False)
    def test_unknown_kind_and_missing_receipt_preserved(self):
        r=self.preview();self.assertEqual({i['reason'] for i in r['ingestion']['issues']},{'source_kind_unmapped','receipt_date_unmapped_or_unobserved','correction_parent_not_mapped_or_received'})
        self.assertEqual(r['request']['package'],self.x['package'])
        self.assertNotIn('opaque',{s['record_id'] for p in r['patients'] for s in p['source_evidence']})
    def test_raw_row_hash_crosswalk_and_mapping_provenance(self):
        r=self.preview();source=r['patients'][0]['source_evidence'][0];raw=next(x for x in self.records() if x['observation_key']==source['record_id'])
        self.assertEqual(source['_portable_source']['row_hash'],digest(raw));self.assertEqual(source['patient_id'],'PORT-01')
        self.assertEqual(source['_portable_source']['source_patient_id'],'m1');self.assertNotIn('_warehouse_source',source)
    def test_independently_named_schema_same_documentary_results(self):
        self.complete();before=self.preview()
        old=self.x;package=copy.deepcopy(old['package']);mapping=copy.deepcopy(old['mapping'])
        rename={'observation_key':'ID','member_key':'PERSON','category':'TYPE','written_on':'SOURCE_DAY','authored_on':'SIGNED_DAY','received_on':'RECEIVED_DAY','replaces':'PREVIOUS','analyte':'TEST','number':'RESULT','units':'UNIT','result_state':'STATUS','sample_day':'COLLECTED','clinic':'SPECIALTY','attended':'ATTENDANCE','visit_day':'VISIT','bills_difficult':'HARDSHIP'}
        package['tables']={'people':[{'PERSON':x['member_key']} for x in package['tables']['members']],
                           'events':[{rename.get(k,k):v for k,v in x.items()} for x in package['tables']['observations']]}
        mapping['roster']={'table':'people','id':'PERSON'};mapping['records']['table']='events'
        for k in ('id','patient','kind','supersedes'):mapping['records'][k]=rename[mapping['records'][k]]
        mapping['records']['clocks']={k:rename[v] for k,v in mapping['records']['clocks'].items()}
        for m in mapping['kinds'].values():m['fields']={k:rename[v] for k,v in m['fields'].items()}
        self.x={**old,'package':package,'mapping':mapping};after=self.preview()
        def signature(r):return [(p['patient_id'],[(x['binding'],x['result'],x['state']['status'],x['state']['value']) for x in p['evaluation']['requirements']]) for p in r['patients']]
        self.assertEqual(signature(before),signature(after));self.assertEqual(before['summary'],after['summary'])
        self.assertNotEqual(before['ingestion']['package_hash'],after['ingestion']['package_hash'])
    def test_missing_crosswalk_fails(self):
        self.x['package']['crosswalk'].pop()
        with self.assertRaisesRegex(ValueError,'exact unique roster'):self.preview()
    def test_many_to_one_identity_fails(self):
        self.x['package']['crosswalk'][1]['patient_id']='PORT-01'
        with self.assertRaisesRegex(ValueError,'one-to-one'):self.preview()
    def test_unknown_patient_fails(self):
        self.records()[0]['member_key']='outside'
        with self.assertRaisesRegex(ValueError,'outside explicit'):self.preview()
    def test_duplicate_record_fails(self):
        self.records().append(copy.deepcopy(self.records()[0]))
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.preview()
    def test_private_truth_rejected_even_unmapped(self):
        self.records()[-1]['extra']={'oracle':'not runtime evidence'}
        with self.assertRaisesRegex(ValueError,'Private'):self.preview()
    def test_bad_date_rejected(self):
        self.records()[0]['received_on']='2026-09-31'
        with self.assertRaises(ValueError):self.preview()
    def test_boolean_string_not_coerced(self):
        self.records()[5]['bills_difficult']='false'
        with self.assertRaisesRegex(ValueError,'no coercion'):self.preview()
    def test_boolean_is_not_numeric_measurement(self):
        self.records()[0]['number']=True
        with self.assertRaisesRegex(ValueError,'boolean is not a measurement'):self.preview()
    def test_structured_text_field_not_coerced(self):
        self.records()[0]['result_state']=['final']
        with self.assertRaisesRegex(ValueError,'Literal text'):self.preview()
    def test_reserved_target_mapping_rejected(self):
        self.x['mapping']['kinds']['chemistry']['fields']['patient_id']='other'
        with self.assertRaisesRegex(ValueError,'Unsupported analytic'):self.preview()
    def test_missing_source_column_creates_question(self):
        self.complete();self.x['mapping']['kinds']['chemistry']['fields']['collection_date']='not_received'
        r=self.preview();self.assertIn('mapped_field_not_received',{i['reason'] for i in r['ingestion']['issues']})
        self.assertNotEqual(r['patients'][0]['evaluation']['requirements'][0]['state']['status'],'documented')
    def test_cycle_quarantined(self):
        self.records()[0]['replaces']='lab-corrected';r=self.preview()
        self.assertGreater(r['snapshot']['excluded'].get('correction_conflict',0),0)
    def test_cross_patient_correction_does_not_remove_parent(self):
        self.records()[1]['member_key']='m2';r=self.preview()
        ids={s['record_id'] for p in r['patients'] for s in p['source_evidence']}
        self.assertIn('lab-old',ids);self.assertNotIn('lab-corrected',ids)
    def test_cutoff_changes_only_explicit_snapshot(self):
        self.complete();self.x['cutoff']='2026-09-11';r=self.preview()
        state=r['patients'][0]['evaluation']['requirements'][0]['state'];self.assertEqual(state['value']['value'],31)
        self.x['cutoff']='2026-10-12'
        with self.assertRaisesRegex(ValueError,'beyond'):self.preview()
    def test_ingest_transaction_and_exact_replay(self):
        w=Warehouse(self.path/'warehouse.sqlite3')
        try:
            one=A.ingest(w,self.x['package'],self.x['mapping']);two=A.ingest(w,self.x['package'],self.x['mapping'])
            self.assertEqual(one,two);self.assertEqual(w.db.execute('SELECT COUNT(*) FROM patients').fetchone()[0],4)
        finally:w.close()
    def test_save_reopen_idempotent_and_immutable(self):
        a=P.save(self.path,self.x);b=P.save(self.path,self.x);self.assertEqual(a,b)
        self.assertTrue(P.inspect(self.path,{'id':a['id']})['current']);self.assertEqual(len(P.listing(self.path,{})['reviews']),1)
    def test_mapping_revision_comparison_keeps_old_questions(self):
        a=P.save(self.path,self.x);self.complete();b=P.save(self.path,self.x)
        c=P.compare(self.path,{'before_id':a['id'],'after_id':b['id']})
        self.assertEqual(c['roster_patients'],4);self.assertEqual(len(c['changed_requirements']),12)
        self.assertEqual(P.inspect(self.path,{'id':a['id']})['work_items'],a['work_items'])
        self.assertEqual(c['before_summary']['unresolved_work_items'],22);self.assertEqual(c['after_summary']['unresolved_work_items'],19)
    def test_different_source_cannot_compare_as_mapping_change(self):
        a=P.save(self.path,self.x);self.x['package']['source_id']='different';b=P.save(self.path,self.x)
        with self.assertRaisesRegex(ValueError,'Exact same source'):P.compare(self.path,{'before_id':a['id'],'after_id':b['id']})
    def test_comparison_requires_exact_source_hash(self):
        a=P.save(self.path,self.x)
        self.records()[0]['number']=float(self.records()[0]['number'])
        b=P.save(self.path,self.x)
        self.assertNotEqual(a['ingestion']['package_hash'],b['ingestion']['package_hash'])
        with self.assertRaisesRegex(ValueError,'Exact same source'):P.compare(self.path,{'before_id':a['id'],'after_id':b['id']})
    def test_tampered_saved_result_rejected(self):
        a=P.save(self.path,self.x);a['summary']['roster_patients']=999;db=P.database(self.path)
        with db:db.execute('UPDATE reviews SET payload=?',(json.dumps(a),))
        db.close()
        with self.assertRaisesRegex(ValueError,'integrity'):P.inspect(self.path,{'id':a['id']})
    def test_stale_code_not_current_or_comparable(self):
        a=P.save(self.path,self.x)
        with patch.object(P,'code_identity',return_value='new-code'):
            self.assertFalse(P.inspect(self.path,{'id':a['id']})['current'])
            with self.assertRaisesRegex(ValueError,'Current'):P.compare(self.path,{'before_id':a['id'],'after_id':a['id']})


if __name__=='__main__':unittest.main()
