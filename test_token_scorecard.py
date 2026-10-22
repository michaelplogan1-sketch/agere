"""Token-economics scorecard: metrics from per-question telemetry, the fixed realistic question set run end to end through
conversation.handle_compose (fake stack, real ledger attribution), and the policy violations."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from clinical_core.agere.clinical import token_scorecard as S  # noqa: E402
from clinical_core.agere.gateway import BudgetLedger  # noqa: E402
from tools import token_scorecard as CLI  # noqa: E402
from tools.component_usage import QUESTIONS, load_questions  # noqa: E402


def tel(qid, status='answered', by='compositional_grammar', attempts=(), usd=0.0, known=True, s1=(), opus=()):
    new = [a for a in attempts if not a.get('cache_hit')]
    models = {}
    for a in new: models[a['model']] = models.get(a['model'], 0) + 1
    return {'question_id': qid, 'status': status, 'answered_by': by, 'attempts': list(attempts), 'calls_by_model': models,
            'api_usd': usd, 'usd_known': known, 'no_paid_call': not new, 'llm_free': not new and by != 'local_normalizer',
            'local_llm': by == 'local_normalizer', 'claude_calls': sum(a['provider'] == 'claude' for a in new),
            'system_one_calls': sum(a['provider'] in ('jev', 'perplexity') for a in new), 'system_one_decided': list(s1),
            'opus_without_failed_sonnet': list(opus), 'tokens': {'input_tokens': 10 * len(new)},
            'stages': {'compose': 1.0, 'cascade': [{'tier': 'semantic_cache', 'seconds': 0.2}]}}


def A(provider, model, cache_hit=False): return {'provider': provider, 'model': model, 'cache_hit': cache_hit}


class Metrics(unittest.TestCase):
    def test_scorecard_numbers(self):
        rows = [tel('q1'), tel('q2', by='local_normalizer'),
                tel('q3', by='atomic_system_one', attempts=[A('jev', 'jev-1.13.0'), A('jev', 'jev-1.13.0')], usd=0.0004,
                    s1=['answer:atomic_system_one', 'route:chart_evidence']),
                tel('q4', by='hosted_typed_interpreter', attempts=[A('jev', 'jev-1.13.0'), A('claude', 'claude-sonnet-5-5'),
                                                                     A('claude', 'claude-opus-5-5')], usd=0.30),
                tel('q5', status='error', by=None, attempts=[A('claude', 'claude-opus-5-5')], known=False, opus=['k5'])]
        card = S.scorecard(rows, question_set='fixture')
        self.assertEqual(card['questions'], 5); self.assertEqual(card['answered'], 4)
        self.assertEqual(card['answered_no_llm'], 0.2)        # q1 only (q2 used the local model)
        self.assertEqual(card['answered_no_paid_call'], 0.4)  # q1, q2
        self.assertEqual(card['llm_calls_per_question'], {'claude-opus-5-5': 0.4, 'claude-sonnet-5-5': 0.2, 'jev-1.13.0': 0.6})
        self.assertEqual(card['cost_per_question_usd'], round(0.3004 / 4, 6))  # q5 is unpriced: counted apart, never $0
        self.assertEqual(card['questions_with_unpriced_calls'], 1)
        self.assertEqual(card['system_one']['decided_share'], 0.2)
        self.assertEqual(card['system_one']['claude_before_system_one'], ['q5'])
        self.assertEqual(card['opus_without_failed_sonnet'], {'failures': 1, 'calls': 1, 'questions': ['q5']})
        self.assertEqual(len(card['violations']), 3)
        self.assertIn('Opus', S.render(card))

    def test_empty(self):
        self.assertEqual(S.scorecard([])['questions'], 0)


class FixedSet(unittest.TestCase):
    """The fixed realistic set runs end to end: handle_compose -> cost telemetry -> scorecard."""

    def test_question_set_is_fixed_and_realistic(self):
        rows = load_questions(QUESTIONS)
        self.assertEqual(len(rows), 48); self.assertEqual(len({r['id'] for r in rows}), 48)
        fams = {r['family'] for r in rows}
        for need in ('count', 'care_gap', 'notes', 'definition', 'engine', 'why', 'trend', 'multi_step', 'followup', 'vbc'):
            self.assertIn(need, fams)

    def test_end_to_end_on_fake_stack(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / 'ledger.sqlite3'
            os.environ['AGERE_LEDGER_PATH'] = str(ledger_path); os.environ['AGERE_TRAINING_DIR'] = str(Path(tmp) / 'tr')
            try:
                class Stack:
                    path, allow_live, receipts = Path(tmp), True, Path(tmp)

                    def compose(self, question, planning='auto'):
                        ledger = BudgetLedger(ledger_path)
                        try:
                            by = 'compositional_grammar'
                            if 'notes' in question or 'documented' in question:
                                ledger.reserve('j' + question, 'jev', 'jev-1.13.0')
                                ledger.finish('j' + question, response={'model': 'jev-1.13.0', 'usage': {'input_tokens': 300, 'output_tokens': 0}})
                                by = 'atomic_system_one'
                            elif 'why' in question.lower() or 'drives' in question:
                                ledger.reserve('o' + question, 'claude', 'claude-opus-5-5')  # Opus with no failed Sonnet review
                                ledger.finish('o' + question, response={'model': 'claude-opus-5-5', 'content': [{'type': 'text', 'text': '{}'}],
                                                                        'usage': {'input_tokens': 9000, 'output_tokens': 400}})
                                by = 'frontier_graph_planner'
                        finally:
                            ledger.close()
                        return {'question': question, 'graph': {}, 'planner_receipt': {'route': 'shared_planner', 'interpretation': {
                                'interpreter': by, 'execution_accepted': True}}, 'planning_economics': {}, 'new_paid_api_calls': 0,
                                'answer': {'status': 'answered', 'answer_type': 'count', 'headline': '1'}}
                telemetry, _ = CLI.run_set(QUESTIONS, Path(tmp) / 'out', stack=Stack())
            finally:
                os.environ.pop('AGERE_LEDGER_PATH'); os.environ.pop('AGERE_TRAINING_DIR')
        card = S.scorecard(telemetry, question_set='fixed')
        self.assertEqual(card['questions'], 48)
        self.assertGreater(card['answered_no_llm'], 0.5)
        self.assertGreater(card['system_one']['decided_share'], 0)
        self.assertGreaterEqual(card['opus_without_failed_sonnet']['failures'], 2)  # the why/driver questions
        self.assertTrue(card['violations'])

    def test_recorded_run_if_present(self):
        """The real scorecard run (tools/token_scorecard.py --run) when one exists on this machine; reported, not assumed."""
        root = next((p / 'artifacts' / 'scorecard' / 'latest' for p in (Path(CLI.ROOT), *Path(CLI.ROOT).parents)
                     if (p / 'artifacts' / 'scorecard' / 'latest' / 'per_question.jsonl').exists()), None)
        if root is None: self.skipTest('no recorded scorecard run (python tools/token_scorecard.py --run)')
        telemetry, _ = CLI.from_usage_run(root)
        card = S.scorecard(telemetry)
        print('\n' + S.render(card))
        self.assertEqual(card['questions'], len(telemetry))


if __name__ == '__main__':
    unittest.main()
