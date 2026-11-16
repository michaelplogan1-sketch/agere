"""Self-count execution rewrites preserve source scope, nulls and all rows."""
import sqlite3,unittest
from clinical_core.agere.clinical.general_analysis import grouped_self_counts

class SelfCounts(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE events(patient_id TEXT,record_id TEXT,kind TEXT,body TEXT,event_date TEXT);CREATE TABLE other AS SELECT * FROM events;')
        self.db.executemany('INSERT INTO events VALUES(?,?,?,?,?)',[
            ('p1','a','x',' Text ','2028-01-01'),('p1','b','x','text','2029-01-01'),('p1','b','x','text','2029-01-01'),
            ('p1',None,'x','TEXT','2029-01-02'),('p1','c','x',None,'2029-01-03'),('p1','d','y','text','2029-01-04'),
            ('p2','a','x','text','2029-01-05'),(None,'e','x','text','2029-01-06'),('p1','f',None,'text','2029-01-07')])
    def equivalent(self,query,rewrites=1):
        optimized,receipt=grouped_self_counts(query)
        self.assertIsNotNone(receipt);self.assertEqual(len(receipt['rewrites']),rewrites)
        left=self.db.execute(query);cols=[x[0]for x in left.description];rows=left.fetchall()
        right=self.db.execute(optimized);self.assertEqual([x[0]for x in right.description],cols)
        self.assertEqual(right.fetchall(),rows)
        return rows,optimized
    def test_distinct_source_records_nulls_normalized_text_and_year_scope(self):
        q="SELECT n.patient_id,n.record_id,n.kind,n.body,(SELECT COUNT(DISTINCT m.record_id) FROM events m WHERE m.patient_id=n.patient_id AND m.kind=n.kind AND lower(trim(m.body))=lower(trim(n.body)))AS copies FROM events n WHERE n.event_date>='2029-01-01' ORDER BY n.rowid"
        rows,_=self.equivalent(q);self.assertEqual([r[-1]for r in rows],[2,2,2,0,1,1,0,0])
    def test_inner_local_predicates_remain_inside_the_grouped_relation(self):
        q="SELECT n.record_id,(SELECT COUNT(DISTINCT m.record_id)FROM events m WHERE n.patient_id=m.patient_id AND m.event_date>='2029-01-01' AND (m.kind='x'OR m.kind='y'))AS n FROM events n ORDER BY n.rowid"
        self.equivalent(q)
    def test_multiple_counts_are_independent_and_outer_qualified_star_stays_exact(self):
        q="SELECT n.*,(SELECT COUNT(DISTINCT m.record_id)FROM events m WHERE m.patient_id=n.patient_id)AS per_patient,(SELECT COUNT(DISTINCT t.record_id)FROM events t WHERE t.kind=n.kind)AS per_kind FROM events n ORDER BY n.rowid"
        self.equivalent(q,2)
    def test_unsupported_or_volatile_shapes_are_unchanged_byte_for_byte(self):
        queries=[
            'SELECT *, (SELECT COUNT(DISTINCT m.record_id)FROM events m WHERE m.kind=n.kind)AS c FROM events n',
            'SELECT n.record_id,(SELECT COUNT(m.record_id)FROM events m WHERE m.kind=n.kind)AS c FROM events n',
            'SELECT n.record_id,(SELECT COUNT(DISTINCT m.record_id)FROM other m WHERE m.kind=n.kind)AS c FROM events n',
            'SELECT n.record_id,(SELECT COUNT(DISTINCT m.record_id)FROM events m WHERE m.kind=n.body)AS c FROM events n',
            'SELECT n.record_id,(SELECT COUNT(DISTINCT m.record_id)FROM events m WHERE m.event_date<=n.event_date)AS c FROM events n',
            'SELECT n.record_id,(SELECT COUNT(DISTINCT m.record_id)FROM events m WHERE m.kind=n.kind AND random()>0)AS c FROM events n',
            'SELECT n.kind,MAX((SELECT COUNT(DISTINCT m.record_id)FROM events m WHERE m.kind=n.kind))AS c FROM events n GROUP BY n.kind',
        ]
        for q in queries:
            with self.subTest(query=q):self.assertEqual(grouped_self_counts(q),(q,None))
    def test_declared_collation_and_numeric_affinity_preserve_equality_groups(self):
        self.db.executescript("CREATE TABLE keys(id TEXT,code TEXT COLLATE NOCASE,n NUMERIC);INSERT INTO keys VALUES('a','A',1),('b','a','1'),('c','B',1.0),('d',NULL,NULL);")
        self.equivalent('SELECT k.id,(SELECT COUNT(DISTINCT z.id)FROM keys z WHERE z.code=k.code AND z.n=k.n)AS c FROM keys k ORDER BY k.rowid')
    def test_cte_plan_rewrite_preserves_dependencies_and_final_columns(self):
        q='WITH first AS (SELECT n.record_id,(SELECT COUNT(DISTINCT m.record_id)FROM events m WHERE m.patient_id=n.patient_id)AS copies FROM events n),second AS (SELECT record_id,copies FROM first) SELECT * FROM second ORDER BY record_id'
        self.equivalent(q)

if __name__=='__main__':unittest.main()
