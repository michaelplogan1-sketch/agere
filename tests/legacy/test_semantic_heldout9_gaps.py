"""Semantic-layer/16 gaps found by the heldout9 blind adjudication, one fixture per gap: attended appointments are kept and
population-feed bookings never get a fabricated scheduled date, PHQ questionnaires are measurements, Medicare annual wellness
visits are modelled, payer pharmacy claims count as fills while prepared / returned dispenses do not, enrollment-span members
have coverage, and length of stay uses one clock."""
import sqlite3
import unittest

from clinical_core.agere.clinical import concepts, semantic_layer
from test_warehouse_v22 import stack_fixture


def appt(rid, pid, aid, status, day, **kw):
    return (rid, pid, 'appointments', {'appointment_id': aid, 'status': status, 'event_date': day, 'available_date': kw.pop('av', day), **kw})


def phq(rid, pid, day, total, instrument='PHQ-9', time='T15:00:00+00:00'):
    return (rid, pid, 'mood_questionnaires', {'instrument': instrument, 'total': total, 'event_date': day, 'event_time': day + time,
                                              'available_date': day})


PATIENTS = {'p1': {'age_reported': 70, 'sex_reported': 'F', 'data_segment': 'deep_chart'},
            'p2': {'age_reported': 68, 'sex_reported': 'M', 'data_segment': 'deep_chart'},
            'w1': {'age_reported': 66, 'sex_reported': 'F', 'data_segment': 'claims_only'},
            'w2': {'age_reported': 40, 'sex_reported': 'M', 'data_segment': 'claims_only'},
            'w3': {'age_reported': 30, 'sex_reported': 'M', 'data_segment': 'claims_only'}}
RECORDS = [
    # appointments: attended is kept; a reschedule moves the scheduled date; booked_at/slot_start bookings; no booking row
    appt('a1b', 'p1', 'A1', 'booked', '2029-03-01', scheduled_date='2029-03-10'),
    appt('a1o', 'p1', 'A1', 'attended', '2029-03-10', scheduled_date='2029-03-10'),
    appt('a2b', 'p1', 'A2', 'booked', '2029-03-20', scheduled_date='2029-04-01'),
    appt('a2r', 'p1', 'A2', 'rescheduled', '2029-03-25', scheduled_date='2029-04-15'),
    appt('a2o', 'p1', 'A2', 'fulfilled', '2029-04-15'),
    appt('a3b', 'w1', 'A3', 'booked', '2029-05-02', booked_at='2029-05-02T11:00:00+00:00', slot_start='2029-05-05T15:20:00+00:00', lead_time_days=3),
    appt('a3o', 'w1', 'A3', 'no_show', '2029-05-05', booked_at='2029-05-02T11:00:00+00:00', slot_start='2029-05-05T15:20:00+00:00', lead_time_days=3),
    appt('a4o', 'p2', 'A4', 'attended', '2029-06-01', scheduled_date='2029-06-01'),
    appt('a5b', 'p2', 'A5', 'booked', '2029-06-02', scheduled_date='2029-06-20'),
    appt('a5u', 'p2', 'A5', 'unassigned_requires_review', '2029-06-20'),
    appt('a6b', 'p2', 'A6', 'booked', '2029-06-03', scheduled_date='2029-06-21'),
    appt('a6c', 'p2', 'A6', 'cancelled', '2029-06-10', scheduled_date='2029-06-21'),
    appt('a7b', 'p2', 'A7', 'booked', '2029-06-04', scheduled_date='2029-06-22'),
    appt('a7a', 'p2', 'A7', 'arrived', '2029-06-22'),
    # PHQ-9 administrations (two the same day ordered by time) and a PHQ-2
    phq('q1', 'p1', '2028-09-01', 6), phq('q2', 'p1', '2029-07-01', 9, time='T09:00:00+00:00'), phq('q3', 'p1', '2029-07-01', 14, time='T16:00:00+00:00'),
    phq('q4', 'p2', '2029-02-01', 3, instrument='PHQ-2'),
    # wellness visits: initial and subsequent AWV, generic preventive review (not an AWV), cancelled AWV
    ('wv1', 'p1', 'wellness_visit_services', {'visit_type': 'medicare_annual_wellness', 'hcpcs_code': 'G0438', 'status': 'completed',
                                               'encounter_id': 'E-AWV', 'event_date': '2029-03-10'}),
    ('wv2', 'p2', 'wellness_visit_services', {'visit_type': 'medicare_annual_wellness', 'hcpcs_code': 'G0439', 'status': 'completed', 'event_date': '2029-04-10'}),
    ('wv3', 'w1', 'wellness_visit_services', {'visit_type': 'preventive_review', 'hcpcs_code': None, 'status': 'completed', 'event_date': '2029-04-11'}),
    ('wv4', 'w2', 'wellness_visit_services', {'visit_type': 'medicare_annual_wellness', 'hcpcs_code': 'G0438', 'status': 'cancelled', 'event_date': '2029-04-12'}),
    ('ewv', 'p1', 'encounters', {'type': 'office', 'encounter_id': 'E-AWV', 'status': 'completed', 'event_date': '2029-03-10'}),
    # pharmacy: picked-up dispense, prepared-only, dispensed then returned to stock, payer claim paid / rejected
    ('d1', 'p1', 'pharmacy_dispenses', {'medication_name': 'atorvastatin 20 mg tablet', 'fill_id': 'F1', 'status': 'dispensed', 'event_date': '2029-02-01'}),
    ('d2', 'p1', 'pharmacy_dispenses', {'medication_name': 'atorvastatin 20 mg tablet', 'fill_id': 'F2', 'status': 'prepared', 'event_date': '2029-03-01'}),
    ('d3', 'p1', 'pharmacy_dispenses', {'medication_name': 'atorvastatin 20 mg tablet', 'fill_id': 'F3', 'status': 'dispensed', 'event_date': '2029-04-01'}),
    ('d3r', 'p1', 'pharmacy_dispenses', {'medication_name': 'atorvastatin 20 mg tablet', 'fill_id': 'F3', 'status': 'returned_to_stock', 'event_date': '2029-04-12'}),
    ('rx1', 'w1', 'payer_pharmacy_claims', {'rx_claim_id': 'RX1', 'claim_status': 'paid', 'drug_name': 'atorvastatin 20 MG Oral Tablet',
                                            'ndc': '00093505910', 'days_supply': 30, 'fill_number': 0, 'event_date': '2029-02-01'}),
    ('rx2', 'w1', 'payer_pharmacy_claims', {'rx_claim_id': 'RX2', 'claim_status': 'rejected', 'drug_name': 'atorvastatin 20 MG Oral Tablet',
                                            'ndc': '00093505910', 'days_supply': 30, 'fill_number': 1, 'event_date': '2029-03-01'}),
    # coverage: a coverage record for p1; w1 and w2 only have enrollment spans (w2's span ended before the as-of date)
    ('c1', 'p1', 'coverage', {'coverage_type': 'medicare', 'status': 'active', 'event_date': '2026-08-28'}),
    ('s1', 'w1', 'enrollment_spans', {'payer_id': 'WH-PAYER-1', 'product': 'medicare_advantage', 'coverage_start': '2028-01-01',
                                      'coverage_end': None, 'event_date': '2028-01-01', 'available_date': '2028-01-01'}),
    ('s2', 'w2', 'enrollment_spans', {'payer_id': 'WH-PAYER-1', 'product': 'commercial', 'coverage_start': '2028-01-01',
                                      'coverage_end': '2028-12-31', 'event_date': '2028-01-01', 'available_date': '2028-01-01'}),
    # inpatient stay: local admit date 2029-02-28, UTC start 2029-03-01T05:45, UTC end 2029-03-04T18:00, no ADT discharge
    ('ip1', 'p2', 'encounters', {'type': 'inpatient', 'encounter_id': 'E-IP', 'status': 'in_progress', 'event_date': '2029-02-28',
                                 'start_time': '2029-03-01T05:45:00+00:00'}),
    ('ip2', 'p2', 'encounters', {'type': 'inpatient', 'encounter_id': 'E-IP', 'status': 'completed', 'event_date': '2029-02-28',
                                 'start_time': '2029-03-01T05:45:00+00:00', 'end_time': '2029-03-04T18:00:00+00:00'}),
]


class Heldout9Gaps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = stack_fixture(RECORDS, PATIENTS)
        semantic_layer.build(cls.path)
        cls.db = sqlite3.connect(cls.path / 'semantic.sqlite3'); cls.db.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def rows(self, sql, *args):
        return [dict(r) for r in self.db.execute(sql, args)]

    def test_version_bumped(self):
        self.assertEqual(self.rows("SELECT value FROM meta WHERE key = 'version'")[0]['value'], semantic_layer.VERSION)
        self.assertNotEqual(semantic_layer.VERSION, 'semantic-layer/15')

    def test_attended_and_arrived_are_kept_and_outcomes_are_exclusive(self):
        a = {r['appointment_id']: r for r in self.rows('SELECT * FROM appointment')}
        self.assertEqual({k: (v['kept'], v['outcome']) for k, v in a.items()},
                         {'A1': (1, 'kept'), 'A2': (1, 'kept'), 'A3': (0, 'no_show'), 'A4': (1, 'kept'), 'A5': (0, 'unassigned'),
                          'A6': (0, 'cancelled'), 'A7': (1, 'kept')})

    def test_scheduled_date_from_latest_carrier_and_slot_start_never_booking_date(self):
        a = {r['appointment_id']: r for r in self.rows('SELECT appointment_id, scheduled_date, booked_date, lead_time_days FROM appointment')}
        self.assertEqual((a['A1']['scheduled_date'], a['A1']['booked_date'], a['A1']['lead_time_days']), ('2029-03-10', '2029-03-01', 9))
        self.assertEqual(a['A2']['scheduled_date'], '2029-04-15')  # the reschedule moved it
        self.assertEqual((a['A3']['scheduled_date'], a['A3']['booked_date'], a['A3']['lead_time_days']), ('2029-05-05', '2029-05-02', 3))
        self.assertEqual((a['A4']['scheduled_date'], a['A4']['booked_date'], a['A4']['lead_time_days']), ('2029-06-01', None, None))
        self.assertFalse(self.rows("SELECT 1 FROM appointment WHERE lead_time_days = 0"))

    def test_phq_questionnaires_are_measurements(self):
        m = self.rows("SELECT record_id, concept, value_canonical FROM measurement WHERE source_table = 'mood_questionnaires' ORDER BY record_id")
        self.assertEqual([(r['record_id'], r['concept'], r['value_canonical']) for r in m],
                         [('q1', 'phq9', 6), ('q2', 'phq9', 9), ('q3', 'phq9', 14), ('q4', 'phq2', 3)])
        latest = self.rows("SELECT value_canonical, record_id FROM measurement_latest WHERE patient_id = 'p1' AND concept = 'phq9'")
        self.assertEqual(latest[0]['value_canonical'], 14)
        self.assertEqual(self.rows("SELECT event_time FROM measurement WHERE record_id = 'q3'")[0]['event_time'], '2029-07-01T16:00:00+00:00')
        self.assertEqual(self.rows("SELECT records FROM concept_patient WHERE concept = 'phq9' AND patient_id = 'p1'")[0]['records'], 3)

    def test_annual_wellness_visits(self):
        w = {r['record_id']: (r['awv'], r['awv_kind']) for r in self.rows('SELECT record_id, awv, awv_kind FROM wellness_visit')}
        self.assertEqual(w, {'wv1': (1, 'initial'), 'wv2': (1, 'subsequent'), 'wv3': (0, None), 'wv4': (1, 'initial')})
        n = self.rows("SELECT count(DISTINCT patient_id) n FROM wellness_visit WHERE awv = 1 AND status = 'completed' AND event_date LIKE '2029%'")
        self.assertEqual(n[0]['n'], 2)
        self.assertEqual(self.rows("SELECT awv, wellness_visit_type FROM visit WHERE encounter_id = 'E-AWV'"),
                         [{'awv': 1, 'wellness_visit_type': 'medicare_annual_wellness'}])

    def test_payer_claims_count_as_fills_and_unpicked_dispenses_do_not(self):
        self.assertEqual(concepts.FILL_EVIDENCE, ('dispensed', 'claim', 'payer_claim'))
        self.assertEqual(concepts.FILL_EVIDENCE_SQL, '(' + ','.join(repr(e) for e in concepts.FILL_EVIDENCE) + ')')
        picked = {r['record_id'] for r in self.rows('SELECT record_id FROM medication WHERE picked_up = 1')}
        self.assertEqual(picked, {'d1', 'rx1'})
        same = {r['record_id'] for r in self.rows('SELECT record_id FROM medication WHERE ' + concepts.picked_up_sql())}
        self.assertEqual(same, picked)

    def test_enrollment_span_members_have_coverage(self):
        p = {r['patient_id']: r for r in self.rows('SELECT patient_id, coverage_type, coverage_status, coverage_source FROM patient')}
        self.assertEqual((p['p1']['coverage_type'], p['p1']['coverage_source']), ('medicare', 'coverage_record'))
        self.assertEqual((p['w1']['coverage_type'], p['w1']['coverage_status'], p['w1']['coverage_source']), ('medicare', 'active', 'enrollment_span'))
        self.assertEqual((p['w2']['coverage_type'], p['w2']['coverage_status'], p['w2']['coverage_source']), ('commercial', 'inactive', 'enrollment_span'))
        self.assertEqual((p['w3']['coverage_type'], p['w3']['coverage_source']), (None, None))

    def test_length_of_stay_in_one_clock(self):
        s = self.rows("SELECT admit_date, discharge_date, admit_time_utc, discharge_time_utc, los_days, los_days_elapsed FROM stay WHERE patient_id = 'p2'")
        self.assertEqual(len(s), 1); s = s[0]
        self.assertEqual((s['admit_date'], s['admit_time_utc']), ('2029-02-28', '2029-03-01T05:45:00+00:00'))
        self.assertEqual(s['los_days'], 3)  # UTC 03-01 -> 03-04; mixing the local admit date with the UTC discharge gave 4
        self.assertAlmostEqual(s['los_days_elapsed'], 3.5104, places=3)

    def test_described_to_planners(self):
        t = semantic_layer.describe(self.path)['tables']
        self.assertIn('awv', t['wellness_visit']['columns'])
        self.assertIn('picked_up', t['medication']['columns'])
        self.assertIn('outcome', t['appointment']['columns'])
        self.assertIn('los_days_elapsed', t['stay']['columns'])
        self.assertIn('attended', t['appointment']['meaning'])


if __name__ == '__main__':
    unittest.main()
