"""Usage check: a component wired into Agere but never run on the realistic question set is flagged (fails).

The tracer (tools/component_usage.py) wraps every ledger component and asks the fixed question set through
conversation.handle_compose on the active warehouse. This test gates its latest run: every instrumented ledger
component must have run on at least one question; the only allowlist is build-time-only components (ledger
current_wiring 'build_time_only'). Without a run on this machine the gate cannot be evaluated and says so."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from tools import component_usage as U  # noqa: E402


class Tracer(unittest.TestCase):
    def test_wraps_functions_and_classes_and_counts(self):
        mod = types.ModuleType('fake_usage_mod')
        mod.f = lambda x: x + 1

        class K:
            def __init__(self, v): self.v = v
        mod.K = K
        sys.modules['fake_usage_mod'] = mod
        try:
            wrapped, skipped = U.instrument([{'id': 'f', 'module': 'fake_usage_mod', 'symbol': 'f'},
                                             {'id': 'k', 'module': 'fake_usage_mod', 'symbol': 'K'},
                                             {'id': 'tool', 'module': 'tools.x', 'symbol': 'y'},
                                             {'id': 'gone', 'module': 'fake_usage_mod', 'symbol': 'missing'}])
            self.assertEqual(wrapped, ['f', 'k']); self.assertEqual(skipped, ['tool', 'gone'])
            U.CALLS.clear(); mod.f(1); mod.f(2); mod.K(3)
            self.assertEqual(U.CALLS['f'], 2); self.assertEqual(U.CALLS['k'], 1)
        finally:
            del sys.modules['fake_usage_mod']

    def test_check_flags_unused_and_allowlists_build_time_only(self):
        ledger = [{'id': 'a', 'family': 'x', 'current_wiring': 'cascade_tier:a'},
                  {'id': 'b', 'family': 'x', 'current_wiring': 'inside:a'},
                  {'id': 'train', 'family': 'learning', 'current_wiring': 'build_time_only'},
                  {'id': 'new', 'family': 'x', 'current_wiring': 'planner_menu'}]
        rep = U.report(ledger[:3], ['a', 'b', 'train'], [], {'a': {'q1'}}, {}, U.Counter(), 1)
        verdict = U.check(rep, ledger)
        self.assertEqual(verdict['never_ran'], ['b'])
        self.assertEqual(verdict['allowlisted'], ['train'])
        self.assertEqual(verdict['not_measured_in_run'], ['new'])

    def test_per_question_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'per_question.jsonl'
            log.write_text(json.dumps({'id': 'q1', 'status': 'answered', 'answered_by': 'compositional_grammar',
                                       'components_run': ['answer_log_record']}) + '\n')
            rep = U.from_per_question(log, [{'id': 'answer_log_record', 'family': 'infrastructure', 'current_wiring': 'spine:x',
                                             'module': 'clinical_core.agere.clinical.answer_log', 'symbol': 'record'},
                                            {'id': 'learning_hooks', 'family': 'learning', 'current_wiring': 'spine:x',
                                             'module': 'clinical_core.agere.clinical.learning_hooks', 'symbol': 'after_answer'}])
            self.assertEqual(rep['never_ran'], ['learning_hooks'])


class UsageGate(unittest.TestCase):
    def test_every_wired_component_runs_on_the_question_set(self):
        rep, source = U.latest_report()
        if rep is None:
            self.skipTest('no usage run on this machine: python tools/component_usage.py --out artifacts/usage/<run>')
        verdict = U.check(rep)
        summary = (f"usage run {source}: {verdict['questions']} questions; {len(verdict['never_ran'])} wired components never ran: "
                   f"{verdict['never_ran']}; not instrumentable (non build-time): {verdict['not_instrumented']}; "
                   f"added to the ledger after the run: {verdict['not_measured_in_run']}")
        self.assertEqual(verdict['never_ran'], [], summary)


if __name__ == '__main__':
    unittest.main()
