import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import original_source_lineage as lineage


class OriginalSourceLineage(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.release=root/'release';self.release.mkdir();self.stack=root/'stack';self.stack.mkdir();self.out=root/'policy'
        db=sqlite3.connect(self.release/'warehouse.sqlite');db.execute('CREATE TABLE patients(patient_id TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO patients VALUES (?)',[('p1',),('p2',),('p3',)]);db.commit();db.close()
        db=sqlite3.connect(self.stack/'stack.sqlite3');db.execute('CREATE TABLE patients(id TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO patients VALUES (?)',[('p1',),('p2',),('p3',)]);db.commit();db.close()
        sha=lineage.checksum(self.release/'warehouse.sqlite')
        (self.release/'manifest.json').write_text(json.dumps({'schema':'single-healthcare-warehouse/1','population':'same_original_patient_roster',
            'metrics':{'original_patient_id_set_preserved':True},'files':{'warehouse.sqlite':sha}}))
        (self.stack/'run.json').write_text(json.dumps({'synthetic':True,'snapshot':{'release':'release','source_dataset':{'adapter':lineage.ADAPTER,
            'source':str(self.release/'warehouse.sqlite'),'source_sha256':sha,'original_patient_id_digest':digest(['p1','p2','p3'])}}}))
        self.guard=patch.object(lineage.learning,'protections',return_value={'patient_ids':['variant'],'family_ids':['p1'],'register_sha256':'protected'});self.guard.start()
    def tearDown(self):self.guard.stop();self.tmp.cleanup()

    def test_public_identity_only_and_frozen_family_protection(self):
        receipt=lineage.prepare(self.stack,self.release,self.out)
        policy=json.loads((self.out/'policy.json').read_text())
        self.assertEqual(policy['patient_families'],{p:p for p in ('p1','p2','p3')})
        self.assertEqual(receipt['frozen_original_families_excluded'],1)
        self.assertFalse(receipt['private_files_read']);self.assertEqual(receipt['extracted_fields'],['patients.patient_id'])
        self.assertEqual(sum(receipt['unprotected_partition_patient_counts'].values()),2)
        self.assertEqual(lineage.partition('p1'),lineage.partition('p1'))

    def test_roster_or_source_change_blocks_policy(self):
        db=sqlite3.connect(self.stack/'stack.sqlite3');db.execute('DELETE FROM patients WHERE id=?',('p3',));db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'roster'):lineage.prepare(self.stack,self.release,self.out)
        self.assertFalse(self.out.exists())

    def test_unresolvable_frozen_family_is_not_silently_ignored(self):
        with patch.object(lineage.learning,'protections',return_value={'patient_ids':[],'family_ids':['missing'],'register_sha256':'protected'}):
            with self.assertRaisesRegex(ValueError,'Frozen source families'):lineage.prepare(self.stack,self.release,self.out)


if __name__=='__main__':unittest.main()
