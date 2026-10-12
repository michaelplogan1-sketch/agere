import copy,json,sqlite3,tempfile,shutil,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_reference_applicability as fixture
from clinical_core.contracts import digest
from clinical_core.agere.clinical import reference_lifecycle as L,reference_applicability as R

class Lifecycle(unittest.TestCase):
    acquire=fixture.ReferenceApplicability.acquire
    def setUp(self):
        fixture.ReferenceApplicability.setUp(self)
        for name in ('snapshot','inspect','references'):self.addCleanup(setattr,R,name,getattr(R,name))
        L.install();L.install()
        self.change={'reference_id':self.row['reference']['id'],'expected_revision':0,'event_kind':'local_mapping_revision','event_date':'2026-10-11',
          'actor':'Engineering fixture','actor_kind':'synthetic_test','reason':'Make the assessment question more explicit without asserting clinical review','notice':None,
          'question_changes':{'assessment':'Does the proposed program include a center-based assessment before remote participation and the needed rehabilitation components?'}}
    def event_request(self,change=None):
        change=change or self.change;p=L.preview(self.path,change);return {'change':change,'preview_id':p['id'],'request_id':'change-1'}
    def commit(self,change=None):return L.commit(self.path,self.event_request(change))
    def successor(self,event):return L.successor(self.path,{'manifest_id':self.row['id'],'event_id':event['id'],'expected_task_revision':self.task['revision'],'reason':'Explicitly reconsider original task under refined question wording'})
    def test_preview_is_nonmutating_and_counts_exact_work(self):
        self.acquire();p=L.preview(self.path,self.change)
        self.assertEqual(p,L.preview(self.path,self.change));self.assertEqual(p['impact']['counts']['questions'],6)
        self.assertEqual(p['impact']['counts']['source_contexts'],1);self.assertEqual(L.state(self.path,self.change['reference_id'])['revision'],0)
        self.assertEqual(R.resolve(self.path,self.target)['patient_id'],'p1')
    def test_revision_preserves_original_and_requires_explicit_successor(self):
        self.acquire();event=self.commit();self.assertEqual(event['status'],'superseded_local_mapping')
        self.assertEqual(R.load(self.path,self.row['id']),self.row)
        self.assertEqual(L.archived(self.path,self.row['reference']['id']),self.row['reference'])
        self.assertFalse(R.inspect(self.path,self.task['id'])['sets'][0]['current'])
        self.assertFalse(R.inspect(self.path,self.task['id'])['sets'][0]['questions'][0]['acquisitions'][0]['current'])
        with self.assertRaisesRegex(ValueError,'reassessment'):self.acquire()
        self.assertEqual(len(R.history(self.path,self.task['id'])),1)
        successor=self.successor(event);self.assertNotEqual(successor['id'],self.row['id'])
        self.assertEqual(successor['original_target'],self.row['original_target']);self.assertEqual(successor['owner'],self.row['owner'])
        self.assertEqual(successor['reference']['citation'],self.row['reference']['citation'])
        self.assertEqual(successor['reference']['checked_on'],self.row['reference']['checked_on'])
        self.assertEqual(successor['reference']['questions'][1]['question'],self.change['question_changes']['assessment'])
        state=R.inspect(self.path,self.task['id']);self.assertEqual([x['current'] for x in state['sets']],[False,True])
        self.assertTrue(all(not q['acquisitions'] for q in state['sets'][1]['questions']))
        link=L.inspect(self.path,self.change['reference_id'])['successor_links'][0]
        self.assertEqual(link['new_manifest_id'],successor['id']);self.assertEqual(sum(x['wording_changed'] for x in link['question_map']),1)
        self.assertEqual(link['source_contexts_carried_forward'],0);self.assertEqual(self.successor(event),successor)
    def test_idempotency_and_request_collision(self):
        req=self.event_request();first=L.commit(self.path,req);self.assertEqual(L.commit(self.path,req),first)
        with self.assertRaisesRegex(ValueError,'Request ID'):L.commit(self.path,{**req,'preview_id':'changed'})
    def test_changed_affected_context_requires_new_preview(self):
        req=self.event_request();self.acquire()
        with self.assertRaisesRegex(ValueError,'affected work changed'):L.commit(self.path,req)
        self.assertEqual(L.state(self.path,self.change['reference_id'])['revision'],0)
    def test_concurrent_same_request_commits_once(self):
        req=self.event_request()
        with ThreadPoolExecutor(max_workers=2) as pool:out=list(pool.map(lambda _:L.commit(self.path,req),range(2)))
        self.assertEqual(out[0],out[1]);self.assertEqual(len(L.events(self.path,self.change['reference_id'])),1)
    def test_concurrent_different_changes_cannot_overwrite(self):
        a=self.event_request();b=self.event_request({**self.change,'reason':'Other explicit reason'});b['request_id']='change-2'
        L.commit(self.path,a)
        with self.assertRaises(ValueError):L.commit(self.path,b)
        self.assertEqual(len(L.events(self.path,self.change['reference_id'])),1)
    def test_withdrawal_preserves_question_history_and_blocks_packet_targets(self):
        event=self.commit({**self.change,'event_kind':'local_withdrawal','question_changes':{}})
        self.assertEqual(event['status'],'withdrawn_locally');self.assertIsNone(event['replacement_id'])
        self.assertFalse(any(t['target']['kind']==R.KIND for t in R.packet_followup.targets(self.path,self.task['id'])))
        self.assertEqual(len(R.history(self.path,self.task['id'])),1)
        with self.assertRaises(ValueError):self.successor(event)
    def test_publisher_reports_require_notice_and_never_claim_verification(self):
        change={**self.change,'event_kind':'publisher_retraction_report','question_changes':{}}
        with self.assertRaises(ValueError):self.event_request(change)
        change['notice']={'publisher':'Synthetic fixture publisher','url':'https://example.test/notice','published_on':'2026-10-10','locator':'Fixture only','summary':'Hypothetical reported event for an isolated test; not a real guideline retraction'}
        event=self.commit(change);self.assertEqual(event['status'],'withheld_for_publisher_report');self.assertFalse(event['publisher_event_independently_verified'])
        self.assertFalse(event['clinical_validation']);self.assertEqual(L.archived(self.path,self.change['reference_id']),self.row['reference'])
    def test_local_changes_cannot_impersonate_publisher_notice(self):
        with self.assertRaisesRegex(ValueError,'must not claim'):self.event_request({**self.change,'notice':{'url':'https://example.test'}})
    def test_invalid_fields_dates_authority_and_unchanged_wording_rejected(self):
        cases=[{'expected_revision':True},{'event_date':'2099-01-01'},{'event_date':'20261011'},{'event_date':'2020-01-01'},
          {'clinical_validation':True},{'actor_kind':'clinician_verified'},{'question_changes':{'unknown':'x'}},
          {'question_changes':{}},{'question_changes':{'assessment':self.row['reference']['questions'][1]['question']}}]
        for c in cases:
            with self.subTest(c=c),self.assertRaises(ValueError):self.event_request({**self.change,**c})
    def test_wrong_or_stale_successor_event_and_revision_rejected(self):
        event=self.commit()
        for c in [{'event_id':'0'*64},{'expected_task_revision':0},{'reason':''}]:
            with self.assertRaises(ValueError):L.successor(self.path,{'manifest_id':self.row['id'],'event_id':event['id'],'expected_task_revision':self.task['revision'],'reason':'Explicit replacement',**c})
    def test_store_scope_is_isolated_and_contextvar_resets(self):
        with tempfile.TemporaryDirectory() as temp:
            other=Path(temp)/'other';shutil.copytree(self.path,other);self.commit()
            with ThreadPoolExecutor(max_workers=2) as pool:
                a=pool.submit(R.inspect,self.path,self.task['id']);b=pool.submit(R.inspect,other,self.task['id'])
                self.assertFalse(a.result()['sets'][0]['current']);self.assertTrue(b.result()['sets'][0]['current'])
            self.assertIsNone(L._PATH.get());self.assertEqual(R.references(),L._BASE_REFERENCES())
    def test_catalog_only_lists_available_replacement(self):
        event=self.commit();self.assertEqual([r['id'] for r in L.available(self.path)],[event['replacement_id']])
        self.assertEqual([r['id'] for r in R.inspect(self.path,self.task['id'])['references']],[event['replacement_id']])
    def test_immutable_events_definitions_and_requests(self):
        event=self.commit();self.successor(event);db=L.database(self.path)
        try:
            for table in ['events','definitions','requests','successors']:
                with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM '+table)
        finally:db.close()
    def test_tampered_history_is_rejected(self):
        self.commit();db=L.database(self.path)
        try:
            db.execute('DROP TRIGGER immutable_events_UPDATE');e=L.events(self.path,self.change['reference_id'])[0];e['status']='available_unreviewed';db.execute('UPDATE events SET payload=?',(json.dumps(e),));db.commit()
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'integrity'):L.state(self.path,self.change['reference_id'])
    def test_original_task_and_patients_unchanged(self):
        self.acquire();event=self.commit();self.successor(event)
        self.assertEqual(R.care_readiness.task(self.path,self.task['id']),self.task)
        self.assertEqual(self.records[0]['patient_id'],'p1');self.assertEqual(L.inspect(self.path,self.change['reference_id'])['impact']['counts']['questions'],6)
    def test_changed_impact_during_commit_rolls_back_replacement(self):
        req=self.event_request();original=L.impact;calls=[]
        def changing(path,key):
            out=original(path,key);calls.append(1)
            if len(calls)>1:out['counts']['questions']+=1
            return out
        with patch.object(L,'impact',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'during reference update'):L.commit(self.path,req)
        self.assertEqual(L.events(self.path,self.change['reference_id']),[])
        self.assertEqual(L.payloads(self.path,'reference-lifecycle.sqlite3','definitions'),[])
    def test_publisher_notice_cannot_use_credentials_or_future_notice(self):
        change={**self.change,'event_kind':'publisher_correction_report','question_changes':{},'notice':{'publisher':'fixture','url':'https://example.test','published_on':'2026-10-11','locator':'fixture','summary':'Synthetic notice'}}
        for c in [{'url':'https://user:secret@example.test'},{'published_on':'2099-01-01'},{'url':'http://example.test'}]:
            with self.assertRaises(ValueError):self.event_request({**change,'notice':{**change['notice'],**c}})
    def test_saved_answer_use_becomes_stale_but_original_answer_not_deleted(self):
        from clinical_core.agere.clinical import reference_scope_work as scope
        self.records[0].update(synthetic=True,identity_status='matched',_warehouse_fields=['text'])
        preview=scope.preview(self.path,{'target':self.target,'record_ids':['r1']});selected=scope.select(self.path,{'preview_id':preview['id'],'record_ids':['r1']})
        answer={'id':'d'*64,'status':'complete','patient_id':'p1','context_id':selected['context_id'],'passes':[{}],'findings':[{'citations':[{}]}],'review_steps':[]}
        needs={'id':'e'*64,'summary':{'proposed_questions':0,'questions_with_exact_located_records':0,'unbound_questions':0}};original=scope.source_synthesis.inspect
        with patch.object(scope.source_synthesis,'inspect',side_effect=lambda path,key:copy.deepcopy(answer) if key=='d'*64 else original(path,key)),patch.object(scope.evidence_needs,'prepare',return_value=needs),patch.object(scope.evidence_needs,'inspect',return_value=needs):
            attachment=scope.attach(self.path,{'selection_id':selected['id'],'answer_id':'d'*64})
            self.assertEqual(L.impact(self.path,self.change['reference_id'])['counts']['saved_answers'],1)
            self.commit();self.assertEqual(scope.load(self.path,'answers',attachment['id']),attachment)
            self.assertFalse(scope.listing(self.path,{'target':self.target})['answers'][0]['current'])
            self.assertEqual(scope.source_synthesis.inspect(self.path,'d'*64),answer)
            with self.assertRaises(ValueError):scope.attach(self.path,{'selection_id':selected['id'],'answer_id':'d'*64})

class PacketLifecycle(unittest.TestCase):
    put=fixture.ReferencePacket.put;update=fixture.ReferencePacket.update;save=fixture.ReferencePacket.save
    match=fixture.ReferencePacket.match;context=fixture.ReferencePacket.context;queue=fixture.ReferencePacket.queue
    event=fixture.ReferencePacket.event;credential=fixture.ReferencePacket.credential;bearer=fixture.ReferencePacket.bearer
    run_packet=fixture.ReferencePacket.run_packet;review_packet=fixture.ReferencePacket.review_packet;prepare=fixture.ReferencePacket.prepare
    def setUp(self):
        fixture.ReferencePacket.setUp(self)
        for name in ('snapshot','inspect','references'):self.addCleanup(setattr,R,name,getattr(R,name))
        L.install()
    def test_completed_packet_followup_is_counted_and_stales(self):
        out=self.prepare();key=R.resolve(self.path,self.target)['reference_id']
        self.assertEqual(L.impact(self.path,key)['counts']['packet_followups'],1)
        change={'reference_id':key,'expected_revision':0,'event_kind':'local_withdrawal','event_date':'2026-10-11','actor':'fixture','actor_kind':'synthetic_test','reason':'Disposable withholding test, not a publisher retraction','notice':None,'question_changes':{}}
        p=L.preview(self.path,change);L.commit(self.path,{'change':change,'preview_id':p['id'],'request_id':'packet-change'})
        self.assertFalse(R.packet_followup.inspect(self.path,self.task['id'])['followups'][0]['current'])
        self.assertEqual(R.packet_followup.inspect(self.path,self.task['id'])['followups'][0]['id'],out['id'])
        with self.assertRaises(ValueError):R.packet_followup.prepare(self.path,{**self.req,'request_id':'new-packet-followup'},self.bearer(self.reviewer))

if __name__=='__main__':unittest.main()
