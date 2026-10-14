"""Retraction mode (warehouse v2.6+ drops deep-chart records dated after a death): the declared post-death rule, the
--retraction inventory and migration audit, and the activation gate. Tiny synthetic fixtures only."""
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere import warehouse_retraction as R
from clinical_core.agere.warehouse_activation import validate_migration

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
from restatement_audit import audit  # noqa: E402
import inventory_received_warehouse  # noqa: E402

COLUMNS = ('record_id', 'patient_id', 'source_table', 'source_system', 'organization_id', 'event_date', 'recorded_date',
           'available_date', 'encounter_id', 'facts', 'source_patient_id', 'event_date_precision', 'event_time',
           'recorded_time', 'signed_time', 'available_time', 'synthetic')


def row(rid, pid, table, event_date, **facts):
    return {'record_id': rid, 'patient_id': pid, 'source_table': table, 'event_date': event_date, 'available_date': event_date,
            'facts': json.dumps(facts), 'synthetic': 1}


# P-dead dies 2029-03-10 (death record, 1-3 months late, plus the coverage termination at month end).
# P-term has only an enrollment span terminated for death (coverage_end 2029-05-31 is the upper bound).
PRIOR = [row('a1', 'P-dead', 'labs', '2029-01-05', name='A1c', value=7.1),
         row('a2', 'P-dead', 'labs', '2029-04-02', name='A1c', value=7.4),                   # after death: retractable
         row('a3', 'P-dead', 'appointments', '2029-06-01', status='booked'),               # after death: retractable
         row('a4', 'P-dead', 'medical_claims', '2029-03-20', service_date='2029-03-08'),   # billed after, served before death
         row('a5', 'P-dead', 'notes', '2029-03-10', note_type='pronouncement'),            # on the date of death
         row('b1', 'P-live', 'labs', '2029-02-01', name='LDL', value=99),
         row('c1', 'P-term', 'encounters', '2029-06-15', encounter_type='office'),          # after the termination bound
         row('c2', 'P-term', 'encounters', '2029-05-20', encounter_type='office')]          # before the bound
DEATHS = [row('d1', 'P-dead', 'death_records', '2029-03-10', date_of_death='2029-03-10', source='state_death_index'),
          row('d2', 'P-dead', 'enrollment_spans', '2029-01-01', coverage_end='2029-03-31', termination_reason='death'),
          row('d3', 'P-dead', 'demographic_updates', '2029-04-01', update_type='deceased', deceased=True, date_of_death='2029-03-10'),
          row('d4', 'P-term', 'enrollment_spans', '2029-01-01', coverage_end='2029-05-31', termination_reason='death')]


def write_release(root, rows, patients=('P-dead', 'P-live', 'P-term'), supersedes=False):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'warehouse.sqlite')
    db.execute('CREATE TABLE patients(patient_id TEXT PRIMARY KEY, data_segment TEXT)')
    db.executemany('INSERT INTO patients VALUES (?,?)', [(p, 'deep_chart') for p in patients])
    cols = COLUMNS + (('supersedes_record_id',) if supersedes else ())
    db.execute('CREATE TABLE source_records(' + ','.join(c + (' INTEGER' if c == 'synthetic' else ' TEXT') for c in cols) + ')')
    db.executemany('INSERT INTO source_records VALUES (' + ','.join('?' * len(cols)) + ')', [[r.get(c) for c in cols] for r in rows])
    db.commit(); db.close()
    sha = hashlib.sha256((root / 'warehouse.sqlite').read_bytes()).hexdigest()
    (root / 'manifest.json').write_text(json.dumps({'as_of': '2030-05-27', 'files': {'warehouse.sqlite': sha}}))
    (root / 'private').mkdir(exist_ok=True); (root / 'private' / 'truth.json').write_text('NOT JSON: never opened')
    return root


def releases(new_rows, **kw):
    d = Path(tempfile.mkdtemp())
    old = write_release(d / 'old', PRIOR + DEATHS[:2] + DEATHS[3:])
    new = write_release(d / 'new', new_rows, **kw)
    build = d / 'build'; build.mkdir()
    (build / 'connection.json').write_text(json.dumps({'release': 'new', 'records': len(new_rows), 'snapshot': {
        'as_of': '2030-05-27', 'source_dataset': {'source': str(new / 'warehouse.sqlite'), 'original_patient_id_digest': 'r',
                                                  'source_sha256': 's'}}}))
    return d, old, new, build


LEGIT = [r for r in PRIOR if r['record_id'] not in ('a2', 'a3', 'c1')] + DEATHS   # only post-death rows dropped


class PostDeathRule(unittest.TestCase):
    def deaths(self):
        _, _, new, _ = releases(LEGIT)
        db = sqlite3.connect(new / 'warehouse.sqlite')
        try: return R.death_dates(db)
        finally: db.close()

    def test_death_dates_prefer_reported_date_over_coverage_end(self):
        self.assertEqual(self.deaths(), {'P-dead': ('2029-03-10', 'death_records'),
                                         'P-term': ('2029-05-31', 'enrollment_spans.coverage_end')})

    def test_classify_explains_only_post_death_rows(self):
        missing = [(r['record_id'], r['patient_id'], r['source_table'], r['event_date'], r['facts']) for r in PRIOR
                   if r['record_id'] != 'b1'] + [('x1', 'P-gone', 'labs', '2029-01-01', '{}'), ('x2', 'P-dead', 'notes', None, '{}')]
        out = R.classify(missing, self.deaths(), {'P-dead', 'P-live', 'P-term'})
        self.assertEqual(out['retracted_by_table'], {'labs': 1, 'appointments': 1, 'encounters': 1})
        self.assertEqual((out['prior_rows_retracted'], out['retracted_patients']), (3, 2))
        self.assertEqual(out['unexplained_by_reason'], {'dated_on_or_before_death': 3, 'service_on_or_before_death': 1,
                                                        'patient_absent_from_new_release': 1, 'undated': 1})
        reasons = {e['record_id']: e['reason'] for e in out['unexplained_examples']}
        self.assertEqual(reasons['a4'], 'service_on_or_before_death')   # bill dated after death for care before it
        self.assertEqual(reasons['a5'], 'dated_on_or_before_death')     # the day of death itself is kept
        self.assertEqual(reasons['c2'], 'dated_on_or_before_death')     # before the coverage-end upper bound
        out = R.classify([('b1', 'P-live', 'labs', '2029-02-01', '{}')], self.deaths(), {'P-live'})
        self.assertEqual(out['unexplained_by_reason'], {'no_death_in_new_release': 1})


class RetractionAuditAndInventory(unittest.TestCase):
    def test_audit_counts_retracted_rows_by_stream(self):
        _, old, _, build = releases(LEGIT, supersedes=True)
        a = audit(build, old, retraction=True)
        self.assertEqual((a['retraction'], a['retraction_rule'], a['prior_rows_missing'], a['prior_rows_retracted'], a['prior_rows_unexplained']),
                         (True, R.RULE, 3, 3, 0))
        self.assertEqual(a['retracted_by_table'], {'labs': 1, 'appointments': 1, 'encounters': 1})
        self.assertEqual((a['prior_rows_changed'], a['prior_rows_reassigned']), (0, 0))
        self.assertFalse(audit(build, old)['retraction'])

    def test_audit_lists_unexplained_rows_and_record_id_reuse(self):
        rows = [r for r in LEGIT if r['record_id'] != 'a4'] + [row('b1', 'P-dead', 'vitals', '2029-01-02', bp='120/80')]
        rows = [r for r in rows if r['record_id'] != 'b1' or r['patient_id'] == 'P-dead']
        _, old, _, build = releases(rows)
        a = audit(build, old, retraction=True)
        self.assertEqual((a['prior_rows_missing'], a['prior_rows_retracted'], a['prior_rows_unexplained']), (4, 3, 1))
        self.assertEqual(a['unexplained_examples'][0]['record_id'], 'a4')
        self.assertEqual((a['prior_rows_reassigned'], a['reassigned_by_table']), (1, {'labs': 1}))

    def run_inventory(self, rows, *flags):
        d, old, new, _ = releases(rows, supersedes=True)
        out = d / 'inventory'
        with mock.patch.object(sys, 'argv', ['inventory', '--release', str(new), '--previous', str(old), '--output', str(out), *flags]), \
             mock.patch('builtins.print'):
            inventory_received_warehouse.main()
        return json.loads((out / 'inventory.json').read_text())

    def test_inventory_accepts_explained_retraction_and_new_column(self):
        inv = self.run_inventory(LEGIT, '--retraction')
        self.assertEqual((inv['prior_rows_missing'], inv['prior_rows_retracted'], inv['prior_rows_unexplained']), (3, 3, 0))
        self.assertEqual(inv['new_source_record_columns'], ['supersedes_record_id'])
        self.assertEqual(inv['new_source_categories'], ['demographic_updates'])
        with self.assertRaisesRegex(AssertionError, 'use --retraction'): self.run_inventory(LEGIT)
        with self.assertRaisesRegex(AssertionError, 'not explained'):
            self.run_inventory([r for r in LEGIT if r['record_id'] != 'b1'], '--retraction')


def gate_fixture(**over):
    connection = {'records': 100, 'patients': 10, 'snapshot': {'as_of': '2030-05-27', 'source_dataset': {
        'original_patient_id_digest': 'r', 'source_sha256': 'sha'}}}
    prior = {'snapshot': {'as_of': '2030-05-27', 'source_dataset': {'original_patient_id_digest': 'r'}}}
    audit_ = {'kind': 'restatement', 'identical_roster': True, 'new_received_rows': 100, 'patient_id_digest': 'r',
              'manifest_source_sha256': 'sha', 'prior_rows_missing': 6, 'prior_rows_changed': 0, 'changed_by_table': {},
              'prior_rows_missing_or_changed': 6, 'retraction': True, 'retraction_rule': R.RULE, 'prior_rows_retracted': 6,
              'retracted_by_table': {'labs': 4, 'appointments': 2}, 'prior_rows_unexplained': 0}
    audit_.update(over)
    return audit_, connection, prior


class RetractionGate(unittest.TestCase):
    def test_explained_retraction_accepted_only_with_flag(self):
        audit_, connection, prior = gate_fixture()
        self.assertEqual(validate_migration(audit_, connection, prior, retraction=True), 'identical_retracted')
        with self.assertRaisesRegex(ValueError, '--retraction'): validate_migration(audit_, connection, prior)
        with self.assertRaisesRegex(ValueError, '--retraction'): validate_migration(audit_, connection, prior, restatement=True)

    def test_unexplained_or_uncounted_rows_rejected(self):
        audit_, connection, prior = gate_fixture(prior_rows_unexplained=1, prior_rows_retracted=5, retracted_by_table={'labs': 5})
        with self.assertRaisesRegex(ValueError, 'unexplained'): validate_migration(audit_, connection, prior, retraction=True)
        for over in ({'retracted_by_table': {'labs': 4}}, {'prior_rows_retracted': 5}, {'prior_rows_retracted': None},
                     {'retraction': False}, {'retraction_rule': 'anything-goes'}):
            with self.assertRaises(ValueError, msg=over): validate_migration(gate_fixture(**over)[0], connection, prior, retraction=True)
        # a plain restatement audit (no retraction block) cannot back --retraction
        plain = {k: v for k, v in audit_.items() if not k.startswith(('retract', 'prior_rows_unexplained'))}
        with self.assertRaisesRegex(ValueError, 'retraction migration audit'):
            validate_migration(plain, connection, prior, retraction=True)

    def test_retraction_composes_with_restatement_and_expansion(self):
        audit_, connection, prior = gate_fixture(prior_rows_changed=3, changed_by_table={'labs': 3}, prior_rows_missing_or_changed=9)
        with self.assertRaisesRegex(ValueError, 'original-cohort'): validate_migration(audit_, connection, prior, retraction=True)
        self.assertEqual(validate_migration(audit_, connection, prior, retraction=True, restatement=True), 'restated_retracted')
        expanded = {**audit_, 'kind': 'expansion', 'identical_roster': False, 'prior_patients': 8, 'new_patients': 2, 'patients': 10,
                    'prior_patients_missing': 0, 'prior_patient_id_digest': 'r'}
        with self.assertRaisesRegex(ValueError, '--retraction'): validate_migration(expanded, connection, prior, expansion=True, restatement=True)
        with self.assertRaisesRegex(ValueError, 'drop prior rows'):
            validate_migration({**expanded, 'retraction': False}, connection, prior, expansion=True, restatement=True)
        self.assertEqual(validate_migration(expanded, connection, prior, expansion=True, restatement=True, retraction=True),
                         'expanded_restated_retracted')
        with self.assertRaisesRegex(ValueError, 'drop prior patients'):
            validate_migration({**expanded, 'prior_patients_missing': 1}, connection, prior, expansion=True, restatement=True, retraction=True)

    def test_nothing_retracted_keeps_the_base_kind(self):
        audit_, connection, prior = gate_fixture(prior_rows_missing=0, prior_rows_retracted=0, retracted_by_table={},
                                                 prior_rows_missing_or_changed=0)
        self.assertEqual(validate_migration(audit_, connection, prior, retraction=True), 'identical')
        self.assertEqual(validate_migration(audit_, connection, prior), 'identical')


if __name__ == '__main__':
    unittest.main()
