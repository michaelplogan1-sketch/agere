import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import retrieval_coverage as C


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript("CREATE TABLE records(id TEXT,patient TEXT,stream TEXT); INSERT INTO records VALUES ('n','P-1','notes'),('p','P-1','coverage'),('q','P-1','coverage'),('other','P-2','notes');")
        self.r=SimpleNamespace(db=self.db,meta={'snapshot':{'as_of':'2029-08-15'}})
        row={'source':{'source_table':'notes','record_id':'n'},'rrf_selected_rank':1,'reranked_selected_rank':None}
        self.report={'id':'example','request':{'patient':'P-1','analysis_window':{'basis':'source_event','start':'2029-01-01','end':'2029-08-15'}},'identity':{'source_run':'s','source_digest':'d'},'candidates':[row,row]}
        p=patch.object(C.R,'inspect',return_value=self.report);p.start();self.addCleanup(p.stop)

    def test_missing_category_is_visible_without_relevance_claim(self):
        r=C.inspect(self.r,{'id':'example'})
        self.assertEqual(r['received_records'],3);self.assertEqual(r['categories_outside_candidates'],1)
        self.assertEqual(r['categories_outside_final_selection'],2)
        self.assertIn('outside any narrower query date window',r['inventory_scope'])

    def test_passages_do_not_double_count_records(self):
        r=C.inspect(self.r,{'id':'example'});n=next(x for x in r['categories'] if x['source_category']=='notes')
        self.assertEqual((n['candidates'],n['rrf_selected'],n['reranked_selected']),(1,1,0))

    def test_inventory_changes_produce_new_receipt(self):
        before=C.inspect(self.r,{'id':'example'})
        self.db.execute("INSERT INTO records VALUES ('third','P-1','coverage')")
        self.assertNotEqual(before['id'],C.inspect(self.r,{'id':'example'})['id'])

    def test_patient_cannot_be_overridden(self):
        with self.assertRaises(ValueError):C.inspect(self.r,{'id':'example','patient':'P-2'})

    def test_population_is_not_presented_as_one_patient_inventory(self):
        self.report['request']['patient']=None
        with self.assertRaises(ValueError):C.inspect(self.r,{'id':'example'})

    def test_absent_candidate_category_rejected(self):
        self.db.execute("DELETE FROM records WHERE stream='notes'")
        with self.assertRaisesRegex(ValueError,'absent'):C.inspect(self.r,{'id':'example'})


if __name__=='__main__':unittest.main()
