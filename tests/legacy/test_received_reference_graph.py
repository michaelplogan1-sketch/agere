import unittest
from clinical_core.agere.clinical import received_reference_graph as g

def record(key,stream,**fields):
    return {'record_id':key,'patient_id':'p1','source_table':stream,'event_date':'2026-01-01','available_date':'2026-01-02','_warehouse_fields':list(fields),**fields}

class Graph(unittest.TestCase):
    def rows(self):return [record('c','diagnostic_access_contacts',order_record_id='o',packet_record_id='p'),record('o','diagnostic_orders'),record('p','diagnostic_referral_packets',order_record_id='o')]
    def build(self,rows,roots=None,**kwargs):return g.build(rows,'p1','2026-10-10',roots or ['c'],**kwargs)
    def test_forward_reverse_edges_and_cycles(self):
        out=self.build(self.rows());self.assertEqual(out['record_ids'],['c','o','p']);self.assertEqual(len(out['edges']),3)
        self.assertEqual(self.build(self.rows(),['o'])['record_ids'],out['record_ids'])
        self.assertTrue(all(not e['clinical_relationship_validated'] for e in out['edges']))
    def test_future_unknown_and_wrong_type_never_expand(self):
        for change,reason in [({'available_date':'2027-01-01'},'not_received_by_cutoff'),({'available_date':None},'not_received_by_cutoff'),({'source_table':'medication_orders'},'wrong_reference_type')]:
            rows=self.rows();rows[1].update(change);out=self.build(rows)
            self.assertNotIn('o',out['record_ids']);self.assertIn(reason,{r['reason'] for r in out['issues']})
    def test_missing_and_abbreviated_identifiers_not_guessed(self):
        rows=self.rows();rows[0]['order_record_id']='o…';out=self.build(rows)
        self.assertIn('not_received_for_patient',{r['reason'] for r in out['issues']})
    def test_unmapped_reference_and_unmarked_field_do_not_add_edges(self):
        rows=self.rows();rows[0]['_warehouse_fields']=['extra_record_id'];rows[0]['extra_record_id']='o';out=self.build(rows)
        self.assertEqual(out['record_ids'],['c']);self.assertEqual(out['issues'][0]['reason'],'reference_type_unmapped')
    def test_duplicate_ids_and_cross_patient_records_rejected(self):
        rows=self.rows()
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.build(rows+[rows[0]])
        rows[1]['patient_id']='foreign'
        with self.assertRaisesRegex(ValueError,'same-patient'):self.build(rows)
    def test_lists_self_references_and_invalid_values(self):
        rows=self.rows();rows[1]['_warehouse_fields']=['supporting_record_ids'];rows[1]['supporting_record_ids']=['o',{},None,'missing'];out=self.build(rows)
        self.assertEqual({r['reason'] for r in out['issues']},{'self_reference','invalid_reference_value','not_received_for_patient'})
        self.assertEqual(len(out['issues']),4)
    def test_cutoff_applies_to_sources_and_roots(self):
        rows=self.rows();rows[0]['available_date']='2027-01-01'
        with self.assertRaisesRegex(ValueError,'decision date'):self.build(rows)
        out=self.build(rows,['o']);self.assertNotIn('c',out['record_ids'])
    def test_component_bound_rejects_without_truncation(self):
        with self.assertRaisesRegex(ValueError,'no partial'):self.build(self.rows(),maximum=2)
        with self.assertRaisesRegex(ValueError,'no partial'):self.build(self.rows(),['c','o','p'],maximum=2)

if __name__=='__main__':unittest.main()
