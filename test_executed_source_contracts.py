"""Executed source meanings reach verification without changing SQL or authority."""
import json,sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere.clinical.semantic_analyst import SemanticWarehouse
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json
from clinical_core.contracts import digest


class SourceContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'source.sqlite3'
        with sqlite3.connect(self.path)as db:
            db.executescript("""
                CREATE TABLE meta(key,value);
                INSERT INTO meta VALUES('source_digest','authored-source'),('as_of','2030-05-27'),('version','fixture/1');
                CREATE TABLE patient(patient_id,birth_date,coverage_type,data_segment);
                INSERT INTO patient VALUES('A','1970-01-01','medicare','received'),('B','1970-01-01','medicare','received'),('C','1970-01-01','medicare','duplicate_chart');
                CREATE TABLE death(patient_id,death_date);
                INSERT INTO death VALUES('B','2030-01-01');
                CREATE TABLE fill(patient_id,fill_date,drug_name);
                INSERT INTO fill VALUES('A','2030-05-01','Authored medication'),('B','2030-05-01','Authored medication');
            """)
        self.w=SemanticWarehouse(self.path);self.addCleanup(self.w.close)

    def test_real_cte_and_inherited_view_reads_carry_exact_definition_and_fill_meaning(self):
        self.w.define('2030-05-27')
        sql='WITH chosen AS (SELECT patient_id FROM living_patient) SELECT f.* FROM fill f JOIN chosen USING(patient_id)'
        out=self.w.query(sql)
        self.assertEqual(out['rows'],[{'patient_id':'A','fill_date':'2030-05-01','drug_name':'Authored medication'}])
        c=out['source_contracts'];self.assertEqual(c['source_digest'],'authored-source')
        definitions={x['name']:x for x in c['definitions']}
        self.assertIn('living_patient',definitions)
        self.assertEqual(definitions['living_patient']['query'],self.w._local.defined_views['living_patient'])
        self.assertEqual(definitions['living_patient']['query_digest'],digest(definitions['living_patient']['query']))
        meanings={x['name']:x['meaning'] for x in c['relations']}
        self.assertIn('picked_up = 1',meanings['fill']);self.assertIn('patient',meanings);self.assertIn('death',meanings)

    def test_strings_comments_and_cte_names_are_not_received_source_relations(self):
        self.w.define('2030-05-27')
        out=self.w.query("WITH fill AS (SELECT 'living_patient' AS text) SELECT * FROM fill /* death */")
        self.assertEqual(out['source_contracts']['definitions'],[])
        self.assertEqual(out['source_contracts']['relations'],[])

    def test_authority_is_preserved_after_failure_and_success(self):
        self.w.define('2030-05-27')
        with self.assertRaises(sqlite3.DatabaseError):self.w.query('DELETE FROM patient')
        self.assertEqual(len(self.w.query('SELECT * FROM patient')['rows']),3)
        with self.assertRaises(sqlite3.DatabaseError):self.w.db.execute('DELETE FROM patient')

    def test_plain_semantic_reader_does_not_inherit_product_definitions(self):
        self.assertNotIn('source_contracts',self.w.query('SELECT * FROM fill'))

    def test_support_packet_deduplicates_complete_definitions_and_retains_bindings(self):
        self.w.define('2030-05-27')
        out=self.w.query('SELECT * FROM living_patient');rows=out['rows']
        calls=[{'name':'query_sql','input':{'sql':'SELECT * FROM living_patient'},'result':{**out,'result_view':'base'}},
            {'name':'query_sql','input':{'sql':'SELECT * FROM base'},'result':{**out,'result_view':'final'}}]
        for call in calls:call['sql']=call['input']['sql']
        answer={'table':rows,'table_binding':{'result_view':'final'},'headline':'Received people','facts':{}}
        packets=B.support_packets('List living people',answer,calls,[],'2030-05-27')
        for packet in packets:
            state=uncompact_json(json.loads(packet['text']));definitions=state['executed_source_definitions']
            self.assertEqual(len(definitions),len(out['source_contracts']['definitions'])+len(out['source_contracts']['relations']))
            for c in state['native_calculation_contracts']:
                self.assertEqual(set(c['source_definition_keys']),set(definitions))
            for key,value in definitions.items():self.assertEqual(key,digest(value))
        graph=B.execution_review_graph(calls)
        self.assertEqual(graph['executed_source_definitions'],definitions)
        for node in graph['nodes']:
            self.assertEqual(set(node['parameters']['source_definition_keys']),set(definitions))
        before={p['id']:p['text']for p in packets}
        calls.append({'name':'query_sql','input':{'sql':'SELECT 7'},'result':{'result_view':'unrelated','rows':[{'n':7}],
            'source_contracts':{**out['source_contracts'],'relations':[{'name':'unrelated_source','meaning':'Unrelated context'}]}}})
        after={p['id']:p['text']for p in B.support_packets('List living people',answer,calls,[],'2030-05-27')}
        self.assertEqual(before['patient_A'],after['patient_A']);self.assertNotEqual(before['summary'],after['summary'])


if __name__=='__main__':unittest.main()
