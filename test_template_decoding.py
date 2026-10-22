import json, tempfile, unittest
from pathlib import Path
import numpy as np
from clinical_core.agere.clinical import template_decoding as td, learning

Q1 = 'How many patients with diabetes had an A1c above 9 in 2028?'
S1 = "SELECT count(DISTINCT patient_id) FROM measurement WHERE concept = 'a1c' AND value > 9 AND event_date >= '2028-01-01' AND event_date < '2029-01-01' LIMIT 1"
Q2 = 'What was the average LDL on 2029-03-15 for members over 65?'
S2 = "SELECT avg(value) FROM measurement WHERE concept = 'ldl' AND event_date = '2029-03-15' AND age > 65"


class Enc:
    identity = 'dummy'
    def encode(self, qs):
        return np.array([[1.0, 0.0] for _ in qs])   # every pair identical: only the signature check differentiates


class TemplateTests(unittest.TestCase):
    def test_abstract_roundtrip_and_slot_kinds(self):
        t = td.abstract(Q2, S2)
        self.assertEqual([(s['kind'], s['example']) for s in t['slots']], [('date', '2029-03-15'), ('integer', '65')])
        self.assertIn('{s0}', t['question']); self.assertEqual(td.instantiate(t, {'s0': '2030-01-02', 's1': '40'}).count('2030-01-02'), 1)
        t1 = td.abstract(Q1, S1)
        self.assertIsNotNone(t1)                         # 9 and the year (inside quoted dates) become slots; LIMIT 1 stays fixed
        self.assertIn('LIMIT 1', t1['sql']); self.assertIn("{s1}-01-01", t1['sql']); self.assertIn("{s1+1}-01-01", t1['sql'])
        self.assertIsNone(td.abstract('How many patients are there?', 'SELECT count(*) FROM patient'))

    def test_exact_template_fill_and_literal_binding(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'run.json').write_text(json.dumps({'source_digest': 'fx'}))
            learning.record_verified(d, Q1, 'sql', {'query': S1, 'returns': 'count'}, {}, 'r', [], 'primary=second')
            hit, rc = td.lookup(d, 'How many patients with diabetes had an A1c above 8 in 2029?', Enc())
            self.assertEqual(rc['fill'], 'exact_template')
            q = hit['plan']['query']
            self.assertIn('value > 8', q); self.assertIn("event_date >= '2029-01-01'", q); self.assertIn("event_date < '2030-01-01'", q)  # range moves with the year
            self.assertEqual(hit['plan']['returns'], 'count')
            # a different concept is not an entailed template
            miss, rc2 = td.lookup(d, 'How many patients with asthma had an A1c above 8 in 2029?', Enc())
            self.assertIsNone(miss)
            # the exact verified question belongs to the learned rung
            self.assertIsNone(td.lookup(d, Q1, Enc())[0])

    def test_model_slot_fill_is_grammar_constrained(self):
        t = td.abstract(Q2, S2)
        t['source_questions'] = [Q2]
        seen = {}
        class Model:
            def __call__(self, msgs, k=1, grammar=None, **kw):
                seen['grammar'] = grammar
                return ["SELECT avg(value) FROM measurement WHERE concept = 'ldl' AND event_date = '2031-02-03' AND age > 70"], 0.0
        vals = td.fill_with_model(Model(), t, 'average LDL on 2031-02-03 for members over 70')
        self.assertEqual(vals, {'s0': '2031-02-03', 's1': '70'})
        g = seen['grammar']; s = g.start()
        for ch in "SELECT avg(value) FROM measurement WHERE concept = 'ldl' AND event_date = '":
            s = g.advance(s, ch); self.assertIsNotNone(s)
        self.assertIsNone(g.advance(s, 'x'))             # a slot position only takes date characters
        t1 = td.abstract(Q1, S1); t1['source_questions'] = [Q1]
        class M1:
            def __call__(self, msgs, k=1, grammar=None, **kw):
                return [td.instantiate(t1, {'s0': '8', 's1': '2031'})], 0.0
        self.assertEqual(td.fill_with_model(M1(), t1, 'A1c above 8 in 2031'), {'s0': '8', 's1': '2031'})
        class M2(M1):
            def __call__(self, msgs, k=1, grammar=None, **kw):
                return [td.instantiate(t1, {'s0': '8', 's1': '2031'}).replace('< \'2032', '< \'2035')], 0.0
        self.assertIsNone(td.fill_with_model(M2(), t1, 'A1c above 8 in 2031'))   # next-year end disagrees with the year slot
        ok, rc = td._hit(t, {'s0': '2031-02-03', 's1': '99'}, 'average LDL on 2031-02-03 for members over 70', {}, 'model_slot_fill', .9)
        self.assertIsNone(ok)                            # 99 is not a literal of the question: decline


if __name__ == '__main__':
    unittest.main()
