"""Grammar-constrained decoding: grammars, the mlx_lm logits processor (dummy tokenizer + dummy logits, no Qwen), wiring."""
import json
import sys
import types
import unittest
from unittest import mock

import numpy as np

from clinical_core.agere.clinical import constrained_decoding as C


class CharTokenizer:
    """Tiny vocabulary: single characters plus a few multi-char pieces; id 0 = EOS."""
    def __init__(self, pieces):
        self.pieces = ['<eos>'] + list(pieces)
        self.eos_token_ids = [0]

    def decode(self, ids):
        return ''.join('' if i == 0 else self.pieces[i] for i in ids)


def greedy(tokenizer, processor, preference, max_steps=80):
    """Greedy decode where the dummy 'model' always prefers tokens in `preference` order (ids), EOS last."""
    V = len(tokenizer.pieces)
    out, tokens = [], [1]  # one 'prompt' token
    for step in range(max_steps):
        logits = np.full((1, V), -5.0)
        prefs = preference(step, out) if callable(preference) else preference
        for rank, tid in enumerate(prefs): logits[0, tid] = 10.0 - rank * 0.01
        logits = processor(np.array(tokens), logits)
        tid = int(np.argmax(logits))
        if tid == 0: break
        out.append(tid); tokens.append(tid)
    return tokenizer.decode(out)


class GrammarTests(unittest.TestCase):
    def test_json(self):
        g = C.JsonGrammar(root='object')
        self.assertTrue(C.check(g, '{"a": [1, -2.5e3, true, null, "q\\"x"], "b": {"c": "\\u00e9"}}'))
        for bad in ('[1]', '{"a":1,}', '{"a" 1}', '{"a":01}', '{"a":tru}', '{"a":1}}'):
            self.assertFalse(C.check(g, bad), bad)
        self.assertIsNotNone(g.advance(g.start(), '{"a": [1, 2'))  # a valid prefix

    def test_json_keys(self):
        g = C.JsonGrammar(root='object', keys={'family', 'ask'})
        self.assertTrue(C.check(g, '{"family": "x", "ask": "count"}'))
        self.assertIsNone(g.advance(g.start(), '{"fx'))
        self.assertTrue(C.check(g, '{"family": {"anything": 1}}'))  # nested keys are free

    def test_sql(self):
        g = C.SqlGrammar(tables={'patient', 'condition'}, fenced=True)
        good = ("```sql\n-- DEFINITION: diabetics\nWITH d AS (SELECT patient_id FROM condition WHERE concept = 'diabetes')\n"
                "SELECT count(*) FROM patient p JOIN d ON d.patient_id = p.patient_id WHERE replace(p.sex, 'x', 'y') <> '-'\n```")
        self.assertTrue(C.check(g, good))
        self.assertIsNone(g.advance(g.start(), '```sql\nSELECT * FROM patients_wrong'))
        plain = C.SqlGrammar()
        self.assertFalse(C.check(plain, 'SELECT 1; DROP TABLE patient'))
        self.assertFalse(C.check(plain, 'DELETE FROM patient'))
        self.assertFalse(C.check(plain, 'SELECT (1'))
        self.assertTrue(C.check(plain, "select 'a(' as x -- trailing comment"))
        self.assertTrue(C.check(plain, 'SELECT a - b FROM t'))

    def test_line_and_template(self):
        self.assertTrue(C.check(C.LineGrammar(), 'How many patients have diabetes?'))
        self.assertFalse(C.check(C.LineGrammar(), '<think>'))
        t = C.TemplateGrammar([('lit', 'A1c > '), ('slot', 'v', 'number'), ('lit', ' in '), ('slot', 'y', 'integer')])
        self.assertTrue(C.check(t, 'A1c > 9.5 in 2028'))
        self.assertEqual(t.slots('A1c > 9.5 in 2028'), {'v': '9.5', 'y': '2028'})
        self.assertFalse(C.check(t, 'A1c > nine in 2028'))
        choice = C.TemplateGrammar([('lit', "concept = '"), ('slot', 'c', 'string', ['diabetes', 'ckd']), ('lit', "'")])
        self.assertTrue(C.check(choice, "concept = 'ckd'"))
        self.assertFalse(C.check(choice, "concept = 'copd'"))


class ProcessorTests(unittest.TestCase):
    def test_masks_invalid_json_tokens(self):
        tok = CharTokenizer(['x', '{', '}', '"', 'a', ':', '1', ' ', 'hello'])
        ids = {p: i for i, p in enumerate(tok.pieces)}
        proc = C.GrammarProcessor(tok, C.JsonGrammar(root='object', keys={'a'}), top_k=4)
        target = '{"a":1}'
        def prefs(step, out):  # the dummy model always ranks garbage ('hello', 'x') above the right next character
            nxt = target[len(tok.decode(out))] if len(tok.decode(out)) < len(target) else None
            return [ids['hello'], ids['x']] + ([ids[nxt]] if nxt else [])
        text = greedy(tok, proc, prefs)
        self.assertEqual(text, target)
        rep = proc.report()
        self.assertTrue(rep['complete']); self.assertEqual(rep['violations'], 0)

    def test_eos_only_when_complete(self):
        tok = CharTokenizer(['S', 'E', 'L', 'C', 'T', ' ', '1'])
        ids = {p: i for i, p in enumerate(tok.pieces)}
        proc = C.GrammarProcessor(tok, C.SqlGrammar())
        # model wants to stop immediately (EOS first) and then spell SELECT 1
        text = greedy(tok, proc, [0, ids['S'], ids['E'], ids['L'], ids['C'], ids['T'], ids[' '], ids['1']])
        self.assertTrue(text.upper().startswith('SELECT'), text)

    def test_heals_when_nothing_valid(self):
        tok = CharTokenizer(['x'])
        proc = C.GrammarProcessor(tok, C.JsonGrammar(root='object'))
        logits = np.zeros((1, 2))
        out = proc(np.array([1]), logits)
        np.testing.assert_array_equal(out, logits)  # unchanged: never blocks
        self.assertEqual(proc.report()['violations'], 1)

    def test_mlx_arrays(self):
        try:
            import mlx.core as mx
        except ImportError:
            self.skipTest('mlx not installed')
        tok = CharTokenizer(['x', '{', '}'])
        proc = C.GrammarProcessor(tok, C.JsonGrammar(root='object'))
        logits = mx.array([[0.0, 9.0, 1.0, 0.5]])
        out = proc(mx.array([1]), logits)
        self.assertEqual(int(mx.argmax(out, axis=-1).item()), 2)  # '{' is the only valid start

    def test_disabled_is_noop(self):
        with mock.patch.dict('os.environ', {'AGERE_CONSTRAINED_DECODING': 'off'}):
            self.assertEqual(C.processors(CharTokenizer(['x']), C.LineGrammar()), [])


class WiringTests(unittest.TestCase):
    def test_normalizer_passes_line_grammar(self):
        from clinical_core.agere.clinical import local_normalizer as N
        seen = {}
        fake_tok = types.SimpleNamespace(apply_chat_template=lambda *a, **k: 'PROMPT', eos_token_ids=[0], decode=lambda ids: '')
        def fake_generate(model, tokenizer, prompt, max_tokens, sampler, verbose, logits_processors=None):
            seen['procs'] = logits_processors; return 'How many patients have diabetes?\nextra'
        with mock.patch.object(N, 'available', return_value=True), mock.patch.object(N, '_load', return_value=(object(), fake_tok)), \
                mock.patch('mlx_lm.generate', fake_generate):
            self.assertEqual(N.propose('how many diabetics'), 'How many patients have diabetes?')
        self.assertIsInstance(seen['procs'][0].grammar, C.LineGrammar)

    def test_worker_passes_json_grammar(self):
        from clinical_core.agere.clinical import local_request_worker as W
        seen = {}
        fake_tok = types.SimpleNamespace(apply_chat_template=lambda *a, **k: 'PROMPT', eos_token_ids=[0], decode=lambda ids: '')
        def fake_generate(model, tokenizer, prompt, max_tokens, sampler, verbose, logits_processors=None):
            seen['procs'] = logits_processors; return '{"select": []}'
        with mock.patch('mlx_lm.load', return_value=(object(), fake_tok)), mock.patch('mlx_lm.generate', fake_generate):
            run = W.mlx_generate('adapter')
            self.assertEqual(json.loads(run([{'role': 'user', 'content': 'q'}])), {'select': []})
        self.assertEqual(seen['procs'][0].grammar.root, 'object')

    def test_bottled_grammars(self):
        from clinical_core.agere.clinical import bottled
        g = bottled.sql_grammar(None)
        self.assertTrue(g.fenced)
        self.assertTrue(C.check(g, '```sql\n-- DEFINITION: x\nSELECT count(*) FROM patient\n```'))
        self.assertTrue(C.check(bottled.slot_grammar(), '{"family": null}'))
        self.assertEqual(bottled._grammar_kw(object(), g), {})  # test doubles keep their signature


if __name__ == '__main__':
    unittest.main()
