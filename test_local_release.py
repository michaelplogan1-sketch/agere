"""Recovery faults use independent tiny SQLite builds; no live records or models."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere import local_release as L

class LiveSemanticPolicy(unittest.TestCase):
    def test_policy_mismatch_stops_before_credentials_or_server_and_never_rebuilds(self):
        import serve_current_warehouse as launcher
        from clinical_core.agere.clinical import semantic_layer
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);stack=root/'stack';stack.mkdir()
            path=stack/'semantic.sqlite3'
            with sqlite3.connect(path)as db:
                db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
                db.execute('INSERT INTO meta VALUES(?,?)',('postprocessing_identity','previous'))
            before=path.read_bytes()
            with patch.object(launcher,'validate_config',return_value={'build':str(root)}), \
                 patch.object(launcher,'serve')as serve, \
                 patch.object(semantic_layer,'ensure',side_effect=AssertionError('No implicit rebuild')):
                with self.assertRaisesRegex(ValueError,'pinned configuration'):
                    launcher.main(['--allow-live-stack'])
                serve.assert_not_called()
            self.assertEqual(path.read_bytes(),before)
            self.assertFalse((stack/'semantic.sqlite3.tmp').exists())
            with sqlite3.connect(path)as db:
                db.execute('UPDATE meta SET value=?',(semantic_layer.policy_identity(),))
            launcher.validate_semantic_policy(stack)


class LedgerDependencies(unittest.TestCase):
    def test_checkpoint_tracks_configured_accounting_instead_of_unused_default(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp).resolve();build=root/'build';(build/'stack').mkdir(parents=True)
            encoder=root/'encoder';source=root/'source';reranker=root/'models/bge-reranker-v2-m3'
            for path in (encoder,source,reranker):
                path.mkdir(parents=True);(path/'run.json').write_text('{"synthetic":true}')
            (build/'stack/run.json').write_text(json.dumps({'encoder_path':str(encoder)}))
            legacy=root/'artifacts/agere-full/provider-budget.sqlite3';legacy.parent.mkdir(parents=True);legacy.write_text('unused accounting')
            current=root/'current/provider-budget.sqlite3';current.parent.mkdir();current.write_text('current accounting')
            with patch.object(L,'ROOT',root),patch.dict(os.environ,{'AGERE_LEDGER_PATH':str(current)}):
                deps=L.dependencies(build,{'source_release':str(source)})
                ledger=[d for d in deps if d['verification']=='current_shared_ledger_never_restored']
                self.assertEqual(ledger,[{'path':str(current),'verification':'current_shared_ledger_never_restored'}])
                L.check_dependencies(deps)
                current.unlink()
                with self.assertRaisesRegex(ValueError,'Current shared provider ledger required'):
                    L.dependencies(build,{'source_release':str(source)})
            self.assertEqual(legacy.read_text(),'unused accounting')


class IndependentFileCopies(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'native clonefile contract')
    def test_native_small_file_clone_is_independent_and_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp)/'source', Path(temp)/'nested/copy'
            source.write_bytes(b'independent runtime'); source.chmod(0o751)
            os.utime(source, ns=(1700000000123456789, 1700000000123456789))
            before = source.stat()
            with patch.object(L.shutil, 'copy2', side_effect=AssertionError('clone expected')), \
                 patch.object(L.subprocess, 'run', side_effect=AssertionError('no per-file process')):
                L.copy_file(source, target)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(target.stat().st_mode, before.st_mode)
            self.assertNotEqual(target.stat().st_ino, before.st_ino)
            target.write_bytes(b'changed installation')
            self.assertEqual(source.read_bytes(), b'independent runtime')
            source.write_bytes(b'changed development runtime')
            self.assertEqual(target.read_bytes(), b'changed installation')

    def test_unsupported_clone_and_existing_destination_keep_copy_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp)/'source', Path(temp)/'copy'
            source.write_bytes(b'complete data')
            for clone in (None, lambda *args: -1):
                with self.subTest(clone=clone), patch.object(L, '_clone_function', return_value=clone):
                    L.copy_file(source, target)
                    self.assertEqual(target.read_bytes(), b'complete data')
                    target.write_bytes(b'old destination')
            L.copy_file(source, target)
            self.assertEqual(target.read_bytes(), b'complete data')

    def test_source_symlink_copies_contents_and_same_file_is_not_truncated(self):
        with tempfile.TemporaryDirectory() as temp:
            source, link, target = (Path(temp)/n for n in ('source', 'link', 'copy'))
            source.write_text('runtime'); link.symlink_to(source)
            L.copy_file(link, target)
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_text(), 'runtime')
            import shutil
            with self.assertRaises(shutil.SameFileError): L.copy_file(source, source)
            self.assertEqual(source.read_text(), 'runtime')


class SavedAnswerRecovery(unittest.TestCase):
    def test_cold_copy_is_independent_and_retains_observation_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp)/'source.sqlite3', Path(temp)/'copy.sqlite3'
            with sqlite3.connect(source) as db:
                db.execute('CREATE TABLE state(value)'); db.execute('INSERT INTO state VALUES(1)')
            before = source.stat()
            method = L.sqlite_copy(source, target)
            self.assertIn(method, ('cold_filesystem_clone', 'sqlite_online_backup'))
            if sys.platform == 'darwin': self.assertEqual(method, 'cold_filesystem_clone')
            self.assertEqual(source.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
            with sqlite3.connect(target) as db: db.execute('UPDATE state SET value=2')
            with sqlite3.connect(source) as db: self.assertEqual(db.execute('SELECT value FROM state').fetchone(), (1,))

    def test_live_wal_copy_keeps_committed_rows_and_excludes_uncommitted(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp)/'source.sqlite3', Path(temp)/'copy.sqlite3'
            with sqlite3.connect(source) as db:
                db.execute('CREATE TABLE state(value)'); db.commit()
            writer = sqlite3.connect(source)
            try:
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute('INSERT INTO state VALUES(1)'); writer.commit()
                writer.execute('INSERT INTO state VALUES(2)')
                self.assertEqual(L.sqlite_copy(source, target), 'sqlite_online_backup')
                with sqlite3.connect(target) as db:
                    self.assertEqual(db.execute('SELECT value FROM state').fetchall(), [(1,)])
            finally: writer.close()

    @unittest.skipUnless(sys.platform == 'darwin', 'APFS clone fence')
    def test_cold_copy_rejects_source_change_instead_of_publishing(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp)/'source.sqlite3', Path(temp)/'copy.sqlite3'
            with sqlite3.connect(source) as db: db.execute('CREATE TABLE state(value)')
            run = subprocess.run
            def change(*args, **kwargs):
                result = run(*args, **kwargs)
                s = source.stat(); os.utime(source, ns=(s.st_atime_ns, s.st_mtime_ns+1000000))
                return result
            with patch.object(L.subprocess, 'run', side_effect=change):
                with self.assertRaisesRegex(ValueError, 'Source changed'):
                    L.sqlite_copy(source, target)

    def test_backup_reopens_current_answer_and_later_source_change_still_invalidates(self):
        from test_patient_review_profiles import Profiles
        from clinical_core.agere.deliverables import answer_context as A
        case = Profiles(); case.setUp(); self.addCleanup(case.doCleanups)
        ref = A.capture(case.path, 'Inspect the selected patients', {'answer': case.answer})
        before = A.load(case.path, ref['id'], current=True)
        source = case.path / 'semantic.sqlite3'
        backup = case.path / 'backup.sqlite3'
        L.sqlite_copy(source, backup)
        source.rename(case.path / 'original.sqlite3')
        L.copy_file(backup, source)
        restored = A.load(case.path, ref['id'], current=True)
        self.assertEqual(restored['source'], before['source'])
        self.assertFalse(restored['stale'])
        self.assertEqual(restored['answer'], before['answer'])
        with sqlite3.connect(source) as db:
            db.execute('UPDATE measurement_latest SET value_canonical=5.1')
        with self.assertRaisesRegex(ValueError, 'data changed'):
            A.load(case.path, ref['id'], current=True)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        # These authored packages contain kilobytes. Exercise their recovery
        # behavior independently of the developer machine's free space;
        # low-space rejection is covered by test_storage_prevention.
        capacity = patch.object(L.shutil, 'disk_usage', return_value=SimpleNamespace(free=1_000_000_000_000))
        capacity.start(); self.addCleanup(capacity.stop)
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
