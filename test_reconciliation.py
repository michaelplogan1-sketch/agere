"""Current reference contract, exact source binding and historical route guards."""
import copy
import json
import unittest
from unittest.mock import patch

import test_case_passes as F
import test_linked_claim_reconciliation as T
from clinical_core.agere.clinical import reconciliation as R
from clinical_core.contracts import digest


class Reconciliation(unittest.TestCase):
    raw = F.CasePasses.raw
    answer = F.CasePasses.answer
    sender = F.CasePasses.sender
    run_synthesis = F.CasePasses.run_synthesis

    def setUp(self):
        T.LinkedClaims.setUp(self)
        self.job = R.request(self.parent, self.row, self.refs, self.received)

    def text(self, value, refs=()):
        return {'text': value, 'unit_refs': list(refs)}

    def result(self):
        excerpt = next(iter(self.job['excerpt_pool']))
        unit = self.job['excerpt_pool'][excerpt]['unit']
        return {'findings': [{'id': 'f1', 'kind': 'documented_account',
            'text': self.text('The source describes an unconfirmed request.', [unit]),
            'excerpt_ids': [excerpt], 'unresolved': [self.text('Completion is not established.')]}],
            'claim_reviews': [{'input_id': key, 'status': 'unassessed', 'finding_ids': [],
                              'reason': self.text('The excerpt cannot establish completion.')}
                             for key in self.job['finding_ids']],
            'unanswered': [self.text('Full source meaning has not been revalidated.')],
            'review_steps': [{'kind': 'review_source_meaning', 'finding_ids': ['f1'],
                              'question': self.text('What does the source establish?', [unit])}]}

    def parsed(self, value=None):
        return R.parse(self.raw(self.job['body'], value or self.result()), self.job, self.refs)

    def test_explicit_refs_render_exact_records_without_guessing(self):
        value = self.result(); original = copy.deepcopy(value); result = self.parsed(value)
        unit = value['findings'][0]['text']['unit_refs'][0]
        self.assertEqual(value, original)
        self.assertIn(unit, result['findings'][0]['text'])
        self.assertEqual(result['review_steps'][0]['evidence_references'], [self.refs[unit]])
        self.assertEqual(result['reference_bindings'][0]['unit_refs'], [unit])
        self.assertEqual(result['findings'][0]['citations'][0]['source_unit_id'], self.refs[unit]['source_unit_id'])

    def test_no_bare_or_qualified_ids_in_prose(self):
        for text in ('Review u1', 'Review p1:u1'):
            v = self.result(); v['findings'][0]['text']['text'] = text
            with self.assertRaisesRegex(ValueError, 'explicit fields'): self.parsed(v)

    def test_unknown_duplicate_and_uncited_references_rejected(self):
        uncited = next(n for n in self.refs if n not in self.job['units'])
        known = next(iter(self.job['units']))
        for names in (['p999:u1'], [known, known], [uncited], [known] * 5):
            v = self.result(); v['review_steps'][0]['question']['unit_refs'] = names
            with self.assertRaisesRegex(ValueError, 'explicit unit reference'): self.parsed(v)

    def test_strict_citation_and_claim_accounting_still_apply(self):
        v = self.result(); v['findings'][0]['excerpt_ids'] = ['invented']
        with self.assertRaisesRegex(ValueError, 'Unknown or duplicate excerpt'): self.parsed(v)
        v = self.result(); v['claim_reviews'].pop()
        with self.assertRaisesRegex(ValueError, 'Every original claim'): self.parsed(v)
        v = self.result(); v['claim_reviews'][0].update(status=R.L.STATES[0], finding_ids=[])
        with self.assertRaisesRegex(ValueError, 'need a consolidated finding'): self.parsed(v)

    def test_transport_retains_all_original_claim_text(self):
        wire = json.loads(self.job['body']['messages'][0]['content'])
        claims = {r[0]: r for r in wire['claims']['rows']}
        for p in self.parent['passes']:
            for f in p['result']['findings']:
                self.assertEqual(claims[f['id']][3:5], [f['text'], f['unresolved']])
                self.assertEqual([self.job['excerpt_pool'][n]['quote'] for n in claims[f['id']][5]],
                                 [c['quote'] for c in f['citations']])
        self.assertFalse(wire['retained_parent_scope']['source_coverage_upgraded'])
        self.assertEqual(self.parent, self.original)
        self.assertLessEqual(R.P.request_bytes(self.job['body']), R.G.MAX_BYTES)

    def fake_send(self, body, ledger, out, *, parse):
        raw = self.raw(body, self.result()); result = parse(raw); key = R.G.request_key(body)
        ledger.reserve(key, 'claude', body['model']); ledger.finish(key, response=raw)
        return result, {'cache_hit': False, 'request_hash': key, 'usage': {}}

    def test_receipt_replays_without_resend_and_preserves_parent_coverage(self):
        self.parent['result_digest'] = 'historical-parent-digest'
        self.parent['coverage'].update(system_unassessed_units=4)
        with patch.object(R.G, 'send', side_effect=self.fake_send), patch.object(R.C, 'token_check',
                return_value={'input_tokens': 750, 'provider_count_cached': False}):
            answer = R.synthesize(self.path, self.parent['id'], self.ledger, allow_live=True)
        saved = {k: v for k, v in answer.items() if k != 'result_digest'}
        self.assertEqual(answer['result_digest'], digest(saved))
        self.assertEqual(answer['coverage']['system_unassessed_units'], 4)
        self.assertFalse(answer['coverage']['source_coverage_upgraded'])
        self.assertEqual(answer['passes'], self.parent['passes'])
        self.assertEqual(R.inspect(self.path, answer['id']), answer)
        with patch.object(R.G, 'send') as send:
            replay = R.synthesize(self.path, self.parent['id'], self.ledger)
            send.assert_not_called(); self.assertTrue(replay['cache_hit'])
        self.assertEqual(replay['new_paid_api_calls'], 0)

    def test_failed_key_cannot_be_retried(self):
        *_, job, _ = R.prepare(self.path, self.parent['id'])
        key = R.G.request_key(job['body'])
        self.ledger.reserve(key, 'claude', job['body']['model']); self.ledger.finish(key, error='fixture failure')
        with patch.object(R.G, 'send') as send:
            with self.assertRaisesRegex(ValueError, 'no silent retry'):
                R.synthesize(self.path, self.parent['id'], self.ledger, allow_live=True)
            send.assert_not_called()

    def test_historical_context_route_is_not_replaced(self):
        with patch('clinical_core.agere.clinical.meaning_source_dispatch.pinned', return_value={'pinned'}), \
                patch.object(R.S, 'stages') as stages:
            self.assertIsNone(R.context_route(self.path, 'pinned')); stages.assert_not_called()
        with patch.object(R, 'context_route', return_value=None), patch.object(R.S, 'synthesize', return_value={'old': True}) as old:
            self.assertEqual(R.synthesize_context(self.path, 'pinned', self.ledger), {'old': True})
            old.assert_called_once()


if __name__ == '__main__': unittest.main()
