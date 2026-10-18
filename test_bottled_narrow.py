import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.clinical import bottled, bottled_cohort_hook as hook, cohort_families as cf

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools' / 'bottling'))
import narrow_data  # noqa: E402

AS_OF = '2030-05-27'
ED = {'family': 'event_followup_gap', 'ask': 'list', 'anchor': 'ed', 'condition': 'hf', 'lookback': {'months': 12},
      'followup': {'kind': 'visit', 'days': 14}}


class FakeModel:
    identity = 'fake-narrow'

    def __init__(self, texts):
        self.texts, self.calls = texts, 0

    def __call__(self, msgs, k=1, **_):
        self.calls += 1
        assert msgs[0]['content'] == bottled.NARROW_SYSTEM and msgs[1]['content'].startswith('Q: ')
        return self.texts[:k], 0.01


class Dataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.manifest = narrow_data.build(cls.tmp.name, seed=11, n_train_slots=120, n_held_slots=40)
        out = Path(cls.tmp.name)
        cls.train = [json.loads(l) for l in (out / 'train-records.jsonl').read_text().splitlines()]
        cls.held = [json.loads(l) for l in (out / 'heldout.jsonl').read_text().splitlines()]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_split_has_no_template_or_text_leakage(self):
        self.assertTrue(self.train and self.held)
        t_frames = {r['template'].split(':', 1)[1] for r in self.train}
        h_frames = {r['template'].split(':', 1)[1] for r in self.held}
        self.assertFalse(t_frames & h_frames, 'a phrasing frame appears in both splits')
        self.assertFalse({bottled._norm(r['question']) for r in self.train} & {bottled._norm(r['question']) for r in self.held})
        t_slots = {json.dumps(r['slots'], sort_keys=True) for r in self.train if r['family'] not in (None, 'screening_overdue')}
        h_slots = {json.dumps(r['slots'], sort_keys=True) for r in self.held if r['family'] not in (None, 'screening_overdue')}
        self.assertFalse(t_slots & h_slots, 'a slot instance appears in both splits')
        tr = {r['slot_id'] for r in self.train if r['split'] == 'train'}
        va = {r['slot_id'] for r in self.train if r['split'] == 'valid'}
        self.assertFalse(tr & va)

    def test_every_label_validates_and_renders(self):
        for r in self.train + self.held:
            if r['family'] is None:
                self.assertEqual(r['slots'], {'family': None}); continue
            p = bottled.spec_from_slots(r['slots'], AS_OF)
            spec = cf.validate({'family': p['family'], 'parameters': p['parameters'], 'returns': p['returns']})
            self.assertTrue(cf.render(spec, AS_OF).startswith('-- DEFINITION: cohort family'))

    def test_mlx_rows_are_chat_triples_with_json_slots(self):
        row = json.loads((Path(self.tmp.name) / 'train.jsonl').read_text().splitlines()[0])
        self.assertEqual([m['role'] for m in row['messages']], ['system', 'user', 'assistant'])
        self.assertIsNotNone(bottled.parse_slots(row['messages'][2]['content']))


class Slots(unittest.TestCase):
    def test_canonical_round_trip_matches_the_parser(self):
        p = bottled.spec_from_slots(ED, AS_OF)
        self.assertEqual(p['parameters']['anchor']['dx_prefixes'], ['I50'])
        self.assertEqual(p['parameters']['followup'], {'kinds': ['visit'], 'days': 14})
        self.assertEqual(p, {**cf.parse(p['canonical_question'], AS_OF), 'canonical_question': p['canonical_question'], 'slots': bottled.check_slots(ED)})

    def test_typed_checks_refuse_unknown_values(self):
        for bad in ({**ED, 'condition': 'gout'}, {**ED, 'anchor': 'clinic'}, {**ED, 'followup': {'kind': 'visit', 'days': 0}},
                    {'family': 'therapy_class_gap', 'ask': 'list', 'classes': ['allopurinol']}, {'family': None, 'ask': 'list'},
                    {**ED, 'extra': 1}, {'family': 'screening_overdue', 'ask': 'list', 'screening': 'breast', 'age': [40, 50]}):
            with self.assertRaises(ValueError): bottled.check_slots(bad)

    def test_agreement_rule(self):
        good = json.dumps(ED)
        other = json.dumps({**ED, 'followup': {'kind': 'visit', 'days': 30}})
        self.assertEqual(bottled.narrow_decide([good, good, good], 3)[0], bottled.check_slots(ED))
        self.assertIsNone(bottled.narrow_decide([good, other, good], 3)[0])
        self.assertIsNotNone(bottled.narrow_decide([good, other, good], 2)[0])
        self.assertIsNone(bottled.narrow_decide(['not json', good, good], 2)[0])          # greedy must be valid
        self.assertIsNone(bottled.narrow_decide(['{"family": null}'] * 3, 1)[0])           # a non-family answer declines
        # key order and whitespace do not matter
        self.assertIsNotNone(bottled.narrow_decide([good, json.dumps(ED, sort_keys=True, indent=1)], 2)[0])

    def test_cohort_slots_accepts_agreeing_slots_and_declines_otherwise(self):
        q = 'Who had a trip to the ER for CHF in the past year without being seen within two weeks?'
        m = FakeModel([json.dumps(ED)] * 3)
        parsed, rec = bottled.cohort_slots(q, AS_OF, model=m, k=3, agree=3)
        self.assertEqual(parsed['family'], 'event_followup_gap')
        self.assertEqual(rec['api_usd'], 0.0)
        m2 = FakeModel([json.dumps(ED), '{"family": null}', json.dumps(ED)])
        self.assertIsNone(bottled.cohort_slots(q, AS_OF, model=m2, k=3, agree=3)[0])
        m3 = FakeModel([json.dumps(ED)] * 3)
        parsed, rec = bottled.cohort_slots('What is the ED visit rate by practice?', AS_OF, model=m3)
        self.assertIsNone(parsed); self.assertEqual(m3.calls, 0)


class Hook(unittest.TestCase):
    Q = 'Who had a trip to the ER for CHF in the past year without being seen within two weeks?'

    def test_flag_off_is_cohort_families_plan(self):
        m = FakeModel([json.dumps(ED)] * 3)
        with patch.dict(os.environ, {'AGERE_BOTTLED_COHORT': 'off'}):
            for q in (self.Q, 'Which patients had 3 or more ED visits in the last 12 months?'):
                self.assertEqual(hook.plan(None, q, AS_OF, model=m), cf.plan(None, q, AS_OF))
        self.assertEqual(m.calls, 0)
        self.assertEqual(bottled.NARROW_DEFAULT, 'on')  # enabled after the held-out gate (docs/agere/BOTTLING_INTEGRATION.md)

    def test_flag_on_fills_a_parser_miss_for_free_and_leaves_parser_hits_alone(self):
        self.assertIsNone(cf.plan(None, self.Q, AS_OF))
        m = FakeModel([json.dumps(ED)] * 3)
        with patch.dict(os.environ, {'AGERE_BOTTLED_COHORT': 'on', 'AGERE_BOTTLED_COHORT_K': '3'}):
            res = hook.plan(None, self.Q, AS_OF, model=m)
            self.assertEqual(res['calls'], [])
            self.assertTrue(res['receipt']['bottled']['used'])
            self.assertEqual(res['spec']['parameters']['anchor']['dx_prefixes'], ['I50'])
            q = 'Which patients had 3 or more ED visits in the last 12 months?'
            self.assertEqual(hook.plan(None, q, AS_OF, model=m)['spec'], cf.plan(None, q, AS_OF)['spec'])
        self.assertEqual(m.calls, 1)


if __name__ == '__main__':
    unittest.main()
