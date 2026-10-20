"""Mutation-boundary checks using authored synthetic rows and a scripted sender."""
import importlib
import json
import re
import sqlite3
import unittest
from unittest.mock import patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import shared_planner as S, learning
from clinical_core.agere.deliverables import answer_context as A


class AnswerDependencies(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)
        self.question = 'List patients with diabetes'
        self.result = S.compose(self.stack, self.question)
        self.key = A.capture(self.path, self.question, self.result)['id']

    def change(self):
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("UPDATE meta SET value='changed' WHERE key='source_digest'")

    def request(self):
        return {'context_id': self.key, 'program': 'custom', 'patient_ids': ['A'], 'owner': 'Fixture team', 'note': ''}

    def test_sealed_contents_and_legacy_answers_remain_inspectable_but_not_reusable(self):
        target = A.root(self.path)/self.key/'answer.json'
        original = json.loads(target.read_text())
        for mutation in ('tamper', 'legacy'):
            data = dict(original)
            if mutation == 'tamper':
                data['patient_ids'] = ['OUTSIDE']
            else:
                data.pop('dependencies')
            target.write_text(json.dumps(data))
            self.assertTrue(A.load(self.path, self.key)['invalidated'])
            with self.assertRaisesRegex(ValueError, 'invalidated'):
                A.create_work(self.path, self.request())

    def test_source_change_during_work_write_rolls_back_every_item(self):
        now = A.now
        def during_write():
            self.change()
            return now()
        with patch.object(A, 'now', side_effect=during_write), self.assertRaisesRegex(ValueError, 'data changed'):
            A.create_work(self.path, self.request())
        self.assertEqual(A.inspect(self.path, self.key)['items'], [])

    def test_wal_write_is_detected_even_without_main_file_stat_change(self):
        writer = sqlite3.connect(self.path/'semantic.sqlite3')
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        self.result = S.compose(self.stack, self.question)
        self.key = A.capture(self.path, self.question, self.result)['id']
        before = A.source_identity(self.path)
        writer.execute("UPDATE patient SET sex_reported='X' WHERE patient_id='A'")
        writer.commit()
        self.assertEqual(before, A.source_identity(self.path))
        context = A.load(self.path, self.key)
        self.assertTrue(context['stale'])
        self.assertFalse(context['dependency_validation'][0]['observation_current'])

    def deliver(self, mutation=None):
        def sender(body):
            if mutation:
                mutation()
            rid = re.search(r'"receipt":"(R-[0-9a-f]{10})","tool":"saved_answer"', body['messages'][0]['content']).group(1)
            return {'content': [{'type': 'text', 'text': 'TITLE: Evidence brief\n```html\n<h1>Evidence brief</h1>'
                    '<p>Synthetic evidence</p><p data-bind="'+rid+':answer.headline"></p>\n```'}],
                    'usage': {'input_tokens': 100, 'output_tokens': 100}}, {'cost_estimate': {'estimated_usd': .25}}
        return A.compose_frontier(self.path, {'context_id': self.key, 'request': 'Create an evidence brief'}, allow_live=True, send=sender)

    def test_generation_rechecks_source_and_feedback_before_publication_and_keeps_costs(self):
        for mutation in (self.change, lambda: learning.record_correction(self.path, self.question, 'Recheck the population')):
            with self.subTest(mutation=mutation):
                # Each scenario starts from an independently captured current answer.
                self.result = S.compose(self.stack, self.question)
                self.key = A.capture(self.path, self.question, self.result)['id']
                delivered = self.deliver(mutation)
                self.assertEqual(delivered['status'], 'rejected')
                self.assertIsNone(delivered['html'])
                self.assertIsNone(delivered['path'])
                self.assertIn('dependency_failure', delivered['verification'])
                self.assertEqual(delivered['new_paid_api_calls'], 1)
                self.assertAlmostEqual(delivered['costs']['known_api_estimate_usd'], .25)

    def test_generation_rechecks_work_versions_and_unchanged_generation_succeeds(self):
        item = A.create_work(self.path, self.request())['items'][0]
        def mutation():
            A.update_work(self.path, {'context_id': self.key, 'item_id': item['id'], 'revision': item['revision'],
                                     'state': 'in_review', 'owner': 'Fixture team', 'note': 'Updated during generation'})
        delivered = self.deliver(mutation)
        self.assertEqual(delivered['status'], 'rejected')
        self.assertIn('work changed', delivered['failure'])
        self.assertEqual(self.deliver()['status'], 'delivered')

    def test_old_frozen_deliverable_cannot_be_revised_after_work_changes(self):
        delivered = self.deliver()
        A.create_work(self.path, self.request())
        composer = importlib.import_module('clinical_core.agere.deliverables.compose')
        with self.assertRaisesRegex(ValueError, 'work changed'):
            composer.revise(A.workspace(self.path, A.load(self.path, self.key)), delivered['id'], 'Update title',
                            send=lambda *a: self.fail('Stale revision must not call a provider'))


if __name__ == '__main__':
    unittest.main()
