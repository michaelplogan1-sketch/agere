"""Authored healthcare curriculum and fit-only recursive adapter contracts."""
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from clinical_core.contracts import digest
from clinical_core.agere.clinical import adapter_learning as A,component_learning as C,analysis_graph,literal_check
from tools.training import healthcare_curriculum as H,adapter_tasks as T,adapters as D,recursive_adapters as R
from test_adapter_learning import ScriptedBackend,base_fixture,task_manifest
from test_schema_prefetch import warehouse


class HealthcareLearning(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.path=Path(temp.name)
        for name in ('socket.socket.connect','socket.create_connection','urllib.request.urlopen','subprocess.Popen'):
            p=patch(name,side_effect=AssertionError('No external sends in authored curriculum checks'));p.start();self.addCleanup(p.stop)

    def test_all_domains_have_independent_python_sql_and_native_agreement_on_changed_sources(self):
        root=self.path/'curriculum';built=H.build(root,fit_per_family=1,calibration_per_family=1,verification_per_family=1)
        self.assertEqual(built['families'],104);self.assertEqual(len(built['domains']),13)
        self.assertEqual(built['family_splits'],{'fit':65,'calibration':13,'verification':26})
        for learner in ('typed','bottled'):
            result=H.verify(root,learner);self.assertEqual(result['acceptable'],104,result['failures'][:2])
            self.assertEqual(result['source_fixtures'],3)
        self.assertEqual(len(H.CONDITIONS),11)

    def test_wrong_reference_sql_cannot_pass_by_comparing_it_to_itself(self):
        root=self.path/'curriculum';H.build(root,fit_per_family=1,calibration_per_family=1,verification_per_family=1)
        path=root/'typed-manifest.json';spec=json.loads(path.read_text());data=Path(spec['files'][0]['path'])
        rows=[json.loads(line) for line in data.read_text().splitlines()]
        row=next(r for r in rows if r['family']=='claims/ever-denied')
        row['reference_sql']='SELECT COUNT(*) AS answer FROM adjudication'
        row['target']['analysis']['steps'][0]['sql']=row['reference_sql']
        data.write_text(''.join(json.dumps(r)+'\n' for r in rows));spec['files'][0]['sha256']=A.file_hash(data);path.write_text(json.dumps(spec))
        result=H.verify(root);self.assertLess(result['acceptable'],result['cases'])
        self.assertTrue(any(r['family']=='claims/ever-denied' and r['independent']!='Within declared task acceptability' for r in result['failures']))

    def test_arithmetic_denominators_modifiers_and_distinct_grain_are_not_group_requests(self):
        for q in ('Count patients below their per-patient dose goal.','What are net dollars per received member-month?',
                  'Count each claim once.','Calculate cost multiplied by capacity cost rate.'):
            self.assertNotEqual(analysis_graph.requested_shape(q),'table',q)
        for q in ('Count patients by payer.','What are dollars per member-month by payer?','List dose goals for each patient.'):
            self.assertEqual(analysis_graph.requested_shape(q),'table',q)
        self.assertTrue(literal_check.missing('Count patients without diabetes.','SELECT COUNT(*) FROM patient'))
        self.assertTrue(literal_check.missing('What are dollars per received member-month?','SELECT SUM(paid_cents) FROM claim'))
        self.assertFalse(literal_check.missing('Report total reversal dollars.','SELECT SUM(reversal_cents)/100.0 FROM pharmacy_claim'))

    def test_residual_selection_rejects_verification_and_keeps_approved_targets_and_splits(self):
        warehouse(self.path);manifest,rows=task_manifest(self.path)
        wrong={'task':digest([rows[-1]['question'],rows[-1]['family']]),'family':rows[-1]['family'],'acceptable':False}
        with self.assertRaisesRegex(ValueError,'fit-only'):R.correction_manifest(manifest,[wrong],self.path/'wrong')
        probe={'task':digest([rows[0]['question'],rows[0]['family']]),'family':rows[0]['family'],'acceptable':False}
        new,audit=R.correction_manifest(manifest,[probe],self.path/'corrected')
        _,old,_=T.load(manifest);_,updated,_=T.load(new)
        self.assertEqual(old['verification'],updated['verification']);self.assertEqual(old['calibration'],updated['calibration'])
        self.assertEqual(old['fit'][0]['target'],updated['fit'][0]['target']);self.assertEqual(audit['generated_targets'],0)

    def test_two_rounds_use_immutable_initialization_and_compare_original_and_parent_without_install(self):
        warehouse(self.path);manifest,rows=task_manifest(self.path);base=base_fixture(self.path/'base');root=self.path/'registry'
        class Backend(ScriptedBackend):
            def train(self,data,output,cfg,resume=None):
                result=super().train(data,output,cfg,resume)
                (output/'adapter_config.json').write_text(json.dumps({'num_layers':cfg['num_layers'],'lora_parameters':{k:cfg[k] for k in ('rank','scale','dropout','keys')}}))
                (output/'adapters.safetensors').write_bytes(('authored-round-'+str(len(self.fits))).encode());return result
            @contextmanager
            def generator(self,learner,adapter,ctx,cfg):
                def generate(row):
                    if not adapter:return 'UNSUPPORTED'
                    if row['split']=='fit' and len(self.fits)==1:return 'UNSUPPORTED'
                    return json.dumps(row['target'])
                yield generate
        backend=Backend()
        with patch.dict('os.environ',{'AGERE_COMPONENT_RELEASE_ROOT':str(root),'AGERE_COMPONENT_LEARNING':'on','AGERE_SCHEMA_RERANK':'off'}):
            result=R.run(manifest,root,stack=self.path,base=base,rounds=2,train=True,backend=backend)
        self.assertEqual(result['completed_rounds'],2);self.assertIsNotNone(backend.fits[-1]['resume'])
        self.assertIsNone(C.active(root,'slm_typed'))
        second=json.loads((Path(result['study'])/'round-2.json').read_text())
        self.assertIn('parent_candidate',second['qualification']['comparisons'])
        self.assertEqual(second['qualification']['improvements_over_parent'],0)
        self.assertEqual(second['status'],'not_selected')
        again=R.run(manifest,root,stack=self.path,base=base,rounds=2,train=True,backend=backend)
        self.assertEqual(len(backend.fits),2);self.assertEqual(again['revisions'],result['revisions'])
        model=C.models(root)[0];Path(json.loads(Path(model['model_path']).read_text())['adapter'],'adapters.safetensors').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'artifact changed'):A.training_revision(root,'typed',model['id'],base)


if __name__=='__main__':unittest.main()
