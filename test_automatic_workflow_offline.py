"""Connected authored engineering fixtures, with network and subprocess sends forbidden.

These check product wiring, native arithmetic, full document coverage, saved
evidence and artifact binding. Scripted model judgments are NOT clinical grades.
"""
import copy
import importlib
import json
import os
import re
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import backstop_agent as B, conversation, proposers as P, semantic_analyst as S
from clinical_core.agere.deliverables import answer_context
from clinical_core.agere.gateway import BudgetLedger

composer=importlib.import_module('clinical_core.agere.deliverables.compose')


class AutomaticWorkflow(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)
        for target in ('socket.socket.connect','socket.create_connection','subprocess.Popen'):
            p=patch(target,side_effect=AssertionError('Offline workflow must not contact a provider or CLI'))
            p.start();self.addCleanup(p.stop)
        p=patch.dict(os.environ,{'AGERE_EVALUATION':'0','AGERE_RECURSIVE_IMPROVEMENT':'off','AGERE_LEARNED':'off'})
        p.start();self.addCleanup(p.stop)
        self.stack.allow_live=True  # exercise normal materialization with scripted send boundaries
        self.stack.receipts=self.path/'receipts';self.stack.receipts.mkdir()
        self.stack.ledger_path=self.path/'providers.sqlite3'
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript('''CREATE TABLE record(record_id,patient_id,stream,event_date,available_date,payload);
              CREATE TABLE scanned_document(record_id,patient_id,document_type,event_date,available_date);
              CREATE TABLE scanned_document_text(record_id,patient_id,text_source,text);''')
            for pid,drug,amount in [('A','carvedilol','6.25 mg'),('C','lisinopril','10 mg')]:
                db.execute('INSERT INTO record VALUES(?,?,?,?,?,?)',('dose'+pid,pid,'medication_lists','2030-04-01','2030-04-01',
                    json.dumps({'name':drug,'dose':amount,'frequency':'BID','list_status':'active','event_date':'2030-04-01'})))
            self.quote='Patient cannot afford the prescription. Later: assistance approved; barrier resolved.'
            self.full_text=self.quote+' Complete ordinary clinical context.'*1200
            for rid,pid,stream,day,arrival,text in [('noteA','A','notes','2030-04-01','2030-04-02',self.full_text),
                    ('copyA','A','hie_documents','2030-04-01','2030-04-02',self.full_text),
                    ('later','A','notes','2030-04-05','2030-06-01','This was not available at the snapshot.'),
                    ('outside','OUTSIDE','notes','2030-04-01','2030-04-01','Unrelated patient.')]:
                db.execute('INSERT INTO record VALUES(?,?,?,?,?,?)',(rid,pid,stream,day,arrival,json.dumps({'text':text})))
        self.read_bodies=[];self.actor_bodies=[]
        self.question=('Find patients with diabetes over age 65, review all notes for treatment barriers and assess documented daily doses; '
                       'create an impressive navigation service brief with daily support and a tablet.')
        self.brief='create an impressive navigation service brief with daily support and a tablet'
        self.cohort_sql="SELECT DISTINCT patient_id FROM patient JOIN condition USING(patient_id) WHERE concept='diabetes' AND birth_date<'1965-05-27' ORDER BY patient_id"

    def reader(self,stack,ledger,receipts):
        def send(body):
            self.read_bodies.append(copy.deepcopy(body));p=json.loads(body['messages'][0]['content'])
            self.assertEqual(p['patient_id'],'A');self.assertEqual(len(p['whole_documents']),1)
            self.assertEqual(p['whole_documents'][0]['text'],self.full_text)
            rid=p['whole_documents'][0]['record_id']
            receipts.append({'cost_estimate':{'estimated_usd':.002}})
            return {'content':[{'type':'text','text':json.dumps({'findings':{'barriers':[
                {'value':'Historical affordability barrier, later resolved','record_id':rid,'quote':self.quote}]}})}]},.002,False
        return send

    def actor(self,body):
        self.actor_bodies.append(copy.deepcopy(body));turn=len(self.actor_bodies)
        if turn==1:
            out={'type':'tool_use','id':'review','name':'review_documents','input':{
                'patient_query_sql':self.cohort_sql,'questions':{'barriers':'What treatment barriers and subsequent resolutions are documented?'}}}
        elif turn==2:
            out={'type':'tool_use','id':'dose','name':'query_sql','input':{'complete':True,'sql':
                "SELECT r.patient_id,r.review_status,r.documents_reviewed,r.review_json,d.daily_mg,d.dose_status "
                "FROM agere_result_1 r LEFT JOIN {{current_dose}} d USING(patient_id) ORDER BY r.patient_id"}}
        else:
            out={'type':'text','text':json.dumps({'status':'answered','answer_type':'table',
                'headline':'Documented daily doses and narrative barriers, with source gaps retained.',
                'patient_ids':['A','C'],'table_from_tool':'agere_result_2','sources':[],
                'limitations':['Reference ranges do not establish individual suitability or maximum tolerated dose.'],
                'workflow':[{'kind':'deliverable','title':'Navigation service','template':'command','quote':self.brief,'unresolved':[]}]})}
        return {'content':[out],'usage':{'input_tokens':10,'output_tokens':10}},.001,False

    def artifact(self,body):
        self.assertEqual(body['tool_choice'],{'type':'none'})
        rid=re.search(r'"receipt":"(R-[0-9a-f]{10})","tool":"saved_answer"',body['messages'][0]['content']).group(1)
        return {'content':[{'type':'text','text':'TITLE: Navigation service\n```html\n<h1>Daily navigation</h1>'
                '<p>Proposed service; eligibility and enrollment require verification.</p>'
                f'<p>Patients <span data-bind="{rid}:answer.patient_ids"></span></p>\n```'}]}, {'cost_estimate':{'estimated_usd':.01}}

    def run_connected(self):
        original=B.answer
        def compose(question,**kw):
            ledger=BudgetLedger(self.stack.ledger_path)
            cascade=P.Cascade(self.stack,self.catalog,question,ledger=ledger)
            try:
                contract,_=cascade.tier_backstop_agent()
                return {**contract['executed'],'graph':contract['graph'],'planner_receipt':contract['legacy_receipt']}
            finally:ledger.close()
        self.stack.compose=compose
        s1=SimpleNamespace(decide=Mock(return_value={'label':'supported','decided_by':'authored_fixture','steps':[]}))
        def answer_gate(cascade,name,graph,receipt,result):
            return {**result,'answer_checks':{'status':'authored_fixture'}}
        with patch.object(B,'answer',side_effect=lambda stack,q,**kw:original(stack,q,send=self.actor,**kw)),\
             patch.object(P,'claude_available',return_value=True),patch.object(P,'answer_gate',side_effect=answer_gate),\
             patch.object(S,'semantic_context',return_value=('Authored fixture schema',set())),\
             patch('clinical_core.agere.clinical.together._ledgered_send',side_effect=self.reader),\
             patch('clinical_core.agere.clinical.system_one_gate.ready',return_value=True),\
             patch('clinical_core.agere.clinical.system_one_gate.check',return_value={'label':'answers_fully','calls':0,'cost_usd':0}),\
             patch('clinical_core.agere.clinical.system_one_gate.s1',return_value=s1),\
             patch('clinical_core.agere.clinical.system_one.decide',return_value={'label':'supported','decided_by':'authored_fixture','steps':[]}),\
             patch.object(composer,'ledgered_sender',return_value=self.artifact):
            return conversation.handle_compose(self.stack,{'question':self.question})

    def test_complete_non_hf_note_dose_and_artifact_workflow_uses_same_shared_path(self):
        result=self.run_connected()
        self.assertEqual(result['answer']['status'],'answered')
        rows=result['answer']['table'];self.assertEqual([r['patient_id'] for r in rows],['A','C'])
        self.assertEqual([r['daily_mg'] for r in rows],[12.5,20.0])
        self.assertEqual([r['review_status'] for r in rows],['reviewed','no_documents'])
        self.assertEqual(len(self.read_bodies),1)
        self.assertEqual(result['investigation']['document_reviews'][0]['manifest'][0]['received_documents'],2)
        self.assertEqual(result['workflow'][0]['status'],'ready')
        context=answer_context.load(self.path,result['action_context']['id'])
        self.assertEqual(context['patient_ids'],['A','C']);self.assertEqual(context['answer']['table'],rows)
        _,bundle,_,_=composer.load(answer_context.workspace(self.path,context),result['workflow'][0]['deliverable_id'])
        saved=next(bundle.results[rid] for rid,r in bundle.receipts.items() if r['tool']=='saved_answer')
        self.assertEqual(saved['answer']['table'],rows)

    def test_recursive_configuration_reuses_native_execution_through_the_complete_workflow(self):
        with patch.dict(os.environ,{'AGERE_RECURSIVE_IMPROVEMENT':'on'}):
            baseline=self.run_connected()
            self.assertEqual(baseline['workflow'][0]['status'],'ready')
            candidate=baseline['investigation']['recursive_improvement']['candidate']
            self.assertEqual(candidate['status'],'validated_pending_complete_workflow')
            reused=self.run_connected()
        info=reused['investigation']['recursive_improvement']
        self.assertEqual(info['replay']['used'],candidate['id'])
        self.assertEqual(len(info['replay']['certificates']),2)
        self.assertTrue(info['final_decision_reused'])
        self.assertEqual(len(self.actor_bodies),3)  # baseline 3, reuse 0; fresh reviews still execute
        self.assertEqual(reused['workflow'][0]['status'],'ready')
        self.assertEqual(reused['answer']['table'],baseline['answer']['table'])

    def test_component_learning_records_full_workflow_without_legacy_learning_stores(self):
        with patch.dict(os.environ,{'AGERE_COMPONENT_LEARNING':'on'}),patch(
                'clinical_core.agere.clinical.learning.record_outcome',side_effect=AssertionError('No legacy learning store')),patch(
                'clinical_core.agere.clinical.learning.corrected',side_effect=AssertionError('No legacy correction lookup')):
            result=self.run_connected()
        self.assertEqual(result['workflow'][0]['status'],'ready')
        self.assertEqual(result['learning']['components']['component_learning']['status'],'recorded')
        self.assertEqual(result['learning']['errors'],[])

    def test_complete_native_results_survive_model_preview_and_final_binding(self):
        wh=S.SemanticWarehouse(self.path/'semantic.sqlite3');self.addCleanup(wh.close)
        tools=B.Tools(self.stack,wh,'2030-05-27');self.addCleanup(tools.db.close)
        sql='WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<2505) SELECT x,printf("%080d",x) AS detail FROM n'
        result=tools({'name':'query_sql','input':{'sql':sql,'complete':True}})
        self.assertEqual(len(result['rows']),2505);self.assertFalse(result['truncated'])
        preview=S.actor_result(result)
        self.assertTrue(preview['preview_only']);self.assertLessEqual(len(preview['rows']),3)
        answer={'answer_type':'table','table_from_tool':result['result_view']}
        B.materialize_table(answer,[{'result':result,'sql':sql}])
        self.assertEqual(len(answer['table']),2505);self.assertEqual(answer['table'][-1]['x'],2505)


if __name__=='__main__':unittest.main()
