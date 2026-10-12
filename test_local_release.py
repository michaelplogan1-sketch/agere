"""Recovery faults use independent tiny SQLite builds; no live records or models."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from clinical_core.agere import local_release as L


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.build = self.root / 'active'; self.build.mkdir()
        self.config = self.root / 'config.json'
        snapshot = {'source_dataset': {'original_patient_id_digest': 'roster'}, 'as_of': '2029-08-15'}
        def save(name, data):
            p = self.build / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(data))
        save('connection.json', {'status': 'connected', 'snapshot': snapshot})
        save('clinical/run.json', {'cache_key_digest': 'source'})
        save('annotations/run.json', {'schema': 'factorized-reader-run/2', 'synthetic': True, 'source_run': 'source', 'model_identity': 'model'})
        save('evidence/run.json', {'schema': 'evidence-query/1', 'synthetic': True, 'source_run': 'source', 'model_identity': 'model'})
        save('stack/run.json', {'snapshot': snapshot, 'source_run': 'source'})
        for name, directory in zip(L.INTERNAL_LINKS, ('evidence', 'clinical-state', 'longitudinal')):
            (self.build / directory).mkdir(exist_ok=True)
            save('stack/' + name, {'path': str(self.build / directory)})
        for name in ('warehouse.sqlite3', 'stack/stack.sqlite3', 'stack/work.sqlite3'):
            db = sqlite3.connect(self.build / name)
            db.execute('create table records(id text primary key, value text)')
            db.execute("insert into records values('original', 'preserved')"); db.commit(); db.close()
        self.config.write_text(json.dumps({'schema': 'active-healthcare-warehouse/1', 'synthetic': True, 'build': str(self.build), 'snapshot': snapshot}))
        self.dep = self.root / 'external'; self.dep.mkdir(); (self.dep / 'run.json').write_text('{"version":1}')
        self.deps = [{'path': str(self.dep), 'verification': 'top_level_json_manifests', 'files': {'run.json': L.file_entry(self.dep / 'run.json')}}]
        p = patch.object(L, 'dependencies', return_value=self.deps); p.start(); self.addCleanup(p.stop)
        p = patch.object(L, 'code_identity', return_value={'engine': 'pinned'}); p.start(); self.addCleanup(p.stop)
        self.cp = self.root / 'checkpoint'

    def make(self): return L.checkpoint(self.config, self.cp)

    def test_restore_exact_data_rebind_and_independent_writes(self):
        result = self.make(); self.assertEqual(result['databases'], 3)
        out = self.root / 'restored'; L.restore(self.cp, out)
        c = L.validate_config(out / 'configuration.json'); self.assertEqual(c['recovery']['mode'], 'inspection_only')
        for name in L.INTERNAL_LINKS:
            self.assertTrue(Path(L.read(out / 'build/stack' / name)['path']).is_relative_to(out))
            self.assertTrue(Path(L.read(self.build / 'stack' / name)['path']).is_relative_to(self.build))
        db = sqlite3.connect(out / 'build/stack/work.sqlite3')
        self.assertEqual(db.execute('select * from records').fetchall(), [('original', 'preserved')])
        db.execute("update records set value='copy'"); db.commit(); db.close()
        db = sqlite3.connect(self.build / 'stack/work.sqlite3')
        self.assertEqual(db.execute('select value from records').fetchone()[0], 'preserved'); db.close()

    def test_active_lock_refuses_checkpoint(self):
        with L.build_lock(self.build):
            with self.assertRaisesRegex(ValueError, 'in use'): self.make()
        self.assertFalse(self.cp.exists())

    def test_source_mutation_during_copy_never_publishes(self):
        original = L.copy_file
        def mutate(s, d):
            original(s, d); (self.build / 'new.txt').write_text('concurrent')
        with patch.object(L, 'copy_file', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'Source changed'): self.make()
        self.assertFalse(self.cp.exists())
        self.assertTrue(list(self.root.glob('.checkpoint.partial-*')))

    def test_interrupted_copy_never_publishes(self):
        with patch.object(L, 'sqlite_copy', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError): self.make()
        self.assertFalse(self.cp.exists())

    def test_wal_crash_committed_rows_retained_uncommitted_rows_rolled_back(self):
        dbpath = self.build / 'stack/work.sqlite3'
        code = "import sqlite3,os,sys; d=sqlite3.connect(sys.argv[1]); d.execute('PRAGMA journal_mode=WAL'); d.execute(\"insert into records values('committed','yes')\"); d.commit(); d.execute(\"insert into records values('uncommitted','no')\"); os._exit(0)"
        subprocess.run([sys.executable, '-c', code, str(dbpath)], check=True)
        self.make()
        db = sqlite3.connect(self.cp / 'build/stack/work.sqlite3')
        self.assertEqual(db.execute('select id from records order by id').fetchall(), [('committed',), ('original',)])
        self.assertEqual(db.execute('pragma integrity_check').fetchall(), [('ok',)]); db.close()

    def test_readonly_empty_wal_does_not_report_work_changed(self):
        p = self.build / 'stack/work.sqlite3'
        db = sqlite3.connect(p); db.execute('pragma journal_mode=wal'); db.close()
        before = L.sha(p)
        self.make()
        self.assertEqual(L.sha(p), before)
        self.assertNotIn('stack/work.sqlite3-wal', L.read(self.cp / 'manifest.json')['files'])

    def test_existing_destination_and_nested_destination_rejected(self):
        self.make()
        with self.assertRaisesRegex(ValueError, 'already exists'): self.make()
        with self.assertRaisesRegex(ValueError, 'separate'): L.checkpoint(self.config, self.build / 'nested')
        with self.assertRaisesRegex(ValueError, 'already exists'): L.restore(self.cp, self.build)

    def test_corruption_rejected_before_restore_published(self):
        self.make(); (self.cp / 'build/connection.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Corrupt'): L.restore(self.cp, self.root / 'restored')
        self.assertFalse((self.root / 'restored').exists())

    def test_manifest_mismatch_and_incompatible_version(self):
        self.make(); p = self.cp / 'manifest.json'; value = L.read(p); value['schema'] = 'future/9'; p.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'checksum'): L.verify_checkpoint(self.cp)
        (self.cp / 'complete.json').write_text(json.dumps({'manifest_sha256': L.sha(p)}))
        with self.assertRaisesRegex(ValueError, 'version'): L.verify_checkpoint(self.cp)

    def test_external_manifest_change_rejected(self):
        self.make(); (self.dep / 'run.json').write_text('{"version":2}')
        with self.assertRaisesRegex(ValueError, 'dependency manifest changed'): L.restore(self.cp, self.root / 'restored')

    def test_changed_code_refused(self):
        self.make()
        with patch.object(L, 'code_identity', return_value={'engine': 'changed'}):
            with self.assertRaisesRegex(ValueError, 'code differs'): L.verify_checkpoint(self.cp)

    def test_symlink_and_unsafe_paths(self):
        (self.build / 'leak').symlink_to(self.dep)
        with self.assertRaisesRegex(ValueError, 'symlink'): self.make()
        for name in ('../outside', '/absolute', 'a/../../outside', 'a//b'):
            with self.assertRaises(ValueError): L.safe_relative(name)

    def test_missing_config_or_cross_build_link_refused(self):
        with self.assertRaises(FileNotFoundError): L.validate_config(self.root / 'missing.json')
        (self.build / 'stack/annotation-link.json').write_text(json.dumps({'path': str(self.dep)}))
        with self.assertRaisesRegex(ValueError, 'inside this build'): L.validate_config(self.config)

    def test_configuration_source_identity_mismatch(self):
        path = self.build / 'evidence/run.json'; c = L.read(path); c['source_run'] = 'other'; path.write_text(json.dumps(c))
        with self.assertRaisesRegex(ValueError, 'Source-run'): L.validate_config(self.config)

    def test_startup_uses_receipt_and_rejects_live_flag(self):
        self.make(); out = self.root / 'restored'; L.restore(self.cp, out)
        path = out / 'configuration.json'; value = L.read(path); value['recovery']['checkpoint_id'] = 'other'; path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'receipt'): L.validate_config(path)

if __name__ == '__main__': unittest.main()
