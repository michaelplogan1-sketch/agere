"""Patient-fit source work preserves questions without manufacturing confirmation."""
import copy,json,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
import test_patient_context as fixture
from clinical_core.contracts import digest
from clinical_core.agere.clinical import patient_fit_work as f,patient_context as p
from clinical_core.agere.clinical import internal_execution as execution,followup_answers

class PatientFit(unittest.TestCase):
    record=fixture.PatientContext.record
    context=fixture.PatientContext.context
    match=fixture.PatientContext.match
    queue=fixture.PatientContext.queue
    event=fixture.PatientContext.event
    def setUp(self):
        fixture.PatientContext.setUp(self)
        for module,name in [(f.dispositions,'resolve_target'),(f.dispositions,'acquisition_context'),(f.dispositions,'save'),(f.packet_followup,'targets')]:
            self.addCleanup(setattr,module,name,getattr(module,name))
        f.install();f.install()
        self.plan=self.match();self.task=p.task_event(self.path,self.event(self.queue(self.plan)))['task']
        self.request={'task_id':self.task['id'],'expected_revision':self.task['revision']}
        self.manifest=f.materialize(self.path,self.request)
        self.q=next(q for q in self.manifest['questions'] if q['kind']=='helper_scope');self.target=f.target_for(self.manifest,self.q)
        self.contexts={}
        def create(path,patient,question,ids):
            c={'patient_id':patient,'parameters':{'question':question},'record_scope':{'record_ids':sorted(ids)},'omissions':[],
               'dispatch':{'source_review_allowed':True},'model_context':{'sources':sorted(ids)}}
            c['id']=digest(c);self.contexts[c['id']]=c;return copy.deepcopy(c)
        for name,fn in [('create',create),('inspect',lambda path,key:copy.deepcopy(self.contexts[key])),('verified',lambda path,key:copy.deepcopy(self.contexts[key]))]:
            patcher=patch.object(f.record_context,name,side_effect=fn);patcher.start();self.addCleanup(patcher.stop)
    def acquire(self,**extra):return f.dispositions.acquisition_context(self.path,{'target':self.target,'record_ids':['r1'],**extra})
    def view(self):return f.inspect(self.path,self.task['id'])
    def question(self):return next(q for q in self.view()['sets'][0]['questions'] if q['id']==self.q['id'])
    def credential(self,roles):
        return execution.provision(self.path,{'principal_id':'Synthetic patient-fit test','identity_kind':'synthetic_test',
            'buyer_id':'buyer1','service_id':'pharmacy','roles':roles,'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()})
    def packet(self):
        worker=self.credential(['packet_worker']);reviewer=self.credential(['packet_reviewer'])
        run=execution.run(self.path,{**self.request,'request_id':'run'},'Bearer '+worker['token'])['run']
        execution.review(self.path,{'run_id':run['id'],'expected_revision':0,'request_id':'review','disposition':'needs_more_evidence',
            'note':'Synthetic engineering test; no clinical review','follow_up_on':datetime.now(timezone.utc).date().isoformat()},'Bearer '+reviewer['token'])
        return run,reviewer
    def test_exact_questions_owner_scope_and_idempotent_manifest(self):
        self.assertEqual(f.materialize(self.path,self.request),self.manifest)
        self.assertEqual(len(self.view()['sets']),1);self.assertEqual(self.manifest['owner'],self.task['owner'])
        self.assertEqual(self.q['source_reports'][0]['fields']['permission_to_contact_helper'],False)
        self.assertEqual(self.q['review_question'],f.resolve(self.path,self.target)['question'])
        self.assertFalse(self.view()['current_patient_consent_established'])
    def test_source_context_tracks_progress_without_changing_packet(self):
        before=execution.binding(self.path,self.task['id'])[2];out=self.acquire()
        self.assertEqual(self.question()['work_state'],'source_context_prepared')
        self.assertEqual(self.question()['acquisitions'][0]['acquisition'],out)
        self.assertEqual(before,execution.binding(self.path,self.task['id'])[2]);self.assertFalse(self.question()['requirement_resolved'])
    def test_prepare_without_sources_does_not_fill_goals_or_consent(self):
        for q in self.manifest['questions']:
            if not q['record_ids']:
                with self.assertRaisesRegex(ValueError,'No confirming source'):self.acquire(target=f.target_for(self.manifest,q))
                self.assertEqual(f.acquisitions(self.path,f.target_for(self.manifest,q)),[])
    def test_foreign_or_unrelated_record_and_duplicate_selection_rejected(self):
        for ids in (['r2'],['foreign'],['r1','r1'],[],['r1']*13):
            with self.assertRaises(ValueError):self.acquire(record_ids=ids)
        self.assertEqual(self.question()['acquisitions'],[])
    def test_wrong_question_or_manifest_rejected(self):
        for target in ({**self.target,'question_id':'x'*64},{**self.target,'manifest_id':'x'*64}):
            with self.assertRaises(ValueError):f.resolve(self.path,target)
    def test_wrong_revision_cancelled_or_unbound_task_rejected(self):
        for v in (0,True,'2'):
            with self.assertRaises(ValueError):f.materialize(self.path,{**self.request,'expected_revision':v})
        p.task_event(self.path,{**self.event(self.task),'request_id':'cancel','action':'cancel'})
        with self.assertRaisesRegex(ValueError,'cancelled'):f.materialize(self.path,self.request)
        self.assertFalse(self.view()['sets'][0]['current'])
    def test_unbound_task_has_no_patient_fit_work_or_added_targets(self):
        plain=p.prepare_match(self.path,self.req);task=self.queue(plain)
        # Same patient/service task is deliberately refreshed to the unbound plan.
        with self.assertRaisesRegex(ValueError,'no bound'):f.materialize(self.path,{'task_id':task['id'],'expected_revision':task['revision']})
        self.assertFalse(any(t['target']['kind']==f.KIND for t in f.packet_followup.targets(self.path,task['id'])))
    def test_changed_source_preserves_history_and_blocks_new_acquisition(self):
        self.acquire();self.records[0]['permission_to_contact_helper']=True
        row=self.question();self.assertFalse(row['current']);self.assertFalse(row['acquisitions'][0]['current'])
        with self.assertRaises(ValueError):self.acquire()
    def test_changed_buyer_requires_new_question_set(self):
        self.cap['description']='Updated service';self.save_request['expected_revision']=1;p.care.save_portfolio(self.path,self.save_request)
        with self.assertRaises(ValueError):self.acquire()
        task=p.refresh_match(self.path,{**self.request,'actor':'synthetic fixture'})['task']
        new=f.materialize(self.path,{'task_id':task['id'],'expected_revision':task['revision']})
        self.assertNotEqual(new['id'],self.manifest['id']);self.assertEqual(len(self.view()['sets']),2)
        self.assertEqual([s['current'] for s in self.view()['sets']],[False,True])
    def test_changed_context_is_historical_and_never_closes_question(self):
        out=self.acquire();self.contexts[out['context_id']]['extra']='changed'
        a=self.question()['acquisitions'][0];self.assertFalse(a['current']);self.assertIn('changed',' '.join(a['stale_reasons']))
        self.assertEqual(self.question()['work_state'],'dated_sources_available')
    def test_current_confirmation_cannot_be_marked_addressed_by_any_actor(self):
        for actor in ('operator','machine','synthetic_test'):
            with self.assertRaisesRegex(ValueError,'cannot be established'):
                f.dispositions.save(self.path,{'target':self.target,'disposition':'addressed','actor_kind':actor})
        self.assertFalse(f.dispositions.inspect(self.path,self.target)['documentary_question_addressed'])
    def test_unresolved_observation_retains_exact_question(self):
        row=f.dispositions.save(self.path,{'target':self.target,'expected_revision':0,'request_id':'observation','actor':'engineering fixture',
            'actor_kind':'machine','disposition':'unresolved','rationale':'Dated report cannot confirm current permission','bindings':[]})
        self.assertEqual(row['target_snapshot']['question'],self.q['review_question'])
        self.assertFalse(f.dispositions.inspect(self.path,self.target)['documentary_question_addressed'])
    def test_partial_context_failure_saves_no_work_receipt(self):
        with patch.object(f.record_context,'verified',side_effect=ValueError('Incomplete source context')):
            with self.assertRaises(ValueError):self.acquire()
        self.assertEqual(self.question()['acquisitions'],[])
    def test_concurrent_preparations_deduplicate(self):
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:self.acquire(),range(2)))
        self.assertEqual(rows[0],rows[1]);self.assertEqual(len(self.question()['acquisitions']),1)
    def test_history_is_immutable(self):
        self.acquire();db=f.database(self.path)
        try:
            for table in ('manifests','acquisitions'):
                for op in ('DELETE FROM '+table,'UPDATE '+table+' SET payload=payload'):
                    with self.assertRaises(sqlite3.IntegrityError):db.execute(op)
        finally:db.close()
    def test_packet_target_requires_authenticated_current_review(self):
        run,reviewer=self.packet();req={'run_id':run['id'],'review_revision':1,'target':self.target,'record_ids':['r1'],'note':'Synthetic source follow-up','request_id':'followup'}
        with self.assertRaises(PermissionError):f.packet_followup.prepare(self.path,req,None)
        out=f.packet_followup.prepare(self.path,req,'Bearer '+reviewer['token'])
        self.assertEqual(out['target']['question'],self.q['review_question']);self.assertFalse(out['requirement_resolved'])
        state=f.packet_followup.inspect(self.path,self.task['id']);self.assertTrue(state['followups'][0]['current'])
        self.assertEqual(len([t for t in state['available_targets'] if t['target']['kind']==f.KIND]),len(self.manifest['questions']))
        self.assertTrue(f.packet_followup.prepare(self.path,req,'Bearer '+reviewer['token'])['idempotent_replay'])
    def test_packet_followup_failure_does_not_fabricate_review_or_hide_local_prep(self):
        self.acquire();state=f.packet_followup.inspect(self.path,self.task['id'])
        self.assertFalse(state['prepare_available']);self.assertEqual(state['followups'],[])
        self.assertFalse(self.question()['acquisitions'][0]['human_review_recorded'])
    def test_source_race_during_context_creation_cannot_commit_progress(self):
        original=f.record_context.create
        def changed(*args):
            result=original(*args);self.records[0]['permission_to_contact_helper']=True;return result
        with patch.object(f.record_context,'create',side_effect=changed):
            with self.assertRaises(ValueError):self.acquire()
        self.assertEqual(f.acquisitions(self.path,self.target),[])
    def test_bound_literal_field_can_be_observed_but_unrelated_source_cannot(self):
        req={'target':self.target,'expected_revision':0,'request_id':'literal','actor':'engineering fixture','actor_kind':'machine',
             'disposition':'unresolved','rationale':'Historical permission is not current consent','bindings':[]}
        for record,field in [(self.records[0],'permission_to_contact_helper'),(self.records[1],'transport')]:
            b={'kind':'field','record_id':record['record_id'],'record_hash':digest(record),'field':field,'value_hash':digest(record[field])}
            if record['record_id']=='r1':
                out=f.dispositions.save(self.path,{**req,'bindings':[b]});self.assertFalse(out['dependencies'][0]['reference']['value'])
            else:
                with self.assertRaisesRegex(ValueError,'exact dated records'):f.dispositions.save(self.path,{**req,'expected_revision':1,'request_id':'unrelated','bindings':[b]})

if __name__=='__main__':unittest.main()
