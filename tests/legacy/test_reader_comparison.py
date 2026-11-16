import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import reader_comparison as C


class ReaderComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.stack = self.tmp.name
        self.packet = {'synthetic': True, 'concept': 'appointment', 'text': 'Requested an appointment.',
                       'target_start': 13, 'target_end': 24, 'target_quote': 'appointment', 'context_complete': True}
        self.target = {'patient_id': 'P-1', 'record_id': 'R-1', 'field': 'text', 'start': 13, 'end': 24, 'concept': 'appointment'}
        self.labels = {'statement': 'patient_affirmed', 'time': 'planned', 'relation': 'care_plan'}
        self.rows = [{'partition': p, 'labels': self.labels, 'patient_id': 'P-2', 'source_hash': 'other', 'input': self.packet} for p in ('train', 'calibration', 'validation')]
        self.request = {'target': self.target, 'cohort_id': 'a'*64, 'roles': ['cohort_lexical', 'cohort_windowed_minilm']}
        self.fixed = {'schema': C.VERSION, 'implementation': 'test', 'request': self.request, 'source_hash': 'source',
                      'source_identity': {'source_digest': 'warehouse'}, 'context': self.packet, 'context_digest': digest(self.packet),
                      'models': {r: {'model_identity': r} for r in self.request['roles']}, 'cohort_dataset_digest': digest(self.rows)}
        self.receipts = {r: {'id': r, 'target': self.target, 'source_hash': 'source', 'source_identity': self.fixed['source_identity'],
                            'context': self.packet, 'model_identity': r, 'provider': r, 'reading': {'labels': self.labels,
                            'automatic_acceptance': False, 'clinical_authority': False}, 'usage': {}, 'cache_hit': False} for r in self.request['roles']}

    def execution(self):
        return (patch.object(C, 'prepare_inputs', return_value=(self.fixed, self.rows)),
                patch.object(C, 'run_role', side_effect=lambda s,q,r:self.receipts[r]),
                patch.object(C.R, 'inspect', side_effect=lambda s,k:self.receipts[k]))

    def test_persist_replay_without_inference(self):
        a,b,c = self.execution()
        with a,b as inference,c:
            first = C.prepare(self.stack, self.request); second = C.prepare(self.stack, self.request)
            self.assertEqual(inference.call_count, 2)
            self.assertTrue(second['cache_hit']); self.assertEqual(first['comparison'], second['comparison'])
            self.assertFalse(first['comparison']['human_review_created'])
            self.assertEqual(first['comparison']['stage_assessment']['planning'], 'not_run')

    def test_failure_persisted_and_not_retried_on_replay(self):
        a,b,c = self.execution()
        with a,patch.object(C, 'run_role', side_effect=RuntimeError('failed local runtime')) as infer,c:
            first=C.prepare(self.stack,self.request);again=C.prepare(self.stack,self.request)
            self.assertEqual(infer.call_count,2);self.assertTrue(again['cache_hit'])
            self.assertEqual(len(first['comparison']['stage_assessment']['failed_roles']),2)

    def test_exact_reading_rejects_cross_patient(self):
        r=deepcopy(self.receipts['cohort_lexical']);r['target']['patient_id']='other'
        with patch.object(C.R,'inspect',return_value=r),self.assertRaises(ValueError):C.checked_reading(self.stack,'x',self.fixed,'cohort_lexical')

    def test_exact_reading_rejects_changed_context(self):
        r=deepcopy(self.receipts['cohort_lexical']);r['context']['text']='Changed'
        with patch.object(C.R,'inspect',return_value=r),self.assertRaises(ValueError):C.checked_reading(self.stack,'x',self.fixed,'cohort_lexical')

    def test_exact_reading_rejects_wrong_model_and_role(self):
        for field in ('model_identity','provider','source_hash','source_identity'):
            with self.subTest(field=field):
                r=deepcopy(self.receipts['cohort_lexical']);r[field]='wrong'
                with patch.object(C.R,'inspect',return_value=r),self.assertRaises(ValueError):C.checked_reading(self.stack,'x',self.fixed,'cohort_lexical')

    def test_reject_automatic_authority(self):
        for field in ('automatic_acceptance','clinical_authority'):
            r=deepcopy(self.receipts['cohort_lexical']);r['reading'][field]=True
            with patch.object(C.R,'inspect',return_value=r),self.assertRaises(ValueError):C.checked_reading(self.stack,'x',self.fixed,'cohort_lexical')

    def test_label_contract(self):
        r=deepcopy(self.receipts['cohort_lexical']);r['reading']['labels']['relation']='covered_by_insurance'
        with patch.object(C.R,'inspect',return_value=r),self.assertRaises(ValueError):C.checked_reading(self.stack,'x',self.fixed,'cohort_lexical')

    def test_changed_source_during_execution_not_saved(self):
        _,b,c=self.execution();changed={**self.fixed,'source_hash':'new'}
        with patch.object(C,'prepare_inputs',side_effect=[(self.fixed,self.rows),(changed,self.rows)]),b,c,self.assertRaises(ValueError):C.prepare(self.stack,self.request)
        db=C.db(self.stack);self.assertEqual(db.execute('SELECT COUNT(*) FROM comparisons').fetchone()[0],0);db.close()

    def test_inspect_rejects_stale_identity(self):
        a,b,c=self.execution()
        with a,b,c:result=C.prepare(self.stack,self.request)['comparison']
        with patch.object(C,'prepare_inputs',return_value=({**self.fixed,'implementation':'new'},self.rows)),self.assertRaises(ValueError):C.inspect(self.stack,{'id':result['id']})

    def test_comparisons_are_immutable(self):
        db=C.db(self.stack);db.execute('INSERT INTO comparisons VALUES (?,?)',('x','{}'));db.commit()
        for sql in ('UPDATE comparisons SET payload="changed"','DELETE FROM comparisons'):
            with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
        db.close()

    def test_unknown_comparison(self):
        with self.assertRaises(ValueError):C.inspect(self.stack,{'id':'b'*64})

    def test_invalid_id(self):
        for body in ({'id':'bad'},{'id':7},{'id':'a'*64,'target':'changed'}):
            with self.assertRaises(ValueError):C.inspect(self.stack,body)

    def test_invalid_request_and_remote_roles_rejected_before_reading(self):
        for body in ({}, {**self.request,'extra':True}, {**self.request,'roles':[]}, {**self.request,'roles':['jev']},
                     {**self.request,'roles':['kev','kev']}, {**self.request,'roles':[{}]}, {**self.request,'cohort_id':'../secret'}):
            with patch.object(C.R,'source') as src,self.assertRaises(ValueError):C.prepare_inputs(self.stack,body)
            src.assert_not_called()

    def test_partial_shared_context_rejected_before_models(self):
        with patch.object(C.R,'source',return_value=({},'text',{'snapshot':{'as_of':'2029-01-01'}})),patch.object(C.R,'packet',return_value={**self.packet,'context_complete':False}),patch.object(C,'model_specs') as models,self.assertRaises(ValueError):C.prepare_inputs(self.stack,self.request)
        models.assert_not_called()

    def test_roles_canonicalized(self):
        meta={'snapshot':{'as_of':'2029-01-01'},'source_digest':'digest'}
        with patch.object(C,'identity',return_value='test'),patch.object(C.R,'source',return_value=({},'text',meta)),patch.object(C.R,'packet',return_value=self.packet),patch.object(C,'model_specs',return_value=({},self.rows)):
            one=C.prepare_inputs(self.stack,self.request)[0];two=C.prepare_inputs(self.stack,{**self.request,'roles':list(reversed(self.request['roles']))})[0]
        self.assertEqual(one,two)

    def test_majority_uses_training_only(self):
        rows=deepcopy(self.rows);rows[1]['labels']={k:'unknown' for k in self.labels};rows[2]['labels']=rows[1]['labels']
        self.assertEqual(C.baselines(rows)['training_majority']['labels'],self.labels)
        with self.assertRaises(ValueError):C.baselines(rows[1:])

    def test_abstention_distinct_from_nonabstaining_disagreement(self):
        variants={'a':{'status':'completed','labels':self.labels},'b':{'status':'completed','labels':{k:'unknown' for k in self.labels}},'c':{'status':'failed'}}
        assessment=C.assess(variants)
        self.assertTrue(assessment['axes']['relation']['disagreement']);self.assertFalse(assessment['axes']['relation']['nonabstaining_disagreement'])
        self.assertEqual(assessment['axes']['relation']['abstaining_roles'],['b']);self.assertEqual(assessment['failed_roles'],['c'])

    def test_overlap_separated_by_partition(self):
        rows=deepcopy(self.rows);rows[0]['patient_id']='P-1';rows[1]['source_hash']='source'
        result=C.overlap(self.target,self.packet,'source',rows)
        self.assertEqual(result['train']['same_patient'],1);self.assertEqual(result['calibration']['same_source'],1)
        self.assertEqual(result['validation']['same_patient'],0);self.assertEqual(result['validation']['same_model_text'],1)


if __name__=='__main__':unittest.main()
