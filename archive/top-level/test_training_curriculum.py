"""New public-reference snapshots and authored curricula, with no external calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools.training import curriculum as Q,public_references as P,adapter_tasks as T,adapters as D


class Curriculum(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.path=Path(temp.name)
        for name in ('socket.socket.connect','socket.create_connection','urllib.request.urlopen','subprocess.Popen'):
            p=patch(name,side_effect=AssertionError('No providers or downloads in curriculum tests'));p.start();self.addCleanup(p.stop)

    def test_public_snapshot_has_raw_provenance_and_never_claims_target_doses(self):
        def fetch(url):return json.dumps([1,['Example (Oral Pill)'],{'STRENGTHS_AND_FORMS':[['5 mg Tab','10 mg Tab']],'RXCUIS':[['111','112']]},[['Example (Oral Pill)']]]).encode()
        result=P.snapshot(['example'],self.path/'reference',fetch=fetch)
        self.assertEqual(result['records'],2);self.assertFalse(result['scope']['entire_vocabulary'])
        self.assertIn('not max dose',result['meaning'])
        with self.assertRaisesRegex(ValueError,'preserve prior'):P.snapshot(['example'],self.path/'reference',fetch=fetch)
        with self.assertRaisesRegex(ValueError,'public drug-name'):P.snapshot(['patient=123'],self.path/'bad',fetch=fetch)

    def test_family_separated_curriculum_executes_native_targets_against_both_source_fixtures(self):
        manifest,build=Q.build(self.path/'curriculum',count=45)
        spec,by,_=T.load(manifest)
        family_sets=[{r['family'] for r in rows} for rows in by.values()]
        self.assertFalse(family_sets[0]&family_sets[1]);self.assertFalse(family_sets[0]&family_sets[2]);self.assertFalse(family_sets[1]&family_sets[2])
        result=Q.verify(manifest,self.path/'verified.json')
        self.assertEqual(result['cases'],45);self.assertEqual(result['acceptable'],45);self.assertEqual(result['source_variants'],1)
        self.assertEqual(result['provider_calls'],0);self.assertFalse(result['clinical_qualification'])

    def test_explicit_empty_or_foreign_task_population_does_not_become_all_patients(self):
        manifest,_=Q.build(self.path/'curriculum',count=15);ctx=T.Context(manifest.parent/'source')
        result=ctx.query('SELECT COUNT(*) AS patients FROM patient',[])
        self.assertEqual(result['rows'],[{'patients':0}])
        with self.assertRaisesRegex(ValueError,'unreceived'):ctx.query('SELECT COUNT(*) FROM patient',['NOT-RECEIVED'])

    def test_sequence_length_expands_without_a_hidden_truncation(self):
        self.assertEqual(D.sequence_length([100,1537]),(1537,1568))
        with self.assertRaisesRegex(ValueError,'would truncate'):D.sequence_length([100,1537],1536)

    def test_equivalent_on_one_source_is_not_enough_when_variant_exposes_a_constant_answer(self):
        manifest,_=Q.build(self.path/'curriculum',count=15);ctx=T.Context(manifest.parent/'source');variant=T.Context(manifest.parent/'source-variant')
        row={'question':'Count patients','acceptance':{'returns':'count','columns':{'patients':{'unit':'count'}}},'reference_sql':'SELECT COUNT(*) AS patients FROM patient'}
        row.update(family='constant-answer-negative',target='SELECT 24 AS patients')
        self.assertTrue(ctx.assess('bottled',row,row['target'])['acceptable'])
        self.assertFalse(variant.assess('bottled',row,row['target'])['acceptable'])


if __name__=='__main__':unittest.main()
