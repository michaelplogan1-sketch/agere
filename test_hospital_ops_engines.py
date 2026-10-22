"""Wave-2 hospital-operations engines: registration, free routes, and each engine's core logic on small synthetic fixtures."""
import json
import sqlite3
import unittest
from unittest import mock

import numpy as np

from clinical_core.agere.clinical import (antimicrobial_use as AM, bundle_compliance as BC, capacity_simulation as CS, discharge_forecast as DF, engine_suite as E,
                                          flow_decomposition as FD, graph_contracts, hai_surveillance as HAI, hospital_ops as H, network_adequacy as NA,
                                          or_block as OR, overbooking_fairness as OF, scheduling as S, spatiotemporal_cluster as ST)
from clinical_core.agere.clinical.analysis_graph import validate as graph_validate
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_planner import CONTRACTS

AS_OF = '2030-05-27'
NEW = {'capacity_simulation': CS, 'discharge_forecast': DF, 'flow_decomposition': FD, 'or_block_optimization': OR, 'hai_surveillance': HAI, 'antimicrobial_use': AM,
       'bundle_compliance': BC, 'spatiotemporal_cluster': ST, 'overbooking_fairness': OF, 'network_adequacy': NA}


def visit(i, pid='P1', cls='direct_inpatient', start='2029-03-01', end='2029-03-04', org='WH-HOSPITAL-1', known=1, **kw):
    inp = cls in ('direct_inpatient', 'ed_to_inpatient', 'observation_to_inpatient')
    row = {'visit_group_id': f'V{i}', 'patient_id': pid, 'organization_id': org, 'visit_class': cls, 'has_ed': int(cls.startswith('ed') or cls == 'emergency'),
           'has_observation': int('observation' in cls), 'has_inpatient': int(inp), 'start_date': start, 'end_date': end if known else None, 'discharge_known': known,
           'ed_arrival': None, 'ed_departure': None, 'admit_decision': None, 'inpatient_admit': None, 'discharge_time': None, 'ed_hours': None,
           'los_days': (H.d(end) - H.d(start)).days if known else None, 'los_hours': None, 'time_precision': 'day', 'disposition': None, 'admit_reason': None,
           'principal_icd10': None, 'unit': None, 'transfer_in': 0, 'sources': 'ehr', 'members': '[]', 'n_members': 1, 'version': 'x'}
    row.update(kw)
    return row


class Keep:
    """sqlite connection whose close() is a no-op, so one fixture serves an engine that closes its connection."""
    def __init__(self, db): self.db = db
    def __getattr__(self, name): return getattr(self.db, name)
    def close(self): pass


def run(mod, rows, params=None, db=None, **patches):
    db = Keep(db or sqlite3.connect(':memory:'))
    if not H.has_table(db, 'organization'): db.execute('CREATE TABLE organization(organization_id, display_name, facility_class, organization_type)')
    with mock.patch.object(H, 'connect', return_value=(db, AS_OF)), \
            mock.patch.object(H, 'visits_in', return_value=(rows, 'test', ('2029-01-01', AS_OF), None, [])):
        return mod.execute('/nowhere', None, params or {}, AS_OF)


class Registered(unittest.TestCase):
    def test_every_new_engine_is_on_the_menu(self):
        for op in NEW:
            self.assertIn(op, E.MODULES)
            self.assertIn(op, PRODUCT, op); self.assertIn(op, CONTRACTS, op); self.assertIn(op, graph_contracts.TYPED, op)
            self.assertIn('type', graph_contracts.parameter_schema(op, {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}}))
            self.assertIn(op, E.ROUTE_ORDER)

    def test_every_template_routes_to_its_own_engine_with_valid_parameters(self):
        for op, mod in NEW.items():
            for q in mod.TEMPLATES:
                got = E.compile_route(q, {}, AS_OF)
                self.assertIsNotNone(got, f'{op}: {q}')
                g, _ = got
                self.assertEqual(g['nodes'][0]['operation'], op, q)
                mod.validate(g['nodes'][0]['parameters'])
                graph_validate(g, AS_OF)

    def test_definitions_do_not_route(self):
        for q in ('What is a CLABSI?', 'Explain network adequacy', 'What is SEP-1?', 'Define days of therapy', 'What is a capacity simulation?'):
            self.assertIsNone(E.compile_route(q, {}, AS_OF), q)

    def test_validators_reject_unknown_parameters(self):
        for mod in NEW.values():
            with self.assertRaises(ValueError): mod.validate({'bogus': 1})


class Capacity(unittest.TestCase):
    def test_more_beds_cut_boarding_and_common_random_numbers_hold(self):
        twin = {'rates': [3.0] * 7, 'los_hours': [96.0] * 20, 'ed_share': 1.0}
        tight = CS.scenario(twin, 10, 6, 120)
        roomy = CS.scenario(twin, 40, 6, 120)
        self.assertGreater(tight['boarding_mean_h'][0], roomy['boarding_mean_h'][0])
        self.assertEqual(roomy['boarding_mean_h'][0], 0.0)
        self.assertEqual(CS.scenario(twin, 10, 6, 120), tight)  # deterministic

    def test_shorter_los_and_growth_move_the_right_way(self):
        twin = {'rates': [2.0] * 7, 'los_hours': [72.0] * 20, 'ed_share': 1.0}
        base = CS.scenario(twin, 9, 6, 120)
        self.assertLessEqual(CS.scenario(twin, 9, 6, 120, los_mult=0.7)['boarding_mean_h'][0], base['boarding_mean_h'][0])
        self.assertGreaterEqual(CS.scenario(twin, 9, 6, 120, adm_mult=1.4)['boarding_mean_h'][0], base['boarding_mean_h'][0])

    def test_execute_states_the_bed_assumption(self):
        rows = [visit(i, start=f'2029-{1 + i % 9:02d}-{1 + i % 25:02d}', end=f'2029-{1 + i % 9:02d}-{3 + i % 25:02d}', cls='ed_to_inpatient') for i in range(120)]
        r = run(CS, rows, {'add_beds': 4, 'replications': 4, 'horizon_days': 60, 'sensitivity': False})
        self.assertEqual(r['status'], 'computed')
        self.assertTrue(any('bed count is not received' in n for n in r['notes']))
        self.assertEqual(r['receipt']['beds_scenario'], r['receipt']['beds_baseline'] + 4)
        self.assertEqual(CS.answer(r, {'coverage': 'supported_observation'}, 'q')['status'], 'answered')

    def test_routes_extract_scenario(self):
        g, _ = CS.route('If we opened 12 more inpatient beds, how much would ED boarding hours fall?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters']['add_beds'], 12)
        g, _ = CS.route('simulate capacity if length of stay drops 10% at hospital 2', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters'], {'facility': 'hospital 2', 'los_change_pct': -10})


class Discharge(unittest.TestCase):
    def test_forecast_uses_open_visits_and_backtests(self):
        rows = [visit(i, start=f'2029-{1 + i % 11:02d}-{1 + i % 20:02d}', end=f'2029-{1 + i % 11:02d}-{3 + i % 20:02d}') for i in range(150)]
        rows += [visit(900, pid='P9', start='2030-05-24', end=None, known=0)]
        r = run(DF, rows, {'horizon_days': 5, 'replications': 20})
        self.assertEqual(len(r['table']), 5)
        self.assertEqual(r['receipt']['in_house_now'], 1)
        self.assertEqual(r['patient_ids'], ['P9'])
        self.assertIn('discharges', DF.answer(r, {'coverage': 'supported_observation'}, 'q')['value'])

    def test_backtest_beats_nothing_when_weekday_pattern_exists(self):
        rows = []
        for w in range(40):
            for k in range(7):
                for j in range(3 if k < 5 else 0):
                    day = H.add_days('2029-06-04', w * 7 + k)
                    rows.append(visit(len(rows), start=H.add_days(day, -2), end=day))
        bt = DF.backtest(rows, '2030-03-01')
        self.assertLess(bt['mae_weekday_mean'], bt['mae_overall_mean'])


class Flow(unittest.TestCase):
    def test_open_stay_is_censored_not_dropped(self):
        rows = [visit(i, cls='ed_to_inpatient', start='2030-05-01', end='2030-05-03', ed_arrival='2030-05-01T01:00:00', admit_decision='2030-05-01T07:00:00',
                      inpatient_admit='2030-05-01T09:00:00', discharge_time='2030-05-03T09:00:00') for i in range(10)]
        rows.append(visit(99, cls='ed_to_inpatient', start='2030-05-20', end=None, known=0, ed_arrival='2030-05-20T01:00:00', inpatient_admit='2030-05-20T09:00:00'))
        r = run(FD, rows)
        t = {x['stage']: x for x in r['table']}
        self.assertEqual(t['ed_arrival_to_admit_decision']['median_hours'], 6.0)
        self.assertEqual(t['admit_decision_to_inpatient_admit']['median_hours'], 2.0)
        self.assertEqual(t['inpatient_los']['censored_open'], 1)
        self.assertEqual(t['ed_arrival_to_admit_decision']['missing_clock_pair'], 1)
        self.assertEqual(r['receipt']['bottleneck']['all']['stage'], 'ed_arrival_to_admit_decision')

    def test_km_median_exceeds_naive_when_stays_are_open(self):
        self.assertEqual(H.km_quantiles([1, 2, 3, 10, 10], [True, True, True, False, False])[0.5], 3)


def hai_db():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE encounter_diagnosis(encounter_id, patient_id, rank, icd10, type, record_id, source, event_date)')
    return db


class Hai(unittest.TestCase):
    def test_day3_window_and_repeat_dedupe(self):
        db = hai_db()
        for pid, code, day in (('P1', 'N39.0', '2029-03-01'), ('P2', 'N39.0', '2029-03-04'), ('P2', 'N39.0', '2029-03-06'), ('P3', 'N39.0', '2029-03-03')):
            db.execute("INSERT INTO encounter_diagnosis VALUES('e',?,1,?,'dx','r','enc',?)", (pid, code, day))
        rows = [visit(1, 'P1', start='2029-03-01', end='2029-03-08'), visit(2, 'P2', start='2029-03-01', end='2029-03-10'), visit(3, 'P3', start='2029-03-01', end='2029-03-08')]
        got = HAI.cases(db, rows, AS_OF, HAI.validate({}), None)
        by = {(c['patient_id']): c for c in got}
        self.assertFalse(by['P1']['hospital_onset'])          # hospital day 1: present on admission
        self.assertTrue(by['P2']['hospital_onset'])           # day 4
        self.assertEqual(sum(1 for c in got if c['patient_id'] == 'P2'), 1)  # second code inside the 14-day window is not a new case
        self.assertTrue(by['P3']['hospital_onset'])           # day 3 counts

    def test_rate_and_spc(self):
        db = hai_db()
        for i in range(6):
            db.execute("INSERT INTO encounter_diagnosis VALUES('e',?,1,'A04.7','dx','r','enc','2029-03-05')", (f'P{i}',))
        rows = [visit(i, f'P{i}', start='2029-03-01', end='2029-03-11') for i in range(6)] + [visit(50 + i, f'Q{i}', start='2029-04-01', end='2029-07-10') for i in range(6)]
        r = run(HAI, rows, {'infections': ['cdi'], 'group_by': 'month'}, db=db)
        self.assertEqual(r['receipt']['hospital_onset_cases'], 6)
        self.assertEqual(r['receipt']['patient_days'], 660)
        self.assertAlmostEqual(r['receipt']['rate_per_1000_patient_days'], 9.1, 1)
        self.assertEqual(r['receipt']['spc_signal_months'], ['2029-03'])


def mar_db(extra=''):
    db = sqlite3.connect(':memory:')
    db.executescript('CREATE TABLE record(record_id, patient_id, stream, event_date, recorded_date, available_date, organization_id, payload);'
                     'CREATE TABLE measurement(patient_id, event_date, event_time, value, loinc, name);'
                     'CREATE TABLE encounter_diagnosis(encounter_id, patient_id, rank, icd10, type, record_id, source, event_date);' + extra)
    return db


def mar(db, pid, drug, day, route='intravenous', time=None, status='given'):
    db.execute("INSERT INTO record VALUES(?,?,?,?,?,?,?,?)", (f'{pid}{drug}{day}{route}', pid, 'medication_administrations', day, day, day, 'WH-HOSPITAL-1',
               json.dumps({'medication_name': drug, 'administration_status': status, 'route': route, 'event_date': day, 'event_time': time})))


class Antimicrobial(unittest.TestCase):
    def test_dot_per_1000_days_present_and_stewardship(self):
        db = mar_db()
        for d_ in ('2029-03-01', '2029-03-02'): mar(db, 'P1', 'vancomycin', d_)
        mar(db, 'P1', 'vancomycin', '2029-03-02')                      # duplicate dose same day is one DOT
        mar(db, 'P1', 'cephalexin', '2029-03-03', route='oral'); mar(db, 'P1', 'cephalexin', '2029-03-04', route='oral')
        mar(db, 'P1', 'ceftriaxone', '2029-03-01', status='not given')  # refused doses do not count
        mar(db, 'P1', 'metoprolol', '2029-03-01')                      # not an antibacterial
        rows = [visit(1, 'P1', start='2029-03-01', end='2029-03-05'), visit(2, 'P2', start='2029-03-01', end='2029-03-11')]
        r = run(AM, rows, {'group_by': 'none'}, db=db)
        self.assertEqual(r['receipt']['days_of_therapy'], 4)
        self.assertEqual(r['receipt']['days_present'], 14)
        self.assertEqual(r['receipt']['dot_per_1000_days_present'], round(4 / 14 * 1000, 1))
        st = r['receipt']['stewardship']
        self.assertEqual((st['stays_on_iv'], st['iv_to_oral_switch'], st['de_escalated']), (1, 1, 1))

    def test_group_by_spectrum(self):
        db = mar_db()
        mar(db, 'P1', 'vancomycin', '2029-03-01'); mar(db, 'P1', 'cephalexin', '2029-03-02', route='oral')
        r = run(AM, [visit(1, 'P1', start='2029-03-01', end='2029-03-03')], {'group_by': 'spectrum'}, db=db)
        self.assertEqual({t['spectrum']: t['days_of_therapy'] for t in r['table']}, {'broad_hospital': 1, 'narrow_or_community': 1})


class Bundle(unittest.TestCase):
    def test_all_or_none_with_not_evaluable_elements(self):
        db = mar_db()
        db.execute("INSERT INTO encounter_diagnosis VALUES('e','P1',1,'A41.9','dx','r','enc','2029-03-01')")
        db.execute("INSERT INTO encounter_diagnosis VALUES('e','P2',1,'A41.9','dx','r','enc','2029-03-01')")
        # P1: lactate 1.5 at +1h and ceftriaxone at +2h  -> compliant on evaluable elements.  P2: antibiotic at +5h, no lactate -> fails.
        db.execute("INSERT INTO measurement VALUES('P1','2029-03-01','2029-03-01T03:00:00+00:00','1.5','2524-7','Lactate')")
        mar(db, 'P1', 'ceftriaxone', '2029-03-01', time='2029-03-01T04:00:00+00:00')
        mar(db, 'P2', 'ceftriaxone', '2029-03-01', time='2029-03-01T07:00:00+00:00')
        rows = [visit(1, 'P1', cls='ed_to_inpatient', start='2029-03-01', end='2029-03-04', ed_arrival='2029-03-01T02:00:00'),
                visit(2, 'P2', cls='ed_to_inpatient', start='2029-03-01', end='2029-03-04', ed_arrival='2029-03-01T02:00:00')]
        r = run(BC, rows, {'list_cases': True}, db=db)
        self.assertEqual((r['receipt']['sepsis_cases'], r['receipt']['compliant_on_evaluable_elements']), (2, 1))
        cases = {c['patient_id']: c for c in r['cases']}
        self.assertTrue(cases['P1']['compliant_on_evaluable'])
        self.assertIn('no lactate within the window', cases['P2']['failure_reasons'])
        self.assertEqual(cases['P1']['elements']['blood_culture_before_antibiotic'], 'not_evaluable')
        self.assertEqual(cases['P1']['elements']['repeat_lactate'], 'not_applicable')
        self.assertIn('not evaluable', BC.headline(r))

    def test_high_lactate_needs_repeat(self):
        db = mar_db()
        db.execute("INSERT INTO encounter_diagnosis VALUES('e','P1',1,'R65.20','dx','r','enc','2029-03-01')")
        db.execute("INSERT INTO measurement VALUES('P1','2029-03-01','2029-03-01T03:00:00+00:00','4.5','2524-7','Lactate')")
        mar(db, 'P1', 'ceftriaxone', '2029-03-01', time='2029-03-01T04:00:00+00:00')
        rows = [visit(1, 'P1', cls='ed_to_inpatient', start='2029-03-01', end='2029-03-04', ed_arrival='2029-03-01T02:00:00')]
        r = run(BC, rows, {'list_cases': True}, db=db)
        c = r['cases'][0]
        self.assertEqual(c['elements']['repeat_lactate'], 'failed'); self.assertEqual(c['elements']['crystalloid_fluids'], 'not_evaluable')
        self.assertFalse(c['compliant_on_evaluable'])


class Cluster(unittest.TestCase):
    def test_planted_cluster_is_found_and_background_is_not(self):
        rng = np.random.default_rng(3)
        db = sqlite3.connect(':memory:'); db.execute('CREATE TABLE patient(patient_id, zip, county, state)')
        rows, n = [], 0
        zips = [f'0{2100 + i}' for i in range(40)]
        for i in range(600):          # background: random zip, random day in 2029
            z = zips[rng.integers(0, 40)]; day = H.add_days('2029-01-01', int(rng.integers(0, 300)))
            db.execute('INSERT INTO patient VALUES(?,?,?,?)', (f'B{i}', z, '25017', 'MA'))
            rows.append(visit(n, f'B{i}', cls='emergency', start=day, end=day, principal_icd10='J10.1', has_ed=1)); n += 1
        for i in range(25):           # planted: one zip, two weeks in July
            db.execute('INSERT INTO patient VALUES(?,?,?,?)', (f'C{i}', '02133', '25025', 'MA'))
            rows.append(visit(n, f'C{i}', cls='emergency', start=H.add_days('2029-07-10', i % 12), end=H.add_days('2029-07-10', i % 12), principal_icd10='J18.9', has_ed=1)); n += 1
        r = run(ST, rows, {'syndrome': 'respiratory', 'permutations': 99, 'months': 30}, db=db)
        top = r['table'][0]
        self.assertEqual(top['zone'], 'ZIP 02133')
        self.assertTrue(top['significant']); self.assertGreaterEqual(top['observed'], 20)
        self.assertTrue(top['window_start'] <= '2029-07-12' <= top['window_end'])
        quiet = [v for v in rows if not v['patient_id'].startswith('C')]
        q = run(ST, quiet, {'syndrome': 'respiratory', 'permutations': 99, 'months': 30}, db=db)
        self.assertFalse(any(t['significant'] and t['observed'] >= 15 for t in q['table']))

    def test_too_few_cases_is_unknown_not_a_guess(self):
        db = sqlite3.connect(':memory:'); db.execute('CREATE TABLE patient(patient_id, zip, county, state)')
        r = run(ST, [], {}, db=db)
        self.assertEqual(r['status'], 'unknown')


class Overbooking(unittest.TestCase):
    def test_group_with_higher_no_show_carries_more_double_booking(self):
        rng = np.random.default_rng(7)
        rows, k = [], 0
        for day in range(300):
            for org in ('WH-PRACTICE-1', 'WH-PRACTICE-2'):
                for slot in range(8):
                    young = rng.random() < 0.3
                    ns = int(rng.random() < (0.25 if young else 0.04))
                    sched = 740000 + day
                    rows.append({'appointment_id': f'A{k}', 'patient_id': f'P{k}', 'scheduled_date': f'{2020 + day // 365}-{(day % 365) // 31 + 1:02d}-{(day % 28) + 1:02d}',
                                 'sched': sched, 'booked': sched - 10 - slot, 'final_status': 'kept', 'no_show': ns, 'cancelled': 0, 'rescheduled': 0, 'kept': 1 - ns,
                                 'organization_id': org, 'purpose': 'visit', 'age': 10 if young else 55, 'coverage': 'medicaid' if young else 'commercial',
                                 'prior': 2, 'prior_no_shows': 1 if ns else 0})
                    k += 1
        for r in rows: r['scheduled_date'] = f"{2020 + (r['sched'] - 740000) // 365}-{((r['sched'] - 740000) % 365) // 31 + 1:02d}-{((r['sched'] - 740000) % 28) + 1:02d}"
        with mock.patch.object(S, '_load', return_value=(rows, {})), mock.patch.object(S, '_history', lambda rows: None), \
                mock.patch.object(S, 'choose_l2', return_value=(1.0, None)), mock.patch.object(H, 'connect', return_value=(sqlite3.connect(':memory:'), AS_OF)):
            r = OF.execute('/nowhere', None, {'start': '2020-10-01', 'end': '2021-12-31'}, AS_OF)
        self.assertEqual(r['status'], 'computed')
        pay = {t['group']: t for t in r['table'] if t['dimension'] == 'payer'}
        self.assertGreater(pay['medicaid']['expected_double_booked_per_100'], pay['commercial']['expected_double_booked_per_100'])
        self.assertEqual(pay['medicaid']['finding'], 'higher exposure')
        self.assertIn('payer medicaid', OF.headline(r))

    def test_helpers(self):
        self.assertEqual(OF.band(70), '65+'); self.assertEqual(OF.band(None), 'age unknown')
        self.assertLess(OF.z_test(50, 100, 10, 100), 0.001); self.assertGreater(OF.z_test(10, 100, 11, 100), 0.5)


class OrBlock(unittest.TestCase):
    def cases(self):
        out = []
        for w in range(40):
            for dow, svc, n in ((0, 'Orthopedics', 4), (1, 'Orthopedics', 1), (2, 'Ophthalmology', 6), (3, 'Orthopedics', 4), (4, 'Ophthalmology', 1)):
                for j in range(n):
                    out.append({'patient_id': f'P{w}{dow}{j}', 'day': H.add_days('2029-01-01', w * 7 + dow), 'minutes': 120.0, 'chapter': 'x', 'facility': 'WH-ASC-1', 'service': svc})
        return out

    def test_minutes_model(self):
        self.assertEqual(OR.minutes('66984')[0], 30); self.assertEqual(OR.minutes('27447')[0], 105); self.assertIsNone(OR.minutes('99213')[0])

    def test_optimised_beats_flat_and_puts_blocks_where_demand_is(self):
        with mock.patch.object(OR, 'load_cases', return_value=self.cases()), mock.patch.object(H, 'connect', return_value=(sqlite3.connect(':memory:'), AS_OF)), \
                mock.patch.object(H, 'facilities', return_value={}):
            r = OR.execute('/nowhere', None, {'start': '2029-01-01', 'end': '2029-10-01', 'rooms': 1, 'block_hours': 8}, AS_OF)
        self.assertEqual(r['status'], 'computed')
        opt = {t['service']: t for t in r['table'] if t['schedule'] == 'optimised'}
        self.assertGreaterEqual(opt['Orthopedics']['Mon'], 1); self.assertGreaterEqual(opt['Ophthalmology']['Wed'], 1)
        self.assertGreaterEqual(r['receipt']['optimised_covered_minutes'], r['receipt']['flat_covered_minutes'])
        self.assertTrue(all(sum(t[d] for t in opt.values()) <= 1 for d in OR.DAYS))   # one room


class Network(unittest.TestCase):
    def test_desert_and_local_share(self):
        db = sqlite3.connect(':memory:')
        db.executescript('CREATE TABLE patient(patient_id, county, state, line_of_business);'
                         'CREATE TABLE clinician(clinician_id, organization_id, specialty, npi, taxonomy_code);'
                         'CREATE TABLE appointment(appointment_id, patient_id, scheduled_date, kept, clinician_id);'
                         'CREATE TABLE payer_claim(patient_id, rendering_npi, network_status, service_from, counts_in_metrics);')
        for i in range(40): db.execute("INSERT INTO patient VALUES(?,?,?,?)", (f'A{i}', '001', 'MA', 'commercial'))
        for i in range(40): db.execute("INSERT INTO patient VALUES(?,?,?,?)", (f'B{i}', '002', 'MA', 'commercial'))
        db.execute("INSERT INTO clinician VALUES('C1','O','Cardiology','N1',NULL)")
        for i in range(10): db.execute("INSERT INTO appointment VALUES(?,?,?,1,'C1')", (f'x{i}', f'A{i}', '2030-01-05'))
        for i in range(2): db.execute("INSERT INTO appointment VALUES(?,?,?,1,'C1')", (f'y{i}', f'B{i}', '2030-01-06'))
        db.execute("INSERT INTO payer_claim VALUES('B3','N1','out_of_network','2030-01-07',1)")
        db.execute("INSERT INTO payer_claim VALUES('A3','N1','in_network','2030-01-07',1)")
        db = Keep(db)
        with mock.patch.object(H, 'connect', return_value=(db, AS_OF)):
            r = NA.execute('/nowhere', None, {'group_by': 'geography', 'min_members': 10}, AS_OF)
        t = {x['geography']: x for x in r['table']}
        self.assertEqual(t['001']['specialties_covered'], 1)
        self.assertEqual(t['002']['specialties_no_local_provider'], ['Cardiology'])
        with mock.patch.object(H, 'connect', return_value=(db, AS_OF)):
            r = NA.execute('/nowhere', None, {'min_members': 10}, AS_OF)
        c = r['table'][0]
        self.assertEqual((c['specialty'], c['geographies_no_local_provider']), ('Cardiology', 1))
        self.assertEqual(c['out_of_network_claim_share'], 0.5)
        self.assertAlmostEqual(c['local_visit_share'], 10 / 12, 3)


if __name__ == '__main__':
    unittest.main()
