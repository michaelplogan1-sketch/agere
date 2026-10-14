"""Loop closure: planted open loops on a fixture warehouse, evidence types, close rates, routing (no stealing), wiring, real-v20 smoke."""
import json, os, re, shutil, sqlite3, subprocess, tempfile, unittest
from pathlib import Path

from clinical_core.agere.clinical import (analysis_graph, capability_registry, graph_contracts, graph_planner, local_compiler,
                                          loop_closure as lc, semantic_layer, source_profile)
from clinical_core.agere.clinical.analysis_graph import build_answer, validate
from test_aggregate_explain import build

ROOT = Path(__file__).resolve().parent
AS_OF = '2030-05-27'
REAL_V20 = Path(os.environ.get('SAFETY_STACK') or next(
    (p / 'artifacts' / 'agere-healthcare-v20' / 'stack' for p in (ROOT, *ROOT.parents) if (p / 'artifacts' / 'agere-healthcare-v20' / 'stack').exists()),
    ROOT / 'artifacts' / 'agere-healthcare-v20' / 'stack'))

# ---- fixture warehouse: the semantic-layer tables the engine reads --------------------------------------------------
DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY, age_reported INTEGER, sex_reported TEXT, pcp_clinician_id TEXT, pcp_practice_id TEXT);
CREATE TABLE measurement(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, value_canonical REAL, unit_canonical TEXT,
  abnormal_flag TEXT, event_date TEXT, recorded_date TEXT, available_date TEXT);
CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type TEXT, type_concept TEXT, status TEXT, event_date TEXT, organization_id TEXT,
  attending_clinician_id TEXT, principal_icd10 TEXT);
CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT, available_date TEXT,
  organization_id TEXT, payload TEXT);
CREATE TABLE link(record_id TEXT, stream TEXT, field TEXT, target_record_id TEXT);
CREATE INDEX link_target ON link(target_record_id, stream);
CREATE TABLE note_fact(patient_id TEXT, record_id TEXT, stream TEXT, fact_type TEXT, value, unit TEXT, negated INTEGER, temporality TEXT,
  event_date TEXT, evidence_text TEXT, qualifier TEXT);
CREATE TABLE medication(record_id TEXT, patient_id TEXT, evidence TEXT, drug_class TEXT, event_date TEXT);
CREATE TABLE clinician(clinician_id TEXT PRIMARY KEY, organization_id TEXT, specialty TEXT);
CREATE TABLE organization(organization_id TEXT PRIMARY KEY, organization_type TEXT, display_name TEXT);
"""


def fixture():
    root = Path(tempfile.mkdtemp())
    db = sqlite3.connect(root / 'semantic.sqlite3')
    db.executescript(DDL)
    db.execute("INSERT INTO meta VALUES ('version','semantic-layer/13'), ('as_of', ?)", (AS_OF,))
    for i in range(1, 9):
        db.execute('INSERT INTO patient VALUES (?,?,?,?,?)', (f'p{i}', 60, 'F', 'drA' if i <= 4 else 'drB', 'PR1' if i <= 4 else 'PR2'))
    db.executemany('INSERT INTO clinician VALUES (?,?,?)', [('drA', 'PR1', 'Family Medicine'), ('drB', 'PR2', 'Family Medicine'),
                                                            ('cardX', 'SPEC', 'Cardiology'), ('drZ', 'PR1', 'Internal Medicine')])
    db.executemany('INSERT INTO organization VALUES (?,?,?)', [('PR1', 'primary_care', 'Practice 1'), ('PR2', 'primary_care', 'Practice 2'),
                                                               ('SPEC', 'independent_specialty_practice', 'Specialty')])
    labs = [('L1', 'p1', 'potassium', 6.5, 'mmol/L', 'H', '2030-03-01'),   # critical, nothing after -> open past due
            ('L2', 'p2', 'potassium', 6.4, 'mmol/L', 'H', '2030-03-01'),   # critical, repeat next day -> closed (repeat_test)
            ('L2b', 'p2', 'potassium', 4.6, 'mmol/L', 'N', '2030-03-02'),
            ('L3', 'p3', 'hemoglobin', 8.5, 'g/dL', 'L', '2030-02-01'),    # abnormal, visit day 19 -> closed (visit)
            ('L4', 'p4', 'glucose', 300, 'mg/dL', 'H', '2030-02-01'),      # abnormal, first evidence a note instruction on day 32 -> closed late
            ('L5', 'p5', 'hba1c', 9.5, '%', 'H', '2030-01-10'),            # abnormal, decision record cites it day 5 -> structured review
            ('L6', 'p5', 'potassium', 4.0, 'mmol/L', 'N', '2030-01-10'),   # normal: no item
            ('L7', 'p6', 'hba1c', 7.2, '%', 'H', '2030-01-10')]            # flagged H but below the action limit: no item (actionable basis)
    for rid, pid, c, v, u, f, d in labs:
        db.execute('INSERT INTO measurement VALUES (?,?,?,?,?,?,?,?,?,?)', (rid, pid, 'labs', c, v, u, f, d, d, d))
        db.execute('INSERT INTO record VALUES (?,?,?,?,?,?,?,?)', (rid, pid, 'labs', d, d, d, 'PR1',
                                                                  json.dumps({'laboratory_order_record_id': 'O2'} if rid == 'L2' else {})))
    rec = lambda rid, pid, stream, d, payload: db.execute('INSERT INTO record VALUES (?,?,?,?,?,?,?,?)', (rid, pid, stream, d, d, d, 'PR1', json.dumps(payload)))
    rec('O2', 'p2', 'laboratory_orders', '2030-02-28', {'ordering_clinician_id': 'drZ', 'requested_tests_local': ['BMP']})
    db.execute("INSERT INTO link VALUES ('L2','labs','laboratory_order_record_id','O2')")
    rec('D5', 'p5', 'prescription_reassessment_decisions', '2030-01-15', {'status': 'reviewed'})
    db.execute("INSERT INTO link VALUES ('D5','prescription_reassessment_decisions','reviewed_laboratory_report_record_ids','L5')")
    visits = [('e1', 'p3', 'office', 'primary_care_visit', '2030-02-20', 'PR1', 'drA'), ('e2', 'p4', 'office', 'primary_care_visit', '2030-03-10', 'PR1', 'drA'),
              ('e3', 'p8', 'specialty_followup', 'specialist_visit', '2030-02-01', 'SPEC', 'cardX'),
              ('e4', 'p3', 'office', 'primary_care_visit', '2029-12-01', 'PR1', 'drA')]
    for e, pid, typ, tc, d, org, clin in visits:
        db.execute('INSERT INTO visit VALUES (?,?,?,?,?,?,?,?,?)', (e, pid, typ, tc, 'completed', d, org, clin, None))
    rec('N4', 'p4', 'notes', '2030-04-15', {'text': 'Discussed the glucose result and diet.'})
    # referrals: p6 open, p7 declined, p8 completed by a visit at the receiving organisation
    rec('R6', 'p6', 'referrals', '2030-01-05', {'status': 'requested', 'specialty': 'Cardiology', 'receiving_organization_id': 'SPEC', 'requesting_clinician_id': 'drB'})
    rec('R7', 'p7', 'referrals', '2030-01-05', {'status': 'requested', 'referral_type': 'dental_care_access', 'requesting_clinician_id': 'drB'})
    rec('U7', 'p7', 'referral_status_updates', '2030-01-20', {'status': 'declined_further_contact'})
    db.execute("INSERT INTO link VALUES ('U7','referral_status_updates','referral_record_id','R7')")
    rec('R8', 'p8', 'referrals', '2030-01-05', {'status': 'requested', 'specialty': 'Cardiology', 'receiving_organization_id': 'SPEC', 'requesting_clinician_id': 'drB'})
    # lab orders: p6 never resulted; p7 resulted two days later
    rec('O6', 'p6', 'laboratory_orders', '2030-02-01', {'ordering_clinician_id': 'drB', 'requested_tests_local': ['CBC']})
    rec('O7', 'p7', 'laboratory_orders', '2030-02-01', {'ordering_clinician_id': 'drB', 'requested_tests_local': ['BMP']})
    rec('RN7', 'p7', 'laboratory_result_notifications', '2030-02-03', {'laboratory_order_record_id': 'O7'})
    db.execute("INSERT INTO link VALUES ('RN7','laboratory_result_notifications','laboratory_order_record_id','O7')")
    # screens: positive FIT (p8, not yet due), BI-RADS 0 (p3, diagnostic session 10 days later)
    rec('F8', 'p8', 'cancer_screening_results', '2030-01-10', {'test': 'FIT', 'method': 'fecal_immunochemical_test', 'result': 'positive', 'performed_date': '2030-01-10'})
    rec('M3', 'p3', 'cancer_screening_results', '2030-01-10', {'test': 'bilateral_screening_mammography', 'birads_category': 0, 'performed_date': '2030-01-10'})
    rec('M3r', 'p3', 'received_screening_reports', '2030-01-10', {'test': 'bilateral_screening_mammography', 'birads_category': 0, 'performed_date': '2030-01-10'})
    rec('BS3', 'p3', 'breast_diagnostic_sessions', '2030-01-20', {'status': 'performed'})
    # an abnormal report never reviewed (p2), and a normal one (p1)
    rec('IR2', 'p2', 'imaging_reports', '2030-02-01', {'impression': 'pulmonary_edema_pattern', 'text': 'Pulmonary vascular prominence with small pleural effusions.'})
    rec('IR1', 'p1', 'imaging_reports', '2030-02-01', {'impression': 'no_acute_osseous_abnormality', 'text': 'No acute fracture or dislocation identified.'})
    # a note follow-up instruction: visit in 14 days (p4 visits on day 5 -> closed); it is also the first acknowledgement of L4 (late)
    db.execute("INSERT INTO note_fact VALUES ('p4','NF4','notes','follow_up',14,NULL,0,'current','2030-03-05','a visit is requested within 7-14 days','visit')")
    db.commit(); db.close()
    return root


class Rules(unittest.TestCase):
    def test_lab_tiers(self):
        self.assertEqual(lc.lab_tier('potassium', 6.3), 'critical')
        self.assertEqual(lc.lab_tier('potassium', 5.8), 'abnormal')
        self.assertIsNone(lc.lab_tier('potassium', 5.0))
        self.assertIsNone(lc.lab_tier('hba1c', 7.5, 'H'))                         # flagged high, not past the action limit
        self.assertEqual(lc.lab_tier('hba1c', 7.5, 'H', 'reference_range'), 'abnormal')
        self.assertEqual(lc.lab_tier('wbc', 8, 'HH'), 'critical')                   # received critical flag wins
        self.assertEqual(lc.lab_tier('sodium', 118), 'critical')

    def test_actions_and_intervals(self):
        self.assertEqual(lc.action_of('Arrange repeat colonoscopy with revised preparation'), 'colonoscopy')
        self.assertEqual(lc.action_of('Diagnostic mammography with targeted ultrasound as indicated.'), 'imaging')
        self.assertEqual(lc.action_of('Refer to cardiology'), 'specialist')
        self.assertEqual(lc.interval_days('Follow-up CT in 3 months'), 90)
        self.assertEqual(lc.interval_days('no interval'), 90)
        self.assertEqual(lc.grace(14), 21)
        self.assertEqual(lc.grace(180), 225)

    def test_classify(self):
        item = {'due_date': '2030-03-01'}
        self.assertEqual(lc.classify({**item, 'closed_date': '2030-02-01'}, AS_OF), 'closed_on_time')
        self.assertEqual(lc.classify({**item, 'closed_date': '2030-04-01'}, AS_OF), 'closed_late')
        self.assertEqual(lc.classify(item, AS_OF), 'open_overdue')
        self.assertEqual(lc.classify(item, '2030-02-01'), 'pending')
        self.assertEqual(lc.classify({**item, 'declined': True}, AS_OF), 'declined')

    def test_parameters(self):
        p = lc.validate({})
        self.assertEqual((p['focus'], p['group_by'], p['evidence'], p['window_days']), ('all', 'practice', 'any', 365))
        self.assertEqual(lc.validate({'focus': 'referrals'})['item_types'], ['referral'])
        self.assertEqual(lc.validate({'windows': {'abnormal_result': 14}})['windows']['abnormal_result'], 14)
        for bad in ({'focus': 'x'}, {'item_types': ['nope']}, {'windows': {'abnormal_result': 0}}, {'concept': 'unobtainium'},
                    {'period': {'start': '2030-13-01'}}, {'window_days': 5}, {'extra': 1}, {'group_by': 'payer'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                lc.validate(bad)


class Engine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = fixture()
        cls.report = lc.execute(cls.path, None, {}, AS_OF)
        db = sqlite3.connect(cls.path / 'semantic.sqlite3')
        cls.full = lc.run(db, None, {'top': 1000}, AS_OF)
        db.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.path, ignore_errors=True)

    def items_of(self, params=None):
        """Every item with its status (re-runs the pure pipeline to see closed items too)."""
        db = sqlite3.connect(self.path / 'semantic.sqlite3')
        try:
            p = lc.validate(params or {}); cap = lc.capability(db)
            start, end = lc._period(p, AS_OF)
            from clinical_core.agere.clinical import safety_data as sd
            people = sd.patients(db)
            items = (lc.lab_items(db, None, people, p, start, end) + lc.screen_items(db, None, people, p, start, end) +
                     lc.recommendation_items(db, None, people, p, start, end, cap) + lc.order_items(db, None, people, p, start, end, cap) +
                     lc.referral_items(db, None, people, p, start, end, cap) + lc.report_items(db, None, people, p, start, end, cap))
            items = lc.dedupe([i for i in items if i['item_type'] in p['item_types']])
            data = lc.Data(db, {i['patient_id'] for i in items}, cap)
            links = {}
            for kind in ('review', 'test_order', 'referral'):
                mine = [i for i in items if (i['item_type'] if i['item_type'] in ('test_order', 'referral') else 'review') == kind]
                for t, rows in lc._links_dated(db, {t for i in mine for t in i['targets']}, lc.citing_streams(cap['streams'], kind)).items():
                    links.setdefault(t, []).extend(rows)
            lc.close(items, data, links, p, AS_OF)
            return {i['item_id']: i for i in items}
        finally:
            db.close()

    def test_planted_loops_and_evidence(self):
        it = self.items_of()
        expect = {'L1': ('critical_result', 'open_overdue', None), 'L2': ('critical_result', 'closed_on_time', 'repeat_test'),
                  'L3': ('abnormal_result', 'closed_on_time', 'visit'), 'L4': ('abnormal_result', 'closed_late', 'note_acknowledgement'),
                  'L5': ('abnormal_result', 'closed_on_time', 'structured_review'), 'R6': ('referral', 'open_overdue', None),
                  'R7': ('referral', 'declined', None), 'R8': ('referral', 'closed_on_time', 'expected_action'),
                  'O6': ('test_order', 'open_overdue', None), 'O7': ('test_order', 'closed_on_time', 'expected_action'),
                  'O2': ('test_order', 'closed_on_time', 'expected_action'),
                  'F8': ('positive_screen', 'pending', None), 'M3': ('positive_screen', 'closed_on_time', 'expected_action'),
                  'IR2': ('report_review', 'open_overdue', None), 'NF4#visit': ('recommendation', 'closed_on_time', 'expected_action')}
        for k, (t, status, ev) in expect.items():
            with self.subTest(item=k):
                self.assertIn(k, it)
                self.assertEqual((it[k]['item_type'], it[k]['status'], it[k].get('closed_by')), (t, status, ev))
        self.assertNotIn('L6', it); self.assertNotIn('L7', it); self.assertNotIn('IR1', it)   # normal results / reports open no loop
        self.assertNotIn('M3r', it); self.assertIn('M3r', it['M3']['targets'])                # a received copy merges into the original
        self.assertEqual(it['L1']['tier'], 'critical')
        self.assertEqual(it['L1']['due_date'], '2030-03-08')
        self.assertEqual(it['L1']['days_overdue'], 80)
        self.assertEqual(it['L2']['owner_id'], 'drZ')        # ordering clinician through the order link
        self.assertEqual(it['F8']['due_date'], '2030-07-09')  # FIT: 180 days; p8's cardiology visit does not close it

    def test_report_open_loops_and_rates(self):
        r = self.full
        self.assertEqual(r['status'], 'answered')
        self.assertEqual(r['items_total'], 15)
        self.assertEqual(r['status_counts']['open_overdue'], 4)
        open_ids = [x['item_id'] for x in r['open_loops']]
        self.assertEqual(open_ids[0], 'L1')                   # critical first
        self.assertEqual(set(open_ids), {'L1', 'R6', 'O6', 'IR2'})
        l1 = r['open_loops'][0]
        self.assertEqual((l1['owner'], l1['practice'], l1['days_overdue']), ('drA', 'PR1', 80))
        by = {g['group']: g for g in r['by_group']}
        # PR1: L1 open, L2/L3/L5? (L5 is p5 -> PR2) ... count per practice from the planted table
        self.assertEqual(by['PR1']['open_overdue'], 2)        # L1, IR2
        self.assertEqual(by['PR2']['open_overdue'], 2)        # R6, O6
        self.assertEqual(by['PR2']['declined'], 1)
        self.assertEqual(by['PR2']['pending'], 1)
        pr1_due = by['PR1']['due']
        self.assertAlmostEqual(by['PR1']['close_rate'], by['PR1']['closed_on_time'] / pr1_due, places=4)
        kinds = {e['evidence']: e['kind'] for e in r['closure_evidence']}
        self.assertEqual(kinds['visit'], 'contact (not specific to the item)')
        self.assertIn('time_to_closure', r)
        self.assertTrue(r['data_gaps'])                       # no call log, no inbox log in the fixture

    def test_specific_evidence_and_window_override(self):
        it = self.items_of({'evidence': 'specific'})
        self.assertEqual(it['L3']['status'], 'open_overdue')              # a visit alone no longer closes it
        self.assertEqual(it['L4']['status'], 'closed_late')               # the note naming glucose still does (day 73)
        self.assertEqual(it['L4']['closed_by'], 'note_acknowledgement')
        it = self.items_of({'windows': {'abnormal_result': 14}})
        self.assertEqual(it['L3']['status'], 'closed_late')               # visit on day 19 > 14

    def test_focus_and_filters(self):
        db = sqlite3.connect(self.path / 'semantic.sqlite3')
        try:
            r = lc.run(db, None, {'focus': 'referrals'}, AS_OF)
            self.assertEqual({x['item_type'] for x in r['open_loops']}, {'referral'})
            r = lc.run(db, None, {'focus': 'results', 'concept': 'potassium'}, AS_OF)
            self.assertEqual(r['items_total'], 2)
            r = lc.run(db, None, {'practice': 'PR2'}, AS_OF)
            self.assertTrue(all(x['practice'] == 'PR2' for x in r['open_loops']))
            r = lc.run(db, ['p1'], {}, AS_OF)                              # population input restricts items
            self.assertEqual(r['items_total'], 1)
            v, table = lc.summarize(self.full)
            self.assertIn('critical results have no acknowledgement', v)
            self.assertEqual(table[0]['item_id'], 'L1')
        finally:
            db.close()

    def test_answer_contract(self):
        plan = {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'loop_closure', 'inputs': [], 'parameters': {'focus': 'all'}, 'purpose': 'x'}]}
        plan = validate(plan, AS_OF)
        a = build_answer(plan, [{'result': self.full}], {}, 'q')
        self.assertEqual((a['status'], a['answer_type'], a['count']), ('answered', 'table', 4))
        self.assertIn('loop_closure', a['overall'])
        json.dumps(a)
        bad = {**plan, 'nodes': [{**plan['nodes'][0], 'parameters': {'period': {'start': '2031-01-01', 'end': '2031-02-01'}}}]}
        with self.assertRaises(ValueError): validate(bad, AS_OF)


# ---- routing ---------------------------------------------------------------------------------------------------------------
PHRASINGS = {'Which abnormal lab results have no follow-up after 14 days?': 'results',
             'Show critical potassium results that were never acknowledged': 'critical',
             'Which referrals are still open after 90 days?': 'referrals',
             'Referral completion rate by practice': 'referrals',
             'Incidental lung nodules with no follow-up imaging': 'recommendations',
             'Which positive FIT tests never had a colonoscopy?': 'screening',
             'How many ordered labs were never resulted?': 'orders',
             'Open loops by ordering clinician': 'all',
             'Abnormal imaging reports not reviewed by anyone': 'reports',
             'What is our loop closure rate this year?': 'all',
             'Which abnormal results fell through the cracks for heart failure patients?': 'results'}
STAY_AWAY = ['profile', 'survival', 'risk_adjustment', 'pathway', 'outreach', 'early_signal', 'scheduling']


def routing_profile():
    patients = {f'p{i}': {'age_reported': 60, 'sex_reported': 'F'} for i in range(3)}
    recs = [(f'p{i}', 'coverage', {'coverage_type': 'medicare', 'event_date': '2026-01-01'}) for i in range(3)]
    recs += [('p0', 'labs', {'test': 'HbA1c', 'value': 7, 'unit': '%', 'event_date': '2028-01-01'}),
             ('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'}),
             ('p0', 'encounters', {'type': 'office', 'event_date': '2028-01-01', 'organization_id': 'PR1'}),
             ('p1', 'referrals', {'status': 'requested', 'event_date': '2028-02-01', 'organization_id': 'PR1'}),
             ('p2', 'cancer_screening_results', {'test': 'FIT', 'result': 'positive', 'event_date': '2028-03-01'}),
             ('p0', 'medical_claims', {'claim_id': 'C1', 'service_date': '2028-01-01', 'event_date': '2028-01-01', 'status': 'paid', 'ms_drg': '291',
                                       'source_system': 'PAY1', 'billed_cents': 10000, 'allowed_cents': 8000, 'payer_paid_cents': 8000})]
    path = build(recs, patients)
    semantic_layer.build(path)
    return path, source_profile.load(path)


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path, cls.profile = routing_profile()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.path, ignore_errors=True)

    def compile(self, q):
        return local_compiler.compile(q, self.profile, '2029-08-15')

    def test_phrasings(self):
        for q, focus in PHRASINGS.items():
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertIsNotNone(g, info)
                self.assertEqual(g['nodes'][-1]['operation'], 'loop_closure', info)
                self.assertEqual(g['nodes'][-1]['parameters']['focus'], focus)
        g, _ = self.compile('Which abnormal lab results have no follow-up after 14 days?')
        w = g['nodes'][-1]['parameters']['windows']
        self.assertEqual((w['critical_result'], w['abnormal_result'], w['referral']), (14, 14, 90))
        g, _ = self.compile('Open loops by ordering clinician')
        self.assertEqual(g['nodes'][-1]['parameters']['group_by'], 'owner')
        g, _ = self.compile('Which abnormal results fell through the cracks for heart failure patients?')
        self.assertEqual([n['operation'] for n in g['nodes']], ['cohort', 'loop_closure'])
        g, info = self.compile('What share of critical lab values were communicated to a responsible clinician within 60 minutes?')
        self.assertEqual(g['coverage'], 'unsupported')                    # needs the call log
        self.assertIn('call log', g['unmet_requirements'][0])
        bare = {**self.profile, 'streams': {k: v for k, v in self.profile['streams'].items() if k != 'cancer_screening_results'}}
        g, info = local_compiler.compile('Which positive FIT tests never had a colonoscopy?', bare, '2029-08-15')
        self.assertIsNone(g)                                              # release without screening results: decline, never "0 open"
        self.assertIn('screening results', info['reason'])

    def test_acceptance_file(self):
        rows = json.loads((ROOT / 'bench' / 'acceptance' / 'loop_closure.json').read_text())
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            with self.subTest(q=row['question']):
                g, info = self.compile(row['question'])
                self.assertIsNotNone(g, info)
                self.assertEqual(g['nodes'][-1]['operation'], 'loop_closure', info)

    def test_does_not_steal(self):
        qs = [c['question'] for op in STAY_AWAY for c in json.loads((ROOT / 'bench' / 'acceptance' / f'{op}.json').read_text())]
        qs += ['How long after a positive fecal immunochemical test do patients get a colonoscopy?',      # time-to-event: survival
               'Which open referrals should schedulers call first to get patients booked?',             # call list: outreach
               'Which referrals were sent but never resulted in a completed specialist visit?',         # literal library question
               'What share of patients get primary care follow-up within 7 days after discharge?']      # post-discharge: pathway / measures
        for q in qs:
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertNotEqual(g['nodes'][-1]['operation'] if g and g.get('nodes') else None, 'loop_closure', info)

    def test_follow_ups_compile_back(self):
        for q in lc.follow_up():
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertEqual(g['nodes'][-1]['operation'], 'loop_closure', info)


# ---- integration -----------------------------------------------------------------------------------------------------------
class Integration(unittest.TestCase):
    def test_registered_everywhere(self):
        op = 'loop_closure'
        self.assertIn(op, analysis_graph.OPERATIONS)
        self.assertIn(op, analysis_graph.NO_BENCHMARK_OPERATIONS)
        self.assertIn(op, graph_contracts.TYPED)
        self.assertEqual(graph_contracts.input_contract(op)['count'], [0, 1])
        self.assertIn(op, graph_planner.CONTRACTS)
        self.assertEqual(capability_registry.PRODUCT[op]['population'], 'optional')
        schema = graph_contracts.parameter_schema(op, {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']['focus']['enum']), set(lc.FOCUS))
        self.assertTrue(set(schema['properties']) <= lc.FIELDS)
        self.assertLess(len(json.dumps(schema)), 3500)
        lc.validate({k: v for k, v in graph_planner.CONTRACTS[op]['parameters'].items() if not isinstance(v, str) or '|' not in v})
        source = Path(analysis_graph.__file__).read_text()
        block = source[source.index(f"if last['operation'] == '{op}':"):]
        block = block[:block.index("\n    if last['operation']", 10)]
        self.assertTrue(block.rstrip().endswith('return answer'))
        self.assertIn('loop_closure.py', source[source.index('def code_identity'):source.index('def code_identity') + 2500])

    def test_router_tool_comes_from_the_registry(self):
        from clinical_core.agere.clinical import operator_router
        self.assertIn('loop_closure', {t['name'] for t in operator_router.menu()})
        t = operator_router.tool('loop_closure')
        self.assertIn('referrals never completed', t['description'])
        self.assertIn('population', t['input_schema']['properties'])

    def test_console_renders(self):
        from clinical_core.agere import ask_view
        html = ask_view.page()
        self.assertIn('function vizLoops', html)
        self.assertIn('if(o.loop_closure)return vizLoops(a)', html)
        node = shutil.which('node') or str(Path.home() / '.local' / 'bin' / 'node')
        if not Path(node).exists(): self.skipTest('node not available for a syntax check')
        script = re.findall(r'<script>(.*?)</script>', html, re.S)[-1]
        tmp = Path(os.environ.get('TMPDIR', '/tmp')) / 'agere_loops_check.js'
        tmp.write_text(script)
        out = subprocess.run([node, '--check', str(tmp)], capture_output=True, text=True)
        tmp.unlink()
        self.assertEqual(out.returncode, 0, out.stderr[:500])


def _v20_ready():
    return (REAL_V20 / 'semantic.sqlite3').exists()


@unittest.skipUnless(_v20_ready(), 'warehouse v2.0 semantic layer not present')
class RealV20(unittest.TestCase):
    def test_smoke(self):
        db = sqlite3.connect(f"file:{REAL_V20 / 'semantic.sqlite3'}?mode=ro", uri=True)  # read-only; never rebuilt
        try:
            r = lc.run(db, None, {'focus': 'referrals'}, AS_OF)
            c = lc.run(db, None, {'focus': 'critical'}, AS_OF)
        finally:
            db.close()
        self.assertEqual(r['status'], 'answered')
        self.assertGreater(r['items_total'], 100)
        self.assertTrue(r['by_group'])
        self.assertTrue(all(x['item_type'] == 'referral' for x in r['open_loops']))
        self.assertEqual(c['status'], 'answered')
        self.assertTrue(all(x['tier'] == 'critical' for x in c['open_loops']))
        self.assertTrue(any('call log' in g for g in c['data_gaps']))


if __name__ == '__main__':
    unittest.main()
