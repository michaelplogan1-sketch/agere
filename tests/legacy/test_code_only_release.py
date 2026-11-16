"""Tiny authored archives; no live source, provider, model or runtime copies."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from clinical_core.agere import local_release as L, runnable_release as R


class CodeOnlyRelease(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(); self.code = self.root / 'checkout'
        source = self.code / 'clinical_core/example.py'; source.parent.mkdir(parents=True); source.write_text('VALUE = 1\n')
        (self.code / 'run_integrated_stack.py').write_text('def keys(): pass\n')
        (self.code / 'models').mkdir(); (self.code / 'models/weights').write_bytes(b'never copy models')
        self.build = self.root / 'build'; self.build.mkdir(); (self.build / 'source.sqlite3').write_bytes(b'never copy source')
        self.ledger = self.root / 'provider-ledger.sqlite3'; self.ledger.write_bytes(b'accounting')
        self.archive = self.root / 'code.tar.gz'; self.installation = self.root / 'fresh-code'
        self.authorization = self.root / 'authorization.json'
        self.record = {'schema': 'agere-code-only-storage-authorization/1', 'approved': True, 'authority': 'Authored unit fixture',
            'archive': str(self.archive), 'installation': str(self.installation),
            'database_copy_bytes': 0, 'model_copy_bytes': 0, 'runtime_copy_bytes': 0}
        self.authorization.write_text(json.dumps(self.record))
        for target, name, value in [(L, 'ROOT', self.code),
                (L, 'code_identity', lambda: {'clinical_core/example.py': L.sha(source)}),
                (L, 'validate_config', lambda _: {'build': str(self.build), 'synthetic': True})]:
            handle = patch.object(target, name, value); handle.start(); self.addCleanup(handle.stop)
        handle = patch('clinical_core.agere.gateway.default_ledger_path', return_value=self.ledger)
        handle.start(); self.addCleanup(handle.stop)

    def make(self):
        return R.make_code_only(self.root / 'config.json', self.archive, authorization=self.authorization)

    def test_archive_references_existing_resources_and_never_copies_data_runtime_or_ledger(self):
        before = self.ledger.read_bytes()
        with patch.object(L, 'copy_file', side_effect=AssertionError('No runtime copy')), \
                patch.object(L, 'sqlite_copy', side_effect=AssertionError('No database copy')):
            result = self.make()
            R.install_code_only(self.archive, self.installation, authorization=self.authorization)
        with tarfile.open(self.archive) as stream:
            names = stream.getnames()
        self.assertFalse(any('sqlite' in name or 'models/' in name or 'runtime/' in name for name in names))
        self.assertFalse(result['source_data_copied']); self.assertFalse(result['runtime_copied'])
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertEqual((self.installation / 'app/models').resolve(), self.code / 'models')
        self.assertEqual(L.read(self.installation / 'configuration.json')['build'], str(self.build))

    def test_general_guard_still_applies_without_explicit_target_bound_exception(self):
        with patch.object(L, 'require_storage', side_effect=OSError('60 GB required')) as guard:
            with self.assertRaisesRegex(OSError, '60 GB'):
                R.make_code_only('config', self.archive)
            guard.assert_called_once(); self.assertFalse(self.archive.exists())
        for changes in ({'approved': False}, {'runtime_copy_bytes': 1}, {'archive': str(self.root / 'other')}):
            with self.subTest(changes=changes):
                self.authorization.write_text(json.dumps(dict(self.record, **changes)))
                with self.assertRaisesRegex(ValueError, 'authorization'):
                    self.make()
                self.assertFalse(self.archive.exists())

    def test_changed_installed_code_and_extra_files_refused_and_existing_paths_preserved(self):
        self.make(); R.install_code_only(self.archive, self.installation, authorization=self.authorization)
        with self.assertRaisesRegex(ValueError, 'already exists'): self.make()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            R.install_code_only(self.archive, self.installation, authorization=self.authorization)
        source = self.installation / 'app/clinical_core/example.py'
        before = source.read_bytes(); source.write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'code changed'):
            R.verify_code_only(self.installation, authorization=self.authorization)
        source.write_bytes(before); (self.installation / 'app/unexpected.py').write_text('unexpected')
        with self.assertRaisesRegex(ValueError, 'Unexpected installed'):
            R.verify_code_only(self.installation, authorization=self.authorization)

    def test_unsafe_or_duplicate_tar_members_cannot_extract(self):
        for names in (['../escape'], ['same', 'same'], ['/absolute']):
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode='w') as stream:
                for name in names:
                    entry = tarfile.TarInfo(name); entry.size = 1; stream.addfile(entry, io.BytesIO(b'x'))
            data.seek(0)
            with tarfile.open(fileobj=data, mode='r') as stream, self.assertRaises(ValueError):
                R._verified_code_archive(stream)
        self.assertFalse((self.root / 'escape').exists())

    def test_even_self_consistent_database_entry_is_not_a_code_archive(self):
        self.make()
        with tarfile.open(self.archive) as stream:
            record, files = R._verified_code_archive(stream)
        files['app/source.sqlite3'] = b'excluded data'
        record['files']['app/source.sqlite3'] = {'size': len(files['app/source.sqlite3']),
            'sha256': hashlib.sha256(files['app/source.sqlite3']).hexdigest()}
        files['release.json'] = json.dumps(record).encode()
        files['complete.json'] = json.dumps({'release_id': hashlib.sha256(files['release.json']).hexdigest()}).encode()
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as stream:
            for name, value in files.items():
                entry = tarfile.TarInfo(name); entry.size = len(value); stream.addfile(entry, io.BytesIO(value))
        data.seek(0)
        with tarfile.open(fileobj=data, mode='r') as stream, self.assertRaisesRegex(ValueError, 'non-code'):
            R._verified_code_archive(stream)


if __name__ == '__main__':
    unittest.main()
