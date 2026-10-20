"""Independent, hand-computed financial scenarios and source-bound learning checks.

Only temporary stores and public rulebook definitions; no reserved benchmark or
generator answers. These are engineering checks, not actuarial qualification.
"""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from clinical_core.agere.clinical import economics_learning as E, learning, programs, vbc
from clinical_core.agere.clinical import contract_strategy as C, shared_planner as S


class Settlement(unittest.TestCase):
    def test_independent_upside_downside_thresholds_and_caps(self):
        # $1m benchmark. A 2% hurdle unlocks first-dollar sharing.
        terms = {'sharing_rate': .5, 'loss_rate': .4, 'msr': .02, 'mlr': .02, 'two_sided': True}
        for actual, expected in ((97.9, 10500), (98, 10000), (98.1, 0), (101.9, 0), (102, -8000), (110, -40000)):
            with self.subTest(actual=actual): self.assertAlmostEqual(vbc.settle(actual, 100, 10000, terms), expected)
        self.assertEqual(vbc.settle(120, 100, 10000, {**terms, 'loss_cap_pct': .05}), -50000)
        self.assertEqual(vbc.settle(120, 100, 10000, {**terms, 'loss_cap_pct': .05,
                         'loss_cap_revenue_pct': .08, 'participant_revenue': 100000}), -8000)
        self.assertEqual(vbc.settle(120, 100, 10000, {**terms, 'two_sided': False}), 0)

    def test_invalid_or_missing_financial_inputs_are_rejected(self):
        cases = [({'sharing_rate': .75, 'two_sided': True}, 'loss_rate'),
                 ({'sharing_rate': .5, 'two_sided': True, 'loss_rate': .4, 'loss_cap_revenue_pct': .08}, 'participant_revenue'),
                 ({'sharing_rate': 1.5}, 'fraction'), ({'sharing_rate': True}, 'finite'),
                 ({'sharing_rate': .5, 'quality_score': float('nan')}, 'finite'),
                 ({'sharing_rate': .5, 'discount_pct': 1}, 'positive benchmark')]
        for terms, message in cases:
            with self.subTest(terms=terms), self.assertRaisesRegex(ValueError, message): vbc.settle(110, 100, 10000, terms)
        for actual in (float('nan'), float('inf'), -1, True):
            with self.assertRaises(ValueError): vbc.settle(actual, 100, 1000, {'sharing_rate': .5})

    def test_both_simulation_and_scalar_apply_the_revenue_cap(self):
        terms = programs.contract_terms('MSSP BASIC level E 2026', {'participant_revenue': 100000, 'msr': .02})
        np.testing.assert_allclose(C.payout([110, 120], [100, 100], 10000, terms), [-8000, -8000])
        terms.pop('participant_revenue')
        with self.assertRaisesRegex(ValueError, 'participant_revenue'): C.payout([110], 100, 10000, terms)

    def test_rulebook_loss_floor_and_quality_correction(self):
        full = programs.contract_terms('MSSP ENHANCED 2026', {'msr': .02, 'quality_score': 1})
        partial = programs.contract_terms('MSSP ENHANCED 2026', {'msr': .02, 'quality_score': .5})
        self.assertEqual(vbc.settle(110, 100, 10000, full), -40000)
        self.assertEqual(vbc.settle(110, 100, 10000, partial), -62500)
        self.assertNotIn('Quality performance standard met', json.dumps(partial['program']['assumptions']))

    def test_scenario_explains_assumptions_and_has_no_entitlement_claim(self):
        r = vbc.settlement_explanation(90, 100, 10000, {'sharing_rate': .5})
        self.assertEqual((r['benchmark_dollars'], r['gross_savings_or_loss'], r['net_payment_to_aco']), (1000000, 100000, 50000))
        self.assertEqual(r['threshold_method'], 'first_dollar_after_threshold')
        self.assertFalse(r['payment_entitlement_established']); self.assertFalse(r['causal_savings_established'])
        self.assertTrue(any('quality' in x for x in r['assumptions']))

    def test_corridors_are_separate_from_thresholds(self):
        terms = {'corridors': [{'from': 0, 'to': .05, 'rate': .5}, {'from': .05, 'to': None, 'rate': .25}], 'two_sided': True}
        self.assertAlmostEqual(vbc.settle(110, 100, 10000, terms), -37500)
        bad = copy.deepcopy(terms); bad['corridors'][1]['from'] = .04
        with self.assertRaisesRegex(ValueError, 'nonoverlapping'): vbc.settle(110, 100, 10000, bad)

    def test_default_year_is_disclosed_and_does_not_enable_rule_learning(self):
        terms = programs.contract_terms('MSSP ENHANCED')
        self.assertEqual(terms['program']['year_selection'], 'latest_stored_year_assumed')
        self.assertTrue(any('year was not supplied' in a for a in terms['program']['assumptions']))
        self.assertEqual(E.rule_options('MSSP ENHANCED downside risk'), [])
        self.assertEqual(programs.contract_terms('MSSP ENHANCED 2026')['program']['year_selection'], 'supplied')

    def test_numeric_loss_inputs_are_preserved_by_the_local_parser(self):
        from clinical_core.agere.clinical.local_compiler import _contract_terms
        terms = _contract_terms('shared savings with a $100 pmpm benchmark and sharing rate 50%, '
            'two-sided with 30% loss rate, loss cap of 5%, participant revenue of $1,000,000')
        self.assertEqual(terms['loss_rate'], .3)
        self.assertEqual(terms['loss_cap_pct'], .05)
        self.assertNotIn('savings_cap_pct', terms)
        self.assertEqual(terms['participant_revenue'], 1000000)


class Learning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.q = 'What is our downside risk under MSSP ENHANCED 2026?'
        self.rule = next(r for r in E.rule_options(self.q) if r['parameter'] == 'loss_rate_min')
        self.rule = {k: self.rule[k] for k in ('program', 'parameter', 'value', 'source_digest')}

    def correct(self, **kwargs):
        return learning.record_correction(self.path, self.q, 'The minimum shared-loss rate is 40%; check the specification.', **kwargs)

    def test_reported_feedback_does_not_promote_itself(self):
        self.correct(expected={'loss_rate_min': .01})
        self.assertEqual(E.context(self.path, self.q), [])
        self.assertEqual(learning.correction_context(self.path, self.q)[0]['status'], 'needs_revalidation')

    def test_source_checked_lesson_crosses_wording_but_not_year_or_program(self):
        r = self.correct(rule=self.rule)
        self.assertEqual(r['rule_verification']['status'], 'verified_rule')
        new_q = 'Explain the minimum loss sharing rate for MSSP ENHANCED 2026'
        lesson = E.context(self.path, new_q)[0]
        self.assertEqual(lesson['value'], .4); self.assertTrue(lesson['sources'])
        self.assertNotIn('note', lesson)
        self.assertEqual(E.context(self.path, new_q.replace('2026', '2025')), [])
        self.assertEqual(E.context(self.path, 'MSSP BASIC level E 2026 downside risk'), [])
        self.assertEqual(E.context(self.path, 'MSSP ENHANCED downside risk'), [])
        self.assertTrue(any(c.get('verified_rule') for c in learning.correction_context(self.path, new_q)))

    def test_wrong_claim_stale_version_and_invalid_scope_cannot_promote(self):
        for change in ({'value': .25}, {'source_digest': 'old'}, {'parameter': 'invented'},
                       {'program': {'id': 'mssp_enhanced', 'year': 2025}}, {'value': True}):
            with self.subTest(change=change):
                result = self.correct(rule={**self.rule, **change})
                self.assertEqual(result['rule_verification']['status'], 'rejected')
        self.assertEqual(E.lessons(self.path), [])

    def test_source_change_suspends_lesson_and_changes_planner_policy(self):
        self.correct(rule=self.rule)
        before = S.policy_identity()
        from clinical_core.agere.clinical import analysis_graph
        before_graph = analysis_graph.code_identity()
        docs = programs.load(); docs['mssp_enhanced']['parameters']['loss_rate_min']['value'] = .45
        with patch.object(programs, 'load', return_value=docs):
            self.assertEqual(E.context(self.path, self.q), [])
            self.assertEqual(E.lessons(self.path)[0]['status'], 'source_changed')
            self.assertNotEqual(S.policy_identity(), before)
            self.assertNotEqual(analysis_graph.code_identity(), before_graph)

    def test_later_correction_suspends_prior_rule_until_checked_again(self):
        self.correct(rule=self.rule); self.correct()
        self.assertEqual(E.context(self.path, self.q), [])
        self.assertEqual(E.lessons(self.path)[0]['status'], 'suspended_by_new_correction')
        self.correct(rule=self.rule)
        self.assertEqual(len(E.context(self.path, self.q)), 1)

    def test_suspension_keeps_cross_wording_feedback_and_cannot_reenable_old_cache(self):
        other = 'Explain the minimum loss sharing rate for MSSP ENHANCED 2026'
        before = learning.feedback_identity(self.path, other)
        self.correct(rule=self.rule)
        checked = learning.feedback_identity(self.path, other)
        self.correct()
        self.assertEqual(E.context(self.path, other), [])
        pending = learning.correction_context(self.path, other)
        self.assertTrue(any(c.get('financial_feedback') for c in pending))
        self.assertFalse(any(c.get('verified_rule') for c in pending))
        after = learning.feedback_identity(self.path, other)
        self.assertNotEqual(before, after)
        self.assertNotEqual(checked, after)
        self.assertEqual(E.feedback_context(self.path, other.replace('2026', '2025')), [])

    def test_verification_is_idempotent_and_history_is_immutable(self):
        r = self.correct(rule=self.rule)
        again = E.verify_rule(self.path, r['correction_id'], self.rule)
        self.assertEqual(again['id'], r['rule_verification']['id'])
        self.assertEqual(len(E.lessons(self.path)), 1)
        with sqlite3.connect(self.path/'learning.sqlite3') as db:
            with self.assertRaises(sqlite3.IntegrityError): db.execute("UPDATE economics_lessons SET payload='{}'")

    def test_question_capture_and_gap_priority_persist_without_training(self):
        for _ in range(3): learning.record_outcome(self.path, self.q, 'shared_planner', 'partial', 'Missing participant revenue')
        learning.record_outcome(self.path, 'Shared savings for our ACO?', 'vbc', 'answered')
        self.correct(rule=self.rule)
        r = learning.summary(self.path)['healthcare_economics']
        self.assertEqual(r['questions'], 4); self.assertEqual(r['verified_rules'], 1)
        self.assertEqual(r['gaps'], [{'reason': 'Missing participant revenue', 'count': 3}])
        self.assertFalse(r['model_weights_trained'])

    def test_rulebook_load_detects_updates_without_process_restart(self):
        root = self.path/'rules'; root.mkdir()
        doc = programs.load()['mssp_enhanced']; target = root/'enhanced.json'
        target.write_text(json.dumps(doc)); first = programs.load(root)
        doc['parameters']['loss_rate_min']['value'] = .45; target.write_text(json.dumps(doc))
        self.assertNotEqual(programs.load(root), first)
        first['mssp_enhanced']['parameters']['loss_rate_min']['value'] = .99
        self.assertEqual(programs.load(root)['mssp_enhanced']['parameters']['loss_rate_min']['value'], .45)

    def test_shared_interpreter_receives_scoped_verified_lesson(self):
        self.correct(rule=self.rule)
        # Exercise the actual prompt boundary; no paid call or fake acceptance.
        class StopProbe(Exception): pass
        captured = []
        def probe(system, user, **kw):
            captured.append(json.loads(user)); raise StopProbe()
        from types import SimpleNamespace
        catalog = SimpleNamespace(target=self.path/'semantic.sqlite3')
        with patch.object(S, 'schema_slice', return_value={}), patch.object(S, 'schema_prompt_view', side_effect=lambda x:x):
            with self.assertRaises(StopProbe): S.interpret(catalog, 'Describe the loss floor in MSSP ENHANCED 2026', model=probe)
        facts = [c['verified_rule'] for c in captured[0]['reported_corrections'] if c.get('verified_rule')]
        self.assertEqual(facts[0]['value'], .4)


if __name__ == '__main__': unittest.main()
