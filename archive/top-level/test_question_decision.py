"""Connected question decisions: integrity, authority, idempotency and follow-up."""
import copy
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from clinical_core.agere.clinical import question_decision as q
from clinical_core.contracts import digest
import test_care_capabilities as fixtures


class Decision(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CareCapabilities(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.path; self.retriever = SimpleNamespace(path=self.path)
        self.answer = self.fixture.answer
        src = {'record_id':'r1','record_hash':'hash1','patient_id':'p1','source_table':'medication_lists','available_date':'2026-10-01'}
        self.answer['findings'][0]['citations'][0]['source'] = src
        self.answer['unanswered'] = ['Actual medication use remains unknown.']
        self.context = {'model_context':{'sources':{'r1':{'source':src}}}}
        self.source_record = {'record_id':'r1','patient_id':'p1','source_table':'medication_lists',
            'available_date':'2026-10-01','event_date':'2026-10-01','synthetic':True,
            'identity_status':'matched','text':'Review needed'}
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            db.execute('CREATE TABLE patients(id TEXT)'); db.execute("INSERT INTO patients VALUES ('p1')")
            db.execute('CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,payload TEXT)')
            db.execute('INSERT INTO records VALUES (?,?,?)',('r1','p1',json.dumps(self.source_record)))
        src['record_hash'] = digest(self.source_record)
        self.binding = {'question':'What is documented?', 'patient_id':'p1', 'answer_id':self.answer['id'],
            'answer_digest':digest(self.answer), 'context_id':'c'*64, 'analysis_window':{'basis':'all_observed','start':'','end':''},
            'source_versions':{'snapshot':{'as_of':'2026-10-10'},'records':{'r1':src['record_hash']}}, 'record_scope':None,'answer_coverage':{}}
        def bind(_, key):
            if key != self.answer['id']: raise ValueError('Unknown answer')
            return copy.deepcopy(self.answer), copy.deepcopy(self.context), {**copy.deepcopy(self.binding), 'answer_digest':digest(self.answer)}
        self.patch(q, 'source_binding', side_effect=bind)
        self.patch(q.patient_context, 'prepare', return_value={'id':'patientctx'})
        def match(path, req):
            result=q.care.prepare(path,{k:v for k,v in req.items() if k!='patient_context_id'})
            return {**result,'participation_contexts':{o['id']:{'id':'part'} for o in result['options']}}
        self.patch(q.patient_context, 'prepare_match', side_effect=match)
        self.patch(q.patient_context, 'association', return_value=None)
        self.patch(q.participation_work.evidence, 'inspect', return_value={})
        self.patch(q.participation_work, 'enqueue', side_effect=lambda p,r:q.care.enqueue(p,{k:v for k,v in r.items() if k!='participation_context_id'}))
        self.patch(q.patient_context, 'task_event', side_effect=q.care.task_event)
        self.req = {'answer_id':self.answer['id'], 'buyer_id':'buyer1','coverage':copy.deepcopy(self.fixture.req['coverage'])}

    def patch(self,obj,key,**kwargs):
        p=patch.object(obj,key,**kwargs);p.start();self.addCleanup(p.stop)

    def prepare(self):return q.prepare(self.retriever,self.req)
    def queue(self,row):return q.queue(self.retriever,{'decision_id':row['id'],'option_id':row['matching']['options'][0]['id'],'actor':'fixture operator'})['task']

    def test_question_to_owned_work_reopen_and_followup(self):
        row=self.prepare();self.assertEqual(row,self.prepare())
        self.assertEqual(row['planning']['review_topics'][0]['need_tag'],'medication_review')
        task=self.queue(row);self.assertEqual(task,self.queue(row))
        self.assertEqual(task['owner']['id'],'fixture-pharmd')
        result=q.event(self.retriever,{'decision_id':row['id'],'task_id':task['id'],'expected_revision':task['revision'],'request_id':'handoff','action':'prepare_handoff','actor':'fixture operator','note':'Review only'})
        self.assertFalse(result['task']['handoff']['external_dispatch_performed'])
        view=q.reopen(self.retriever,row['id']);self.assertEqual(len(view['tasks']),1)
        req={'decision_id':row['id'],'task_id':task['id'],'answer_id':self.answer['id']}
        follow=q.followup(self.retriever,req);self.assertEqual(follow,q.followup(self.retriever,req))
        self.assertEqual(follow['status'],'no_new_evidence');self.assertFalse(follow['care_outcome_established'])
        self.assertEqual(len(q.reopen(self.retriever,row['id'])['followups']),1)
        self.assertEqual(follow['unresolved'],self.answer['unanswered'])

    def test_no_evaluator_tags_or_finding_ids_accepted(self):
        for key,value in [('need_tags',['functional_recovery']),('finding_ids',['f1']),('question','different')]:
            with self.assertRaises(ValueError):q.prepare(self.retriever,{**self.req,key:value})

    def test_unknown_source_types_abstain_and_negation_is_not_need(self):
        self.answer['findings'][0]['text']='No medication problem reported.'
        p=self.prepare()['planning'];self.assertFalse(p['clinical_indication_established'])
        self.assertEqual(p['review_topics'][0]['authority'],'documentary_review_topic_only')
        self.answer['findings'][0]['citations'][0]['source']['source_table']='notes'
        self.context['model_context']['sources']['r1']['source']['source_table']='notes'
        row=self.prepare();self.assertIsNone(row['matching']);self.assertEqual(row['planning']['unmapped_finding_ids'],['f1'])

    def test_cross_patient_citation_rejected(self):
        self.answer['findings'][0]['citations'][0]['source']['patient_id']='other'
        with self.assertRaises(ValueError):self.prepare()

    def test_future_and_unknown_arrival_do_not_support_earlier_decision(self):
        for date in ['2026-10-11',None]:
            self.context['model_context']['sources']['r1']['source']['available_date']=date
            with self.assertRaises(ValueError):self.prepare()

    def test_changed_answer_is_stale_for_queue_but_historical_view_survives(self):
        row=self.prepare();self.answer['findings'][0]['text']='Changed answer'
        self.assertFalse(q.reopen(self.retriever,row['id'])['dependencies_current'])
        with self.assertRaises(ValueError):self.queue(row)

    def test_buyer_revision_and_rule_revision_invalidate(self):
        row=self.prepare();self.fixture.cap['name']='Revised';self.fixture.update()
        with self.assertRaises(ValueError):self.queue(row)
        row=self.prepare()
        with patch.object(q,'identity',return_value='new-rules'):
            with self.assertRaises(ValueError):self.queue(row)

    def test_other_option_and_task_rejected(self):
        row=self.prepare()
        with self.assertRaises(ValueError):q.queue(self.retriever,{'decision_id':row['id'],'option_id':'other','actor':'operator'})
        with self.assertRaises(ValueError):q.followup(self.retriever,{'decision_id':row['id'],'task_id':'other','answer_id':self.answer['id']})

    def test_same_question_followup_required_and_record_changes_are_documentary(self):
        row=self.prepare();task=self.queue(row);req={'decision_id':row['id'],'task_id':task['id'],'answer_id':self.answer['id']}
        self.binding['patient_id']='other'
        with self.assertRaises(ValueError):q.followup(self.retriever,req)
        self.binding['patient_id']='p1';self.binding['source_versions']['records']['r1']='hash2'
        result=q.followup(self.retriever,req)
        self.assertEqual(result['changes'],[{'record_id':'r1','change':'source_version_changed'}])
        self.assertFalse(result['care_outcome_established'])
        self.assertEqual(q.care_readiness.task(self.path,task['id'])['state'],'queued_for_internal_review')


class SourceArrival(unittest.TestCase):
    setUp = Decision.setUp
    patch = Decision.patch
    prepare = Decision.prepare
    queue = Decision.queue

    def put(self, key, **changes):
        record = {**self.source_record, 'record_id':key, **changes}
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (key,'p1',json.dumps(record)))
        return record

    def observe(self, row, task):
        return q.observe(self.retriever, {'decision_id':row['id'],'task_id':task['id']})

    def test_inventory_distinguishes_arrival_from_preexisting_unselected_evidence(self):
        self.put('outside-selection')
        row=self.prepare(); task=self.queue(row)
        initial=self.observe(row,task)
        self.assertEqual(initial['current_records'],2)
        self.assertEqual(initial['newly_received_count'],0)
        self.put('later-arrival',event_date='2025-01-01',available_date='2026-10-09')
        observed=self.observe(row,task)
        self.assertEqual(observed['newly_received_count'],1)
        self.assertEqual(observed['changes'][0]['record_id'],'later-arrival')
        self.assertFalse(observed['changes'][0]['in_original_answer_selection'])
        self.assertEqual(observed['changes'][0]['clinical_relevance'],'unassessed')
        self.assertEqual(observed['question'],row['question'])
        self.assertEqual(observed,self.observe(row,task))
        self.assertEqual(q.care_readiness.task(self.path,task['id']),task)
        self.assertFalse(observed['interpretation_changed'])
        self.assertFalse(observed['care_outcome_established'])
        self.assertFalse(observed['task_state_changed'])
        self.assertEqual(len(q.reopen(self.retriever,row['id'])['followups']),2)
        with self.assertRaises(ValueError): self.queue(row)
        with self.assertRaises(ValueError):
            q.event(self.retriever,{'decision_id':row['id'],'task_id':task['id'],
                'expected_revision':task['revision'],'request_id':'stale','action':'prepare_handoff',
                'actor':'test','note':'Must not advance stale decision'})

    def test_changed_and_absent_sources_are_not_clinical_events(self):
        self.put('outside-selection'); row=self.prepare(); task=self.queue(row)
        self.put('r1',text='Changed received account')
        with sqlite3.connect(self.path/'stack.sqlite3') as db:
            db.execute("DELETE FROM records WHERE id='outside-selection'")
        result=self.observe(row,task)
        self.assertEqual(result['version_changed_count'],1)
        self.assertEqual(result['not_in_current_snapshot_count'],1)
        self.assertEqual(result['newly_received_count'],0)
        self.assertFalse(q.reopen(self.retriever,row['id'])['dependencies_current'])
        self.assertTrue(all(c['clinical_relevance']=='unassessed' for c in result['changes']))

    def test_legacy_decision_does_not_invent_historical_arrivals(self):
        row=self.prepare(); row.pop('received_inventory'); row.pop('id'); row['id']=digest(row)
        with q.database(self.path) as db:
            db.execute('INSERT INTO question_decisions VALUES (?,?)',(row['id'],json.dumps(row)))
        task=self.queue(row); self.put('outside-selection')
        result=self.observe(row,task)
        self.assertIsNone(result['newly_received_count'])
        self.assertEqual(result['changes'],[])
        self.assertEqual(result['unbaselined_current_records'],1)
        self.assertEqual(result['status'],'outside_selection_arrival_unassessed')
        self.put('r1',text='Changed selected evidence')
        self.assertEqual(self.observe(row,task)['version_changed_count'],1)

    def test_same_selected_answer_cannot_hide_newly_received_sources(self):
        row=self.prepare();task=self.queue(row);self.put('new-outside-selection')
        result=q.followup(self.retriever,{'decision_id':row['id'],'task_id':task['id'],'answer_id':row['answer_id']})
        self.assertEqual(result['changes'],[])
        self.assertEqual(result['received_source_changes']['newly_received_count'],1)
        self.assertEqual(result['status'],'source_or_interpretation_changed_requires_review')
        self.assertFalse(result['interpretation_changed'])
        self.assertFalse(result['care_outcome_established'])

    def test_wrong_patient_future_unmatched_and_nonsynthetic_rejected(self):
        for changes in [{'patient_id':'p2'},{'available_date':'2026-10-11'},
                        {'identity_status':'unmatched'},{'synthetic':False}]:
            with self.subTest(changes=changes):
                self.put('bad',**changes)
                with self.assertRaises(ValueError):q.received_inventory(self.path,'p1')
        with self.assertRaises(ValueError):q.received_inventory(self.path,'unknown')

    def test_duplicate_record_identity_rejected(self):
        self.put('alias',record_id='r1')
        with self.assertRaisesRegex(ValueError,'Duplicate'):q.received_inventory(self.path,'p1')

    def test_bound_source_inventory_mismatch_prevents_decision(self):
        self.put('r1',text='Changed source after binding')
        with self.assertRaisesRegex(ValueError,'inventory differs'):self.prepare()

    def test_observation_rejects_supplied_changes_and_other_tasks(self):
        row=self.prepare();task=self.queue(row)
        with self.assertRaises(ValueError):self.observe(row,{'id':'other'})
        with self.assertRaises(ValueError):
            q.observe(self.retriever,{'decision_id':row['id'],'task_id':task['id'],'changes':[]})
        current=q.received_inventory(self.path,'p1');current['patient_id']='p2'
        with self.assertRaises(ValueError):q.compare_received(row,current)

    def test_concurrent_source_change_prevents_saved_observation(self):
        row=self.prepare();task=self.queue(row)
        original=q.received_inventory; calls=[]
        def moving(path,patient):
            calls.append(1)
            # bound_task/reopen takes two snapshots; observe then captures and
            # rechecks its own inventory. Change between those final two reads.
            if len(calls)==4:self.put('concurrent-arrival')
            return original(path,patient)
        with patch.object(q,'received_inventory',side_effect=moving):
            with self.assertRaisesRegex(ValueError,'during follow-up'):self.observe(row,task)
        self.assertEqual(q.reopen(self.retriever,row['id'])['followups'],[])

    def test_concurrent_work_change_prevents_saved_observation(self):
        row=self.prepare();task=self.queue(row)
        with patch.object(q,'bound_task',return_value=task), patch.object(q.care_readiness,'task',return_value={**task,'revision':2}):
            with self.assertRaisesRegex(ValueError,'Work changed'):self.observe(row,task)
        self.assertEqual(q.reopen(self.retriever,row['id'])['followups'],[])


class DecisionScorecardBinding(unittest.TestCase):
    def test_scoped_prompt_needs_exact_original_question_binding(self):
        from clinical_core.agere.workflow_scorecard import question_matches
        case={'question':'Original question','patient':'p1'}
        answer={'id':'a','question':'Original question\n\nEvidence scope: a narrow selection','context_id':'ctx','patient_id':'p1'}
        decision={'id':'d','question':'Original question','patient_id':'p1','answer_id':'a','answer_digest':digest(answer),'context_id':'ctx','source_versions':{'source_plan_id':'plan'}}
        self.assertTrue(question_matches(answer,case,decision))
        self.assertFalse(question_matches(answer,case,{}))
        for field,value in [('answer_digest','stale'),('context_id','other'),('patient_id','other'),('question','Other question')]:
            self.assertFalse(question_matches(answer,case,{**decision,field:value}))


if __name__=='__main__':unittest.main()
