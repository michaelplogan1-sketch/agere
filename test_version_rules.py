"""Version and event-evidence rules from the generator's batch-2 verdicts (ACC-11 appointments, HOS-09 ED visits)."""
import json
import sqlite3
import unittest

from clinical_core.agere.clinical import cohort_families as cf, semantic_layer as sl
from clinical_core.agere.ingest.adapt_clinical import _Run


class AppointmentVersionsInOneFile(unittest.TestCase):
    def _layer(self, versions):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE IF NOT EXISTS record(record_id TEXT, patient_id TEXT, stream TEXT, payload TEXT, event_date TEXT, '
                   'available_date TEXT, organization_id TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS appointment(appointment_id TEXT PRIMARY KEY, patient_id TEXT, scheduled_date TEXT, '
                   'booked_date TEXT, final_status TEXT, ever_no_show INTEGER, ever_cancelled INTEGER, ever_rescheduled INTEGER, '
                   'kept INTEGER, organization_id TEXT, clinician_id TEXT, purpose TEXT, versions INTEGER)')
        for rid, status, seq in versions:
            p = {'appointment_id': 'A1', 'status': status, 'scheduled_date': '2029-03-04', 'version_seq': seq}
            db.execute('INSERT INTO record VALUES (?,?,?,?,?,?,?)', (rid, 'P1', 'appointments', json.dumps(p), '2029-03-01', '2029-03-31', 'O'))
        sl._appointments(db)
        return db.execute('SELECT final_status FROM appointment').fetchone()[0]

    def test_later_version_in_the_same_file_wins_whatever_the_record_hash(self):
        # same available_date: the record hash used to decide; the delivered order (version_seq) now does
        self.assertEqual(self._layer([('AGR-z', 'booked', 0), ('AGR-a', 'fulfilled', 1)]), 'fulfilled')
        self.assertEqual(self._layer([('AGR-a', 'booked', 0), ('AGR-z', 'noshow', 1)]), 'noshow')

    def test_unchained_versions_carry_their_delivered_order(self):
        run = _Run.__new__(_Run)
        run.drop = lambda *a, **k: None
        recs = [((('2029-03-02T10:00',), 'f', 2), {'status': 'fulfilled', '_lineage': []}, True),
                ((('2029-03-01T09:00',), 'f', 1), {'status': 'booked', '_lineage': []}, True)]
        out = _Run.versions(run, 'appointments', {('k',): recs}, chain=False)
        self.assertEqual([(r['status'], r['version_seq']) for r in out], [('booked', 0), ('fulfilled', 1)])
        self.assertNotIn('supersedes_record_id', {k for r in out for k, v in r.items() if v})


class EdVisitEvidence(unittest.TestCase):
    Q = ('Which members went to the ER three or more times in 2029? Definition: Persons with >= 3 ED visits with service dates '
         'in 2029. ED visit = distinct person and service date with a non-voided payer claim for an ED service (professional '
         'place of service 23, or an institutional line with revenue code 045x).')

    def test_calendar_year_threshold_is_a_family_question(self):
        r = cf.parse(self.Q, '2030-05-27')
        self.assertEqual(r['family'], 'utilization_threshold')
        p = r['parameters']
        self.assertEqual((p['min_count'], p['window'], p['ed']['sources']), (3, {'year': 2029}, ['claims']))
        self.assertEqual(p['ed']['cpt'], [])  # the stated rule (POS 23 / revenue 045x) replaces the default CPT list

    def test_denied_header_only_claims_prove_the_visit(self):
        db = sqlite3.connect(':memory:')
        db.executescript('CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, place_of_service TEXT, '
                         'counts_in_metrics INTEGER, final_status TEXT);'
                         'CREATE TABLE payer_claim_line(payer_claim_id TEXT, revenue_code TEXT);')
        rows = [('c1', 'P1', '2029-01-05', '23', 1, 'paid'), ('c2', 'P1', '2029-04-05', '23', 1, 'denied'),
                ('c3', 'P1', '2029-07-05', '23', 1, 'denied_upheld'), ('c4', 'P2', '2029-01-05', '23', 0, 'voided'),
                ('c5', 'P2', '2029-02-05', '23', 1, 'paid'), ('c6', 'P2', '2028-12-31', '23', 1, 'paid')]
        db.executemany('INSERT INTO payer_claim VALUES (?,?,?,?,?,?)', rows)
        r = cf.parse(self.Q, '2030-05-27')
        sql = cf.render(r, '2030-05-27')
        self.assertEqual([x[0] for x in db.execute(sql)], ['P1'])

    def test_years_in_other_roles_still_decline(self):
        self.assertIsNone(cf._calendar_year('patients born in 1950 with 3 or more ED visits in 2029'))
        self.assertIsNone(cf._calendar_year('3 or more ED visits as of 2029-06-30'))
        self.assertEqual(cf._calendar_year('3 or more ED visits during 2029'), 2029)


if __name__ == '__main__':
    unittest.main()
