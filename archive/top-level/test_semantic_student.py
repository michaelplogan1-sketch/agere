"""Learning isolation, exact serialization, refusal and shadow-only serving."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.special import softmax
from sklearn.linear_model import LogisticRegression

from clinical_core.agere.clinical import semantic_student as st
from clinical_core.agere.clinical import semantic_curriculum as curriculum
from clinical_core.agere.clinical import semantic_reader as reader


def small_rows():
    # Two contrasting recipes in each partition, three entities each.
    return [r for r in curriculum.build() if r['patient_family'].split(':')[-1] in ('2','21') and r['input']['target_quote'] in ('metoprolol','carvedilol','metformin','difficulty affording medications','financial strain','trouble paying medical bills')]


class StudentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.path=Path(cls.tmp.name)/'student'
        cls.rows=small_rows()
        cls.manifest=st.fit(cls.rows,cls.path,kind='lexical',supervision='authored_synthetic_development',provenance={'fixture':True})
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_curriculum_preserves_all_labels_and_partition_lineage(self):
        rows=curriculum.build();st.validate_rows(rows)
        for axis in reader.AXES:self.assertEqual({r['labels'][axis] for r in rows if r['partition']=='train'},set(reader.AXES[axis]))
        self.assertTrue(all('not clinician-reviewed' in r['supervision'] for r in rows))

    def test_leaking_template_or_input_rejected(self):
        rows=copy.deepcopy(self.rows);a=next(r for r in rows if r['partition']=='train');b=next(r for r in rows if r['partition']=='validation')
        b['source_families']=a['source_families']
        with self.assertRaisesRegex(ValueError,'cross partitions'):st.validate_rows(rows)

    def test_features_exclude_supervision_and_partition(self):
        p=self.rows[0]['input'];text=st.model_text({**p,'labels':{'fake':'SECRET'},'partition':'SECRET','patient_id':'SECRET'})
        self.assertNotIn('SECRET',text);self.assertIn('[TARGET]',text)

    def test_incomplete_invalid_and_overlength_context_refused(self):
        for changes in ({'context_complete':False},{'target_start':True},{'target_quote':'different'},{'synthetic':False}):
            with self.assertRaises(ValueError):st.model_text({**self.rows[0]['input'],**changes})
        p=copy.deepcopy(self.rows[0]['input']);p['text']+='x'*13000
        with self.assertRaisesRegex(ValueError,'no truncation'):st.model_text(p)

    def test_binary_logits_match_sklearn_probability(self):
        x=np.array([[1.,2.],[2.,3.],[-2.,-1.],[-1.,-3.]])
        m=LogisticRegression().fit(x,['yes','yes','no','no'])
        np.testing.assert_allclose(softmax(st.logits(x,m.coef_,m.intercept_,list(m.classes_)),axis=1),m.predict_proba(x))

    def test_serialized_prediction_matches_saved_validation(self):
        model=st.Student(self.path)
        try:
            records=[json.loads(l) for l in (self.path/'validation-predictions.jsonl').read_text().splitlines()]
            for row,saved in zip([r for r in self.rows if r['partition']=='validation'],records):
                actual=model.predict(row['input']);self.assertEqual(actual['labels'],saved['labels'])
                for axis in reader.AXES:np.testing.assert_allclose(list(actual['probabilities'][axis].values()),[saved['probabilities'][axis][c] for c in reader.AXES[axis]],atol=1e-12)
        finally:model.close()

    def test_validation_labels_cannot_change_weights_or_calibration(self):
        rows=copy.deepcopy(self.rows)
        for r in rows:
            if r['partition']=='validation':r['labels']={a:'unknown' for a in reader.AXES}
        out=Path(self.tmp.name)/'changed-validation'
        second=st.fit(rows,out,kind='lexical',supervision='authored_synthetic_development',provenance={'fixture':True})
        with np.load(self.path/'weights.npz',allow_pickle=False) as a,np.load(out/'weights.npz',allow_pickle=False) as b:
            for key in a.files:np.testing.assert_array_equal(a[key],b[key])
        self.assertEqual(self.manifest['heads'],second['heads']);self.assertEqual(self.manifest['calibration'],second['calibration'])
        self.assertNotEqual(self.manifest['validation'],second['validation'])

    def test_sparse_calibration_cannot_accept_and_unknown_is_unselectable(self):
        self.assertIsNone(self.manifest['calibration']['threshold'])
        self.assertFalse(st.selectable({'labels':{'statement':'unknown','time':'current_at_source','relation':'reported_use'}}))

    def test_development_never_promoted_and_missing_classes_reported(self):
        self.assertFalse(self.manifest['release']['automatic_acceptance'])
        self.assertEqual(self.manifest['release']['status'],'shadow_only')
        self.assertTrue(any(self.manifest['unrepresented_training_classes'].values()))
        self.assertFalse(self.manifest['foundation_weights_updated'])

    def test_corrupt_weight_file_rejected_before_loading(self):
        import shutil
        out=Path(self.tmp.name)/'corrupt';shutil.copytree(self.path,out)
        with (out/'weights.npz').open('ab') as f:f.write(b'corruption')
        with self.assertRaisesRegex(ValueError,'checksum'):st.Student(out)

    def test_source_bound_serving_cache_and_no_fabricated_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);record={'record_id':'n','patient_id':'p','source_table':'notes','identity_status':'matched','synthetic':True,
                'event_date':'2026-09-01','available_date':'2026-09-02','text':'Today the patient confirms taking metoprolol every day.'}
            (path/'run.json').write_text(json.dumps({'synthetic':True,'source_digest':'fixture','snapshot':{'as_of':'2026-09-30'}}))
            db=sqlite3.connect(path/'stack.sqlite3');db.execute('CREATE TABLE records(id TEXT,patient TEXT,payload TEXT)');db.execute('INSERT INTO records VALUES (?,?,?)',('n','p',json.dumps(record)));db.commit();db.close()
            a=record['text'].index('metoprolol');target={'patient_id':'p','record_id':'n','field':'text','start':a,'end':a+10,'concept':'medication bb'}
            with patch.object(st,'linked',return_value=(self.path,self.manifest,st.sha(self.path/'manifest.json'))):
                first=st.read(path,target,'student_lexical');replay=st.read(path,target,'student_lexical')
                self.assertFalse(first['cache_hit']);self.assertTrue(replay['cache_hit']);self.assertEqual(first['id'],replay['id'])
                self.assertFalse(first['reading']['automatic_acceptance']);self.assertEqual(first['usage']['hosted_tokens'],0)
                self.assertEqual(reader.inspect(path,first['id'])['reviews'],[])
                self.assertFalse(reader.learning_export(path)['rows'])
                other=st.read(path,{**target,'concept':'drug report'},'student_lexical');self.assertNotEqual(first['id'],other['id'])

    def test_reviewed_model_rechecks_frozen_reviews_on_use(self):
        m={**self.manifest,'supervision':'source_reviewed_snapshot','provenance':{'reviewed_dataset':str(self.path),'snapshot_sha256':st.sha(self.path/'manifest.json')}}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'semantic-student-link.json').write_text(json.dumps({'student_lexical':{'path':str(self.path),'manifest_sha256':st.sha(self.path/'manifest.json')}}))
            with patch.object(st,'verify',return_value=m),patch('clinical_core.agere.clinical.semantic_learning.load_snapshot',side_effect=ValueError('Reviews changed')):
                with self.assertRaisesRegex(ValueError,'Reviews changed'):st.linked(tmp,'student_lexical')


if __name__=='__main__':unittest.main()
