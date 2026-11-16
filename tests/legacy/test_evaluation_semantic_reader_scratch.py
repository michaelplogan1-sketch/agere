import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import evaluation as E, semantic_reader as S

class ReadingScratch(unittest.TestCase):
    def test_existing_readings_are_visible_and_new_inferences_stay_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack = Path(tmp) / 'stack'; stack.mkdir()
            with patch.dict(os.environ, {'AGERE_EVALUATION': '', 'AGERE_EVALUATION_STRICT': '1',
                                        'AGERE_EVALUATION_SCRATCH': str(Path(tmp) / 'scratch')}):
                db = S.db(stack)
                with db: db.execute("INSERT INTO readings VALUES ('prior','P-1001','{}')")
                db.close()
                store = stack / 'semantic-readings.sqlite3'
                before = hashlib.sha256(store.read_bytes()).hexdigest()
                E.install_audit()
                try:
                    with E.scope('reader-fixture', stack):
                        db = S.db(stack)
                        self.assertEqual(db.execute('SELECT id FROM readings').fetchall(), [('prior',)])
                        with db: db.execute("INSERT INTO readings VALUES ('new','P-1001','{}')")
                        db.close()
                        db = S.db(stack)
                        self.assertEqual(db.execute('SELECT count(*) FROM readings').fetchone()[0], 2)
                        db.close()
                        self.assertEqual(E.receipt()['write_attempts_by_store'], {})
                finally: E.uninstall_audit()
                self.assertEqual(hashlib.sha256(store.read_bytes()).hexdigest(), before)
                db = S.db(stack)
                self.assertEqual(db.execute('SELECT count(*) FROM readings').fetchone()[0], 1)
                db.close()
