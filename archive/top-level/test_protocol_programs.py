"""Any registered protocol can supply persistent review work without new clinical rules."""
import copy,json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import protocol_programs as P,care_protocols as C,program_work as W
from clinical_core.agere.clinical.graph_workbench import protocol_work_examples
from clinical_core.agere.clinical.economical_planner import local_plan
import test_care_protocols as fixture


class ProtocolPrograms(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ProtocolCache();self.f.setUp();self.addCleanup(self.f.tearDown);self.root=self.f.root
        self.program='protocol:affordability_action_review@1'

    def test_catalog_and_examples_follow_registered_protocols(self):
        self.assertEqual({r['protocol_id'] for r in P.catalog()},{C.qualified(s) for s in C.definitions().values()})
        for ex in protocol_work_examples():
            graph,route=local_plan(ex['question'],fixture.ASOF)
            self.assertEqual(route,'registered_example');self.assertTrue(P.handles(graph['nodes'][-1]['parameters']['program']))
        with self.assertRaises(ValueError):W.definition('protocol:unknown')

    def test_complete_assessment_retains_unresolved_and_source_links(self):
        self.f.review(self.f.a)
        r=W.prepare(self.root,self.program,['p','q']);self.assertEqual(r['counts']['selected'],2);self.assertEqual(r['counts']['assessed'],2)
        self.assertEqual(r['assessment_status_counts'],{'review_criterion_supported':1,'insufficient_evidence':1})
        db=W.database(self.root)
        try:
            saved=W.load(db,'work_proposals',r['proposal_id']);rows=[W.load(db,'work_assessments',k) for k in saved['assessment_ids']]
            for a in rows:
                self.assertFalse(a['clinical_eligibility_established']);self.assertEqual(C.inspect(self.root,a['protocol_context']['receipt_id'])['patient_id'],a['patient_id'])
            supported=next(a for a in rows if a['patient_id']=='p');self.assertTrue(supported['evidence'])
            unresolved=next(a for a in rows if a['patient_id']=='q');self.assertIsNone(unresolved['protocol_context']['trigger'])
        finally:db.close()
        W.materialize(self.root,r['proposal_id'],'ISOLATED TEST FIXTURE');self.assertEqual(W.list_items(self.root)['matched'],1)

    def test_changed_source_review_prevents_stale_work_event(self):
        self.f.review(self.f.a);r=W.prepare(self.root,self.program,['p'])
        W.materialize(self.root,r['proposal_id'],'ISOLATED TEST FIXTURE');item=W.list_items(self.root)['items'][0]
        self.f.review(self.f.a,status='unresolved',revision=1)
        with self.assertRaises(W.WorkConflict):W.change(self.root,{'item_id':item['id'],'expected_revision':item['revision'],'action':'assign','actor':'ISOLATED TEST FIXTURE','request_id':'changed','data':{'owner':'Fixture reviewer','due_date':None}})
        new=W.prepare(self.root,self.program,['p']);update=W.materialize(self.root,new['proposal_id'],'ISOLATED TEST FIXTURE')
        self.assertEqual(update['changes']['evidence_changed'],1);self.assertTrue(W.list_items(self.root)['items'][0]['reassessment_required'])

    def test_definition_change_blocks_old_proposal(self):
        self.f.review(self.f.a);r=W.prepare(self.root,self.program,['p']);spec=copy.deepcopy(C.definition('affordability_action_review@1'));spec['version']='changed';spec['hash']='changed'
        with patch.object(C,'definition',return_value=spec):
            with self.assertRaises(W.WorkConflict):W.materialize(self.root,r['proposal_id'],'ISOLATED TEST FIXTURE')

    def test_protocol_adapter_does_not_write_clinical_analyses(self):
        db=sqlite3.connect(self.root/'stack.sqlite3');before=db.execute('select * from analyses order by patient,operator').fetchall()
        W.prepare(self.root,self.program,['p','q']);self.assertEqual(before,db.execute('select * from analyses order by patient,operator').fetchall());db.close()

    def test_program_preparation_identity_changes_with_review(self):
        first=W.preparation_identity(self.root,[self.program]);self.f.review(self.f.a)
        self.assertNotEqual(first,W.preparation_identity(self.root,[self.program]))


if __name__=='__main__':unittest.main()
