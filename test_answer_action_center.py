"""Independent synthetic workflow: answer -> exact cohort -> owned review -> reopen.
No benchmark references, model calls, simulated enrollment or external actions.
"""
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from clinical_core.agere.deliverables import answer_context as A
from clinical_core.agere.clinical import answer_shape, metric_router, conversation
from clinical_core.agere import ask_view

USER_QUESTION = "Find every patient with heartfailure and reduced EF, flag which patients aren't on defect free GDMT or not on max dose, who their PCP is, and who their cardiologist is"

class Workflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        db = sqlite3.connect(self.path / 'semantic.sqlite3')
        db.executescript("CREATE TABLE meta(key,value); INSERT INTO meta VALUES ('as_of','2030-05-27'),('source_digest','independent-source'),('version','test'); CREATE TABLE patient(patient_id PRIMARY KEY); INSERT INTO patient VALUES ('A'),('B'),('C'),('OUTSIDE');")
        db.close()
        self.question = 'List patients with recorded missed appointments and show their PCP'
        self.result = {'answer': {'status':'answered','answer_type':'table','headline':'Three patients',
                       'table':[{'patient_id':'A','pcp':'Dr Test','record_id':'R-A'},
                                {'patient_id':'B','pcp':None,'record_id':'R-B'},
                                {'patient_id':'C','pcp':'Dr Other','record_id':'R-C'}], 'missing':[]},
                       'graph':{'interpretation':'Observed missed appointments'},'new_paid_api_calls':0}
        self.c = A.capture(self.path, self.question, self.result)

    def req(self, **kw):
        return {'context_id':self.c['id'],'program':'pharmd','patient_ids':['A','B'],
                'owner':'PharmD review team','note':'Review suitability; no enrollment', **kw}

    def test_connected_work_survives_reopen_and_duplicate_create(self):
        result = A.create_work(self.path, self.req())
        self.assertEqual([i['patient_id'] for i in result['items']], ['A','B'])
        item = result['items'][0]
        A.update_work(self.path, {'context_id':self.c['id'],'item_id':item['id'],'revision':1,
                                 'state':'blocked','owner':'Evidence review team','note':'Need provider attribution'})
        # A repeated click cannot reset ownership, notes or progress.
        with self.assertRaisesRegex(ValueError, 'different owner'):
            A.create_work(self.path,self.req(owner='Different team'))
        reopened = A.inspect(self.path, self.c['id'])
        self.assertEqual(reopened['context']['question'],self.question)
        self.assertEqual(reopened['counts'],{'blocked':1,'needs_review':1})
        self.assertEqual(reopened['items'][0]['owner'],'Evidence review team')
        db = A._db(self.path,reopened['context'])
        self.assertEqual(db.execute('SELECT count(*) FROM work_events').fetchone()[0],3)
        self.assertEqual(db.execute('SELECT count(*) FROM work_requests').fetchone()[0],0)
        self.assertEqual(db.execute('SELECT count(*) FROM question_decision_tasks').fetchone()[0],2)
        db.close()
        self.assertFalse(any(i['external_dispatch_performed'] for i in reopened['items']))

    def test_historical_summaries_are_not_authorized_by_legacy_flags(self):
        for shape, table, select in [
            ('value', None, [{'kind':'avg'}]),
            ('table', [{'payer':'P1','count':3}], [{'kind':'count'}]),
            ('table', [{'payer':'P1','amount':8}], [{'kind':'sum'}]),
        ]:
            context = {'answer': {'status':'answered','answer_type':shape,'table':table,
                                   'patient_ids':['A','B'],'actionable_cohort':True},
                       'planner_receipt': {'request_contract': {'request': {'select':select}}}}
            self.assertFalse(A.supports_patient_work(context))

    def test_wrong_cohort_stale_data_and_conflicting_revision_rejected(self):
        with self.assertRaisesRegex(ValueError,'outside'): A.create_work(self.path,self.req(patient_ids=['OUTSIDE']))
        item=A.create_work(self.path,self.req())['items'][0]
        with self.assertRaisesRegex(ValueError,'changed in another'): A.update_work(self.path,{'context_id':self.c['id'],'item_id':item['id'],'revision':0,'state':'blocked','owner':'Team','note':''})
        db=sqlite3.connect(self.path/'semantic.sqlite3');db.execute("UPDATE meta SET value='new-source' WHERE key='source_digest'");db.commit();db.close()
        self.assertTrue(A.inspect(self.path,self.c['id'])['context']['stale'])
        with self.assertRaisesRegex(ValueError,'data changed'): A.create_work(self.path,self.req())

    def test_missing_fields_and_non_roster_ids_do_not_become_work(self):
        self.result['answer']['missing']=['The PCP relation is unresolved']
        self.c=A.capture(self.path,self.question,self.result)
        with self.assertRaisesRegex(ValueError,'incomplete'): A.create_work(self.path,self.req())
        self.result['answer']['missing']=[]
        self.result['answer']['table']=[{'patient_id':'INVENTED'}]
        self.c=A.capture(self.path,self.question,self.result)
        with self.assertRaisesRegex(ValueError,'absent from the source roster'): A.create_work(self.path,self.req(patient_ids=['INVENTED']))

    def test_two_programs_preserve_exact_patient_evidence(self):
        A.create_work(self.path,self.req())
        d=A.create_work(self.path,self.req(program='rpm',patient_ids=['B','C']))
        self.assertEqual(len(d['items']),4)
        c,b=A.evidence_bundle(self.path,self.c['id'])
        self.assertEqual(c['patient_ids'],['A','B','C'])
        self.assertEqual(c['patient_rows'][1]['evidence_rows'][0]['pcp'],None)
        self.assertEqual(len(b.receipts),2)
        self.assertEqual(len(A.listing(self.path)),1)

    def test_frontier_uses_same_frozen_evidence_and_no_other_data_tools(self):
        C=importlib.import_module('clinical_core.agere.deliverables.compose')
        def sender(body):
            self.assertEqual(body['tool_choice'],{'type':'none'})
            text=body['messages'][0]['content']
            self.assertIn('saved_answer',text)
            self.assertIn('Observed missed appointments',text)
            return {'content':[{'type':'text','text':'TITLE: Review command center\n```html\n<h1>Review command center</h1><p>Clinical suitability needs review.</p>\n```'}]}, {'cost_estimate':{'estimated_usd':0}}
        with mock.patch.object(C.toolsmod.ToolContext,'call',side_effect=AssertionError('Must not fetch a new cohort')):
            r=A.compose_frontier(self.path,{'context_id':self.c['id'],'request':'Build a war room for these patients'},allow_live=True,send=sender)
        self.assertEqual(r['status'],'delivered')
        self.assertEqual(r['tool_calls'],[])
        self.assertEqual(r['costs']['frontier']['calls'],1)
        self.assertEqual(A.inspect(self.path,self.c['id'])['items'],[])
        with self.assertRaisesRegex(ValueError,'disabled'):
            A.compose_frontier(self.path,{'context_id':self.c['id'],'request':'Build a view'})

    def test_compose_route_saves_context_in_conversation_without_new_query(self):
        stack=SimpleNamespace(path=self.path,allow_live=False,compose=mock.Mock(return_value=self.result))
        with mock.patch('clinical_core.agere.clinical.learning.record_outcome'):
            r=conversation.handle_compose(stack,{'question':self.question,'conversation_id':'independent-workflow'})
        stack.compose.assert_called_once()
        self.assertIn('action_context',r)
        summary=conversation.turns(self.path,'independent-workflow')[0]['answer_summary']
        self.assertEqual(summary['action_context']['id'],r['action_context']['id'])
        self.assertIn('Open action workspace',ask_view.page())

class Scope(unittest.TestCase):
    def test_full_user_request_is_not_a_beta_blocker_metric(self):
        library=metric_router.routable()
        metric=library['hf_no_beta_blocker']
        missing=metric_router.unaccounted_clinical_scope(USER_QUESTION,metric)
        self.assertEqual(set(missing),{'GDMT regimen assessment','ejection fraction','dose assessment'})
        self.assertEqual(answer_shape.requested(USER_QUESTION)['shape'],'table')
        self.assertIsNotNone(metric_router.shape_misfit(USER_QUESTION,'hf_no_beta_blocker',{'operation':'sql','parameters':{'returns':'patient_set'}}))
        self.assertEqual(metric_router.unaccounted_clinical_scope('Find patients with heart failure and no beta blocker',metric),[])

    def test_other_patient_details_remain_tables_and_filters_remain_sets(self):
        for q in ('List patients with CKD and include their nephrologist',
                  "Find patients with asthma and show each patient's PCP",
                  'List patients with heart failure and reduced EF who are not on maximum dose, and list each patient\'s PCP and cardiologist'):
            self.assertEqual(answer_shape.requested(q)['shape'],'table',q)
        self.assertEqual(answer_shape.requested('Which patients have a PCP?')['shape'],'patient_set')

if __name__=='__main__': unittest.main()
