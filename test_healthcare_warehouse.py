"""Source migration integrity: original IDs, unknown clocks and no private truth."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from clinical_core.agere.healthcare_warehouse import project, ingest, checksum
from clinical_core.agere.warehouse import Warehouse
from clinical_core.agere.narrative_sources import text_fields
from clinical_core.agere.clinical.warehouse_retrieval import representations
from clinical_core.agere.clinical.clinical_events import window_state
from clinical_core.agere.clinical.longitudinal import record_events
from clinical_core.agere.clinical.graph_workbench import baseline_examples,snapshot_examples
from clinical_core.agere.clinical.economical_planner import local_plan
from clinical_core.agere.clinical.evidence_reader import candidates


def source(**kw):
    return {'record_id':'r1','patient_id':'P-original','source_table':'outside_reports',
        'source_system':'outside','organization_id':'org','event_date':None,
        'recorded_date':'2028-08-01','available_date':'2028-08-03','encounter_id':None,
        'facts':json.dumps({'text':'Patient reports cost concerns; medication supply unavailable.', 'performed':False}),
        'source_patient_id':'public-binding','event_date_precision':'unknown',
        'event_time':None,'recorded_time':None,'signed_time':None,'available_time':None,'synthetic':1,**kw}


class HealthcareWarehouseTests(unittest.TestCase):
    def test_original_identity_and_unknown_clock(self):
        r=project(source());self.assertEqual(r['patient_id'],'P-original');self.assertIsNone(r['event_date'])
        self.assertEqual(r['recorded_date'],'2028-08-01');self.assertEqual(r['available_date'],'2028-08-03')
        self.assertIsNone(window_state(r,{'basis':'source_event','start':'','end':'2028-09-30'},'2028-09-30'))
        self.assertTrue(window_state(r,{'basis':'arrival','start':'','end':'2028-09-30'},'2028-09-30'))
    def test_private_nested_field_rejected(self):
        with self.assertRaisesRegex(ValueError,'Private'):
            project(source(facts=json.dumps({'notes':[{'clean_text':'hidden canary'}]})))
    def test_identity_override_rejected(self):
        with self.assertRaisesRegex(ValueError,'collision'):
            project(source(facts=json.dumps({'patient_id':'different'})))
    def test_explicit_laboratory_name_alias_does_not_invent_status(self):
        r=project(source(source_table='labs',facts=json.dumps({'name':'creatinine','value':1.5,'unit':'mg/dL'})))
        self.assertEqual(r['test'],'creatinine');self.assertNotIn('status',r)
        self.assertEqual(r['_warehouse_source']['field_aliases'],{'test':'facts.name'})
    def test_new_stream_all_fields_searchable_narrative_has_exact_offsets(self):
        r=project(source());parts=list(representations(r))
        self.assertEqual(parts[0]['fields'],['performed','text']);self.assertFalse(parts[0]['dense'])
        self.assertEqual(list(text_fields(r)),[{'field':'text','text':r['text']}]);self.assertTrue(parts[1]['dense'])
        self.assertNotIn('_warehouse_source',parts[0]['text'])
    def test_undated_received_note_can_reach_reader(self):
        r=project(source(facts=json.dumps({'text':'The patient has chronic kidney disease; nephrology visit pending.'})))
        rows=list(candidates('P-original',[r],'2028-09-30'));self.assertTrue(rows)
        self.assertTrue(all(x['source']['event_date'] is None for x in rows))
    def test_native_medication_name_keeps_source_field_and_no_exposure_claim(self):
        import re
        from clinical_core.agere.clinical.medication_identity import inspect_record
        r=project(source(source_table='medication_orders',facts=json.dumps({'medication_name':'unmapped drug','dose':'5 mg'})))
        rows=inspect_record(r,{},re.compile('NEVER_MATCH'))
        self.assertEqual(rows[0]['source']['field'],'medication_name')
        self.assertEqual(rows[0]['identity']['status'],'unmatched')
        self.assertFalse(rows[0]['actual_use_established'])
    def test_observation_stay_not_upcoded_to_admission(self):
        for event,want in [('observation_start','adt'),('admission','admission'),('discharge','discharge')]:
            r=project(source(source_table='adt',event_date='2028-08-01',facts=json.dumps({'event':event})))
            self.assertEqual(list(record_events(r,'2028-09-30'))[0]['event_type'],want)
    def test_native_structured_fields_are_typed_without_collapsing_sources(self):
        from clinical_core.agere.clinical.cohorts import validate
        spec={'all':[{'kind':'record','stream':'pharmacy_dispenses','conditions':[{'field':'payment_channel','op':'eq','value':'cash'}],
            'window':{'basis':'all_observed','start':'','end':''}}],'any':[],'exclude':[]}
        self.assertEqual(validate(spec,'2028-09-30')['all'][0]['stream'],'pharmacy_dispenses')
    def test_example_dates_use_connected_snapshot_and_local_compiler(self):
        row=snapshot_examples(baseline_examples(),'2028-09-30')[0]
        graph,route=local_plan(row['question'],'2028-09-30')
        self.assertEqual(route,'registered_example');self.assertEqual(graph['nodes'][1]['parameters']['available_by'],'2028-09-30')
        self.assertEqual(baseline_examples()[0]['graph']['nodes'][1]['parameters']['available_by'],'2026-09-30')
    def test_public_database_import_coverage_and_hash_guard(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);db=sqlite3.connect(root/'warehouse.sqlite')
            db.executescript('CREATE TABLE patients(patient_id TEXT PRIMARY KEY,display_name TEXT,age_reported INTEGER,sex_reported TEXT,profile_captured_date TEXT); CREATE TABLE organizations(organization_id TEXT); CREATE TABLE clinicians(clinician_id TEXT);')
            db.execute("INSERT INTO patients VALUES ('P-original','Synthetic',42,'F','2026-08-28')")
            rows=[source(),source(record_id='r2',source_table='future_source',available_date='2028-10-01')]
            keys=list(rows[0]);db.execute('CREATE TABLE source_records('+','.join(k+(' INTEGER' if k=='synthetic' else ' TEXT') for k in keys)+')')
            db.executemany('INSERT INTO source_records VALUES ('+','.join('?' for _ in keys)+')',[[r[k] for k in keys] for r in rows]);db.commit();db.close()
            (root/'private').mkdir();(root/'private'/'truth.json').write_text('NOT VALID JSON: must never be opened')
            manifest={'schema':'single-healthcare-warehouse/1','as_of':'2028-09-30','files':{'warehouse.sqlite':checksum(root/'warehouse.sqlite'),'private/truth.json':'bad'}}
            (root/'manifest.json').write_text(json.dumps(manifest));w=Warehouse(root/'derived.sqlite3')
            try:
                rel,receipt=ingest(w,root);self.assertEqual(receipt['counts']['patients'],1)
                patients,snapshot=w.snapshot(rel,'2028-09-30')
                self.assertEqual(list(patients),['P-original']);self.assertEqual(len(patients['P-original']['records']),1)
                self.assertEqual(snapshot['excluded']['late_arrival'],1);self.assertEqual(snapshot['records_with_unknown_event_date'],1)
                self.assertFalse(receipt['private_files_read']);self.assertTrue(ingest(w,root)[1]['cache_hit'])
                with (root/'warehouse.sqlite').open('ab') as f:f.write(b'changed')
                with self.assertRaisesRegex(ValueError,'checksum'):ingest(w,root)
            finally:w.close()


if __name__=='__main__':unittest.main()
