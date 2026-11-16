"""Documentary request lifecycle; fixtures are not clinical/model targets."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import decision_acquisition as A, question_decision as Q
from clinical_core.contracts import digest
import test_question_decision as fixtures
from test_warehouse_interpretation import record


class Acquisition(unittest.TestCase):
    setUp = fixtures.Decision.setUp
    patch = fixtures.Decision.patch
    prepare = fixtures.Decision.prepare
    queue = fixtures.Decision.queue
    put = fixtures.SourceArrival.put

    def start(self):
        self.row = self.prepare(); self.task = self.queue(self.row)
        self.origin = {'decision_id':self.row['id'], 'task_id':self.task['id']}

    def reference(self, key='response', target='missing', **changes):
        r = record(key, 'pharmacy_information_responses', patient_id='p1',
                   available_date='2026-10-09', event_date='2026-10-01', request_record_id=target, **changes)
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (key,'p1',json.dumps(r)))
        return r

    def preview(self):return A.preview(self.retriever,self.origin)

    def save(self, target='missing'):
        p=self.preview();d=next(d for d in p['dependencies'] if d['identity']['target_record_id']==target)
        return A.create(self.retriever,{**self.origin,'preview_id':p['id'],'dependency_id':d['id'],'actor':'fixture operator'})

    def arrive(self, key='missing', **kw):
        return self.put(key,source_table='medication_information_requests',**kw)

    def test_request_reopen_arrival_preserves_question_work_and_history(self):
        self.start();self.reference();saved=self.save()
        self.assertEqual(saved,self.save())
        self.assertEqual(saved['owner'],self.task['owner'])
        self.assertEqual(saved['question'],self.row['question'])
        req={'request_id':saved['result_id']}
        before=A.check(self.retriever,req)
        self.assertEqual(before['status'],'not_received_for_patient')
        self.arrive();after=A.check(self.retriever,req)
        self.assertEqual(after['status'],A.AVAILABLE)
        self.assertEqual(before['dependency_id'],after['dependency_id'])
        self.assertNotEqual(before['current_dependency']['version'],after['current_dependency']['version'])
        self.assertFalse(after['clinical_resolution_established'])
        self.assertEqual(after,A.check(self.retriever,req))
        self.assertEqual(Q.care_readiness.task(self.path,self.task['id']),self.task)
        self.assertEqual(Q.load(self.path,self.row['id']),self.row)
        self.assertEqual(len(Q.reopen(self.retriever,self.row['id'])['followups']),3)

    def test_other_patient_and_forged_dependency_do_not_supply_evidence(self):
        self.start();self.reference();saved=self.save();self.put('missing',patient_id='other')
        # Source ID is indexed to p1 but payload claims another patient: adapter
        # must reject it, never satisfy the dependency.
        with self.assertRaises(ValueError):A.check(self.retriever,{'request_id':saved['result_id']})

    def test_wrong_task_forged_id_and_caller_question_rejected(self):
        self.start();self.reference();p=self.preview()
        for extra in [{'dependency_id':'forged'}, {'task_id':'other'}, {'question':'override'}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                A.create(self.retriever,{**self.origin,'preview_id':p['id'],'dependency_id':p['dependencies'][0]['id'],'actor':'fixture',**extra})

    def test_stale_preview_cannot_create_request(self):
        self.start();self.reference();p=self.preview();self.arrive()
        with self.assertRaisesRegex(ValueError,'preview changed'):
            A.create(self.retriever,{**self.origin,'preview_id':p['id'],'dependency_id':p['dependencies'][0]['id'],'actor':'fixture'})

    def test_target_arrival_and_source_correction_are_dependency_specific(self):
        self.start();self.reference();self.reference('other-response','other-missing')
        first=self.save();second=self.save('other-missing');self.arrive()
        self.assertFalse(A.reopen(self.retriever,{'request_id':first['result_id']})['dependency_unchanged'])
        self.assertTrue(A.reopen(self.retriever,{'request_id':second['result_id']})['dependency_unchanged'])
        self.reference(target='replacement')
        check=A.check(self.retriever,{'request_id':first['result_id']})
        self.assertEqual(check['status'],'reference_changed_requires_new_request')

    def test_date_and_wrong_type_arrivals_do_not_satisfy_request(self):
        self.binding['analysis_window']={'basis':'source_event','start':'2026-09-01','end':'2026-10-10'}
        self.start();self.reference();saved=self.save();req={'request_id':saved['result_id']}
        for fields,status in [({'event_date':'2025-01-01'},'outside_original_window'),({'event_date':None},'unknown_date')]:
            self.arrive(**fields);self.assertEqual(A.check(self.retriever,req)['status'],status)
        self.put('missing',source_table='notes')
        self.assertEqual(A.check(self.retriever,req)['status'],'wrong_reference_type')

    def test_unmapped_and_invalid_reference_require_contract_review(self):
        self.start();self.reference(target=23);p=self.preview()
        self.assertEqual(p['dependencies'][0]['observation']['status'],'invalid_reference_value')
        self.reference(unmapped_record_id='missing-other')
        p=self.preview();d=next(d for d in p['dependencies'] if d['identity'].get('field')=='unmapped_record_id')
        self.assertEqual(d['observation']['status'],'reference_type_unmapped')
        self.assertEqual(d['requested_action'],'review_reference_contract')

    def test_absent_direct_citation_retains_finding_dependency(self):
        self.start()
        with sqlite3.connect(self.path/'stack.sqlite3') as db:db.execute("DELETE FROM records WHERE id='r1'")
        p=self.preview();d=p['dependencies'][0]
        self.assertEqual(d['direct_finding_ids'],[self.answer['findings'][0]['id']])
        saved=self.save('r1');self.put('r1')
        self.assertEqual(A.check(self.retriever,{'request_id':saved['result_id']})['status'],A.AVAILABLE)

    def test_owner_revision_requires_new_preview_and_is_disclosed_on_check(self):
        self.start();self.reference();saved=self.save();p=self.preview()
        with patch.object(Q,'bound_task',return_value={**self.task,'revision':99,'owner':{'id':'new-owner'}}):
            with self.assertRaisesRegex(ValueError,'preview changed'):
                A.create(self.retriever,{**self.origin,'preview_id':p['id'],'dependency_id':p['dependencies'][0]['id'],'actor':'fixture'})
            x=A.check(self.retriever,{'request_id':saved['result_id']})
            self.assertFalse(x['work_binding_unchanged']);self.assertEqual(x['current_owner']['id'],'new-owner')

    def test_no_arrival_needed_to_expose_preexisting_broken_reference(self):
        self.reference();self.binding['source_versions']['records']['response']=digest(self.reference())
        self.start();self.assertEqual(len(self.preview()['dependencies']),1)

    def test_race_and_forged_saved_followup_rejected(self):
        self.start();self.reference();p=self.preview()
        with patch.object(A,'preview',side_effect=[p,{**p,'task_revision':999}]):
            with self.assertRaisesRegex(ValueError,'during request'):
                A.create(self.retriever,{**self.origin,'preview_id':p['id'],'dependency_id':p['dependencies'][0]['id'],'actor':'fixture'})
        saved=self.save();req={'request_id':saved['result_id']};v=A.reopen(self.retriever,req)
        with patch.object(A,'reopen',side_effect=[v,{**v,'inventory_id':'changed'}]):
            with self.assertRaisesRegex(ValueError,'during acquisition'):A.check(self.retriever,req)
        with self.assertRaises(ValueError):A.reopen(self.retriever,{'request_id':'0'*64})


if __name__=='__main__':unittest.main()
