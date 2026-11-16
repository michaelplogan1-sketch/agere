"""Answer shape, windows, fills (review of the heldout9 v2.8 misses). Own phrasings and fixtures only;
no network, no paid call. Covers local_compiler (grouped literal counts keep an 'unknown' group and count versioned entities,
ALOS by discharge, present-tense medication, days' supply across duplicate fill sources,
no-show denominators, social-need referral gaps, adjudication anchor), clinical_events windows for undated records, concept
code bindings, analysis_graph answer shapes and VBC value columns, metric_router breakdowns, answer_guard relaxed paths and
the nl2sql judge's missing-data answer."""
import json, os, sqlite3, tempfile, unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.clinical import (analysis_graph, answer_guard as G, clinical_events, cohorts, concepts, local_compiler as lc,
                                          metric_router as MR, nl2sql)

AS_OF = '2030-05-27'


def _f(values=None, populated=100, elements=None):
    spec = {'populated': populated, 'types': {'text': populated}, 'distinct': len(values or {}) or 50}
    if values: spec['values'] = values
    if elements: spec['element_values'] = elements
    return spec


PROFILE = {'source_digest': 'fixture', 'patients': {'records': 500, 'fields': {}}, 'measurements': {
    'labs': [{'name': 'Urine albumin/creatinine ratio', 'unit': 'mg/g', 'records': 50, 'numeric': 50, 'range': [1, 300]}]},
    'streams': {
        'appointments': {'records': 400, 'fields': {
            'appointment_id': _f(), 'event_date': _f(), 'patient_id': _f(), 'scheduled_date': _f(),
            'status': _f({'cancelled': 50, 'fulfilled': 300, 'no_show': 50}),
            'cancellation_channel': _f({'patient_portal': 20, 'patient_call': 20})}},
        'labs': {'records': 900, 'fields': {'name': _f(), 'value': _f(), 'unit': _f(), 'event_date': _f(), 'patient_id': _f(),
                                            'loinc': _f(), 'local_code': _f()}},
        'social_screens': {'records': 300, 'fields': {
            'food_difficulty_reported': {'populated': 300, 'types': {'true': 40, 'false': 260}, 'distinct': 2},
            'transport_difficulty_reported': {'populated': 300, 'types': {'true': 40, 'false': 260}, 'distinct': 2},
            'positive_domains': _f(elements={'food_worry': 10, 'food_ran_out': 8, 'transportation_barrier': 6}),
            'instrument': _f({'AHC_HRSN': 100, 'brief_clinical_needs_interview': 200}), 'event_date': _f(), 'patient_id': _f()}},
        'referrals': {'records': 200, 'fields': {'referral_type': _f({'food_resource_contact': 30, 'cardiology': 100}),
                                                 'event_date': _f(), 'patient_id': _f()}},
        'community_service_referrals': {'records': 40, 'fields': {'service_requested': _f({'food_access': 15, 'housing_navigation': 25}),
                                                                  'event_date': _f(), 'patient_id': _f()}},
        'encounters': {'records': 900, 'fields': {'type': _f({'office': 600, 'ED': 200, 'inpatient': 100}), 'event_date': _f(),
                                                  'patient_id': _f()}},
        'adt': {'records': 100, 'fields': {'event': _f({'admission': 50, 'discharge': 50}), 'event_date': _f(), 'patient_id': _f()}},
        'conditions': {'records': 900, 'fields': {'description': _f(), 'icd10': _f(), 'event_date': _f(), 'patient_id': _f()}},
    }}


def compile_(q):
    return lc.compile(q, PROFILE, AS_OF)


def sql_of(q):
    g, info = compile_(q)
    assert g is not None, info
    node = g['nodes'][-1]
    assert node['operation'] == 'sql', node
    return node['parameters']['query'], node['parameters']['returns'], node.get('purpose', '')


class GroupedLiteralCounts(unittest.TestCase):
    def test_missing_channel_is_its_own_group_and_appointments_are_entities(self):
        sql, returns, _ = sql_of('In 2028, how many appointments were cancelled and via which channel?')
        self.assertEqual(returns, 'table')
        self.assertIn("coalesce(json_extract(payload,'$.cancellation_channel'), 'unknown')", sql)
        self.assertIn("count(DISTINCT json_extract(payload,'$.appointment_id'))", sql)
        self.assertNotIn('IS NOT NULL', sql)

    def test_explicit_exclusion_drops_the_unknown_group(self):
        sql, _, _ = sql_of('Count the cancelled appointments in 2028 by cancellation channel, excluding missing channels')
        self.assertIn("json_extract(payload,'$.cancellation_channel') IS NOT NULL", sql)

    def test_record_counts_stay_row_counts(self):
        sql, _, _ = sql_of('How many appointment records were cancelled in 2028?')
        self.assertTrue(sql.startswith('SELECT count(*)'))


class LengthOfStay(unittest.TestCase):
    def test_alos_for_a_period_is_by_discharge(self):
        sql, _, purpose = sql_of('What is our average length of stay for 2028?')
        self.assertIn("discharge_date BETWEEN '2028-01-01' AND '2028-12-31'", sql)
        self.assertIn('discharge_known = 1', sql)
        self.assertNotIn('stay_source', sql)  # 'our' = the whole patients view (stays from EHR, ADT and payer claims)

    def test_alos_admitted_anchor(self):
        sql, _, _ = sql_of('What was the average length of stay for patients admitted in 2028?')
        self.assertIn("admit_date BETWEEN '2028-01-01'", sql)


class ClaimsByPayer(unittest.TestCase):
    def test_adjudication_anchor_and_payer_label(self):
        sql, returns, _ = sql_of('Net paid by payer for claims processed during 2028')
        self.assertEqual(returns, 'table')
        self.assertIn("first_adjudication_date BETWEEN '2028-01-01' AND '2028-12-31'", sql)
        self.assertIn("organization_type = 'payer'", sql)
        self.assertIn("ELSE 'unknown'", sql)


class Medication(unittest.TestCase):
    def test_present_tense_is_current_therapy(self):
        sql, _, purpose = sql_of('How many diabetics are currently on metformin?')
        self.assertIn("event_date BETWEEN '2029-05-28' AND '2030-05-27'", sql)
        self.assertIn('currently on', purpose)

    def test_ever_keeps_full_history(self):
        sql, _, _ = sql_of('How many patients have ever filled a statin?')
        self.assertNotIn('BETWEEN', sql)

    def test_days_supply_counts_a_pickup_once_across_sources(self):
        sql, returns, purpose = sql_of("Which patients picked up 90 days' supply of metformin in 2028?")
        self.assertEqual(returns, 'patient_set')
        self.assertIn('max(days)', sql)  # one pickup per patient and date across the pharmacy and payer sources
        self.assertIn('GROUP BY 1, 2, 3', sql)
        self.assertIn('HAVING sum(days) >= 90', sql)
        self.assertIn("days_supply_reported", sql)
        self.assertIn('counts once', purpose)

    def test_days_supply_semantics_run(self):
        sql, _, _ = sql_of("How many patients picked up more than 60 days' supply of statins in 2028?")
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE medication(record_id TEXT, patient_id TEXT, event_date TEXT, source_stream TEXT, drug_class TEXT, '
                   'evidence TEXT, status TEXT, fill_reversed INTEGER)')
        db.execute('CREATE TABLE record(record_id TEXT, payload TEXT)')
        rows = [('a', 'P1', '2028-02-01', 'pharmacy_dispenses', 'dispensed', 'dispensed', {'days_supply_reported': 30}),
                ('b', 'P1', '2028-02-01', 'payer_pharmacy_claims', 'payer_claim', 'paid', {'days_supply': 30}),  # same pickup
                ('c', 'P1', '2028-05-01', 'payer_pharmacy_claims', 'payer_claim', 'paid', {'days_supply': 30}),
                ('d', 'P2', '2028-03-01', 'payer_pharmacy_claims', 'payer_claim', 'paid', {'days_supply': 90}),
                ('e', 'P2', '2028-04-01', 'pharmacy_dispenses', 'dispensed', 'prepared', {'days_supply_reported': 90})]
        for rid, pid, d, src, ev, st, payload in rows:
            db.execute('INSERT INTO medication VALUES (?,?,?,?,?,?,?,0)', (rid, pid, d, src, 'statin', ev, st))
            db.execute('INSERT INTO record VALUES (?,?)', (rid, json.dumps(payload)))
        self.assertEqual(db.execute(sql).fetchone()[0], 1)  # P1 = 60 days (not 90), P2 = 90 days (prepared fill never counts)


class NoShow(unittest.TestCase):
    def test_excluding_cancellations_is_the_default_definition(self):
        sql, returns, purpose = sql_of('What was the appointment no-show rate for visits scheduled in 2028, excluding cancellations?')
        self.assertEqual(returns, 'value')
        self.assertIn('sum(ever_no_show = 1 AND kept = 0)', sql)
        self.assertNotIn("outcome = 'cancelled'", sql)

    def test_including_cancellations_is_stated(self):
        sql, _, purpose = sql_of('What was the no-show rate in 2028 including cancellations?')
        self.assertIn("outcome = 'cancelled'", sql)
        self.assertIn('kept + no-shows + cancelled', purpose)


class SocialNeedReferralGap(unittest.TestCase):
    def spec(self, q):
        g, info = compile_(q)
        self.assertIsNotNone(g, info)
        return g['nodes'][-1]['parameters']['specification']

    def test_positive_screen_reads_every_instrument(self):
        s = self.spec('Which patients screened positive for food insecurity in 2028?')
        conds = [c for a in s['any'] for c in a['conditions']]
        self.assertIn({'field': 'positive_domains', 'op': 'contains', 'value': 'food_worry'}, conds)
        self.assertIn({'field': 'food_difficulty_reported', 'op': 'eq', 'value': True}, conds)

    def test_no_referral_on_record_excludes_any_time(self):
        s = self.spec('Which patients screened positive for food insecurity in 2028 but have no food resource referral on record?')
        excluded = {(a['stream'], a['conditions'][0]['value']): a['window'] for a in s['exclude']}
        self.assertEqual(set(excluded), {('referrals', 'food_resource_contact'), ('community_service_referrals', 'food_access')})
        self.assertTrue(all(w['basis'] == 'all_observed' for w in excluded.values()))

    def test_no_referral_in_the_period_is_bounded(self):
        s = self.spec('Which patients screened positive for food insecurity in 2028 but had no food referral in 2028?')
        self.assertTrue(all(a['window']['start'] == '2028-01-01' for a in s['exclude']))


class UndatedRecords(unittest.TestCase):
    W = {'basis': 'source_event', 'start': '2029-01-01', 'end': '2029-12-31'}

    def rec(self, table, arrived):
        return {'record_id': 'R1', 'patient_id': 'P1', 'source_table': table, 'event_date': None, 'recorded_date': arrived,
                'available_date': arrived}

    def test_received_before_the_window_is_outside_it(self):
        self.assertIs(clinical_events.window_state(self.rec('labs', '2026-09-01'), self.W, AS_OF), False)

    def test_received_during_the_window_stays_unknown(self):
        self.assertIsNone(clinical_events.window_state(self.rec('labs', '2029-06-01'), self.W, AS_OF))

    def test_planned_events_received_early_stay_unknown(self):
        self.assertIsNone(clinical_events.window_state(self.rec('appointments', '2026-09-01'), self.W, AS_OF))


class MeasurementCodes(unittest.TestCase):
    def test_loinc_and_local_code_identify_the_analyte(self):
        res = concepts.resolve('uacr', PROFILE)
        b = res['bindings'][0]
        self.assertIn('14959-1', b['codes']['loinc']); self.assertIn('MALB/CR', b['codes']['local_code'])
        r = {'source_table': 'labs', 'name': 'MICROALB/CREAT RATIO, RANDOM URINE', 'local_code': 'MALB/CR'}
        self.assertTrue(cohorts._concept_matches(b, r))
        self.assertTrue(cohorts._concept_matches(b, {'source_table': 'labs', 'name': 'Albumin/Creat [Mass Ratio]', 'loinc': '9318-7'}))
        self.assertFalse(cohorts._concept_matches(b, {'source_table': 'labs', 'name': 'Creatinine', 'loinc': '2160-0'}))


class AnswerShapes(unittest.TestCase):
    def test_pmpm_is_a_value(self):
        self.assertEqual(analysis_graph.requested_shape('What was our medical PMPM on a paid basis for 2028?'), 'value')
        self.assertEqual(analysis_graph.requested_shape('Medical PMPM by practice for 2028'), 'table')

    def test_a_numeric_by_is_not_a_breakdown(self):
        self.assertNotEqual(analysis_graph.requested_shape('Which members gained weight by at least 5 kg?'), 'table')

    def test_vbc_value_column_follows_the_question(self):
        col = lambda q: next(c for p, c in analysis_graph.VBC_VALUE_COLUMNS if __import__('re').search(p, q))
        self.assertEqual(col('what was our medical pmpm on an allowed-amount basis for 2028?'), 'medical_allowed_pmpm')
        self.assertEqual(col('pharmacy pmpm, paid basis, 2028'), 'pharmacy_paid_pmpm')
        self.assertEqual(col('what was our pmpm in 2028?'), 'paid_pmpm')  # Mike 2026-10-25: default spend is payer paid.

    def test_patient_list_question(self):
        self.assertTrue(analysis_graph.patient_list_question('Which members had their PHQ-9 rise by 5 or more points?'))
        self.assertFalse(analysis_graph.patient_list_question('How many patients had a PHQ-9?'))

    def test_one_row_per_patient_table_answers_a_list_question(self):
        plan = {'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': {'query': 'SELECT 1', 'returns': 'table'}}]}
        report = {'returns': 'table', 'query': 'SELECT 1', 'truncated': False, 'row_count': 2,
                  'rows': [{'patient_id': 'P2', 'change': 6}, {'patient_id': 'P1', 'change': 5}]}
        answer = analysis_graph.build_answer(plan, [{'id': 'answer', 'result': report}], {}, 'Which members worsened by at least 5 points?')
        self.assertEqual((answer['answer_type'], answer['patient_ids'], answer['count']), ('patient_set', ['P1', 'P2'], 2))


class MetricRouterBreakdown(unittest.TestCase):
    def test_single_value_metric_declines_a_by_question(self):
        class Script:
            def __init__(self, *c): self.c = list(c)
            def __call__(self, question, as_of, tools, forced=None, feedback=None):
                name, args = self.c.pop(0); return {'name': name, 'input': args}, {'api_usd': 0.0, 'cache_hit': True}
        ask = Script((MR.tool_name('no_show_rate'), {}), (MR.tool_name('no_show_rate'), {'period': {'preset': 'year', 'year': 2028}}))
        graph, info = MR.route('What was the no-show rate in 2028 for each clinic?', AS_OF, ask=ask)
        self.assertIsNone(graph)
        self.assertIn('breakdown', json.dumps(info.get('declined')))

    def test_per_member_per_month_is_not_a_breakdown(self):
        import re
        low = re.sub(r'\bper (?:member|patient) per (?:month|year)\b|\bper 1,?000\b', ' ', 'cost per member per month in 2028')
        self.assertIsNone(re.search(MR.BREAKDOWN, low))


class RelaxedCrossCheck(unittest.TestCase):
    def test_relaxed_path_never_replaces_or_contradicts(self):
        class Pending:
            deadline, started = 1e18, 0.0
            def __init__(self, receipt): self.receipt = receipt
            def provide(self, answer): pass
            def cancel(self): pass
            def result(self):
                return ('local_compiler (relaxed)', {'nodes': []}, None,
                        {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 759}}, 0.0, 0)
        receipt = {'route': 'hosted_verified_sql', 'steps': [], 'local_compiler_declined': {'unexplained': ['uncontrolled']}}
        result = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 213}}
        stack = mock.Mock(allow_live=True)
        frontier = mock.Mock()
        with mock.patch.dict(os.environ, {'AGERE_CROSS_CHECK': 'on'}), \
                mock.patch.object(G, 'certification', return_value={'certified': False, 'level': 'unverified', 'reason': 'x'}):
            _, receipt, out = G.cross_check(stack, 'How many hypertensives are uncontrolled?', {'nodes': []}, receipt, result,
                                            ledger=object(), frontier=frontier, pending=Pending(receipt))
        cc = receipt['answer_guard']['cross_check']
        self.assertEqual(cc['status'], 'unavailable'); self.assertIn('uncontrolled', cc['reason'])
        self.assertEqual(out['answer']['count'], 213); frontier.assert_not_called()
        self.assertNotIn('readings', out['answer'])


class JudgeMissingData(unittest.TestCase):
    def test_judge_saying_missing_refuses(self):
        src = Path(nl2sql.__file__).read_text()
        # both ladders turn the judge's MISSING answer into a refusal instead of reading its reason as a query
        self.assertEqual(src.count("if final == 'missing'"), 2)


if __name__ == '__main__':
    unittest.main()
