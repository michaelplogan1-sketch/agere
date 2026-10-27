"""General documentary analytics supervision and safe component selection."""
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import adapter_learning as A,component_learning as C
from tools.training import adapter_tasks as T,adapters as D,approved as P,evidence_curriculum as E,recursive_adapters as R
from test_adapter_learning import ScriptedBackend,base_fixture,task_manifest
from test_component_learning import Encoder
from test_schema_prefetch import warehouse


class EvidenceLearning(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.path=Path(temp.name)
        for name in ('socket.socket.connect','socket.create_connection','urllib.request.urlopen','subprocess.Popen'):
            p=patch(name,side_effect=AssertionError('Evidence learning is offline'));p.start();self.addCleanup(p.stop)

    def test_twenty_structures_have_independent_targets_across_changed_sources(self):
        root=self.path/'tasks';built=E.build(root,fit_per_family=1,calibration_per_family=1,verification_per_family=1)
        self.assertEqual(built['families'],20);self.assertEqual(built['family_splits'],{'fit':10,'calibration':4,'verification':6})
        for learner in ('typed','bottled'):
            result=E.verify(root,learner);self.assertEqual(result['acceptable'],20,result['failures'][:1])
        _,by,_=T.load(root/'typed-manifest.json')
        self.assertTrue(all(r['source_kind']=='authored_engineering' for rows in by.values() for r in rows))

    def test_wrong_missingness_target_is_detected_by_independent_rows(self):
        root=self.path/'tasks';E.build(root,fit_per_family=1,calibration_per_family=1,verification_per_family=1)
        manifest=root/'typed-manifest.json';spec=json.loads(manifest.read_text());data=Path(spec['files'][0]['path'])
        rows=[json.loads(line) for line in data.read_text().splitlines()]
        row=next(r for r in rows if r['family']=='evidence/below-goal-reason-absent')
        row['reference_sql']=row['reference_sql'].replace("(documented_reason IS NULL OR TRIM(documented_reason)='')","documented_reason IS NOT NULL")
        row['target']['analysis']['steps'][0]['sql']=row['reference_sql']
        data.write_text(''.join(json.dumps(r)+'\n' for r in rows));spec['files'][0]['sha256']=A.file_hash(data);manifest.write_text(json.dumps(spec))
        result=E.verify(root,'typed')
        self.assertTrue(any(f['family']==row['family'] and f['independent']!='Within declared task acceptability' for f in result['failures']))

    def test_independent_latest_and_referral_logic_uses_cutoff_and_not_any_history(self):
        received={'care_review':[{'patient_id':'P','condition':'diabetes','event_date':'2030-01-01','review_id':'1','medication_state':'active'},{'patient_id':'P','condition':'diabetes','event_date':'2030-02-01','review_id':'2','medication_state':'stopped'},{'patient_id':'P','condition':'diabetes','event_date':'2030-06-01','review_id':'3','medication_state':'active'}],
            'care_work':[{'patient_id':'P','condition':'diabetes','referral_date':'2030-01-01','enrollment_state':'referred','completion_date':'2030-06-01'}]}
        latest=next(f for f in E.families() if f.shape=='latest')
        referral=next(f for f in E.families() if f.name=='referred-without-completed-service')
        self.assertEqual(E.expected(latest,received,'diabetes','2030-05-27'),[{'answer':0}])
        self.assertEqual(E.expected(referral,received,'diabetes','2030-05-27'),[{'patient_id':'P'}])

    def test_useful_calibrated_variant_survives_a_higher_argmax_but_wrong_confident_alternative(self):
        rows=[]
        for split in ('fit','calibration','verification'):
            for word,label in (('alpha','count'),('beta','table')):
                rows.append({'question':word+' '+split,'accepted_labels':[label],'family':word+'/'+split,'split':split,'source_kind':'authored_engineering'})
        data=self.path/'shape.jsonl';data.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        manifest=self.path/'manifest.json';manifest.write_text(json.dumps({'schema':'approved-component-data/1','role':'shape','files':[{'path':str(data),'sha256':A.file_hash(data)}],'provenance':'Authored classifier selection regression'}))
        common={'cases':20,'accepted_decision_acceptability':1.0}
        safe={**common,'argmax_acceptability':.8,'coverage':.4,'wrong_confident':0,'encoder_calls_per_prediction':0}
        unsafe={**common,'argmax_acceptability':.95,'coverage':.9,'wrong_confident':1,'encoder_calls_per_prediction':1}
        with patch.object(P,'score',side_effect=[safe,unsafe]):result=P.run(manifest,self.path/'registry',encoder=Encoder(),install=False)
        self.assertEqual(result['status'],'qualified_candidate')
        self.assertEqual(result['qualification']['chosen'],safe)
        self.assertEqual(len(result['qualification']['comparisons']),2)
        self.assertEqual(json.loads(Path(result['model']).read_text())['feature_kind'],'lexical')
        self.assertIsNone(C.active(self.path/'registry','shape'))

    def test_recursive_study_can_start_from_an_explicit_immutable_candidate_without_activation(self):
        warehouse(self.path);manifest,_=task_manifest(self.path);base=base_fixture(self.path/'base');root=self.path/'registry'
        class Backend(ScriptedBackend):
            def train(self,data,output,cfg,resume=None):
                result=super().train(data,output,cfg,resume)
                (output/'adapter_config.json').write_text(json.dumps({'num_layers':cfg['num_layers'],'lora_parameters':{k:cfg[k] for k in ('rank','scale','dropout','keys')}}))
                (output/'adapters.safetensors').write_bytes(('fixture-'+str(len(self.fits))).encode());return result
        backend=Backend();first=D.run(manifest,root,stack=self.path,base=base,backend=backend,train=True,install=False)
        result=R.run(manifest,root,stack=self.path,base=base,backend=backend,rounds=1,train=True,initial_revision=first['revision'])
        self.assertEqual(result['initial_revision'],first['revision']);self.assertIsNotNone(backend.fits[-1]['resume'])
        saved=json.loads((Path(result['study'])/'round-1.json').read_text())
        self.assertIn('parent_candidate',saved['qualification']['comparisons'])
        self.assertIsNone(C.active(root,'slm_typed'))


if __name__=='__main__':unittest.main()
