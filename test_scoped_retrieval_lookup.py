"""Scoped dense retrieval retains content membership without a warehouse-wide scan."""
import sqlite3
import unittest


class ScopedContentLookup(unittest.TestCase):
    def test_scoped_lookup_preserves_membership_and_uses_primary_key_probes(self):
        from clinical_core.agere.clinical.warehouse_retrieval import SCOPED_CONTENT_SQL
        new=SCOPED_CONTENT_SQL; old='SELECT DISTINCT p.content FROM passages p JOIN query_scope_passages qs ON qs.id=p.id'
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('CREATE TABLE passages(id INTEGER PRIMARY KEY,content INTEGER,patient TEXT); CREATE INDEX content_passages ON passages(content); CREATE TEMP TABLE query_scope_passages(id INTEGER PRIMARY KEY);')
        db.executemany('INSERT INTO passages VALUES (?,?,?)',[(i,(i*53)%997,'A' if i%2 else 'B') for i in range(1,1001)])
        for ids in ([],[1],[1,2,3,4],[10,100,500,1000],list(range(1,1001))):
            with self.subTest(scope_size=len(ids)):
                db.execute('DELETE FROM query_scope_passages');db.executemany('INSERT INTO query_scope_passages VALUES (?)',[(i,)for i in ids])
                self.assertEqual(list(db.execute(old)),list(db.execute(new)))
        plan=[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+new)]
        self.assertTrue(any('SCAN qs' in x for x in plan),plan)
        self.assertTrue(any('SEARCH p USING INTEGER PRIMARY KEY' in x for x in plan),plan)
        self.assertFalse(any('SCAN p' in x for x in plan),plan)
