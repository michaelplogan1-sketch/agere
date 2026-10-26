"""Scoped dense retrieval retains content membership without a warehouse-wide scan."""
import sqlite3
import unittest


class ScopedContentLookup(unittest.TestCase):
    def test_fts_scope_first_retains_bm25_order_without_global_postings_scan(self):
        from clinical_core.agere.clinical.warehouse_retrieval import lexical_query
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('CREATE TABLE passages(id INTEGER PRIMARY KEY,patient TEXT); CREATE INDEX patient_passages ON passages(patient); CREATE VIRTUAL TABLE search USING fts5(text); CREATE TEMP TABLE query_scope_passages(id INTEGER PRIMARY KEY);')
        for i,text in enumerate(['heart failure','beta blocker','heart failure heart failure','heart medication','renal failure','not taking beta blocker'],1):
            db.execute('INSERT INTO passages VALUES (?,?)',(i,'A' if i%2 else 'B'))
            db.execute('INSERT INTO search(rowid,text) VALUES (?,?)',(i,text))
        for scope in ([],[1],[1,2,3],[2,4,6],list(range(1,7))):
            db.execute('DELETE FROM query_scope_passages');db.executemany('INSERT INTO query_scope_passages VALUES (?)',[(i,)for i in scope])
            for patient in (None,'A','B'):
                for expression in ('heart OR failure','beta AND blocker','"not taking"','missingterm'):
                    for limit in (1,40,80):
                        with self.subTest(scope=scope,patient=patient,expression=expression,limit=limit):
                            old='SELECT p.id FROM search JOIN passages p ON p.id=search.rowid JOIN query_scope_passages qs ON qs.id=p.id WHERE search MATCH ?'+(' AND p.patient=?' if patient else '')+' ORDER BY bm25(search),p.id LIMIT '+str(limit)
                            args=[expression]+([patient] if patient else [])
                            new=lexical_query(scoped=True,patient=bool(patient),limit=limit)
                            self.assertEqual(list(db.execute(old,args)),list(db.execute(new,args)))
        query=lexical_query(scoped=True,patient=True)
        plan=[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+query,('heart','A'))]
        self.assertIn('SCAN qs',plan)
        self.assertTrue(any('VIRTUAL TABLE INDEX' in x and '=' in x for x in plan),plan)
        patient_plan=[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+lexical_query(patient=True),('heart','A'))]
        self.assertTrue(any('SEARCH p USING COVERING INDEX patient_passages' in x for x in patient_plan),patient_plan)
        self.assertEqual(lexical_query(), 'SELECT p.id FROM search JOIN passages p ON p.id=search.rowid WHERE search MATCH ? ORDER BY bm25(search),p.id LIMIT 80')

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
