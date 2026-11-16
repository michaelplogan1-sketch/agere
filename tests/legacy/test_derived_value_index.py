"""Derived-value discovery index: stored/derived entries with recipes, discovery ranking, aggregation grain, route safety."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from clinical_core.agere.clinical import derived_value_index as D, engine_suite as E, graph_contracts
from clinical_core.agere.clinical.analysis_graph import validate as graph_validate
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_planner import CONTRACTS

AS_OF = '2030-05-27'


def mini(root, n=40):
    root = Path(root)
    (root / 'run.json').write_text(json.dumps({'source_digest': 'dig1'}))
    db = sqlite3.connect(root / 'semantic.sqlite3')
    db.executescript('''CREATE TABLE meta(key TEXT, value TEXT); INSERT INTO meta VALUES ('as_of','2030-05-27');
        CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, payload TEXT);
        CREATE TABLE patient(patient_id TEXT PRIMARY KEY, birth_date TEXT, pcp_practice_id TEXT, payer_id TEXT);
        CREATE TABLE appointment(appointment_id TEXT, patient_id TEXT, booked_date TEXT, scheduled_date TEXT, lead_time_days INTEGER);
        CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, los_days INTEGER);
        CREATE TABLE visit(patient_id TEXT, event_date TEXT);
        CREATE TABLE claim(patient_id TEXT, allowed_cents INTEGER, billed_cents INTEGER);
        CREATE TABLE measurement(record_id TEXT, patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL);
        CREATE TABLE measurement_latest(patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL);''')
    base = date(2029, 1, 1)
    for i in range(n):
        p = f'P{i}'
        db.execute('INSERT INTO patient VALUES (?,?,?,?)', (p, f'{1950 + i % 40}-06-15', f'PR{i % 2}', 'PAY'))
        for j in range(2):
            booked = base + timedelta(days=i * 3 + j * 40); lead = 5 + (i % 7) + j * 10
            db.execute('INSERT INTO appointment VALUES (?,?,?,?,?)', (f'A{i}{j}', p, booked.isoformat(), (booked + timedelta(days=lead)).isoformat(), lead))
        adm = base + timedelta(days=i); los = 1 + i % 6
        db.execute('INSERT INTO stay VALUES (?,?,?,?)', (p, adm.isoformat(), (adm + timedelta(days=los)).isoformat(), los))
        db.execute('INSERT INTO visit VALUES (?,?)', (p, (date(2030, 5, 27) - timedelta(days=30 * (i % 20))).isoformat()))
        db.execute('INSERT INTO claim VALUES (?,?,?)', (p, 8000 + i, 10000 + i))
        for k, v in enumerate((9.0 - 0.02 * i, 8.0 - 0.02 * i)):
            db.execute('INSERT INTO measurement VALUES (?,?,?,?,?)', (f'M{i}{k}', p, 'hba1c', (base + timedelta(days=100 * k)).isoformat(), v))
        db.execute('INSERT INTO measurement_latest VALUES (?,?,?,?)', (p, 'hba1c', (base + timedelta(days=100)).isoformat(), 8.0 - 0.02 * i))
        db.execute('INSERT INTO record VALUES (?,?,?,?)', (f'R{i}', p, 'appointments', json.dumps({'booked_date': 'x', 'scheduled_date': 'y', 'event_date': 'z'})))
    db.commit(); db.close()
    return root


class Index(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = mini(self.tmp.name)
        self.report = D.build(self.root)

    def test_build_covers_every_kind_with_support_and_caches(self):
        kinds = self.report['by_kind']
        for k in ('stored', 'days_between', 'days_since', 'count', 'concept', 'age', 'ratio'):
            self.assertGreater(kinds.get(k, 0), 0, k)
        self.assertEqual(D.build(self.root)['source_digest'], 'dig1')
        e = D.entry(self.root, 'days_between:appointment.booked_date>scheduled_date')
        self.assertEqual(e['support']['patients'], 40); self.assertIn('julianday', e['derivation'] + e['sql'])
        self.assertIn('appointment.booked_date', e['inputs'])

    def test_received_fields_are_not_listed_as_derived(self):
        ids = {r['id'] for r in D.discover(self.root, 'booked date scheduled date', k=20)}
        self.assertNotIn('stored:appointment.booked_date', ids)             # booked_date is a payload key of the received stream
        self.assertIn('stored:stay.los_days', {r['id'] for r in D.discover(self.root, 'length of stay', k=5)})

    def test_discovery_ranks_by_what_is_asked(self):
        top = lambda q: D.discover(self.root, q, k=3)[0]
        self.assertEqual(top('median days between booking and the scheduled visit')['id'], 'days_between:appointment.booked_date>scheduled_date')
        self.assertEqual(top('how many days since the last visit')['id'], 'days_since:visit.event_date')
        self.assertEqual(top('change in a1c from first to latest result')['id'], 'concept:hba1c.change')
        self.assertEqual(top('allowed as a share of billed')['id'], 'ratio:claim.allowed_cents/billed_cents')
        self.assertEqual(top('billed as a share of allowed')['id'], 'ratio:claim.billed_cents/allowed_cents')
        self.assertEqual(top('how old are patients')['id'], 'age:patient.birth_date')
        self.assertEqual(D.discover(self.root, 'zzz qqq', k=3), [])

    def test_unrelated_cues_halve_coverage(self):
        c = {r['id']: r['coverage'] for r in D.discover(self.root, 'stay visit appointment', k=20)}
        self.assertLess(c.get('count:visit', 0.5), 1.0)                      # a count entry needs a count cue

    def test_aggregation_grain_and_values(self):
        rep = D.execute(self.root, None, {'entry': 'days_between:appointment.booked_date>scheduled_date', 'aggregate': 'median'}, AS_OF)
        self.assertEqual((rep['grain'], rep['values_aggregated'], rep['patients_with_value']), ('event', 80, 40))   # per appointment, not per patient mean
        leads = sorted(5 + (i % 7) + j * 10 for i in range(40) for j in range(2))
        self.assertEqual(rep['overall'], (leads[39] + leads[40]) / 2)
        stay = D.execute(self.root, None, {'entry': 'stored:stay.los_days', 'aggregate': 'mean'}, AS_OF)
        self.assertAlmostEqual(stay['overall'], sum(1 + i % 6 for i in range(40)) / 40, places=3)
        since = D.execute(self.root, None, {'entry': 'days_since:visit.event_date', 'aggregate': 'patients_over', 'threshold': 365}, AS_OF)
        self.assertEqual(since['grain'], 'patient'); self.assertEqual(len(since['patient_ids']), sum(1 for i in range(40) if 30 * (i % 20) > 365))
        change = D.execute(self.root, None, {'entry': 'concept:hba1c.change', 'aggregate': 'mean'}, AS_OF)
        self.assertAlmostEqual(change['overall'], -1.0, places=3)              # 8.0 - 9.0 for every patient
        cohort = D.execute(self.root, [f'P{i}' for i in range(10)], {'entry': 'stored:stay.los_days', 'aggregate': 'count'}, AS_OF)
        self.assertEqual(cohort['overall'], 10)
        grouped = D.execute(self.root, None, {'entry': 'stored:stay.los_days', 'aggregate': 'mean', 'group_by': 'practice'}, AS_OF)
        self.assertEqual({r['practice'] for r in grouped['table']}, {'PR0', 'PR1'})
        dist = D.execute(self.root, None, {'entry': 'stored:stay.los_days', 'aggregate': 'distribution'}, AS_OF)
        self.assertEqual((dist['overall']['p0'], dist['overall']['p100']), (1.0, 6.0))
        a = D.answer(rep, {'coverage': 'supported_observation'}, 'q')
        self.assertIn('80 events from 40 patients', a['value']); self.assertIn('julianday', a['method'] + rep['entry']['derivation'])

    def test_validate_rejects_bad_parameters(self):
        for bad in ({'entry': 'x'}, {'entry': 'stored:stay.los_days', 'aggregate': 'nope'}, {'entry': 'stored:stay.los_days', 'aggregate': 'share_over'},
                    {'entry': 'stored:stay.los_days', 'extra': 1}, {'entry': 'stored:stay.los_days', 'group_by': 'zip'}):
            with self.assertRaises(ValueError): D.validate(bad)
        with self.assertRaises(ValueError): D.execute(self.root, None, {'entry': 'stored:nope.nothing'}, AS_OF)


class Wiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = mini(self.tmp.name); D.build(self.root)
        D.attach(self.root, 'dig1')
        self.profile = {'source_digest': 'dig1', 'streams': {}}

    def test_on_menu(self):
        self.assertIn('derived_value', E.MODULES)
        self.assertIn('derived_value', PRODUCT); self.assertIn('derived_value', CONTRACTS); self.assertIn('derived_value', graph_contracts.TYPED)
        self.assertIn('type', graph_contracts.parameter_schema('derived_value', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}}))

    def test_free_routes(self):
        cases = {'median days between booking and the scheduled visit': ('days_between:appointment.booked_date>scheduled_date', 'median', None),
                 'how many patients have gone more than 365 days since their last visit': ('days_since:visit.event_date', 'patients_over', 365.0),
                 'how many appointments had a lead time over 30 days': (None, None, None),
                 'average change in a1c from first to latest result': ('concept:hba1c.change', 'mean', None)}
        for q, (entry, how, thr) in cases.items():
            got = E.compile_route(q, self.profile, AS_OF)
            if entry is None:
                continue
            self.assertIsNotNone(got, q)
            p = got[0]['nodes'][0]['parameters']
            self.assertEqual((p['entry'], p['aggregate'], p.get('threshold')), (entry, how, thr), q)
            graph_validate(got[0], AS_OF)

    def test_route_defers_when_not_confident_or_governed(self):
        self.assertIsNone(E.compile_route('what is the average zzz', self.profile, AS_OF))            # nothing discovered
        self.assertIsNone(E.compile_route('median days between booking and the scheduled visit', {'source_digest': 'unknown', 'streams': {}}, AS_OF))  # no index for that build
        with mock.patch('clinical_core.agere.clinical.metrics.candidates', return_value=[(1.0, 'some_metric')]):
            self.assertIsNone(E.compile_route('median days between booking and the scheduled visit', self.profile, AS_OF))   # a governed metric defines it


if __name__ == '__main__':
    unittest.main()
