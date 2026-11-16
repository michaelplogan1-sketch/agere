"""Warehouse v2.8 decisions: supersession semantics in the snapshot (parent hidden, every child current, no multi-child
quarantine, cutoff-aware), per-stream reconciliation, the raw-source current-count check of the general verifier, and
the explicit --rebaseline activation mode (prior-release diff recorded, not gated). Tiny synthetic fixtures only."""
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.warehouse import Warehouse
from clinical_core.agere.warehouse_activation import REBASELINE_BASIS, activate, validate_migration, validate_rebaseline
from verify_healthcare_warehouse_stack import current_source_counts
from test_warehouse_retraction import LEGIT, PRIOR, releases
import inventory_received_warehouse


def record(rid, **kw):
    return {'record_id': rid, 'patient_id': 'p', 'source_table': 'payer_medical_claims', 'event_date': '2026-09-01',
            'available_date': '2026-09-02', 'synthetic': True, 'identity_status': 'matched', **kw}


class Supersession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name); self.w = Warehouse(self.root / 'db')

    def tearDown(self): self.w.close(); self.tmp.cleanup()

    def load(self, rows):
        observed = self.root / 'observed'; observed.mkdir(exist_ok=True)
        (observed / 'patients.jsonl').write_text(json.dumps({'patient_id': 'p', 'synthetic': True}) + '\n' +
                                                 json.dumps({'patient_id': 'q', 'synthetic': True}) + '\n')
        (observed / 'notes.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        spec = {'synthetic': True, 'as_of': '2026-09-30', 'files': {}}
        for f in observed.iterdir(): spec['files']['observed/' + f.name] = {'sha256': hashlib.sha256(f.read_bytes()).hexdigest()}
        (self.root / 'manifest.json').write_text(json.dumps(spec))
        return self.w.ingest(self.root)[0]

    def current(self, rows, cutoff='2026-09-30'):
        patients, meta = self.w.snapshot(self.load(rows), cutoff)
        return sorted(r['record_id'] for p in patients.values() for r in p['records']), meta

    def test_one_child_hides_parent(self):
        ids, meta = self.current([record('a'), record('b', supersedes_record_id='a')])
        self.assertEqual(ids, ['b'])
        self.assertEqual((meta['excluded'], meta['superseded_by_stream']), ({'superseded': 1}, {'notes': 1}))

    def test_void_and_replacement_both_current(self):
        ids, meta = self.current([record('orig'), record('void', supersedes_record_id='orig', claim_frequency_code='8'),
                                  record('repl', supersedes_record_id='orig', claim_frequency_code='7')])
        self.assertEqual(ids, ['repl', 'void'])
        self.assertNotIn('correction_conflict', meta['excluded'])

    def test_chain_keeps_only_the_last(self):
        ids, meta = self.current([record('A'), record('B', supersedes_record_id='A'), record('C', supersedes_record_id='B')])
        self.assertEqual((ids, meta['excluded']), (['C'], {'superseded': 2}))

    def test_child_after_cutoff_leaves_parent_current(self):
        rows = [record('a'), record('b', supersedes_record_id='a', available_date='2026-10-05')]
        ids, meta = self.current(rows)
        self.assertEqual((ids, meta['excluded'], meta['not_visible_by_stream']), (['a'], {'late_arrival': 1}, {'notes': 1}))
        self.assertEqual(self.w.snapshot(self.w.ingest(self.root)[0], '2026-10-06')[0]['p']['records'][0]['record_id'], 'b')

    def test_parent_delivered_after_child_is_still_superseded(self):
        ids, _ = self.current([record('a', available_date='2026-09-10'), record('b', supersedes_record_id='a', available_date='2026-09-05')])
        self.assertEqual(ids, ['b'])

    def test_future_rows_not_visible_and_reconciled(self):
        ids, meta = self.current([record('a'), record('d', event_date='2026-10-12', available_date='2026-09-30')])
        self.assertEqual((ids, meta['excluded'], meta['not_visible_by_stream']), (['a'], {'future': 1}, {'notes': 1}))

    def test_cycles_and_cross_patient_links_still_quarantined(self):
        ids, meta = self.current([record('x', supersedes_record_id='y'), record('y', supersedes_record_id='x'), record('k')])
        self.assertEqual((ids, meta['excluded']['correction_conflict']), (['k'], 2))
        ids, meta = self.current([record('a'), record('b', patient_id='q', supersedes_record_id='a')])
        self.assertEqual((ids, meta['correction_conflict_by_stream']), (['a'], {'notes': 1}))

    def test_no_reconciliation_keys_without_supersession(self):
        _, meta = self.current([record('a'), record('b')])
        self.assertFalse({'superseded_by_stream', 'not_visible_by_stream', 'correction_conflict_by_stream'} & set(meta))


class RawCurrentCounts(unittest.TestCase):
    def test_general_verifier_counts_current_rows_from_raw_source(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE source_records(record_id, patient_id, source_table, event_date, available_date, supersedes_record_id)')
        db.executemany('INSERT INTO source_records VALUES (?,?,?,?,?,?)', [
            ('o', 'p', 'claims', '2026-01-01', '2026-01-02', None), ('v', 'p', 'claims', '2026-01-05', '2026-01-06', 'o'),
            ('r', 'p', 'claims', '2026-01-05', '2026-01-06', 'o'), ('late', 'p', 'claims', '2026-01-05', '2026-12-01', 'v'),
            ('fut', 'p', 'death_records', '2026-08-01', '2026-03-01', None), ('x', 'q', 'claims', '2026-01-05', '2026-01-06', 'o')])
        self.assertEqual(current_source_counts(db, '2026-06-30'), {'claims': 2})   # v, r current; o superseded; x quarantined
        db2 = sqlite3.connect(':memory:'); db2.execute('CREATE TABLE source_records(record_id, patient_id, source_table, event_date, available_date)')
        db2.execute("INSERT INTO source_records VALUES ('a','p','labs','2026-01-01','2026-01-01')")
        self.assertEqual(current_source_counts(db2, '2026-06-30'), {'labs': 1})


def rebaseline_fixture():
    connection = {'records': 100, 'patients': 12, 'snapshot': {'as_of': '2030-05-27', 'source_dataset': {
        'original_patient_id_digest': 'new', 'source_sha256': 'sha'}}}
    prior = {'snapshot': {'as_of': '2030-05-27', 'source_dataset': {'original_patient_id_digest': 'old'}}}
    audit = {'kind': 'expansion', 'prior_release': 'v2.3', 'new_received_rows': 100, 'patient_id_digest': 'new',
             'manifest_source_sha256': 'sha', 'prior_received_rows': 90, 'prior_rows_missing': 40, 'prior_rows_changed': 900,
             'prior_patients_missing': 3, 'prior_rows_reassigned': 800, 'retraction': True, 'prior_rows_unexplained': 30,
             'unexplained_by_reason': {'no_death_in_new_release': 30}, 'prior_rows_retracted': 10}
    checks = {'synthetic': True, 'as_of': '2030-05-27', 'semantic_layer': 'semantic-layer/15'}
    return audit, connection, prior, checks


class Rebaseline(unittest.TestCase):
    def test_diff_recorded_not_gated(self):
        audit, connection, prior, checks = rebaseline_fixture()
        for kw in ({}, {'expansion': True, 'restatement': True, 'retraction': True}):
            with self.assertRaises(ValueError): validate_migration(audit, connection, prior, **kw)
        diff = validate_rebaseline(audit, connection, prior, checks)
        self.assertEqual((diff['gated'], diff['prior_patients_missing'], diff['prior_rows_unexplained'], diff['prior_rows_reassigned']),
                         (False, 3, 30, 800))
        self.assertNotIn('manifest_source_sha256', diff)

    def test_audit_and_client_checks_still_required(self):
        audit, connection, prior, checks = rebaseline_fixture()
        with self.assertRaisesRegex(ValueError, 'informational diff'):
            validate_rebaseline({k: v for k, v in audit.items() if k != 'prior_release'}, connection, prior, checks)
        with self.assertRaisesRegex(ValueError, 'this build'):
            validate_rebaseline({**audit, 'manifest_source_sha256': 'other'}, connection, prior, checks)
        for bad in (None, {**checks, 'as_of': '2029-01-01'}, {**checks, 'synthetic': False}):
            with self.assertRaisesRegex(ValueError, 'client-data checks'): validate_rebaseline(audit, connection, prior, bad)
        later = {'snapshot': {'as_of': '2031-01-01', 'source_dataset': {}}}
        with self.assertRaisesRegex(ValueError, 'roll back'): validate_rebaseline(audit, connection, later, checks)

    def test_rebaseline_is_explicit_and_exclusive(self):
        for kw in ({'restatement': True}, {'expansion': True}, {'retraction': True}):
            with self.assertRaisesRegex(ValueError, 'cannot be combined'):
                activate('/nonexistent', '/nonexistent/config.json', rebaseline=True, **kw)
        self.assertTrue(REBASELINE_BASIS.startswith('Rebaselined release (fresh load; prior-release diff recorded, not gated)'))

    def test_inventory_rebaseline_records_unexplained_and_absent_without_failing(self):
        rows = [r for r in LEGIT if r['patient_id'] != 'P-live']   # P-live absent, b1 missing
        d, old, new, _ = releases(rows)
        db = sqlite3.connect(new / 'warehouse.sqlite'); db.execute("DELETE FROM patients WHERE patient_id='P-live'"); db.commit(); db.close()
        m = json.loads((new / 'manifest.json').read_text()); m['files']['warehouse.sqlite'] = hashlib.sha256((new / 'warehouse.sqlite').read_bytes()).hexdigest()
        (new / 'manifest.json').write_text(json.dumps(m))
        args = ['inventory', '--release', str(new), '--previous', str(old), '--output', str(d / 'inv')]
        with mock.patch.object(sys, 'argv', args), mock.patch('builtins.print'):
            with self.assertRaisesRegex(AssertionError, 'roster changed'): inventory_received_warehouse.main()
        with mock.patch.object(sys, 'argv', args[:-1] + [str(d / 'inv2'), '--rebaseline']), mock.patch('builtins.print'):
            inventory_received_warehouse.main()
        inv = json.loads((d / 'inv2' / 'inventory.json').read_text())
        self.assertEqual((inv['rebaseline'], inv['migration_gated'], inv['prior_patients_missing'], inv['prior_rows_missing'],
                          inv['prior_rows_retracted'], inv['unexplained_by_reason']),
                         (True, False, 1, 4, 3, {'patient_absent_from_new_release': 1}))
        with mock.patch.object(sys, 'argv', args[:-1] + [str(d / 'inv3'), '--rebaseline', '--expansion']), \
             mock.patch('builtins.print'), mock.patch('sys.stderr'):
            with self.assertRaises(SystemExit): inventory_received_warehouse.main()


if __name__ == '__main__':
    unittest.main()
