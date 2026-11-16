"""The new correction -> frozen dataset path, using isolated authored sources."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import typed_distillation as td, learning_queue as queue


class TypedLearningReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.source = self.root/'source'; self.source.mkdir()
        self.stack = self.root/'build/stack'; self.stack.mkdir(parents=True)
        self.lineage = self.root/'lineage'; self.lineage.mkdir()
        self.database = self.source/'warehouse.sqlite'
        self.text = 'The patient reports missing the Tuesday dose.'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE source_records(record_id TEXT PRIMARY KEY, patient_id TEXT,source_table TEXT,event_date TEXT,available_date TEXT,facts TEXT,synthetic INTEGER)')
            db.execute('INSERT INTO source_records VALUES (?,?,?,?,?,?,?)', ('R1','P1','notes','2030-01-01','2030-01-02',json.dumps({'text':self.text}),1))
        self.snapshot = {'release':'authored-source-v1','as_of':'2030-01-10',
            'source_dataset':{'source':str(self.database),'source_sha256':queue.sha(self.database)}}
        queue.write(self.stack/'run.json', {'synthetic':True,'snapshot':self.snapshot})
        policy = {'lineage_source':'authored-person-roster','patient_families':{'P1':'F1'},
            'family_partitions':{'F1':'train'},'additional_protected_families':[]}
        queue.write(self.lineage/'policy.json',policy)
        queue.write(self.lineage/'manifest.json',{'synthetic':True,'warehouse_release':self.snapshot['release'],
            'policy_sha256':queue.sha(self.lineage/'policy.json'),'protection_register_sha256':'authored-exclusions'})
        self.protected = {'patient_ids':[],'family_ids':[],'register_sha256':'authored-exclusions'}
        self.patch = patch.object(td.semantic_learning,'protections',return_value=self.protected);self.patch.start();self.addCleanup(self.patch.stop)
        self.environment = patch.dict(os.environ,{'AGERE_SYSTEM_ONE_DISTILL':str(self.root/'observations.sqlite3'),
            'AGERE_TYPED_LEARNING_LINEAGE':str(self.lineage)});self.environment.start();self.addCleanup(self.environment.stop)
        self.options = {'yes':'The note explicitly reports a missed dose','no':'The note explicitly reports taking every dose','unknown':'The note does not settle the question'}
        self.question = 'Does the received note report a missed dose?'
        self.evidence = [{'id':'R1#0','record_id':'R1','date':'2030-01-01','text':self.text}]
        r = td.record_outcome(self.question,self.options,self.evidence,
            {'label':'no','probabilities':{'no':.99},'decided_by':'jev','provider':'jev','model':'authored-transport'},as_of='2030-01-10')
        self.ident = 'typed_outcomes:'+r['id']
        self.config = self.root/'config.json'
        queue.write(self.config,{'synthetic':True,'build':str(self.stack.parent),'source_release':str(self.source),
            'snapshot':self.snapshot,'runtime':{'environment':{'AGERE_SYSTEM_ONE_DISTILL':str(self.root/'observations.sqlite3')}}})

    def payload(self):
        r = td.inspect_review(self.stack,self.ident)
        return {'observation_id':self.ident,'expected_binding':r['binding'],'expected_revision':r['revision'],
            'request_id':'authored-review-1','actor':'Authored engineering fixture','status':'corrected','label':'yes',
            'rationale':'The exact received text explicitly reports missing the dose.','use_for_development':True}

    def freeze(self,name):
        output=self.root/name
        td.prepare(self.config,self.lineage,output,options_sha256=td.option_contract(self.options))
        return td.load(output)

    def test_correction_replays_and_reaches_dataset_as_declared_review(self):
        payload=self.payload();review=td.save_review(self.stack,payload)
        self.assertTrue(td.save_review(self.stack,payload)['replayed'])
        manifest,rows=self.freeze('reviewed')
        self.assertEqual(rows[0]['label'],'yes');self.assertEqual(rows[0]['fit_status'],'local_documentary_review')
        self.assertEqual(manifest['local_documentary_references'],1)
        self.assertEqual(manifest['independent_reviewed_references'],0)
        self.assertFalse(review['reviewer_identity_verified']);self.assertFalse(review['clinical_state_established'])

    def test_rejection_withdraws_supervision_without_deleting_prior_review(self):
        td.save_review(self.stack,self.payload());second=self.payload()
        second.update(request_id='authored-review-2',status='rejected',label=None,use_for_development=False)
        td.save_review(self.stack,second)
        _,rows=self.freeze('withdrawn');self.assertIsNone(rows[0]['label'])
        with sqlite3.connect(self.root/'observations.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM typed_reviews').fetchone()[0],2)
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM typed_reviews')

    def test_source_changes_and_protected_people_reject_review(self):
        payload=self.payload();self.protected['patient_ids']=['P1']
        with self.assertRaisesRegex(ValueError,'protected'):td.save_review(self.stack,payload)
        self.protected['patient_ids']=[]
        with sqlite3.connect(self.database) as db:db.execute('UPDATE source_records SET facts=?',(json.dumps({'text':'Changed source text.'}),))
        with self.assertRaisesRegex(ValueError,'current_received_text'):td.save_review(self.stack,payload)

    def test_stale_revision_and_reused_request_cannot_overwrite(self):
        payload=self.payload();td.save_review(self.stack,payload)
        with self.assertRaisesRegex(ValueError,'identity reused'):td.save_review(self.stack,{**payload,'rationale':'Different reason'})
        with self.assertRaisesRegex(ValueError,'Review changed'):td.save_review(self.stack,{**payload,'request_id':'other'})


if __name__=='__main__':unittest.main()
