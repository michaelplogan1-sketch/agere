import json, os, sqlite3, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from clinical_core.agere.clinical import bottled, economical_planner as p

PROFILE = {'streams': {}}
GOOD = "```sql\n-- DEFINITION: diabetic patients\nSELECT count(DISTINCT patient_id) AS patients FROM condition WHERE concept = 'diabetes'\n```"
OTHER = "```sql\nSELECT count(*) AS patients FROM patient WHERE patient_id = 'P3'\n```"          # different result (1 vs 2)
SAME = "```sql\nSELECT count(*) AS n FROM (SELECT DISTINCT patient_id FROM condition WHERE concept = 'diabetes')\n```"  # same result
LINT_BAD = "```sql\nSELECT count(*) FROM record WHERE stream = 'invented_stream'\n```"
ROSTER = "```sql\nSELECT count(DISTINCT p.patient_id) FROM patient p JOIN condition c ON 1 = 1 WHERE c.concept = 'diabetes'\n```"


class FakeModel:
    identity = 'fake-bottled'

    def __init__(self, texts):
        self.texts, self.calls = texts, 0

    def __call__(self, msgs, k=1, **_):
        self.calls += 1
        assert msgs[0]['role'] == 'system' and msgs[1]['content'].endswith('Q: ' + self.question)
        return self.texts[:k], 0.01


def fixture(root):
    db = sqlite3.connect(root / 'semantic.sqlite3')
    db.executescript("CREATE TABLE patient(patient_id TEXT); CREATE TABLE condition(patient_id TEXT, concept TEXT);"
                     "INSERT INTO patient VALUES ('P1'),('P2'),('P3');"
                     "INSERT INTO condition VALUES ('P1','diabetes'),('P2','diabetes'),('P2','diabetes'),('P3','asthma');")
    db.commit(); db.close()


class Rule(unittest.TestCase):
    def cand(self, ok=True, sig='a'):
        return {'ok': ok, 'signature': sig, 'rejected': None if ok else 'lint: x'}

    def test_greedy_must_pass_and_agreement_is_counted_on_its_result(self):
        self.assertIsNone(bottled.decide([], 1)[0])
        self.assertIsNone(bottled.decide([self.cand(ok=False), self.cand(), self.cand()], 2)[0])  # greedy rejected
        self.assertIsNone(bottled.decide([self.cand(), self.cand(sig='b'), self.cand(sig='b')], 2)[0])  # majority elsewhere
        self.assertIsNone(bottled.decide([self.cand(), self.cand(ok=False, sig='a'), self.cand(sig='b')], 2)[0])  # failed decode never votes
        chosen, why = bottled.decide([self.cand(), self.cand(sig='b'), self.cand()], 2)
        self.assertIsNotNone(chosen); self.assertEqual(why, '2/3')
        self.assertIsNone(bottled.decide([self.cand(), self.cand(sig='b'), self.cand()], 3)[0])

    def test_signature_ignores_column_and_row_order(self):
        self.assertEqual(bottled.signature([[1, 'a'], [2.00001, 'b']]), bottled.signature([['b', 2.0], ['a', 1]]))
        self.assertNotEqual(bottled.signature([[1]]), bottled.signature([[2]]))

    def test_rule_reads_environment(self):
        with patch.dict(os.environ, {'AGERE_BOTTLED_K': '5', 'AGERE_BOTTLED_AGREE': '9'}):
            self.assertEqual(bottled.rule(), (5, 5))
        with patch.dict(os.environ, {'AGERE_BOTTLED': 'on'}): self.assertTrue(bottled.enabled())
        with patch.dict(os.environ, {'AGERE_BOTTLED': 'off'}): self.assertFalse(bottled.enabled())


class Answer(unittest.TestCase):
    Q = 'How many patients have a diabetes condition on file?'

    def run_model(self, texts, question=None, k=3, agree=3):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            model = FakeModel(texts); model.question = question or self.Q
            with patch.object(bottled, 'benchmark_match', return_value=None):
                return bottled.answer(root, model.question, '2029-08-15', PROFILE, model=model, k=k, agree=agree), model

    def test_accepts_when_all_decodes_agree(self):
        (params, receipt), model = self.run_model([GOOD, SAME, GOOD])
        self.assertEqual(model.calls, 1)
        self.assertIn("concept = 'diabetes'", params['query']); self.assertEqual(params['returns'], 'count')
        self.assertEqual(receipt['agreement'], '3/3'); self.assertEqual(receipt['model'], 'fake-bottled')
        self.assertEqual(receipt['version'], bottled.VERSION); self.assertIn('decode_seconds', receipt)
        self.assertTrue(all(c['ok'] for c in receipt['candidates']))

    def test_falls_through_on_disagreement(self):
        with self.assertRaises(ValueError) as ctx: self.run_model([GOOD, OTHER, GOOD])
        self.assertIn('disagree', str(ctx.exception)); self.assertIsNone(ctx.exception.receipt['agreement'])

    def test_falls_through_on_lint_or_sanity_failure(self):
        with self.assertRaises(ValueError) as ctx: self.run_model([LINT_BAD, LINT_BAD, LINT_BAD])
        self.assertIn('lint', str(ctx.exception))
        with self.assertRaises(ValueError) as ctx: self.run_model([ROSTER, ROSTER, ROSTER])
        self.assertIn('sanity', str(ctx.exception))
        with self.assertRaises(ValueError): self.run_model(['I cannot answer that.'] * 3)

    def test_never_answers_a_benchmark_question(self):
        root = Path(bottled.__file__).resolve().parents[3]
        bench = json.loads(next((root / 'bench' / 'questions').glob('*.json')).read_text())[0]
        self.assertEqual(bottled.benchmark_match(bench['question']), bench['id'])
        self.assertTrue(bottled.benchmark_match('  ' + bench['question'].upper() + '  '))  # normalised text
        model = Mock(side_effect=AssertionError('benchmark questions never reach the model'))
        with tempfile.TemporaryDirectory() as tmp:
            root_ = Path(tmp); fixture(root_)
            with self.assertRaises(ValueError) as ctx:
                bottled.answer(root_, bench['question'], '2029-08-15', PROFILE, model=model, k=3, agree=3)
        self.assertIn('benchmark', str(ctx.exception)); model.assert_not_called()
        self.assertIsNone(bottled.benchmark_match(self.Q + ' zebra quokka axolotl narwhal'))


class Planner(unittest.TestCase):
    def stack(self, root, live=False):
        return SimpleNamespace(path=root, receipts=root, allow_live=live,
                               retriever=SimpleNamespace(meta={'source_digest': 's', 'snapshot': {'as_of': '2029-08-15'}}))

    def common(self):
        return [patch('clinical_core.agere.clinical.source_profile.load', return_value=PROFILE),
                patch('clinical_core.agere.clinical.local_compiler.compile', return_value=(None, {'route': 'fallback', 'reason': 'test'})),
                patch.dict(os.environ, {'AGERE_LOCAL_MODEL': 'off', 'AGERE_LOCAL_SQL': 'off', 'AGERE_BOTTLED': 'on'}),
                patch.object(bottled.BottledModel, 'available', return_value=True),
                patch.object(bottled, 'benchmark_match', return_value=None)]

    def test_bottled_route_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            model = FakeModel([GOOD, GOOD, SAME]); model.question = Answer.Q
            ctx = self.common() + [patch.object(bottled.BottledModel, 'get', return_value=model)]
            for c in ctx: c.start()
            try:
                graph, r = p.plan(self.stack(root), Answer.Q, None, Mock(side_effect=AssertionError('no frontier')))
            finally:
                for c in reversed(ctx): c.stop()
        self.assertEqual(r['route'], 'bottled_sql'); self.assertEqual(r['model'], 'fake-bottled')
        self.assertEqual(r['new_paid_api_calls'], 0); self.assertFalse(r['frontier_called'])
        self.assertIn('elapsed_seconds', r); self.assertEqual(r['bottled']['agreement'], '3/3')
        self.assertEqual(graph['nodes'][-1]['operation'], 'sql')

    def test_decline_falls_through_to_next_rung(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            model = FakeModel([GOOD, OTHER, OTHER]); model.question = Answer.Q
            routed = {'interpretation': 'x', 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': []}
            from clinical_core.agere.clinical import operator_router
            ctx = self.common() + [patch.object(bottled.BottledModel, 'get', return_value=model),
                                   patch.object(operator_router, 'route', return_value=(routed, {'decision': 'x', 'calls': []})),
                                   patch.object(operator_router, 'cost', return_value=0.0)]
            for c in ctx: c.start()
            try:
                _, r = p.plan(self.stack(root, live=True), Answer.Q, None, Mock())
            finally:
                for c in reversed(ctx): c.stop()
        self.assertEqual(r['route'], 'operator_router')
        self.assertIn('disagree', r['local_compiler_declined']['bottled_declined'])
        self.assertEqual(len(r['local_compiler_declined']['bottled']['candidates']), 3)

    def test_off_never_loads_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            ctx = self.common() + [patch.dict(os.environ, {'AGERE_BOTTLED': 'off'}),
                                   patch.object(bottled.BottledModel, 'get', side_effect=AssertionError('loaded while off'))]
            for c in ctx: c.start()
            try:
                with patch('clinical_core.agere.clinical.analysis_graph.catalog', return_value={}), \
                     patch('clinical_core.agere.clinical.analysis_graph.code_identity', return_value='code'):
                    try: p.plan(self.stack(root), Answer.Q, None, Mock(return_value=(None, {})))
                    except Exception as exc:  # later rungs need a real stack; only the bottled rung is under test here
                        self.assertNotIn('loaded while off', str(exc))
            finally:
                for c in reversed(ctx): c.stop()

    def test_importing_the_rung_does_not_load_mlx(self):
        import subprocess, sys
        code = 'import sys; from clinical_core.agere.clinical import economical_planner, bottled; print("mlx" in sys.modules or "mlx_lm" in sys.modules)'
        out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, cwd=Path(__file__).parent)
        self.assertEqual(out.stdout.strip(), 'False', out.stderr[-500:])


if __name__ == '__main__':
    unittest.main()
