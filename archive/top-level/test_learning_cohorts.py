"""Extended learning keeps prior source partitions, receipts and failed runs."""
import copy
import json
import sqlite3
import string
import unittest
from unittest.mock import patch

import test_learning_queue as fixtures
from clinical_core.contracts import digest
from clinical_core.agere.clinical import learning_cohorts as c


def example(key, family=None, template=None, part=None):
    text='Source '+key
    row={'item_id':key,'patient_family':family or key,'source_hash':key,'template':template or key,
        'input':{'synthetic':True,'text':text,'target_start':0,'target_end':6,'target_quote':'Source',
                 'concept':'source','context_complete':True}}
    if part:row['partition']=part
    return row


class PartitionTests(unittest.TestCase):
    def test_prior_partition_is_inherited_through_transitive_template_links(self):
        rows=[example('new','a','x'),example('second','b','x')]
        anchors=[example('old','a','older','validation')]
        chosen,excluded=c.partition(rows,anchors)
        self.assertFalse(excluded);self.assertEqual({r['partition'] for r in chosen},{'validation'})
        self.assertEqual({r['partition_basis'] for r in chosen},{'inherited'})
        self.assertEqual((chosen,excluded),c.partition(list(reversed(rows)),anchors))

    def test_cross_partition_component_is_excluded_without_moving_prior_rows(self):
        anchors=[example('old-a','a','x','train'),example('old-b','b','y','calibration')]
        before=copy.deepcopy(anchors)
        rows=[example('bridge','a','y'),example('sibling','c','y'),example('free')]
        chosen,excluded=c.partition(rows,anchors)
        self.assertEqual([r['item_id'] for r in chosen],['free'])
        self.assertEqual({r['item_id'] for r in excluded},{'bridge','sibling'});self.assertEqual(anchors,before)

    def test_identical_model_input_cannot_cross_partitions(self):
        row=example('new');anchor=example('old',part='calibration');anchor['input']=copy.deepcopy(row['input'])
        chosen,_=c.partition([row],[anchor]);self.assertEqual(chosen[0]['partition'],'calibration')


class CohortTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.QueueTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.stack=self.fixture.stack;self.root=self.fixture.root
        policy=fixtures.policy();parts={p:policy['family_partitions'][f] for p,f in policy['patient_families'].items()}
        policy.update(patient_families={p:p for p in parts},family_partitions=parts,additional_protected_families=['d'])
        src=sqlite3.connect(self.stack/'stack.sqlite3');events=sqlite3.connect(self.fixture.event_path)
        for i in range(280):
            pid='dev-'+str(i);suffix=string.ascii_uppercase[i//26]+string.ascii_uppercase[i%26]
            text=('Today patient takes metoprolol.' if i%2 else 'Previously patient denied taking metoprolol. An order was planned.')+' Reference '+suffix
            record={'record_id':pid,'patient_id':pid,'source_table':'notes','identity_status':'matched','synthetic':True,
                'event_date':'2026-09-01','available_date':'2026-09-02','text':text}
            src.execute('INSERT INTO patients VALUES (?)',(pid,));src.execute('INSERT INTO records VALUES (?,?,?,?)',(pid,pid,'notes',json.dumps(record)))
            a=text.index('metoprolol');event={'id':pid,'patient_id':pid,'record_hash':digest(record),
                'source':{'record_id':pid,'field':'text','start':a,'end':a+10,'quote':'metoprolol'},
                'concept':'medication bb','assertion':'affirmed','subject':'patient','time':'current','relation':'clinical_observation'}
            events.execute('INSERT INTO events VALUES (?,?)',('text',json.dumps(event)))
            policy['patient_families'][pid]=pid;policy['family_partitions'][pid]='train'
        src.commit();src.close();events.commit();events.close()
        c.queue.write(self.fixture.lineage/'policy.json',policy)
        c.queue.write(self.fixture.lineage/'manifest.json',{'synthetic':True,'warehouse_release':'release-fixture','policy_sha256':c.queue.sha(self.fixture.lineage/'policy.json')})
        for name in ('annotation-link.json','longitudinal-link.json'):(self.stack/name).write_text('{}')
        c.queue.build(self.stack,self.fixture.lineage,self.root/'original-queue',size=32)
        self.link_before=(self.stack/'learning-queue-link.json').read_bytes()
        with patch.object(c.local_decisions,'verify',return_value={'fixture':'native'}):
            self.plan=c.prepare(self.stack,self.root/'cohort',size=280)
        self.key=self.plan['id'];self.output=self.root/'cohort'

    def fake_model(self, fail=False):
        identity=self.plan['teacher_identity']
        class Fake:
            def __call__(self,state,questions):
                if fail:raise RuntimeError('Native fixture failure')
                negative='Previously' in state
                labels={'statement':'patient_negated' if negative else 'patient_affirmed',
                    'time':'historical' if negative else 'current_at_source','relation':'prescription_order' if negative else 'reported_use'}
                return {'answers':{a:{'choice':labels[a],'probabilities':{k:float(k==labels[a]) for k in questions[a]['criteria']}} for a in questions},
                    'stats':{'seconds':0.01,'input_tokens':12}}
        model=Fake();model.identity=identity;return model

    def acquire(self,fail=False):
        with patch.object(c.local_decisions,'KevMLX',return_value=self.fake_model(fail)) as load:
            result=c.acquire(self.stack,self.key)
        return result,load.call_count

    def test_new_sample_preserves_attached_queue_and_source_links(self):
        self.assertEqual((self.stack/'learning-queue-link.json').read_bytes(),self.link_before)
        self.assertEqual(c.queue.connected(self.stack)[1]['selected'],32)
        self.assertEqual(c.queue.connected(self.output/'input-view')[1]['selected'],280)
        _,_,rows=c.connected(self.stack,self.key)
        self.assertNotIn('c',{r['patient_family'] for r in rows});self.assertNotIn('d',{r['patient_family'] for r in rows})
        self.assertTrue(self.plan['design']['structural_minimums_met'])
        self.assertFalse((self.output/'input-view/semantic-readings.sqlite3').is_symlink())

    def test_native_batch_loads_once_and_resume_reuses_completed_attempts(self):
        first,loads=self.acquire();self.assertEqual(loads,1)
        self.assertEqual(len(first['rows']),self.plan['target_count'])
        second,loads=self.acquire();self.assertEqual(loads,0);self.assertEqual(first,second)
        self.assertEqual(c.status(self.stack,self.key)['acquisition']['pending'],0)
        self.assertFalse(c.reader.learning_export(self.stack)['rows'])

    def test_failed_and_interrupted_attempts_are_retained_without_retry(self):
        first,_=self.acquire(fail=True);self.assertTrue(all(r['status']=='failed' for r in first['rows']))
        again,loads=self.acquire();self.assertEqual(loads,0);self.assertEqual(first,again)
        with self.assertRaisesRegex(ValueError,'Complete declared acquisition'):c.freeze(self.stack,self.key)
        first['rows'][0]={'item_id':first['rows'][0]['item_id'],'status':'running'};c.write(self.output/'attempts.json',first)
        _,loads=self.acquire();self.assertEqual(loads,0)
        self.assertEqual(c.status(self.stack,self.key)['acquisition']['interrupted_or_running'],1)

    def test_missing_model_records_one_failure_then_stops(self):
        with patch.object(c.local_decisions,'KevMLX',side_effect=ImportError('Missing runtime')):
            result=c.acquire(self.stack,self.key)
        self.assertEqual(len(result['rows']),1);self.assertEqual(result['rows'][0]['status'],'failed')

    def test_source_edit_or_rebound_input_view_invalidates_the_cohort(self):
        link=self.output/'input-view/stack.sqlite3';link.unlink();link.symlink_to(self.root/'other.sqlite3')
        with self.assertRaisesRegex(ValueError,'binding changed'):c.connected(self.stack,self.key)
        self.assertEqual(c.listing(self.stack)['cohorts'][0]['status'],'stale')

    def test_changed_protection_registry_prevents_acquisition(self):
        with patch.object(c.queue.learning,'protections',return_value={**fixtures.PROTECTED,'register_sha256':'changed'}),patch.object(c.local_decisions,'KevMLX') as load:
            with self.assertRaisesRegex(ValueError,'protections changed'):c.acquire(self.stack,self.key)
            load.assert_not_called()

    def test_dataset_requires_exact_teacher_labels_and_partitions(self):
        self.acquire();c.freeze(self.stack,self.key);_,_,rows,m=c.dataset(self.stack,self.key)
        self.assertEqual(len(rows),self.plan['target_count']);self.assertEqual(m['supervision'],'machine_teacher_snapshot')
        file=self.output/'dataset/train.jsonl';file.write_text(file.read_text()+'{}\n')
        with self.assertRaisesRegex(ValueError,'partition file changed'):c.dataset(self.stack,self.key)

    def test_complete_compared_heads_serve_shadow_readings_and_exact_replay(self):
        self.acquire();c.freeze(self.stack,self.key)
        # Fit lexical math for both tiny test variants; real encoder execution is
        # exercised by the live run, not a downloaded model in unit tests.
        real=c.student.fit
        with patch.object(c.student,'fit',side_effect=lambda rows,path,**kw:real(rows,path,**{**kw,'kind':'lexical'})):
            fitted=c.fit(self.stack,self.key)
        self.assertEqual(fitted['status'],'complete_shadow_only')
        _,_,targets=c.connected(self.stack,self.key)
        request={'cohort_id':self.key,'kind':'lexical','target':targets[0]['target']}
        first=c.read(self.stack,request);again=c.read(self.stack,request)
        self.assertEqual(first['id'],again['id']);self.assertTrue(again['cache_hit'])
        self.assertFalse(first['reading']['automatic_acceptance']);self.assertFalse(first['training_label_created'])
        self.assertFalse(c.reader.inspect(self.stack,first['id'])['reviews'])
        self.assertFalse(c.reader.learning_export(self.stack)['rows'])

    def test_duplicate_or_foreign_journal_rows_cannot_freeze(self):
        attempts,_=self.acquire();attempts['rows'][0]=attempts['rows'][1];c.write(self.output/'attempts.json',attempts)
        with self.assertRaisesRegex(ValueError,'Complete declared acquisition'):c.freeze(self.stack,self.key)

    def test_failed_representation_is_preserved_without_discarding_completed_shadow_head(self):
        self.acquire();c.freeze(self.stack,self.key);real=c.student.fit
        def fit(rows,path,**kw):
            if kw['kind']=='minilm':raise ValueError('Overlength input; no truncation')
            return real(rows,path,**kw)
        with patch.object(c.student,'fit',side_effect=fit):result=c.fit(self.stack,self.key)
        self.assertEqual(result['status'],'partial_shadow_only');self.assertIn('minilm',result['failures'])
        _,_,targets=c.connected(self.stack,self.key)
        self.assertEqual(c.read(self.stack,{'cohort_id':self.key,'kind':'lexical','target':targets[0]['target']})['provider'],'cohort_lexical')
        with self.assertRaisesRegex(ValueError,'failed variants cannot serve'):
            c.read(self.stack,{'cohort_id':self.key,'kind':'minilm','target':targets[0]['target']})
        with self.assertRaisesRegex(ValueError,'already been attempted'):c.fit(self.stack,self.key)

    def test_new_cohort_preserves_planned_assessment_readings_as_their_prior_partitions(self):
        self.acquire()
        with patch.object(c.local_decisions,'verify',return_value={'fixture':'native'}):
            next_plan=c.prepare(self.stack,self.root/'second-cohort',size=280)
        self.assertEqual(next_plan['prior_reading_exposures'],0)
        _,_,before=c.connected(self.stack,self.key);_,_,after=c.connected(self.stack,next_plan['id'])
        old={r['item_id']:r['partition'] for r in before}
        self.assertTrue(after);self.assertTrue(all(old[r['item_id']]==r['partition'] for r in after if r['item_id'] in old))
        self.assertEqual((self.stack/'learning-queue-link.json').read_bytes(),self.link_before)


if __name__=='__main__':unittest.main()
