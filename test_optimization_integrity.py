"""Authored result-equivalence and constraint-comparison checks; no providers, models or external extensions."""
import inspect
import itertools
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import plan_optimizer as O, sql_query as Q, outreach_allocation as A, duckdb_engine as D,vbc


def fuse(query,db):
    return O.fuse_sql(query,db) if 'db' in inspect.signature(O.fuse_sql).parameters else O.fuse_sql(query)


class SqlEquivalence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'semantic.sqlite3'
        self.db=sqlite3.connect(self.path);self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE meta(key,value);INSERT INTO meta VALUES('version','authored'),('source_digest','authored');
          CREATE TABLE financial_label(label TEXT,amount INTEGER);INSERT INTO financial_label VALUES('Care  Plan',7),('Care Plan',11);
          CREATE TABLE parent(id INTEGER,amount INTEGER);INSERT INTO parent VALUES(1,4),(2,9);
          CREATE TABLE child(other INTEGER);INSERT INTO child VALUES(1);''');self.db.commit()

    def run_query(self,query,optimize):
        with patch.dict('os.environ',{'AGERE_PLAN_OPTIMIZER':'on' if optimize else 'off','AGERE_SQL_ENGINE':'sqlite','AGERE_SQL_SHADOW':''}):
            return Q.execute(None,{'query':query,'returns':'table'},target=self.path)

    def test_governed_execution_preserves_spaces_inside_distinct_literals(self):
        q="SELECT (SELECT amount FROM financial_label WHERE label='Care  Plan') AS spaced,(SELECT amount FROM financial_label WHERE label='Care Plan') AS single"
        before=self.run_query(q,False);after=self.run_query(q,True)
        self.assertEqual(before['rows'],[{'spaced':7,'single':11}]);self.assertEqual(after['rows'],before['rows'])
        self.assertEqual(after['query'],q)

    def test_identical_literals_remain_fused_and_cte_names_cannot_collide(self):
        sub="SELECT amount FROM financial_label WHERE label='Care  Plan'"
        q=f'WITH _fused1 AS (SELECT 123 AS placeholder) SELECT ({sub}),({sub})'
        rewritten,receipt=fuse(q,self.db)
        self.assertTrue(receipt['fused']);self.assertNotEqual(receipt['fused'][0]['name'],'_fused1')
        self.assertEqual(self.db.execute(q).fetchall(),self.db.execute(rewritten).fetchall())

    def test_quoted_identifiers_and_comments_keep_their_contents(self):
        self.db.executescript('CREATE TABLE quoted("Care  Plan" INTEGER,"Care Plan" INTEGER);INSERT INTO quoted VALUES(7,11);')
        q='SELECT (SELECT "Care  Plan" FROM quoted WHERE "Care  Plan" IS NOT NULL),(SELECT "Care Plan" FROM quoted WHERE "Care Plan" IS NOT NULL)'
        rewritten,_=fuse(q,self.db);self.assertEqual(self.db.execute(rewritten).fetchone(),(7,11))
        sub="SELECT amount FROM financial_label WHERE label='Care  Plan'"
        commented=f'SELECT ({sub}),({sub}) -- preserve scope and trailing comment\n'
        self.assertEqual(fuse(commented,self.db)[0],commented)

    def test_correlated_volatile_and_view_subqueries_are_not_hoisted(self):
        inner='SELECT amount FROM child WHERE other=1 AND other IS NOT NULL'
        q=f'SELECT ({inner}),({inner}) FROM parent'
        self.assertEqual(fuse(q,self.db)[0],q)
        counter=[0]
        def tick():counter[0]+=1;return counter[0]
        self.db.create_function('authored_tick',0,tick)
        inner='SELECT authored_tick() FROM child WHERE other=1 AND other IS NOT NULL'
        q=f'SELECT ({inner}),({inner})'
        rewritten,receipt=fuse(q,self.db)
        self.assertEqual(receipt['fused'],[]);self.assertEqual(self.db.execute(rewritten).fetchone(),(1,2))
        self.db.execute('CREATE VIEW volatile_source AS SELECT authored_tick() AS amount')
        inner='SELECT amount FROM volatile_source WHERE amount IS NOT NULL'
        q=f'SELECT ({inner}),({inner})';self.assertEqual(fuse(q,self.db)[0],q)


def candidates(rows):
    return lambda db,as_of,keep,discount:([r for r in rows if keep is None or r['patient_id'] in keep],{})


def candidate(pid,value,practice):
    return {'patient_id':pid,'practice':practice,'value_if_engaged':value,'expected_value':value*.5,'engagement_probability':.5,
            'risk_score':1,'reasons':[{'code':'AUTHORED'}]}


class ConstraintParity(unittest.TestCase):
    def run_allocation(self,rows,floor=0,slots=2,**kw):
        p=A.validate({'channels':[{'channel':'mail','hours_per_week':0,'slots_per_week':slots}],
                      'weeks':1,'min_per_practice':floor,**kw})
        with patch('clinical_core.agere.clinical.outreach_learning.response_rates',return_value={}):
            return A.run(None,None,p,'2030-12-31',candidates=candidates(rows))

    def test_floor_constrained_optimum_cannot_have_lift_against_infeasible_greedy(self):
        rows=[candidate('A1',100,'A'),candidate('A2',99,'A'),candidate('B1',1,'B')]
        r=self.run_allocation(rows,floor=1)
        feasible=[sum(rows[i]['value_if_engaged']*.5 for i in subset) for n in range(3) for subset in itertools.combinations(range(3),n)
                  if len(subset)<=2 and {rows[i]['practice'] for i in subset}=={'A','B'}]
        self.assertEqual(r['objective_usd'],max(feasible));self.assertEqual(r['objective_usd'],50.5)
        self.assertIsNone(r['greedy_baseline']['milp_lift_usd']);self.assertFalse(r['greedy_baseline'].get('comparable',True))
        self.assertEqual({x['practice'] for x in r['assignment']},{'A','B'})
        control=self.run_allocation(rows)
        self.assertEqual(control['objective_usd'],99.5);self.assertEqual(control['greedy_baseline']['milp_lift_usd'],0)

    def test_infeasible_minimum_remains_mandatory_with_a_separate_scenario(self):
        rows=[candidate('A',100,'A'),candidate('B',1,'B')]
        r=self.run_allocation(rows,floor=1,slots=1)
        self.assertEqual(r['assigned'],0);self.assertIsNone(r['objective_usd']);self.assertFalse(r['solver']['floor_dropped'])
        self.assertEqual(r['relaxed_scenario']['assigned'],1)
        self.assertEqual(A.answer(r,{'coverage':'supported_observation'},'q')['status'],'partial')

    def test_default_population_has_no_silent_candidate_cutoff(self):
        rows=[candidate(f'A{i:04}',1000-i/1000,'A') for i in range(800)]+[candidate('B',1,'B')]
        r=self.run_allocation(rows,floor=1)
        self.assertEqual(r['candidates_in_program'],801)
        self.assertEqual({x['practice'] for x in r['assignment']},{'A','B'})
        explicit=self.run_allocation(rows,max_candidates=5)
        self.assertEqual(explicit['candidates_in_program'],5)


class NumericEngineParity(unittest.TestCase):
    def setUp(self):
        import duckdb
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.sqlite=Path(self.tmp.name)/'semantic.sqlite3';self.duck=self.sqlite.with_suffix('.duckdb')
        with sqlite3.connect(self.sqlite) as db:
            db.executescript("CREATE TABLE meta(key,value);INSERT INTO meta VALUES('version','authored'),('source_digest','fixture');CREATE TABLE observations(events INTEGER,exposure INTEGER);INSERT INTO observations VALUES(1,0),(0,0);")
        with duckdb.connect(str(self.duck),config={'threads':1,'enable_external_access':False}) as db:
            db.execute('CREATE TABLE meta(key VARCHAR,value VARCHAR)');db.execute('INSERT INTO meta VALUES (?,?)',['duckdb_source',D._source_key(self.sqlite,D._meta(self.sqlite))])
            db.execute('CREATE TABLE observations(events INTEGER,exposure INTEGER)');db.execute('INSERT INTO observations VALUES(1,0),(0,0)')

    def paired(self,query):
        with sqlite3.connect(self.sqlite) as db:expected=db.execute(query).fetchall()
        cols,rows,receipt=D.serve(self.sqlite,query)
        if cols is None:
            with sqlite3.connect(self.sqlite) as db:rows=db.execute(query).fetchall()
        return expected,rows,receipt

    def test_integer_float_and_zero_denominator_keep_sqlite_meaning(self):
        for query in ('SELECT SUM(events)/COUNT(*) FROM observations','SELECT 1.0*SUM(events)/COUNT(*) FROM observations',
                      'SELECT SUM(events)/SUM(exposure) FROM observations'):
            with self.subTest(query=query):
                expected,actual,receipt=self.paired(query)
                self.assertEqual(actual,expected)
                self.assertEqual(receipt['engine'],'sqlite')
                self.assertIn('division_semantics',receipt['fallback'])

    def test_supported_real_and_nullif_arithmetic_can_still_use_duckdb(self):
        for query in ('SELECT SUM(events)/100.0 FROM observations','SELECT 1.0*SUM(events)/NULLIF(SUM(exposure),0) FROM observations'):
            expected,actual,receipt=self.paired(query);self.assertEqual(actual,expected)
            self.assertEqual(receipt['engine'],'duckdb')

    def test_nonfinite_results_cannot_be_accepted_as_numeric_answers(self):
        with self.assertRaises(ValueError):D.run(self.duck,"SELECT CAST('Infinity' AS DOUBLE)")
        with patch.dict('os.environ',{'AGERE_SQL_ENGINE':'sqlite','AGERE_SQL_SHADOW':''}):
            with self.assertRaises(ValueError):Q.execute(None,{'query':'SELECT 1e999','returns':'value'},target=self.sqlite)


class SmallRiskPopulation(unittest.TestCase):
    def test_empty_and_single_member_calibration_do_not_emit_nonfinite_means(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE concept_patient(concept,patient_id,kind)')
            claims=[('A','2030-01',10000,8000,'medical','2030-01-01')]
            with patch.object(vbc,'_claims',return_value=claims):
                scores,empty=vbc.risk_scores(db,{},'2030-01-01','2030-12-31')
                self.assertEqual(scores,{});self.assertEqual(empty['members'],0)
                scores,one=vbc.risk_scores(db,{'A':{'months':['2030-01'],'age':65,'sex':'F'}},'2030-01-01','2030-12-31')
            self.assertEqual(len(one['calibration_by_decile']),1)
            json.dumps({'scores':scores,'calibration':one},allow_nan=False)

