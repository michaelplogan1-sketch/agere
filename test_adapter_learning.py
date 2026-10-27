"""Authored task contracts + actual native SQL; scripted local generator, no GPU/provider.

The real pinned MLX check is retained separately in local operational evidence.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import component_learning as C,adapter_learning as A,local_request_worker as W,local_normalizer as N,bottled as B,learning_hooks as H
from tools.training import adapters as D,adapter_tasks as T
from test_schema_prefetch import warehouse


def base_fixture(path):
    path.mkdir()
    for name in ('config.json','tokenizer_config.json','tokenizer.json','model.safetensors'):(path/name).write_bytes(b'{}')
    spec={'repository':'authored-fixture','revision':'v1','files':{p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in path.iterdir()}}
    (path/'manifest.json').write_text(json.dumps(spec));return path


def task_manifest(path,learner='typed'):
    from clinical_core.agere.clinical import request_language as L
    ctx=T.Context(path)
    cases=[('fit','denial-breakdown','Count claims where ever_denied = 1 in 2029 by payer',
        "SELECT payer,COUNT(*) AS count FROM claim WHERE ever_denied=1 AND service_date>='2029-01-01' AND service_date<'2030-01-01' GROUP BY payer",{'payer':{'unit':'id'},'count':{'unit':'count'}}),
        ('calibration','patient-coverage','Count patients where coverage_type = medicare',"SELECT COUNT(DISTINCT patient_id) AS patients FROM patient WHERE coverage_type='medicare'",{'patients':{'unit':'count'}}),
        ('verification','appointment-kept','Count appointments where kept = 1','SELECT COUNT(*) AS count FROM appointment WHERE kept=1',{'count':{'unit':'count'}})]
    rows=[]
    for split,family,q,sql,columns in cases:
        rows.append({'question':q,'family':family,'split':split,'source_kind':'authored_engineering',
            'target':L.parse(ctx.catalog,q) if learner=='typed' else q if learner=='normalizer' else sql,
            'reference_sql':sql,'acceptance':{'returns':'table' if len(columns)>1 else 'count','columns':columns,'keys':['payer'] if len(columns)>1 else []}})
    data=path/(learner+'-tasks.jsonl');data.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    spec={'schema':'approved-adapter-data/1','learner':learner,'source_identity':ctx.identity,'training':{'iterations':2,'rank':2,'num_layers':1},
        'files':[{'path':str(data),'sha256':A.file_hash(data)}],'provenance':'Newly authored engineering task contracts; not clinical answer keys'}
    manifest=path/(learner+'-manifest.json');manifest.write_text(json.dumps(spec));return manifest,rows


class ScriptedBackend:
    def __init__(self):self.fits=[]
    def train(self,data,output,cfg,resume=None):
        self.fits.append({'train':(data/'train.jsonl').read_text(),'calibration':(data/'valid.jsonl').read_text(),'resume':resume})
        if (data/'test.jsonl').exists():raise AssertionError('Verification must not enter the MLX training loader')
        output.mkdir(parents=True,exist_ok=True);(output/'adapter_config.json').write_text('{}');(output/'adapters.safetensors').write_bytes(b'authored-test-weights')
        return {'scripted_trainer':True,'truncated':False}
    @contextmanager
    def generator(self,learner,adapter,ctx,cfg):
        # Model boundaries are scripted; compilation/query/acceptance are real.
        yield lambda row:json.dumps(row['target']) if isinstance(row['target'],dict) and adapter and adapter.name=='candidate' else row['target'] if adapter and adapter.name=='candidate' else 'UNSUPPORTED'


class AdapterLearning(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.path=Path(temp.name);warehouse(self.path)
        self.base=base_fixture(self.path/'base');self.root=self.path/'component-learning';self.manifest,self.rows=task_manifest(self.path)
        self.stack=SimpleNamespace(path=self.path,retriever=None)
        p=patch.dict(os.environ,{'AGERE_COMPONENT_RELEASE_ROOT':str(self.root),'AGERE_COMPONENT_LEARNING':'on','AGERE_COMPONENT_LEARNING_PLAN':'','AGERE_EVALUATION':'0'});p.start();self.addCleanup(p.stop)
        for name in ('socket.socket.connect','socket.create_connection','urllib.request.urlopen','subprocess.Popen',
            'tools.training.train_all.evaluate_adapter','tools.training.datasets.Bench','clinical_core.agere.clinical.bottled._bench_questions'):
            p=patch(name,side_effect=AssertionError('Offline adapter tests forbid providers and legacy sources'));p.start();self.addCleanup(p.stop)

    def run_job(self,**kw):return D.run(self.manifest,self.root,stack=self.path,base=self.base,backend=kw.pop('backend',ScriptedBackend()),**kw)

    def test_prepare_validates_tasks_without_fitting_and_never_materializes_verification_for_training(self):
        backend=ScriptedBackend();r=self.run_job(backend=backend)
        self.assertEqual(r['status'],'prepared');self.assertEqual(backend.fits,[])
        files=list((self.root/'adapter-jobs').rglob('*.jsonl'))
        self.assertEqual({p.name for p in files},{'train.jsonl','valid.jsonl'})
        self.assertNotIn(self.rows[-1]['question'],''.join(p.read_text() for p in files))

    def test_fit_release_runtime_correction_refit_and_rollback(self):
        with patch.object(T.Context,'deterministic',return_value=False):first=self.run_job(train=True,install=True)
        self.assertEqual(first['status'],'activated')
        with patch.object(W,'_base',return_value=self.base):self.assertIsNotNone(W.typed_adapter())
        C.feedback(self.root,'slm_typed',accepted=False,reason='Authored interpretation corrected')
        self.assertIsNone(A.runtime_adapter('typed',self.base))
        self.assertEqual(self.run_job(train=True,install=True)['status'],'review_required')
        spec=json.loads(self.manifest.read_text());spec['provenance']='Newly reviewed revised task supervision';self.manifest.write_text(json.dumps(spec))
        with patch.object(T.Context,'deterministic',return_value=False):second=self.run_job(train=True,install=True)
        self.assertEqual(second['parent'],first['revision']);self.assertEqual(second['qualification']['round'],2)
        C.rollback(self.root,'slm_typed',first['revision'],'Explicit engineering rollback')
        self.assertIsNotNone(A.runtime_adapter('typed',self.base))

    def test_no_release_when_free_grammar_already_answers_all_tasks(self):
        result=self.run_job(train=True,install=True)
        self.assertEqual(result['status'],'not_selected');self.assertEqual(result['qualification']['deterministic_already_acceptable'],1)
        self.assertIsNone(A.runtime_adapter('typed',self.base))

    def test_all_four_runtime_consumers_use_versioned_releases_and_retire_cached_models(self):
        adapter=self.path/'weights';adapter.mkdir();(adapter/'adapter_config.json').write_text('{}');(adapter/'adapters.safetensors').write_bytes(b'weights')
        for learner in A.LEARNERS:A.capture(self.root,learner,adapter,self.base,{'selected':True,'clinical_qualification':False},activate=True)
        with patch.object(W,'_base',return_value=self.base),patch.object(N,'BASE_MODEL',self.base),patch.object(B,'base_dir',return_value=self.base):
            self.assertIsNotNone(W.typed_adapter());self.assertTrue(N.available());self.assertTrue(B.BottledModel.available());self.assertTrue(B.NarrowModel.available())
            from clinical_core.agere.clinical import proposers
            with patch.dict(os.environ,{'AGERE_BOTTLED':'on'}):self.assertTrue(proposers.enabled('bottled_sql',self.stack))
            with patch.dict(os.environ,{'AGERE_PROPOSER_SKIP':'bottled_sql'}):self.assertFalse(proposers.enabled('bottled_sql',self.stack))
            for learner in A.LEARNERS:C.feedback(self.root,A.role(learner),accepted=False,reason='End engineering release')
            self.assertIsNone(W.typed_adapter());self.assertFalse(N.available());self.assertFalse(B.BottledModel.available());self.assertFalse(B.NarrowModel.available())

    def test_weights_base_or_source_mutation_cannot_silently_reuse_a_release(self):
        with patch.object(T.Context,'deterministic',return_value=False):r=self.run_job(train=True,install=True)
        before=C.active_identity();model=json.loads(Path(r['model']).read_text());(Path(model['adapter'])/'adapters.safetensors').write_bytes(b'changed')
        self.assertIsNone(A.runtime_adapter('typed',self.base));self.assertNotEqual(before,C.active_identity())
        (self.base/'model.safetensors').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'base model changed'):A.pin_base(self.base)

    def test_family_leaks_changed_inputs_and_invalid_native_targets_are_rejected(self):
        spec=json.loads(self.manifest.read_text());data=Path(spec['files'][0]['path']);rows=self.rows.copy();rows[-1]={**rows[-1],'family':rows[0]['family']}
        data.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        with self.assertRaisesRegex(ValueError,'content changed'):T.load(self.manifest)
        spec['files'][0]['sha256']=A.file_hash(data);self.manifest.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError,'boundaries'):T.load(self.manifest)

    def test_clinically_readable_acceptance_allows_rounding_and_yes_no_without_hiding_material_errors(self):
        self.assertTrue(T.same_value(49.9999,50,{'unit':'percent','decimals':1}))
        self.assertTrue(T.same_value(100.004,100,{'unit':'dollars','decimals':2}))
        self.assertTrue(T.same_value('yes',True,{'unit':'yes_no'}))
        self.assertFalse(T.same_value(49,50,{'unit':'percent','decimals':1}))
        self.assertFalse(T.same_value(None,0,{'unit':'count'}))
        self.assertFalse(T.same_value('possibly','no',{'unit':'yes_no'}))

    def test_malformed_model_data_declines_but_generator_infrastructure_errors_invalidate_the_run(self):
        result=T.Context(self.path).assess('typed',self.rows[-1],json.dumps({'source':'appointment','select':[]}))
        self.assertFalse(result['guarded']);self.assertIn('source must be an object',result['reason'])
        def broken(row):raise RuntimeError('GPU execution failed')
        with self.assertRaisesRegex(RuntimeError,'GPU execution failed'):D.evaluate(T.Context(self.path),'typed',self.rows,broken)

    def test_formatting_preamble_does_not_change_a_complete_plan_and_ambiguous_outputs_are_rejected(self):
        row=self.rows[-1];target=json.dumps(row['target'])
        self.assertTrue(T.Context(self.path).assess('typed',row,'Formatting preamble\n'+target)['acceptable'])
        self.assertEqual(W.decode('!\n\n!\n\n'+target),row['target'])
        for text in (target+'\n'+target,'{"bad":'+target,target+' trailing explanation'):
            with self.assertRaises(ValueError):W.decode(text)

    def test_reporting_caution_is_not_a_patient_exclusion_and_scalar_aliases_are_not_answer_keys(self):
        from clinical_core.agere.clinical.literal_check import missing
        for caution in ('Keep missing values explicit.','Do not infer outside records.','Never invent unavailable evidence.'):
            self.assertEqual(missing('Count patients. '+caution,'SELECT COUNT(*) FROM patient'),[])
            self.assertTrue(any('negates or excludes' in g for g in missing('Count patients without diabetes. '+caution,'SELECT COUNT(*) FROM patient')))
        actual={'columns':['count'],'rows':[{'count':2}]};expected={'columns':['patients'],'rows':[{'patients':2}]}
        self.assertTrue(T.compare(actual,expected,{'returns':'count','columns':{'patients':{'unit':'count'}}})[0])
        self.assertFalse(T.compare({**actual,'rows':[{'count':3}]},expected,{'returns':'count','columns':{'patients':{'unit':'count'}}})[0])
        rule={'returns':'table','columns':{'payer':{'unit':'id'},'claims':{'unit':'count'}},'keys':['payer']}
        reference={'columns':['payer','claims'],'rows':[{'payer':'P1','claims':2}]}
        equivalent={'columns':['payer','denied_claims'],'rows':[{'payer':'P1','denied_claims':2}]}
        self.assertTrue(T.compare(equivalent,reference,rule)[0])
        self.assertFalse(T.compare({**equivalent,'rows':[{'payer':'P1','denied_claims':3}]},reference,rule)[0])
        self.assertTrue(any('negates or excludes' in g for g in missing('Count patients. Keep missing values explicit and exclude patients without diabetes.','SELECT COUNT(*) FROM patient')))

    def test_direct_review_queues_heavy_learning_without_running_it_inside_an_answer(self):
        for row in self.rows:
            review={'learner':'typed','accepted':True,**{k:row[k] for k in ('target','reference_sql','acceptance','family')},'note':'Authored engineering review'}
            result=H.after_answer(self.stack,row['question'],{'answer':{'status':'answered'}},body={'learning':{'adapter_review':review}})
            self.assertEqual(result['errors'],[])
        plan=self.path/'plan.json';plan.write_text(json.dumps({'schema':'component-learning-plan/1','jobs':[{'learner':'typed','base':str(self.base),
            'family_splits':{r['family']:r['split'] for r in self.rows},'training':{'iterations':2},'install':False}]}))
        with patch.dict(os.environ,{'AGERE_COMPONENT_LEARNING_PLAN':str(plan)}),patch.object(D,'run',return_value={'status':'prepared'}) as trained:
            result=H.after_answer(self.stack,'Ordinary answer',{'answer':{'status':'answered'}})
            self.assertEqual(result['components']['learning_cycle']['jobs'][0]['status'],'queued_offline_adapter_job');trained.assert_not_called()
            self.assertEqual(C.run_plan(self.stack,train_adapters=True)['jobs'][0]['status'],'prepared');trained.assert_called_once()

    def test_normalizer_and_bottled_use_their_actual_serving_messages_in_preparation(self):
        for learner in ('normalizer','bottled'):
            manifest,_=task_manifest(self.path,learner)
            with patch('clinical_core.agere.clinical.source_profile.load',return_value={'streams':{}}),patch.dict(os.environ,{'AGERE_SCHEMA_RERANK':'off'}):
                result=D.run(manifest,self.root,stack=self.path,base=self.base)
            self.assertEqual(result['status'],'prepared');self.assertEqual(result['learner'],learner)

    def test_failed_generation_is_preserved_and_cannot_activate_a_release(self):
        class Broken(ScriptedBackend):
            @contextmanager
            def generator(self,*args):
                def generate(row):raise RuntimeError('GPU backend unavailable')
                yield generate
        with self.assertRaisesRegex(RuntimeError,'GPU backend unavailable'):self.run_job(train=True,backend=Broken())
        self.assertTrue(list((self.root/'adapter-failures').glob('*.json')))
        self.assertIsNone(C.active(self.root,'slm_typed'))

    def test_saved_answer_review_derives_native_targets_without_manual_json_and_rejects_stale_review(self):
        from clinical_core.agere.deliverables import answer_context
        from clinical_core.agere.service import answer_feedback
        q=self.rows[-1]['question'];sql=self.rows[-1]['reference_sql']
        result={'answer':{'status':'answered','answer_type':'count','count':1,'verification':'checked'},
            'graph':{'nodes':[{'id':'answer','operation':'sql','parameters':{'query':sql,'returns':'count'}}]},
            'planner_receipt':{'interpretation':{'execution_accepted':True}}}
        saved=answer_context.capture(self.path,q,result)
        with patch('clinical_core.agere.clinical.learning.record_correction',side_effect=AssertionError('No legacy correction store')):
            receipt=answer_feedback(self.stack,{'context_id':saved['id'],'question':q,'accepted':True,'note':'Authored saved-answer review'})
            self.assertEqual(receipt['derived_adapter_tasks'],2);self.assertTrue(receipt['eligible'])
            receipt=answer_feedback(self.stack,{'context_id':saved['id'],'question':q,'accepted':False,'note':'Correction of the accepted interpretation'})
            self.assertIn('revalidation',receipt['effect'])
            with self.assertRaisesRegex(ValueError,'source/policy changed'):
                answer_feedback(self.stack,{'context_id':saved['id'],'accepted':True,'note':'Cannot silently approve stale evidence'})

    def test_observations_do_not_change_policy_but_a_correction_without_active_models_does(self):
        self.assertIsNone(C.active_identity())
        C.observe(self.stack,'Unreviewed answer',{'answer':{'status':'answered'}})
        self.assertIsNone(C.active_identity())
        C.handle_feedback(self.stack,{'question':'Unreviewed answer','note':'Wrong interpretation'})
        self.assertIsNotNone(C.active_identity())

    def test_narrow_slots_round_trip_through_the_existing_cohort_engine_and_source_sql(self):
        import sqlite3
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript("CREATE TABLE payer_claim(patient_id,type_of_bill,counts_in_metrics,admission_date);CREATE TABLE adt_visit(patient_id,final_patient_class,patient_class,admit_date);CREATE TABLE death(patient_id);")
            db.executemany('INSERT INTO payer_claim VALUES(?,?,?,?)',[('A','0111',1,'2029-07-01'),('A','0111',1,'2029-08-01'),('B','0111',1,'2029-07-01')])
        slots={'family':'utilization_threshold','ask':'list','event':'admission','min_count':2,'lookback':{'months':12}}
        q=B.canonical_question(slots)
        row={'question':q,'target':slots,'reference_sql':"SELECT patient_id FROM payer_claim WHERE counts_in_metrics=1 AND admission_date>='2029-05-27' GROUP BY patient_id HAVING COUNT(DISTINCT admission_date)>=2",'acceptance':{'returns':'patient_set','columns':{'patient_id':{'unit':'id'}},'keys':['patient_id']}}
        result=T.Context(self.path).assess('narrow',row,json.dumps(slots))
        self.assertTrue(result['acceptable'],result)


if __name__=='__main__':unittest.main()
