"""Complete native continuations with authored scores; no model qualification."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import decision_evaluation as E
from clinical_core.agere.request_lineage import Execution


class DecisionLoopEvaluation(unittest.TestCase):
    def rows(self,kind='route',threshold=.95):
        rows=[]
        for partition in ('fit','validate'):
            for i in range(8):
                correct=i%2==0
                # A wrong operator changes downstream filtering and the FINAL
                # answer. Both arms start from a separate actual SQLite database.
                def run(state,choice):
                    with sqlite3.connect(':memory:')as db:
                        db.execute('CREATE TABLE invoice(amount INTEGER)')
                        db.executemany('INSERT INTO invoice VALUES (?)',[(x,)for x in state['amounts']])
                        aggregate=db.execute('SELECT '+('SUM(amount)'if choice=='sum'else'COUNT(*)')+' FROM invoice').fetchone()[0]
                        # Dependent work sees the actual selected operator result.
                        selected=db.execute('SELECT amount FROM invoice WHERE amount < ? ORDER BY amount',(aggregate/2,)).fetchall()
                    return {'total':aggregate,'selected':[x[0]for x in selected],'cost':1.0 if choice=='sum'else .1}
                def judge(result):
                    return {'success':result['total']==12 and result['selected']==[1,1],
                        'cost':1.0 if result['cost']==1 else .1,'receipt':'locked native arithmetic fixture; simulated cost units'}
                # Even correct proposals have lower simulated continuation cost;
                # the evaluation tests policy plumbing, not a model's saving.
                def candidate_run(state,choice):
                    value=run(state,choice['operation']);value['cost']=choice['cost'];return value
                row=E.evaluate_pair(state={'amounts':[10,1,1]},incumbent={'operation':'sum','cost':1},
                    proposal={'operation':'sum'if correct else'count','cost':.1},run=candidate_run,judge=judge,
                    guard=lambda:{'source':'same','rules':'same'},decision_type=kind,model='authored_decider',
                    confidence=threshold if correct else threshold-.3,family=f'{kind}-{partition}-{i}',partition=partition,
                    identity={'rules':'native-fixture-1'},evidence_kind='authored_fixture',outcome_basis='Locked total and filtered row expectations')
                rows.append(row)
        return rows

    def test_thresholds_depend_on_complete_task_outcomes_and_decision_type(self):
        rows=self.rows()+self.rows('operator_choice',.7)+self.rows('clarify_or_not',.9)
        fitted=E.fit(rows,min_pairs=4,probe_cost=.01,include_fixtures=True)
        groups={g['decision_type']:g for g in fitted['groups']}
        self.assertEqual({k:g['threshold']for k,g in groups.items()},{'route':.95,'operator_choice':.7,'clarify_or_not':.9})
        for g in groups.values():
            self.assertTrue(g['empirical_validation_passed']);self.assertFalse(g['threshold_activation'])
            self.assertEqual(g['validation']['fitted']['policy_successes'],8)
            self.assertEqual(g['validation']['always_delegate']['policy_successes'],4)
        self.assertFalse(fitted['runtime_policy_changed'])

    def test_authored_outcomes_cannot_fit_an_observed_threshold(self):
        result=E.fit(self.rows(),min_pairs=4,probe_cost=.01)
        self.assertEqual(result['status'],'insufficient_outcome_pairs')
        self.assertEqual(result['excluded']['not_observed'],16)

    def test_missing_cost_is_unknown_and_does_not_establish_savings(self):
        result=E.fit(self.rows(),min_pairs=4,include_fixtures=True)
        self.assertIsNone(result['groups'][0]['threshold'])
        self.assertEqual(result['groups'][0]['status'],'retain_incumbent_no_supported_saving')

    def test_fit_validation_family_overlap_is_rejected(self):
        rows=self.rows();rows[-1]['family']=rows[0]['family']
        with self.assertRaisesRegex(ValueError,'source-family overlap'):E.fit(rows,include_fixtures=True)

    def test_changed_execution_policy_cannot_pool_old_threshold_evidence(self):
        rows=self.rows()
        for row in rows:
            if row['partition']=='validate':row['policy_identity']='changed-rules'
        result=E.fit(rows,min_pairs=4,probe_cost=.01,include_fixtures=True)
        self.assertTrue(all(g['threshold']is None for g in result['groups']))

    def test_validation_failures_do_not_get_refitted_on_validation_labels(self):
        rows=self.rows()
        for row in rows:
            if row['partition']=='validate':row['arms']['proposal']['success']=False
        g=E.fit(rows,min_pairs=4,probe_cost=.01,include_fixtures=True)['groups'][0]
        self.assertEqual(g['threshold'],.95);self.assertEqual(g['status'],'validation_failed_retain_incumbent')

    def test_journal_preserves_errors_and_deduplicates_exact_pair_ids(self):
        rows=self.rows()
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)
            with Execution(path,'/api/stack/compose',{})as execution:
                E.record_pair(rows[0]);E.record_pair(rows[0])
            pairs,counts=E.read_journal(path/'program-work.sqlite3')
            self.assertEqual(counts['decision_pair_evaluated'],2)
            result=E.fit(pairs,include_fixtures=True)
            self.assertEqual(result['excluded']['duplicate_pair'],1)
            with sqlite3.connect(path/'program-work.sqlite3')as db:
                with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM question_request_events')

    def test_both_arms_are_attempted_and_guard_drift_aborts(self):
        calls=[]
        def run(state,choice):calls.append(choice);raise RuntimeError('retained failure')
        row=E.evaluate_pair(state={},incumbent='a',proposal='b',run=run,judge=lambda r:{},guard=lambda:{},
            decision_type='route',model='fixture',confidence=.9,family='f',partition='fit',identity={},
            evidence_kind='authored_fixture',outcome_basis='locked fixture')
        self.assertEqual(calls,['a','b']);self.assertTrue(all(a['cost']is None for a in row['arms'].values()))
        states=iter([{}, {'changed':True}])
        with self.assertRaisesRegex(ValueError,'changed during paired'):
            E.evaluate_pair(state={},incumbent='a',proposal='b',run=lambda *a:{},judge=lambda r:{'success':True,'cost':0},
                guard=lambda:next(states),decision_type='route',model='fixture',confidence=.9,family='f',partition='fit',
                identity={},evidence_kind='authored_fixture',outcome_basis='locked fixture')

    def test_confidence_logging_makes_no_outcome_claim_or_selection(self):
        with patch.object(E,'execution_observation')as record:
            E.observe(decision_type='route',model='fixture',confidence=.8,proposal='x',incumbent='y',identity={},inputs={})
        self.assertFalse(record.call_args.kwargs['outcome_label_available'])
        self.assertFalse(record.call_args.kwargs['threshold_activation'])

    def test_router_and_small_model_observations_do_not_add_calls_or_change_order(self):
        from types import SimpleNamespace
        from clinical_core.agere.clinical import system_one_gate as G,tier_router as T
        from test_system_one_gate import FakeS1,clean_env
        fake=FakeS1('literal_lookup',.9);stack=SimpleNamespace(allow_live=True,retriever=SimpleNamespace(meta=None))
        G._ROUTE_CACHE.clear()
        order=('exact_local_plan','backstop_agent','local_normalizer')
        with clean_env(),patch.object(G,'s1',return_value=fake),patch.object(E,'execution_observation')as recorded:
            routed,receipt=G.route_receipt('Authored observed-data count',order,stack=stack)
            again,_=G.route_receipt('Authored observed-data count',order,stack=stack)
        self.assertEqual(len(fake.calls),1);self.assertEqual(routed,again)
        self.assertEqual(routed,G.reorder(order,'literal_lookup'))
        self.assertEqual(recorded.call_count,2)
        model={'version':T.VERSION,'feature_kind':'lexical','threshold':.8}
        with patch.object(T,'predict',return_value=('local_normalizer',.9)),patch.object(E,'execution_observation')as recorded:
            routed,receipt=T.reorder(stack,'Authored count',order,model=model)
        self.assertEqual(routed,('exact_local_plan','local_normalizer','backstop_agent'))
        self.assertEqual(recorded.call_args.kwargs['model'],'tier_router')


if __name__=='__main__':unittest.main()
