"""Confidently-wrong answer guard (answer_guard.py): zero / empty / implausible answers probe the full received catalog and
re-plan once or abstain naming the unused source; legitimate zeros ship with a stated catalog check; uncertified paths are
cross-checked by one independent path; the hosted SQL writer sees a raw-catalog slice. Fixture warehouse, fake models only:
no network, no paid call."""
import json, os, re, sqlite3, sys, tempfile, types, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from clinical_core.agere.clinical import answer_guard as G, semantic_layer, sql_query, source_profile, nl2sql
from clinical_core.agere.clinical.analysis_graph import build_answer
from clinical_core.agere.clinical.economical_planner import economics

AS_OF = '2029-12-31'
WELLNESS_Q = 'How many Medicare annual wellness visits in 2029?'
DOLLAR_Q = 'How much did payers pay, net of reversals, on medical claims adjudicated in 2029?'
PHQ_Q = 'How many patients had a PHQ-9 score of 10 or more in 2029?'


def build(root):
    """stack.sqlite3 (received records, for the source profile) + semantic.sqlite3 (typed tables + read-only record table)."""
    recs = []
    for i in range(5):
        recs.append((f'E{i}', f'P{i}', 'encounters', '2029-03-0%d' % (i + 1), {'encounter_id': f'E{i}', 'type': 'office', 'status': 'completed'}))
    for i in range(3):
        recs.append((f'W{i}', f'P{i}', 'wellness_visit_services', '2029-05-0%d' % (i + 1),
                     {'encounter_id': f'E{i}', 'visit_type': 'medicare_annual_wellness', 'hcpcs_code': 'G0438', 'status': 'completed'}))
    recs.append(('W9', 'P4', 'wellness_visit_services', '2029-06-01', {'encounter_id': 'E4', 'visit_type': 'preventive_review', 'status': 'completed'}))
    for i, cents in enumerate((120000, 150000, 90000)):
        recs.append((f'C{i}', f'P{i}', 'medical_claims', '2029-04-1%d' % i, {'claim_id': f'CL{i}', 'status': 'paid', 'payer_paid_cents': cents}))
    recs.append(('C9', 'P0', 'medical_claims', '2029-05-10', {'claim_id': 'CL0', 'status': 'reversed', 'payer_paid_cents': 0}))
    for i, total in enumerate((4, 12, 15, 8)):
        recs.append((f'Q{i}', f'P{i}', 'mood_questionnaires', '2029-07-0%d' % (i + 1), {'instrument': 'PHQ-9', 'total': total}))
    for i in range(3):
        recs.append((f'L{i}', f'P{i}', 'labs', '2029-02-0%d' % (i + 1), {'test': 'LDL cholesterol', 'value': 120 + i, 'unit': 'mg/dL'}))
    stack = sqlite3.connect(root / 'stack.sqlite3')
    stack.execute('CREATE TABLE records(id TEXT, stream TEXT, payload TEXT)')
    stack.execute('CREATE TABLE patients(id TEXT, payload TEXT)')
    stack.executemany('INSERT INTO records VALUES (?,?,?)', [(r[0], r[2], json.dumps(r[4])) for r in recs])
    stack.executemany('INSERT INTO patients VALUES (?,?)', [(f'P{i}', json.dumps({'sex': 'F'})) for i in range(5)])
    stack.commit(); stack.close()
    db = sqlite3.connect(root / 'semantic.sqlite3')
    db.execute('CREATE TABLE meta(key TEXT, value TEXT)')
    db.executemany('INSERT INTO meta VALUES (?,?)', [('version', semantic_layer.VERSION), ('source_digest', 'fixture'), ('as_of', AS_OF)])
    db.execute('CREATE TABLE patient(patient_id TEXT, coverage_type TEXT)')
    db.executemany('INSERT INTO patient VALUES (?,?)', [(f'P{i}', 'medicare') for i in range(5)])
    db.execute('CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT, available_date TEXT, '
               'organization_id TEXT, payload TEXT)')
    db.executemany('INSERT INTO record VALUES (?,?,?,?,?,?,?,?)', [(r[0], r[1], r[2], r[3], r[3], r[3], 'ORG', json.dumps(r[4])) for r in recs])
    db.execute('CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type TEXT, event_date TEXT, status TEXT)')
    db.executemany('INSERT INTO visit VALUES (?,?,?,?,?)', [(r[4]['encounter_id'], r[1], 'office', r[3], 'completed') for r in recs if r[2] == 'encounters'])
    db.execute('CREATE TABLE claim(claim_id TEXT, patient_id TEXT, net_payer_paid_cents INTEGER, first_adjudication_date TEXT)')
    db.executemany('INSERT INTO claim VALUES (?,?,?,?)', [('CL0', 'P0', 120000, '2029-04-10'), ('CL1', 'P1', 150000, '2029-04-11'), ('CL2', 'P2', 90000, '2029-04-12')])
    db.execute('CREATE TABLE measurement(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, name TEXT, value REAL, event_date TEXT)')
    db.executemany('INSERT INTO measurement VALUES (?,?,?,?,?,?,?)', [(r[0], r[1], 'labs', 'ldl', 'LDL cholesterol', r[4]['value'], r[3])
                                                                    for r in recs if r[2] == 'labs'])
    db.commit(); db.close()
    (root / 'run.json').write_text(json.dumps({'source_digest': 'fixture', 'as_of': AS_OF}))
    return root


def sql_graph(query, returns='count'):
    return {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': {'query': query, 'returns': returns}, 'purpose': 'test'}]}


def run_graph_for(root):
    def run_graph(graph, question=None):
        node = graph['nodes'][-1]
        report = sql_query.execute(root, node['parameters'])
        return {'answer': build_answer(graph, [{'result': report}], {}, question), 'new_paid_api_calls': 0}
    return run_graph


def fake_stack(root, live=True):
    return SimpleNamespace(path=root, receipts=root, allow_live=live, run_graph=run_graph_for(root),
                           retriever=SimpleNamespace(encoder=None, meta={'snapshot': {'as_of': AS_OF}}))


class Hosted:
    """Fake verified hosted ladder: picks a query by what it was shown; records every call; $0.002 per call."""
    def __init__(self, root, queries=None, fail=False):
        self.root, self.queries, self.fail, self.calls = root, queries or {}, fail, []

    def __call__(self, stack, question, ledger, extra_streams=(), feedback='', independent=None, stage='answer_guard_replan', profile=None):
        self.calls.append({'question': question, 'extra_streams': tuple(extra_streams), 'feedback': feedback, 'independent': independent, 'stage': stage})
        if self.fail:
            exc = ValueError('Verified SQL ladder declined: no candidate passed verification')
            exc.guard_cost, exc.guard_calls = 0.003, 2
            raise exc
        query, returns = self.queries.get(stage) or self.queries['default']
        graph = sql_graph(query, returns)
        receipt = {'route': 'hosted_verified_sql', 'new_paid_api_calls': 1, 'hosted_sql': {'agreement': 'primary=second', 'attempts': []},
                   'acceptance': {'status': 'low_confidence', 'basis': 'test'},
                   'steps': [{'stage': stage, 'receipt': {'cost_estimate': {'estimated_usd': 0.002}}}], '_cost': 0.002}
        return graph, receipt, stack.run_graph(graph, question=question)


def original(root, question, query, returns='count', route='local_compiler', graph=None):
    graph = graph or sql_graph(query, returns)
    result = run_graph_for(root)(graph, question)
    return graph, {'route': route, 'new_paid_api_calls': 0, 'steps': []}, result


WELLNESS_SQL = ("SELECT count(*) FROM record WHERE stream = 'wellness_visit_services' AND json_extract(payload,'$.hcpcs_code') = "
                "'G0438' AND substr(event_date,1,4) = '2029'")
DOLLAR_SQL = "SELECT sum(net_payer_paid_cents) / 100.0 FROM claim WHERE substr(first_adjudication_date,1,4) = '2029'"
PHQ_SQL = ("SELECT count(DISTINCT patient_id) FROM record WHERE stream = 'mood_questionnaires' AND json_extract(payload,'$.instrument') = "
           "'PHQ-9' AND json_extract(payload,'$.total') >= 10 AND substr(event_date,1,4) = '2029'")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = build(Path(self.tmp.name))
        self.profile = source_profile.load(self.root)
        self.env = mock.patch.dict(os.environ, {'AGERE_CROSS_CHECK': 'off', 'AGERE_GUARD_POLICY': 'abstain'})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def review(self, question, graph, receipt, result, hosted=None, live=True, frontier=None):
        return G.review(fake_stack(self.root, live), question, graph, receipt, result, ledger=SimpleNamespace(path=self.root / 'l.sqlite3') if live else None,
                        frontier=frontier, profile=self.profile, hosted=hosted)


class Suspicion(unittest.TestCase):
    def kinds(self, q, a): return {f['kind'] for f in G.suspicion(q, {'status': 'answered', **a})}

    def test_triggers(self):
        self.assertEqual(self.kinds(WELLNESS_Q, {'answer_type': 'count', 'count': 0}), {'zero'})
        self.assertEqual(self.kinds('Which patients had a PHQ-9 in 2029?', {'answer_type': 'patient_set', 'patient_ids': []}), {'empty'})
        self.assertIn('money_shape', self.kinds(DOLLAR_Q, {'answer_type': 'table', 'table': [{'rank': i, 'dollars_at_stake': 5} for i in range(7)], 'count': 7}))
        self.assertIn('money_shape', self.kinds(DOLLAR_Q, {'answer_type': 'count', 'count': 7}))
        self.assertEqual(self.kinds(DOLLAR_Q, {'answer_type': 'value', 'value': 7}), {'money_small'})
        self.assertEqual(self.kinds(DOLLAR_Q, {'answer_type': 'value', 'value': 3600.0}), set())
        self.assertEqual(self.kinds('What is the average copay paid per claim in 2029?', {'answer_type': 'value', 'value': 25}), set())
        self.assertIn('rate_range', self.kinds('What percent of patients had an AWV in 2029?', {'answer_type': 'value', 'value': 140, 'query': 'SELECT 100.0*a/b'}))

    def test_absence_questions_and_declines_are_not_suspicious(self):
        self.assertEqual(self.kinds('How many patients had no visit in 2029?', {'answer_type': 'count', 'count': 0}), set())
        self.assertEqual(G.suspicion(WELLNESS_Q, {'status': 'unsupported', 'answer_type': 'unsupported'}), [])

    def test_terms_keep_codes(self):
        self.assertIn('g0438', G.terms('G0438 visits'))
        self.assertIn('phq9', G.terms('a PHQ-9 score'))
        self.assertEqual(G.period_of('adjudicated in 2029'), ('2029-01-01', '2029-12-31'))


class Probe(Base):
    def test_unmodelled_stream_is_found_with_period_rows(self):
        p = G.probe(WELLNESS_Q, self.profile, path=self.root)
        top = p['candidates'][0]
        self.assertEqual(top['stream'], 'wellness_visit_services'); self.assertTrue(top['likely'])
        self.assertEqual(top['period_records'], 4)
        self.assertEqual(G.describe_source(top), "wellness_visit_services has 3 records in 2029 with visit_type 'medicare_annual_wellness' / hcpcs_code 'G0438'")
        self.assertTrue(G.probe('How many G0438 services in 2029?', self.profile)['candidates'][0]['code_match'])

    def test_no_source_anywhere(self):
        p = G.probe('How many patients had a hip replacement in 2029?', self.profile, path=self.root)
        self.assertFalse([c for c in p['candidates'] if c['likely']])
        self.assertEqual(p['streams_searched'], 5)

    def test_rows_outside_the_period_are_not_a_source(self):
        p = G.probe('How many Medicare annual wellness visits in 2031?', self.profile, path=self.root)
        self.assertFalse([c for c in p['candidates'] if c['likely']])

    def test_used_sources(self):
        streams = self.profile['streams']
        self.assertEqual(G.used_sources(sql_graph('SELECT count(*) FROM visit'), {}, streams), {'encounters'})
        self.assertIn('mood_questionnaires', G.used_sources(sql_graph("SELECT count(*) FROM measurement WHERE concept='phq9'"), {}, streams))
        self.assertIn('wellness_visit_services', G.used_sources(sql_graph(WELLNESS_SQL), {}, streams))


class UnmodelledStream(Base):
    """'Medicare annual wellness visits in 2029' -> 0 from a visit-type filter; the data sat in wellness_visit_services."""
    def zero(self):
        return original(self.root, WELLNESS_Q, "SELECT count(*) FROM visit WHERE type = 'wellness' AND substr(event_date,1,4) = '2029'")

    def test_replans_with_the_source_exposed(self):
        hosted = Hosted(self.root, {'default': (WELLNESS_SQL, 'count')})
        graph, receipt, result, calls = self.review(WELLNESS_Q, *self.zero(), hosted=hosted)
        self.assertEqual(result['answer']['count'], 3)
        self.assertEqual(receipt['answer_guard']['decision'], 'replanned')
        self.assertEqual(receipt['replaced_route']['route'], 'local_compiler')
        self.assertIn('wellness_visit_services', hosted.calls[0]['extra_streams'])
        self.assertIn("'G0438'", hosted.calls[0]['feedback'])
        self.assertEqual(calls, 1)
        self.assertAlmostEqual(economics(receipt)['known_api_estimate_usd'], 0.002)
        out = G.finish(result, receipt)
        self.assertTrue(out['answer']['confidence_detail']['replanned'])

    def test_abstains_naming_the_source_when_the_replan_fails(self):
        graph, receipt, result, calls = self.review(WELLNESS_Q, *self.zero(), hosted=Hosted(self.root, fail=True))
        a = G.finish(result, receipt)['answer']
        self.assertEqual(receipt['answer_guard']['decision'], 'abstained')
        self.assertEqual(a['answer_type'], 'unsupported'); self.assertIsNone(a['count']); self.assertEqual(a['withheld']['count'], 0)
        self.assertIn("wellness_visit_services has 3 records in 2029 with visit_type 'medicare_annual_wellness'", a['missing'][-1])
        self.assertIn("that this answer didn't read", a['missing'][-1])
        self.assertEqual(a['confidence'], 'low')
        self.assertEqual(calls, 2); self.assertAlmostEqual(economics(receipt)['known_api_estimate_usd'], 0.003)

    def test_abstains_offline_and_low_policy_keeps_the_number(self):
        _, receipt, result, _ = self.review(WELLNESS_Q, *self.zero(), live=False)
        self.assertEqual(result['answer']['answer_type'], 'unsupported')
        with mock.patch.dict(os.environ, {'AGERE_GUARD_POLICY': 'low'}):
            _, receipt, result, _ = self.review(WELLNESS_Q, *self.zero(), live=False)
            a = G.finish(result, receipt)['answer']
        self.assertEqual((a['count'], a['confidence']), (0, 'low'))
        self.assertTrue(any(c['control'] == 'QC-CATALOG' for c in a['quality_checks']))

    def test_replan_that_is_still_zero_without_reading_the_source_abstains(self):
        hosted = Hosted(self.root, {'default': ("SELECT count(*) FROM visit WHERE type = 'awv'", 'count')})
        _, receipt, result, _ = self.review(WELLNESS_Q, *self.zero(), hosted=hosted)
        self.assertEqual(receipt['answer_guard']['decision'], 'abstained')
        self.assertEqual(result['answer']['answer_type'], 'unsupported')


class MisroutedDollar(Base):
    """A dollar question routed to the claims-review engine answered with 7 review flags."""
    def flags(self):
        answer = {'status': 'answered', 'answer_type': 'table', 'count': 7, 'value': '7 review flag(s) across 3 claims',
                  'table': [{'rank': i, 'category': 'denial', 'dollars_at_stake': 100 * i} for i in range(7)], 'missing': []}
        graph = {'interpretation': DOLLAR_Q, 'coverage': 'supported_observation', 'unmet_requirements': [],
                 'nodes': [{'id': 'review', 'operation': 'claims_review', 'inputs': [], 'parameters': {'top': 7}}]}
        return graph, {'route': 'operator_router', 'operator': 'claims_review', 'new_paid_api_calls': 1, 'steps': []}, {'answer': answer, 'new_paid_api_calls': 0}

    def test_replans_to_a_dollar_amount(self):
        hosted = Hosted(self.root, {'default': (DOLLAR_SQL, 'value')})
        _, receipt, result, _ = self.review(DOLLAR_Q, *self.flags(), hosted=hosted)
        self.assertEqual(receipt['answer_guard']['decision'], 'replanned')
        self.assertEqual(result['answer']['value'], 3600.0)
        self.assertEqual(receipt['answer_guard']['triggers'][0]['kind'], 'money_shape')

    def test_abstains_when_the_replan_still_counts(self):
        hosted = Hosted(self.root, {'default': ('SELECT count(*) FROM claim', 'count')})
        _, receipt, result, _ = self.review(DOLLAR_Q, *self.flags(), hosted=hosted)
        a = G.finish(result, receipt)['answer']
        self.assertEqual((receipt['answer_guard']['decision'], a['answer_type'], a['confidence']), ('abstained', 'unsupported', 'low'))
        self.assertIn('medical_claims', a['missing'][-1])


class EmptyMeasurement(Base):
    """PHQ-9 questions -> 0 rows: the questionnaire totals were not reaching the measurement query."""
    def zero(self, question=PHQ_Q):
        return original(self.root, question, "SELECT count(DISTINCT patient_id) FROM measurement WHERE concept = 'phq9' AND value >= 10",
                        route='metric_router')

    def test_replans(self):
        _, receipt, result, _ = self.review(PHQ_Q, *self.zero(), hosted=Hosted(self.root, {'default': (PHQ_SQL, 'count')}))
        self.assertEqual((receipt['answer_guard']['decision'], result['answer']['count']), ('replanned', 2))

    def test_offline_abstention_names_the_source(self):
        _, receipt, result, _ = self.review(PHQ_Q, *self.zero(), live=False)
        msg = result['answer']['missing'][-1]
        self.assertIn("mood_questionnaires has 4 records in 2029 with instrument 'PHQ-9'", msg)
        self.assertIn('returned nothing from it', msg)


class LegitimateZero(Base):
    def test_nothing_anywhere_answers_zero_with_a_catalog_check(self):
        q = 'How many patients had a hip replacement in 2029?'
        hosted = Hosted(self.root, {'default': ('SELECT 1', 'count')})
        _, receipt, result, calls = self.review(q, *original(self.root, q, "SELECT count(*) FROM visit WHERE type = 'hip replacement'"), hosted=hosted)
        a = result['answer']
        self.assertEqual((a['count'], a['status'], receipt['answer_guard']['decision']), (0, 'answered', 'zero_confirmed_by_catalog'))
        self.assertEqual(hosted.calls, []); self.assertEqual(calls, 0)
        check = next(c for c in a['quality_checks'] if c['control'] == 'QC-CATALOG')
        self.assertEqual(check['severity'], 'info'); self.assertIn('searched all 5 received streams', check['message'])

    def test_zero_confirmed_by_a_replan_that_read_the_source(self):
        q = 'How many patients had a PHQ-9 score of 30 or more in 2029?'
        hosted = Hosted(self.root, {'default': (PHQ_SQL.replace('>= 10', '>= 30'), 'count')})
        _, receipt, result, _ = self.review(q, *original(self.root, q, "SELECT count(*) FROM measurement WHERE concept = 'phq9' AND value >= 30",
                                                         route='metric_router'), hosted=hosted)
        self.assertEqual((receipt['answer_guard']['decision'], result['answer']['count']), ('zero_confirmed_by_replan', 0))
        self.assertTrue(any('also found none' in c['message'] for c in result['answer']['quality_checks']))

    def test_guard_switch(self):
        with mock.patch.dict(os.environ, {'AGERE_ANSWER_GUARD': 'off'}):
            _, receipt, result, _ = self.review(WELLNESS_Q, *original(self.root, WELLNESS_Q, 'SELECT count(*) FROM visit WHERE 0'), live=False)
        self.assertEqual(result['answer']['count'], 0); self.assertNotIn('triggers', receipt['answer_guard'])


class CrossCheck(Base):
    def setUp(self):
        super().setUp()
        self.env2 = mock.patch.dict(os.environ, {'AGERE_CROSS_CHECK': 'on'}); self.env2.start()
        self.q = 'How many patients had an LDL test in 2029?'
        self.orig = original(self.root, self.q, "SELECT count(DISTINCT patient_id) FROM measurement WHERE concept = 'ldl'")

    def tearDown(self):
        self.env2.stop(); super().tearDown()

    def test_agreement_marks_cross_checked_and_records_cost(self):
        hosted = Hosted(self.root, {'default': ("SELECT count(DISTINCT patient_id) FROM record WHERE stream = 'labs'", 'count')})
        _, receipt, result, calls = self.review(self.q, *self.orig, hosted=hosted)
        cc = receipt['answer_guard']['cross_check']
        self.assertEqual((cc['status'], cc['path']), ('agree', 'hosted_verified_sql'))
        self.assertEqual(hosted.calls[0]['stage'], 'cross_check')
        free = hosted.calls[0]['independent'][0](self.q)
        self.assertEqual((free['label'], free['result']), ('local_compiler', {'value': 3}))  # the original answer is a free candidate
        self.assertEqual(calls, 1); self.assertAlmostEqual(economics(receipt)['known_api_estimate_usd'], 0.002)
        a = G.finish(result, receipt)['answer']
        self.assertTrue(a['confidence_detail']['cross_checked'])
        self.assertEqual(a['count'], 3)

    def test_disagreement_without_tiebreak_returns_both_readings_low(self):
        hosted = Hosted(self.root, {'default': ("SELECT count(*) FROM record WHERE stream IN ('labs', 'encounters')", 'count')})
        with mock.patch.dict(os.environ, {'AGERE_CROSS_CHECK_FRONTIER': 'off'}):
            _, receipt, result, _ = self.review(self.q, *self.orig, hosted=hosted)
        a = G.finish(result, receipt)['answer']
        self.assertEqual(receipt['answer_guard']['cross_check']['status'], 'disagree')
        self.assertEqual(a['confidence'], 'low'); self.assertEqual(len(a['readings']), 2)
        self.assertEqual(a['count'], 3)  # the default reading is unchanged

    def test_frontier_breaks_the_tie(self):
        hosted = Hosted(self.root, {'default': ("SELECT count(*) FROM record WHERE stream = 'labs' AND patient_id <> 'P0'", 'count')})
        frontier = lambda cap, feedback=None: (sql_graph("SELECT 2"), {'cost_estimate': {'estimated_usd': 0.05}})
        with mock.patch('clinical_core.agere.clinical.analysis_graph.catalog', return_value={}):
            graph, receipt, result, calls = self.review(self.q, *self.orig, hosted=hosted, frontier=frontier)
        self.assertEqual(receipt['answer_guard']['cross_check']['status'], 'replaced')
        self.assertEqual(result['answer']['count'], 2); self.assertEqual(receipt['replaced_route']['route'], 'local_compiler')
        self.assertEqual(calls, 2); self.assertAlmostEqual(economics(receipt)['known_api_estimate_usd'], 0.052)

    def test_hosted_route_with_independent_agreement_costs_nothing(self):
        graph, receipt, result = self.orig
        receipt = {'route': 'hosted_verified_sql', 'new_paid_api_calls': 2, 'steps': [], 'acceptance': {'status': 'verified'},
                   'hosted_sql': {'verification': {'status': 'accepted', 'basis': 'independent'}}}
        hosted = Hosted(self.root, {'default': ('SELECT 1', 'count')})
        _, receipt, _, calls = self.review(self.q, graph, receipt, result, hosted=hosted)
        self.assertEqual(receipt['answer_guard']['cross_check']['status'], 'agree'); self.assertEqual((calls, hosted.calls), (0, []))

    def test_certified_path_table_answers_and_switch_skip(self):
        fake = types.ModuleType('gate_policy')
        fake.assess = lambda receipt, encoder=None: {'level': 'verified', 'reason': 'certified', 'gates': []}
        hosted = Hosted(self.root, {'default': ('SELECT 1', 'count')})
        with mock.patch.dict(sys.modules, {'clinical_core.agere.clinical.gate_policy': fake}):
            _, receipt, _, _ = self.review(self.q, *self.orig, hosted=hosted)
        self.assertEqual(receipt['answer_guard']['cross_check']['status'], 'skipped')
        self.assertEqual(receipt['answer_guard']['cross_check']['certification']['source'], 'gate_policy')
        fake.assess = lambda receipt, encoder=None: {'level': 'unverified', 'reason': 'no certificate', 'gates': []}
        with mock.patch.dict(sys.modules, {'clinical_core.agere.clinical.gate_policy': fake}):
            self.assertFalse(G.certification({'route': 'local_compiler'})['certified'])
        tab = original(self.root, self.q, 'SELECT patient_id, value FROM measurement', returns='table')
        _, receipt, _, _ = self.review(self.q, *tab, hosted=hosted)
        self.assertEqual(receipt['answer_guard']['cross_check']['status'], 'skipped')
        with mock.patch.dict(os.environ, {'AGERE_CROSS_CHECK': 'off'}):
            _, receipt, _, _ = self.review(self.q, *self.orig, hosted=hosted)
        self.assertEqual(receipt['answer_guard']['cross_check']['status'], 'off'); self.assertEqual(hosted.calls, [])

    def test_fallback_certification_without_gate_policy(self):
        with mock.patch.dict(sys.modules, {'clinical_core.agere.clinical.gate_policy': None}):
            c = G.certification({'route': 'local_compiler'})
        self.assertEqual((c['certified'], c['source']), (False, 'fallback'))


class WriterSlice(Base):
    """The hosted SQL writer sees the raw streams the catalog probe matches, with exact values, within the byte bound."""
    def test_slice_in_the_writer_prompt(self):
        seen = []

        def model(system, user, max_tokens=700, temperature=0.0, seed=None):
            seen.append(user)
            return f'```sql\n{WELLNESS_SQL}\n```', {'model': 'fake', 'api_usd': 0.0, 'cache_hit': False}
        shown = (['encounters'], 'TABLES\n' + nl2sql.TABLES + '\n\nRELEVANT RECORD STREAMS\n' + nl2sql.stream_line(self.profile, 'encounters'))
        with mock.patch.dict(os.environ, {'AGERE_HARVEST_SHOTS': 'off', 'AGERE_METRIC_PINS': 'off'}), \
                mock.patch.object(nl2sql, 'schema_for', return_value=shown):
            params, result, receipt = nl2sql._verified_answer(self.root, WELLNESS_Q, AS_OF, model, model, model)
        self.assertIn('RECEIVED STREAMS MATCHING THE QUESTION', seen[0])
        block = seen[0].split('RECEIVED STREAMS MATCHING THE QUESTION', 1)[1].split('\n\nEXAMPLES', 1)[0]
        self.assertLessEqual(len(block), G.RAW_SLICE_BYTES)
        self.assertIn('wellness_visit_services', block); self.assertIn("'medicare_annual_wellness'", block)
        self.assertEqual(result['value'], 3)
        self.assertEqual(receipt['raw_catalog']['streams'][0], 'wellness_visit_services')
        with mock.patch.dict(os.environ, {'AGERE_RAW_CATALOG': 'off'}):
            self.assertEqual(G.raw_catalog_slice(WELLNESS_Q, self.profile), ('', None))

    def test_hosted_sql_forces_extra_streams(self):
        seen = []

        class Model:
            def __init__(self, label): self.model, self.cost, self.label = label, 0.0, label

            def __call__(self, system, user, max_tokens=700, temperature=0.0, seed=None):
                seen.append(user); self.cost += 0.001
                return f'```sql\n{WELLNESS_SQL}\n```', {'model': self.label, 'api_usd': 0.001, 'cache_hit': False}
        env = {'AGERE_SCHEMA_RERANK': 'off', 'AGERE_HARVEST_SHOTS': 'off', 'AGERE_METRIC_PINS': 'off', 'AGERE_VERIFICATION_V2': 'off',
               'AGERE_RAW_CATALOG': 'off'}
        with mock.patch.dict(os.environ, env), mock.patch.object(G, '_models', return_value=(Model('p'), Model('s'), Model('j'), None)):
            graph, receipt, result = G.hosted_sql(fake_stack(self.root), WELLNESS_Q, SimpleNamespace(path=self.root / 'l.sqlite3'),
                                                  extra_streams=('wellness_visit_services',), feedback='\n\nNOTE: read wellness_visit_services.')
        self.assertIn("stream 'wellness_visit_services'", seen[0]); self.assertIn('NOTE: read wellness_visit_services.', seen[0])
        self.assertEqual(result['answer']['count'], 3)
        self.assertEqual(receipt['route'], 'hosted_verified_sql'); self.assertAlmostEqual(receipt['_cost'], 0.002)


if __name__ == '__main__':
    unittest.main()
