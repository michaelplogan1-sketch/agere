"""Operational snapshots retain committed state and isolate later writes."""
import os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import evaluation as E


class OperationalSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.stack=self.root/'stack';self.stack.mkdir()
        env=patch.dict(os.environ,{'AGERE_EVALUATION_SCRATCH':str(self.root/'scratch'),
                                   'AGERE_EVALUATION':'','AGERE_FROZEN_LEARNING':''})
        env.start();self.addCleanup(env.stop)
        self.source=self.stack/'state.sqlite3'

    def seed(self,wal=False):
        db=sqlite3.connect(self.source)
        if wal:db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY,payload TEXT)')
        db.executemany('INSERT INTO state VALUES (?,?)',[(1,'known'),(2,'unknown')]);db.commit()
        return db

    def rows(self,path):
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)as db:
            return db.execute('SELECT * FROM state ORDER BY id').fetchall()

    def test_snapshot_and_both_write_directions_remain_independent(self):
        self.seed().close();before=self.source.read_bytes()
        with E.scope('first',self.stack):
            target=E.operational_path(self.stack,'state.sqlite3')
            self.assertEqual(target.read_bytes(),before)
            self.assertNotEqual(target.stat().st_ino,self.source.stat().st_ino)
            with sqlite3.connect(target)as db:db.execute("INSERT INTO state VALUES (3,'private')")
            self.assertEqual(E.operational_path(self.stack,'state.sqlite3'),target)
            self.assertEqual(len(self.rows(target)),3)
        self.assertEqual(self.source.read_bytes(),before)
        with sqlite3.connect(self.source)as db:db.execute("INSERT INTO state VALUES (4,'later source')")
        self.assertEqual(self.rows(target),[(1,'known'),(2,'unknown'),(3,'private')])
        with E.scope('independent',self.stack):second=E.operational_path(self.stack,'state.sqlite3')
        self.assertEqual(self.rows(second),[(1,'known'),(2,'unknown'),(4,'later source')])

    def test_live_wal_is_included_by_sqlite_backup(self):
        original=self.seed(wal=True)
        try:
            self.assertGreater(Path(str(self.source)+'-wal').stat().st_size,0)
            with E.scope('wal',self.stack),patch('ctypes.CDLL',side_effect=AssertionError('Live WAL cannot use file clone')):
                target=E.operational_path(self.stack,'state.sqlite3')
            self.assertEqual(self.rows(target),[(1,'known'),(2,'unknown')])
            original.execute("INSERT INTO state VALUES (3,'after snapshot')");original.commit()
            self.assertEqual(len(self.rows(target)),2);self.assertEqual(len(self.rows(self.source)),3)
        finally:original.close()

    def test_unsupported_clone_retains_portable_backup(self):
        self.seed().close();before=self.source.read_bytes()
        with E.scope('portable',self.stack),patch.object(E,'_clone_sqlite_snapshot',return_value=False):
            target=E.operational_path(self.stack,'state.sqlite3')
        self.assertEqual(self.rows(target),self.rows(self.source));self.assertEqual(self.source.read_bytes(),before)

    def test_non_evaluation_keeps_original_path_and_missing_source_is_private(self):
        self.assertEqual(E.operational_path(self.stack,'state.sqlite3'),self.source)
        with E.scope('empty',self.stack):target=E.operational_path(self.stack,'state.sqlite3')
        self.assertTrue(target.exists());self.assertFalse(self.source.exists())


if __name__=='__main__':unittest.main()
