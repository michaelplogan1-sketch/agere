"""Qualification registration integrity and preservation, using received fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import prediction_registry as R
from test_outcome_learning import learning_fixture


class Registry(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.stack=self.root/'stack';self.stack.mkdir();self.data=self.root/'data';self.data.mkdir()
        self.x,self.y,self.policy,self.protected=learning_fixture()
        for y in self.y:y['recorded_label']=y['qualification']['recorded_label']=0
        self.meta={'synthetic':True,'snapshot':{'release':'fixture','as_of':'2026-09-30'},'source_digest':'fixture'}
        R.L.write(self.stack/'run.json',self.meta)
        for name,rows in [('features.jsonl',self.x),('targets.jsonl',self.y)]:
            (self.data/name).write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.manifest={'synthetic':True,'identity':'dataset-id','source_identity':self.meta,
                       'files':{n:R.sha(self.data/n) for n in R.FILES},'profile':{'id':'received_prior_180d'},
                       'outcome_parameters':{'contract':'admission_discharge_source_30d'},
                       'parameters':{'contract':'received_admission_future_discharge_30d'},'contract':{'title':'Fixture landmark'}}
        R.L.write(self.data/'manifest.json',self.manifest)
        for target,value in [(R.L.learning_queue,'lineage'),(R.L.semantic_learning,'protections')]:
            patcher=patch.object(target,value,return_value=(self.policy,{'policy_sha256':'policy'}) if value=='lineage' else self.protected)
            patcher.start();self.addCleanup(patcher.stop)
        self.prep=patch.object(R,'prepare',return_value={'identity':'dataset-id'});self.prepared=self.prep.start();self.addCleanup(self.prep.stop)

    def build(self,adapter='index_day',name='model'):
        return R.build(self.stack,self.root/'lineage',adapter,self.data,self.root/name)

    def test_two_adapters_immutable_ids_and_observational_scope(self):
        a=self.build();b=self.build('receipt_day','landmark');status=R.attach(self.stack,[self.root/'model',self.root/'landmark'])
        self.assertTrue(status['all_current']);self.assertEqual(len(status['reports']),2)
        self.assertEqual({r['status'] for r in status['reports']},{'not_fitted'})
        self.assertFalse(list(self.root.rglob('*.pkl')))
        self.assertFalse(status['clinical_prediction_authorized'])
        self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(R.status(self.stack),status)
        for r in status['reports']:
            self.assertNotIn('lineage_root',r);self.assertNotIn('protected_register',r)
            self.assertNotIn('family',r['source_qualification']['examples']['recorded_0'][0])
            self.assertEqual(sum(d['deficit'] for d in r['class_deficits'] if d['recorded_class']==1),20)
        with self.assertRaisesRegex(ValueError,'Fresh'):self.build()

    def test_invalid_attachment_preserves_existing_pointer(self):
        self.build();R.attach(self.stack,[self.root/'model']);before=(self.stack/R.LINK).read_bytes()
        (self.root/'model/qualification.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'Cannot attach'):R.attach(self.stack,[self.root/'model'])
        self.assertEqual(before,(self.stack/R.LINK).read_bytes())
        r=R.status(self.stack)['reports'][0];self.assertFalse(r['current']);self.assertNotIn('counts',r)

    def test_separate_task_failure_does_not_hide_current_sibling(self):
        self.build();self.build('receipt_day','landmark');R.attach(self.stack,[self.root/'model',self.root/'landmark'])
        (self.root/'landmark/models/run.json').write_text('{}')
        s=R.status(self.stack);self.assertFalse(s['all_current']);self.assertEqual([r['current'] for r in s['reports']],[True,False])

    def test_changed_received_evidence_never_appears_current(self):
        self.build();R.attach(self.stack,[self.root/'model']);self.prepared.return_value={'identity':'new-source'}
        r=R.status(self.stack)['reports'][0];self.assertFalse(r['current']);self.assertIn('evidence changed',r['reason'])

    def test_source_snapshot_and_export_tampering_rejected(self):
        self.build();R.attach(self.stack,[self.root/'model']);p=self.data/'features.jsonl';before=p.read_text();p.write_text(before+'\n')
        self.assertIn('dataset changed',R.status(self.stack)['reports'][0]['reason']);p.write_text(before)
        R.L.write(self.stack/'run.json',{**self.meta,'source_digest':'new'})
        self.assertIn('source snapshot',R.status(self.stack)['reports'][0]['reason'])

    def test_missing_target_file_and_extra_files_rejected(self):
        for files in ({'features.jsonl':self.manifest['files']['features.jsonl']},{**self.manifest['files'],'extra.json':'x'}):
            R.L.write(self.data/'manifest.json',{**self.manifest,'files':files})
            with self.assertRaisesRegex(ValueError,'Exact separate'):self.build()

    def test_protection_and_implementation_changes_unavailable(self):
        self.build();R.attach(self.stack,[self.root/'model']);self.protected['patient_ids'].append('p0')
        self.assertIn('protections changed',R.status(self.stack)['reports'][0]['reason']);self.protected['patient_ids'].clear()
        with patch.object(R,'identity',return_value='changed'):
            self.assertIn('implementation changed',R.status(self.stack)['reports'][0]['reason'])

    def test_nonempty_unique_task_registration(self):
        self.build()
        for roots in ([],[self.root/'model',self.root/'model']):
            with self.assertRaisesRegex(ValueError,'Nonempty unique'):R.attach(self.stack,roots)

    def test_midbuild_source_change_leaves_no_activatable_report(self):
        self.prepared.side_effect=[{'identity':'dataset-id'},{'identity':'new'}]
        with self.assertRaisesRegex(ValueError,'evidence changed'):self.build()
        self.assertFalse((self.root/'model/run.json').exists());self.assertFalse((self.stack/R.LINK).exists())

    def test_unknown_adapter_rejected_and_empty_status_explicit(self):
        self.assertFalse(R.status(self.stack)['available'])
        with self.assertRaisesRegex(ValueError,'Unknown'):self.build('unknown')

    def test_observational_exclusions_and_class_counts_are_separate(self):
        y=copy.deepcopy(self.y);y[0]['qualification'].update(timing_eligible_observed_target=False,exclusion_reasons=['late','unknown'])
        r=R.source_summary(self.x,y)
        self.assertEqual(r['timing_eligible'],239);self.assertEqual(r['timing_eligible_label_counts'],{'0':239})
        self.assertEqual(r['exclusion_reason_counts'],{'late':1,'unknown':1});self.assertTrue(r['exclusion_counts_overlap'])
        self.assertIsNone(r['clinical_label'])

    def test_fitted_fixture_weights_and_predictions_are_verified(self):
        _,targets,_,_=learning_fixture()
        (self.data/'targets.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in targets))
        self.manifest['files']['targets.jsonl']=R.sha(self.data/'targets.jsonl');R.L.write(self.data/'manifest.json',self.manifest)
        self.build();s=R.attach(self.stack,[self.root/'model']);self.assertEqual(s['reports'][0]['status'],'research_comparison_fitted')
        weights=self.root/'model/models/logistic.pkl';old=weights.read_bytes();weights.write_bytes(b'changed')
        self.assertIn('weights changed',R.status(self.stack)['reports'][0]['reason']);weights.write_bytes(old)
        (self.root/'model/models/development-predictions.json').write_text('[]')
        self.assertIn('results changed',R.status(self.stack)['reports'][0]['reason'])

    def test_concurrent_dataset_mutation_rejected(self):
        def mutate(*args):
            p=self.data/'targets.jsonl';p.write_text(p.read_text()+'\n');return {'identity':'dataset-id'}
        self.prepared.side_effect=mutate
        with self.assertRaisesRegex(ValueError,'during verification'):self.build()


if __name__=='__main__':unittest.main()
