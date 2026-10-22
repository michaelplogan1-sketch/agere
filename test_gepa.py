"""GEPA reflective prompt optimization: Pareto front, minibatch-gated reflective mutation, merge, the deterministic
reflector, the System One dry-run evaluator and failure cases from recorded traffic (no network, no Claude)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from tools.training import gepa as G, datasets as D  # noqa: E402

_Bench = D.Bench


def case(i, feedback, split='train'):
    return {'id': f'c{i}', 'family': f'f{i}', 'question': f'question {i}?', 'feedback': feedback, 'split': split}


def toy_evaluate(additions, c):
    """1.0 when the guidance mentions the failure's key word (a stand-in for a live trial)."""
    text = ' '.join(additions.values()).lower()
    key = {'shape': 'requested output', 'window': 'time phrase', 'group': 'group_by'}[c['feedback'].split(':')[0]]
    return {'score': 1.0 if key in text else 0.0, 'feedback': c['feedback'], 'calls': 1, 'usd': 0.001}


class Engine(unittest.TestCase):
    def test_pareto_front(self):
        scores = {'a': {'x': 1, 'y': 0}, 'b': {'x': 0, 'y': 1}, 'c': {'x': 0, 'y': 0.5}, 'd': {'x': 1, 'y': 1}}
        self.assertEqual(G.pareto_front(scores), ['d'])
        self.assertEqual(G.pareto_front({k: v for k, v in scores.items() if k != 'd'}), ['a', 'b'])

    def test_merge_unions_sentences(self):
        m = G.merge({'proposal': 'One. Two.'}, {'proposal': 'Two. Three.', 'typed_review': 'R.'})
        self.assertEqual(m, {'proposal': 'One. Two. Three.', 'typed_review': 'R.'})

    def test_optimize_improves_and_keeps_baseline(self):
        train = [case(1, 'shape: answer shape mismatch, asks for a count'), case(2, 'window: the time window was dropped'),
                 case(3, 'group: the breakdown by payer was missing')]
        valid = [case(4, 'shape: returned a table for a count', 'validation'), case(5, 'group: per practice missing', 'validation')]
        rep = G.optimize(train, valid, toy_evaluate, G.rule_reflector, budget=60, minibatch=3, seed=3)
        self.assertEqual(rep['activation'], 'candidate_only')
        self.assertEqual(rep['validation_mean'][rep['baseline']], 0.0)
        self.assertGreater(rep['validation_mean'][rep['selected']], 0.0)
        self.assertIn(rep['selected'], rep['pool']); self.assertGreater(rep['spend']['calls'], 0)
        self.assertTrue(all(set(a) <= set(G.ROLES) for a in rep['pool'].values()))

    def test_rule_reflector_adds_one_general_correction(self):
        out = G.rule_reflector({}, [{'case': case(1, ''), 'feedback': 'Unaccounted original request text: about', 'score': 0}])
        self.assertIn('Every phrase', out['proposal'])
        self.assertNotRegex(out['proposal'], r'P-\d|SELECT')  # general guidance only


class Evaluators(unittest.TestCase):
    def test_system_one_evaluator(self):
        fake = mock.Mock(return_value={'label': 'addresses', 'calibrated_probabilities': {'addresses': 0.7, 'partly': 0.2, 'no': 0.1},
                                       'decided_by': 'jev', 'steps': [{'status': 'answered', 'cache_hit': False}], 'cost_usd': 0.0001})
        with mock.patch('clinical_core.agere.clinical.system_one.decide', fake):
            r = G.system_one_evaluator('2030-05-27')({'proposal': 'Match the requested output exactly.'}, case(1, 'answer shape'))
        self.assertEqual(r['score'], 0.8); self.assertEqual(r['calls'], 1); self.assertEqual(r['usd'], 0.0001)
        self.assertIn('Match the requested output', fake.call_args[0][2][0]['text'])

    def test_cases_from_recorded_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rows = [{'question': f'How many widgets {i} were in 2029?', 'status': 'error',
                     'proposers': [{'proposer': 'bottled_sql', 'outcome': 'rejected', 'detail': 'answer shape: asks for a count'}]} for i in range(6)]
            rows.append({'question': "{'text': 'garbled'}", 'proposers': [{'proposer': 'x', 'outcome': 'rejected', 'detail': 'y'}]})
            (d / 'gaps-1.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
            with mock.patch.object(D, 'Bench', lambda questions=None: _Bench([])):
                got = G.cases_from([d], limit=10)
        self.assertEqual(len(got), 6)
        self.assertTrue(any(c['split'] == 'validation' for c in got))
        self.assertIn('bottled_sql: answer shape', got[0]['feedback'])


if __name__ == '__main__':
    unittest.main()
