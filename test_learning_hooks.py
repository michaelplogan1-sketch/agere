"""learning_hooks.after_answer: verified cascade answers become free learned pairs and training rows; nothing else does."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from clinical_core.agere.clinical import learning, learning_hooks as H  # noqa: E402


def cascade_result(proposer='typed_decider', status='answered', paid=1, route=None, query='SELECT COUNT(*) AS n FROM patient', checks=None):
    return {'answer': {'status': status, 'answer_type': 'count', 'count': 7, 'missing': [], 'quality_checks': checks or []},
            'graph': {'nodes': [{'id': 'answer', 'operation': 'sql', 'parameters': {'query': query, 'returns': 'count'}}]},
            'request_contract': {'kind': 'checked_graph', 'id': 'c1'},
            'planner_receipt': {'route': route or proposer, 'interpretation': {
                'execution_accepted': True, 'interpreter': proposer,
                'proposer': {'name': proposer, 'cost': 'Perplexity, ~$0.000006/call', 'check': 'literal-check/1'},
                'proposers': [{'proposer': 'local_normalizer', 'outcome': 'no proposal', 'detail': None}]}},
            'new_paid_api_calls': paid}


class Hooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.path = root / 'stack'; self.path.mkdir()
        (self.path / 'run.json').write_text(json.dumps({'source_digest': 'fixture', 'snapshot': {'as_of': '2030-05-27'}}))
        self.stack = SimpleNamespace(path=self.path, allow_live=False)
        self.env = mock.patch.dict(os.environ, {'AGERE_TRAINING_DIR': str(root / 'training')})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def traffic(self, name='labels-traffic'):
        return [json.loads(l) for p in H.training_dir().glob(f'{name}-*.jsonl') for l in p.read_text().splitlines()]

    def test_paid_cascade_answer_is_saved_for_free_reuse_and_corpus(self):
        q = 'How many members are on file?'
        r = H.after_answer(self.stack, q, cascade_result())
        self.assertTrue(r['verified']); self.assertEqual(r['errors'], [])
        self.assertTrue(r['saved_pair']['saved']); self.assertTrue(r['saved_pair']['paid'])
        hit = learning.lookup(self.path, q)  # the learned_verified_sql tier now answers this exact question for free
        self.assertEqual(hit['plan']['query'], 'SELECT COUNT(*) AS n FROM patient')
        self.assertTrue(hit['agreement'].startswith('cascade:typed_decider'))
        rows = self.traffic()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['status'], rows[0]['family'], rows[0]['proposer'], rows[0]['paid']), ('ok', 'traffic', 'typed_decider', True))
        self.assertEqual(rows[0]['provenance']['tables'], ['patient'])

    def test_replays_are_not_saved_under_new_wording(self):
        for proposer in sorted(H.REPLAYS):
            r = H.after_answer(self.stack, f'question via {proposer}', cascade_result(proposer, paid=0))
            self.assertIsNone(r['saved_pair'], proposer)
        self.assertIsNone(learning.lookup(self.path, 'question via semantic_cache'))
        self.assertEqual({x['proposer'] for x in self.traffic()}, {'paraphrase_reuse', 'local_normalizer'})

    def test_unverified_answers_are_gaps_not_lessons(self):
        r = H.after_answer(self.stack, 'partial one', cascade_result(status='partial'))
        self.assertFalse(r['verified']); self.assertIsNone(r['saved_pair'])
        self.assertIsNone(learning.lookup(self.path, 'partial one'))
        self.assertEqual(self.traffic(), [])
        self.assertEqual(self.traffic('gaps')[0]['question'], 'partial one')

    def test_quality_warning_quarantines_through_correction_record(self):
        q = 'flagged question'
        r = H.after_answer(self.stack, q, cascade_result(checks=[{'control': 'QC-UNITS', 'severity': 'warning'}]))
        self.assertFalse(r['verified']); self.assertEqual(r['quarantine'], ['QC-UNITS'])
        self.assertTrue(learning.corrected(self.path, q))
        self.assertIsNone(learning.lookup(self.path, q))

    def test_hosted_ladder_keeps_its_own_capture(self):
        r = H.after_answer(self.stack, 'hosted one', cascade_result('hosted_verified_sql', route='hosted_verified_sql'))
        self.assertIsNone(r['saved_pair'])
        self.assertEqual(r['quarantine'], [])

    def test_typed_contract_pair_and_request_in_corpus(self):
        result = cascade_result('hosted_typed_interpreter')
        result['graph'] = {'nodes': [{'id': 'population', 'operation': 'query_population', 'parameters': {}}]}
        result['request_contract'] = {'kind': None, 'id': 'r1', 'query': 'SELECT 1 AS n', 'returns': 'count',
                                      'request': {'source': {'table': 'patient', 'quote': 'members'}, 'select': [], 'analyses': []}}
        r = H.after_answer(self.stack, 'typed one', result)
        self.assertTrue(r['saved_pair']['saved'])
        row = self.traffic()[0]
        self.assertEqual(row['request']['kind'], 'request')
        self.assertEqual(row['provenance']['query'], 'SELECT 1 AS n')

    def test_explicit_components_dispatch(self):
        with mock.patch('clinical_core.agere.clinical.question_learning.prepare', return_value={'id': 'task1'}) as ql, \
             mock.patch('clinical_core.agere.clinical.learning_queue.suggest', return_value={'id': 's1'}) as lq, \
             mock.patch('clinical_core.agere.clinical.followup_answers.attach', return_value={'id': 'f1'}) as fa:
            body = {'learning': {'question_learning': {'candidate_id': 'c'}, 'learning_queue': {'item_id': 'i', 'provider': 'student_minilm'},
                                 'followup_answer': {'task_id': 'a' * 64, 'followup_id': 'b' * 64, 'answer_id': 'c' * 64},
                                 'correction': {'note': 'payer should be the current plan'}}}
            r = H.after_answer(self.stack, 'corrected later', cascade_result(), body=body)
        c = r['components']
        self.assertEqual(c['question_learning'], {'task_id': 'task1'})
        self.assertEqual(c['learning_queue_suggest']['id'], 's1')
        self.assertEqual(c['followup_answers'], {'id': 'f1'})
        self.assertTrue(c['correction_record']['recorded'])
        ql.assert_called_once(); fa.assert_called_once()
        self.assertEqual(lq.call_args.kwargs['allow_live'], False)
        self.assertTrue(learning.corrected(self.path, 'corrected later'))
        # corrected traffic never trains: datasets.rows excludes it
        from tools.training import datasets as D
        self.assertEqual([x['canonical'] for x in D.rows(H.training_dir())], [])

    def test_paid_queue_provider_needs_live(self):
        with mock.patch('clinical_core.agere.clinical.learning_queue.suggest') as lq:
            r = H.after_answer(self.stack, 'q', cascade_result(), body={'learning': {'learning_queue': {'item_id': 'i', 'provider': 'jev'}}})
        lq.assert_not_called()
        self.assertIn('skipped', r['components']['learning_queue_suggest'])

    def test_never_raises(self):
        r = H.after_answer(self.stack, 'q', None)
        self.assertFalse(r['verified'])
        r = H.after_answer(SimpleNamespace(path=Path(self.tmp.name) / 'missing'), 'q', cascade_result())
        self.assertIsInstance(r['errors'], list)
        self.assertIsNotNone(H.after_failure(self.stack, 'q', ValueError('nobody could read it')))

    def test_traffic_rows_feed_the_dataset_builder(self):
        H.after_answer(self.stack, 'How many members are on file?', cascade_result())
        from tools.training import datasets as D
        rows = list(D.rows(H.training_dir()))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['answer_key'], self.traffic()[0]['answer_key'])


if __name__ == '__main__':
    unittest.main()
