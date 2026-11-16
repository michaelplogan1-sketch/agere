"""Training-data boundaries; examples are isolated authored fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.contracts import digest
from clinical_core.agere.clinical import semantic_learning as learning

PROTECTED={'patient_ids':['reserved-patient'],'family_ids':['reserved-family'],'register_sha256':'fixture'}


def example(patient, text='Patient reports financial difficulty.', **labels):
    target={'patient_id':patient,'record_id':patient+'-record','field':'text','start':0,'end':len(text),'concept':'financial hardship'}
    return {'reading_id':patient,'target':target,'source_hash':digest([patient,text]),
        'context':{'text':text,'target_start':0,'target_end':len(text),'target_quote':text,'concept':'financial hardship','context_complete':True},
        'labels':{'statement':'patient_affirmed','time':'current_at_source','relation':'clinical_observation',**labels},
        'review':{'revision':1,'status':'accepted'},'source_family':digest(text)}


def policy(rows,part='train'):
    return {'lineage_source':'Isolated synthetic test map','patient_families':{r['target']['patient_id']:'family-'+r['target']['patient_id'] for r in rows},
        'family_partitions':{'family-'+r['target']['patient_id']:part for r in rows},'additional_protected_families':[]}


def export(rows):return {'synthetic':True,'rows':rows,'excluded':[]}


class LearningTests(unittest.TestCase):
    def test_no_labels_not_ready_no_automatic_training(self):
        r=learning.prepare(export([]),policy([]),PROTECTED)
        self.assertFalse(r['fit_input_contract_ready']);self.assertFalse(r['training_performed'])

    def test_same_occurrence_across_readers_is_one_example(self):
        a=example('a');b=copy.deepcopy(a);b['reading_id']='b'
        r=learning.prepare(export([a,b]),policy([a]),PROTECTED)
        self.assertEqual(len(r['rows']),1);self.assertEqual(len(r['rows'][0]['reviews']),2)
        self.assertNotIn('patient_id',r['rows'][0]['input'])

    def test_missing_lineage_is_excluded_not_guessed(self):
        r=learning.prepare(export([example('a')]),policy([]),PROTECTED)
        self.assertFalse(r['rows']);self.assertEqual(r['excluded'][0]['reason'],'missing_verified_patient_family_lineage')

    def test_reserved_patient_family_and_additional_protection_excluded(self):
        a,b,c=example('reserved-patient'),example('b'),example('c');p=policy([a,b,c])
        p['patient_families']['b']='reserved-family';p['additional_protected_families']=['family-c']
        r=learning.prepare(export([a,b,c]),p,PROTECTED)
        self.assertFalse(r['rows']);self.assertTrue(all(x['reason']=='protected_evaluation_family' for x in r['excluded']))

    def test_conflicting_labels_are_not_majority_voted(self):
        a=example('a');b=copy.deepcopy(a);b['reading_id']='b';b['labels']['statement']='patient_negated'
        r=learning.prepare(export([a,b]),policy([a]),PROTECTED)
        self.assertFalse(r['rows']);self.assertEqual(r['excluded'][0]['reason'],'conflicting_labels_of_same_occurrence')

    def test_repeated_templates_may_not_cross_partitions(self):
        rows=[example('a'),example('b')];p=policy(rows);p['family_partitions']['family-b']='validation'
        with self.assertRaisesRegex(ValueError,'crosses'):learning.prepare(export(rows),p,PROTECTED)

    def test_transitive_family_and_template_links_may_not_cross_partitions(self):
        a=example('a','Report one.');b=example('b','Report two.');c=example('c','Report two.')
        p=policy([a,b,c]);p['patient_families']['b']='family-a';p['family_partitions']['family-c']='validation'
        with self.assertRaisesRegex(ValueError,'crosses'):learning.prepare(export([a,b,c]),p,PROTECTED)

    def test_identical_inputs_with_conflicting_labels_rejected(self):
        rows=[example('a'),example('b',statement='patient_negated')]
        with self.assertRaisesRegex(ValueError,'conflicting reviewed'):learning.prepare(export(rows),policy(rows),PROTECTED)

    def test_corrupt_span_rejected_and_partial_context_excluded(self):
        a=example('a');a['context']['target_quote']='different'
        with self.assertRaises(ValueError):learning.prepare(export([a]),policy([a]),PROTECTED)
        a=example('a');a['context']['context_complete']=False
        self.assertFalse(learning.prepare(export([a]),policy([a]),PROTECTED)['rows'])

    def test_snapshot_hashes_immutable_and_fit_rejects_incomplete_data(self):
        rows=[example('a')];p=policy(rows)
        with tempfile.TemporaryDirectory() as tmp, patch.object(learning.reader,'learning_export',return_value=export(rows)),patch.object(learning,'protections',return_value=PROTECTED):
            out=Path(tmp)/'frozen';r=learning.freeze(tmp,out,p)
            self.assertEqual(set(r['files']),{'train.jsonl','calibration.jsonl','validation.jsonl'})
            with self.assertRaises(FileExistsError):learning.freeze(tmp,out,p)
            with self.assertRaisesRegex(ValueError,'not ready'):learning.load_fit(tmp,out)
            (out/'train.jsonl').write_text('changed')
            with self.assertRaisesRegex(ValueError,'checksum'):learning.load_fit(tmp,out)

    def test_revoked_reviews_block_fit_and_validation_rows_never_returned(self):
        rows=[example('a','Use report.'),example('b','Historical plan.',statement='patient_negated',time='historical',relation='care_plan'),
              example('c','Distinct calibration.'),example('d','Distinct validation.')]
        p=policy(rows);p['family_partitions'].update({'family-c':'calibration','family-d':'validation'})
        with tempfile.TemporaryDirectory() as tmp, patch.object(learning.reader,'learning_export',return_value=export(rows)) as get,patch.object(learning,'protections',return_value=PROTECTED):
            out=Path(tmp)/'frozen';r=learning.freeze(tmp,out,p)
            self.assertTrue(r['fit_input_contract_ready'])
            self.assertEqual({r['patient_id'] for r in learning.load_fit(tmp,out)},{'a','b'})
            original=(out/'train.jsonl').read_text();corrupt=json.loads(original.splitlines()[0]);corrupt['labels']['time']='planned'
            lines=original.splitlines();lines[0]=json.dumps(corrupt);(out/'train.jsonl').write_text('\n'.join(lines)+'\n')
            manifest=json.loads((out/'manifest.json').read_text());manifest['files']['train.jsonl']=learning.hashlib.sha256((out/'train.jsonl').read_bytes()).hexdigest()
            (out/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'differ from verified'):learning.load_fit(tmp,out)
            (out/'train.jsonl').write_text(original);manifest['files']['train.jsonl']=learning.hashlib.sha256((out/'train.jsonl').read_bytes()).hexdigest();(out/'manifest.json').write_text(json.dumps(manifest))
            get.return_value=export(rows[1:])
            with self.assertRaisesRegex(ValueError,'changed'):learning.load_fit(tmp,out)


if __name__=='__main__':unittest.main()
