"""Authored input revisions and dialogue; native integrity, never model qualification."""
import copy
import json
import os
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from clinical_core.agere.clinical import contract_performance as C, clarification as I
from clinical_core.contracts import digest
import test_contract_performance as fixture


class ContractInputFlow(unittest.TestCase):
    setUp = fixture.ContractPerformance.setUp
    run_report = fixture.ContractPerformance.run_report

    def configure(self):
        patch.dict(os.environ, {'AGERE_CONTRACT_INPUTS': str(self.path / 'inputs')}).start()

    def save(self, changes=None, restore=None, expected=None):
        return C.save_input({'contract_id': self.contract['id'], 'expected_digest': expected or C.load(self.contract['id'])[2],
            'changes': changes or {}, 'restore_revision': restore, 'reason': 'Authored scenario for the input workflow test.'})

    def test_revise_recompute_restore_preserves_history_and_packaged_inputs(self):
        self.configure()
        original = self.file.read_bytes()
        initial = C.load(self.contract['id'])[2]
        first = self.run_report()
        revised = self.save({'benchmark_pmpm_cents': 110000})
        self.assertEqual(revised['version'], 2)
        second = self.run_report()
        self.assertEqual(second['summary']['observed_allowed_cents'], first['summary']['observed_allowed_cents'])
        self.assertEqual(second['summary']['benchmark_cents'] - first['summary']['benchmark_cents'], 30000)
        self.assertEqual(self.file.read_bytes(), original)
        folder = self.path / 'inputs' / self.contract['id']
        history = {p.name: p.read_bytes() for p in (folder / 'versions').glob('*.json')}
        with self.assertRaisesRegex(ValueError, 'another window'):
            self.save({'sharing_rate_bps': 4000}, expected=initial)
        self.assertEqual(C.load(self.contract['id'])[2], revised['contract_digest'])
        restored = self.save(restore='base')
        self.assertEqual(restored['contract_digest'], initial)
        self.assertEqual({p.name: p.read_bytes() for p in (folder / 'versions').glob('*.json')}, history)
        self.assertEqual(len(list((folder / 'events').glob('*.json'))), 2)
        third = self.save({'quality_target_bps': 6500})
        self.assertEqual(third['version'], 3, 'Restoration must not reuse an old version number')
        self.save(restore=revised['revision'])
        self.assertEqual(C.load(self.contract['id'])[2], revised['contract_digest'])

    def test_discovery_tracks_current_terms_and_does_not_select_a_demo(self):
        self.configure()
        before = C.input_request('Show contract performance')
        bound = before['bindings'][self.contract['id']]
        self.assertIn('authored', before['request']['reason'])
        self.assertIn('different_inputs', [o['id'] for o in before['request']['options']])
        self.assertIsNone(C.input_request('Show contract performance for people over 60'))
        self.save({'sharing_rate_bps': 4000})
        self.assertNotEqual(C.input_request('Show contract performance')['bindings'][self.contract['id']], bound)
        with self.assertRaisesRegex(ValueError, 'changed'):
            C.validate(bound)
        schema = C.schema(lambda fields: {'properties': fields}, None, None)
        self.assertEqual(schema['required'], ['contract_id'])
        self.assertEqual(schema['properties']['contract_id']['enum'], [self.contract['id']])
        self.assertEqual(json.loads(schema['description'])['registered_inputs'][0]['version'], 2)

    def test_invalid_edits_cannot_publish_terms_or_change_original_sources(self):
        self.configure()
        original = C.load(self.contract['id'])[2]
        for changes in ({'payer_id': 'INVENTED'}, {'benchmark_pmpm_cents': True}, {'benchmark_pmpm_cents': 0},
                        {'sharing_rate_bps': 10001}, {'quality_target_bps': -1}, {'benchmark_pmpm_cents': 1.1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.save(changes)
            self.assertEqual(C.load(self.contract['id'])[2], original)
        with self.assertRaises(ValueError):
            self.save(restore='../outside')
        self.assertFalse(list((self.path / 'inputs').rglob('current.json')))

    def test_history_corruption_is_detected_before_activation(self):
        self.configure()
        saved = self.save({'sharing_rate_bps': 4000})
        p = self.path / 'inputs' / self.contract['id'] / 'versions' / (saved['revision'] + '.json')
        event = json.loads(p.read_text()); event['data']['benchmark_pmpm_cents'] += 1
        p.write_text(json.dumps(event))
        with self.assertRaisesRegex(ValueError, 'integrity'):
            C.load(self.contract['id'])

    def pause(self):
        self.configure()
        self.stack = SimpleNamespace(path=self.path, compose=Mock(return_value={'answer': {'status': 'answered'}}))
        source = {'as_of': '2030-05-27', 'source_digest': 'independently-authored-fixture'}
        patch('clinical_core.agere.deliverables.answer_context.source_identity', return_value=source).start()
        patch.object(I, 'input_guard', return_value={'kind': 'authored_fixture'}).start()
        return I.registered_input_pause(self.stack, 'Show contract performance')

    def test_selection_restart_and_duplicate_reply_reuse_existing_durable_store(self):
        paused = self.pause()
        self.assertEqual(paused['answer']['status'], 'clarify')
        self.assertEqual(paused['new_paid_api_calls'], 0)
        self.assertFalse(paused['results']); self.stack.compose.assert_not_called()
        public = I.publish(self.path, 'input-conversation', 'Show contract performance', paused)
        ident = public['clarification']['id']
        row, reply, prior = I.claim(self.path, 'input-conversation', ident, {'option_id': self.contract['id']})
        self.assertIsNone(prior)
        resumed = I.resume_investigator(self.stack, row['checkpoint'], reply)
        self.stack.compose.assert_called_once_with('Show contract performance for ' + self.contract['id'])
        binding = resumed['planner_receipt']['clarification_binding']
        self.assertEqual(binding['original_question'], 'Show contract performance')
        self.assertEqual(binding['authority'], 'user_intent_only')
        self.assertFalse(binding['source_facts_established'])
        I.finish(self.path, ident, result=resumed)
        self.assertEqual(I.claim(self.path, 'input-conversation', ident, {'option_id': self.contract['id']})[2], resumed)
        self.assertEqual(self.stack.compose.call_count, 1)

    def test_different_inputs_and_autonomous_mode_preserve_unresolved_intent(self):
        paused = self.pause()
        result = I.resume_investigator(self.stack, paused['clarification_checkpoint'], {'option': {'id': 'different_inputs'}})
        self.assertEqual(result['answer']['status'], 'partial'); self.stack.compose.assert_not_called()
        with I.interaction('autonomous'):
            away = I.registered_input_pause(self.stack, 'Show contract performance')
        self.assertNotIn('clarification_checkpoint', away)
        self.assertFalse(away['results']); self.assertEqual(away['new_paid_api_calls'], 0)

    def test_changed_offered_terms_rejected_before_execution(self):
        paused = self.pause()
        self.save({'quality_target_bps': 6500})
        with self.assertRaisesRegex(ValueError, 'changed'):
            I.resume_investigator(self.stack, paused['clarification_checkpoint'], {'option': {'id': self.contract['id']}})
        self.stack.compose.assert_not_called()

    def coverage_fixture(self):
        self.configure()
        observation = {'source': {'digest': 'authored-source'}, 'wal': {'digest': 'no-wal'}}
        patch('clinical_core.agere.deliverables.answer_context.source_identity', return_value=observation['source']).start()
        patch('clinical_core.agere.clinical.answer_dependencies.wal_identity', return_value=observation['wal']).start()
        report = self.run_report(); report['observation_binding'] = observation
        question = 'Show contract performance for ' + self.contract['id']
        graph = C.registered_input_command(question, '2030-05-27')
        result = {'result_id': 'authored-test-result', 'question': question, 'graph': graph,
            'results': [{'node': graph['nodes'][0], 'result': report}],
            'answer': C.answer(report, {'coverage': 'supported_observation'}, question)}
        with sqlite3.connect(self.path / 'stack.sqlite3') as db:
            db.execute('CREATE TABLE result_cache(id,payload)')
            db.execute('INSERT INTO result_cache VALUES(?,?)', (result['result_id'], json.dumps(result)))
        return question, graph, result

    def test_coverage_state_retains_roles_uncertainties_and_full_native_result_identity(self):
        question, graph, result = self.coverage_fixture()
        proof = C.coverage_state(self.path, question, graph, result)
        self.assertEqual(proof['complete_native_result_digest'], digest(result))
        self.assertEqual(proof['retained_collections']['all_members'], 2)
        self.assertEqual(proof['retained_collections']['all_included_claim_bindings'], 4)
        self.assertEqual(proof['retained_collections']['all_months'], 12)
        self.assertFalse(proof['clinical_semantic_acceptance'])
        self.assertIn('review_needed', proof['columns'])
        self.assertIsNone(C.coverage_state(self.path, question + ' with extra constraints', graph, result))
        before = copy.deepcopy(result)
        result['answer']['table'][0]['observed_allowed_cents'] += 1
        with self.assertRaisesRegex(ValueError, 'changed'):
            C.coverage_state(self.path, question, graph, result)
        self.assertNotEqual(before, result)
        with patch('clinical_core.agere.deliverables.answer_context.source_identity', return_value={'digest': 'changed'}):
            with self.assertRaisesRegex(ValueError, 'source changed'):
                C.coverage_state(self.path, question, graph, before)


if __name__ == '__main__':
    unittest.main()
