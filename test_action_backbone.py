"""The workspace action backbone (clinical_core/agere/plans/backbone.py) with simulated counterparties, the completion
evaluator and barriers that change the action. Synthetic sqlite fixtures only (test_plans.build_fixture)."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import test_plans
from clinical_core.agere import workspace_api
from clinical_core.agere.onboarding.workspace import Workspace
from clinical_core.agere.plans import act as actmod, backbone, barriers, catalog as catmod, completion, router
from clinical_core.agere.plans.counterparty import SimulatedCounterparty

AS_OF = test_plans.AS_OF  # 2030-05-27 = the fixture's data as_of

SURE = {'outreach': {'attempts': 3, 'attempt_days': [1, 3, 6], 'p_reach': 1.0}, 'p_decline': 0, 'p_eligibility_denied': 0,
        'p_coverage_denied': 0, 'p_scheduling_failed': 0, 'p_rescheduled': 0, 'p_no_show': 0, 'p_reschedule_after_no_show': 1,
        'p_second_no_show': 0, 'p_completed': 1.0, 'modifiers': []}


def forced(**cp):
    """The packaged catalog with every program's counterparty replaced by a forced scenario (SURE + overrides)."""
    cat = catmod.load()
    for p in cat['programs']:
        if p['kind'] == 'coverage_check':
            continue
        p['counterparty'] = {**copy.deepcopy(SURE), **({'chart_review': {'p_records_needed': 0}} if p.get('role') == 'barrier_review' else {}),
                             **copy.deepcopy(cp)}
    return cat


def workspace(root, slug='plan-clinic'):
    test_plans._workspace(root, slug)
    return Workspace(slug, root)


def count(ws, sql, *args):
    db = sqlite3.connect(Path(ws.dir) / 'action' / 'program-work.sqlite3')
    try:
        return db.execute(sql, args).fetchone()[0]
    finally:
        db.close()


def add(path, sql, rows):
    db = sqlite3.connect(path)
    db.executemany(sql, rows)
    db.commit()
    db.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.ws = workspace(self.root)
        self.db = self.ws.semantic_path

    def tearDown(self):
        self.tmp.cleanup()

    def run_plan(self, kind, ids, as_of=AS_OF, cat=None, through=None):
        cat = cat or catmod.load()
        plan = router.route(self.db, ids, kind, as_of, catalog=cat)
        return plan, backbone.run(self.ws.dir, plan, workspace=self.ws.slug, semantic_path=self.db, catalog=cat, through=through)


class Idempotence(Base):
    def test_same_plan_twice_is_a_replay_and_survives_restart(self):
        first = self.ws.plan(program_kind='pharmacy_gdmt')
        ex1 = first['plan']['execution']
        self.assertFalse(ex1['replay'])
        self.assertTrue(ex1['decision_id'])
        n_items = count(self.ws, 'SELECT COUNT(*) FROM work_items')
        n_events = count(self.ws, 'SELECT COUNT(*) FROM work_events')
        again = Workspace(self.ws.slug, self.root).plan(program_kind='pharmacy_gdmt')  # a new object: nothing held in memory
        ex2 = again['plan']['execution']
        self.assertTrue(ex2['replay'])
        self.assertEqual(ex2['decision_id'], ex1['decision_id'])
        self.assertEqual(ex2['items'], {'unchanged': ex1['work_items']})
        self.assertEqual(count(self.ws, 'SELECT COUNT(*) FROM work_items'), n_items)
        self.assertEqual(count(self.ws, 'SELECT COUNT(*) FROM work_events'), n_events)
        self.assertEqual(count(self.ws, 'SELECT COUNT(*) FROM question_decisions'), 1)
        self.assertEqual(ex2['summary'], ex1['summary'])
        self.assertEqual(ex1['summary']['steps_done_without_receipt'], 0)
        self.assertIn('saved as decision', first['headline'])

    def test_changed_patient_state_reassesses_only_that_patient(self):
        _, r1 = self.run_plan('pharmacy_gdmt', ['HF1', 'HF4'])
        hf4 = backbone.items(self.ws.dir, patient_id='HF4', kind='plan_item')['items'][0]
        # a new MRA fill for HF4 (a restaged delivery): the gap changes
        add(self.db, 'INSERT INTO fill VALUES (?,?,?,?,?,?,?,?,?)',
            [(90, 'HF4', '2030-05-01', 30, '2030-05-30', 'mra', 'mra', 'spironolactone 25 MG', 'FR-90')])
        _, r2 = self.run_plan('pharmacy_gdmt', ['HF1', 'HF4'])
        self.assertNotEqual(r1['decision_id'], r2['decision_id'])
        self.assertEqual((r2['items'].get('reassessed'), r2['items'].get('unchanged')), (1, 1))
        got = backbone.item(self.ws.dir, hf4['id'])
        self.assertEqual(got['item']['cycle'], 2)
        ev = got['events'][-1] if got['events'][-1]['action'] == 'reassessed' else next(e for e in got['events'] if e['action'] == 'reassessed')
        self.assertEqual(ev['changes']['gap']['from']['missing_pillars'], ['beta_blocker', 'mra', 'rasi', 'sglt2i'])
        self.assertNotIn('mra', ev['changes']['gap']['to']['missing_pillars'])
        self.assertEqual(count(self.ws, "SELECT COUNT(*) FROM work_items WHERE patient='HF4' AND program='plan:pharmacy_gdmt'"), 1)

    def test_newly_excluded_patient_cancels_the_item(self):
        self.run_plan('pharmacy_gdmt', ['HF4'])
        add(self.db, 'INSERT INTO encounter VALUES (?,?,?,?,?,?,?,?,?)',
            [('EN-DEATH', 'HF4', 'E9', 'inpatient', 'inpatient_admission', '2030-05-20', 'HOSP', None, 'expired')])
        _, r2 = self.run_plan('pharmacy_gdmt', ['HF4'])
        self.assertEqual(r2['items'].get('cancelled'), 1)
        it = backbone.items(self.ws.dir, patient_id='HF4', kind='plan_item')['items'][0]
        self.assertEqual(it['state'], 'cancelled')


class Determinism(Base):
    def test_same_plan_same_outcomes_and_duplicate_submit_returns_original(self):
        plan = router.route(self.db, ['HF1', 'HF2', 'HF4', 'HF7'], 'pharmacy_gdmt', AS_OF)
        a = actmod.act(plan, workspace='plan-clinic')
        b = actmod.act(copy.deepcopy(plan), workspace='plan-clinic')
        self.assertEqual(a, b)
        prog = catmod.load()['programs'][0]
        ledger = {}
        cp = SimulatedCounterparty(prog, prog['vendor_slots'][0], workspace='w', patient_id='P', as_of=AS_OF, ledger=ledger)
        r1 = cp.submit({'request_id': 'R1', 'step': 'consent', 'attempt': 1, 'day': 1})
        r2 = cp.submit({'request_id': 'R1', 'step': 'consent', 'attempt': 1, 'day': 1})
        self.assertTrue(r2['duplicate_submission'])
        self.assertEqual({k: v for k, v in r2.items() if k != 'duplicate_submission'}, r1)
        self.assertEqual(r1['counterparty_id'], 'sim:gdmt_pharmacy_slot_1')
        self.assertTrue(r1['at'].startswith('2030-05-28T'))
        # the seed is workspace + patient + program + as_of: another plan date draws again
        other = SimulatedCounterparty(prog, prog['vendor_slots'][0], workspace='w', patient_id='P', as_of='2030-06-01')
        self.assertNotEqual(other.seed, cp.seed)

    def test_rates_differ_by_program_and_respond_to_patient_facts(self):
        cat = catmod.load()
        progs = {p['id']: p for p in cat['programs']}
        self.assertNotEqual(progs['pharmacy_gdmt_titration']['counterparty']['p_decline'],
                            progs['access_virtual_pt_msk']['counterparty']['p_decline'])
        pt = progs['access_virtual_pt_msk']
        base = SimulatedCounterparty(pt, pt['vendor_slots'][0], workspace='w', patient_id='P', as_of=AS_OF, facts={})
        recent = SimulatedCounterparty(pt, pt['vendor_slots'][0], workspace='w', patient_id='P', as_of=AS_OF, facts={'ed_within_30d': True})
        self.assertLess(recent.p('decline', pt['counterparty']['p_decline'])[0], base.p('decline', pt['counterparty']['p_decline'])[0])
        g = progs['pharmacy_gdmt_titration']
        barrier = SimulatedCounterparty(g, None, workspace='w', patient_id='P', as_of=AS_OF, facts={'barrier_on_target': True})
        self.assertGreater(barrier.p('decline', g['counterparty']['p_decline'])[0], g['counterparty']['p_decline'])
        nocov = SimulatedCounterparty(g, None, workspace='w', patient_id='P', as_of=AS_OF, facts={'no_active_coverage': True})
        self.assertEqual(nocov.submit({'request_id': 'X', 'step': 'eligibility_check', 'day': 1})['outcome'], 'coverage_denied')
        # a population of patients gives a mixed funnel, not all-success
        plan = {'as_of': AS_OF, 'program_kind': 'virtual_pt', 'patients': [
            {'patient_id': f'P{i}', 'route': 'enroll', 'program_id': 'plan_benefit_virtual_pt',
             'vendor_slot': {'slot_id': 'plan_virtual_pt_slot_1'}, 'coverage': {'active': {'value': True}, 'product': {'value': 'commercial'}}}
            for i in range(200)]}
        s = actmod.act(plan, workspace='w')['summary']
        for k in ('unreachable', 'declined', 'enrolled', 'completed', 'no_show_ever', 'prior_auth_denied'):
            self.assertGreater(s['outcomes'].get(k, 0), 0, k)
        self.assertLess(s['outcomes']['completed'], 100)


class Followups(Base):
    def test_prior_auth_denial_appeal_upheld_creates_followup_work(self):
        cat = forced(prior_auth={'when': 'before_enrollment', 'products': ['commercial'], 'p_required': 1.0, 'p_denied': 1.0,
                                 'denial_reasons': ['conservative care not documented'], 'appeal': {'p_overturned': 0, 'days': 10}})
        plan, res = self.run_plan('virtual_pt', ['PT1', 'PT3'], cat=cat)  # PT3: commercial -> plan-benefit PT
        it = backbone.items(self.ws.dir, patient_id='PT3', kind='plan_item')['items'][0]
        self.assertEqual(it['state'], 'prior_auth_denied')
        full = backbone.item(self.ws.dir, it['id'])
        steps = [(s['step'], s['receipt']['outcome']) for s in full['item']['action']['steps'] if s.get('receipt')]
        self.assertIn(('prior_auth', 'denied'), steps)
        self.assertIn(('appeal', 'upheld'), steps)
        types = {(f['type'], f['state']) for f in full['followup_items']}
        self.assertIn(('appeal', 'resolved'), types)
        self.assertIn(('alternate_program', 'open'), types)
        alt = next(f for f in full['item']['action']['followups'] if f['type'] == 'alternate_program')
        self.assertTrue(alt['alternatives'])
        self.assertEqual(res['summary']['outcomes']['appeal_upheld'], 1)

    def test_appeal_overturned_continues_to_enrollment(self):
        cat = forced(prior_auth={'when': 'before_enrollment', 'products': ['commercial'], 'p_required': 1.0, 'p_denied': 1.0,
                                 'appeal': {'p_overturned': 1.0, 'days': 10}})
        self.run_plan('virtual_pt', ['PT3'], cat=cat)
        it = backbone.items(self.ws.dir, patient_id='PT3', kind='plan_item')['items'][0]
        self.assertEqual(it['state'], 'completed')
        fu = backbone.items(self.ws.dir, patient_id='PT3', kind='followup')['items']
        self.assertEqual([(f['type'], f['state']) for f in fu], [('appeal', 'resolved')])

    def test_declined_unreachable_and_denied_never_end_silently(self):
        for cp, state, ftype in (({'p_decline': 1.0}, 'declined', 'pcp_notice'),
                                 ({'outreach': {'attempts': 2, 'attempt_days': [1, 2], 'p_reach': 0}}, 'unreachable', 'pcp_notice'),
                                 ({'p_eligibility_denied': 1.0}, 'eligibility_denied', 'alternate_program')):
            with self.subTest(state=state):
                tmp = tempfile.TemporaryDirectory()
                try:
                    ws = workspace(tmp.name)
                    cat = forced(**cp)
                    plan = router.route(ws.semantic_path, ['HF4'], 'pharmacy_gdmt', AS_OF, catalog=cat)
                    backbone.run(ws.dir, plan, workspace=ws.slug, semantic_path=ws.semantic_path, catalog=cat)
                    got = backbone.items(ws.dir, patient_id='HF4')['items']
                    self.assertEqual(next(i for i in got if i['kind'] == 'plan_item')['state'], state)
                    self.assertIn(ftype, [i['type'] for i in got if i['kind'] == 'followup'])
                finally:
                    tmp.cleanup()

    def test_no_show_then_reschedule_then_attended(self):
        cat = forced(p_no_show=1.0, p_reschedule_after_no_show=1.0, p_second_no_show=0)
        self.run_plan('virtual_pt', ['PT1'], cat=cat)
        it = backbone.items(self.ws.dir, patient_id='PT1', kind='plan_item')['items'][0]
        steps = [(s['step'], s['attempt'], s['receipt']['outcome'])
                 for s in backbone.item(self.ws.dir, it['id'])['item']['action']['steps'] if s.get('receipt')]
        i = steps.index(('first_visit', 1, 'no_show'))
        self.assertEqual(steps[i + 1:i + 3], [('reschedule', 1, 'rescheduled'), ('first_visit', 2, 'attended')])
        self.assertEqual(it['state'], 'completed')

    def test_through_withholds_receipts_until_due(self):
        cat = forced()
        _, r1 = self.run_plan('virtual_pt', ['PT1'], cat=cat, through='2030-05-29')
        it = backbone.items(self.ws.dir, patient_id='PT1', kind='plan_item')['items'][0]
        self.assertNotEqual(it['state'], 'completed')
        steps = backbone.item(self.ws.dir, it['id'])['item']['action']['steps']
        self.assertEqual(steps[-1]['status'], 'awaiting_counterparty')
        self.assertIsNone(steps[-1]['receipt'])
        _, r2 = self.run_plan('virtual_pt', ['PT1'], cat=cat)  # no cut: the rest arrives as new receipts, nothing retracted
        self.assertEqual(r2['items'].get('updated'), 1)
        got = backbone.item(self.ws.dir, it['id'])
        self.assertEqual(got['item']['state'], 'completed')
        self.assertIn('receipts_received', [e['action'] for e in got['events']])
        self.assertEqual(r2['decision_id'], r1['decision_id'])


class Completion(Base):
    def test_completion_from_pt_claim_and_from_fills_and_reversal(self):
        cat = forced()
        # PT6: commercial, ED low back pain 2030-03-01, PT evaluation claim 2030-04-10 (after a plan dated 2030-03-15)
        add(self.db, 'INSERT INTO patient(patient_id, birth_date, preferred_language) VALUES (?,?,?)', [('PT6', '1970-01-01', 'en')])
        add(self.db, 'INSERT INTO enrollment_span(record_id, patient_id, member_id, payer_id, product, line_of_business, coverage_start, '
                     'attribution_practice_id, attribution_npi, available_date) VALUES (?,?,?,?,?,?,?,?,?,?)',
            [('ES-PT6', 'PT6', 'N6', 'PAY-COM', 'commercial', 'commercial', '2026-01-01', 'PRAC-B', '2222222222', '2026-01-05')])
        add(self.db, 'INSERT INTO payer_claim(payer_claim_id, record_id, patient_id, place_of_service, service_from, service_to, '
                     'counts_in_metrics) VALUES (?,?,?,?,?,?,1)', [('CL-ED6', 'RC-ED6', 'PT6', '23', '2030-03-01', '2030-03-01')])
        add(self.db, 'INSERT INTO payer_claim_diagnosis VALUES (?,?,?,?,?)', [('CL-ED6', 'PT6', '2030-03-01', 1, 'M54.50')])
        add(self.db, 'INSERT INTO payer_claim_line VALUES (?,?,?,?,?,?,?,1)', [('LN-PT6', 'CL-PT6', 'PT6', 1, '2030-04-10', '97161', None)])
        _, res = self.run_plan('virtual_pt', ['PT6'], as_of='2030-03-15', cat=cat)
        it = backbone.item(self.ws.dir, backbone.items(self.ws.dir, patient_id='PT6', kind='plan_item')['items'][0]['id'])['item']
        self.assertEqual((it['closure']['status'], it['closure']['source']), ('closed', 'workspace_data'))
        self.assertEqual(it['closure']['evidence'], ['payer_claim_line:LN-PT6'])
        self.assertEqual(res['completion']['closure_rate'], 1.0)
        # HF1 (missing mra + sglt2i at 2030-04-01): both filled after enrollment -> closed from fills
        add(self.db, 'INSERT INTO fill VALUES (?,?,?,?,?,?,?,?,?)',
            [(91, 'HF1', '2030-04-15', 30, '2030-05-14', 'mra', 'mra', 'spironolactone 25 MG', 'FR-91'),
             (92, 'HF1', '2030-04-16', 30, '2030-05-15', 'sglt2_inhibitor', 'sglt2_inhibitor', 'empagliflozin 10 MG', 'FR-92')])
        _, res = self.run_plan('pharmacy_gdmt', ['HF1'], as_of='2030-04-01', cat=cat)
        hf1 = backbone.items(self.ws.dir, patient_id='HF1', kind='plan_item')['items'][0]
        got = backbone.item(self.ws.dir, hf1['id'])['item']
        self.assertEqual((got['closure']['status'], got['closure']['source']), ('closed', 'workspace_data'))
        self.assertEqual(set(got['closure']['pillars']), {'mra', 'sglt2i'})
        # the MRA fill is reversed in a later delivery: restage -> the closure reopens with the reversal as evidence
        db = sqlite3.connect(self.db)
        db.execute('DELETE FROM fill WHERE fill_id = 91')
        db.execute('CREATE TABLE payer_pharmacy_claim(record_id TEXT, patient_id TEXT, fill_date TEXT, drug_name TEXT, claim_status TEXT, '
                   'reversal_of_record_id TEXT, reversed_by_record_id TEXT, counts_as_fill INTEGER)')
        db.executemany('INSERT INTO payer_pharmacy_claim VALUES (?,?,?,?,?,?,?,?)', [
            ('RX-91', 'HF1', '2030-04-15', 'spironolactone 25 MG', 'paid', None, 'RX-91R', 0),
            ('RX-91R', 'HF1', '2030-04-18', 'spironolactone 25 MG', 'reversed', 'RX-91', None, 0)])
        db.commit()
        db.close()
        _, res = self.run_plan('pharmacy_gdmt', ['HF1'], as_of='2030-04-01', cat=cat)
        got = backbone.item(self.ws.dir, hf1['id'])
        self.assertEqual(got['item']['closure']['status'], 'reopened')
        self.assertIn('payer_pharmacy_claim:RX-91R', got['item']['closure']['evidence'])
        ev = [e for e in got['events'] if e['action'] == 'closure_evaluated']
        self.assertEqual([(e['from'], e['to']) for e in ev], [(None, 'closed'), ('closed', 'reopened')])

    def test_window_beyond_the_data_uses_simulated_attendance(self):
        cat = forced()
        _, res = self.run_plan('virtual_pt', ['PT1'], cat=cat)  # plan as_of = data as_of: every visit is in the future
        it = backbone.item(self.ws.dir, backbone.items(self.ws.dir, patient_id='PT1', kind='plan_item')['items'][0]['id'])['item']
        self.assertEqual((it['closure']['status'], it['closure']['source']), ('closed', 'simulated_attendance'))
        self.assertTrue(it['closure']['evidence'][0].startswith('receipt:RCPT-'))
        self.assertEqual(res['completion']['source:simulated_attendance'], 1)


class BarrierScopeAndRecency(unittest.TestCase):
    POLICY = catmod.load()['barrier_policy']

    def db(self, rows):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE therapy_barrier(patient_id TEXT, drug_class TEXT, gdmt_pillar TEXT, drug_key TEXT, barrier_type TEXT, '
                   'detail TEXT, event_date TEXT, last_date TEXT, mentions INTEGER, evidence_text TEXT, record_id TEXT, source_type TEXT, '
                   'method TEXT, confidence REAL, assertion TEXT, negated INTEGER, hypothetical INTEGER, experiencer TEXT)')
        db.execute('CREATE TABLE record(record_id TEXT, payload TEXT)')
        for r in rows:
            db.execute('INSERT INTO therapy_barrier VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,0.9,?,0,0,?)',
                       (r['pid'], r['cls'], r['pillar'], r.get('drug'), r['type'], r.get('detail', 'x'), r['date'], r.get('last', r['date']),
                        r.get('text', 'evidence'), r.get('rid', 'R1'), 'clinical_note', 'rules', 'affirmed', 'patient'))
            if r.get('note'):
                db.execute('INSERT INTO record VALUES (?,?)', (r.get('rid', 'R1'), json.dumps({'text': r['note']})))
        return db

    def test_losartan_intolerance_keeps_rasi_open_with_other_agents(self):
        note = 'HPI: doing ok. Patient reports losartan 50 MG is not being used: did not like how it made them feel. Plan: f/u.'
        ev = 'Patient reports losartan 50 MG is not being used: did not like how it made them feel.'
        db = self.db([{'pid': 'P', 'cls': 'arb', 'pillar': 'rasi', 'drug': 'losartan', 'type': 'intolerance_side_effect',
                       'date': '2029-09-10', 'text': ev, 'note': note}])
        a = barriers.assess(db, ['P'], '2030-05-27', self.POLICY)['P']['rasi']
        self.assertEqual(a['status'], 'open_with_barrier')
        self.assertEqual(a['excluded_agents'], ['losartan'])
        self.assertIn('valsartan', a['usable_agents'])
        self.assertIn('sacubitril valsartan', a['usable_agents'])
        self.assertIn('lisinopril', a['usable_agents'])
        b = a['barriers'][0]
        self.assertEqual((b['scope'], b['record_id']), ('agent', 'R1'))
        self.assertEqual(note[b['span_start']:b['span_end']], ev)

    def test_ace_angioedema_excludes_acei_and_flags_arni(self):
        db = self.db([{'pid': 'P', 'cls': 'ace_inhibitor', 'pillar': 'rasi', 'drug': 'lisinopril', 'type': 'allergy_angioedema',
                       'date': '2029-01-01'}])
        a = barriers.assess(db, ['P'], '2030-05-27', self.POLICY)['P']['rasi']
        self.assertEqual(a['status'], 'open_with_barrier')
        self.assertIn('enalapril', a['excluded_agents'])
        self.assertNotIn('sacubitril valsartan', a['usable_agents'])
        self.assertIn('sacubitril valsartan', a['remaining_agents'])  # possible only after clinician review
        self.assertIn('losartan', a['usable_agents'])
        self.assertIn('contraindicated', a['flags'][0]['flag'])

    def test_class_level_and_pillar_wide_barriers_explain_the_gap(self):
        db = self.db([{'pid': 'P', 'cls': 'mra', 'pillar': 'mra', 'drug': None, 'type': 'intolerance_side_effect', 'date': '2029-06-01'},
                      {'pid': 'Q', 'cls': 'mra', 'pillar': 'mra', 'drug': 'spironolactone', 'type': 'contraindication_lab',
                       'detail': 'hyperkalemia K 5.9', 'date': '2030-02-01'}])
        out = barriers.assess(db, ['P', 'Q'], '2030-05-27', self.POLICY)
        self.assertEqual(out['P']['mra']['status'], 'explained')  # no agent named: the class is excluded
        self.assertEqual(out['Q']['mra']['status'], 'explained')  # a lab contraindication explains the pillar

    def test_recency_window_and_restatement(self):
        db = self.db([{'pid': 'OLD', 'cls': 'arb', 'pillar': 'rasi', 'drug': 'losartan', 'type': 'intolerance_side_effect', 'date': '2026-09-10'},
                      {'pid': 'RESTATED', 'cls': 'arb', 'pillar': 'rasi', 'drug': 'losartan', 'type': 'intolerance_side_effect',
                       'date': '2026-09-10', 'last': '2029-11-01'},
                      {'pid': 'LAB', 'cls': 'mra', 'pillar': 'mra', 'drug': 'spironolactone', 'type': 'contraindication_lab', 'date': '2028-01-01'},
                      {'pid': 'FUTURE', 'cls': 'mra', 'pillar': 'mra', 'drug': 'spironolactone', 'type': 'contraindication_lab', 'date': '2030-08-01'}])
        out = barriers.assess(db, ['OLD', 'RESTATED', 'LAB', 'FUTURE'], '2030-05-27', self.POLICY)
        self.assertEqual(out['OLD']['rasi']['status'], 'stale_only')
        self.assertEqual(out['OLD']['rasi']['stale_barriers'][0]['flag'], 'stale_barrier')
        self.assertEqual(out['RESTATED']['rasi']['status'], 'open_with_barrier')
        self.assertEqual(out['RESTATED']['rasi']['barriers'][0]['effective_date'], '2029-11-01')
        self.assertEqual(out['LAB']['mra']['status'], 'stale_only')  # lab contraindications: 365-day window
        self.assertNotIn('FUTURE', out)
        # as_of earlier: the 2026 barrier is current again
        self.assertEqual(barriers.assess(db, ['OLD'], '2028-06-01', self.POLICY)['OLD']['rasi']['status'], 'open_with_barrier')

    def test_barrier_routes_to_review_and_rides_into_the_action(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            ws = workspace(tmp.name)
            db = sqlite3.connect(ws.semantic_path)
            db.execute('CREATE TABLE therapy_barrier(patient_id TEXT, drug_class TEXT, gdmt_pillar TEXT, drug_key TEXT, barrier_type TEXT, '
                       'detail TEXT, event_date TEXT, last_date TEXT, mentions INTEGER, evidence_text TEXT, record_id TEXT, source_type TEXT, '
                       'method TEXT, confidence REAL, assertion TEXT, negated INTEGER, hypothetical INTEGER, experiencer TEXT)')
            db.executemany('INSERT INTO therapy_barrier VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,0.9,?,0,0,?)', [
                ('HF4', 'arb', 'rasi', 'losartan', 'intolerance_side_effect', 'dizziness', '2029-10-01', '2029-10-01', 'losartan: dizziness',
                 'NB-9', 'clinical_note', 'rules', 'affirmed', 'patient'),
                ('HF1', 'arb', 'rasi', 'losartan', 'cost_access', 'could not afford', '2026-01-01', '2026-01-01', 'old', 'NB-8',
                 'clinical_note', 'rules', 'affirmed', 'patient')])
            db.commit()
            db.close()
            plan = router.route(ws.semantic_path, ['HF1', 'HF4'], 'pharmacy_gdmt', AS_OF)
            rows = {r['patient_id']: r for r in plan['patients']}
            self.assertEqual(rows['HF4']['program_id'], 'pharmd_barrier_review')
            self.assertIn('rasi', rows['HF4']['gap']['missing_pillars']['value'])  # still an open gap: another agent is possible
            self.assertEqual(rows['HF1']['program_id'], 'pharmacy_gdmt_titration')  # HF1's barrier is on a pillar it is on
            res = backbone.run(ws.dir, plan, workspace=ws.slug, semantic_path=ws.semantic_path)
            it = backbone.items(ws.dir, patient_id='HF4', kind='plan_item')['items'][0]
            action = backbone.item(ws.dir, it['id'])['item']['action']
            self.assertEqual(action['agent'], 'Agere PharmD barrier review agent')
            self.assertEqual(action['steps'][0]['step'], 'chart_review')
            b = action['barriers'][0]
            self.assertEqual((b['pillar'], b['drug_key'], b['record_id']), ('rasi', 'losartan', 'NB-9'))
            self.assertIn('still possible', b['suggested_action'])
            self.assertEqual(res['summary']['barrier_reviews'], 1)
            self.assertEqual(res['summary']['actions_with_barriers'], 1)
        finally:
            tmp.cleanup()


class ApiRoutes(Base):
    def test_plan_then_work_and_decision_routes(self):
        code, out = workspace_api.handle('POST', '/api/workspaces/plan-clinic/plan', {'program_kind': 'virtual_pt'}, root=self.root)
        self.assertEqual(code, 200)
        ex = out['plan']['execution']
        self.assertFalse(ex['replay'])
        code, again = workspace_api.handle('POST', '/api/workspaces/plan-clinic/plan', {'program_kind': 'virtual_pt'}, root=self.root)
        self.assertTrue(again['plan']['execution']['replay'])
        code, lst = workspace_api.handle('GET', '/api/workspaces/plan-clinic/work?program_kind=virtual_pt&kind=plan_item', root=self.root)
        self.assertEqual(code, 200)
        self.assertEqual(lst['total'], ex['work_items'])
        code, one = workspace_api.handle('GET', f"/api/workspaces/plan-clinic/work/{lst['items'][0]['id']}", root=self.root)
        self.assertEqual(code, 200)
        self.assertTrue(all(s.get('receipt') for s in one['item']['action']['steps'] if s['status'] == 'done'))
        self.assertEqual(one['events'][0]['action'], 'created')
        code, decs = workspace_api.handle('GET', '/api/workspaces/plan-clinic/decisions', root=self.root)
        self.assertEqual([d['id'] for d in decs['decisions']], [ex['decision_id']])
        code, dec = workspace_api.handle('GET', f"/api/workspaces/plan-clinic/decisions/{ex['decision_id']}", root=self.root)
        self.assertEqual(code, 200)
        self.assertEqual(dec['summary']['funnel'], ex['summary']['funnel'])
        self.assertEqual(workspace_api.handle('GET', '/api/workspaces/plan-clinic/work/WI-nope', root=self.root)[0], 404)
        self.assertEqual(workspace_api.handle('POST', '/api/workspaces/plan-clinic/plan', {'program_kind': 'virtual_pt', 'through': 'soon'},
                                              root=self.root)[0], 400)


if __name__ == '__main__':
    unittest.main()
