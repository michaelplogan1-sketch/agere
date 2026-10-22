"""social_needs engine (Gravity/BSeR referral closure, zero false closures) and monitor_sweep (saved monitors on new snapshots)."""
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from clinical_core.agere.clinical import social_needs as SN, monitor_sweep as MS, engine_suite as E
from clinical_core.agere.clinical.analysis_graph import validate as graph_validate
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_planner import CONTRACTS
from clinical_core.agere.clinical import graph_contracts

AS_OF = '2030-05-27'


def build(path):
    db = sqlite3.connect(Path(path)/'semantic.sqlite3')
    db.executescript('''
    CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT, available_date TEXT,
      organization_id TEXT, payload TEXT);
    CREATE TABLE social_need(patient_id TEXT, need TEXT, positive INTEGER, instrument TEXT, source_stream TEXT, source_field TEXT, event_date TEXT, record_id TEXT);
    CREATE TABLE encounter_diagnosis(encounter_id TEXT, patient_id TEXT, rank INTEGER, icd10 TEXT, type TEXT, record_id TEXT, source TEXT, event_date TEXT);
    ''')
    rec = lambda rid, pid, stream, day, payload, org='WH-PRACTICE-1': db.execute(
        'INSERT INTO record VALUES(?,?,?,?,?,?,?,?)', (rid, pid, stream, day, day, day, org, json.dumps(payload)))
    # P1: food need (Z59.4 + screen) referred, delivered by patient report -> closed
    db.execute("INSERT INTO encounter_diagnosis VALUES('e1','P1',1,'Z59.41','dx','d1','enc','2030-01-02')")
    db.execute("INSERT INTO social_need VALUES('P1','food',1,'ahc','social_screens','food','2030-01-03','s1')")
    rec('R1', 'P1', 'community_service_referrals', '2030-01-05', {'service_requested': 'food_access', 'status': 'request_sent', 'patient_consents': True})
    rec('O1', 'P1', 'community_service_outcomes', '2030-01-10', {'referral_record_id': 'R1', 'status': 'scheduled', 'service_delivered': False,
                                                                 'information_source': 'coordinator follow-up'})
    rec('O2', 'P1', 'community_service_outcomes', '2030-01-20', {'referral_record_id': 'R1', 'status': 'patient_reports_received',
                                                                 'service_delivered': True, 'information_source': 'patient telephone report'})
    # P2: transport referral only scheduled -> open (never a false closure)
    rec('R2', 'P2', 'community_service_referrals', '2030-02-01', {'service_requested': 'transport', 'status': 'request_sent'}, 'WH-PRACTICE-2')
    rec('O3', 'P2', 'community_service_outcomes', '2030-02-05', {'referral_record_id': 'R2', 'status': 'scheduled', 'service_delivered': False,
                                                                 'information_source': 'coordinator follow-up'})
    # P3: housing need (two screen dates) with no referral -> unreferred
    db.execute("INSERT INTO social_need VALUES('P3','housing',1,'ahc','social_screens','housing','2030-01-01','s2')")
    db.execute("INSERT INTO social_need VALUES('P3','housing',1,'ahc','social_screens','housing','2030-03-01','s3')")
    # P4: declined
    rec('R4', 'P4', 'community_service_referrals', '2030-03-01', {'service_requested': 'transport', 'status': 'request_sent'})
    rec('O4', 'P4', 'community_service_outcomes', '2030-03-03', {'referral_record_id': 'R4', 'status': 'patient_declined', 'service_delivered': False,
                                                                 'information_source': 'coordinator follow-up'})
    # P5: single vendor-free positive screen only -> below min_score, not a need
    db.execute("INSERT INTO social_need VALUES('P5','food',1,'ahc','social_screens','food','2030-01-01','s4')")
    db.commit()
    return db


class SocialNeeds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        build(self.tmp.name).close()

    def run_(self, **p):
        return SN.execute(self.tmp.name, None, p, AS_OF)

    def test_states_and_zero_false_closures(self):
        r = self.run_(focus='referrals')
        states = {l['patient_id']: l['state'] for l in r['loops']}
        self.assertEqual(states, {'P1': 'closed_patient_reported', 'P2': 'open_in_progress', 'P4': 'closed_declined'})
        self.assertEqual(r['closed_with_service'], 1)
        p1 = next(l for l in r['loops'] if l['patient_id'] == 'P1')
        self.assertEqual(p1['days_to_closure'], 15)
        self.assertEqual(p1['record_ids'], ['R1', 'O1', 'O2'])

    def test_strict_evidence_keeps_patient_report_open(self):
        r = self.run_(focus='open', closure_evidence='organization_confirmed')
        self.assertEqual(set(r['patient_ids']), {'P1', 'P2'})

    def test_needs_are_corroborated_and_unreferred_listed(self):
        r = self.run_(focus='unreferred')
        self.assertEqual(r['patient_ids'], ['P3'])
        self.assertNotIn('P5', {n['patient_id'] for n in r['needs']})
        self.assertIn('P1', {n['patient_id'] for n in r['needs']})

    def test_group_by_practice_and_answer(self):
        r = self.run_(focus='referrals', group_by='practice')
        groups = {g['group']: g for g in r['by_group']}
        self.assertEqual(groups['WH-PRACTICE-2']['open'], 1)
        a = SN.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(a['status'], 'answered'); self.assertIn('False closures: 0', a['value'])

    def test_validate_rejects_unknown(self):
        with self.assertRaises(ValueError): SN.validate({'focus': 'everything'})
        with self.assertRaises(ValueError): SN.validate({'bogus': 1})

    def test_routes(self):
        g, _ = E.compile_route('Which patients with food insecurity have never been referred to a community resource?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters'], {'needs': ['food'], 'focus': 'unreferred'})
        graph_validate(g, AS_OF)
        self.assertIsNone(SN.route('What is the Gravity Project?', {}, AS_OF))
        self.assertIsNone(SN.route('What drives ED visits among patients with food insecurity?', {}, AS_OF))


class Registered(unittest.TestCase):
    def test_every_suite_engine_is_on_the_menu(self):
        for op in E.MODULES:
            self.assertIn(op, PRODUCT, op); self.assertIn(op, CONTRACTS, op); self.assertIn(op, graph_contracts.TYPED, op)
            self.assertIn('type', graph_contracts.parameter_schema(op, {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}}))


class MonitorSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        old = {'source_digest': 'a', 'as_of': '2030-04-01', 'semantic_version': 'v', 'size': 1, 'mtime_ns': 1}
        for key, src, status in (('ANS-' + '1' * 24, old, 'watching'), ('ANS-' + '2' * 24, None, 'watching'), ('ANS-' + '3' * 24, None, 'needs_definition')):
            d = self.path / 'answer-workspaces' / key; d.mkdir(parents=True)
            (d / 'answer.json').write_text(json.dumps({'question': f'question {key[-1]}'}))
            obs = [{'at': 't', 'source': src, 'measurement': {}, 'baseline': True}] if status == 'watching' else []
            (d / 'workflow.json').write_text(json.dumps({'steps': [{'kind': 'deliverable'}, {'kind': 'monitor', 'mode': 'answer', 'scope': 'query',
                                                                                             'status': status, 'observations': obs}]}))
        self.current = {'source_digest': 'b', 'as_of': '2030-05-27', 'semantic_version': 'v', 'size': 2, 'mtime_ns': 2}
        self.stack = SimpleNamespace(path=self.path)

    def test_status_marks_only_new_snapshot_due(self):
        with mock.patch('clinical_core.agere.deliverables.answer_context.source_identity', return_value=self.current):
            rows, _ = MS.status(self.path)
        due = {r['context_id'][-1]: r['due'] for r in rows}
        self.assertEqual(due, {'1': True, '2': True, '3': False})  # baseline without source (None) differs from current

    def test_sweep_refreshes_due_and_survives_errors(self):
        def refresh(stack, req):
            if req['context_id'].endswith('2'): raise ValueError('Planner policy changed')
            return {'steps': [{}, {'observations': [{}, {'changed': True, 'source': self.current}], 'reason': 'Observed answer changed'}]}
        MS.schedule(self.path)
        with mock.patch('clinical_core.agere.deliverables.answer_context.source_identity', return_value=self.current), \
             mock.patch('clinical_core.agere.deliverables.workflow.refresh', side_effect=refresh):
            r = MS.execute(self.stack, None, {}, '2030-05-27')
        self.assertEqual((r['counts']['changed'], r['counts']['not_recomputed'], r['counts']['not_watching']), (1, 1, 1))
        self.assertFalse((self.path / MS.DUE_FILE).exists())
        self.assertIn('1 changed', MS.answer(r, {}, 'q')['value'])
        self.assertTrue((self.path / MS.LOG_FILE).exists())

    def test_route(self):
        g, _ = E.compile_route('Did any of our saved monitors change after the latest data refresh?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['operation'], 'saved_monitors')
        self.assertIsNone(MS.route('Is ED use out of control at any practice?', {}, AS_OF))


if __name__ == '__main__':
    unittest.main()
