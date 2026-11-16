import sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere.clinical.warehouse_concurrency import configure


class ConcurrentIndexTests(unittest.TestCase):
    def test_active_source_reader_does_not_block_cache_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);target=path/'stack.sqlite3';db=sqlite3.connect(target)
            db.executescript('CREATE TABLE records(id); INSERT INTO records VALUES (1),(2),(3); CREATE TABLE result_cache(id,payload);')
            db.close();self.assertEqual(configure(path)['journal_mode'],'wal')
            reader=sqlite3.connect(target);writer=sqlite3.connect(target,timeout=.05)
            try:
                reader.execute('BEGIN');cursor=reader.execute('SELECT id FROM records');self.assertEqual(cursor.fetchone(),(1,))
                with writer:writer.execute('INSERT INTO result_cache VALUES (?,?)',('key','result'))
                self.assertEqual(cursor.fetchall(),[(2,),(3,)])
                self.assertEqual(writer.execute('SELECT count(*) FROM result_cache').fetchone()[0],1)
            finally:reader.close();writer.close()
            self.assertEqual(configure(path)['journal_mode'],'wal')

    def test_never_creates_a_missing_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):configure(tmp)
            self.assertFalse((Path(tmp)/'stack.sqlite3').exists())

    def test_population_source_query_uses_derived_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);db=sqlite3.connect(path/'stack.sqlite3')
            db.executescript('CREATE TABLE records(id TEXT,patient TEXT,stream TEXT,payload TEXT); CREATE INDEX patient_records ON records(patient,stream);')
            db.executemany('INSERT INTO records VALUES (?,?,?,?)',[(str(i),'P'+str(i),'notes' if i%2 else 'labs','{}') for i in range(30)])
            db.commit();before=db.execute('SELECT * FROM records ORDER BY id').fetchall();db.close()
            self.assertTrue(configure(path)['population_source_index']);db=sqlite3.connect(path/'stack.sqlite3')
            try:
                plan=db.execute('EXPLAIN QUERY PLAN SELECT patient,payload FROM records WHERE stream=?',('notes',)).fetchall()
                self.assertIn('received_stream_patients',' '.join(r[-1] for r in plan))
                self.assertEqual(db.execute('SELECT * FROM records ORDER BY id').fetchall(),before)
            finally:db.close()


if __name__=='__main__':unittest.main()
