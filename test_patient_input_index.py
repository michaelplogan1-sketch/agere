import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.clinical import patient_input_index as index


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name);self.db=sqlite3.connect(self.path/'stack.sqlite3')
        self.db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT); CREATE INDEX patient_records ON records(patient); CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT, PRIMARY KEY(patient,operator));')
        self.db.executemany('INSERT INTO patients VALUES (?,?)',[('a','{"name":"A"}'),('b','{"name":"B"}')])
        self.db.execute('INSERT INTO records VALUES (?,?,?,?)',('r','a','labs','{"value":5}'))
        self.db.execute('INSERT INTO analyses VALUES (?,?,?)',('a','heart_failure_review','{"result":true}'));self.db.commit()
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def run_index(self):self.db.commit();return index.snapshot(self.path)
    def test_exact_original_protocol_hash_and_no_rehash_on_repeat(self):
        first=self.run_index();r=first['patients']['a']
        self.assertEqual(r['protocol_hash'],digest([digest('{"name":"A"}'),hashlib.sha256(digest('{"value":5}').encode()).hexdigest(),[['heart_failure_review',digest('{"result":true}')]]]))
        again=self.run_index();self.assertEqual(first['identity'],again['identity']);self.assertEqual(again['processing']['record_payloads_hashed'],0)
        self.assertEqual(again['processing']['patient_cache_hits'],2)
    def test_insert_update_delete_and_move_dirty_exact_patients(self):
        previous=self.run_index()
        for command,expected in [("UPDATE records SET payload='changed' WHERE id='r'",1),("UPDATE records SET patient='b' WHERE id='r'",2),("DELETE FROM records WHERE id='r'",1),("INSERT INTO records VALUES ('r2','a','notes','new')",1)]:
            self.db.execute(command);current=self.run_index();self.assertEqual(current['processing']['patients_recomputed'],expected)
            self.assertNotEqual(previous['identity'],current['identity']);previous=current
    def test_patient_and_relevant_analysis_changes_invalidate(self):
        self.run_index();self.db.execute("UPDATE patients SET payload='changed' WHERE id='b'");self.assertEqual(self.run_index()['processing']['patients_recomputed'],1)
        self.db.execute("INSERT INTO analyses VALUES ('b','source_completeness','irrelevant')");self.assertEqual(self.run_index()['processing']['patients_recomputed'],0)
        self.db.execute("UPDATE analyses SET operator='affordability_review' WHERE patient='b'");self.assertEqual(self.run_index()['processing']['patients_recomputed'],1)
        self.db.execute("DELETE FROM analyses WHERE patient='a'");self.assertEqual(self.run_index()['processing']['patients_recomputed'],1)
    def test_replace_moving_existing_record_invalidates_old_and_new_patient(self):
        self.run_index()
        self.db.execute("INSERT OR REPLACE INTO records VALUES ('r','b','labs','replacement')")
        result=self.run_index()
        self.assertEqual(result['processing']['patients_recomputed'],2)
        self.assertEqual(result['patients']['a']['record_count'],0)
        self.assertEqual(result['patients']['b']['record_count'],1)
    def test_missing_trigger_causes_full_rebuild_and_catches_untracked_change(self):
        before=self.run_index();self.db.execute('DROP TRIGGER agere_inputs_records_update');self.db.execute("UPDATE records SET payload='untracked' WHERE id='r'")
        after=self.run_index();self.assertTrue(after['processing']['trigger_set_reinstalled']);self.assertNotEqual(before['identity'],after['identity']);self.assertEqual(after['processing']['patients_recomputed'],2)
    def test_orphan_source_rejected_and_deleted_empty_patient_removed(self):
        self.run_index();self.db.execute("DELETE FROM patients WHERE id='a'");self.db.commit()
        with self.assertRaisesRegex(ValueError,'absent from roster'):self.run_index()
        self.db.execute("DELETE FROM records WHERE patient='a'");self.db.execute("DELETE FROM analyses WHERE patient='a'");self.assertEqual(set(self.run_index()['patients']),{'b'})
    def test_cache_corruption_is_rejected(self):
        self.run_index();self.db.execute("UPDATE agere_inputs_cache SET payload='{}' WHERE patient='a'")
        with self.assertRaisesRegex(ValueError,'checksum'):self.run_index()
    def test_rollback_does_not_invalidate_committed_input(self):
        before=self.run_index();self.db.execute("UPDATE records SET payload='rollback' WHERE id='r'");self.db.rollback()
        after=self.run_index();self.assertEqual(before['identity'],after['identity']);self.assertEqual(after['processing']['patients_recomputed'],0)

if __name__=='__main__':unittest.main()
