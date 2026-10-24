"""The semantic analyst inherits the decision context by construction: definition views, arithmetic in code, a deterministic SQL audit.
Fake transports only (no Anthropic call, no local model); a synthetic fixture (test_decision_context.build + a duplicate chart)."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.clinical import analyst_definitions as AD
from clinical_core.agere.clinical import decision_context as DC
from clinical_core.agere.clinical import semantic_analyst as SA
from clinical_core.agere.clinical import sql_query
from clinical_core.agere.clinical.age import age_at
from test_decision_context import AS_OF, build


def make(path):
    """The decision-context fixture plus patient G: a duplicate chart of A (diabetic, Medicare, lab-positive) that must count once."""
    build(path)
    with sqlite3.connect(Path(path) / 'semantic.sqlite3') as db:
        db.execute("ALTER TABLE patient ADD COLUMN data_segment TEXT")
        db.execute("ALTER TABLE patient ADD COLUMN age_reported INTEGER")
        db.execute("UPDATE patient SET data_segment = 'deep_chart', age_reported = 99")
        db.execute("INSERT INTO patient VALUES ('G','1950-01-01','F','medicare','duplicate_chart', 99)")
        db.execute("INSERT INTO condition VALUES ('c7','G','conditions','diabetes','E11.9','2028-01-01',1,NULL)")
        db.execute("INSERT INTO measurement_latest VALUES ('G','potassium','2029-01-01',6.0,NULL,1,6.0,'m9',NULL)")
        db.execute("UPDATE patient SET coverage_type = 'dual' WHERE patient_id = 'E'")
        db.execute("UPDATE patient SET coverage_type = 'medigap' WHERE patient_id = 'F'")


def reply(*blocks, usage=None):
    return {'content': list(blocks), 'usage': usage or {'input_tokens': 10, 'output_tokens': 5}, 'stop_reason': 'end_turn'}


def tool(sql, n=1):
    return {'type': 'tool_use', 'id': f't{n}', 'name': 'query_sql', 'input': {'sql': sql}}


def text(obj):
    return {'type': 'text', 'text': json.dumps(obj)}


class Fake:
    """A scripted send(body) -> (response, cost, cache_hit); records every request body (no network)."""
    def __init__(self, *responses):
        self.responses, self.bodies = list(responses), []

    def __call__(self, body):
        self.bodies.append(body)
        return self.responses.pop(0), 0.001, False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        make(self.path)
        self.wh = SA.SemanticWarehouse(self.path / 'semantic.sqlite3', timeout_s=5)
        self.addCleanup(self.wh.close)
        patcher = patch.object(SA, 'semantic_context', return_value=('BASE DOCS', frozenset({'ex1'})))
        patcher.start(); self.addCleanup(patcher.stop)

    def q(self, sql):
        self.wh.define(AS_OF)
        return self.wh.query(sql, as_of=AS_OF)['rows']

    def scalar(self, sql):
        rows = self.q(sql)
        return next(iter(rows[0].values()))

    def scoped(self, question, sql, returns='count'):
        with DC.scope(question, AS_OF):
            return sql_query.execute(self.path, {'query': sql, 'returns': returns}, target=self.path / 'semantic.sqlite3')['value']


class Views(Base):
    def test_views_exist_on_the_readonly_connection_and_are_never_written_to_the_stack(self):
        created = self.wh.define(AS_OF)
        self.assertEqual(set(created), set(AD.VIEW_NAMES))
        with sqlite3.connect(f'file:{self.path / "semantic.sqlite3"}?mode=ro', uri=True) as raw:
            self.assertEqual(raw.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('living_patient','condition_member')").fetchone()[0], 0)
        # the guard still holds: writes, temp tables and the temp schema are denied
        for sql in ('CREATE TEMP TABLE x AS SELECT 1', 'SELECT * FROM sqlite_temp_master', 'INSERT INTO living_patient VALUES (1)'):
            with self.assertRaises(sqlite3.Error): self.q(sql)

    def test_living_patient_matches_the_decision_context(self):
        # roster A-F + duplicate chart G; D deceased, G duplicate -> A B C E F
        self.assertEqual(self.scalar('SELECT COUNT(*) AS n FROM living_patient'), 5)
        self.assertEqual(self.scalar('SELECT COUNT(*) AS n FROM living_patient'),
                         self.scoped('How many patients are there?', 'SELECT COUNT(DISTINCT patient_id) FROM patient'))

    def test_age_is_from_the_birth_date_at_the_as_of_date(self):
        for patient_id, born in (('A', '1950-01-01'), ('B', '1955-01-01'), ('F', '1985-01-01')):
            self.assertEqual(self.scalar(f"SELECT age AS a FROM living_patient WHERE patient_id = '{patient_id}'"), age_at(born, AS_OF))
        self.assertEqual(self.scalar("SELECT age_reported AS a FROM living_patient WHERE patient_id = 'A'"), 99)  # stale feed age stays out of `age`
        self.assertNotEqual(self.scalar("SELECT age AS a FROM living_patient WHERE patient_id = 'A'"), 99)
        sql = AD.age_sql('b', '2030-05-27')
        with sqlite3.connect(':memory:') as db:
            for b in ('2000-05-27', '2000-05-28', '2000-02-29', '2030-05-27', '2030-05-28', '1999-12-31', None, 'not a date'):
                self.assertEqual(db.execute(f'SELECT {sql} FROM (SELECT ? AS b)', (b,)).fetchone()[0], age_at(b, '2030-05-27') if b else None, b)

    def test_condition_member_is_the_canonical_evidence_and_matches_the_decision_context(self):
        # diabetes: A (EHR), B and F (counted payer claims; claims-only members count), C (laboratory evidence);
        # D deceased, E encounter diagnosis only (not evidence), G duplicate chart
        got = {r['patient_id'] for r in self.q("SELECT patient_id FROM condition_member WHERE concept = 'diabetes'")}
        self.assertEqual(got, {'A', 'B', 'C', 'F'})
        by = {r['patient_id']: r['evidence'] for r in self.q("SELECT patient_id, evidence FROM condition_member WHERE concept = 'diabetes'")}
        self.assertIn('laboratory_evidence', by['C']); self.assertIn('payer_claim_diagnosis', by['B'])
        self.assertEqual(self.scalar("SELECT first_date AS d FROM condition_member WHERE concept = 'diabetes' AND patient_id = 'A'"), '2028-01-01')
        self.assertEqual({r['patient_id'] for r in self.q("SELECT patient_id FROM condition_member WHERE concept = 'copd'")}, {'A'})
        self.assertEqual(self.q("SELECT 1 AS x FROM condition_member WHERE concept = 'hyperlipidemia'"), [])  # lab-inferred rows of other conditions are out

    def test_latest_lab_is_one_living_row_per_patient_and_concept(self):
        rows = self.q("SELECT patient_id, value FROM latest_lab WHERE concept = 'potassium' ORDER BY patient_id")
        self.assertEqual([(r['patient_id'], r['value']) for r in rows], [('A', 4.0), ('B', 5.9), ('C', 3.9)])  # not D (deceased) or G (duplicate)
        self.assertEqual(self.scalar("SELECT COUNT(DISTINCT patient_id) AS n FROM latest_lab WHERE concept = 'potassium' AND value > 5.5"),
                         self.scoped('How many patients have a potassium above 5.5?',
                                     "SELECT COUNT(DISTINCT patient_id) FROM measurement WHERE concept = 'potassium' AND value_canonical > 5.5"))

    def test_window_and_medicare_views(self):
        row = self.q('SELECT start_exclusive, end_inclusive FROM last_12_months')[0]
        self.assertEqual((row['start_exclusive'], row['end_inclusive']), ('2029-05-27', AS_OF))
        # medicare + dual + medigap among the living: A B E F (G a duplicate, D deceased, C commercial)
        self.assertEqual({r['patient_id'] for r in self.q('SELECT patient_id FROM medicare_member')}, {'A', 'B', 'E', 'F'})

    def test_views_follow_the_question_as_of_and_degrade_on_a_thin_database(self):
        self.wh.define('2040-05-27')
        one = lambda sql: next(iter(self.wh.query(sql, as_of='2040-05-27')['rows'][0].values()))
        self.assertEqual(one("SELECT age AS a FROM living_patient WHERE patient_id = 'A'"), 90)
        self.assertEqual(one('SELECT end_inclusive AS d FROM last_12_months'), '2040-05-27')
        with self.assertRaises(ValueError): self.wh.define("2030'; DROP TABLE patient; --")
        thin = sqlite3.connect(':memory:')
        thin.executescript('CREATE TABLE patient(patient_id TEXT, coverage_type TEXT);')
        views = AD.build_views({'patient': ['patient_id', 'coverage_type']}, AS_OF)
        self.assertEqual(set(views), {'living_patient', 'last_12_months', 'medicare_member'})
        thin.close()

    def test_the_documentation_leads_with_the_views(self):
        docs, ids = AD.agere_context(self.wh, ('BASE DOCS', frozenset({'ex1'})))
        self.assertTrue(docs.startswith('AGERE DEFINITION VIEWS: ALWAYS BUILD ON THESE'))
        self.assertTrue(docs.endswith('BASE DOCS'))
        for name in AD.VIEW_NAMES: self.assertIn(name, docs)
        self.assertEqual(ids, frozenset({'ex1'}))


class Analyze(Base):
    def run_analyst(self, fake, question='How many patients have diabetes?', **kw):
        return SA.analyze(question, AS_OF, self.wh, send=fake, model='haiku-fake', **kw)

    def test_agere_system_prompt_documents_the_views_first_and_the_competitor_prompt_is_unchanged(self):
        answer = {'status': 'answered', 'answer_type': 'count', 'count': 3,
                  'sql': "SELECT COUNT(DISTINCT patient_id) FROM condition_member WHERE concept = 'diabetes'"}
        fake = Fake(reply(tool(answer['sql'])), reply(text(answer)))
        result = self.run_analyst(fake)
        system = fake.bodies[0]['system']
        self.assertTrue(system[1]['text'].startswith('AGERE DEFINITION VIEWS'))
        self.assertTrue(system[0]['text'].endswith(AD.CONTRACT_SUFFIX))
        self.assertEqual(result['answer']['count'], 3)
        self.assertEqual(result['definitions_audit']['action'], 'ok')
        # competitor baseline: definitions=False sends the plain documented layer byte for byte
        plain = Fake(reply(text({'status': 'unsupported', 'answer_type': 'unsupported'})))
        SA.analyze('q', AS_OF, self.wh, send=plain, model='m', definitions=False)
        self.assertEqual(plain.bodies[0]['system'], [{'type': 'text', 'text': SA.SEMANTIC_RULES + SA.CONTRACT},
                                                     {'type': 'text', 'text': 'BASE DOCS', 'cache_control': {'type': 'ephemeral'}}])
        self.assertNotIn('definitions_audit', SA.analyze('q', AS_OF, self.wh, send=Fake(reply(text({'status': 'answered', 'answer_type': 'count', 'count': 1}))),
                                                         model='m', definitions=False))

    def test_the_bench_competitor_passes_definitions_false(self):
        from bench import adapters
        seen = {}
        original = SA.analyze

        def spy(*a, **k):
            seen.update(k); return {'answer': None, 'example_ids': frozenset()}
        with patch.object(SA, 'analyze', spy), patch.object(adapters, '_sender', return_value=lambda b: None):
            adapters.frontier_semantic({'id': 'x', 'question': 'q', 'as_of': AS_OF}, self.wh, key='k')
        self.assertIs(seen.get('definitions'), False)
        self.assertIs(original, SA.analyze)

    def test_competitor_baseline_documentation_is_unchanged_by_this_module(self):
        self.assertNotIn('DEFINITION VIEWS', SA.SEMANTIC_RULES + SA.CONTRACT)
        self.assertNotIn('living_patient', SA.SEMANTIC_RULES + SA.CONTRACT)


class Arithmetic(Base):
    N = "SELECT COUNT(*) FROM condition_member WHERE concept = 'diabetes'"
    D = 'SELECT COUNT(*) FROM living_patient'

    def finish(self, **ratio):
        base = {'numerator': 4, 'denominator': 5, 'numerator_sql': self.N, 'denominator_sql': self.D, 'scale': 'percent'}
        answer = {'status': 'answered', 'answer_type': 'value', 'value': 0.6, 'ratio': {**base, **ratio}}
        fake = Fake(reply(tool(self.N)), reply(text(answer)))
        return SA.analyze('What share of patients have diabetes?', AS_OF, self.wh, send=fake, model='m')

    def test_a_wrong_stated_share_is_replaced_by_the_computed_one_and_noted(self):
        result = self.finish()
        a = result['answer']
        self.assertEqual(a['value'], 80.0)
        self.assertEqual(a['headline'], '80% (4 of 5)')
        self.assertTrue(result['definitions_audit']['arithmetic']['replaced'])
        self.assertIn('stated value 0.6 replaced', result['definitions_audit']['arithmetic']['notes'][0])

    def test_a_stated_share_that_agrees_within_its_rounding_is_kept(self):
        answer = {'status': 'answered', 'answer_type': 'value', 'value': 80.0,
                  'ratio': {'numerator': 4, 'denominator': 5, 'numerator_sql': self.N, 'denominator_sql': self.D, 'scale': 'percent'}}
        result = SA.analyze('share?', AS_OF, self.wh, send=Fake(reply(text(answer))), model='m')
        self.assertFalse(result['definitions_audit']['arithmetic']['replaced'])
        third = {'status': 'answered', 'answer_type': 'value', 'value': 33.3,
                 'ratio': {'numerator': 1, 'denominator': 3, 'scale': 'percent', 'numerator_sql': "SELECT COUNT(*) FROM condition_member WHERE concept = 'copd'",
                           'denominator_sql': "SELECT COUNT(*) FROM living_patient WHERE patient_id IN ('A','B','C')"}}
        result = SA.analyze('share?', AS_OF, self.wh, send=Fake(reply(text(third))), model='m')
        self.assertEqual(result['answer']['value'], 33.3)
        self.assertEqual(result['answer']['headline'], '33.3% (1 of 3)')

    def test_stated_numerator_and_denominator_are_replaced_by_their_executed_queries(self):
        result = self.finish(numerator=9, denominator=6)
        self.assertEqual((result['answer']['ratio']['numerator'], result['answer']['ratio']['denominator']), (4, 5))
        self.assertEqual(result['answer']['value'], 80.0)
        self.assertTrue(any('numerator stated 9' in n for n in result['definitions_audit']['arithmetic']['notes']))

    def test_average_scales_and_a_zero_denominator_is_declined(self):
        result = self.finish(scale='mean', numerator_sql='SELECT 10', denominator_sql='SELECT 4')
        self.assertEqual(result['answer']['value'], 2.5)
        self.assertEqual(result['answer']['headline'], '2.5 (10 of 4)')
        result = self.finish(denominator_sql='SELECT 0')
        self.assertEqual(result['answer']['status'], 'unsupported')
        self.assertIn('denominator is 0', result['definitions_audit']['reason'])

    def test_compute_ratio_scales(self):
        for scale, expect in (('fraction', 0.25), ('percent', 25.0), ('per_1000', 250.0), ('per_100000', 25000.0)):
            self.assertEqual(AD.compute_ratio({'numerator': 1, 'denominator': 4, 'scale': scale})[0], expect)
        with self.assertRaises(AD.Decline): AD.compute_ratio({'numerator': 1, 'denominator': 4, 'scale': 'weird'})
        with self.assertRaises(AD.Decline): AD.compute_ratio({'numerator': None, 'denominator': 4})


class Audit(Base):
    COLUMNS = None

    def setUp(self):
        super().setUp()
        self.cols = self.wh.columns

    def audit(self, question, sql, returns=None):
        return AD.audit_sql(question, AS_OF, sql, self.cols, returns)

    def rules(self, question, sql, returns=None):
        return {f['rule'] for f in self.audit(question, sql, returns)}

    def test_deceased_and_duplicate_charts_are_caught_and_mechanically_rewritten(self):
        q = 'How many patients have COPD?'
        sql = "SELECT COUNT(DISTINCT patient_id) AS n FROM condition WHERE concept = 'copd' AND is_diagnosis = 1"
        self.assertIn('living', self.rules(q, 'SELECT COUNT(*) FROM patient'))
        self.assertIn('living', self.rules(q, 'SELECT COUNT(DISTINCT patient_id) FROM patient'))
        self.assertEqual(self.rules(q, 'SELECT COUNT(*) FROM living_patient'), set())
        self.assertNotIn('living', self.rules('How many deceased patients had COPD?', 'SELECT COUNT(*) FROM patient'))  # the question asks
        self.assertNotIn('living', self.rules('What was total paid in 2029?', 'SELECT SUM(paid) FROM payer_claim'))
        findings = self.audit(q, 'SELECT COUNT(DISTINCT patient_id) AS n FROM patient')
        out, notes = AD.rewrite_sql(q, AS_OF, 'SELECT COUNT(DISTINCT patient_id) AS n FROM patient', self.cols, findings)
        self.assertIn('living patients', notes)
        self.assertEqual(self.scalar(out), 5)  # not 7
        self.assertEqual(self.audit(q, out), [])

    def test_a_death_filter_without_the_duplicate_exclusion_is_caught_and_fixed(self):
        q = 'How many patients are there?'
        sql = 'SELECT COUNT(*) AS n FROM patient WHERE patient_id NOT IN (SELECT patient_id FROM death)'
        findings = self.audit(q, sql)
        self.assertEqual([f['rule'] for f in findings], ['living'])
        self.assertTrue(findings[0]['mechanical'])
        self.assertEqual(self.scalar(sql), 6)
        out, _ = AD.rewrite_sql(q, AS_OF, sql, self.cols, findings)
        self.assertEqual(self.scalar(out), 5)
        self.assertEqual(self.audit(q, out), [])
        self.assertEqual(self.rules(q, sql + " AND data_segment <> 'duplicate_chart'"), set())

    def test_any_ever_lab_thresholds_use_the_latest_result_unless_the_question_says_ever(self):
        sql = "SELECT COUNT(DISTINCT patient_id) AS n FROM measurement WHERE concept = 'potassium' AND value_canonical > 5.5"
        q = 'How many patients have a potassium above 5.5?'
        self.assertEqual(self.rules(q, sql), {'latest_lab', 'living'})
        self.assertEqual(self.rules('How many patients ever had a potassium above 5.5?', sql), {'living'})
        self.assertEqual(self.rules(q, "SELECT COUNT(DISTINCT patient_id) AS n FROM latest_lab WHERE concept = 'potassium' AND value > 5.5"), set())
        out, notes = AD.rewrite_sql(q, AS_OF, sql, self.cols, self.audit(q, sql))
        self.assertEqual(self.scalar(out), 1)  # B only; A's old 6.1 and deceased D / duplicate G are out
        self.assertEqual(self.audit(q, out), [])

    def test_conditions_must_come_from_the_canonical_evidence(self):
        q = 'How many patients have diabetes?'
        self.assertIn('condition_evidence', self.rules(q, "SELECT COUNT(DISTINCT patient_id) FROM condition WHERE concept = 'diabetes'"))
        self.assertIn('condition_evidence', self.rules(q, "SELECT COUNT(DISTINCT patient_id) FROM living_patient JOIN payer_claim_diagnosis USING (patient_id) "
                                                           "WHERE icd10 LIKE 'E11%'"))
        self.assertEqual(self.rules(q, "SELECT COUNT(DISTINCT patient_id) FROM condition_member WHERE concept = 'diabetes'"), set())
        self.assertNotIn('condition_evidence', self.rules(q, "SELECT COUNT(DISTINCT patient_id) FROM diagnosis_any WHERE icd10 LIKE 'E11%'"))
        self.assertFalse(all(f['mechanical'] for f in self.audit(q, "SELECT COUNT(DISTINCT patient_id) FROM condition WHERE concept = 'diabetes'")))

    def test_stale_feed_age_is_rewritten_to_the_birth_date(self):
        q = 'How many patients are 65 or older?'
        sql = 'SELECT COUNT(*) AS n FROM living_patient WHERE age_reported >= 65'
        findings = self.audit(q, sql)
        self.assertEqual([f['rule'] for f in findings], ['age'])
        out, notes = AD.rewrite_sql(q, AS_OF, sql, self.cols, findings)
        self.assertEqual(self.scalar(sql), 5)  # everyone is 99 in the feed
        self.assertEqual(self.scalar(out), 3)  # A 80, B 75, E 70 (C 40 and F 45 are under 65; D deceased, G a duplicate)
        self.assertEqual(self.audit(q, out), [])
        aliased = 'SELECT COUNT(*) AS n FROM patient p WHERE p.age_latest > 65'
        out, _ = AD.rewrite_sql(q, AS_OF, aliased, self.cols, self.audit(q, aliased))
        self.assertIn('p.birth_date', out)

    def test_events_and_medicare(self):
        q = 'How many patients had an inpatient stay?'
        self.assertIn('event_source', self.rules(q, "SELECT COUNT(DISTINCT patient_id) FROM visit WHERE type = 'inpatient'"))
        self.assertNotIn('event_source', self.rules(q, "SELECT COUNT(DISTINCT patient_id) FROM utilization_event WHERE kind = 'inpatient'"))
        q = 'How many Medicare patients are there?'
        sql = "SELECT COUNT(*) AS n FROM living_patient WHERE coverage_type = 'medicare'"
        self.assertEqual(self.rules(q, sql), {'medicare'})
        self.assertEqual(self.scalar(sql), 2)  # A, B
        out, _ = AD.rewrite_sql(q, AS_OF, sql, self.cols, self.audit(q, sql))
        self.assertEqual(self.scalar(out), 4)  # + dual E, medigap F
        self.assertEqual(self.rules('How many traditional Medicare patients are there?', sql), set())
        self.assertEqual(self.rules(q, 'SELECT COUNT(*) AS n FROM medicare_member'), set())


class Finalize(Base):
    def go(self, answer, question, *sql):
        calls = [reply(tool(s, i + 1)) for i, s in enumerate(sql)]
        return SA.analyze(question, AS_OF, self.wh, send=Fake(*calls, reply(text(answer))), model='m')

    def test_a_mechanical_violation_is_rewritten_and_the_answer_replaced(self):
        sql = "SELECT COUNT(DISTINCT patient_id) AS n FROM measurement WHERE concept = 'potassium' AND value_canonical > 5.5"
        wrong = self.scalar_free(sql)
        result = self.go({'status': 'answered', 'answer_type': 'count', 'count': wrong, 'sql': sql},
                         'How many patients have a potassium above 5.5?', sql)
        self.assertEqual(result['answer']['count'], 1)
        audit = result['definitions_audit']
        self.assertEqual(audit['action'], 'rewritten')
        self.assertEqual(sorted(audit['rewrites'][0]['rules']), ['latest_lab', 'living'])
        self.assertEqual(result['tool_calls'][-1]['audit'], 'answer')

    def scalar_free(self, sql):
        self.wh.define(AS_OF)
        return next(iter(self.wh.query(sql, as_of=AS_OF)['rows'][0].values()))

    def test_a_patient_list_is_recomputed_under_the_definitions(self):
        sql = "SELECT DISTINCT patient_id FROM patient WHERE coverage_type = 'commercial' OR coverage_type = 'medicare'"
        self.wh.define(AS_OF)
        ids = sorted(r['patient_id'] for r in self.wh.query(sql, as_of=AS_OF)['rows'])
        result = self.go({'status': 'answered', 'answer_type': 'patient_set', 'patient_ids': ids, 'count': len(ids), 'sql': sql},
                         'Which patients have Medicare or commercial coverage?', sql)
        self.assertEqual(ids, ['A', 'B', 'C', 'D', 'G'])
        # D (deceased) and G (duplicate chart) are out; the question says Medicare, so dual E and Medigap F are in too
        self.assertEqual(result['answer']['patient_ids'], ['A', 'B', 'C', 'E', 'F'])
        self.assertNotIn('D', result['answer']['patient_ids'])
        self.assertNotIn('G', result['answer']['patient_ids'])
        self.assertEqual(result['answer']['count'], len(result['answer']['patient_ids']))

    def test_a_non_mechanical_violation_is_declined_with_the_reason(self):
        sql = "SELECT COUNT(DISTINCT patient_id) AS n FROM living_patient JOIN condition USING (patient_id) WHERE concept = 'diabetes'"
        result = self.go({'status': 'answered', 'answer_type': 'count', 'count': 3, 'sql': sql}, 'How many patients have diabetes?', sql)
        self.assertEqual(result['answer']['status'], 'unsupported')
        self.assertIsNone(result['answer']['count'])
        self.assertIn('condition_member', result['answer']['missing'][0])
        self.assertEqual(result['definitions_audit']['action'], 'declined')
        self.assertEqual(result['declined_answer']['count'], 3)

    def test_a_stated_answer_that_does_not_come_from_its_sql_is_declined_when_a_rewrite_is_needed(self):
        sql = 'SELECT COUNT(*) AS n FROM patient'
        result = self.go({'status': 'answered', 'answer_type': 'count', 'count': 42, 'sql': sql}, 'How many patients are there?', sql)
        self.assertEqual(result['answer']['status'], 'unsupported')
        self.assertIn('does not come from the declared sql', result['answer']['missing'][0])

    def test_questions_that_ask_for_the_deceased_or_any_lab_are_not_rewritten(self):
        sql = 'SELECT COUNT(*) AS n FROM patient'
        result = self.go({'status': 'answered', 'answer_type': 'count', 'count': 7, 'sql': sql}, 'How many patients died or are alive, all patients ever?', sql)
        self.assertEqual(result['answer']['count'], 7)
        self.assertEqual(result['definitions_audit']['action'], 'ok')

    def test_unsupported_answers_and_errors_pass_through_untouched(self):
        answer = {'status': 'unsupported', 'answer_type': 'unsupported', 'missing': ['no data']}
        result = self.go(answer, 'q')
        self.assertEqual(result['answer'], answer)
        self.assertNotIn('definitions_audit', result)

    def test_the_audit_reads_only_and_costs_nothing(self):
        sql = 'SELECT COUNT(*) AS n FROM patient'
        result = self.go({'status': 'answered', 'answer_type': 'count', 'count': 7, 'sql': sql}, 'How many patients are there?', sql)
        self.assertEqual(result['new_paid_api_calls'], 2)  # the fake transport's two scripted calls; the audit made none
        self.assertEqual(result['answer']['count'], 5)


class Reconcile(Base):
    def test_agere_reconcile_uses_the_views_and_the_bench_reconcile_stays_plain(self):
        row = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 1}}
        verdict = text({'choice': 'abstain', 'reason': 'x', 'answer': None})
        plain, agere = Fake(reply(verdict)), Fake(reply(verdict))
        SA.reconcile('q', AS_OF, row, row, self.wh, send=plain, model='m', seed_key='s', definitions=False)
        SA.reconcile('q', AS_OF, row, row, self.wh, send=agere, model='m', seed_key='s')
        self.assertEqual(plain.bodies[0]['system'][1]['text'], 'BASE DOCS')
        self.assertTrue(agere.bodies[0]['system'][1]['text'].startswith('AGERE DEFINITION VIEWS'))

    def test_views_are_per_thread(self):
        import threading
        seen = {}

        def work():
            self.wh.define(AS_OF)
            seen['n'] = self.wh.query('SELECT COUNT(*) AS n FROM living_patient', as_of=AS_OF)['rows'][0]['n']
        t = threading.Thread(target=work); t.start(); t.join()
        self.assertEqual(seen['n'], 5)


class Calibration(Base):
    """tools/dev_set/analyst_calib.py is prepared, not run: here it runs on synthetic questions through a fake transport."""
    def test_dev_only_loading_and_a_scored_fake_run(self):
        from tools.dev_set import analyst_calib as C
        rows = [{'id': 'd1', 'split': 'dev', 'bucket': 'population_clinical_quality', 'question': 'What share of patients have diabetes?',
                 'answer_type': 'value', 'key': {'answer': 80.0, 'tolerance': {'abs': 0.5}}},
                {'id': 'h1', 'split': 'heldout', 'bucket': 'population_clinical_quality', 'question': 'never loaded', 'answer_type': 'count', 'key': {}},
                {'id': 'd2', 'split': 'dev', 'bucket': 'single_patient_structured', 'question': 'other bucket', 'answer_type': 'count', 'key': {}}]
        path = self.path / 'q.jsonl'
        path.write_text('\n'.join(json.dumps(r) for r in rows))
        self.assertEqual([q['id'] for q in C.load_dev(path)], ['d1'])
        n = "SELECT COUNT(*) FROM condition_member WHERE concept = 'diabetes'"
        d = 'SELECT COUNT(*) FROM living_patient'
        answer = {'status': 'answered', 'answer_type': 'value', 'value': 0.8,
                  'ratio': {'numerator': 4, 'denominator': 5, 'numerator_sql': n, 'denominator_sql': d, 'scale': 'percent'}}
        row = C.run_one(C.load_dev(path)[0], self.wh, Fake(reply(text(answer))), 'haiku-fake')
        self.assertEqual(row['outcome'], 'correct')
        self.assertTrue(row['arithmetic_replaced'])
        self.assertIn('correct=1', C.summarize([row])[0])
        self.assertEqual(C.classify(None, {'status': 'unsupported'}), 'declined')


if __name__ == '__main__':
    unittest.main()
