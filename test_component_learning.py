"""New feedback -> explicit data -> real local fit -> runtime release -> correction/refit/rollback.
Authored classification fixtures, not clinical answer scoring or legacy keys.
"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from clinical_core.agere.clinical import component_learning as C,learning_hooks as H,learned_router as LR,tier_router as TR
from tools.training import self_improve as SI,approved as A


class Encoder:
    identity='authored-feature-fixture'
    def encode(self,questions):return np.array([[1.,0.] if 'alpha' in q else [0.,1.] for q in questions])


class Components(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.path=Path(temp.name)
        self.stack=SimpleNamespace(path=self.path,allow_live=False,retriever=SimpleNamespace(encoder=Encoder()))
        self.root=C.root_for(self.path)
        p=patch.dict(os.environ,{'AGERE_COMPONENT_LEARNING':'on','AGERE_COMPONENT_RELEASE_ROOT':str(self.root),'AGERE_COMPONENT_LEARNING_PLAN':'','AGERE_EVALUATION':'0'});p.start();self.addCleanup(p.stop)
        for target in ('socket.socket.connect','socket.create_connection','subprocess.Popen',
                       'tools.training.train_all.acceptance_accuracy','tools.training.datasets.Bench',
                       'clinical_core.agere.clinical.learning.corrected','clinical_core.agere.clinical.learning.quarantine'):
            p=patch(target,side_effect=AssertionError('No providers, benchmark keys or legacy stores'))
            p.start();self.addCleanup(p.stop)

    def collect(self,role='router',reverse=False):
        choices={'router':['trajectory','queueing'],'tier_router':['typed_decider','backstop_agent'],'shape':['count','table']}[role]
        families={}
        for split in ('fit','calibration','verification'):
            for i,word in enumerate(('alpha','beta')):
                family=f'{split}-{word}';families[family]=split
                question=f'{word} received-source task {split}'
                body={'learning':{'component_review':{'role':role,'accepted':True,'accepted_labels':[choices[1-i if reverse else i]],'family':family,'note':'Authored engineering role judgment'}}}
                r=H.after_answer(self.stack,question,{'answer':{'status':'answered'}},body=body)
                self.assertEqual(r['errors'],[]);self.assertTrue(r['components']['component_learning']['eligible'])
        output=self.path/('data-'+role+('-reverse' if reverse else ''))
        return C.export(self.root,output,role,families)

    def test_feedback_to_fit_release_runtime_rejection_recursive_refit_and_rollback(self):
        manifest=self.collect()
        first=SI.run(root=self.root,manifest=manifest,encoder=Encoder(),install=True)
        self.assertEqual(first['status'],'activated')
        label,receipt=LR.decide('alpha received-source task live',Encoder())
        self.assertEqual(label,'trajectory');self.assertTrue(receipt['used'])
        self.assertEqual(SI.run(root=self.root,manifest=manifest,encoder=Encoder(),install=True)['status'],'unchanged')
        C.feedback(self.root,'router',accepted=False,reason='Engineering feedback changes the accepted interpretation')
        self.assertIsNone(C.runtime_model('router'))
        self.assertEqual(SI.run(root=self.root,manifest=manifest,encoder=Encoder(),install=True)['status'],'review_required')
        second=SI.run(root=self.root,manifest=self.collect(reverse=True),encoder=Encoder(),install=True)
        self.assertEqual(second['parent'],first['revision']);self.assertEqual(second['qualification']['round'],2)
        self.assertEqual(LR.decide('alpha received-source task live',Encoder())[0],'queueing')
        C.rollback(self.root,'router',first['revision'],'Explicit recovery after reverting the authored interpretation')
        self.assertEqual(LR.decide('alpha received-source task live',Encoder())[0],'trajectory')

    def test_tier_and_shape_models_are_consumed_by_existing_runtime(self):
        SI.run(root=self.root,manifest=self.collect('tier_router'),encoder=Encoder(),install=True)
        order,receipt=TR.reorder(self.stack,'beta received-source task live',('exact_local_plan','typed_decider','backstop_agent'))
        self.assertTrue(receipt['applied']);self.assertEqual(order,('exact_local_plan','backstop_agent','typed_decider'))
        SI.run(root=self.root,manifest=self.collect('shape'),encoder=Encoder(),install=True)
        hint=C.shape_hint(self.stack,'alpha received-source task live')
        self.assertTrue(hint['confident']);self.assertEqual(hint['label'],'count')

    def test_source_family_leaks_changed_files_and_unreviewed_observations_are_not_training_data(self):
        r=H.after_answer(self.stack,'Unreviewed answer',{'answer':{'status':'answered'},'learning_requests':{'component_review':{'accepted':True}}})
        self.assertFalse(r['components']['component_learning']['eligible'])
        manifest=self.collect();spec=json.loads(manifest.read_text());data=Path(spec['files'][0]['path'])
        rows=[json.loads(line) for line in data.read_text().splitlines()];rows[-1]['family']=rows[0]['family']
        data.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        with self.assertRaisesRegex(ValueError,'content changed'):A.load(manifest)
        spec['files'][0]['sha256']=hashlib.sha256(data.read_bytes()).hexdigest();manifest.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError,'boundaries'):A.load(manifest)
        self.assertEqual(SI.run(root=self.root)['status'],'requires_approved_manifest')
        with self.assertRaisesRegex(ValueError,'Protected'):C.checked_path(self.path/'private'/'keys.jsonl')

    def test_quarantined_feedback_and_changed_model_bytes_abstain_and_history_is_immutable(self):
        first=SI.run(root=self.root,manifest=self.collect(),encoder=Encoder(),install=True)
        model=C.runtime_model('router');self.assertIsNotNone(model)
        model.write_text('{}');self.assertIsNone(C.runtime_model('router'))
        db=C.database(self.root);self.addCleanup(db.close)
        for table in ('component_models','component_events','component_observations'):
            with self.assertRaisesRegex(Exception,'Immutable'):db.execute('DELETE FROM '+table)

    def test_complete_machine_routing_observations_are_separate_from_human_supervision_and_failures(self):
        result={'answer':{'status':'answered','verification':'checked'},
            'planner_receipt':{'interpretation':{'execution_accepted':True,'proposer':{'name':'backstop_agent'}}},
            'graph':{'nodes':[{'operation':'sql','parameters':{'query':'SELECT 1'}}]}}
        r=H.after_answer(self.stack,'Machine-supervised complete workflow',result)
        self.assertEqual(r['components']['component_learning']['source_kind'],'machine_review')
        self.assertTrue(r['components']['component_learning']['eligible'])
        incomplete={**result,'answer':{'status':'partial','verification':'checked'}}
        r=H.after_answer(self.stack,'Incomplete workflow',incomplete)
        self.assertFalse(r['components']['component_learning']['eligible'])
        failure=H.after_failure(self.stack,'Failed workflow',RuntimeError('Source unavailable'))
        self.assertFalse(failure['eligible'])
        with patch.dict(os.environ,{'AGERE_EVALUATION':'1'}):
            self.assertTrue(H.after_answer(self.stack,'Evaluation',result)['evaluation'])

    def test_classical_head_wins_a_tie_and_runs_without_an_encoder(self):
        families={}
        for split in ('fit','calibration','verification'):
            for word,label in [('How many patients','count'),('List patients','patient_set')]:
                family=f'{split}-{label}';families[family]=split
                H.after_answer(self.stack,word+' '+split,{'answer':{'status':'answered'}},body={'learning':{'component_review':{
                    'role':'shape','accepted':True,'accepted_labels':[label],'family':family,'note':'Authored shape classification'}}})
        manifest=C.export(self.root,self.path/'classical-data','shape',families)
        result=SI.run(root=self.root,manifest=manifest,encoder=Encoder(),install=True)
        self.assertEqual(result['qualification']['chosen']['encoder_calls_per_prediction'],0)
        hint=C.shape_hint(SimpleNamespace(retriever=None),'How many patients today')
        self.assertTrue(hint['confident']);self.assertEqual(hint['label'],'count')

    def test_user_correction_retires_component_and_recursive_execution_reuse_together(self):
        from clinical_core.agere.clinical import harness_improvement as R
        from test_harness_improvement import Executor
        SI.run(root=self.root,manifest=self.collect(),encoder=Encoder(),install=True)
        with patch.dict(os.environ,{'AGERE_RECURSIVE_IMPROVEMENT':'on'}):
            executor=Executor(self.path);self.addCleanup(executor.db.close)
            use={'name':'query_sql','input':{'sql':'SELECT 1'}}
            calls=[{**use,'result':executor(use),'actor_callback_id':'native-fixture'}]
            candidate=R.propose(executor,'question',{},'fixture',calls,lambda:Executor(self.path))
            R.finish(self.path,{'answer':{'status':'answered'},'workflow':[],
                'investigation':{'recursive_improvement':{'candidate':candidate}}})
            self.assertIsNotNone(R.replay(Executor(self.path),'question',{},'fixture')['used'])
            C.observe(self.stack,'question',{'answer':{'status':'answered'},
                'investigation':{'recursive_improvement':{'candidate':candidate}}},
                {'learning':{'correction':{'note':'User correction of the interpretation'}}})
            self.assertIsNone(C.runtime_model('router'))
            fresh=Executor(self.path);self.addCleanup(fresh.db.close)
            self.assertIsNone(R.replay(fresh,'question',{},'fixture')['used'])

    def test_generic_correction_removes_prior_supervision_until_new_review(self):
        self.collect()
        C.observe(self.stack,'alpha received-source task fit',{'answer':{'status':'answered'}},
            {'learning':{'correction':{'note':'Prior routing judgment was wrong'}}})
        families={f'{split}-{word}':split for split in ('fit','calibration','verification') for word in ('alpha','beta')}
        manifest=C.export(self.root,self.path/'corrected-data','router',families)
        spec=json.loads(manifest.read_text())
        rows=[json.loads(line) for line in Path(spec['files'][0]['path']).read_text().splitlines()]
        self.assertNotIn('alpha received-source task fit',[r['question'] for r in rows])
        self.assertEqual(len(rows),5)

    def test_incomplete_deliverable_cannot_supply_positive_routing_supervision(self):
        result={'answer':{'status':'answered','verification':'checked','workflow':[{'kind':'deliverable','quote':'Create a report'}]},
            'planner_receipt':{'interpretation':{'execution_accepted':True,'proposer':{'name':'backstop_agent'}}},'workflow':[]}
        self.assertFalse(C.observe(self.stack,'Analyze and create a report',result)['eligible'])

    def test_optional_invalid_release_location_abstains_without_legacy_fallback(self):
        with patch.dict(os.environ,{'AGERE_COMPONENT_RELEASE_ROOT':str(self.path/'private'/'models')}):
            self.assertIsNone(C.runtime_model('router'))
            self.assertIsNotNone(C.active_identity().get('unavailable'))
            self.assertIsNone(C.shape_hint(self.stack,'How many patients?'))

    def test_after_answer_automatically_fits_releases_reuses_and_recursively_revises(self):
        self.collect()
        families={f'{split}-{word}':split for split in ('fit','calibration','verification') for word in ('alpha','beta')}
        plan=self.path/'learning-plan.json';plan.write_text(json.dumps({'schema':'component-learning-plan/1','jobs':[
            {'role':'router','family_splits':families,'install':True}]}))
        with patch.dict(os.environ,{'AGERE_COMPONENT_LEARNING_PLAN':str(plan)}):
            result=H.after_answer(self.stack,'Ordinary finished workflow',{'answer':{'status':'answered'}})
            self.assertEqual(result['errors'],[])
            first=result['components']['learning_cycle']['jobs'][0]
            self.assertEqual(first['status'],'activated')
            self.assertEqual(LR.decide('alpha request',Encoder())[0],'trajectory')
            result=H.after_answer(self.stack,'Ordinary finished workflow',{'answer':{'status':'answered'}})
            self.assertEqual(result['components']['learning_cycle']['jobs'][0]['status'],'unchanged')
            result=H.after_answer(self.stack,'alpha received-source task fit',{'answer':{'status':'answered'}},
                body={'learning':{'correction':{'note':'Prior label was wrong'}}})
            self.assertEqual(result['components']['learning_cycle']['jobs'][0]['status'],'waiting_for_reviewed_families')
            self.assertIsNone(C.runtime_model('router'))
            with patch.dict(os.environ,{'AGERE_COMPONENT_LEARNING_PLAN':''}):self.collect(reverse=True)
            result=H.after_answer(self.stack,'Another finished workflow',{'answer':{'status':'answered'}})
            second=result['components']['learning_cycle']['jobs'][0]
            self.assertEqual(second['status'],'activated');self.assertEqual(second['parent'],first['revision'])
            self.assertEqual(LR.decide('alpha request',Encoder())[0],'queueing')

    def test_family_partition_is_stable_and_learning_cycle_lock_never_blocks_answers(self):
        import fcntl
        self.collect()
        rule={'salt':'authored-engineering-v1','fit':.6,'calibration':.2,'verification':.2}
        self.assertEqual(C.family_partition(self.root,'router',rule),C.family_partition(self.root,'router',rule))
        plan=self.path/'plan.json';plan.write_text(json.dumps({'schema':'component-learning-plan/1','jobs':[]}))
        with (self.root/'learning.lock').open('a') as lock,patch.dict(os.environ,{'AGERE_COMPONENT_LEARNING_PLAN':str(plan)}):
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=H.after_answer(self.stack,'Another completed question',{'answer':{'status':'answered'}})
            self.assertEqual(result['errors'],[]);self.assertEqual(result['components']['learning_cycle']['status'],'busy')

    def test_candidate_registration_does_not_invalidate_active_execution_identity(self):
        manifest=self.collect();SI.run(root=self.root,manifest=manifest,encoder=Encoder(),install=True)
        before=C.active_identity()
        SI.run(root=self.root,manifest=self.collect(reverse=True),encoder=Encoder(),install=False)
        self.assertEqual(before,C.active_identity())


if __name__=='__main__':unittest.main()
