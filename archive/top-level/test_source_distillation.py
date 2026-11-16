"""Development acquisition cannot promote teacher guesses or cross protections."""
import copy
import json
from pathlib import Path
import sqlite3
import string
import unittest
from unittest.mock import patch

import test_learning_queue as fixtures
from clinical_core.contracts import digest
from clinical_core.agere.clinical import source_distillation as sd
from clinical_core.agere.clinical import learning_queue as q, semantic_reader as reader
from clinical_core.agere.clinical import semantic_student as student
from clinical_core.agere.clinical import local_decisions


class DistillationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.QueueTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.stack=self.fixture.stack;self.root=self.fixture.root
        policy=fixtures.policy();source=sqlite3.connect(self.stack/'stack.sqlite3');events=sqlite3.connect(self.fixture.event_path)
        for i in range(48):
            pid='development-'+str(i);suffix=string.ascii_uppercase[i//26]+string.ascii_uppercase[i%26]
            text=('Today the patient reports taking metoprolol.' if i%2 else 'Previously the patient denied taking metoprolol. An order was planned.')+' Reference '+suffix+'.'
            r={'record_id':pid,'patient_id':pid,'source_table':'notes','identity_status':'matched','synthetic':True,
               'event_date':'2026-09-01','available_date':'2026-09-02','text':text}
            source.execute('INSERT INTO patients VALUES (?)',(pid,));source.execute('INSERT INTO records VALUES (?,?,?,?)',(pid,pid,'notes',json.dumps(r)))
            policy['patient_families'][pid]=pid;policy['family_partitions'][pid]='train'
            a=text.index('metoprolol');event={'id':pid,'patient_id':pid,'record_hash':digest(r),
                'source':{'record_id':pid,'field':'text','start':a,'end':a+10,'quote':'metoprolol'},
                'concept':'medication bb','assertion':'affirmed','subject':'patient','time':'current','relation':'clinical_observation'}
            events.execute('INSERT INTO events VALUES (?,?)',('text',json.dumps(event)))
        source.commit();events.commit();source.close();events.close()
        q.write(self.fixture.lineage/'policy.json',policy)
        q.write(self.fixture.lineage/'manifest.json',{'synthetic':True,'warehouse_release':'release-fixture','policy_sha256':q.sha(self.fixture.lineage/'policy.json')})
        q.build(self.stack,self.fixture.lineage,self.root/'queue',size=96)
        self.plan=self.root/'plan'
        with patch.object(local_decisions,'verify',side_effect=lambda path:{'fixture':str(path)}):
            self.manifest=sd.prepare(self.stack,self.plan)

    def model(self,provider,fail=False):
        identity=self.manifest['model_identities'][provider]
        class Fake:
            def __call__(self,state,questions):
                if fail: raise RuntimeError('Native execution fixture failure')
                negative='Previously' in state
                labels={'statement':'patient_negated' if negative else 'patient_affirmed',
                    'time':'historical' if negative else 'current_at_source',
                    'relation':'prescription_order' if negative else 'reported_use'}
                return {'answers':{a:{'choice':labels[a],'probabilities':{c:float(c==labels[a]) for c in questions[a]['criteria']}} for a in questions},
                        'stats':{'seconds':0.1,'input_tokens':12}}
        f=Fake();f.identity=identity;return f

    def acquire(self,provider='kev',fail=False):
        with patch.object(local_decisions,'KevMLX' if provider=='kev' else 'LiquidD1',return_value=self.model(provider,fail)) as load:
            result=sd.acquire(self.stack,self.plan,provider)
            return result,load.call_count

    def test_transitive_patient_and_template_partitions_are_order_invariant(self):
        _,_,rows=sd.connected(self.stack)
        rows=copy.deepcopy(rows[:3]);rows[1]['patient_family']=rows[0]['patient_family'];rows[2]['template']=rows[1]['template']
        out=sd.partition(rows)
        self.assertEqual(len({r['component'] for r in out}),1)
        reverse=sd.partition(list(reversed(rows)))
        self.assertEqual({r['item_id']:r['partition'] for r in out},{r['item_id']:r['partition'] for r in reverse})

    def test_plan_only_contains_train_families_and_unmarked_audit_is_unassessed(self):
        _,_,rows=sd.connected(self.stack)
        self.assertNotIn('reserved-family',{r['patient_family'] for r in rows})
        self.assertNotIn('fc',{r['patient_family'] for r in rows})
        self.assertTrue(any(r['reason']=='no_marked_target_not_a_retrieval_miss' for r in self.manifest['unassessed']))
        self.assertEqual(set(self.manifest['counts']),set(student.PARTITIONS))

    def test_native_batch_reuses_one_loaded_model_and_second_run_makes_no_calls(self):
        first,loads=self.acquire();self.assertEqual(loads,1)
        self.assertEqual(len(first['rows']),self.manifest['target_count'])
        again,loads=self.acquire();self.assertEqual(loads,0);self.assertEqual(first,again)
        self.assertFalse(reader.learning_export(self.stack)['rows'])
        self.assertTrue(all(r['new_paid_api_calls']==0 for r in first['rows']))

    def test_failed_attempts_remain_separate_and_are_not_silently_retried(self):
        first,_=self.acquire(fail=True);self.assertTrue(all('error' in r for r in first['rows']))
        again,loads=self.acquire();self.assertEqual(loads,0);self.assertEqual(first,again)
        with self.assertRaisesRegex(ValueError,'Complete fixed acquisition'):sd.freeze(self.stack,self.plan,self.root/'dataset')
        status=sd.status(self.stack);self.assertIsNone(status['interpretation_error_rate']);self.assertIsNone(status['retrieval_recall'])

    def test_unavailable_native_runtime_is_saved_once_without_interpretation(self):
        with patch.object(local_decisions,'KevMLX',side_effect=ImportError('No Metal device available')) as load:
            result=sd.acquire(self.stack,self.plan,'kev')
        self.assertEqual(load.call_count,1);self.assertEqual(len(result['rows']),1)
        self.assertIn('No Metal',result['rows'][0]['error'])
        self.assertNotIn('reading_id',result['rows'][0])

    def test_source_edit_blocks_cached_suggestions_and_training_snapshot(self):
        self.acquire();sd.freeze(self.stack,self.plan,self.root/'dataset')
        db=sqlite3.connect(self.stack/'stack.sqlite3')
        raw=db.execute('SELECT payload FROM records WHERE id=?',('development-0',)).fetchone()[0]
        r=json.loads(raw);r['text']+=' Different source.'
        db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),'development-0'));db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'source/context changed'):sd.load_snapshot(self.stack,self.root/'dataset')

    def test_changed_teacher_cannot_reuse_frozen_supervision(self):
        self.acquire();sd.freeze(self.stack,self.plan,self.root/'dataset')
        db=reader.db(self.stack);key,raw=db.execute('SELECT id,payload FROM readings LIMIT 1').fetchone()
        r=json.loads(raw);r['reading']['labels']['relation']='unknown'
        db.execute('UPDATE readings SET payload=? WHERE id=?',(json.dumps(r),key));db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'Teacher source or labels changed'):sd.load_snapshot(self.stack,self.root/'dataset')

    def test_machine_snapshot_fits_and_serves_without_becoming_reviewed_truth(self):
        self.acquire();self.acquire('d1');sd.freeze(self.stack,self.plan,self.root/'dataset')
        rows,m=sd.load_snapshot(self.stack,self.root/'dataset')
        provenance={'machine_dataset':str(self.root/'dataset'),'snapshot_sha256':q.sha(self.root/'dataset/manifest.json')}
        out=self.root/'student';manifest=student.fit(rows,out,kind='lexical',supervision='machine_teacher_snapshot',provenance=provenance)
        self.assertEqual(manifest['score_reference'],'unreviewed_machine_teacher');self.assertFalse(manifest['release']['automatic_acceptance'])
        q.write(self.stack/'semantic-student-link.json',{'student_lexical':{'path':str(out),'manifest_sha256':q.sha(out/'manifest.json')}})
        target=rows[0]['target'];first=student.read(self.stack,target,'student_lexical');again=student.read(self.stack,target,'student_lexical')
        self.assertTrue(again['cache_hit']);self.assertEqual(first['id'],again['id']);self.assertFalse(first['training_label_created'])
        self.assertFalse(reader.learning_export(self.stack)['rows']);self.assertFalse(first['requirement_bridge']['requirements_satisfied'])
        self.assertFalse(first['current_clinical_state_established']);self.assertEqual(first['usage']['hosted_tokens'],0)
        self.assertEqual(len(sd.status(self.stack)['paired_comparisons']),len(rows))


if __name__=='__main__':unittest.main()
