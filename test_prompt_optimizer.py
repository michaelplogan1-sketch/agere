"""Scripted-provider tests exercise the real planner/compiler/executor.

These establish optimizer mechanics, not measured improvement in a hosted model.
"""
import json
import unittest

import test_shared_planner as fixtures
from tools.learning_loop import prompt_optimizer as O


def cases():
    return [{'id': name, 'family': family, 'split': split, 'question': question,
             'expected': {'value': expected}, 'provenance': 'authored_development'}
            for name, family, split, question, expected in [
                ('roster', 'roster_count', 'train', 'Count registered patients', 5),
                ('billing', 'claim_count', 'validation', 'Count received claims', 5),
                ('access', 'appointment_count', 'test', 'Count scheduled appointments', 2)]]


class PromptOptimization(unittest.TestCase):
    setUp = fixtures.SharedPlanner.setUp

    def model(self, system, user, **kwargs):
        data = json.loads(user)
        if 'question' not in data:
            return json.dumps({'equivalent': True, 'issues': []}), {'api_usd': .01}
        question = data['question']
        table = {'Count registered patients': 'patient', 'Count received claims': 'claim',
                 'Count scheduled appointments': 'appointment'}[question]
        restriction = '' if 'Do not add an unrequested row filter.' in system else ' WHERE rowid=1'
        return json.dumps({'analysis': {'steps': [{'name': 'total', 'sql': f'SELECT COUNT(*) AS n FROM {table}{restriction}'}],
                    'returns': 'count', 'requirements': [{'quote': question, 'applied_in': ['total']}],
                    'unresolved': []}}), {'api_usd': .02}

    def test_reflection_uses_only_training_and_executes_candidates_under_equal_limits(self):
        prompts = []
        def reflect(system, user, **kwargs):
            prompts.append(json.loads(user))
            return json.dumps({'proposal': 'Do not add an unrequested row filter.'}), {'api_usd': .03}
        result = O.optimize(self.catalog, cases(), self.model, reflect, max_candidates=2)
        self.assertEqual([c['id'] for c in prompts[0]['training_failures']], ['roster'])
        self.assertNotIn('billing', json.dumps(prompts))
        self.assertEqual(result['evaluated'][0]['train']['passed'], 0)
        self.assertEqual(result['evaluated'][1]['train']['passed'], 1)
        self.assertEqual(result['evaluated'][1]['validation']['passed'], 1)
        self.assertTrue(result['final_test'][result['selected']['id']][0]['passed'])
        self.assertEqual(result['activation'], 'candidate_only')
        self.assertFalse((self.path/'learning.sqlite3').exists())
        self.assertFalse((self.path/'question-plans.sqlite3').exists())

    def test_rejects_shared_families_numeric_variants_and_non_development_cases(self):
        for mutate in ('family', 'variant', 'provenance'):
            data = cases()
            if mutate == 'family': data[1]['family'] = data[0]['family']
            if mutate == 'variant':
                data[0]['question'], data[1]['question'] = 'Count patients in 2029', 'Count patients in 2028'
            if mutate == 'provenance': data[0]['provenance'] = 'reserved_evaluation'
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): O.validate_cases(data)

    def test_failed_calls_stay_in_denominator_and_candidate_must_match_base(self):
        def failing(*args, **kwargs): raise RuntimeError('provider failure')
        row = O.trial(self.catalog, cases()[0], O.candidate(), failing, max_calls=1)
        self.assertFalse(row['passed'])
        self.assertEqual(row['calls'], 1)
        self.assertIn('provider failure', row['error'])
        tampered = O.candidate()
        tampered['base_prompts'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'base prompts changed'):
            O.TrialModel(self.model, tampered, 1, 100)

    def test_unknown_prompt_roles_and_mutating_queries_cannot_bypass_compiler(self):
        with self.assertRaises(ValueError): O.candidate({'sql': 'DROP TABLE patient'})
        def malformed(system, user, **kwargs):
            return json.dumps({'analysis': {'steps': [{'name': 'bad', 'sql': 'DELETE FROM patient'}], 'returns': 'count',
                              'requirements': [{'quote': 'Count registered patients', 'applied_in': ['bad']}], 'unresolved': []}}), {}
        row = O.trial(self.catalog, cases()[0], O.candidate(), malformed, max_calls=2)
        self.assertFalse(row['passed'])
        self.assertEqual(row['calls'], 2)
        self.assertEqual(O.trial(self.catalog, cases()[0], O.candidate({'proposal': 'Do not add an unrequested row filter.'}),
                                self.model)['actual']['value'], 5)

    def test_reflector_failure_keeps_failed_trials_and_does_not_spend_on_final_test(self):
        def fail(*a, **k): raise RuntimeError('reflection unavailable')
        report = O.optimize(self.catalog, cases(), self.model, fail, max_candidates=2)
        self.assertEqual(report['evaluated'][0]['train']['passed'], 0)
        self.assertEqual(report['final_test'], {})
        self.assertTrue(report['reflection_failures'])


if __name__ == '__main__': unittest.main()
