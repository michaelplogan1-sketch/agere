"""Independent rate/ranking outputs retain a shared explicit calendar scope."""
import sqlite3
from test_composition_language import CompositionFixture
from clinical_core.agere.clinical import request_language as L, request_program as P, sql_query

class CompoundRanking(CompositionFixture):
    def setUp(self):
        super().setUp()
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute('ALTER TABLE payer_pharmacy_claim ADD COLUMN reject_code TEXT')
            db.execute("UPDATE payer_pharmacy_claim SET reject_code='N1' WHERE claim_status='rejected'")
            db.executemany('INSERT INTO payer_pharmacy_claim(patient_id,claim_status,counts_as_fill,fill_date,reject_code) VALUES(?,?,?,?,?)',
                           [('A','rejected',0,'2028-10-01','OLD')]*4)
        from clinical_core.agere.clinical import request_plan as R
        self.catalog=R.Catalog.load(self.path)
    def execute_parts(self,q):
        program=L.parse_program(self.catalog,q)
        c=P.prepare(self.catalog,q,program)
        self.assertTrue(all(not n['unresolved'] for n in c['nodes']))
        return [sql_query.execute(self.path,{'query':n['contract']['query'],'returns':n['contract']['returns']},target=self.catalog.target) for n in c['nodes']]
    def test_contracted_ranking_inherits_only_explicit_same_source_calendar_scope(self):
        a,b=self.execute_parts("What share of pharmacy claims were rejected in 2029, and what's the top reject code?")
        self.assertEqual(a['value'],40)
        self.assertEqual(b['rows'],[{'reject_code':'N1','count':2}])
    def test_separately_stated_ranking_period_is_preserved(self):
        a,b=self.execute_parts('What share of pharmacy claims were rejected in 2029, and what is the most common reject code in 2028?')
        self.assertEqual(a['value'],40)
        self.assertEqual(b['rows'],[{'reject_code':'OLD','count':4}])
    def test_quote_mask_keeps_contractions_but_hides_quoted_commands(self):
        self.assertEqual(L.syntax_text("what's top?"),"what's top?")
        masked=L.syntax_text("label 'A and how many' and count patients")
        self.assertNotIn('A and how many',masked)
        self.assertIn('and count patients',masked)
    def test_unknown_ranking_dimension_is_not_dropped(self):
        from clinical_core.agere.clinical import request_plan as R
        with self.assertRaises(R.PlanError):
            L.parse_program(self.catalog,"What share of pharmacy claims were rejected in 2029, and what's the top frobnicator?")
