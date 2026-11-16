import copy
import json
import unittest
from unittest.mock import patch
import numpy as np
from test_learning_cohorts import CohortTests, example
from clinical_core.agere.clinical import windowed_student as w, cohort_diagnostics as d


class Encoder:
    identity='fixture-encoder'
    def __init__(self,path='fixture',**kwargs):
        self.path=path;self.stats={'input_tokens':0,'texts_encoded':0,'inference_seconds':0};self.closed=False
    def windows(self,text,**kwargs):
        result=[];start=0
        while start<len(text):
            end=min(start+64,len(text));result.append({'start':start,'end':end,'text':text[start:end],'token_start':start,'token_end':end})
            if end==len(text):break
            start=end-16
        return result
    def encode(self,texts,**kwargs):
        self.stats['input_tokens']+=sum(len(t) for t in texts);self.stats['texts_encoded']+=len(texts)
        return np.array([[1.,len(t)/64,float('Previously' in t),sum(map(ord,t))%47/47] for t in texts],dtype=np.float32)
    def close(self):self.closed=True


class WindowTests(unittest.TestCase):
    def test_every_character_and_token_range_retained_with_target_view(self):
        p=example('x')['input'];p.update(text='a'*180+' Target '+'b'*240,target_start=181,target_end=187,target_quote='Target')
        matrix,coverage=w.features(Encoder(),[p]);r=coverage[0]
        self.assertEqual(matrix.shape,(1,8));self.assertGreater(len(r['windows']),1);self.assertGreater(r['target_windows'],0)
        text=w.student.model_text(p);self.assertEqual(r['unique_input_tokens'],len(text));self.assertEqual(r['windows'][-1]['end'],len(text))
        self.assertFalse(r['truncated']);self.assertTrue(r['complete_source_coverage'])

    def test_missing_window_and_incomplete_context_rejected(self):
        p=example('x')['input'];p.update(text='Source '+'z'*400)
        encoder=Encoder();real=encoder.windows
        encoder.windows=lambda text,**kw:real(text,**kw)[::2]
        with self.assertRaisesRegex(ValueError,'coverage|Gap'):w.features(encoder,[p])
        p['context_complete']=False
        with self.assertRaisesRegex(ValueError,'complete received text'):w.features(Encoder(),[p])

    def test_literal_concept_markers_do_not_move_the_source_target(self):
        p=example('x')['input'];p['concept']='[TARGET] literal concept [/TARGET]'
        _,coverage=w.features(Encoder(),[p]);text=w.student.model_text(p);r=coverage[0]
        self.assertEqual(text[r['target_start']:r['target_end']],'[TARGET] Source [/TARGET]')


class FitTests(unittest.TestCase):
    setUp=CohortTests.setUp
    fake_model=CohortTests.fake_model
    acquire=CohortTests.acquire

    def fit(self):
        self.acquire();w.cohorts.freeze(self.stack,self.key)
        with patch.object(w.local_encoders,'LocalEncoder',Encoder):return w.fit(self.stack,self.key)

    def test_fit_preserves_teacher_dataset_and_serves_same_question_contract_with_cache(self):
        self.acquire();w.cohorts.freeze(self.stack,self.key)
        before=(self.output/'dataset/manifest.json').read_bytes()
        with patch.object(w.local_encoders,'LocalEncoder',Encoder):model=w.fit(self.stack,self.key)
        self.assertEqual(before,(self.output/'dataset/manifest.json').read_bytes());self.assertFalse(model['foundation_weights_updated'])
        self.assertFalse(model['release']['automatic_acceptance']);self.assertEqual(model['counts'],self.plan['counts'])
        _,_,rows=w.cohorts.connected(self.stack,self.key)
        request={'cohort_id':self.key,'kind':'windowed_minilm','target':rows[0]['target']}
        with patch.object(w.local_encoders,'LocalEncoder',Encoder):first=w.read(self.stack,request)
        with patch.object(w.local_encoders,'LocalEncoder',side_effect=AssertionError('No load on exact cache hit')):
            again=w.read(self.stack,request)
        self.assertTrue(again['cache_hit']);self.assertEqual(first['id'],again['id']);self.assertFalse(first['training_label_created'])
        self.assertTrue(first['student']['window_coverage']['complete_source_coverage'])
        self.assertFalse(w.reader.learning_export(self.stack)['rows'])
        detail=d.inspect(self.stack,self.key,'windowed_minilm',outcome='all')
        self.assertEqual(detail['validation_rows'],self.plan['counts']['validation'])

    def test_failure_kept_and_no_automatic_refit(self):
        self.acquire();w.cohorts.freeze(self.stack,self.key)
        with patch.object(w.local_encoders,'LocalEncoder',side_effect=RuntimeError('Fixture failure')):
            with self.assertRaisesRegex(RuntimeError,'Fixture failure'):w.fit(self.stack,self.key)
        self.assertEqual(w.status(self.stack,self.key)['status'],'failed')
        with self.assertRaisesRegex(ValueError,'already attempted'):w.fit(self.stack,self.key)

    def test_modified_model_rejected(self):
        self.fit();path=self.output/w.DIRECTORY/'window-coverage.json';path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'artifact changed'):w.model(self.stack,self.key)

    def test_new_provider_requires_exact_request_schema(self):
        with self.assertRaisesRegex(ValueError,'Exact cohort'):w.read(self.stack,{'cohort_id':self.key,'kind':'minilm','target':{}})

    def test_validation_labels_cannot_change_weights_or_calibration(self):
        self.acquire();w.cohorts.freeze(self.stack,self.key)
        output,plan,rows,m=w.cohorts.dataset(self.stack,self.key);changed=copy.deepcopy(rows)
        for row in changed:
            if row['partition']=='validation':row['labels']['statement']='patient_negated' if row['labels']['statement']=='patient_affirmed' else 'patient_affirmed'
        models=[];paths=[]
        for number,data in enumerate([rows,changed]):
            path=self.root/('math-check-'+str(number));(path/'dataset').mkdir(parents=True)
            (path/'dataset/manifest.json').write_bytes((output/'dataset/manifest.json').read_bytes())
            with patch.object(w.cohorts,'dataset',return_value=(path,plan,data,m)),patch.object(w.local_encoders,'LocalEncoder',Encoder):
                models.append(w.fit(self.stack,self.key))
            paths.append(path/w.DIRECTORY/'weights.npz')
        self.assertEqual(models[0]['calibration'],models[1]['calibration'])
        self.assertEqual(models[0]['heads'],models[1]['heads'])
        with np.load(paths[0]) as a,np.load(paths[1]) as b:
            self.assertEqual(a.files,b.files)
            for key in a.files:np.testing.assert_array_equal(a[key],b[key])


if __name__=='__main__':unittest.main()
