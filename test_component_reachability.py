"""Every Agere component must be reachable from the question path (Mike, 2026-10-21: "the entire point of the stack is to
use the right tool for the job"; no component removed or defaulted off without his written OK).

config/agere/component_ledger.json lists every component and how it is wired. These tests verify the declared wiring
against the code instead of trusting it:
  * every built component imports (module + symbol exist);
  * every cascade tier is attempted, in order, when the ones before it decline, and none is off by default;
  * graph tiers call a Ladder method that exists; engines are on the shared planner's operator menu;
  * every entry point (/api/stack/compose, /api/workspaces, follow-ups) reaches shared_planner.compose;
  * no built component is left off the path unless the ledger records Mike's written approval for it.
No model, network or warehouse is used; tiers are stubbed to record that they were reached.
"""
import importlib
import inspect
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import proposers as P
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT/'config/agere/component_ledger.json'
OFF_PATH = ('not_on_path', 'named_only')
# Env vars that would switch a component off; a clean default environment must leave every component on.
OFF_SWITCHES = ('AGERE_PROPOSERS', 'AGERE_PROPOSER_SKIP', 'AGERE_LOCAL_NORMALIZER', 'AGERE_LOCAL_TYPED', 'AGERE_BOTTLED',
                'AGERE_HYBRID')


def ledger():
    return json.loads(LEDGER.read_text())


def clean_env():
    return patch.dict(os.environ, {k: v for k, v in os.environ.items() if k not in OFF_SWITCHES}, clear=True)


class Cascade(unittest.TestCase):
    def run_all_declining(self, allow_live):
        reached = []

        def stub(name):
            def tier(self):
                reached.append(name)
                return None
            return tier
        stack = SimpleNamespace(allow_live=allow_live, path='/nonexistent')
        with clean_env(), patch.multiple(P.Cascade, **{'tier_' + n: stub(n) for n in P.ORDER}):
            cascade = P.Cascade(stack, SimpleNamespace(identity={}), 'how many patients', ledger=SimpleNamespace(close=lambda: None))
            with self.assertRaises(QuestionNotUnderstood):
                cascade.run()
        return reached

    def test_every_tier_is_attempted_in_order_when_live(self):
        self.assertEqual(self.run_all_declining(True), list(P.ORDER))

    def test_free_tiers_run_without_live_calls(self):
        self.assertEqual(self.run_all_declining(False), [n for n in P.ORDER if n not in P.PAID])

    def test_every_tier_has_a_method_and_is_on_by_default(self):
        live = SimpleNamespace(allow_live=True)
        with clean_env():
            for name in P.ORDER:
                self.assertTrue(callable(getattr(P.Cascade, 'tier_' + name, None)), name)
                self.assertTrue(P.enabled(name, live), f'{name} is off by default')

    def test_graph_tiers_call_existing_ladder_methods(self):
        from clinical_core.agere.clinical.economical_planner import Ladder
        for name in P.ORDER:
            source = inspect.getsource(getattr(P.Cascade, 'tier_' + name))
            for method in re.findall(r'self\.ladder\.(\w+)\(', source):
                self.assertTrue(callable(getattr(Ladder, method, None)), f'{name} calls missing Ladder.{method}')

    def test_tier_defaults_inside_methods_are_on(self):
        for name in P.ORDER:
            source = inspect.getsource(getattr(P.Cascade, 'tier_' + name))
            for var, default in re.findall(r"os\.environ\.get\('(\w+)', '(\w+)'\)", source):
                self.assertNotEqual(default, 'off', f'{name}: {var} defaults off')


class EntryPoints(unittest.TestCase):
    def test_planner_hands_declined_questions_to_the_cascade(self):
        from clinical_core.agere.clinical import shared_planner
        self.assertIn('Cascade(', inspect.getsource(shared_planner.plan))
        self.assertIn('proposers.execute', inspect.getsource(shared_planner.compose))

    def test_stack_compose_uses_the_shared_planner(self):
        from clinical_core.agere.clinical.stack_runtime import Stack
        self.assertIn('from .shared_planner import compose', inspect.getsource(Stack.compose))

    def test_workspaces_use_the_shared_planner(self):
        from clinical_core.agere.clinical import together
        self.assertIn('from .shared_planner import compose', inspect.getsource(together.compose_together))
        from clinical_core.agere.onboarding import workspace_stack
        self.assertIn('compose_together', inspect.getsource(workspace_stack))

    def test_api_compose_and_follow_ups_reach_stack_compose(self):
        from clinical_core.agere.clinical import conversation
        self.assertIn('stack.compose(', inspect.getsource(conversation.handle_compose))
        service = (ROOT/'clinical_core/agere/service.py').read_text()
        self.assertIn('conversation.handle_compose(', service)


class Ledger(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.components = ledger()['components']
        self.approved = {a['id'] for a in ledger().get('approved_exceptions', []) if a.get('mike_written_approval')}

    def test_ids_are_unique(self):
        ids = [c['id'] for c in self.components]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_built_component_imports(self):
        broken = []
        for c in self.components:
            if c['current_wiring'] == 'not_built': continue
            if not c.get('module'):
                if not any((ROOT/e.strip().split(':')[0]).exists() for e in (c.get('evidence') or '').split(';')):
                    broken.append(f"{c['id']}: no module and no existing evidence path")
                continue
            if c['module'].startswith('tools.'):
                if not (ROOT/(c['module'].replace('.', '/') + '.py')).exists(): broken.append(f"{c['id']}: {c['module']} missing")
                continue
            try:
                target = importlib.import_module(c['module'])
                for part in (c.get('symbol') or '').split('.') if c.get('symbol') else []:
                    target = getattr(target, part)
            except AttributeError:
                broken.append(f"{c['id']}: {c['module']}.{c['symbol']} missing")
            except Exception as exc:  # noqa: BLE001 - report every broken import together
                broken.append(f"{c['id']}: {type(exc).__name__}: {exc}"[:200])
        self.assertEqual(broken, [])

    def test_declared_cascade_tiers_exist(self):
        for c in self.components:
            if c['current_wiring'].startswith('cascade_tier:'):
                self.assertIn(c['current_wiring'].split(':', 1)[1], P.ORDER, c['id'])

    def test_declared_spine_stages_exist(self):
        for c in self.components:
            if c['current_wiring'].startswith('spine:'):
                self.assertTrue(c.get('module') and c.get('symbol'), c['id'])

    def test_every_cascade_tier_is_in_the_ledger(self):
        declared = {c['current_wiring'].split(':', 1)[1] for c in self.components if c['current_wiring'].startswith('cascade_tier:')}
        self.assertEqual(set(P.ORDER) - declared, set())

    def test_inside_components_point_at_reachable_hosts(self):
        by_id, stranded = {c['id']: c for c in self.components}, []
        for c in self.components:
            if not c['current_wiring'].startswith('inside:'): continue
            host, seen = c, set()
            while host['current_wiring'].startswith('inside:') and host['id'] not in seen:
                seen.add(host['id'])
                host = by_id.get(host['current_wiring'].split(':', 1)[1]) or {'id': '?', 'current_wiring': 'not_built'}
            if host['current_wiring'] in OFF_PATH + ('not_built',) and host['id'] not in self.approved:
                stranded.append(f"{c['id']} (inside {host['id']}, {host['current_wiring']})")
        self.assertEqual(stranded, [], 'components that live inside an unreachable host')

    def test_engines_are_on_the_planner_menu(self):
        from clinical_core.agere.clinical import shared_planner
        menu_source = inspect.getsource(shared_planner)
        excluded = set(re.findall(r"op not in \(([^)]*)\)", menu_source)[0].replace("'", '').replace(' ', '').split(','))
        for c in self.components:
            if c['current_wiring'] == 'planner_menu' and c['family'] == 'engine':
                self.assertNotIn(c['id'], excluded, f"{c['id']} is excluded from the operator menu")

    def test_no_built_component_is_off_the_question_path(self):
        off = sorted(f"{c['id']} ({c['current_wiring']})" for c in self.components
                     if c['current_wiring'] in OFF_PATH and c['id'] not in self.approved)
        self.assertEqual(off, [], 'components built but not reachable from the question path')


if __name__ == '__main__':
    unittest.main()
