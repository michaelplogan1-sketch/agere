"""Independent tiny-source checks for composed work; no protected evaluation."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch
import test_shared_planner as fixtures
from clinical_core.agere.clinical import request_plan as R, request_language as L, shared_planner as S, chief_staff
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
from clinical_core.agere.deliverables import answer_context as A, workflow as W, mock_views


class AnswerWorkflow(unittest.TestCase):
    setUp = fixtures.SharedPlanner.setUp

    def composed(self, base='Count claims where ever_denied = 1 by payer for our clinical utilization report', unresolved=False):
        q = base + '; create a command center; prepare a simulated outreach program; monitor this answer'
        p = L.parse(self.catalog, base)
        p['workflow'] = [
            {'kind': 'deliverable', 'title': 'Claims command center', 'template': 'command', 'quote': 'create a command center', 'unresolved': []},
            {'kind': 'simulation', 'title': 'Outreach review', 'program': 'custom', 'quote': 'prepare a simulated outreach program',
             'unresolved': ['Define program suitability'] if unresolved else []},
            {'kind': 'monitor', 'title': 'Recorded denied claims', 'mode': 'answer', 'scope': 'query', 'outputs': [p['select'][-1]['alias']], 'quote': 'monitor this answer', 'unresolved': []}]
        contract = R.compile_request(self.catalog, q, p)
        receipt = {'version': S.VERSION, 'policy_identity': S.policy_identity(), 'request_contract': contract, 'interpretation': {'execution_accepted': True}, 'new_paid_api_calls': 0}
        result = S.execute_contract(self.stack, q, S.graph_for(contract), receipt)
        c = A.capture(self.path, q, result)
        return q, p, result, c

    def test_summary_keeps_deliverable_and_monitor_but_requires_action_population(self):
        q, p, result, c = self.composed()
        expected = [{'payer': 'P1', 'count': 2}, {'payer': 'P2', 'count': 1}, {'payer': 'P3', 'count': 1}]
        self.assertEqual(result['answer']['table'], expected)
        w = W.materialize(self.path, c['id'])
        self.assertEqual([s['status'] for s in w['steps']], ['ready', 'blocked', 'watching'])
        self.assertEqual(A.inspect(self.path,c['id'])['items'], [])
        self.assertEqual(w, W.materialize(self.path,c['id']))
        self.assertFalse(w['external_dispatch_performed'])
        self.assertEqual(w['steps'][2]['observations'][0]['measurement']['table'], expected)
        import importlib
        composer=importlib.import_module('clinical_core.agere.deliverables.compose')
        _,_,_,html=composer.load(A.workspace(self.path,A.load(self.path,c['id'])),w['steps'][0]['deliverable_id'])
        self.assertNotIn('\"owner\": \"Synthetic care team\"',html)

    def test_explicit_patient_selection_keeps_deliverable_queue_and_monitor(self):
        _, _, result, context = self.composed(base='List patients with diabetes')
        self.assertTrue(result['answer']['actionable_cohort'])
        workflow = W.materialize(self.path, context['id'])
        self.assertEqual([s['status'] for s in workflow['steps']], ['ready', 'ready', 'watching'])
        self.assertEqual({i['patient_id'] for i in A.inspect(self.path, context['id'])['items']}, {'A','B','C'})

    def test_missing_action_definition_keeps_the_complete_answer_and_other_steps(self):
        _, _, result, c = self.composed(unresolved=True)
        self.assertEqual(result['answer']['status'], 'answered')
        self.assertEqual([s['status'] for s in W.materialize(self.path,c['id'])['steps']], ['ready','needs_definition','watching'])
        self.assertEqual(A.inspect(self.path,c['id'])['items'], [])

    def test_query_unknown_blocks_all_downstream_execution(self):
        q, p, _, _ = self.composed()
        q += ' with unmeasured suitability'
        p['unresolved'] = [{'quote': 'with unmeasured suitability', 'reason': 'No supplied definition'}]
        contract = R.compile_request(self.catalog,q,p)
        result = S.execute_contract(self.stack,q,S.graph_for(contract),{'request_contract':contract,'interpretation':{},'new_paid_api_calls':0})
        self.assertEqual(result['answer']['patient_ids'], [])
        self.assertTrue(all(s['status']=='blocked' for s in result['workflow']))
        self.assertIsNone(A.capture(self.path,q,result))

    def test_monitor_deduplicates_same_snapshot_and_preserves_warehouse_scope(self):
        _, _, _, c = self.composed()
        W.materialize(self.path,c['id'])
        self.stack.compose=Mock(side_effect=AssertionError('Must not reinterpret'))
        req={'context_id':c['id'],'index':2}
        self.assertEqual(len(W.refresh(self.stack,req)['steps'][2]['observations']),1)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("UPDATE meta SET value='new' WHERE key='source_digest'")
            db.execute("UPDATE claim SET ever_denied=0 WHERE claim_id='Q1'")
            db.execute("INSERT INTO claim VALUES('NEW','D','P2','2029-01-01','2029-01-02',100,1)")
        out=W.refresh(self.stack,req)['steps'][2]['observations']
        self.assertEqual(len(out),2)
        self.assertEqual(out[-1]['measurement']['table'],[{'payer':'P1','count':1},{'payer':'P2','count':2},{'payer':'P3','count':1}])
        self.assertIn('D',out[-1]['measurement']['patient_ids'])
        self.assertEqual(len(W.refresh(self.stack,req)['steps'][2]['observations']),2)

    def test_monitor_missing_saved_patients_is_explicit(self):
        _, _, _, c = self.composed()
        record=W.materialize(self.path,c['id'])
        record['steps'][2]['patient_scope']=['A','B']
        W._save(self.path,c['id'],record)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("DELETE FROM patient WHERE patient_id='A'")
            db.execute("UPDATE meta SET value='new' WHERE key='source_digest'")
        with self.assertRaisesRegex(ValueError,'missing'):
            W.refresh(self.stack,{'context_id':c['id'],'index':2})

    def test_nonpatient_result_is_rendered_and_never_launches_patient_work(self):
        result={'answer':{'status':'answered','headline':'Payer totals','table':[{'payer':'P1','amount':123}],'missing':[]},
                'workflow':[{'kind':'simulation','program':'custom','title':'Review','unresolved':[]}]}
        c=A.capture(self.path,'Compare payer totals',result)
        w=W.materialize(self.path,c['id'])
        self.assertEqual(w['steps'][0]['status'],'needs_definition')
        d=mock_views.create(self.path,{'context_id':c['id'],'template':'command','request':'Payer view'})
        self.assertIn('\"amount\": 123',d['html'])
        self.assertIn('analysis-tables',d['html'])

    def test_workflow_intent_is_part_of_saved_identity(self):
        q,_,result,c=self.composed()
        other=copy.deepcopy(result);other['workflow']=[]
        self.assertNotEqual(c['id'],A.capture(self.path,q,other)['id'])

    def test_new_snapshot_cannot_be_stamped_onto_old_answer(self):
        q,_,result,_=self.composed()
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("UPDATE meta SET value='changed-after-execution' WHERE key='source_digest'")
        with self.assertRaisesRegex(ValueError,'changed between execution'):
            A.capture(self.path,q,result)

    def test_workflow_cannot_hide_a_filter_or_uncomputed_monitor_metric(self):
        q,p,_,_=self.composed()
        bad=copy.deepcopy(p)
        bad['workflow'][1]['quote']='prepare a simulated outreach program for those without a statin'
        altered=q.replace('prepare a simulated outreach program',bad['workflow'][1]['quote'])
        checked = R.compile_request(self.catalog,altered,bad)
        self.assertTrue(checked['request']['workflow'][1]['unresolved'])
        bad=copy.deepcopy(p)
        bad['workflow'][2]['quote']='monitor ED visits afterwards'
        altered=q.replace('monitor this answer','monitor ED visits afterwards')
        checked = R.compile_request(self.catalog,altered,bad)
        self.assertTrue(checked['request']['workflow'][2]['unresolved'])
        bad['workflow'][2]['unresolved']=['Define and compute the requested visit outcome']
        R.compile_request(self.catalog,altered,bad)  # Explicit unknown cannot start a monitor.

    def test_workflow_content_cannot_smuggle_unquoted_scope_through_intent(self):
        q,p,_,_=self.composed()
        for phrase in ('launch outreach for patients not on a statin',
                       'assign tasks to patients lacking a PCP',
                       'make a dashboard only for uninsured members'):
            bad=copy.deepcopy(p)
            original=bad['workflow'][1]['quote']
            bad['workflow'][1]['quote']=phrase
            if phrase.startswith('make'):
                bad['workflow'][1].update(kind='deliverable',template='command')
                bad['workflow'][1].pop('program')
            contract=R.compile_request(self.catalog,q.replace(original,phrase),bad)
            self.assertTrue(contract['request']['workflow'][1]['unresolved'])

    def test_explicit_result_monitor_freezes_output_while_query_monitor_keeps_parent_input(self):
        q,p,_,_=self.composed()
        p['workflow'][2]['scope']='result'
        contract=R.compile_request(self.catalog,q,p,population=['A','B','D'])
        receipt={'version':S.VERSION,'policy_identity':S.policy_identity(),'request_contract':contract,'interpretation':{'execution_accepted':True},'new_paid_api_calls':0}
        result=S.execute_contract(self.stack,q,S.graph_for(contract),receipt,population=['A','B','D'])
        c=A.capture(self.path,q,result)
        self.assertEqual(W.materialize(self.path,c['id'])['steps'][2]['patient_scope'],['A','B'])
        p['workflow'][2]['scope']='query'
        contract=R.compile_request(self.catalog,q,p,population=['A','B','D'])
        receipt['request_contract']=contract
        result=S.execute_contract(self.stack,q,S.graph_for(contract),receipt,population=['A','B','D'])
        c=A.capture(self.path,q,result)
        self.assertEqual(W.materialize(self.path,c['id'])['steps'][2]['patient_scope'],['A','B','D'])

    def test_parent_quotes_cannot_hide_omitted_leaf(self):
        q='List patients with diabetes and CKD'
        p=L.parse(self.catalog,'List patients with diabetes')
        p['source']['quote']=q
        with self.assertRaises(R.PlanError):R.compile_request(self.catalog,q,p)
        p['source']['quote']='patients'
        p['where']['quote']='with diabetes and CKD'
        with self.assertRaisesRegex(R.PlanError,'CKD'):R.compile_request(self.catalog,q,p)

    def test_ambiguous_boolean_scope_does_not_fall_back_to_model_guess(self):
        model=Mock(side_effect=AssertionError('Ambiguity must remain explicit'))
        # "with diabetes or CKD" is one condition group under "with" (2026-10-21): over 65 and (diabetes or CKD).
        grouped=S.compose(self.stack,'List patients over 65 with diabetes or CKD',model=model)
        r=S.compose(self.stack,'List patients over 65 and (diabetes or CKD)',model=model)
        self.assertEqual(r['answer']['patient_ids'],['A','C','OUTSIDE'])
        self.assertEqual(grouped['answer']['patient_ids'],r['answer']['patient_ids'])
        # A genuinely mixed AND/OR over different kinds of predicates still needs parentheses.
        with self.assertRaises(QuestionNotUnderstood):
            S.compose(self.stack,'List patients over 65 and diabetes or age < 30',model=model)
        model.assert_not_called()

    def test_briefing_failure_isolated_and_each_card_reopens_exact_answer(self):
        with patch.object(chief_staff,'CHECKS',[
            ('claims','Claims','Count claims where ever_denied = 1 by payer for our clinical utilization report','Review','Open'),
            ('broken','Unsupported','Count imaginary_warehouse','Missing','Open')]):
            r=chief_staff.cached(self.path)
            self.assertEqual(len(r['cards']),1)
            self.assertEqual([x['status'] for x in r['checks']],['computed','unavailable'])
            c=A.load(self.path,r['cards'][0]['context_id'])
            self.assertEqual(c['answer']['table'][0],{'payer':'P1','count':2})
            self.assertTrue(chief_staff.cached(self.path)['cache_hit'])
            created=chief_staff.open_pathway(self.path,{'card_id':r['cards'][0]['card_id'],'pathway':'deliverable'})
            self.assertEqual(created['workflow']['steps'][0]['status'],'ready')


if __name__=='__main__':
    unittest.main()
