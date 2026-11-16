import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import evaluation as E, ocr_passages as O

class EvaluationOCR(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.stack = self.root / 'stack'; self.stack.mkdir()
        self.env = patch.dict(os.environ, {'AGERE_EVALUATION': '', 'AGERE_EVALUATION_STRICT': '1',
                                          'AGERE_EVALUATION_SCRATCH': str(self.root / 'scratch')})
        self.env.start(); self.addCleanup(self.env.stop)
        E.install_audit(); self.addCleanup(E.uninstall_audit)

    def source(self):
        with sqlite3.connect(self.stack / 'semantic.sqlite3') as db:
            db.executescript('''CREATE TABLE scanned_document_text(record_id TEXT, patient_id TEXT, text_source TEXT, text TEXT);
                CREATE TABLE scanned_document(record_id TEXT, document_id TEXT, document_type TEXT, received_via TEXT,
                                              event_date TEXT, available_date TEXT, page_count INT);
                INSERT INTO scanned_document_text VALUES ('scan-1','SYN-00000000-000','text_layer','EXAM: CHEST XRAY. The lungs are clear. No acute pulmonary abnormality.');
                INSERT INTO scanned_document VALUES ('scan-1','doc-1','radiology','fax','2029-05-01','2029-05-02',1);''')

    def test_strict_rebuild_returns_same_evidence_without_touching_original_store(self):
        self.source(); O.build(self.stack)
        store = self.stack / O.STORE
        with sqlite3.connect(store) as db: db.execute("UPDATE meta SET value='previous-copy' WHERE key='source'")
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.stack.iterdir() if p.is_file()}
        with E.scope('ocr-fixture', self.stack):
            hits = O.search(self.stack, ['clear'], patient_ids=['SYN-00000000-000'])
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]['record_id'], 'scan-1')
            self.assertNotEqual(O.store_path(self.stack).parent, self.stack)
            self.assertEqual(E.receipt()['write_attempts_by_store'], {})
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.stack.iterdir() if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(hits, O.search(self.stack, ['clear'], patient_ids=['SYN-00000000-000']))
        self.assertEqual(O.store_path(self.stack), store)

    def test_no_source_and_no_original_cache_still_means_unavailable(self):
        with E.scope('ocr-missing', self.stack):
            self.assertIsNone(O.ensure(self.stack))
            self.assertEqual(O.search(self.stack, ['clear']), [])
        self.assertFalse((self.stack / O.STORE).exists())
