import sqlite3
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import plan_optimizer as O

SUB = "SELECT patient_id FROM concept WHERE concept = 'diabetes' AND kind = 'condition' AND records > 0"
Q = (f"SELECT count(*) FROM patient p WHERE p.patient_id IN ({SUB}) AND p.age > 40 "
     f"AND p.patient_id NOT IN (SELECT patient_id FROM fills f WHERE f.patient_id IN ({SUB.replace('  ', ' ')}) AND f.drug = 'x') AND EXISTS ({SUB})")


_connect = sqlite3.connect


def db():
    d = _connect(':memory:')
    d.executescript("""CREATE TABLE patient(patient_id, age); CREATE TABLE concept(patient_id, concept, kind, records);
    CREATE TABLE fills(patient_id, drug);""")
    d.executemany('INSERT INTO patient VALUES (?,?)', [(f'P{i}', 20 + i) for i in range(40)])
    d.executemany('INSERT INTO concept VALUES (?,?,?,?)', [(f'P{i}', 'diabetes' if i % 2 else 'copd', 'condition', 1) for i in range(40)])
    d.executemany('INSERT INTO fills VALUES (?,?)', [(f'P{i}', 'x') for i in range(0, 40, 3)])
    return d


class Optimizer(unittest.TestCase):
    def test_sql_fusion_hoists_one_materialized_cte_and_keeps_the_result(self):
        fused, r = O.fuse_sql(Q, db())
        self.assertEqual(r['fused'][0]['occurrences'], 3)
        self.assertEqual(fused.lower().count("concept = 'diabetes'"), 1)
        self.assertIn('AS MATERIALIZED', fused)
        d = db()
        self.assertEqual(d.execute(Q).fetchall(), d.execute(fused).fetchall())

    def test_existing_with_clause_is_extended_and_literals_are_not_split(self):
        q = f"WITH base AS (SELECT patient_id FROM patient WHERE age > 30) SELECT count(*) FROM base WHERE patient_id IN ({SUB}) OR patient_id IN ({SUB}) OR patient_id LIKE '(SELECT x FROM y WHERE a AND b AND c AND d AND e AND f AND g)' OR patient_id LIKE '(SELECT x FROM y WHERE a AND b AND c AND d AND e AND f AND g)'"
        fused, r = O.fuse_sql(q, db())
        self.assertTrue(r['fused']); self.assertTrue(fused.startswith('WITH _fused1'))
        d = db(); self.assertEqual(d.execute(q).fetchall(), d.execute(fused).fetchall())

    def test_no_repeat_means_no_change(self):
        q = f"SELECT count(*) FROM patient WHERE patient_id IN ({SUB}) AND age > (SELECT avg(age) FROM patient WHERE age > 21 AND age < 99 AND age is not null)"
        self.assertEqual(O.fuse_sql(q), (q, {'version': O.VERSION, 'fused': []}))

    def test_off_switch(self):
        with patch.dict('os.environ', {'AGERE_PLAN_OPTIMIZER': 'off'}):
            self.assertEqual(O.fuse_sql(Q)[0], Q)

    def test_sql_query_execute_runs_fused_and_falls_back_when_the_rewrite_cannot_stand_alone(self):
        from clinical_core.agere.clinical import sql_query
        with patch.object(sql_query.semantic_layer, 'ensure', return_value='x'), patch.object(sql_query, 'register', lambda c: c), \
                patch.object(sql_query.sqlite3, 'connect', lambda *a, **k: db()):
            out = sql_query.execute('p', {'query': Q, 'returns': 'value'})
            self.assertEqual(out['value'], db().execute(Q).fetchone()[0])
            self.assertEqual(out['query'], Q); self.assertIn('plan_optimizer', out['engine'])
            corr = ("SELECT count(*) FROM patient p WHERE p.age IN (SELECT age FROM patient q WHERE q.patient_id = p.patient_id AND q.age > 0 AND q.age < 200) "
                    "OR p.age IN (SELECT age FROM patient q WHERE q.patient_id = p.patient_id AND q.age > 0 AND q.age < 200)")
            out = sql_query.execute('p', {'query': corr, 'returns': 'value'})
            self.assertEqual(out['value'], db().execute(corr).fetchone()[0])

    def test_reused_source_stage_preserves_cells_types_nulls_and_correlated_aggregates(self):
        d=_connect(':memory:')
        d.executescript("CREATE TABLE raw(entity_id,amount,payload); CREATE TABLE subjects(entity_id);")
        d.executemany('INSERT INTO subjects VALUES (?)',[('a',),('b',),('c',),(None,)])
        d.executemany('INSERT INTO raw VALUES (?,?,?)',[
            ('a',12,'{"kind":"x"}'),('a',-3,'{"kind":"x"}'),('a',12,'{"kind":"x"}'),
            ('b',None,'{"kind":"x"}'),('b','5','{"kind":"x"}'),('z',99,'{"kind":"y"}')])
        q=("WITH source_rows AS (SELECT entity_id,amount FROM raw WHERE json_extract(payload,'$.kind')='x') "
           "SELECT s.entity_id,(SELECT sum(amount) FROM source_rows r WHERE r.entity_id=s.entity_id) total,"
           "(SELECT count(*) FROM source_rows r WHERE r.entity_id=s.entity_id) records,"
           "(SELECT typeof(max(amount)) FROM source_rows r WHERE r.entity_id=s.entity_id) max_type "
           "FROM subjects s ORDER BY s.entity_id")
        optimized,receipt=O.fuse_sql(q,d)
        self.assertIn({'name':'source_rows','kind':'reused_source_stage','occurrences':3},receipt['fused'])
        self.assertEqual(d.execute(q).fetchall(),d.execute(optimized).fetchall())
        self.assertEqual([c[0]for c in d.execute(q).description],[c[0]for c in d.execute(optimized).description])
        self.assertEqual(d.execute(q).fetchall(),[(None,None,0,'null'),('a',21,3,'integer'),('b',5,2,'text'),('c',None,0,'null')])
        self.assertEqual(O.fuse_sql(q)[0],q)  # no live schema proof
        with patch.dict('os.environ',{'AGERE_PLAN_OPTIMIZER':'off'}):self.assertEqual(O.fuse_sql(q,d)[0],q)

    def test_reused_stage_does_not_materialize_volatile_opaque_or_selected_rows(self):
        d=db();d.execute('CREATE VIEW opaque AS SELECT random() n FROM patient')
        for body in ['SELECT random() n FROM patient','SELECT * FROM opaque',
                     'SELECT age n FROM patient LIMIT 1',
                     'SELECT row_number() OVER (ORDER BY age) n FROM patient',
                     'SELECT outer_row.age n FROM patient']:
            q=f'WITH x AS ({body}) SELECT (SELECT count(*) FROM x),(SELECT count(*) FROM x)'
            with self.subTest(body=body):self.assertEqual(O.fuse_sql(q,d)[0],q)
        for hint in ['MATERIALIZED','NOT MATERIALIZED']:
            q=f'WITH x AS {hint} (SELECT age FROM patient) SELECT (SELECT count(*) FROM x),(SELECT max(age) FROM x)'
            self.assertEqual(O.fuse_sql(q,d)[0],q)

    def test_cte_shadowing_uses_scope_identity_and_preserves_the_inner_stage(self):
        d=db()
        q=("WITH x AS (SELECT age FROM patient) SELECT (SELECT sum(age) FROM x),"
           "(SELECT max(age) FROM x),(WITH x AS (SELECT 7 AS age) SELECT max(age) FROM x)")
        optimized,receipt=O.fuse_sql(q,d)
        self.assertEqual(len(receipt['fused']),1)
        self.assertEqual(receipt['fused'][0]['occurrences'],2)
        self.assertEqual(optimized.count('AS MATERIALIZED'),1)
        self.assertEqual(d.execute(q).fetchall(),d.execute(optimized).fetchall())

    def plan(self, nodes): return {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}

    def test_graph_fusion_executes_identical_nodes_once_and_rewires_consumers(self):
        a = {'id': 'a', 'operation': 'sql', 'inputs': [], 'parameters': {'query': 'SELECT 1', 'returns': 'value'}}
        b = {'id': 'b', 'operation': 'sql', 'inputs': [], 'parameters': {'query': 'SELECT 1', 'returns': 'value'}}
        c = {'id': 'answer', 'operation': 'explain', 'inputs': ['b'], 'parameters': {'x': 1}}
        out, r = O.optimize_plan(self.plan([a, b, c]))
        self.assertEqual([n['id'] for n in out['nodes']], ['a', 'answer']); self.assertEqual(out['nodes'][1]['inputs'], ['a'])
        self.assertEqual(r['fused'], [{'node': 'b', 'reads': 'a'}])

    def test_graph_fusion_never_drops_the_terminal_or_duplicates_a_consumer_input(self):
        a = {'id': 'a', 'operation': 'sql', 'inputs': [], 'parameters': {'q': 1}}
        z = {'id': 'z', 'operation': 'sql', 'inputs': [], 'parameters': {'q': 1}}
        p = self.plan([a, z])
        self.assertIs(O.optimize_plan(p)[0], p)
        both = {'id': 'answer', 'operation': 'compare', 'inputs': ['a', 'b'], 'parameters': {}}
        b = {'id': 'b', 'operation': 'sql', 'inputs': [], 'parameters': {'q': 1}}
        p2 = self.plan([a, b, both])
        self.assertIs(O.optimize_plan(p2)[0], p2)


if __name__ == '__main__':
    unittest.main()
