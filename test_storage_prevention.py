"""Storage failures use tiny authored fixtures; no live data, models or providers."""
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere import local_release as L
from clinical_core.agere.clinical import semantic_layer as S
from clinical_core.agere.deliverables.receipts import Bundle

C = importlib.import_module('clinical_core.agere.deliverables.compose')
from tools import compact_saved_json as J
import check_agere_build_control as Control


class StoragePrevention(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stack = self.root / 'stack'; self.stack.mkdir()
        (self.stack / 'run.json').write_text(json.dumps({'source_digest': 'authored'}))
        self.db = self.stack / 'semantic.sqlite3'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
            db.executemany('INSERT INTO meta VALUES (?,?)', [('version', S.VERSION),
                ('source_digest', 'authored'), ('postprocessing_identity', 'pinned')])
            db.execute('CREATE TABLE amounts(patient, cents)')
            db.executemany('INSERT INTO amounts VALUES (?,?)', [('a', 0), ('b', -123), ('c', None)])

    def test_policy_mismatch_never_opens_writable_database_or_copies(self):
        before = self.db.read_bytes(); connect = sqlite3.connect
        def readonly(path, *args, **kwargs):
            self.assertIn('mode=ro', str(path))
            return connect(path, *args, **kwargs)
        with patch.object(S, 'policy_identity', return_value='different'), \
             patch.object(S.sqlite3, 'connect', side_effect=readonly), \
             patch.object(S, 'build') as build, patch.object(S, 'finish_materialization') as finish:
            with self.assertRaisesRegex(S.MaterializationRequired, 'runtime profile'):
                S.ensure(self.stack)
            build.assert_not_called(); finish.assert_not_called()
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse((self.stack / 'semantic.sqlite3.tmp').exists())

    def test_compatible_snapshot_reuses_exact_received_values(self):
        before = self.db.read_bytes()
        with patch.object(S, 'policy_identity', return_value='pinned'), patch.object(S, 'build') as build:
            target = S.ensure(self.stack)
            with sqlite3.connect(target.as_uri() + '?mode=ro', uri=True) as db:
                self.assertEqual(db.execute('SELECT * FROM amounts').fetchall(), [('a', 0), ('b', -123), ('c', None)])
            build.assert_not_called()
        self.assertEqual(self.db.read_bytes(), before)

    def test_missing_or_changed_source_requires_explicit_materialization(self):
        for remove in (False, True):
            with self.subTest(missing=remove), patch.object(S, 'build') as build:
                if remove: self.db.unlink()  # authored test fixture only
                else:
                    (self.stack / 'run.json').write_text(json.dumps({'source_digest': 'new-source'}))
                with self.assertRaisesRegex(S.MaterializationRequired, 'explicitly'):
                    S.ensure(self.stack)
                build.assert_not_called()
        self.assertFalse((self.stack / 'semantic.sqlite3.tmp').exists())

    def test_existing_failed_temporary_remains_exact_on_analytical_refusal(self):
        retained = self.stack / 'semantic.sqlite3.tmp'; retained.write_bytes(b'preserved interrupted attempt')
        with patch.object(S, 'policy_identity', return_value='different'):
            with self.assertRaises(S.MaterializationRequired): S.ensure(self.stack)
        self.assertEqual(retained.read_bytes(), b'preserved interrupted attempt')

    def test_storage_reserve_accounts_for_three_times_largest_file(self):
        with patch.object(L.shutil, 'disk_usage', return_value=SimpleNamespace(free=65_000_000_000)):
            with self.assertRaisesRegex(OSError, '72000000000 required'):
                L.require_storage(self.root / 'not-created/file', largest_file=24_000_000_000)
        self.assertFalse((self.root / 'not-created').exists())

    def test_large_plain_and_sqlite_copy_refuse_before_destination_or_db_open(self):
        source = self.root / 'sparse-authored';
        with source.open('wb') as f: f.truncate(100_000_001)
        for copy in (L.copy_file, L.sqlite_copy):
            with self.subTest(copy=copy.__name__), \
                 patch.object(L.shutil, 'disk_usage', return_value=SimpleNamespace(free=2_000_000_000)), \
                 patch.object(L.sqlite3, 'connect', side_effect=AssertionError('refuse before database open')):
                with self.assertRaisesRegex(OSError, 'Nothing copied'):
                    copy(source, self.root / 'absent/destination')
            self.assertFalse((self.root / 'absent').exists())

    def test_low_space_checkpoint_refuses_before_hash_copy_or_stage(self):
        target = self.root / 'package'
        with patch.object(L, 'validate_config', return_value={'build': str(self.stack)}), \
             patch.object(L.shutil, 'disk_usage', return_value=SimpleNamespace(free=2_000_000_000)), \
             patch.object(L, 'sha', return_value='fixture-config'), \
             patch.object(L, 'inventory', side_effect=AssertionError('no inventory hash')), \
             patch.object(L, 'copy_file', side_effect=AssertionError('no copy')):
            with self.assertRaisesRegex(OSError, 'Insufficient storage'):
                L.checkpoint(self.root / 'fixture-config', target)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob('.package.partial-*')))

    def test_pinned_read_only_preflight_does_not_require_a_package_copy_reserve(self):
        config = self.root / 'config/agere/active_warehouse.json'; config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'build': str(self.root)}))
        before = self.db.read_bytes()
        with patch.object(S, 'policy_identity', return_value='pinned'), \
             patch.object(L, 'require_storage', side_effect=AssertionError('No package or large copy')):
            result = Control.warehouse_read_preflight(self.root)
        self.assertEqual(result['mode'], 'read-only')
        self.assertEqual(result['path'], str(self.db.resolve()))
        self.assertEqual(self.db.read_bytes(), before)

    def test_read_only_preflight_refuses_wrong_policy_without_rebuilding(self):
        config = self.root / 'config/agere/active_warehouse.json'; config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'build': str(self.root)}))
        with patch.object(S, 'policy_identity', return_value='different'), patch.object(S, 'build') as build:
            with self.assertRaisesRegex(ValueError, 'pinned configuration'):
                Control.warehouse_read_preflight(self.root)
            build.assert_not_called()
        self.assertFalse((self.stack / 'semantic.sqlite3.tmp').exists())

    def test_explicit_candidate_config_does_not_rebind_a_historical_default(self):
        default = self.root / 'config/agere/active_warehouse.json'; default.parent.mkdir(parents=True)
        default.write_text(json.dumps({'build': str(self.root / 'historical')}))
        candidate = self.root / 'candidate.json'; candidate.write_text(json.dumps({'build': str(self.root)}))
        before = default.read_bytes()
        with patch.object(S, 'policy_identity', return_value='pinned'):
            self.assertEqual(Control.warehouse_read_preflight(self.root, candidate)['path'], str(self.db.resolve()))
            with self.assertRaisesRegex(ValueError, 'materialized semantic'):
                Control.warehouse_read_preflight(self.root)
        self.assertEqual(default.read_bytes(), before)


class PackedBundleStorage(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.ws = SimpleNamespace(dir=self.root, slug='authored')
        self.key = 'DLV-0123456789AB'

    def bundle(self, large=True):
        b = Bundle()
        b.add('authored-source', {'period': '2029'}, {'text': 'dated source evidence\n' * (80000 if large else 1),
            'rows': [{'patient': 'a', 'cents': 0}, {'patient': 'b', 'cents': -123}, {'patient': 'c', 'cents': None}]},
            source={'release': 'authored-source'})
        return b

    def save(self, bundle):
        return C._save(self.ws, self.key, doc=None, body=None, bundle=bundle,
                       spec={'id': self.key, 'status': 'partial', 'verification': 'unverified'})

    def test_large_bundle_roundtrip_is_lossless_and_retains_partial_status(self):
        bundle = self.bundle(); d = self.save(bundle)
        self.assertFalse((d / 'bundle.json').exists())
        self.assertTrue((d / 'bundle.json.gz').exists())
        spec, loaded, _, _ = C.load(self.ws, self.key)
        self.assertEqual(loaded.to_json(), bundle.to_json())
        self.assertEqual(spec['status'], 'partial'); self.assertEqual(spec['verification'], 'unverified')
        plain = json.dumps(bundle.to_json(), default=str).encode()
        self.assertLess((d / 'bundle.json.gz').stat().st_size, len(plain) // 10)

    def test_legacy_plain_bundle_loads_without_storage_metadata(self):
        bundle = self.bundle(False); d = self.save(bundle)
        spec = json.loads((d / 'spec.json').read_text()); spec.pop('bundle_storage')
        (d / 'spec.json').write_text(json.dumps(spec))
        self.assertEqual(C.load(self.ws, self.key)[1].to_json(), bundle.to_json())

    def test_new_packed_revision_cannot_be_shadowed_by_retained_plain_bundle(self):
        old = self.bundle(False); d = self.save(old); before = (d / 'bundle.json').read_bytes()
        current = self.bundle(); self.save(current)
        self.assertEqual((d / 'bundle.json').read_bytes(), before)
        self.assertEqual(C.load(self.ws, self.key)[1].to_json(), current.to_json())

    def test_invalid_storage_path_rejected_and_corrupt_current_never_uses_old_bundle(self):
        d = self.save(self.bundle(False)); self.save(self.bundle())
        (d / 'bundle.json.gz').write_bytes(b'incomplete gzip')
        with self.assertRaises((OSError, EOFError)): C.load(self.ws, self.key)
        spec = json.loads((d / 'spec.json').read_text()); spec['bundle_storage'] = '../other'
        (d / 'spec.json').write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError, 'storage path'): C.load(self.ws, self.key)


class ExistingJsonCompressionSafety(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_incomplete_compression_retains_original_and_partial(self):
        source = self.root / 'answer.json'; source.write_bytes(b'original saved evidence')
        def bad_copy(source, target): target.write_bytes(b'incomplete')
        with self.assertRaisesRegex(ValueError, 'original and partial retained'):
            J.compact(source, copier=bad_copy)
        self.assertEqual(source.read_bytes(), b'original saved evidence')
        self.assertEqual(len(list(self.root.glob('*.partial'))), 1)

    def test_no_destination_created_when_full_copy_would_not_fit(self):
        source = self.root / 'answer.json'; source.write_bytes(b'original')
        with patch.object(J, 'free_bytes', return_value=0):
            result = J.compact(source, copier=lambda *_: self.fail('no copy'))
        self.assertEqual(result['status'], 'deferred_insufficient_copy_space')
        self.assertEqual(list(self.root.iterdir()), [source])

    def test_native_compression_decline_preserves_contents_and_retains_shared_attempt(self):
        source = self.root / 'answer.json'; source.write_bytes(b'authored native decline\n' * 100)
        before = J.metadata(source)
        result = J.compact(source, copier=shutil.copy2)
        self.assertEqual(result['status'], 'native_compression_declined_verified')
        self.assertEqual(J.metadata(source), before)
        self.assertEqual(source.read_bytes(), Path(result['retained_attempt']).read_bytes())

    def test_candidate_scope_excludes_other_artifacts_symlinks_and_small_files(self):
        root = self.root / 'answer-workspaces'; root.mkdir()
        saved = root / ('ANS-' + 'a' * 24); saved.mkdir()
        (saved / 'answer.json').write_bytes(b'a' * 1_048_577)
        (saved / 'bundle.json').write_bytes(b'small')
        (saved / 'database.sqlite3').write_bytes(b'not eligible')
        outside = self.root / 'large-source.json'; outside.write_bytes(b'b' * 1_048_577)
        (saved / 'sub').symlink_to(self.root, target_is_directory=True)
        self.assertEqual(J.candidates(root), [(saved / 'answer.json').resolve()])
        with self.assertRaisesRegex(ValueError, 'answer-workspaces'): J.candidates(self.root)


if __name__ == '__main__': unittest.main()
