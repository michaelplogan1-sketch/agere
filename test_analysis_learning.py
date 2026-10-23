"""Authored learning regressions on independent tiny data; no evaluation corpus."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

import test_shared_planner as fixtures
from test_general_analysis import spec, Q
from clinical_core.agere.clinical import general_analysis as G, learning, shared_planner as S, request_plan as R

ACCEPTED = {'review': 'checked_hosted_interpretation', 'execution_accepted': True,
            'semantic_review': {'equivalent': True, 'issues': []}}


class AnalysisLearning(unittest.TestCase):
    setUp = fixtures.SharedPlanner.setUp

    def seed(self, question=Q, analysis=None, population=None, policy='p1'):
        c = G.prepare(self.catalog, question, analysis or spec(), population=population)
        return G.save_shape(self.path, c, policy, ACCEPTED)

    def test_correction_revokes_family_exact_derivatives_and_cannot_be_reseeded(self):
        ident = self.seed()
        newer = Q.replace('2029', '2028')
        reused = G.lookup_shape(self.path, self.catalog, newer, 'p1')
        self.assertIsNotNone(reused)
        with sqlite3.connect(self.path/'question-plans.sqlite3') as db:
            db.execute('CREATE TABLE shared_requests(id TEXT PRIMARY KEY,payload TEXT)')
            db.execute('INSERT INTO shared_requests VALUES (?,?)', ('exact', json.dumps({
                'question': newer, 'analysis': reused[0]['analysis'], 'interpretation': reused[1]})))
        feedback = learning.record_correction(self.path, ' '+Q.upper()+' ', 'The event clock is wrong', {'requires_review': True})
        self.assertEqual(feedback['status'], 'needs_revalidation')
        self.assertFalse(G.valid_shape(self.path, ident))
        self.assertIsNone(G.lookup_shape(self.path, self.catalog, newer, 'p1'))
        self.assertIsNone(self.seed(question=newer, analysis=spec('2028'), policy='new-code'))
        with sqlite3.connect(self.path/'question-plans.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM shared_requests').fetchone()[0], 0)
        self.assertEqual(learning.summary(self.path)['reusable_structures']['analysis_shapes'], {'revoked': 1})

    def test_feedback_is_supplied_to_new_interpretation_and_review_without_promoting_expected(self):
        learning.record_correction(self.path, Q, 'Recheck dates', expected=999)
        other = Q.replace('2029', '2028')
        model = Mock(side_effect=[(json.dumps({'analysis': spec('2028')}), {}),
                                  (json.dumps({'equivalent': True, 'issues': []}), {})])
        out = S.compose(self.stack, other, model=model)
        self.assertEqual(out['answer']['count'], 0)
        self.assertEqual(out['answer']['status'], 'partial')
        for call in model.call_args_list:
            feedback = json.loads(call.args[1])['reported_corrections'][0]
            self.assertEqual(feedback['expected'], 999)
            self.assertEqual(feedback['authority'], 'reported_feedback; not independently verified truth')
        self.assertNotIn('learned_analysis_shape', out)

    def test_parameter_reuse_does_not_touch_boolean_constants_or_cascade_swaps(self):
        q = 'Show patients with 1 or more ED visits in 2029'
        s = {'steps': [{'name': 'visits', 'purpose': 'received events', 'sql':
             "SELECT patient_id, 1 AS fixed_flag, COUNT(*) AS n FROM utilization_event WHERE kind='ed_visit' "
             "AND event_date BETWEEN {{parameter:year}} || '-01-01' AND {{parameter:year}} || '-12-31' "
             'GROUP BY patient_id HAVING COUNT(*) >= {{parameter:minimum}}'}],
             'returns': 'table', 'requirements': [{'quote': q, 'applied_in': ['visits'], 'how': 'Count events'}],
             'parameters': [{'name': 'minimum', 'value': 1, 'quote': '1 or more'}, {'name': 'year', 'value': 2029, 'quote': '2029'}],
             'unresolved': []}
        self.assertTrue(self.seed(q, s))
        changed = 'Show patients with 2029 or more ED visits in 2030'
        c, _ = G.lookup_shape(self.path, self.catalog, changed, 'p1')
        self.assertIn('1 AS fixed_flag', c['query'])
        self.assertIn('>= 2029', c['query'])
        self.assertIn("2030 || '-01-01'", c['query'])
        self.assertEqual(c['analysis']['steps'][0]['sql'], s['steps'][0]['sql'])

    def test_unbound_legacy_numbers_and_cross_population_never_reuse(self):
        s = spec(); s.pop('parameters')
        s['steps'][0]['sql'] = s['steps'][0]['sql'].replace('{{parameter:year}}', '2029')
        self.assertIsNone(self.seed(analysis=s))
        self.assertTrue(self.seed(population=['A']))
        self.assertIsNone(G.lookup_shape(self.path, self.catalog, Q.replace('2029', '2028'), 'p1', ['B']))
        # A different policy key means a different code release: the checked shape is recompiled under the current
        # code and marked carry_forward (03354fd), not dropped. Only a different population may never reuse it.
        carried = G.lookup_shape(self.path, self.catalog, Q.replace('2029', '2028'), 'different-policy', ['A'])
        self.assertIsNotNone(carried)
        self.assertIn('earlier code release', carried[1]['carry_forward'])
        self.assertIn('2028', carried[0]['query'])

    def test_invalid_parameters_and_unreviewed_shapes_cannot_enter_learning(self):
        for parameter in [{'name': 'year', 'value': True, 'quote': '2029'},
                          {'name': 'year', 'value': 2028, 'quote': '2029'},
                          {'name': 'year', 'value': float('nan'), 'quote': '2029'}]:
            s = spec(); s['parameters'] = [parameter]
            with self.assertRaises(R.PlanError): G.prepare(self.catalog, Q, s)
        c = G.prepare(self.catalog, Q, spec())
        self.assertIsNone(G.save_shape(self.path, c, 'p1', {'execution_accepted': True}))

    def test_ambiguous_output_labels_and_fixed_definitions_require_new_interpretation(self):
        s = spec(); s['steps'][1]['sql'] = s['steps'][1]['sql'].replace('AS patients', 'AS patients_2029')
        self.seed(analysis=s)
        self.assertIsNone(G.lookup_shape(self.path, self.catalog, Q.replace('2029','2028'), 'p1'))
        s = spec(); s['definitions'].append({'term': 'fixed policy', 'basis': '2029 policy version'})
        self.seed(analysis=s, policy='p2')
        self.assertIsNone(G.lookup_shape(self.path, self.catalog, Q.replace('2029','2028'), 'p2'))

    def test_disagreeing_structures_suspend_learning(self):
        ident = self.seed()
        s = spec(); s['steps'][1]['sql'] = s['steps'][1]['sql'].replace('>= 2', '>= 3')
        self.assertIsNone(self.seed(analysis=s))
        self.assertFalse(G.valid_shape(self.path, ident))

    def test_rescue_analysis_repairs_semantic_errors_instead_of_immediately_returning_them(self):
        wrong = spec(); wrong['steps'][1]['sql'] = wrong['steps'][1]['sql'].replace('>= 2', '>= 1')
        bad = (json.dumps({'source': {'table': 'absent', 'quote': 'patients'}}), {})
        model = Mock(side_effect=[bad] * 3 + [(json.dumps({'analysis': wrong}), {}),
            (json.dumps({'equivalent': False, 'issues': ['Wrong event threshold']}), {}),
            (json.dumps({'analysis': spec()}), {}), (json.dumps({'equivalent': True, 'issues': []}), {})])
        out = S.compose(self.stack, Q, model=model)
        self.assertEqual(out['answer']['count'], 1)
        self.assertEqual(model.call_count, 7)
        feedback = json.loads(model.call_args_list[5].args[1])['validator_feedback']['last_feedback']
        self.assertIn('Wrong event threshold', feedback['semantic_review']['issues'])

    def test_final_compiling_proposal_still_gets_semantic_repair(self):
        wrong = spec(); wrong['steps'][1]['sql'] = wrong['steps'][1]['sql'].replace('>= 2', '>= 1')
        bad = (json.dumps({'source': {'table': 'absent', 'quote': 'patients'}}), {})
        model = Mock(side_effect=[bad] * 2 + [(json.dumps({'analysis': wrong}), {}),
            (json.dumps({'equivalent': False, 'issues': ['Wrong event threshold']}), {}),
            (json.dumps({'analysis': spec()}), {}), (json.dumps({'equivalent': True, 'issues': []}), {})])
        out = S.compose(self.stack, Q, model=model)
        self.assertEqual(out['answer']['count'], 1)
        self.assertEqual(model.call_count, 6)

    def test_unaccepted_interpretations_do_not_poison_the_exact_cache(self):
        c = G.prepare(self.catalog, Q, spec())
        candidate = {'execution_accepted': False, 'review': 'model_semantic_review', 'attempts': []}
        with patch.object(S, 'checked_interpret', return_value=(c, candidate)) as interpret:
            S.plan(self.stack, Q)
            S.plan(self.stack, Q)
        self.assertEqual(interpret.call_count, 2)
        with sqlite3.connect(self.path/'question-plans.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM shared_requests').fetchone()[0], 0)

    def test_review_receives_actual_source_meanings_and_family_coverage(self):
        model = Mock(side_effect=[(json.dumps({'analysis': spec()}), {}),
                                  (json.dumps({'equivalent': True, 'issues': []}), {})])
        S.compose(self.stack, Q, model=model)
        audit = json.loads(model.call_args_list[1].args[1])
        self.assertIn('utilization_event', audit['source_schemas'])
        self.assertEqual(audit['source_coverage']['medical_claims']['default_relation'], 'claim')
        self.assertIn('unknown', audit['source_coverage']['medication_observations']['meaning'])

    def test_correction_invalidates_saved_work_authority_and_recheck_gets_new_context(self):
        from clinical_core.agere.deliverables import answer_context as A
        self.stack.allow_live = True
        self.stack.receipts = self.path
        model = Mock(side_effect=[(json.dumps({'analysis': spec()}), {}),
                                  (json.dumps({'equivalent': True, 'issues': []}), {})] * 2)
        with patch('clinical_core.agere.clinical.nl2sql.HostedModel', return_value=model), \
             patch('clinical_core.agere.gateway.default_ledger_path', return_value=self.path/'ledger.sqlite3'):
            first = A.capture(self.path, Q, S.compose(self.stack, Q))
            learning.record_correction(self.path, Q, 'Please verify the event window')
            self.assertTrue(A.load(self.path, first['id'])['invalidated'])
            with self.assertRaisesRegex(ValueError, 'invalidated'):
                A.load(self.path, first['id'], current=True)
            second = A.capture(self.path, Q, S.compose(self.stack, Q))
            self.assertNotEqual(first['id'], second['id'])
            self.assertFalse(A.load(self.path, second['id'], current=True)['invalidated'])


if __name__ == '__main__': unittest.main()
