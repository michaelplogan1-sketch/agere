"""visit_groups: episode grouping rules, stored-table round trip, engine answer and free route."""
import sqlite3
import unittest
from unittest import mock

from clinical_core.agere.clinical import engine_suite, hospital_ops as H, visit_groups as V

AS_OF = '2030-05-27'


def ep(pid, kind, start, end=None, org='WH-HOSPITAL-1', source='ehr', **kw):
    return {'patient_id': pid, 'kind': kind, 'org': org, 'start': H.ts(start), 'end': H.ts(end) if end else None, 'source': source,
            'member': f'{source}:{pid}:{start}:{kind}', 'precision': 'minute' if ':' in start else 'day',
            'discharged': end is not None or kind == 'ed', **kw}


class Grouping(unittest.TestCase):
    def test_ed_then_admission_is_one_visit_with_clocks(self):
        rows = V.group([
            ep('P1', 'ed', '2029-03-01T02:45:00', '2029-03-01T06:00:00', ed_arrival=H.ts('2029-03-01T02:45:00'),
               ed_departure=H.ts('2029-03-01T06:00:00')),
            ep('P1', 'ed', '2029-03-01', org=None, source='ed_definition', ed_sources='adt,claim'),
            ep('P1', 'inpatient', '2029-03-01T05:45:00', '2029-03-04T18:00:00', source='stay_encounters',
               inpatient_admit=H.ts('2029-03-01T05:45:00'), discharge_time=H.ts('2029-03-04T18:00:00')),
        ], AS_OF)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r['visit_class'], 'ed_to_inpatient')
        self.assertEqual((r['ed_hours'], r['los_days'], r['n_members']), (3.25, 3, 3))
        self.assertEqual(r['organization_id'], 'WH-HOSPITAL-1')
        self.assertIn('claim', r['sources'])

    def test_overnight_ed_joins_next_day_admission_but_not_next_day_ed(self):
        rows = V.group([ep('P1', 'ed', '2029-03-01T23:00:00', '2029-03-01T23:50:00'),
                        ep('P1', 'inpatient', '2029-03-02T01:00:00', '2029-03-03T12:00:00')], AS_OF)
        self.assertEqual([r['visit_class'] for r in rows], ['ed_to_inpatient'])
        rows = V.group([ep('P1', 'ed', '2029-03-01T10:00:00', '2029-03-01T12:00:00'),
                        ep('P1', 'ed', '2029-03-02T10:00:00', '2029-03-02T12:00:00')], AS_OF)
        self.assertEqual(len(rows), 2)

    def test_other_facility_is_a_transfer_and_open_stay_is_in_house(self):
        rows = V.group([ep('P1', 'inpatient', '2029-03-01T10:00:00', '2029-03-03T10:00:00'),
                        ep('P1', 'inpatient', '2029-03-03T14:00:00', None, org='WH-HOSPITAL-2')], AS_OF)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['transfer_in'], 1)
        self.assertEqual(rows[1]['discharge_known'], 0)
        self.assertIsNone(rows[1]['end_date'])

    def test_future_episodes_are_ignored(self):
        self.assertEqual(V.group([ep('P1', 'ed', '2031-01-01T10:00:00', '2031-01-01T11:00:00')], AS_OF), [])


class Stored(unittest.TestCase):
    def test_build_and_load_round_trip(self):
        db = sqlite3.connect(':memory:')
        rows = V.group([ep('P1', 'observation', '2029-03-01T10:00:00', '2029-03-02T09:00:00')], AS_OF)
        with mock.patch.object(V, 'compute', return_value=(rows, {'ehr': 1})):
            summary = V.build(db, AS_OF)
        self.assertEqual(summary['visits'], 1)
        got, basis = V.load(db)
        self.assertIn('stored', basis)
        self.assertEqual(got[0]['visit_class'], 'observation')


class Engine(unittest.TestCase):
    def rows(self):
        return V.group([ep('P1', 'ed', '2029-03-01T02:45:00', '2029-03-01T06:00:00', ed_arrival=H.ts('2029-03-01T02:45:00'),
                           ed_departure=H.ts('2029-03-01T06:00:00')),
                        ep('P1', 'inpatient', '2029-03-01T05:45:00', '2029-03-04T18:00:00'),
                        ep('P2', 'ed', '2029-04-01T10:00:00', '2029-04-01T12:00:00', org='WH-HOSPITAL-2'),
                        ep('P3', 'inpatient', '2029-05-01T10:00:00', '2029-05-03T10:00:00')], AS_OF)

    def test_execute_counts_by_class_and_answers(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE organization(organization_id, display_name, facility_class, organization_type)')
        with mock.patch.object(V, 'load', return_value=(self.rows(), 'test')), mock.patch.object(H, 'connect', return_value=(db, AS_OF)):
            r = V.execute('/nowhere', None, {'group_by': 'visit_class'}, AS_OF)
        counts = {t['visit_class']: t['visits'] for t in r['table']}
        self.assertEqual(counts, {'ed_to_inpatient': 1, 'emergency': 1, 'direct_inpatient': 1})
        a = V.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual((a['status'], a['count']), ('answered', 3))
        self.assertIn('3 hospital visits', a['value'])

    def test_validate(self):
        self.assertEqual(V.validate({})['group_by'], 'visit_class')
        for bad in ({'group_by': 'x'}, {'visit_classes': ['nope']}, {'months': 0}, {'zzz': 1}, {'start': 'notadate'}):
            with self.assertRaises(ValueError): V.validate(bad)


class Route(unittest.TestCase):
    def test_positive(self):
        g, info = engine_suite.compile_route('How many ED-to-inpatient visits did we have by month in 2029?', {}, AS_OF)
        node = g['nodes'][0]
        self.assertEqual(node['operation'], 'visit_groups')
        self.assertEqual(node['parameters']['visit_classes'], ['ed_to_inpatient'])
        self.assertEqual(node['parameters']['group_by'], 'month')
        V.validate(node['parameters'])
        g, _ = V.route('hospital visits by facility in the last 12 months', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters'], {'group_by': 'facility', 'months': 12})

    def test_negative(self):
        for q in ('How many ED visits did diabetic patients have last year?', 'What is a hospital visit?', 'Forecast ED visits for next month'):
            self.assertIsNone(V.route(q, {}, AS_OF), q)


if __name__ == '__main__':
    unittest.main()
