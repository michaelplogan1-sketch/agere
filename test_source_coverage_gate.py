"""Source coverage gate: each received stream is mapped, read by an engine, declared unused after review, or listed as
none; ingestion adapters and question libraries do not count as readers; informational until enforced; activation hook."""
import contextlib, io, json, tempfile, unittest
from pathlib import Path

from clinical_core.agere import source_coverage as SC
from clinical_core.agere import warehouse_activation as WA


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


class SourceCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        repo = self.repo = self.root / 'repo'
        write(repo, 'clinical_core/agere/clinical/semantic_layer.py', "STREAMS = ('labs', 'mood_questionnaires')\n")
        write(repo, 'clinical_core/agere/clinical/risk_engine.py', "rows = by_stream['pharmacy_claims']\n")
        write(repo, 'clinical_core/agere/healthcare_warehouse_v4.py', "TABLES = ['wellness_visit_services', 'pharmacy_payments']\n")
        write(repo, 'config/agere/healthcare_questions.json', json.dumps({'q': "count rows in 'wellness_visit_services'"}))
        write(repo, 'config/agere/metrics/m.json', json.dumps({'sql_template': "SELECT 1 FROM record WHERE stream = 'appointments'"}))
        self.config = repo / 'config/agere/unmapped_streams.json'
        write(repo, 'config/agere/unmapped_streams.json', json.dumps({'enforce': False, 'streams': {
            'vision_evaluations': {'reason': 'no product use yet', 'reviewer': 'reviewer (fixture)', 'reviewed_on': '2026-10-14'},
            'dental_evaluations': {'reason': 'incomplete entry'}}}))
        stack = self.build = self.root / 'build'
        streams = {s: {'records': n, 'fields': {'patient_id': {'distinct': n // 2}}} for s, n in
                   [('labs', 100), ('mood_questionnaires', 20), ('pharmacy_claims', 30), ('appointments', 40),
                    ('wellness_visit_services', 866), ('pharmacy_payments', 50), ('vision_evaluations', 5), ('dental_evaluations', 7)]}
        write(stack, 'stack/run.json', json.dumps({'source_digest': 'd'}))
        write(stack, 'stack/source-profile.json', json.dumps({'source_digest': 'd', 'patients': {'records': 10}, 'streams': streams}))

    def tearDown(self): self.tmp.cleanup()

    def check(self, **kw): return SC.check(self.build, config=self.config, root=self.repo, **kw)

    def test_states(self):
        r = self.check()
        st = {s: v['state'] for s, v in r['streams'].items()}
        self.assertEqual(st, {'labs': 'mapped', 'mood_questionnaires': 'mapped', 'appointments': 'mapped', 'pharmacy_claims': 'engine',
                              'vision_evaluations': 'declared', 'wellness_visit_services': 'none', 'pharmacy_payments': 'none',
                              'dental_evaluations': 'none'})
        self.assertEqual(r['streams']['pharmacy_claims']['read_by'], ['clinical_core/agere/clinical/risk_engine.py'])
        self.assertEqual([x['stream'] for x in r['unread']], ['wellness_visit_services', 'pharmacy_payments', 'dental_evaluations'])
        self.assertEqual((r['unread'][0]['records'], r['unread'][0]['patients'], r['unread_records']), (866, 433, 923))
        self.assertEqual(r['invalid_declarations'], ['dental_evaluations'])
        self.assertEqual([x['stream'] for x in r['engine_only_not_in_semantic_layer']], ['pharmacy_claims'])
        self.assertTrue(r['passed'])  # informational: enforce is false

    def test_enforced_gate_lists_unread_streams(self):
        with self.assertRaises(ValueError) as cm:
            SC.gate(self.build, config=self.config, root=self.repo, enforce=True)
        self.assertIn('wellness_visit_services: 866 rows, 433 patients', str(cm.exception))
        self.assertIn('activation blocked', str(cm.exception))
        data = json.loads(self.config.read_text()); data['enforce'] = True
        for s in ('wellness_visit_services', 'pharmacy_payments', 'dental_evaluations'):
            data['streams'][s] = {'reason': 'reviewed', 'reviewer': 'reviewer (fixture)', 'reviewed_on': '2026-10-14'}
        self.config.write_text(json.dumps(data))
        self.assertTrue(SC.gate(self.build, config=self.config, root=self.repo)['passed'])

    def test_activation_hook_and_cli(self):
        orig = SC.CONFIG, SC.ROOT
        SC.check.__kwdefaults__ = {**SC.check.__kwdefaults__, 'config': self.config, 'root': self.repo}
        try:
            info = WA.source_coverage_check(self.build)
            self.assertEqual((info['passed'], len(info['unread'])), (True, 3))
            with self.assertRaisesRegex(ValueError, 'wellness_visit_services'):
                WA.source_coverage_check(self.build, enforce=True)
            self.assertEqual(WA.source_coverage_check(self.root / 'missing')['status'], 'unavailable')
            from tools import source_coverage_gate as cli
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--build', str(self.build), '--config', str(self.config), '--out', str(self.root / 'o')]), 0)
                self.assertEqual(cli.main(['--build', str(self.build), '--config', str(self.config), '--out', str(self.root / 'o'), '--gate']), 2)
            self.assertEqual(json.loads((self.root / 'o' / 'source-coverage.json').read_text())['counts']['none'], 3)
        finally:
            SC.check.__kwdefaults__ = {**SC.check.__kwdefaults__, 'config': orig[0], 'root': orig[1]}


if __name__ == '__main__':
    unittest.main()
