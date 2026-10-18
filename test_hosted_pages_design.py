"""Hosted app design system and demo flows: every page carries the site's tokens and fonts, the workspace app is wired
to the real API (ask, plan, work, decisions, simulate, deliverable feature-detect, physician value), and the follow-on
pathways (GDMT titration, remote monitoring + home labs) run after PharmD enrollment with receipts.
Synthetic sqlite fixtures only (test_plans.build_fixture)."""
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import test_action_backbone as tab
import test_plans
from clinical_core.agere.hosted import brand, pages, value
from clinical_core.agere.hosted.auth import AuthStore
from clinical_core.agere.hosted.secrets import SecretBox
from clinical_core.agere.hosted.server import make_server
from clinical_core.agere.plans import backbone, catalog as catmod, counterparty, pathways, router
from test_hosted_server import Client

TOKENS = ('#eeeee8', '#172b23', '#102b21', '#e8693d', '#cbd0c5')
PAGES = {'login': pages.login, 'app': pages.app, 'admin': pages.admin, 'account': pages.account}


def scripts(html):
    return re.findall(r'<script>(.*?)</script>', html, re.S)


def add_measurements(path, rows):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS measurement(record_id TEXT, patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL)')
    db.executemany('INSERT INTO measurement VALUES (?,?,?,?,?)', rows)
    db.commit()
    db.close()


def labs(pid, k=4.3, egfr=55, sbp=118, hr=72, date='2030-05-01'):
    return [(f'M-{pid}-{c}', pid, c, date, v) for c, v in (('potassium', k), ('egfr', egfr), ('systolic_bp', sbp), ('heart_rate', hr), ('weight', 88))]


class DesignSystem(unittest.TestCase):
    def test_every_page_carries_tokens_fonts_and_both_themes(self):
        for name, fn in PAGES.items():
            with self.subTest(page=name):
                html = fn()
                for t in TOKENS:
                    self.assertIn(t, html)
                self.assertIn('DM Sans', html)
                self.assertIn('IBM Plex Mono', html)
                self.assertIn('fonts.googleapis.com', html)
                self.assertIn('prefers-color-scheme:dark', html)
                self.assertIn(':root[data-theme="dark"]', html)
                self.assertIn('ui-monospace', html)  # system fallbacks when the web fonts are blocked
                self.assertIn(brand.PRODUCT_NAME, html)
                self.assertNotIn('Ask Tom', html)
                self.assertIn('eyebrow', html)

    def test_product_name_is_configured_in_one_place(self):
        self.assertEqual(brand.PRODUCT_NAME, 'Agere')
        self.assertEqual(brand.ASK_TITLE, 'Ask Agere')
        self.assertIn('Ask Agere', pages.app())
        self.assertIn('<title>Agere Sign In</title>', pages.login())

    def test_app_is_wired_to_the_workspace_api(self):
        html = pages.app()
        for route in ("W('/ask')", "W('/plan')", "W('/simulate')", "W('/deliverable')", "W('/deliverables')", "W('/value'",
                      "W('/work", "W('/decisions')", "W('/questions')", "W('/review')", "'/api/workspaces'", "'/api/uploads/'"):
            self.assertIn(route, html, route)
        for label in ('Plan &amp; act', 'Plan & act', 'Simulate', 'Create', 'Physician value', 'Connect', 'Define', 'Compile', 'Compute',
                      'Check', 'Compound', 'What I earned', 'COMING ONLINE', 'EXCEPTIONS THAT NEED A PERSON', 'PATIENT JOURNEY'):
            if label != 'Plan &amp; act':
                self.assertIn(label, html, label)
        gold = [json.loads(l)['question'] for l in brand.GOLD.read_text().splitlines() if l.strip()]
        self.assertTrue(gold)
        for q in gold:
            self.assertIn(json.dumps(q)[1:-1].replace('</', '<\\/'), html)
        self.assertTrue(any(q['category'] == 'Finance' for q in brand.example_questions()))

    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_page_scripts_parse(self):
        with tempfile.TemporaryDirectory() as d:
            for name, fn in PAGES.items():
                for i, js in enumerate(scripts(fn())):
                    p = Path(d) / f'{name}{i}.js'
                    p.write_text(js)
                    r = subprocess.run(['node', '--check', str(p)], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, f'{name}: {r.stderr[:500]}')


class Pathways(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = tab.workspace(self.tmp.name)
        self.db = self.ws.semantic_path

    def tearDown(self):
        self.tmp.cleanup()

    def run_plan(self, ids, cat):
        plan = router.route(self.db, ids, 'pharmacy_gdmt', tab.AS_OF, catalog=cat)
        return backbone.run(self.ws.dir, plan, workspace=self.ws.slug, semantic_path=self.db, catalog=cat)

    def follow(self, pid):
        return {i['program_id']: backbone.item(self.ws.dir, i['id'])['item']
                for i in backbone.items(self.ws.dir, patient_id=pid, kind='follow_on')['items']}

    def test_catalog_has_both_pathways_as_follow_on_work(self):
        cat = catmod.load()
        progs = {p['id']: p for p in cat['programs']}
        self.assertEqual(progs['gdmt_titration_pathway']['role'], 'follow_on')
        self.assertEqual(progs['hf_remote_monitoring_home_labs']['role'], 'follow_on')
        self.assertEqual({f['program_id'] for f in progs['pharmacy_gdmt_titration']['follow_on']},
                         {'gdmt_titration_pathway', 'hf_remote_monitoring_home_labs'})
        for trig in ('titration_hold', 'rpm_threshold_breach', 'lab_out_of_range', 'device_not_activated'):
            self.assertIn(trig, cat['simulation']['followups'])
        self.assertFalse(progs['gdmt_titration_pathway']['request_keywords'])  # request routing to existing kinds unchanged
        with self.assertRaises(ValueError):
            router.route(self.db, ['HF1'], 'gdmt_titration', tab.AS_OF)

    def test_enrolled_hf_patients_get_titration_and_monitoring_with_receipts(self):
        add_measurements(self.db, labs('HF1') + labs('HF4'))
        res = self.run_plan(['HF1', 'HF4'], tab.forced())
        fo = res['follow_on']
        self.assertEqual(set(fo['by_program']), {'gdmt_titration_pathway', 'hf_remote_monitoring_home_labs'})
        self.assertEqual(fo['items'].get('created'), 4)
        got = self.follow('HF4')
        t, m = got['gdmt_titration_pathway'], got['hf_remote_monitoring_home_labs']
        for it in (t, m):
            self.assertTrue(all(s.get('receipt') for s in it['action']['steps'] if s['status'] == 'done'))
            self.assertTrue(it['lane'].startswith('plan:pharmacy_gdmt' + pathways.SEP))
        steps = [s['step'] for s in t['action']['steps']]
        self.assertIn('safety_check', steps)
        self.assertIn('dose_step_proposal', steps)
        first = t['action']['pathway']['cycles'][0]['proposal']
        self.assertEqual(first['action'], 'start')  # HF4 is missing every pillar: start the first in pillar order
        self.assertEqual(first['pillar'], 'sglt2i')
        self.assertIn('device_activation', [s['step'] for s in m['action']['steps']])
        safety = next(s for s in t['action']['steps'] if s['step'] == 'safety_check')
        self.assertEqual(safety['receipt']['counterparty_id'], 'sim:lab_vitals_feed')
        self.assertEqual(safety['receipt']['detail']['measures']['potassium']['value'], 4.3)
        # plan items are unchanged by follow-on work: kind filters and the parent funnel do not see them
        self.assertEqual({i['kind'] for i in backbone.items(self.ws.dir, kind='plan_item')['items']}, {'plan_item'})
        self.assertNotIn('follow_on', res['items'])

    def test_replay_is_idempotent(self):
        add_measurements(self.db, labs('HF1') + labs('HF4'))
        cat = tab.forced()
        self.run_plan(['HF1', 'HF4'], cat)
        n = tab.count(self.ws, 'SELECT COUNT(*) FROM work_items'), tab.count(self.ws, 'SELECT COUNT(*) FROM work_events')
        again = self.run_plan(['HF1', 'HF4'], cat)
        self.assertEqual(again['follow_on']['items'], {'unchanged': 4})
        self.assertEqual((tab.count(self.ws, 'SELECT COUNT(*) FROM work_items'), tab.count(self.ws, 'SELECT COUNT(*) FROM work_events')), n)

    def test_potassium_and_egfr_hold_ras_and_mra_steps(self):
        add_measurements(self.db, labs('HF4', k=5.4, egfr=25, sbp=96, hr=55))
        self.run_plan(['HF4'], tab.forced())
        t = self.follow('HF4')['gdmt_titration_pathway']
        holds = {(h['pillar'], h['measure']) for h in t['action']['pathway']['cycles'][0]['holds']}
        self.assertTrue({('mra', 'potassium'), ('rasi', 'egfr'), ('beta_blocker', 'heart_rate'), ('rasi', 'systolic_bp')} <= holds)
        # SGLT2i (eGFR 25 >= 20) is still allowed, so the first step proposes it
        self.assertEqual(t['action']['pathway']['cycles'][0]['proposal']['pillar'], 'sglt2i')
        for c in t['action']['pathway']['cycles']:
            if c['proposal']:
                self.assertNotIn(c['proposal']['pillar'], ('mra', 'rasi', 'beta_blocker'))

    def test_no_recent_labs_asks_for_a_bmp_and_creates_follow_up_work(self):
        add_measurements(self.db, [('M-x', 'HF4', 'systolic_bp', '2030-05-01', 120), ('M-y', 'HF4', 'heart_rate', '2030-05-01', 50)])
        self.run_plan(['HF4'], tab.forced())
        t = self.follow('HF4')['gdmt_titration_pathway']
        self.assertIn(t['state'], ('labs_needed', 'held'))
        fus = backbone.items(self.ws.dir, patient_id='HF4', kind='followup')['items']
        self.assertTrue({'lab_order', 'titration_hold_review'} & {f['type'] for f in fus})

    def test_monitoring_escalation_holds_the_next_titration_step(self):
        add_measurements(self.db, labs('HF4'))
        cat = tab.forced()
        for p in cat['programs']:
            if p['id'] == 'hf_remote_monitoring_home_labs':
                p['counterparty'] = {**p['counterparty'], 'p_decline': 0, 'p_not_activated': 0, 'p_lab_missed': 0, 'p_lab_out_of_range': 1.0}
        self.run_plan(['HF4'], cat)
        got = self.follow('HF4')
        m, t = got['hf_remote_monitoring_home_labs'], got['gdmt_titration_pathway']
        self.assertEqual(m['state'], 'escalated')
        self.assertIn('escalation', [s['step'] for s in m['action']['steps']])
        esc_date = m['action']['pathway']['escalations'][0]['date']
        held = [c for c in t['action']['pathway']['cycles'] if any(h['measure'] == 'remote_monitoring' for h in c['holds'])]
        self.assertTrue(held and held[0]['date'] >= esc_date)
        self.assertEqual(t['state'], 'held')

    def test_dose_ladder_steps_up_from_the_current_fill(self):
        cat = catmod.load()
        prog = next(p for p in cat['programs'] if p['id'] == 'gdmt_titration_pathway')
        st = pathways.dose_state(prog, {'gap': {'missing_pillars': ['mra', 'sglt2i', 'beta_blocker']}},
                                 {'meds': {'rasi': {'name': 'lisinopril 10 MG Oral Tablet', 'mg': 10.0}}})
        self.assertEqual((st['rasi']['status'], st['rasi']['steps'][st['rasi']['step']]), ('on_board', '10 mg daily'))
        self.assertEqual(pathways._proposal('rasi', st['rasi'])['to'], '20 mg daily')
        top = pathways.dose_state(prog, {'gap': {'missing_pillars': []}}, {'meds': {'sglt2i': {'name': 'dapagliflozin 10 MG', 'mg': 10.0}}})
        self.assertEqual(top['sglt2i']['status'], 'at_target')

    def test_counterparty_pathway_steps_are_deterministic(self):
        prog = next(p for p in catmod.load()['programs'] if p['id'] == 'hf_remote_monitoring_home_labs')
        a = counterparty.SimulatedCounterparty(prog, None, workspace='w', patient_id='P', as_of='2030-06-01')
        b = counterparty.SimulatedCounterparty(prog, None, workspace='w', patient_id='P', as_of='2030-06-01')
        req = {'request_id': 'R1', 'step': 'readings_window', 'day': 30, 'baseline': {'systolic_bp': 120, 'heart_rate': 70},
               'thresholds': prog['thresholds'], 'days': 30}
        ra, rb = a.submit(req), b.submit(dict(req))
        self.assertEqual(ra, rb)
        self.assertIn(ra['outcome'], ('in_range', 'threshold_breach', 'low_adherence'))
        self.assertTrue(a.submit(req)['duplicate_submission'])


class HostedEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data = Path(cls.tmp.name) / 'data'
        cls.server = make_server('127.0.0.1', 0, data, secret_key=SecretBox.generate_key(), cookie_secure=False)
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        auth = AuthStore(data / 'hosted.sqlite3')
        cls.pw = 'correct horse battery staple'
        for org, email in (('Demo Health', 'owner@demo.test'), ('Other Clinic', 'owner@other.test')):
            auth.create_user(email, cls.pw)
            auth.add_member(auth.create_org(org), email, 'owner')
        root = data / 'tenants' / 'demo-health' / 'workspaces'
        root.mkdir(parents=True)
        test_plans._workspace(root, 'pilot')
        add_measurements(root / 'pilot' / 'semantic.sqlite3', labs('HF1') + labs('HF4'))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def client(self, email='owner@demo.test'):
        c = Client(self.base)
        self.assertEqual(c.login(email, self.pw)[0], 200)
        return c

    def html(self, c, path):
        import urllib.request
        req = urllib.request.Request(self.base + path, headers={'Cookie': c.cookie} if c.cookie else {})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.headers, r.read().decode()

    def test_pages_render_with_fonts_allowed_by_csp(self):
        c = self.client()
        for path in ('/app', '/admin', '/account'):
            headers, html = self.html(c, path)
            self.assertIn('#e8693d', html)
            self.assertIn('id="orgbar"', html)
            self.assertIn('https://fonts.gstatic.com', headers['Content-Security-Policy'])
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        headers, html = self.html(Client(self.base), '/login')
        self.assertIn('Sign in to Agere', html)

    def test_plan_work_decisions_and_physician_value(self):
        c = self.client()
        code, _, out = c.call('POST', '/api/workspaces/pilot/plan', {'program_kind': 'pharmacy_gdmt'})
        self.assertEqual(code, 200)
        ex = out['plan']['execution']
        self.assertIn('follow_on', ex)
        code, _, lst = c.call('GET', '/api/workspaces/pilot/work?program_kind=pharmacy_gdmt&limit=5000')
        self.assertEqual(code, 200)
        self.assertTrue({'plan_item', 'follow_on', 'followup'} >= {i['kind'] for i in lst['items']})
        code, _, fus = c.call('GET', '/api/workspaces/pilot/work?program_kind=pharmacy_gdmt&kind=followup&state=open')
        self.assertTrue(fus['items'])
        self.assertEqual({(i['kind'], i['state']) for i in fus['items']}, {('followup', 'open')})  # query filters reach the API
        code, _, dec = c.call('GET', f"/api/workspaces/pilot/decisions/{ex['decision_id']}")
        self.assertEqual(code, 200)
        self.assertEqual(dec['summary']['funnel'], ex['summary']['funnel'])
        code, _, v = c.call('GET', '/api/workspaces/pilot/value')
        self.assertEqual(code, 200)
        self.assertTrue(v['illustrative'])
        self.assertGreater(v['totals']['credits'], 0)
        self.assertTrue(all(cr['receipt_id'] and cr['pays_on'] > cr['earned_on'] for cr in v['credits']))
        npi = v['by_physician'][0]['npi']
        code, _, one = c.call('GET', f'/api/workspaces/pilot/value?npi={npi}')
        self.assertEqual({cr['npi'] for cr in one['credits']}, {npi})
        # another organization cannot see this workspace's value
        self.assertEqual(self.client('owner@other.test').call('GET', '/api/workspaces/pilot/value')[0], 404)
        self.assertEqual(Client(self.base).call('GET', '/api/workspaces/pilot/value')[0], 401)

    def test_deliverables_route_is_feature_detectable(self):
        code, _, _ = self.client().call('GET', '/api/workspaces/pilot/deliverables')
        self.assertIn(code, (200, 404))


class ValueRates(unittest.TestCase):
    def test_rates_are_illustrative_configuration(self):
        r = value.load_rates()
        self.assertTrue(r['illustrative'])
        self.assertEqual([s['order'] for s in r['steps']], [1, 2, 3, 4, 5, 6])
        self.assertEqual(value.pay_date('2030-06-20', r['pay_schedule']), '2030-07-15')
        self.assertEqual(value.pay_date('2030-12-02', r['pay_schedule']), '2031-01-15')


if __name__ == '__main__':
    unittest.main()
