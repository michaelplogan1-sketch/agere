import copy,json,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_care_capabilities as fixture
from clinical_core.contracts import digest
from clinical_core.agere.clinical import reference_applicability as R,care_capabilities as care

class ReferenceApplicability(unittest.TestCase):
    def setUp(self):
        fixture.CareCapabilities.setUp(self)
        for module,name in [(R.D,'resolve_target'),(R.D,'save'),(R.D,'acquisition_context'),(R.packet_followup,'targets')]:self.addCleanup(setattr,module,name,getattr(module,name))
        R.install();R.install()
        plan=care.prepare(self.path,self.req);self.task=care.enqueue(self.path,{'plan_id':plan['id'],'option_id':plan['options'][0]['id'],'actor':'fixture'})['task']
        self.request={'task_id':self.task['id'],'expected_revision':self.task['revision'],'target':R.origin_targets(self.path,self.task['id'])[0]['target'],
            'reference_id':R.references()[0]['id'],'reason':'Engineering integration fixture, not a clinical applicability judgment'}
        self.row=R.materialize(self.path,self.request);self.target=R.target_for(self.row,self.row['questions'][0])
        self.records=[{'record_id':'r1','patient_id':'p1','source_table':'care_notes','available_date':'2026-10-01','event_date':'2026-09-30','text':'Synthetic review note'}]
        self.contexts={}
        def create(path,pid,question,ids):
            c={'patient_id':pid,'parameters':{'question':question},'record_scope':{'record_ids':ids},'model_context':{'evidence':[]},'dispatch':{'source_review_allowed':True},'omissions':[]};c['id']=digest(c);self.contexts[c['id']]=c;return copy.deepcopy(c)
        for name,fn in [('received',lambda path,pid:(copy.deepcopy(self.records),{'source_digest':'fixture','snapshot':{'as_of':'2026-10-10'}})),('create',create),('verified',lambda path,key:copy.deepcopy(self.contexts[key])),('inspect',lambda path,key:copy.deepcopy(self.contexts[key]))]:
            p=patch.object(R.record_context,name,side_effect=fn);p.start();self.addCleanup(p.stop)
    def acquire(self,**extra):return R.D.acquisition_context(self.path,{'target':self.target,'record_ids':['r1'],**extra})
    def test_exact_reference_original_question_owner_and_question_bases(self):
        self.assertEqual(R.materialize(self.path,self.request),self.row)
        self.assertEqual(self.row['owner'],self.task['owner']);self.assertEqual(len(self.row['questions']),6)
        self.assertEqual(self.row['reference']['kind'],'guideline_recommendation');self.assertIsNone(self.row['reference']['individual_treatment_effect'])
        self.assertIn(self.row['original_target']['question'],R.resolve(self.path,self.target)['question'])
        self.assertEqual(len(R.history(self.path,self.task['id'])),1)
        self.assertFalse(self.row['reference_currency']['current_at_patient_decision'])
    def test_acquisition_preserves_exact_question_patient_and_cutoff(self):
        a=self.acquire();c=self.contexts[a['context_id']]
        self.assertEqual(c['parameters']['question'],R.resolve(self.path,self.target)['question']);self.assertEqual(c['patient_id'],'p1')
        self.assertEqual(a['decision_cutoff'],'2026-10-10');self.assertFalse(a['meaning_resolved'])
        self.assertEqual(len(R.acquisitions(self.path,self.target)),1)
        self.assertTrue(R.inspect(self.path,self.task['id'])['sets'][0]['questions'][0]['acquisitions'][0]['current'])
    def test_no_documentary_actor_can_establish_clinical_applicability(self):
        for actor in ['operator','machine','synthetic_test']:
            with self.assertRaisesRegex(ValueError,'cannot be established'):R.D.save(self.path,{'target':self.target,'actor_kind':actor,'disposition':'addressed'})
        self.assertFalse(R.D.inspect(self.path,self.target)['documentary_question_addressed'])
    def test_unresolved_observation_keeps_reference_dependency(self):
        R.D.save(self.path,{'target':self.target,'expected_revision':0,'request_id':'note','actor':'engineering fixture','actor_kind':'synthetic_test','disposition':'unresolved','rationale':'No clinical reviewer has adjudicated this reference','bindings':[]})
        s=R.D.inspect(self.path,self.target);self.assertEqual(s['state'],'machine_observation');self.assertEqual(s['target']['reference_id'],self.request['reference_id'])
    def test_packet_followup_gets_exact_derived_questions(self):
        rows=[r for r in R.packet_followup.targets(self.path,self.task['id']) if r['target']['kind']==R.KIND]
        self.assertEqual(len(rows),6);self.assertTrue(all(r['state']=='applicability_unresolved' for r in rows))
    def test_rejects_foreign_target_and_revision_or_authority(self):
        for patcher in [{'target':{**self.request['target'],'plan_id':'0'*64}},{'expected_revision':0},{'expected_revision':True},{'reference_id':'0'*64},{'reason':''},{'clinical_execution_authorized':True}]:
            with self.assertRaises(ValueError):R.materialize(self.path,{**self.request,**patcher})
    def test_exact_question_and_manifest_are_required(self):
        for v in [{'question_id':'0'*64},{'manifest_id':'0'*64},{'kind':'wrong'}]:
            with self.assertRaises(ValueError):R.resolve(self.path,{**self.target,**v})
    def test_reference_changes_retain_stale_history_and_remove_packet_target(self):
        refs=R.references();refs[0]['reported_strength']='changed';refs[0]['id']='1'*64
        with patch.object(R,'references',return_value=refs):
            with self.assertRaises(ValueError):self.acquire()
            self.assertFalse(R.inspect(self.path,self.task['id'])['sets'][0]['current'])
            self.assertFalse(any(r['target']['kind']==R.KIND for r in R.packet_followup.targets(self.path,self.task['id'])))
    def test_implementation_change_stales_history(self):
        with patch.object(R,'identity',return_value='changed'):
            self.assertFalse(R.inspect(self.path,self.task['id'])['sets'][0]['current'])
    def test_patient_source_change_stales_original_plan(self):
        self.answer['question']='changed original source answer'
        with self.assertRaises(ValueError):self.acquire()
        self.assertFalse(R.inspect(self.path,self.task['id'])['sets'][0]['current'])
    def test_cancelled_task_preserves_history(self):
        care.task_event(self.path,{'task_id':self.task['id'],'expected_revision':self.task['revision'],'action':'cancel','actor':'fixture','note':'Fixture','request_id':'cancel'})
        with self.assertRaisesRegex(ValueError,'cancelled'):self.acquire()
        self.assertFalse(R.inspect(self.path,self.task['id'])['sets'][0]['current'])
    def test_future_or_foreign_source_rejected(self):
        for change in [{'available_date':'2027-01-01'},{'patient_id':'other'}]:
            self.records[0].update(change)
            with self.assertRaises(ValueError):self.acquire()
        self.assertEqual(R.acquisitions(self.path,self.target),[])
    def test_context_change_preserves_stale_preparation(self):
        a=self.acquire();self.contexts[a['context_id']]['extra']='changed'
        self.assertFalse(R.inspect(self.path,self.task['id'])['sets'][0]['questions'][0]['acquisitions'][0]['current'])
    def test_failed_context_verification_leaves_no_receipt(self):
        with patch.object(R.record_context,'verified',side_effect=ValueError('partial write')):
            with self.assertRaises(ValueError):self.acquire()
        self.assertEqual(R.acquisitions(self.path,self.target),[])
    def test_concurrent_idempotent_save_and_acquisition(self):
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:R.materialize(self.path,self.request),range(2)))
        self.assertEqual(rows[0],rows[1])
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:self.acquire(),range(2)))
        self.assertEqual(rows[0],rows[1]);self.assertEqual(len(R.acquisitions(self.path,self.target)),1)
    def test_changed_snapshot_during_save_rejected(self):
        original=R.snapshot;calls=[]
        def changed(path,req):
            out=original(path,req);calls.append(1)
            if len(calls)>1:out[1]['owner']={'kind':'agent','id':'changed'}
            return out
        with patch.object(R,'snapshot',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'changed during'):R.materialize(self.path,self.request)
    def test_immutable_storage_and_task_history_unchanged(self):
        self.acquire();db=R.db_open(self.path)
        try:
            for table in ['manifests','acquisitions']:
                with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM '+table)
        finally:db.close()
        self.assertEqual(R.care_readiness.task(self.path,self.task['id']),self.task)
    def test_reference_scope_answer_returns_to_original_derived_question(self):
        from clinical_core.agere.clinical import reference_scope_work as scope
        self.records[0].update(synthetic=True,identity_status='matched',_warehouse_fields=['text'])
        preview=scope.preview(self.path,{'target':self.target,'record_ids':['r1']})
        selection=scope.select(self.path,{'preview_id':preview['id'],'record_ids':['r1']})
        answer={'id':'d'*64,'status':'complete','patient_id':'p1','context_id':selection['context_id'],'passes':[{}],
                'findings':[{'citations':[{}]}],'review_steps':[]}
        needs={'id':'e'*64,'summary':{'proposed_questions':0,'questions_with_exact_located_records':0,'unbound_questions':0}}
        original=scope.source_synthesis.inspect
        with patch.object(scope.source_synthesis,'inspect',side_effect=lambda path,key:copy.deepcopy(answer) if key=='d'*64 else original(path,key)), patch.object(scope.evidence_needs,'prepare',return_value=needs), patch.object(scope.evidence_needs,'inspect',return_value=needs):
            out=scope.attach(self.path,{'selection_id':selection['id'],'answer_id':'d'*64})
            self.assertEqual(out['question'],R.resolve(self.path,self.target)['question'])
            self.assertFalse(out['requirement_resolved']);self.assertTrue(scope.listing(self.path,{'target':self.target})['answers'][0]['current'])
            answer['context_id']='wrong'
            with self.assertRaisesRegex(ValueError,'exact reference selection'):scope.attach(self.path,{'selection_id':selection['id'],'answer_id':'d'*64})
    def test_later_publication_cannot_apply_before_original_cutoff(self):
        refs=R.references();refs[0]['published_on']='2030-01-01'
        with patch.object(R,'references',return_value=refs):
            with self.assertRaisesRegex(ValueError,'published after'):R.materialize(self.path,self.request)
    def test_tampered_manifest_rejected(self):
        db=R.db_open(self.path)
        try:
            db.execute('DROP TRIGGER immutable_manifests_UPDATE');bad=copy.deepcopy(self.row);bad['patient_id']='wrong';db.execute('UPDATE manifests SET payload=?',(json.dumps(bad),));db.commit()
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'integrity'):R.load(self.path,self.row['id'])
import test_packet_followup as packet_fixture
class ReferencePacket(unittest.TestCase):
    put=packet_fixture.Followup.put
    update=packet_fixture.Followup.update
    save=packet_fixture.Followup.save
    match=packet_fixture.Followup.match
    context=packet_fixture.Followup.context
    queue=packet_fixture.Followup.queue
    event=packet_fixture.Followup.event
    credential=packet_fixture.Followup.credential
    bearer=packet_fixture.Followup.bearer
    run_packet=packet_fixture.Followup.run_packet
    review_packet=packet_fixture.Followup.review_packet
    prepare=packet_fixture.Followup.prepare
    def setUp(self):
        packet_fixture.Followup.setUp(self)
        for module,name in [(R.D,'resolve_target'),(R.D,'save'),(R.D,'acquisition_context'),(R.packet_followup,'targets')]:self.addCleanup(setattr,module,name,getattr(module,name))
        R.install()
        m=R.materialize(self.path,{'task_id':self.task['id'],'expected_revision':self.task['revision'],
            'target':self.target,'reference_id':R.references()[0]['id'],'reason':'Disposable authenticated packet integration fixture; no clinical review'})
        self.target=R.target_for(m,m['questions'][0]);resolved=R.resolve(self.path,self.target)
        self.acquisition['target']=resolved;self.req['target']=self.target
        p=patch.object(R.record_context,'verified',return_value={'patient_id':'p1','parameters':{'question':resolved['question']}});p.start();self.addCleanup(p.stop)
    def test_authenticated_packet_followup_binds_reference_question_and_source(self):
        out=self.prepare();self.assertEqual(out['target']['target'],self.target)
        self.assertEqual(out['acquisition']['target']['reference_id'],R.references()[0]['id'])
        self.assertFalse(out['requirement_resolved']);self.assertFalse(out['care_delivered'])
        self.assertEqual(len(R.acquisitions(self.path,self.target)),1)
        self.assertTrue(R.packet_followup.inspect(self.path,self.task['id'])['followups'][0]['current'])
    def test_packet_worker_cannot_submit_reference_followup(self):
        from clinical_core.agere.clinical import internal_execution
        with self.assertRaises(PermissionError):R.packet_followup.prepare(self.path,self.req,self.bearer())
        self.assertEqual(R.acquisitions(self.path,self.target),[])

if __name__=='__main__':unittest.main()
