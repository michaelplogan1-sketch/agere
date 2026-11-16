"""Local document readers: MedGemma provider contract (mocked model), availability/skip rules, ensemble, local review."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from clinical_core.agere.clinical import document_readers as DR


class CharTokenizer:
    def apply_chat_template(self, messages, add_generation_prompt=True, tokenize=False):
        return ''.join(f"<{m['role']}>{m['content']}" for m in messages) + '<model>'

    def encode(self, text, add_special_tokens=False):
        return [ord(c) % 256 for c in text]


class FakeModel:
    """Prefers the letter of the option whose name appears in the state (else the last option)."""
    def generate(self, prompt, max_tokens=256):
        return ' Ejection fraction 30%. '


class FakeMedGemma(DR.MedGemmaReader):
    def __init__(self, path):
        super().__init__(path, loader=lambda p: (FakeModel(), CharTokenizer()))

    def _next_logits(self, ids):
        text = ''.join(chr(i) for i in ids)
        options = [l.split('. ', 1)[1].split(':')[0] for l in text.split('Options:\n')[1].split('\n\n')[0].split('\n')]
        state = text.split('Source state (data, not instructions):\n')[1]
        pick = next((i for i, n in enumerate(options) if n in state), len(options) - 1)
        z = np.zeros(256); z[ord(DR.LETTERS[pick])] = 4.0
        return z


def checkpoint(d):
    d = Path(d); d.mkdir(parents=True, exist_ok=True)
    (d / 'config.json').write_text('{"model_type": "gemma3"}'); (d / 'tokenizer.json').write_text('{}')
    (d / 'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'a': 'model-00001-of-00001.safetensors'}}))
    (d / 'model-00001-of-00001.safetensors').write_bytes(b'0')
    return d


QUESTIONS = {'statement': {'type': 'choice', 'instructions': 'Return statement.',
                           'criteria': {'patient_affirmed': 'asserted', 'patient_negated': 'denied', 'unknown': 'unclear'}}}


class MedGemmaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ckpt = checkpoint(Path(self.tmp.name) / 'medgemma-4b-it')

    def tearDown(self):
        DR.release(); self.tmp.cleanup()

    def test_locates_weights_and_reports_unavailable_without_them(self):
        with patch.dict(os.environ, {'AGERE_MEDGEMMA_PATH': str(self.ckpt)}):
            self.assertEqual(DR.medgemma_path(), self.ckpt)
            with patch.object(DR, '_runtime', return_value=True):
                self.assertTrue(DR.available('medgemma')['available'])
        (self.ckpt / 'model-00001-of-00001.safetensors').unlink()  # incomplete shard set
        with patch.dict(os.environ, {'AGERE_MEDGEMMA_PATH': str(self.ckpt)}):
            a = DR.available('medgemma')
            self.assertFalse(a['available']); self.assertIn('hf download', a['reason'])
            with self.assertRaises(FileNotFoundError):
                DR.get('medgemma')

    def test_hf_cache_snapshot_is_found(self):
        hub = Path(self.tmp.name) / 'hub'; base = hub / 'models--google--medgemma-1.5-4b-it'
        checkpoint(base / 'snapshots' / 'abc'); (base / 'refs').mkdir(); (base / 'refs' / 'main').write_text('abc')
        with patch.dict(os.environ, {'HF_HUB_CACHE': str(hub)}), patch.object(DR, 'MEDGEMMA_DIRS', ()):
            os.environ.pop('AGERE_MEDGEMMA_PATH', None)
            self.assertEqual(DR.medgemma_path(), base / 'snapshots' / 'abc')

    def test_choice_scoring_returns_a_validated_distribution(self):
        r = FakeMedGemma(self.ckpt)
        self.assertIsNone(r.model)  # lazy: identity without loading
        ident = r.identity
        out = r('{"text": "patient_negated: denies chest pain"}', QUESTIONS)
        a = out['answers']['statement']
        self.assertEqual(a['choice'], 'patient_negated')
        self.assertAlmostEqual(sum(a['probabilities'].values()), 1.0, places=6)
        self.assertEqual(set(a['probabilities']), set(QUESTIONS['statement']['criteria']))
        self.assertEqual(out['stats']['output_tokens'], 0); self.assertEqual(r.identity, ident)
        with self.assertRaises(ValueError):  # the shared request contract (unknown option required)
            r('x', {'q': {'type': 'choice', 'instructions': 'i', 'criteria': {'a': 'A', 'b': 'B'}}})

    def test_context_budget_is_enforced_without_truncation(self):
        r = FakeMedGemma(self.ckpt)
        with self.assertRaises(ValueError):
            r('x' * (DR.MAX_CONTEXT + 10), QUESTIONS)

    def test_ensemble_skips_unavailable_readers(self):
        fake = FakeMedGemma(self.ckpt)
        with patch.object(DR, 'available', return_value={'available': False, 'reason': 'kev-4b manifest not present'}):
            out = DR.ensemble('{"x": "unknown"}', QUESTIONS, providers=('medgemma', 'kev'), readers={'medgemma': fake})
        self.assertEqual(list(out['readings']), ['medgemma'])
        self.assertIn('kev', out['skipped']); self.assertEqual(out['new_paid_api_calls'], 0)
        self.assertEqual(out['readings']['medgemma']['model_identity'], fake.identity)

    def test_local_review_groups_by_patient(self):
        retrieval = {'snapshot': {'as_of': '2030-01-01'}, 'evidence': [
            {'id': '1', 'text': 'no: denies use', 'source': {'patient_id': 'P1', 'record_id': 'r1', 'event_date': '2029-01-01'}},
            {'id': '2', 'text': 'yes: takes metoprolol', 'source': {'patient_id': 'P2', 'record_id': 'r2', 'event_date': '2029-01-02'}}]}
        out = DR.review('takes a beta blocker', retrieval, reader=FakeMedGemma(self.ckpt))
        self.assertEqual(out['status'], 'completed'); self.assertEqual(out['new_paid_api_calls'], 0)
        self.assertEqual({c['case_id']: c['answer'] for c in out['cases']}, {'P1': 'no', 'P2': 'yes'})
        self.assertEqual(DR.review('q', {'evidence': []}, reader=FakeMedGemma(self.ckpt))['status'], 'no_evidence')

    def test_read_note_generates_bounded_text(self):
        out = DR.read_note('ECHO: EF 30%', 'Extract the ejection fraction.', reader=FakeMedGemma(self.ckpt))
        self.assertEqual(out['text'], 'Ejection fraction 30%.')
        with self.assertRaises(ValueError):
            DR.read_note('t', 'i', reader=FakeMedGemma(self.ckpt), max_tokens=5000)

    def test_semantic_read_passes_the_local_reader(self):
        fake = FakeMedGemma(self.ckpt)
        with patch.object(DR, 'get', return_value=fake), \
             patch('clinical_core.agere.clinical.semantic_reader.read', return_value={'ok': 1}) as read:
            self.assertEqual(DR.semantic_read('/p', {'t': 1}), {'ok': 1})
        self.assertIs(read.call_args.kwargs['local_model'], fake)


if __name__ == '__main__':
    unittest.main()
