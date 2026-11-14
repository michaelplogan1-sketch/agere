"""Application startup must preserve request ownership and subscription routing."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import serve_current_warehouse as S
from clinical_core.agere.gateway import BudgetLedger, default_ledger_path


class AppStartup(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name).resolve();self.build=self.root/'build';self.build.mkdir()
        # Live startup already requires a pinned materialized semantic policy.
        # Supply that prerequisite so these transport/ownership controls reach
        # their intended boundary; the production integrity guard stays intact.
        from clinical_core.agere.clinical.semantic_layer import policy_identity
        stack=self.build/'stack';stack.mkdir()
        with sqlite3.connect(stack/'semantic.sqlite3') as db:
            db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
            with S.runtime_preflight(S.application_runtime({})['environment']):
                policy=policy_identity()
            db.execute('INSERT INTO meta VALUES (?,?)',('postprocessing_identity',policy))
        self.selected=self.root/'selected.sqlite3';self.other=self.root/'other.sqlite3'
        for p in (self.selected,self.other):
            with contextlib.closing(BudgetLedger(p)) as ledger:ledger.reserve('in-flight','claude','model')
        self.before={p:p.read_bytes() for p in (self.selected,self.other)}
        self.config={'build':str(self.build)}
        self.patchers=[patch.object(S,'validate_config',return_value=self.config),patch.object(S,'serve'),
                      patch('run_integrated_stack.keys'),patch('clinical_core.agere.subscription_transport.install',return_value=True),
                      patch.dict(os.environ,{'AGERE_LEDGER_PATH':str(self.other)},clear=True)]
        self.validate,self.serve,self.keys,self.install,_=[p.start() for p in self.patchers]
        for p in self.patchers:self.addCleanup(p.stop)

    def unchanged(self):
        for p,b in self.before.items():self.assertEqual(p.read_bytes(),b)

    def test_check_does_not_touch_ledgers_load_credentials_or_enable_service(self):
        with contextlib.redirect_stdout(io.StringIO()):
            S.main(['--check','--allow-live-stack','--ledger',str(self.selected)])
        self.keys.assert_not_called();self.install.assert_not_called();self.serve.assert_not_called()
        self.assertFalse((self.build/'.agere-release.lock').exists());self.unchanged()
        self.assertEqual(default_ledger_path(),self.other)

    def test_live_start_binds_selected_ledger_before_transport_and_preserves_pending(self):
        def key_load():
            self.assertEqual(default_ledger_path(),self.selected)
            self.assertEqual(os.environ['AGERE_CLAUDE_TRANSPORT'],'subscription')
        self.keys.side_effect=key_load
        S.main(['--allow-live-stack','--ledger',str(self.selected)])
        self.keys.assert_called_once();self.install.assert_called_once()
        self.serve.assert_called_once();self.assertTrue(self.serve.call_args.kwargs['allow_live_stack']);self.unchanged()

    def test_external_transport_is_rejected_before_credentials_or_service(self):
        os.environ['AGERE_CLAUDE_TRANSPORT']='api'
        with self.assertRaisesRegex(ValueError,'subscription transport'):S.main(['--allow-live-stack'])
        self.keys.assert_not_called();self.install.assert_not_called();self.serve.assert_not_called();self.unchanged()

    def test_failed_transport_never_enables_service(self):
        self.install.return_value=False
        with self.assertRaisesRegex(ValueError,'did not initialize'):S.main(['--allow-live-stack'])
        self.serve.assert_not_called();self.unchanged()

    def test_inspection_does_not_load_credentials_or_install_a_transport(self):
        S.main([])
        self.keys.assert_not_called();self.install.assert_not_called();self.unchanged()
        self.assertFalse(self.serve.call_args.kwargs['allow_live_stack'])

    def test_recovery_cannot_enable_inference(self):
        self.config['recovery']={'mode':'inspection_only'}
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):S.main(['--allow-live-stack'])
        self.keys.assert_not_called();self.install.assert_not_called();self.serve.assert_not_called();self.unchanged()

    def test_normal_entry_uses_economical_roles_despite_stale_inherited_experiments(self):
        os.environ.update(AGERE_LOCAL_TYPED='on', AGERE_BOTTLED='on', AGERE_DOCUMENT_LOCAL_READERS='kev,d1')
        resource=str(self.root/'qualified-students')
        self.config['runtime']={'environment':{'AGERE_SYSTEM_ONE_STUDENTS':resource}}
        def ready(*args, **kwargs):
            self.assertEqual(os.environ['AGERE_RUNTIME_PROFILE'],'economical')
            self.assertEqual(os.environ['AGERE_LOCAL_TYPED'],'off')
            self.assertEqual(os.environ['AGERE_BOTTLED'],'off')
            self.assertEqual(os.environ['AGERE_DOCUMENT_LOCAL_READERS'],'')
            self.assertEqual(os.environ['AGERE_SYSTEM_ONE_STUDENT'],'on')
            self.assertEqual(os.environ['AGERE_SYSTEM_ONE_STUDENTS'],resource)
        self.serve.side_effect=ready
        S.main(['--allow-live-stack','--ledger',str(self.selected)])
        self.serve.assert_called_once();self.unchanged()

    def test_configured_profile_preserves_explicit_optional_role_selection(self):
        os.environ['AGERE_LOCAL_TYPED']='on'
        self.config['runtime']={'environment':{'AGERE_DOCUMENT_LOCAL_READERS':'kev,d1'}}
        S.main(['--runtime-profile','configured'])
        self.assertEqual(os.environ['AGERE_RUNTIME_PROFILE'],'configured')
        self.assertEqual(os.environ['AGERE_LOCAL_TYPED'],'on')
        self.assertEqual(os.environ['AGERE_DOCUMENT_LOCAL_READERS'],'kev,d1')
        self.keys.assert_not_called();self.install.assert_not_called();self.unchanged()

    def test_check_reports_profile_without_mutating_environment(self):
        self.config['runtime']={'environment':{'AGERE_LOCAL_TYPED':'on'}}
        before=dict(os.environ);text=io.StringIO()
        with contextlib.redirect_stdout(text):S.main(['--check'])
        self.assertEqual(dict(os.environ),before)
        self.assertEqual(json.loads(text.getvalue())['runtime']['environment']['AGERE_LOCAL_TYPED'],'off')
        self.serve.assert_not_called();self.keys.assert_not_called();self.unchanged()

    def test_recovery_inspection_does_not_apply_live_runtime_settings(self):
        self.config['recovery']={'mode':'inspection_only'}
        self.config['runtime']={'environment':{'AGERE_SYSTEM_ONE_STUDENTS':str(self.root/'different')}}
        before=dict(os.environ)
        S.main([])
        self.assertEqual(dict(os.environ),before)
        self.keys.assert_not_called();self.install.assert_not_called();self.unchanged()

    def test_invalid_runtime_configuration_stops_before_credentials_or_service(self):
        for runtime in [{'environment':{'ANTHROPIC_API_KEY':'must-not-be-accepted'}},
                        {'profile':'invented'}, {'environment':{'AGERE_LOCAL_TYPED':True}},
                        {'environment':{'AGERE_LOCAL_TYPED':'on\x00off'}}]:
            with self.subTest(runtime=runtime):
                self.config['runtime']=runtime;before=dict(os.environ)
                with self.assertRaises(ValueError):S.main(['--allow-live-stack'])
                self.assertEqual(dict(os.environ),before)
                self.keys.assert_not_called();self.install.assert_not_called();self.serve.assert_not_called();self.unchanged()

    def test_failed_semantic_preflight_restores_environment_and_keeps_guard(self):
        os.environ['AGERE_NOTE_NER']='on';before=dict(os.environ)
        with sqlite3.connect(self.build/'stack/semantic.sqlite3') as db:
            db.execute("UPDATE meta SET value='changed' WHERE key='postprocessing_identity'")
        with self.assertRaisesRegex(ValueError,'Semantic enrichment policy differs'):
            S.main(['--check','--allow-live-stack'])
        self.assertEqual(dict(os.environ),before)
        self.keys.assert_not_called();self.install.assert_not_called();self.serve.assert_not_called();self.unchanged()


if __name__=='__main__':unittest.main()
