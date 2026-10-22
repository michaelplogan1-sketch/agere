"""Fault containment: injected failure sequences (tools/fault_injection.py) must all be contained; unit checks for the heal / DataAbsent paths."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
import fault_injection as F  # noqa: E402
from clinical_core.agere import fault_containment as FC  # noqa: E402


class Scenarios(unittest.TestCase):
    def test_every_scripted_scenario_is_contained(self):
        for sc in F.SCENARIOS:
            r = F.run_scenario(sc)
            self.assertEqual(r['violations'], [], sc['name'])

    def test_the_harness_detects_a_missing_heal(self):
        original = FC.on_tier_error
        FC.on_tier_error = lambda path, exc: []  # inject a regression: no healing
        try:
            r = F.run_scenario(F.SCENARIOS[3])
        finally:
            FC.on_tier_error = original
        self.assertTrue(any('quarantined' in v for v in r['violations']))

    def test_real_probes(self):
        self.assertEqual(F.probe_corrupt_file()['violations'], [])
        self.assertEqual(F.probe_missing_tables()['violations'], [])


class Units(unittest.TestCase):
    def test_heal_keeps_evidence_and_leaves_healthy_stores(self):
        with tempfile.TemporaryDirectory() as d:
            ok = sqlite3.connect(Path(d) / 'answers.sqlite3'); ok.execute('CREATE TABLE a(x)'); ok.execute('INSERT INTO a VALUES(1)'); ok.commit(); ok.close()
            (Path(d) / 'learning.sqlite3').write_bytes(b'x' * 4096)
            (Path(d) / 'semantic.sqlite3').write_bytes(b'x' * 4096)  # the semantic layer is never moved
            heals = FC.heal_state_stores(d, now=0)
            self.assertEqual([h['store'] for h in heals], ['learning.sqlite3'])
            self.assertTrue((Path(d) / 'learning.sqlite3.corrupt-19700101T000000').exists())
            self.assertTrue((Path(d) / 'answers.sqlite3').exists()); self.assertTrue((Path(d) / 'semantic.sqlite3').exists())
            self.assertEqual(sqlite3.connect(Path(d) / 'answers.sqlite3').execute('SELECT x FROM a').fetchone(), (1,))

    def test_operational_errors_that_are_not_corruption_do_not_trigger_healing(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'learning.sqlite3').write_bytes(b'x' * 4096)
            self.assertEqual(FC.on_tier_error(d, sqlite3.OperationalError('database is locked')), [])
            self.assertTrue((Path(d) / 'learning.sqlite3').exists())

    def test_contain_engine_names_the_absent_source(self):
        class E:
            @staticmethod
            def execute(*a): raise sqlite3.OperationalError('no such table: member_month')
        with self.assertRaises(FC.DataAbsent) as ctx: FC.contain_engine(E, '.', None, {}, 'x')
        self.assertEqual(ctx.exception.sources, ['member_month']); self.assertIsInstance(ctx.exception, ValueError)

        class Other:
            @staticmethod
            def execute(*a): raise sqlite3.OperationalError('database is locked')
        with self.assertRaises(sqlite3.OperationalError): FC.contain_engine(Other, '.', None, {}, 'x')


if __name__ == '__main__':
    unittest.main()
