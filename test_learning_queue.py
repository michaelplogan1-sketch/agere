"""Training acquisition protects ancestry and never manufactures reviewed labels."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.contracts import digest
from clinical_core.agere.clinical import learning_queue as q
from clinical_core.agere.clinical import semantic_reader as sr

PROTECTED={'patient_ids':['reserved-id'],'family_ids':['reserved-family'],'register_sha256':'fixture'}


def policy():
    return {'lineage_source':'isolated fixture','patient_families':{'a':'fa','b':'fb','c':'fc','d':'reserved-family'},
        'family_partitions':{'fa':'train','fb':'train','fc':'validation','reserved-family':'train'},'additional_protected_families':[]}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.stack=self.root/'stack';self.stack.mkdir()
        self.meta={'synthetic':True,'source_digest':'source-fixture','snapshot':{'release':'release-fixture','as_of':'2026-09-30'}}
        q.write(self.stack/'run.json',self.meta)
        self.lineage=self.root/'lineage';self.lineage.mkdir();q.write(self.lineage/'policy.json',policy())
        q.write(self.lineage/'manifest.json',{'synthetic':True,'warehouse_release':'release-fixture','policy_sha256':q.sha(self.lineage/'policy.json')})
        self.sources={}
        db=sqlite3.connect(self.stack/'stack.sqlite3');db.executescript('CREATE TABLE patients(id TEXT);CREATE TABLE records(id TEXT,patient TEXT,stream TEXT,payload TEXT)')
        for pid,text in [('a','Today metoprolol is on hold.'),('b','🩺 The visit happened. The visit happened.'),('c','Validation text.'),('d','Protected family variant.')]:
            r={'record_id':pid+'-n','patient_id':pid,'source_table':'notes','identity_status':'matched','synthetic':True,'event_date':'2026-09-01','available_date':'2026-09-02','text':text,'original_text':'HIDDEN CONTENT'}
            db.execute('INSERT INTO patients VALUES (?)',(pid,));db.execute('INSERT INTO records VALUES (?,?,?,?)',(r['record_id'],pid,'notes',json.dumps(r)));self.sources[pid]=r
        db.commit();db.close()
        self.event_path=self.root/'events.sqlite3';db=sqlite3.connect(self.event_path);db.execute('CREATE TABLE events(kind TEXT,payload TEXT)')
        for pid in ('a','c','d'):
            r=self.sources[pid];quote=r['text'].split()[1];a=r['text'].index(quote)
            event={'id':pid,'patient_id':pid,'record_hash':digest(r),'source':{'record_id':r['record_id'],'field':'text','start':a,'end':a+len(quote),'quote':quote},
                'concept':'medication bb','assertion':'affirmed','subject':'patient','time':'current','relation':'treatment_hold_stop','review_routing':{'priority':'context_conflict'}}
            db.execute('INSERT INTO events VALUES (?,?)',('text',json.dumps(event)))
        db.commit();db.close()
        self.protection_patch=patch.object(q.learning,'protections',return_value=PROTECTED);self.protection_patch.start()
        self.event_patch=patch.object(q.longitudinal,'connect',side_effect=lambda stack:(sqlite3.connect(self.event_path),{'reader_model_identity':'fixture'}));self.event_patch.start()
    def tearDown(self):self.event_patch.stop();self.protection_patch.stop();self.temp.cleanup()
    def build(self):return q.build(self.stack,self.lineage,self.root/'queue',size=8)

    def test_ancestry_and_reserved_partitions_excluded_before_selection(self):
        result=self.build();_,_,_,items=q.connected(self.stack)
        self.assertEqual({r['patient_id'] for r in items},{'a','b'})
        self.assertEqual(result['counts']['records_excluded:protected_evaluation_family'],1)
        self.assertEqual(result['counts']['records_excluded:nontraining_partition'],1)
        self.assertEqual(q.eligibility('reserved-id',policy(),PROTECTED),'protected_evaluation_family')
        self.assertEqual(q.eligibility('unknown',policy(),PROTECTED),'missing_patient_family')

    def test_unannotated_received_content_kept_apart_from_interpretation(self):
        result=self.build();_,_,_,items=q.connected(self.stack)
        audit=next(r for r in items if r['patient_id']=='b')
        self.assertIsNone(audit['target']);self.assertEqual(audit['selection_lane'],'extraction_audit')
        self.assertEqual(result['counts']['eligible_fields_without_annotations'],1)
        self.assertTrue(all(r['field']=='text' for r in items))
        self.assertNotIn('HIDDEN CONTENT',(self.root/'queue/items.json').read_text())

    def test_stale_source_does_not_reach_reader(self):
        self.build();_,_,_,items=q.connected(self.stack);item=items[0]
        db=sqlite3.connect(self.stack/'stack.sqlite3');r=self.sources[item['patient_id']];db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps({**r,'text':'Changed'}),r['record_id']));db.commit();db.close()
        with patch.object(sr,'read') as call:
            with self.assertRaisesRegex(ValueError,'changed|Target'):q.suggest(self.stack,item['id'],'jev',allow_live=True)
            call.assert_not_called()

    def test_unannotated_source_requires_explicit_target_before_model_call(self):
        self.build();_,_,_,items=q.connected(self.stack);item=next(r for r in items if r['target'] is None)
        with patch.object(sr,'read') as call:
            with self.assertRaisesRegex(ValueError,'deliberately marked'):q.suggest(self.stack,item['id'],'jev',allow_live=True)
            call.assert_not_called()

    def test_marked_target_keeps_unicode_and_repeated_occurrences_exact(self):
        self.build();_,_,_,items=q.connected(self.stack);item=next(r for r in items if r['patient_id']=='b')
        payload={'item_id':item['id'],'quote':'visit','concept':'completed care','occurrence':1}
        first=q.mark_target(self.stack,payload);second=q.mark_target(self.stack,payload)
        self.assertEqual(first['id'],second['id']);self.assertFalse(first['labels_created'])
        self.assertEqual(first['quote_occurrences'],2);self.assertEqual(first['target']['start'],self.sources['b']['text'].rindex('visit'))
        _,text,_=sr.source(self.stack,first['target']);self.assertEqual(text[first['target']['start']:first['target']['end']],'visit')
        inspected=q.inspect(self.stack,item['id']);self.assertEqual(len(inspected['marked_targets']),1);self.assertFalse(inspected['readings'])
        self.assertFalse(sr.learning_export(self.stack)['rows'])

    def test_missing_quote_and_invalid_occurrence_do_not_create_targets(self):
        self.build();_,_,_,items=q.connected(self.stack);item=items[0]
        for quote,n in [('not in text',0),('visit',True),('visit',-1)]:
            with self.assertRaises(ValueError):q.mark_target(self.stack,{'item_id':item['id'],'quote':quote,'concept':'care','occurrence':n})

    def test_changed_protection_or_frozen_queue_blocks_use(self):
        self.build()
        with patch.object(q.learning,'protections',return_value={**PROTECTED,'register_sha256':'new'}):
            with self.assertRaisesRegex(ValueError,'protections changed'):q.connected(self.stack)
        f=self.root/'queue/items.json';f.write_text('[]')
        with self.assertRaisesRegex(ValueError,'inputs changed'):q.connected(self.stack)

    def test_dedup_and_patient_family_caps_not_overrun(self):
        rows=[]
        for i in range(30):
            rows.append({'id':str(i),'group':str(i),'patient_id':'p'+str(i%4),'patient_family':'f'+str(i%2),'target':{'concept':'c'},'stream':'notes','priority':i})
        chosen,_=q.select(rows+copy.deepcopy(rows),20)
        self.assertLessEqual(len(chosen),6)
        self.assertEqual(len(chosen),len({r['group'] for r in chosen}))
        self.assertEqual(q.select(list(reversed(rows)),20)[0],chosen)

    def test_document_identifiers_do_not_make_duplicate_text_new(self):
        a='Patient: Synthetic Person AB12\nDOB: 1940-01-01\nPatient ID: ECO-AB12\nPrior dose 20 mg; still held.'
        b='Patient: Synthetic Person EF34\nDOB: 1950-02-02\nPatient ID: ECO-EF34\nPrior dose 40 mg; still held.'
        self.assertEqual(q.normalize(a),q.normalize(b))
        self.assertNotEqual(q.normalize(a),q.normalize(b.replace('held','taken')))

    def test_all_items_are_unreviewed_unlabeled_and_list_is_bounded(self):
        self.build();listing=q.list_items(self.stack)
        self.assertLessEqual(len(listing['rows']),12)
        for r in listing['rows']:self.assertFalse(r['human_reviewed']);self.assertIsNone(r['labels']);self.assertFalse(r['training_eligible'])
        with self.assertRaises(ValueError):q.list_items(self.stack,offset=-1)

    def test_family_only_import_discards_latent_fields_and_verifies_roster(self):
        release=self.root/'release';(release/'truth').mkdir(parents=True)
        source=release/'truth/patients.jsonl'
        source.write_text(''.join(json.dumps({'patient_id':p,'source_family':f,'split':'test' if p=='c' else 'train','disease_truth':'HIDDEN','actions':'HIDDEN'})+'\n' for p,f in policy()['patient_families'].items()))
        observed={'observed/patients.jsonl':'dummy-hash'};meta={**self.meta,'snapshot':{**self.meta['snapshot'],'release':digest(observed)}};q.write(self.stack/'run.json',meta)
        q.write(release/'manifest.json',{'synthetic':True,'files':{'observed/patients.jsonl':{'sha256':'dummy-hash'},'truth/patients.jsonl':{'sha256':q.sha(source)}}})
        result=q.import_lineage(self.stack,release,self.root/'import')
        self.assertEqual(result['patients'],4);self.assertEqual(result['extracted_fields'],['patient_id','source_family','split'])
        self.assertNotIn('HIDDEN',(self.root/'import/policy.json').read_text())
        with source.open('a') as f:f.write('{}')
        with self.assertRaisesRegex(ValueError,'changed'):q.import_lineage(self.stack,release,self.root/'wrong')

    def test_execution_failures_are_separate_from_accuracy_or_retrieval(self):
        from clinical_core.agere.clinical.learning_suggestions import failure_kind
        self.assertEqual(failure_kind('Overlength input; use explicit windows rather than truncation'),'context_limit')
        self.assertEqual(failure_kind('ValueError: Invalid distribution'),'provider_response_validation')
        self.assertEqual(failure_kind('Source quote mismatch'),'source_binding')
        self.assertEqual(failure_kind('HTTP 429'),'provider_transport')


if __name__=='__main__':unittest.main()
