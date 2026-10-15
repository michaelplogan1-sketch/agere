"""Answer contract fixes from blind adjudication: confidence separate from status, time anchors the question names (claims
adjudicated / first paid / cash received / reversed in, stays discharged in), full-precision governed SQL with descriptive
columns, a MISSING claim checked against the warehouse before abstaining (and its cost recorded), and per-1,000 labels
that state the arithmetic actually used. Synthetic fixtures only; every model call is a fake; no network."""
import json, os, sqlite3, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('AGERE_METRIC_ENCODER', 'off')

from clinical_core.agere.clinical import metric_router as MR, metric_families as MF, metrics, nl2sql, answer_checks, operator_router
from clinical_core.agere.clinical import economical_planner as planner
from test_metric_first import fixture_stack, Script, AS_OF
from bench.score import by_confidence, confidence_of, score_answer, summarize, valid_answer

YEAR = lambda y: {'period': {'preset': 'year', 'year': y}}


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = fixture_stack(Path(cls.tmp.name))
        db = sqlite3.connect(cls.root / 'semantic.sqlite3')
        db.execute('ALTER TABLE patient ADD COLUMN race TEXT')
        db.execute("UPDATE patient SET race = CASE WHEN patient_id = 'P0' THEN NULL ELSE 'white' END")
        db.commit(); db.close()
        cls.db = sqlite3.connect(f'file:{cls.root / "semantic.sqlite3"}?mode=ro', uri=True)
        cls.lib = MR.routable()

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.tmp.cleanup()

    def compute(self, mid, question='', **slots):
        m = self.lib[mid]
        node = MR.compile_metric(mid, m, MR.validate_slots(m, slots), AS_OF, question)
        cur = self.db.execute(node['parameters']['query'])
        return cur.fetchall(), [d[0] for d in cur.description], node


class ConfidenceContract(unittest.TestCase):
    ANSWER = {'status': 'answered', 'answer_type': 'value', 'value': 4.94, 'count': None, 'patient_ids': None, 'table': None,
              'missing': [], 'grounding': [], 'unknown_count': 0}

    def test_low_confidence_keeps_status_and_value(self):
        out = MR.finalize_answer(self.ANSWER, {'route': 'hosted_verified_sql', 'acceptance': {'status': 'low_confidence', 'basis': 'judge pick'}})
        self.assertEqual((out['status'], out['value'], out['confidence']), ('answered', 4.94, 'low'))
        self.assertEqual(out['confidence_detail']['policy'], 'low_confidence')
        self.assertTrue(valid_answer(out))

    def test_governed_answers_are_verified_only_with_a_certified_path(self):
        from clinical_core.agere.clinical import calibrated_thresholds as CT, gate_policy as GP
        info = {'resolution': {'metric': 'm', 'version': 1, 'definition': 'd', 'slots': {}, 'statement': 'computed as metric m@v1: d'}}
        receipt = {'route': 'metric_router', 'metric_router': info}
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 't.json'
            CT.write([], target)
            with mock.patch.dict(os.environ, {'AGERE_THRESHOLDS_FILE': str(target)}):
                gov = MR.finalize_answer(self.ANSWER, receipt)
            # a governed metric is deterministic, but its end-to-end error rate is not certified: unverified, not verified
            self.assertEqual((gov['confidence'], gov['status']), ('unverified', 'answered'))
            self.assertIn('m@v1', gov['confidence_reason']); self.assertIn('metric_router.answer', gov['confidence_reason'])
            self.assertTrue(valid_answer(gov))
            CT.write([CT.fit('metric_router.answer', [1.0] * 200, [False] * 200, 1.0, {'source': 'fixture'}, grid=[1.0],
                             bind=GP.identity('metric_router.answer'))], target)
            with mock.patch.dict(os.environ, {'AGERE_THRESHOLDS_FILE': str(target)}):
                gov = MR.finalize_answer(self.ANSWER, receipt)
            self.assertEqual(gov['confidence'], 'verified')
            self.assertIn('Learn-then-Test on 200', gov['confidence_reason'])
        with mock.patch.dict(os.environ, {'AGERE_CONFIDENCE_RULE': 'legacy'}):
            self.assertEqual(MR.finalize_answer(self.ANSWER, receipt)['confidence'], 'verified')

    def test_abstain_policy_still_withholds(self):
        with mock.patch.dict(os.environ, {'AGERE_FREEFORM_POLICY': 'abstain'}):
            out = MR.finalize_answer(self.ANSWER, {'route': 'hosted_verified_sql', 'acceptance': {'status': 'low_confidence', 'basis': 'x'}})
        self.assertEqual((out['answer_type'], out['value'], out['confidence']), ('unsupported', None, 'low'))

    def test_contradicted_abstention_is_low_confidence(self):
        unsupported = {**self.ANSWER, 'status': 'unsupported', 'answer_type': 'unsupported', 'value': None, 'missing': ['Missing input: race']}
        out = MR.finalize_answer(unsupported, {'route': 'hosted_missing_data', 'missing_check': {
            'claim': 'MISSING: race', 'contradicted': [{'name': 'patient.race', 'kind': 'semantic-layer column', 'rows': 7}]}})
        self.assertEqual((out['status'], out['confidence']), ('unsupported', 'low'))
        self.assertIn('patient.race', out['confidence_reason'])
        plain = MR.finalize_answer(unsupported, {'route': 'hosted_missing_data', 'missing_check': {'claim': 'x', 'contradicted': []}})
        self.assertNotIn('confidence', plain)


class BenchConfidence(unittest.TestCase):
    Q = {'id': 'X1', 'answer_type': 'value', 'tolerance': 0.01}
    EXP = {'value': 4.94}

    def row(self, value, confidence, status='answered'):
        answer = {'status': status, 'answer_type': 'value', 'value': value, 'count': None, 'patient_ids': None, 'table': None,
                  'unknown_count': 0, 'missing': [], 'grounding': []}
        if confidence is not None: answer['confidence'] = confidence
        return {'answer': answer, 'score': score_answer(self.Q, self.EXP, answer), 'latency_s': 1.0, 'estimated_cost_usd': 0.0}

    def test_valid_answer_accepts_the_confidence_field(self):
        for c in ('verified', 'low', {'level': 'governed'}, None):
            self.assertTrue(valid_answer(self.row(4.94, c)['answer']), c)
        self.assertFalse(valid_answer(self.row(4.94, 'maybe')['answer']))
        self.assertEqual(confidence_of({'confidence': {'level': 'governed'}}), 'verified')

    def test_strict_correctness_unchanged_and_precision_split(self):
        rows = [self.row(4.94, 'verified'), self.row(4.9, 'verified'), self.row(4.94, 'low'), self.row(5.5, 'low'),
                self.row(4.94, {'level': 'low'}, status='partial'), self.row(4.94, None)]
        self.assertTrue(rows[2]['score']['correct'])            # a low-confidence exact answer is correct
        self.assertFalse(rows[4]['score']['correct'])           # an old partial row still fails the strict rule
        bc = by_confidence(rows)
        self.assertEqual((bc['verified']['asserted'], bc['verified']['value_matches']), (2, 1))
        self.assertEqual((bc['low']['asserted'], bc['low']['value_matches'], bc['low']['correct']), (3, 2, 1))
        self.assertAlmostEqual(bc['low']['precision'], 2 / 3)
        self.assertEqual(bc['unlabelled']['asserted'], 1)
        self.assertIn('by_confidence', summarize(rows, 6))


class Anchors(Fixture):
    def test_claim_clock_follows_the_question(self):
        # CL0: service 2028-03-01, first paid adjudication 2028-03-20, cash 2028-04-01, reversal 2028-04-20
        rows, _, node = self.compute('days_to_payer_cash', 'For claims first paid in 2028, average days from service to payment?', **YEAR(2028))
        self.assertEqual((node['variant'], rows[0][0]), ('first_paid', 19.0))
        rows, _, node = self.compute('days_to_payer_cash', 'Days from service to cash for claims whose payment was received in 2028?', **YEAR(2028))
        self.assertEqual((node['variant'], rows[0][0]), ('cash_received', 30.0))   # CL0-CL2 all cash 2028-04-01: 31, 30, 29 days
        rows, _, node = self.compute('days_to_payer_cash', 'Average days to payer cash for 2028 services?', **YEAR(2028))
        self.assertNotIn('variant', node); self.assertIn('service_date BETWEEN', node['parameters']['query'])
        # the model can name the clock through the anchor slot
        self.assertIn('first_paid', MR.slots_of(self.lib['days_to_payer_cash'])['anchor']['values'])
        rows, _, node = self.compute('days_to_payer_cash', 'Average days to get paid in 2028?', anchor='first_paid', **YEAR(2028))
        self.assertEqual(node['variant'], 'first_paid')
        self.assertIn('anchored on claim.first_paid_date', MR.computed_as('days_to_payer_cash', self.lib['days_to_payer_cash'], {}, node))

    def test_reversed_and_discharged_anchors(self):
        rows, _, node = self.compute('claims_reversed', 'How many claims did the payer reverse in 2028?', **YEAR(2028))
        self.assertEqual(rows[0][0], 1)
        rows, _, _ = self.compute('claims_reversed', 'How many claims were reversed in 2029?', **YEAR(2029))
        self.assertEqual(rows[0][0], 0)
        rows, _, node = self.compute('average_length_of_stay', 'Average inpatient length of stay for stays discharged in 2029?', **YEAR(2029))
        self.assertEqual((node['variant'], rows[0][0]), ('discharged', 4.0))
        self.assertIn('discharge_date BETWEEN', node['parameters']['query'])

    def test_resolve_moves_the_time_anchor(self):
        r = metrics.resolve({'question': 'For claims first paid in 2029, what was the average number of days from service to payment?', 'as_of': AS_OF})
        self.assertEqual((r['id'], r['variant'], r['metric']['time_anchor']['field']), ('days_to_payer_cash', 'first_paid', 'claim.first_paid_date'))
        r = metrics.resolve({'question': 'What percent of billed dollars were denied on first submission for claims adjudicated in 2028?', 'as_of': AS_OF})
        self.assertEqual((r['id'], r['variant']), ('first_pass_denial_rate', 'billed_dollars_adjudicated'))
        self.assertIn('first_adjudication_date BETWEEN', r['sql'])

    def test_medical_claims_family_anchors(self):
        rows, _, _ = self.compute('medical_claims', measures=['avg_days_to_first_paid'], anchor='first_paid_date', **YEAR(2028))
        self.assertEqual(rows[0][0], 19.0)
        rows, _, _ = self.compute('medical_claims', measures=['claims'], anchor='reversal_date', **YEAR(2028))
        self.assertEqual(rows[0][0], 1)
        rows, _, _ = self.compute('medical_claims', measures=['claims'], anchor='first_adjudication_date', **YEAR(2028))
        self.assertEqual(rows[0][0], 3)
        rows, _, _ = self.compute('medical_claims', measures=['claims'], anchor='first_payer_cash_date', **YEAR(2029))
        self.assertEqual(rows[0][0], 0)

    def test_hosted_writer_is_told_the_clocks(self):
        for phrase in ('first_adjudication_date', '"first paid in"', '"reversed in"', '"discharged in"', 'drg_title', 'race'):
            self.assertIn(phrase.strip('"'), nl2sql.TABLES + nl2sql.VERIFY_RULES)


class Formatting(Fixture):
    def test_governed_sql_is_full_precision(self):
        for mid, m in metrics.library().items():
            for sql in [m.get('sql_template')] + [v.get('sql_template') for v in (m.get('variants') or {}).values()]:
                self.assertNotIn('round(', (sql or '').lower(), mid)
        rows, _, _ = self.compute('medical_claims', measures=['first_pass_denial_rate'], **YEAR(2028))
        self.assertAlmostEqual(rows[0][0], 100 / 3, places=9)
        self.assertNotIn('round(', ''.join(q for q, _ in nl2sql.EXAMPLES).lower())
        self.assertIn('Never round', nl2sql.VERIFY_RULES)

    def test_grouped_tables_name_the_group_field_and_the_measure(self):
        _, cols, _ = self.compute('encounter_volume', visit_classes=['ed'], group_by='facility', **YEAR(2029))
        self.assertEqual(cols, ['organization_id', 'ed_visits'])
        _, cols, _ = self.compute('encounter_volume', visit_classes=['ed', 'urgent_care'], group_by='month', **YEAR(2029))
        self.assertEqual(cols, ['month', 'visits'])
        self.assertIn('never generic names like\n  group, value', nl2sql.VERIFY_RULES)

    def test_fill_guidance_uses_picked_up(self):
        self.assertIn('picked_up = 1', nl2sql.VERIFY_RULES)
        self.assertIn('picked_up', nl2sql.TABLES)


class MissingClaims(Fixture):
    PROFILE = {'streams': {'reported_medication_entries': {'records': 20, 'fields': {
        'use_statement': {'populated': 20, 'values': {'reports_taking': 17, 'stopped': 3}, 'types': {}}}}}}

    def test_claimed_missing_elements_are_checked(self):
        found = nl2sql.missing_claim_check(self.root, 'MISSING: race (race is not stored; sex_reported is not a substitute)', self.PROFILE)
        self.assertEqual([(f['name'], f['rows']) for f in found], [('patient.race', 7)])
        found = nl2sql.missing_claim_check(self.root, 'MISSING: reports_taking statement', self.PROFILE)
        self.assertEqual(found[0]['stream'], 'reported_medication_entries')
        self.assertEqual(nl2sql.missing_claim_check(self.root, 'MISSING: household income; MISSING: risk scores', self.PROFILE), [])

    def test_contradicted_claim_gets_one_retry_naming_the_field(self):
        calls = []

        def ladder(path, question, as_of, *a, feedback='', **k):
            calls.append(feedback)
            if len(calls) == 1:
                raise nl2sql.MissingData('MISSING: race (not stored)', receipt={'attempts': [{'role': 'primary'}, {'role': 'second'}]})
            return {'query': 'SELECT 1', 'returns': 'count'}, {'value': 1}, {'attempts': [{'role': 'primary'}], 'agreement': 'x'}
        with mock.patch.object(nl2sql, '_verified_answer', side_effect=ladder), \
                mock.patch.object(nl2sql.source_profile, 'load', return_value=self.PROFILE):
            params, result, receipt = nl2sql.verified_answer(self.root, 'How many patients have no race recorded?', AS_OF, None, None, None)
        self.assertEqual(len(calls), 2); self.assertIn('patient.race', calls[1])
        self.assertEqual(receipt['missing_claim_contradicted']['found'][0]['name'], 'patient.race')
        self.assertEqual(len(receipt['prior_attempts']), 2)

        def always_missing(path, question, as_of, *a, feedback='', **k):
            raise nl2sql.MissingData('MISSING: race', receipt={'attempts': [{'role': 'primary'}]})
        with mock.patch.object(nl2sql, '_verified_answer', side_effect=always_missing), \
                mock.patch.object(nl2sql.source_profile, 'load', return_value=self.PROFILE):
            with self.assertRaises(nl2sql.MissingData) as ctx:
                nl2sql.verified_answer(self.root, 'How many patients have no race recorded?', AS_OF, None, None, None)
        self.assertEqual(ctx.exception.contradicted[0]['name'], 'patient.race')
        self.assertEqual(len(ctx.exception.receipt['prior_attempts']), 1)

    def test_planner_records_the_cost_of_a_missing_data_answer(self):
        class Model:
            def __init__(self, *a, model='m', **k): self.model, self.cost = model, 0.002

        def hosted(*a, **k):
            raise nl2sql.MissingData('MISSING: race', receipt={'attempts': [{'role': 'primary'}, {'role': 'second'}]},
                                     contradicted=[{'name': 'patient.race', 'kind': 'semantic-layer column', 'rows': 7}])
        stack = SimpleNamespace(path=self.root, receipts=self.root, allow_live=True,
                                retriever=SimpleNamespace(encoder=None, meta={'source_digest': 'fixture', 'snapshot': {'as_of': AS_OF}}))
        ps = [mock.patch('clinical_core.agere.clinical.source_profile.load', return_value={'streams': {}}),
              mock.patch('clinical_core.agere.clinical.local_compiler.compile', return_value=(None, {'route': 'fallback', 'reason': 'test'})),
              mock.patch.object(operator_router, 'route', return_value=(None, {'decision': 'literal_lookup', 'calls': []})),
              mock.patch('clinical_core.agere.clinical.nl2sql.verified_answer', side_effect=hosted),
              mock.patch('clinical_core.agere.clinical.nl2sql.HostedModel', Model),
              mock.patch('clinical_core.agere.clinical.nl2sql.Decider', Model),
              mock.patch('clinical_core.agere.gateway.BudgetLedger', Model),
              mock.patch.object(MR, '_ask', side_effect=lambda q, a, l, o, tools, forced=None, feedback=None, model=None:
                                Script((MR.NO_METRIC, {'reason': 'none'}))(q, a, tools)),
              mock.patch.dict(os.environ, {'AGERE_LOCAL_MODEL': 'off', 'AGERE_SEMANTIC_CACHE': 'off', 'AGERE_VERIFICATION_V2': 'off'})]
        for p in ps: p.start()
        try:
            graph, receipt = planner.plan(stack, 'How many patients have no race recorded?', SimpleNamespace(path=self.root / 'ledger.sqlite3'), None)
        finally:
            for p in ps: p.stop()
        self.assertEqual(receipt['route'], 'hosted_missing_data')
        self.assertEqual(receipt['new_paid_api_calls'], 2)
        econ = planner.economics(receipt)
        self.assertAlmostEqual(econ['new_api_estimate_usd'], 0.009)  # four hosted models (4 x 0.002) + the metric-router call; never $0
        self.assertEqual(receipt['missing_check']['contradicted'][0]['name'], 'patient.race')


class PlainCounts(Fixture):
    def test_word_rule(self):
        self.assertTrue(MR.plain_count_question('How many hypertensive members had their BP under control at the most recent 2028 reading?'))
        self.assertTrue(MR.plain_count_question('Quick one - how many patients with high blood pressure were uncontrolled last year?'))
        self.assertTrue(MR.plain_count_question('Number of diabetics with an A1c over 9'))
        self.assertFalse(MR.plain_count_question('What share of hypertensive patients had controlled BP in 2028?'))
        self.assertFalse(MR.plain_count_question('How many patients are in the CBP measure denominator?'))
        self.assertFalse(MR.plain_count_question('How many diabetics have a care gap for eye exams?'))

    def test_a_measure_rate_never_answers_a_plain_count(self):
        q = 'How many hypertensive members had their BP under control at the most recent 2028 reading?'
        ask = Script(MR_choose('measure.cbp'))
        graph, info = MR.route(q, AS_OF, ask=ask, path=self.root)
        self.assertIsNone(graph); self.assertEqual(info['decision'], MR.NO_METRIC)
        self.assertEqual(len(ask.calls), 1)                                   # no fill call is spent on the measure
        self.assertIn('plain count', info['declined'][0]['reason'])
        # two readings offered (measure + cohort): the cohort count answers
        amb = (MR.AMBIGUOUS, {'metrics': [MR.tool_name('measure.cbp'), MR.tool_name('patient_cohort')], 'difference': 'rate vs count'})
        ask = Script(amb, (MR.tool_name('patient_cohort'), {'population': [{'kind': 'condition', 'concepts': ['diabetes']}]}))
        graph, info = MR.route('How many diabetics do we have?', AS_OF, ask=ask, path=self.root)
        self.assertEqual((info['decision'], info['metric']), ('metric', 'patient_cohort'))
        # a rate question still reaches the measure
        ask = Script(MR_choose('measure.cbp'), (MR.tool_name('measure.cbp'), {'period': {'preset': 'year', 'year': 2028}}))
        graph, info = MR.route('What share of hypertensive patients had controlled BP in 2028?', AS_OF, ask=ask, path=self.root)
        self.assertEqual(info['metric'], 'measure.cbp')

    def test_fill_templates_count_picked_up_fills_only(self):
        db = sqlite3.connect(self.root / 'semantic.sqlite3')
        db.execute("INSERT INTO medication(record_id, patient_id, source_stream, evidence, picked_up, drug_name, drug_class, status, event_date) "
                   "VALUES ('D9', 'P6', 'pharmacy_dispenses', 'dispensed', 0, 'Diazepam 5 MG', 'benzodiazepine', 'returned_to_stock', '2029-02-01')")
        db.commit()
        try:
            sql = metrics.render_sql(metrics.get('beers_high_risk_medications_65'), metrics.parameters(metrics.get('beers_high_risk_medications_65'), '', AS_OF))
            self.assertIn('picked_up = 1', sql); self.assertNotIn("evidence IN", sql)
            self.assertEqual(db.execute(sql).fetchall(), [(1,)])                 # P6's returned-to-stock diazepam is not a fill
        finally:
            db.execute("DELETE FROM medication WHERE record_id = 'D9'"); db.commit(); db.close()


def MR_choose(mid): return (MR.tool_name(mid), {})


class RateLabels(unittest.TestCase):
    def test_per_1000_label_states_the_arithmetic_used(self):
        row = {'group': 'all', 'members': 4139, 'member_years': 4014.0008, 'ed_visits': 1508, 'ed_per_1000': 375.7}
        answer = {'status': 'answered', 'answer_type': 'value', 'value': 375.7, 'table': [row], 'missing': [], 'grounding': []}
        arith = answer_checks.rate_arithmetic(answer)
        self.assertEqual(arith['denominator'], 'member_years'); self.assertIn('member-years', arith['statement'])
        answer_checks.metric_findings(answer, 'What was our ED visit rate per 1,000 patients in 2029?', AS_OF)
        self.assertFalse(answer['computed_as']['conforms'])
        self.assertIn('member-years', answer['computed_as']['definition'])
        self.assertNotIn('not annualised', answer['computed'])
        # a headcount computation under the headcount reading conforms and says so
        row2 = {'members': 4000, 'ed_visits': 1500, 'ed_per_1000': 375.0}
        ok = {'status': 'answered', 'answer_type': 'value', 'value': 375.0, 'table': [row2], 'missing': [], 'grounding': []}
        answer_checks.metric_findings(ok, 'What was our ED visit rate per 1,000 patients in 2029?', AS_OF)
        self.assertIn('roster patients', ok['computed_as']['arithmetic'])
        self.assertNotEqual(ok['computed_as'].get('conforms'), False)

    def test_headcount_variant_computes_per_headcount(self):
        r = metrics.resolve({'question': 'ED visits per 1,000 patients in 2029?', 'as_of': AS_OF})
        self.assertEqual(r['variant'], 'per_headcount')
        self.assertIn('FROM patient', r['sql']); self.assertNotIn('julianday', r['sql'])


if __name__ == '__main__':
    unittest.main()
