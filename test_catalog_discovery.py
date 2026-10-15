"""Full-catalog discovery: the modeled layer is a fast path, never the boundary of what was received.

Mechanisms (answer_guard.py, local_compiler.py, concepts.py, metric_router.py, metric_families.py):
  - catalog probe: acronyms a source abbreviates (Social Vulnerability Index -> svi_*), drug classes named by their members
    (a 'statin' question finds 'atorvastatin 20 mg' claim rows), a stream the question names outright, the stream name with
    values of two different fields of one record, and bookkeeping fields a question is ABOUT (corrections ->
    supersedes_record_id, date precision -> event_date_precision);
  - zero vs. the data it filtered: a zero read through the latest-version view of a status-history stream, or with a
    mis-spelled literal, re-plans naming the stored value;
  - declines: 'not received / never populated / not observed' from any route gets a full-catalog probe and one re-plan, or a
    stated catalog check; the local compiler's data-absence refusals are lifted when a received stream or field is named
    for the subject;
  - raw-catalog slice: inbound record links, small coded vocabularies, cross-feed bookkeeping lines;
  - concept resolution: an instrument total carried as a field on another feed (health_risk_assessments.phq2_total);
  - metric router: a subtracted component the metric does not define falls through; feed_quality measures corrections per
    feed and imprecise event dates by precision.
Fixture warehouse, fake models only: no network, no paid call. Phrasings deliberately differ from any benchmark wording."""
import json, os, sqlite3, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clinical_core.agere.clinical import (answer_guard as G, concepts, grounding, local_compiler, metric_families as mf,
                                          metric_router as mr, semantic_layer, source_profile, sql_query)
from test_answer_guard import Hosted, fake_stack, run_graph_for, sql_graph

AS_OF = '2029-12-31'


def records():
    out = []
    # status-history appointments: the reason sits on the 'rescheduled' version, the latest version is 'fulfilled'
    for i in range(6):
        a = f'A{i}'
        out.append((f'{a}-1', f'P{i}', 'appointments', '2029-02-01', {'appointment_id': a, 'status': 'booked'}))
        if i < 4:
            out.append((f'{a}-2', f'P{i}', 'appointments', '2029-02-05', {'appointment_id': a, 'status': 'rescheduled',
                                                                       'reason': 'childcare' if i < 3 else 'clinician_schedule_change'}))
        out.append((f'{a}-3', f'P{i}', 'appointments', '2029-03-01', {'appointment_id': a, 'status': 'fulfilled'}))
    # vendor file with SVI percentiles, two deliveries
    for i, (d, pct, match) in enumerate([('2029-01-15', 40.0, 'exact'), ('2029-06-15', 60.0, 'exact'), ('2029-06-15', 80.0, 'exact'),
                                         ('2029-06-15', 10.0, 'partial_address')]):
        out.append((f'V{i}', f'P{i}', 'neighborhood_vendor_attributes', d, {'delivery_date': d, 'svi_overall_percentile': pct,
                                                                           'member_match_status': match, 'vendor_name': 'Area Data Co'}))
    # payer pharmacy claims: drug names only (no class column)
    for i, (drug, status) in enumerate([('atorvastatin 20 mg tablet', 'rejected'), ('Rosuvastatin', 'paid'), ('metformin 500 mg', 'rejected'),
                                        ('atorvastatin 40 mg tablet', 'paid'), ('lisinopril 10 mg', 'paid')]):
        out.append((f'RX{i}', f'P{i}', 'payer_pharmacy_claims', '2029-04-0%d' % (i + 1), {'drug_name': drug, 'claim_status': status}))
    # an assessment feed carrying a questionnaire total, and follow-up questionnaires linked back to it
    for i, tot in enumerate([1, 4, 5, 2]):
        out.append((f'H{i}', f'P{i}', 'annual_wellness_questionnaires', '2029-05-0%d' % (i + 1), {'phq2_total': tot, 'general_health': 'fair'}))
    out.append(('M1', 'P1', 'mood_questionnaires', '2029-05-20', {'instrument': 'PHQ-9', 'total': 11, 'screen_record_id': 'H1'}))
    # care transition plans + follow-up reports linked by plan_record_id
    for i in range(3):
        out.append((f'T{i}', f'P{i}', 'care_transition_plans', '2029-07-0%d' % (i + 1), {'followup_requested_days': 7, 'focus': 'medication'}))
    out.append(('F0', 'P0', 'transition_followup_reports', '2029-07-04', {'plan_record_id': 'T0', 'outcome': 'completed'}))
    # corrections and date precision on two feeds
    out.append(('C1', 'P0', 'conditions', '2029-01-01', {'description': 'asthma', 'event_date_precision': 'month'}))
    out.append(('C2', 'P1', 'conditions', '2029-01-01', {'description': 'asthma', 'event_date_precision': 'year'}))
    out.append(('C3', 'P1', 'conditions', '2029-01-01', {'description': 'asthma', 'event_date_precision': 'day', 'supersedes_record_id': 'C2'}))
    out.append(('C4', 'P2', 'conditions', None, {'description': 'copd', 'event_date_precision': 'unknown'}))
    out.append(('L1', 'P0', 'labs', '2029-01-02', {'test': 'LDL', 'value': 120, 'unit': 'mg/dL', 'event_date_precision': 'day'}))
    out.append(('L2', 'P0', 'labs', '2029-01-02', {'test': 'LDL', 'value': 125, 'unit': 'mg/dL', 'event_date_precision': 'day', 'supersedes_record_id': 'L1'}))
    return out


def build(root):
    recs = records()
    for r in recs: r[4].setdefault('source_table', r[2])
    stack = sqlite3.connect(root / 'stack.sqlite3')
    stack.execute('CREATE TABLE records(id TEXT, stream TEXT, payload TEXT)')
    stack.execute('CREATE TABLE patients(id TEXT, payload TEXT)')
    stack.executemany('INSERT INTO records VALUES (?,?,?)', [(r[0], r[2], json.dumps(r[4])) for r in recs])
    stack.executemany('INSERT INTO patients VALUES (?,?)', [(f'P{i}', json.dumps({'sex': 'F'})) for i in range(6)])
    stack.commit(); stack.close()
    db = sqlite3.connect(root / 'semantic.sqlite3')
    db.execute('CREATE TABLE meta(key TEXT, value TEXT)')
    db.executemany('INSERT INTO meta VALUES (?,?)', [('version', semantic_layer.VERSION), ('source_digest', 'fixture-cd'), ('as_of', AS_OF)])
    db.execute('CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT, available_date TEXT, '
               'organization_id TEXT, payload TEXT)')
    db.executemany('INSERT INTO record VALUES (?,?,?,?,?,?,?,?)', [(r[0], r[1], r[2], r[3], r[3], r[3], 'ORG', json.dumps(r[4])) for r in recs])
    db.execute('CREATE TABLE current_record(stream TEXT, key_field TEXT, business_id TEXT, record_id TEXT, patient_id TEXT, versions INTEGER, '
               'first_available_date TEXT, available_date TEXT, event_date TEXT, payload TEXT)')
    latest = {}
    for r in recs:
        if r[2] == 'appointments': latest[r[4]['appointment_id']] = r
    db.executemany('INSERT INTO current_record VALUES (?,?,?,?,?,?,?,?,?,?)',
                   [('appointments', 'appointment_id', k, r[0], r[1], 3, r[3], r[3], r[3], json.dumps(r[4])) for k, r in latest.items()])
    db.execute('CREATE TABLE link(record_id TEXT, stream TEXT, field TEXT, target_record_id TEXT)')
    db.executemany('INSERT INTO link VALUES (?,?,?,?)', [(r[0], r[2], f, v) for r in recs for f, v in r[4].items() if f.endswith('_record_id')])
    db.commit(); db.close()
    (root / 'run.json').write_text(json.dumps({'source_digest': 'fixture-cd', 'as_of': AS_OF}))
    return root


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = build(Path(self.tmp.name))
        self.profile = source_profile.load(self.root)
        self.env = mock.patch.dict(os.environ, {'AGERE_CROSS_CHECK': 'off', 'AGERE_GUARD_POLICY': 'abstain', 'AGERE_CATALOG_DENSE': 'off'})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def top(self, q):
        p = G.probe(q, self.profile, dense=None)
        return p['candidates'][0], p

    def review(self, question, graph, receipt, result, hosted=None, live=True):
        return G.review(fake_stack(self.root, live), question, graph, receipt, result,
                        ledger=SimpleNamespace(path=self.root / 'l.sqlite3') if live else None, profile=self.profile, hosted=hosted)


class Probe(Fixture):
    def test_acronym_of_a_title_case_name_finds_the_abbreviated_field(self):
        c, p = self.top('Across the latest delivery, what is the mean Social Vulnerability Index score of exactly matched members?')
        self.assertEqual(c['stream'], 'neighborhood_vendor_attributes'); self.assertTrue(c['likely'])
        self.assertIn('svi', p['terms']); self.assertIn('vulnera', c['matched_terms'])

    def test_drug_class_named_by_its_members(self):
        c, _ = self.top('Who had a statin fill turned down by the insurer (claim rejected)?')
        self.assertEqual(c['stream'], 'payer_pharmacy_claims'); self.assertTrue(c['likely'])
        self.assertTrue(any(ev['value'] in ('atorvastatin 20 mg tablet', 'atorvastatin 40 mg tablet', 'Rosuvastatin') for ev in c['evidence']))
        self.assertIn('statin', G.drug_class_terms('Lipitor 10 mg'))  # brand -> class via the brand table
        self.assertEqual(G.drug_class_terms('furosemide 40 mg')[:2], ['loop', 'diureti'])

    def test_stream_named_outright_is_likely(self):
        c, _ = self.top('For our care transition plans, how often did a follow-up happen in the requested window?')
        self.assertEqual(c['stream'], 'care_transition_plans'); self.assertTrue(c['name_match']); self.assertTrue(c['likely'])

    def test_name_plus_two_fields_of_one_record(self):
        c, _ = self.top('Count appointments rescheduled because of childcare')
        self.assertEqual(c['stream'], 'appointments')
        self.assertGreaterEqual(c['item_coverage'], 0.99)

    def test_bookkeeping_fields_answer_to_their_meaning(self):
        _, p = self.top('Which feeds have sent amended records, and how many amendments each?')
        self.assertTrue(any(ev.get('kind') == 'alias' and ev['field'] == 'supersedes_record_id' for c in p['candidates'] for ev in c['evidence']))
        text, rec = G.raw_catalog_slice('Which feeds have sent amended records, and how many amendments each?', self.profile, dense=None)
        self.assertIn('across feeds: supersedes_record_id', text); self.assertIn('conditions (1)', text); self.assertIn('labs (1)', text)
        text, _ = G.raw_catalog_slice('How many condition entries carry only an approximate date?', self.profile, dense=None)
        self.assertIn('event_date_precision (how precise event_date is', text)

    def test_one_free_text_mention_of_a_code_is_not_a_code_source(self):
        prof = {'streams': {'encounters': {'records': 9, 'fields': {'chief_complaint': {'populated': 9, 'values': {'PHQ-9 14, poor sleep': 1}}}},
                            'mood_questionnaires': {'records': 50, 'fields': {'instrument': {'populated': 50, 'values': {'PHQ-9': 50}}}}}}
        p = G.probe('Which patients scored 10+ on the PHQ-9?', prof, dense=None)
        by = {c['stream']: c for c in p['candidates']}
        self.assertTrue(by['mood_questionnaires']['code_match']); self.assertFalse(by['encounters']['code_match'])


class Slice(Fixture):
    def test_inbound_links_and_vocabulary_reach_the_writer(self):
        text, rec = G.raw_catalog_slice('For our care transition plans, how often did a follow-up happen in the requested window?',
                                        self.profile, path=self.root, dense=None)
        self.assertIn('care_transition_plans', rec['streams'])
        self.assertIn('linked from: transition_followup_reports.plan_record_id', text)
        text, _ = G.raw_catalog_slice('Average of phq2_total on annual wellness questionnaires', self.profile, path=self.root, dense=None)
        self.assertIn('mood_questionnaires.screen_record_id', text)
        text, _ = G.raw_catalog_slice('How did pharmacy claims for drugs get adjudicated?', self.profile, dense=None)
        self.assertIn('claim_status: paid|rejected', text)


class ZeroAgainstTheData(Fixture):
    Q = 'How many visits were pushed to another day because of childcare problems?'
    LATEST = ("SELECT count(DISTINCT c.business_id) FROM current_record c WHERE c.stream = 'appointments' "
              "AND json_extract(c.payload,'$.reason') = 'childcare'")
    ALL = ("SELECT count(DISTINCT json_extract(payload,'$.appointment_id')) FROM record WHERE stream = 'appointments' "
           "AND json_extract(payload,'$.reason') = 'childcare'")

    def zero(self, sql):
        graph = sql_graph(sql)
        return graph, {'route': 'hosted_verified_sql', 'new_paid_api_calls': 0, 'steps': []}, run_graph_for(self.root)(graph, self.Q)

    def test_latest_version_view_hides_the_reason(self):
        _, _, result = self.zero(self.LATEST)
        self.assertEqual(result['answer']['count'], 0)
        c = G.zero_contradictions(result['answer'], self.profile, {'appointments'})
        self.assertEqual([(x['stream'], x['contradiction'], x['evidence'][0]['records']) for x in c], [('appointments', 'version', 3)])
        hosted = Hosted(self.root, {'default': (self.ALL, 'count')})
        _, receipt, result, _ = self.review(self.Q, *self.zero(self.LATEST), hosted=hosted)
        self.assertEqual(result['answer']['count'], 3)
        self.assertEqual(receipt['answer_guard']['decision'], 'replanned')
        self.assertIn('current_record keeps only the latest version per appointment_id', hosted.calls[0]['feedback'])
        self.assertEqual(hosted.calls[0]['extra_streams'][0], 'appointments')

    def test_replan_through_the_same_blind_view_does_not_confirm_the_zero(self):
        hosted = Hosted(self.root, {'default': (self.LATEST, 'count')})
        _, receipt, result, _ = self.review(self.Q, *self.zero(self.LATEST), hosted=hosted)
        self.assertEqual(receipt['answer_guard']['decision'], 'abstained')
        self.assertIn('appointments has 3 records with reason', result['answer']['missing'][-1])

    def test_misspelled_literal(self):
        sql = "SELECT count(*) FROM record WHERE stream = 'appointments' AND json_extract(payload,'$.reason') = 'Child Care'"
        _, _, result = self.zero(sql)
        c = G.zero_contradictions(result['answer'], self.profile, {'appointments'})
        self.assertEqual((c[0]['contradiction'], c[0]['evidence'][0]['value']), ('spelling', 'childcare'))

    def test_a_true_zero_is_not_contradicted(self):
        sql = "SELECT count(*) FROM record WHERE stream = 'appointments' AND json_extract(payload,'$.reason') = 'childcare' AND event_date > '2029-06-01'"
        _, _, result = self.zero(sql)
        self.assertEqual(G.zero_contradictions(result['answer'], self.profile, {'appointments'}), [])


class Declines(Fixture):
    def declined(self, missing, status='grounding_failed', route='operator_router', grounding=()):
        answer = {'status': status, 'answer_type': 'unsupported', 'count': None, 'value': None, 'table': None, 'patient_ids': None,
                  'missing': list(missing), 'grounding': list(grounding), 'quality_checks': []}
        return {'interpretation': 'q', 'coverage': 'unsupported', 'nodes': []}, {'route': route, 'new_paid_api_calls': 0, 'steps': []}, \
            {'answer': answer, 'new_paid_api_calls': 0}

    Q = 'What is the mean Social Vulnerability Index percentile in the newest delivery, exact matches only?'
    SQL = ("SELECT avg(json_extract(payload,'$.svi_overall_percentile')) FROM record WHERE stream = 'neighborhood_vendor_attributes' "
           "AND json_extract(payload,'$.member_match_status') = 'exact' AND json_extract(payload,'$.delivery_date') = "
           "(SELECT max(json_extract(payload,'$.delivery_date')) FROM record WHERE stream = 'neighborhood_vendor_attributes')")

    def test_claims(self):
        self.assertTrue(G.decline_claim({'status': 'unsupported', 'missing': ['Missing input: ADI/SVI (not received)']}, {'route': 'local_unsupported'}))
        self.assertTrue(G.decline_claim({'status': 'grounding_failed', 'missing': [], 'grounding': [
            {'issue': 'concept_not_observed', 'severity': 'error', 'detail': 'No received records match concept phq2.'}]}))
        self.assertIsNone(G.decline_claim({'status': 'unsupported', 'missing': ['Missing input: benchmark_pmpm']}, {'route': 'local_requires_input'}))
        self.assertIsNone(G.decline_claim({'status': 'unsupported', 'missing': ['Missing input: executed payer contract terms']}))
        self.assertIsNone(G.decline_claim({'status': 'answered', 'answer_type': 'count', 'count': 0}))

    def test_decline_replans_with_the_received_source(self):
        hosted = Hosted(self.root, {'default': (self.SQL, 'value')})
        _, receipt, result, calls = self.review(self.Q, *self.declined(['Patient svi is never populated.']), hosted=hosted)
        self.assertEqual(receipt['answer_guard']['decision'], 'replanned_after_decline')
        self.assertAlmostEqual(result['answer']['value'], 70.0)
        self.assertIn('neighborhood_vendor_attributes', hosted.calls[0]['extra_streams'])
        self.assertIn('never populated', hosted.calls[0]['feedback'])
        self.assertEqual(receipt['replaced_route']['route'], 'operator_router'); self.assertEqual(calls, 1)

    def test_decline_offline_states_the_contradicting_source(self):
        _, receipt, result, _ = self.review(self.Q, *self.declined(['Missing input: SVI (not received)'], 'unsupported', 'local_unsupported'), live=False)
        self.assertEqual(receipt['answer_guard']['decision'], 'decline_contradicted_by_catalog')
        qc = [c for c in result['answer']['quality_checks'] if c['control'] == 'QC-CATALOG'][0]
        self.assertIn('neighborhood_vendor_attributes', qc['message']); self.assertEqual(result['answer']['status'], 'unsupported')

    def test_decline_with_nothing_in_the_catalog_stands_with_the_check_stated(self):
        _, receipt, result, _ = self.review('How many members wear an activity tracker?', *self.declined(
            ['Missing input: device or objectively measured activity data (none is received)'], 'unsupported', 'local_unsupported'),
            hosted=Hosted(self.root, fail=True))
        self.assertEqual(receipt['answer_guard']['decision'], 'decline_confirmed_by_catalog')
        self.assertIn('searched all', [c for c in result['answer']['quality_checks'] if c['control'] == 'QC-CATALOG'][0]['message'])

    def test_requires_input_is_left_alone(self):
        _, receipt, result, _ = self.review(self.Q, *self.declined(['Missing input: benchmark_pmpm'], 'unsupported', 'local_requires_input'),
                                            hosted=Hosted(self.root, fail=True))
        self.assertNotIn('decision', receipt['answer_guard'])


class CompilerRefusals(Fixture):
    def test_named_subject_lifts_a_not_received_refusal(self):
        self.assertTrue(G.catalog_holds('social vulnerability index', self.profile))
        self.assertTrue(G.catalog_holds('svi', self.profile))
        self.assertFalse(G.catalog_holds('area deprivation index', self.profile))
        info = {'compiler': 'x', 'recognised': [], 'unexplained': []}
        q = 'what share of members live in tracts with a high social vulnerability index?'
        self.assertIsNone(local_compiler._refusal(q, q, self.profile, False, None, dict(info)))
        q2 = 'what is the average area deprivation index national rank of our members?'
        graph, _ = local_compiler._refusal(q2, q2, self.profile, False, None, dict(info))
        self.assertEqual(graph['coverage'], 'unsupported')
        bare = {'streams': {'labs': self.profile['streams']['labs']}, 'patients': self.profile['patients']}
        graph, _ = local_compiler._refusal(q, q, bare, False, None, dict(info))
        self.assertEqual(graph['coverage'], 'unsupported')


class Concepts(Fixture):
    def test_instrument_total_on_another_feed_binds_the_concept(self):
        r = concepts.resolve('phq2', self.profile)
        self.assertIn({'stream': 'annual_wellness_questionnaires', 'value_field': 'phq2_total', 'fixed_unit': 'points'}, r['bindings'])
        grounded, _ = grounding.prepare({'include': [{'kind': 'measurement', 'concept': 'phq2', 'op': 'ge', 'value': 3, 'aggregate': 'any',
                                                      'window': {'kind': 'all_time'}}]}, self.profile)
        self.assertTrue(grounded['include'][0]['_resolved']['bindings'])


class MetricFit(unittest.TestCase):
    def test_subtracted_component_the_metric_does_not_define(self):
        m = {'id': 'patient_payments', 'name': 'Patient payments received', 'summary': 'Patient payments received (patient_payments records)',
             'event': {'stream': 'patient_payments', 'measures': {'amount_usd': 'sum(amount_cents)/100.0'}}}
        self.assertEqual(mr.unaccounted_components('Patient cash in 2029 less chargebacks issued that year?', m), ['chargebacks'])
        self.assertEqual(mr.unaccounted_components('Patient payments in 2029 net of refunds', m), ['refunds'])
        self.assertEqual(mr.unaccounted_components('How much did patients pay us in 2029?', m), [])
        m2 = {**m, 'summary': 'Patient payments received minus refunds issued'}
        self.assertEqual(mr.unaccounted_components('Patient payments in 2029 net of refunds', m2), [])

    def test_fill_rejects_and_falls_through(self):
        lib = mr.routable()
        calls = []

        def ask(question, as_of, tools=None, forced=None, feedback=None):
            calls.append(feedback)
            if forced is None: return {'name': mr.tool_name('patient_payments'), 'input': {}}, {'cache_hit': True}
            return {'name': forced, 'input': {'measure': 'amount_usd', 'period': {'preset': 'year', 'year': 2029}}}, {'cache_hit': True}
        graph, info = mr.route('Patient collections for 2029 after deducting the refunds we sent back?', '2030-05-27', ask=ask)
        self.assertIsNone(graph); self.assertEqual(info['decision'], 'fill_failed')
        self.assertIn('refunds', calls[-1])
        self.assertIn('patient_payments', lib)


class FeedQuality(Fixture):
    def run_sql(self, slots):
        return sql_query.execute(self.root, mf.build_feed_quality(slots, AS_OF))

    def test_corrections_per_feed(self):
        r = self.run_sql({'stream': 'all', 'measures': ['correction_rows'], 'group_by': 'stream'})
        self.assertEqual(r['rows'], [{'stream': 'conditions', 'correction_rows': 1}, {'stream': 'labs', 'correction_rows': 1}])
        self.assertEqual(self.run_sql({'stream': 'all', 'measures': ['correction_rows']})['value'], 2)
        with self.assertRaises(ValueError): mf.build_feed_quality({'stream': 'all', 'measures': ['rows']}, AS_OF)

    def test_imprecise_dates_by_precision_current_versions_only(self):
        r = self.run_sql({'stream': 'conditions', 'measures': ['imprecise_date_rows'], 'group_by': 'date_precision'})
        self.assertEqual(r['rows'], [{'date_precision': 'month', 'imprecise_date_rows': 1}, {'date_precision': 'unknown', 'imprecise_date_rows': 1}])
        self.assertEqual(self.run_sql({'stream': 'conditions', 'measures': ['imprecise_date_rows']})['value'], 2)

    def test_metric_declares_the_new_slots(self):
        m = mr.routable()['feed_quality']
        self.assertEqual(m['version'], 2)
        self.assertIn('all', m['slots']['stream']['values']); self.assertIn('date_precision', m['slots']['group_by']['values'])
        self.assertIn('correction_rows', m['slots']['measures']['values'])
        mr.validate_slots(m, {'stream': 'all', 'measures': ['correction_rows'], 'group_by': 'stream'})


if __name__ == '__main__':
    unittest.main()
