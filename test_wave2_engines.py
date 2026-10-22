"""Wave-2 revenue/payer engines: every TEMPLATE compiles to its own engine for free, defaults validate, and (when the active
warehouse is present) the first template of each engine executes end to end and returns a sourced, assumption-bearing answer."""
import importlib
import json
from pathlib import Path
import unittest

from clinical_core.agere.clinical import engine_suite

AS_OF = '2030-05-27'
ROOT = Path(__file__).resolve().parent
WAVE2 = ['denial_prediction', 'cdi_opportunity', 'contract_reimbursement', 'price_benchmark', 'prior_auth_rules', 'eligibility_340b', 'ddi_screening',
         'provider_network_graph', 'network_spread', 'upcoding_detection', 'benchmark_rebasing', 'survey_adjustment', 'scorecard_risk']


def mod(op):
    return importlib.import_module('clinical_core.agere.clinical.' + engine_suite.MODULES[op])


class Wave2(unittest.TestCase):
    def test_registered_and_routed(self):
        for op in WAVE2:
            self.assertIn(op, engine_suite.ROUTE_ORDER)
            m = mod(op)
            self.assertTrue(m.TEMPLATES and m.PRODUCT['use'] and m.CONTRACT['meaning'])
            for t in m.TEMPLATES:
                g, _ = engine_suite.compile_route(t, {}, AS_OF) or (None, None)
                self.assertEqual(g['nodes'][0]['operation'] if g else None, op, t)

    def test_defaults_validate_and_knowledge_questions_stay_knowledge(self):
        for op in WAVE2:
            m = mod(op)
            if op not in ('ddi_screening', 'network_spread'): m.validate({})  # these need a drug/class pattern
            with self.assertRaises(ValueError): m.validate({'not_a_parameter': 1})

    def test_live_warehouse_smoke(self):
        cfg = ROOT / 'config/agere/active_warehouse.json'
        stack = Path(json.loads(cfg.read_text())['build']) / 'stack' if cfg.exists() else None
        if not stack or not (stack / 'run.json').exists(): self.skipTest('active warehouse not present')
        for op in WAVE2:
            m = mod(op)
            g, _ = engine_suite.compile_route(m.TEMPLATES[0], {}, AS_OF)
            r = m.execute(stack, None, g['nodes'][0]['parameters'], AS_OF)
            a = m.answer(r, {'coverage': 'supported_observation'}, m.TEMPLATES[0])
            self.assertTrue(a['value'], op); self.assertTrue(a['limitations'], op); self.assertIn(a['status'], ('answered', 'partial'), op)


if __name__ == '__main__':
    unittest.main()
