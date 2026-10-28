"""Application startup must preserve request ownership and subscription routing."""
import contextlib
import io
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


if __name__=='__main__':unittest.main()
