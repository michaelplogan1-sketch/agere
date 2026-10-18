"""Agere deliverables (clinical_core/agere/deliverables): reference examples render from fixtures, receipts resolve, the number
verifier rejects invented numbers / network / tampered data, the browser finance twin matches Python, Shapley is efficient,
the composer delivers / repairs / fails closed with a fake model sender, revise() reuses cached tool results, the API route
and decision write-back work, and no model is called when the deliverable needs none. Small synthetic sqlite fixtures."""
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import test_plans
import test_simulator
import importlib

from clinical_core.agere.deliverables import examples, finance, intent, runtime, verify
from clinical_core.agere.deliverables.receipts import Bundle
from clinical_core.agere.onboarding.workspace import Workspace

composemod = importlib.import_module('clinical_core.agere.deliverables.compose')

BARRIER_COLS = ('patient_id', 'drug_class', 'gdmt_pillar', 'drug_key', 'barrier_type', 'detail', 'event_date', 'last_date', 'mentions',
                'evidence_text', 'record_id', 'source_type', 'method', 'confidence', 'assertion', 'negated', 'hypothetical', 'experiencer')


def _fixture_ws(root):
    d = test_plans._workspace(root)
    test_simulator.add_history(d / 'semantic.sqlite3')
    db = sqlite3.connect(d / 'semantic.sqlite3')
    db.execute(f"CREATE TABLE therapy_barrier({', '.join(BARRIER_COLS)})")
    db.execute(f"INSERT INTO therapy_barrier VALUES ({','.join('?' * len(BARRIER_COLS))})",
               ('HFX00', 'acei', 'rasi', 'lisinopril', 'intolerance_side_effect', 'cough', '2030-04-01', '2030-04-01', 1,
                'Lisinopril stopped because of a persistent dry cough.', 'NOTE-1', 'clinical_note', 'rules', 0.9, 'affirmed', 0, 0, 'patient'))
    db.commit()
    db.close()
    return Workspace('plan-clinic', root)


def _what_if_fixture(n=400, seed=3):
    rng = np.random.default_rng(seed)
    d = {'program_kind': 'virtual_pt', 'terms_label': 'MSSP ENHANCED', 'iterations': n, 'simulation_id': 'SIM-FIXTURE0001',
         'arm': {'id': 'plan', 'label': 'Routed plan', 'patients': 30, 'programs': ['access_virtual_pt_msk'],
                 'saved_total': (rng.gamma(2, 4000, n)).round(2).tolist(), 'pharmacy_total': [0.0] * n,
                 'counts': {'access_virtual_pt_msk': {'outreach': [30] * n, 'enrolled': rng.integers(5, 15, n).tolist(),
                                                      'attended': rng.integers(3, 10, n).tolist(), 'completed': rng.integers(1, 8, n).tolist()}},
                 'unit_costs': {'access_virtual_pt_msk': {'per_outreach': 5}},
                 'oap': {'access_virtual_pt_msk': {'track': 'MSK', 'annual_allowed': 180, 'months': 12, 'withheld_share': 0.5,
                                                   'outcome_attainment_threshold': 0.5, 'medicare_share': 0.8, 'co_management_per_enrolled': 30,
                                                   'p_target_met_if_completed': 0.65, 'simulated_attainment': 0.41, 'counts_in_benchmark': True,
                                                   'sources': ['cms']}}},
         'population': {'boot_rel': (1 + rng.normal(0, 0.03, n)).round(6).tolist(), 'z_trend': rng.standard_normal(n).round(5).tolist(),
                        'z_drift': rng.standard_normal(n).round(5).tolist(), 'trend_sd': 0.02, 'risk_drift_sd': 0.01, 'bootstrap_members': 900,
                        'bootstrap_base_pmpm': 401.5, 'member_months': 12000.0, 'members': 1000, 'projected_do_nothing_pmpm': 380.25, 'seed': 7},
         'terms': {'sharing_rate': 0.75, 'two_sided': True, 'msr': 0.03, 'mlr': 0.03, 'loss_rate': 0.4, 'savings_cap_pct': 0.2, 'loss_cap_pct': 0.15},
         'negotiation_bounds': {'sharing_rate_min': 0.3, 'sharing_rate_max': 0.8, 'msr_min': 0.0, 'msr_max': 0.05},
         'negotiation_bounds_label': 'typical published range; not this payer\'s offer'}
    d['defaults'] = {**finance.DEFAULT_SLIDERS, 'attainment': 0.41}
    d['initial'] = finance.evaluate(d)
    d['initial_nash'] = finance.nash(d)
    return d


def _sim_fixture():
    q = lambda m: {'mean': m, 'p05': m * 0.8, 'p95': m * 1.2}
    return {'id': 'SIM-FIXTURE0001', 'summary_table': [
        {'arm': 'do_nothing', 'label': 'Do nothing', 'admissions': q(12.0), 'ed_visits': q(40.0), 'p_earn_shared_savings': 0.31},
        {'arm': 'plan', 'label': 'Routed plan', 'admissions': q(11.5), 'ed_visits': q(37.0), 'net_savings': q(-4000.0),
         'shared_savings_change': q(120.0), 'p_earn_shared_savings': 0.33}]}


class Receipts(unittest.TestCase):
    def test_paths_resolve_and_numbers_index_receipts(self):
        b = Bundle({'workspace': 'w'})
        rid = b.add('plan', {'program_kind': 'pharmacy_gdmt'}, {'summary': {'by_route': {'enroll': 263}}, 'rows': [{'x': 1.5}]},
                    definition='routing digest abc', sql='SELECT 1', as_of='2030-05-27', data_fp='semantic:1')
        self.assertEqual(b.get(rid + ':summary.by_route.enroll'), 263)
        self.assertEqual(b.get(rid + ':rows.0.x'), 1.5)
        self.assertRaises(KeyError, b.get, rid + ':summary.missing')
        self.assertRaises(KeyError, b.get, 'R-0000000000:x')
        self.assertIn(rid, b.numbers()[263.0])
        rec = b.receipts[rid]
        self.assertEqual((rec['tool'], rec['as_of'], rec['definition']), ('plan', '2030-05-27', 'routing digest abc'))
        self.assertTrue(rec['sql_fingerprint'].startswith('sql:'))


class Verifier(unittest.TestCase):
    def setUp(self):
        self.b = Bundle({'workspace': 'w'})
        self.rid = self.b.add('t', {}, {'savings': 143045.39, 'p': 0.353, 'n': 263})

    def page(self, body):
        return runtime.wrap(body, self.b, 'T')

    def test_bound_and_matching_numbers_pass(self):
        r = verify.verify(self.page(f'<p>Savings <span data-bind="{self.rid}:savings" data-fmt="usd0"></span>, about $143,045 or $143.0K, '
                                    f'35.3% of 263 patients on 2030-05-27, step 3.</p><script>var x = AGERE.get("{self.rid}:p"), w = 640;</script>'), self.b)
        self.assertTrue(r['passed'], r['violations'])
        self.assertTrue(any(self.rid in n['receipts'] for n in r['numbers']))

    def test_rejects_invented_numbers_network_and_unresolved_bindings(self):
        r = verify.verify(self.page(f'<p>We will save $2.4M and 41% more.</p><span data-bind="{self.rid}:nope"></span>'
                                    '<script>el.textContent = "Saved $1,234,567"; var k = 48213.5;</script><img src="https://x.example/a.png">'), self.b)
        kinds = {(v['kind'], v['detail']) for v in r['violations']}
        self.assertFalse(r['passed'])
        for k in [('number_not_in_data', '$2.4M'), ('number_not_in_data', '41%'), ('number_in_script_text_not_in_data', '$1,234,567'),
                  ('script_literal_not_in_data', '48213.5'), ('network', 'external URL'), ('binding_unresolved', f'{self.rid}:nope')]:
            self.assertIn(k, kinds)

    def test_rejects_tampered_data_block(self):
        doc = self.page('<p>ok</p>').replace('143045.39', '999999.99')
        self.assertIn('data_block_mismatch', {v['kind'] for v in verify.verify(doc, self.b)['violations']})

    def test_fetch_only_to_workspace_api(self):
        ok = self.page("<script>fetch('/api/workspaces/' + AGERE.data.meta.workspace + '/deliverable/decision', {method: 'POST'});</script>")
        bad = self.page("<script>fetch('/elsewhere/collect', {method: 'POST'});</script>")
        self.assertTrue(verify.verify(ok, self.b)['passed'])
        self.assertIn('network', {v['kind'] for v in verify.verify(bad, self.b)['violations']})


class Finance(unittest.TestCase):
    def test_settlement_matches_vbc_and_shapley_is_efficient(self):
        from clinical_core.agere.clinical import vbc
        d = _what_if_fixture()
        t = d['terms']
        for a in (350.0, 370.0, 380.0, 395.0, 420.0):
            self.assertAlmostEqual(float(finance.payout(np.array([a]), np.array([380.0]), 12000.0, t)[0]), vbc.settle(a, 380.0, 12000.0, t), places=4)
        rng = np.random.default_rng(1)
        X = rng.gamma(2, 300, (400, 30))
        players = [f'P{i % 4}' for i in range(30)]
        sh = finance.shapley(X, players, d)
        self.assertAlmostEqual(sum(p['shapley_value'] for p in sh['players']), sh['pooled_settlement_change'], places=6)

    def test_browser_twin_matches_python(self):
        node = shutil.which('node') or str(Path.home() / '.local/bin/node')
        if not Path(node).exists():
            self.skipTest('node not available')
        d = _what_if_fixture()
        s = {**d['defaults'], 'benchmark_factor': 1.04, 'trend': -0.01, 'scale': 1.5, 'attainment': 0.6}
        py, pn = finance.evaluate(d, s), finance.nash(d, s)
        js = finance.JS_EVAL + f'\nvar D = {json.dumps(d)}, S = {json.dumps(s)};\nvar e = AgereFinance.evaluate(D, S); delete e._net;\n' \
             'var n = AgereFinance.nash(D, S); delete n.grid; console.log(JSON.stringify({e: e, n: n}));'
        out = json.loads(subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=60, check=True).stdout)
        for k in ('expected_settlement_change', 'p_earn_with_program', 'aco_net_mean', 'aco_net_p05', 'var', 'cvar', 'upside_call_value', 'oap_payments_mean'):
            self.assertAlmostEqual(out['e'][k], py[k], delta=1e-6 * max(1.0, abs(py[k])), msg=k)
        self.assertEqual(out['n']['walk_away_sharing_rate'], pn['walk_away_sharing_rate'])
        self.assertEqual((out['n']['nash'] or {}).get('sharing_rate'), (pn['nash'] or {}).get('sharing_rate'))


class Examples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ws = _fixture_ws(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assert_receipts_resolve(self, out):
        doc = out['html']
        _, rest = runtime.split(doc)
        bundle = Bundle.from_json(json.loads((Path(out['path']).parent / 'bundle.json').read_text()))
        for path in re.findall(r'data-bind="([^"]+)"', rest):
            if not re.fullmatch(r'R-[0-9a-f]{10}:.*', path):
                continue
            rid = path.split(':')[0]
            self.assertIn(rid, bundle.receipts)
            rec = bundle.receipts[rid]
            self.assertTrue(rec['definition'] and rec['as_of'] and rec['data_fingerprint'])

    def test_war_room_renders_from_fixture_and_verifies(self):
        b = Bundle({'workspace': 'plan-clinic', 'deliverable_id': 'DLV-TEST'})
        rid = b.add('what_if', {'program_kind': 'virtual_pt'}, _what_if_fixture(), definition='fixture what-if', as_of='2030-05-27', data_fp='f')
        sid = b.add('simulate', {'program_kind': 'virtual_pt'}, _sim_fixture(), definition='fixture simulation', as_of='2030-05-27', data_fp='f')
        body, title, sub = examples.render('war_room', b, [rid, sid], {'program_kind': 'virtual_pt'})
        doc = runtime.wrap(body, b, title, subtitle=sub)
        r = verify.verify(doc, b)
        self.assertTrue(r['passed'], r['violations'])
        self.assertIn('AgereFinance', doc)
        self.assertIn('ACCESS OAP outcome attainment', doc)

    def test_pharmacist_cards_and_self_running_program_compose_without_a_model(self):
        with mock.patch.object(composemod, 'ledgered_sender', side_effect=AssertionError('no model sender may be built')):
            cards = composemod.compose(self.ws, None, template='pharmacist_cards')
            prog = composemod.compose(self.ws, None, template='self_running_program', params={'program_kind': 'pharmacy_gdmt'})
        for out in (cards, prog):
            self.assertEqual(out['status'], 'delivered', out['verification'])
            self.assertEqual(out['costs']['total_usd'], 0)
            self.assertEqual(out['costs']['frontier']['calls'] + out['costs']['small']['calls'], 0)
            self.assert_receipts_resolve(out)
        data = Bundle.from_json(json.loads((Path(cards['path']).parent / 'bundle.json').read_text()))
        res = next(v for k, v in data.results.items() if data.receipts[k]['tool'] == 'barriers')
        self.assertEqual(res['patients_with_barriers'], 1)
        card = res['cards'][0]
        self.assertEqual(card['patient_id'], 'HFX00')
        self.assertEqual(card['pillars']['rasi']['barriers'][0]['evidence_text'], 'Lisinopril stopped because of a persistent dry cough.')
        self.assertIn('valsartan', card['pillars']['rasi']['usable_agents'])
        self.assertTrue(any(w['lane'] == 'plan:pharmacy_gdmt' for w in card['work_items']))

    def test_free_text_needs_the_model_unless_a_template(self):
        with mock.patch.object(composemod, 'ledgered_sender', side_effect=AssertionError('no model sender may be built')):
            self.assertRaises(ValueError, composemod.compose, self.ws, 'an interactive story of our heart failure program', live=False)


def _fake(replies):
    """A sender returning queued replies (text or callable(body) -> text); records every body."""
    calls = []

    def send(body):
        calls.append(body)
        r = replies.pop(0)
        text = r(body) if callable(r) else r
        return ({'model': body['model'], 'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn',
                 'usage': {'input_tokens': 1000, 'output_tokens': 500}}, {'cost_estimate': {'estimated_usd': 0.0123}})
    return send, calls


def _plan_rid(body):
    return re.search(r'"receipt":"(R-[0-9a-f]{10})","tool":"plan"', body['messages'][0]['content']).group(1)


class Composer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = _fixture_ws(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_repair_then_deliver_and_revise_reuses_tools(self):
        bad = lambda b: (f'TITLE: HF launch\n```html\n<h1>HF launch</h1><p>Routed <span data-bind="{_plan_rid(b)}:patients" data-fmt="int"></span>; '
                         'we will save $987,654.</p>\n```')
        fix = '```json\n[{"find": "we will save $987,654.", "replace": "savings are simulated below."}]\n```'
        send, calls = _fake([bad, fix])
        out = composemod.compose(self.ws, 'a board-ready story of our heart failure PharmD launch', send=send)
        self.assertEqual(out['status'], 'delivered', out['verification'])
        self.assertTrue(out['repaired'])
        self.assertEqual(out['verification']['first_attempt']['violations'][0]['detail'], '$987,654')
        self.assertEqual([c['purpose'] for c in out['model_calls']], ['compose', 'repair'])
        self.assertEqual(out['costs']['frontier']['calls'], 2)
        self.assertAlmostEqual(out['costs']['total_usd'], 0.0246)
        self.assertTrue(all(c['model'] == composemod.FRONTIER_MODEL for c in calls))
        self.assertIn('cache_control', json.dumps(calls[0]['system']))
        self.assertEqual(calls[0]['tools'][-1].get('cache_control'), {'type': 'ephemeral'})
        tools_used = {c['tool'] for c in out['tool_calls']}
        self.assertIn('plan', tools_used)
        self.assertIn('savings are simulated below.', out['html'])
        # revise: edits applied, no tool re-executed
        send2, calls2 = _fake(['```json\n[{"find": "<h1>HF launch</h1>", "replace": "<h1>HF launch, revised</h1>"}]\n```'])
        rev = composemod.revise(self.ws, out['id'], 'retitle it', send=send2)
        self.assertEqual(rev['status'], 'delivered', rev['verification'])
        self.assertEqual(rev['revision'], 2)
        self.assertIn('HF launch, revised', rev['html'])
        self.assertEqual([c for c in rev['tool_calls'] if not c['cached']], [])
        self.assertTrue((Path(out['path']).parent / 'revisions' / 'r1.html').is_file())

    def test_fails_closed_when_repair_does_not_fix(self):
        bad = 'TITLE: X\n```html\n<p>Net savings of $555,555 next year.</p>\n```'
        send, _ = _fake([bad, bad])
        out = composemod.compose(self.ws, 'a story of our heart failure program', send=send)
        self.assertEqual(out['status'], 'rejected')
        self.assertIsNone(out['html'])
        self.assertIsNone(out['path'])
        self.assertFalse((Path(self.ws.dir) / 'deliverables' / out['id'] / 'artifact.html').exists())


class Intent(unittest.TestCase):
    def test_parse(self):
        p = intent.parse('a game-theory negotiation simulator for our heart failure PharmD program for the CFO')
        self.assertEqual((p['program_kind'], p['audience']), ('pharmacy_gdmt', 'CFO'))
        self.assertIn(('contract', {'focus': 'negotiate'}), p['tools'])
        self.assertIn(('plan', {'program_kind': 'pharmacy_gdmt'}), p['tools'])
        self.assertEqual(intent.parse('pharmacist decision cards')['example'], 'pharmacist_cards')
        self.assertEqual(intent.parse('the messages for each ED low back pain patient')['program_kind'], 'virtual_pt')


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.ws = _fixture_ws(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_deliverable_routes_and_decision_write_back(self):
        from clinical_core.agere import workspace_api
        code, out = workspace_api.handle('POST', '/api/workspaces/plan-clinic/deliverable', {'template': 'pharmacist_cards'}, root=self.root)
        self.assertEqual(code, 200, out)
        self.assertEqual(out['status'], 'delivered')
        self.assertTrue(out['html'].startswith('<!doctype html>'))
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/deliverable', {'template': 'pharmacist_cards'},
                                              root=self.root, can_write=False)[0], 403)
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/deliverable', {}, root=self.root)[0], 400)
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/deliverable', {'template': 'nope'}, root=self.root)[0], 400)
        code, lst = workspace_api.handle('GET', '/api/workspaces/plan-clinic/deliverables', root=self.root)
        self.assertEqual((code, lst['deliverables'][0]['id']), (200, out['id']))
        code, one = workspace_api.handle('GET', f"/api/workspaces/plan-clinic/deliverables/{out['id']}", root=self.root)
        self.assertEqual((code, one['status']), (200, 'delivered'))
        bundle = Bundle.from_json(json.loads((Path(self.root) / 'plan-clinic' / 'deliverables' / out['id'] / 'bundle.json').read_text()))
        card = next(v for k, v in bundle.results.items() if bundle.receipts[k]['tool'] == 'barriers')['cards'][0]
        item_id = next(w['item_id'] for w in card['work_items'] if w['lane'] == 'plan:pharmacy_gdmt')
        code, rec = workspace_api.handle('POST', '/api/workspaces/plan-clinic/deliverable/decision',
                                         {'item_id': item_id, 'decision': 'alternative_agent', 'note': 'start valsartan', 'patient_id': 'HFX00',
                                          'deliverable_id': out['id']}, root=self.root)
        self.assertEqual(code, 200, rec)
        from clinical_core.agere.plans import backbone
        it = backbone.item(Path(self.root) / 'plan-clinic', item_id)
        self.assertEqual(it['item']['human_decisions'][-1]['decision'], 'alternative_agent')
        self.assertEqual(it['events'][-1]['action'], 'human_decision')
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/deliverable/decision',
                                              {'item_id': item_id, 'decision': 'delete_everything'}, root=self.root)[0], 400)
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/deliverable/decision',
                                              {'item_id': 'WI-0000000000000000', 'decision': 'close'}, root=self.root)[0], 404)


if __name__ == '__main__':
    unittest.main()
