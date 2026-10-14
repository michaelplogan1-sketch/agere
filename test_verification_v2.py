"""Verification v2: independent candidates (FM-13), capture after checks (FM-23), oracles (FM-08/09/01),
calibrated thresholds (Learn-then-Test), definitional readings (FM-19). Fakes only: no model, no paid call."""
import json, os, re, sqlite3, tempfile, unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.clinical import (calibrated_thresholds as CT, conformal, learning, nl2sql, oracles,
                                          verification_v2 as v2)

ROOT = Path(__file__).resolve().parent
AS_OF = '2029-08-15'


def cand(label, result, query=None, family=None, order=0):
    return {'label': label, 'family': family or v2.family(label), 'params': {'query': query or f'SELECT {label}', 'returns': 'table'},
            'result': result, 'warnings': [], 'order': order}


class Agreement(unittest.TestCase):
    def test_same_answer_across_shapes(self):
        self.assertTrue(v2.same_answer({'value': 3}, {'patient_ids': ['a', 'b', 'c']}))
        self.assertTrue(v2.same_answer({'value': 2.00001}, {'rows': [{'n': 2.000012}], 'columns': ['n']}))
        self.assertTrue(v2.same_answer({'rows': [{'a': 1, 'b': 'x'}]}, {'rows': [{'q': 'x', 'r': 1}]}))  # alias/order-insensitive
        self.assertFalse(v2.same_answer({'value': 3}, {'patient_ids': ['a', 'b']}))
        self.assertFalse(v2.same_answer({'patient_ids': ['a']}, {'patient_ids': ['b']}))

    def test_independent_agreement_is_accepted(self):
        d = v2.decide([cand('primary', {'value': 5}), cand('compiler', {'value': 5}, order=10)])
        self.assertEqual((d['status'], d['basis']), ('accepted', 'independent'))
        self.assertEqual(d['agreement'], 'primary=compiler')

    def test_independent_dissent_disputes_hosted_agreement(self):
        pair = [cand('primary', {'value': 5}), cand('second', {'value': 5}, order=1)]
        d = v2.decide(pair + [cand('local_model', {'value': 7}, order=10)], oracle=lambda c: [])
        self.assertEqual(d['status'], 'disputed'); self.assertIn('independent source disagrees: local_model', d['basis'])
        # The relaxed compiler answers a broader question: its dissent is recorded (blocks capture) but does not route by default.
        weak = v2.decide(pair + [cand('compiler', {'value': 7}, order=10)], oracle=lambda c: [])
        self.assertEqual((weak['status'], weak['dissent']), ('accepted', ['compiler']))
        with mock.patch.dict(os.environ, {'AGERE_COMPILER_DISSENT': 'route'}):
            self.assertEqual(v2.decide(pair + [cand('compiler', {'value': 7}, order=10)])['status'], 'disputed')

    def test_same_family_needs_clean_oracles(self):
        pair = [cand('primary', {'value': 5}), cand('second', {'value': 5}, order=1)]
        self.assertEqual(v2.decide(pair, oracle=lambda c: [])['basis'], 'same_family_with_oracles')
        fired = v2.decide(pair, oracle=lambda c: [{'control': 'QC-PERTURB', 'severity': 'warning', 'message': 'x'}])
        self.assertEqual(fired['status'], 'disputed'); self.assertIn('QC-PERTURB', fired['basis'])
        info_only = v2.decide(pair, oracle=lambda c: [{'control': 'QC-PARTITION', 'severity': 'info', 'message': 'undated'},
                                                       {'control': 'QC-PERIOD', 'severity': 'warning', 'message': 'partial'}])
        self.assertEqual(info_only['status'], 'accepted')  # disclosures do not block

    def test_lone_or_split_candidates_are_disputed(self):
        self.assertEqual(v2.decide([cand('primary', {'value': 1})])['status'], 'disputed')
        self.assertEqual(v2.decide([cand('primary', {'value': 1}), cand('second', {'value': 2}, order=1)])['basis'], 'no agreement')
        self.assertEqual(v2.decide([])['status'], 'none')


class Ladder(unittest.TestCase):
    """nl2sql._independent_ladder with fake models and a fake check()."""

    def run_ladder(self, answers, independent, oracle=None, decider=None):
        calls, receipt = [], {'attempts': [], 'agreement': None}
        results = {'P': {'value': 10}, 'S': {'value': 10}, 'J': {'value': 12}, 'X': {'value': 99}, 'C': {'value': 10}}

        def check(sql):
            key = re.search(r'-- (\w)', sql).group(1) if '-- ' in sql else sql[-1]
            return {'query': sql, 'returns': 'count'}, results[key], []

        def attempt(model, label, extra=''):
            calls.append((label, extra))
            sql = answers[label]
            params, result, warn = check(sql)
            receipt['attempts'].append({'role': label, 'sql': sql, 'model': label})
            return params, result, warn
        out = nl2sql._independent_ladder('How many patients over 65?', receipt, attempt, check, 'p', 's', 'j', decider, independent, oracle)
        return out, receipt, calls

    def test_free_independent_match_skips_second_call(self):
        (params, result, receipt), _, calls = self.run_ladder({'primary': 'SELECT 1 -- P', 'second': 'SELECT 2 -- S'},
                                                              [lambda q: {'label': 'compiler', 'sql': 'SELECT 3 -- C'}])
        self.assertEqual([c[0] for c in calls], ['primary'])
        self.assertTrue(receipt['verification']['second_skipped'])
        self.assertEqual(receipt['agreement'], 'primary=compiler'); self.assertEqual(result, {'value': 10})

    def test_hosted_agreement_with_oracle_finding_goes_to_judge(self):
        oracle = lambda c: [{'control': 'QC-PERTURB', 'fmea': 'FM-01', 'severity': 'warning', 'message': 'over 65 written as >= 65'}]
        (params, result, receipt), _, calls = self.run_ladder({'primary': 'SELECT 1 -- P', 'second': 'SELECT 2 -- S', 'judge': 'SELECT 9 -- J'}, [], oracle)
        self.assertEqual([c[0] for c in calls], ['second', 'primary', 'judge'])
        self.assertIn('over 65 written as >= 65', calls[-1][1])  # the judge sees the deterministic finding
        self.assertEqual(receipt['agreement'], 'judge_own'); self.assertEqual(result, {'value': 12})
        self.assertTrue(receipt['verification']['judge_findings'])  # the judge's query is re-checked too

    def test_hosted_agreement_without_findings_needs_no_judge(self):
        (_, _, receipt), _, calls = self.run_ladder({'primary': 'SELECT 1 -- P', 'second': 'SELECT 2 -- S'}, [], lambda c: [])
        self.assertEqual(sorted(c[0] for c in calls), ['primary', 'second'])
        self.assertEqual(receipt['agreement'], 'primary=second'); self.assertEqual(receipt['verification']['basis'], 'same_family_with_oracles')

    def test_compiler_dissent_disputes_and_failing_source_is_ignored(self):
        def broken(q): raise RuntimeError('no profile')
        answers = {'primary': 'SELECT 1 -- P', 'second': 'SELECT 2 -- S', 'judge': 'SELECT 9 -- J'}
        (_, result, receipt), _, calls = self.run_ladder(answers, [broken, lambda q: {'label': 'local_model', 'sql': 'SELECT 4 -- X'}], lambda c: [])
        self.assertEqual([c[0] for c in calls], ['primary', 'second', 'judge'])
        self.assertIn('independent source disagrees', receipt['verification']['basis'])
        self.assertIn('error', receipt['verification']['independent'][0])
        self.assertEqual(receipt['verification']['dissent'], ['local_model'])  # still disagrees with the judge's answer
        (_, result, receipt), _, calls = self.run_ladder(answers, [lambda q: {'label': 'compiler', 'sql': 'SELECT 4 -- X'}], lambda c: [])
        self.assertEqual([c[0] for c in calls], ['primary', 'second'])  # weak compiler dissent: no judge call
        self.assertEqual(receipt['verification']['dissent'], ['compiler'])

    def test_decider_only_between_disagreeing_hosted_analysts(self):
        picks = []
        def decider(q, a, b):
            picks.append((a[1], b[1])); return {'choice': 'B', 'probability': 0.97, 'accepted': True}
        (_, result, receipt), _, calls = self.run_ladder({'primary': 'SELECT 1 -- P', 'second': 'SELECT 2 -- X'}, [], lambda c: [], decider)
        self.assertEqual(picks, [({'value': 10}, {'value': 99})]); self.assertEqual(receipt['agreement'], 'decider=B')
        self.assertEqual(result, {'value': 99})

    def test_two_model_ladder_unchanged_when_v2_is_off(self):
        self.assertIn('independent=None', Path(nl2sql.__file__).read_text())


class Oracles(unittest.TestCase):
    def test_perturbation_comparators(self):
        f = oracles.perturbation('How many patients had a systolic over 180?', "SELECT count(*) FROM m WHERE value_canonical >= 180", AS_OF)
        self.assertEqual([x['control'] for x in f], ['QC-PERTURB']); self.assertIn('>= 180', f[0]['message'])
        f = oracles.perturbation('How many patients had a systolic over 180?', "SELECT count(*) FROM m WHERE value_canonical < 180", AS_OF)
        self.assertIn('other way', f[0]['message'])
        self.assertEqual(oracles.perturbation('How many patients had a systolic over 180?', 'SELECT count(*) FROM m WHERE 180 < v', AS_OF), [])
        self.assertEqual(oracles.perturbation('Patients aged 65 or older?', 'SELECT count(*) FROM p WHERE age_latest >= 65', AS_OF), [])
        self.assertEqual(oracles.perturbation('Members aged 18 to 85', 'SELECT count(*) FROM p WHERE age_latest BETWEEN 18 AND 85', AS_OF), [])
        self.assertTrue(oracles.perturbation('A1c at least 9', 'SELECT 1 FROM m WHERE v > 9', AS_OF))

    def test_perturbation_windows_and_years(self):
        ok = "SELECT count(*) FROM visit WHERE event_date > '2028-08-15'"
        self.assertEqual(oracles.perturbation('ED visits in the past 12 months?', ok, AS_OF), [])
        self.assertEqual(oracles.perturbation('ED visits in the past 12 months?', "SELECT 1 WHERE event_date >= date('2029-08-15','-12 months')", AS_OF), [])
        bad = oracles.perturbation('ED visits in the past 12 months?', "SELECT count(*) FROM visit WHERE event_date >= '2029-01-01'", AS_OF)
        self.assertEqual(bad[0]['fmea'], 'FM-12'); self.assertEqual(bad[0]['expected_start'], '2028-08-15')
        year = oracles.perturbation('Visits in 2028?', "SELECT count(*) FROM visit WHERE event_date BETWEEN '2027-01-01' AND '2028-12-31'", AS_OF)
        self.assertEqual(year[0]['sql_years'], [2027])
        self.assertEqual(oracles.perturbation('Visits in 2028?', "SELECT count(*) FROM visit WHERE substr(event_date,1,4) = '2028'", AS_OF), [])

    def fixture(self):
        db = sqlite3.connect(':memory:')
        db.executescript("""CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type_concept TEXT, event_date TEXT);
        INSERT INTO visit VALUES ('e1','p1','ed','2028-02-01'),('e1','p1','ed','2028-02-01'),('e2','p2','ed','2028-03-01'),
                                 ('e3','p3','ed',NULL),('e4','p4','office','2028-04-01');""")
        return lambda q: db.execute(q).fetchone()[0]

    def test_partition_identity_versions_and_undated(self):
        ex = self.fixture()
        f = oracles.partition('How many ED visits in 2028?', "SELECT count(*) FROM visit WHERE type_concept = 'ed' AND substr(event_date,1,4) = '2028'", ex)
        by = {x['fmea']: x for x in f}
        self.assertEqual(by['FM-08']['rows'], 3); self.assertEqual(by['FM-08']['distinct_keys'], 2)
        self.assertEqual(by['FM-09']['undated_rows_dropped'], 1); self.assertEqual(by['FM-09']['severity'], 'info')
        p = by['FM-08']['partition']
        self.assertEqual(p['true'] + p['false'] + p['null'], p['total'])
        self.assertNotIn('FM-13', by)  # the identity holds for a deterministic predicate
        distinct = oracles.partition('How many ED visits in 2028?', "SELECT count(DISTINCT encounter_id) FROM visit WHERE type_concept = 'ed'", ex)
        self.assertEqual(distinct, [])
        self.assertEqual(oracles.partition('How many ED visit records?', "SELECT count(*) FROM visit WHERE type_concept = 'ed'", ex), [])  # records asked
        self.assertEqual(oracles.partition('q', 'SELECT patient_id FROM visit WHERE 1', ex), [])  # not a count/sum
        self.assertEqual(oracles.partition('q', 'SELECT count(*) FROM nowhere WHERE x = 1', ex), [])  # failure never raises

    def test_split_and_conjuncts(self):
        parts = oracles.split_simple("-- DEFINITION: x\nSELECT count(*) FROM a JOIN b ON a.i = b.i WHERE a.d BETWEEN 1 AND 2 AND (b.x = 'and') GROUP BY a.k")
        self.assertEqual(parts['from'], 'a JOIN b ON a.i = b.i'); self.assertEqual(parts['group'], 'a.k')
        self.assertEqual(oracles.conjuncts(parts['where']), ['a.d BETWEEN 1 AND 2', "(b.x = 'and')"])
        self.assertIsNone(oracles.split_simple('WITH t AS (SELECT 1) SELECT count(*) FROM t WHERE 1'))

    def test_answer_checks_runs_oracles(self):
        from clinical_core.agere.clinical import answer_checks
        a = {'status': 'answered', 'answer_type': 'count', 'value': 3,
             'query': "SELECT count(*) FROM visit WHERE type_concept = 'ed' AND substr(event_date,1,4) = '2028'"}
        answer_checks.annotate(a, 'How many ED visits in 2028?', AS_OF, execute=self.fixture())
        self.assertIn('QC-PARTITION', {c['control'] for c in a['quality_checks']})
        self.assertTrue(any('distinct encounter_id' in c['message'] and c['severity'] == 'info' for c in a['quality_checks']))
        self.assertFalse(a.get('warnings'))  # FM-08 partition counts are a disclosure (replay precision), not a warning
        b = {'status': 'answered', 'answer_type': 'count', 'value': 3, 'query': 'SELECT count(*) FROM m WHERE value_canonical >= 180'}
        answer_checks.annotate(b, 'How many had a systolic over 180?', AS_OF)  # no executor: text oracle only
        self.assertEqual({c['control'] for c in b['quality_checks']}, {'QC-PERTURB'})


class Capture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        (self.path / 'run.json').write_text(json.dumps({'source_digest': 'fixture'}))

    def tearDown(self): self.tmp.cleanup()

    def receipt(self, **extra):
        return {'route': 'hosted_verified_sql', 'hosted_sql': {'agreement': 'primary=second', **extra,
                'capture': {'plan': {'query': 'SELECT 1', 'returns': 'count'}, 'signature': '["v", 1]', 'models': ['h', 's'], 'agreement': 'primary=second'}}}

    def rows(self, table):
        db = sqlite3.connect(self.path / 'learning.sqlite3')
        try: return db.execute(f'SELECT question FROM {table}').fetchall()
        finally: db.close()

    def test_planner_no_longer_captures_before_checks(self):
        src = (ROOT / 'clinical_core/agere/clinical/economical_planner.py').read_text()
        self.assertNotIn('record_verified(', src)
        self.assertIn("'capture'", src)
        conv = (ROOT / 'clinical_core/agere/clinical/conversation.py').read_text()
        self.assertLess(conv.index('answer_log.record(stack.path, logged, result=result'), conv.index('learning.capture_verified('))

    def test_clean_answer_recorded_flagged_answer_quarantined(self):
        clean = {'status': 'answered', 'quality_checks': [{'control': 'QC-DENOM', 'severity': 'info'}]}
        self.assertTrue(learning.capture_verified(self.path, 'How many?', self.receipt(), clean)['recorded'])
        flagged = {'status': 'answered', 'quality_checks': [{'control': 'QC-PERTURB', 'severity': 'warning'}]}
        out = learning.capture_verified(self.path, 'How many over 9?', self.receipt(), flagged)
        self.assertEqual(out, {'recorded': False, 'reasons': ['QC-PERTURB']})
        ambiguous = learning.capture_verified(self.path, 'Visits in 2028?', self.receipt(readings={'readings': [{}, {}]}), clean)
        self.assertIn('two defensible readings', ambiguous['reasons'])
        dissent = learning.capture_verified(self.path, 'Dissent?', self.receipt(verification={'dissent': ['compiler']}), clean)
        self.assertEqual(dissent['reasons'], ['an independent candidate disagrees'])
        self.assertEqual(self.rows('verified'), [('How many?',)])
        self.assertEqual(sorted(self.rows('quarantined')), [('Dissent?',), ('How many over 9?',), ('Visits in 2028?',)])
        self.assertIsNotNone(learning.lookup(self.path, 'How many?'))
        self.assertIsNone(learning.lookup(self.path, 'How many over 9?'))
        self.assertFalse(learning.capture_verified(self.path, 'x', {'route': 'local_compiler'}, clean)['recorded'])

    def test_handle_compose_captures_after_answer_checks(self):
        from clinical_core.agere.clinical import conversation
        receipt = self.receipt()
        class Stack:
            path, allow_live = self.path, False
            def compose(self, q, planning='auto'):
                return {'question': q, 'planner_receipt': receipt, 'planning_economics': {}, 'new_paid_api_calls': 2,
                        'answer': {'status': 'answered', 'answer_type': 'count', 'value': 1,
                                   'quality_checks': [{'control': 'QC-NUMBERS', 'severity': 'warning'}]}}
        conversation.handle_compose(Stack(), {'question': 'How many visits in 2027?'})
        self.assertEqual(self.rows('verified'), [])
        self.assertEqual(self.rows('quarantined'), [('How many visits in 2027?',)])


class Thresholds(unittest.TestCase):
    def test_binomial_cdf(self):
        self.assertAlmostEqual(conformal.binomial_cdf(0, 10, 0.1), 0.9 ** 10, places=12)
        self.assertAlmostEqual(conformal.binomial_cdf(1, 3, 0.5), 0.5, places=12)
        self.assertEqual(conformal.binomial_cdf(5, 5, 0.3), 1.0)

    def test_ltt_certifies_most_permissive_safe_threshold(self):
        scores = [0.99 - i * 0.001 for i in range(300)]
        errors = [i >= 250 for i in range(300)]  # the 50 lowest-scoring cases are wrong
        r = conformal.ltt_threshold(scores, errors, alpha=0.02, delta=0.1)
        self.assertIsNotNone(r['threshold'])
        self.assertGreaterEqual(r['threshold'], scores[250])  # never admits the error band wholesale
        self.assertLessEqual(r['p_value'], 0.1)
        self.assertLessEqual(r['errors_accepted'] / r['n_accepted'], 0.02)

    def test_ltt_refuses_small_or_noisy_data(self):
        r = conformal.ltt_threshold([0.9] * 50, [False] * 50, alpha=0.02, delta=0.1)
        self.assertIsNone(r['threshold']); self.assertEqual(r['min_accepted_for_zero_errors'], 114)
        self.assertIsNone(conformal.ltt_threshold([0.9] * 400, [i % 10 == 0 for i in range(400)], 0.02, 0.1)['threshold'])
        self.assertIsNotNone(conformal.ltt_threshold([0.9] * 114, [False] * 114, 0.02, 0.1)['threshold'])

    def test_records_fallbacks_and_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 't.json'
            ok = CT.fit('g.ok', [0.9] * 200, [False] * 200, 0.95, {'source': 'fixture'}, bind='m1')
            weak = CT.fit('g.weak', [0.9] * 20, [False] * 20, 0.8, {'source': 'fixture'})
            CT.write([ok, weak], target)
            self.assertEqual(CT.get('g.ok', 0.95, target, bind='m1')['threshold'], 0.9)
            self.assertIn('Learn-then-Test on 200', CT.get('g.ok', 0.95, target, bind='m1')['guarantee'])
            self.assertEqual(CT.get('g.ok', 0.95, target, bind='m2')['threshold'], 0.95)  # another scorer: fallback
            self.assertEqual(CT.get('g.weak', 0.8, target)['threshold'], 0.8)
            self.assertIn('needs >= 114', CT.get('g.weak', 0.8, target)['reason'])
            self.assertEqual(CT.get('g.missing', 0.7, target)['threshold'], 0.7)
            with mock.patch.dict(os.environ, {'AGERE_CALIBRATED_THRESHOLDS': 'off'}):
                self.assertEqual(CT.get('g.ok', 0.95, target, bind='m1')['threshold'], 0.95)
            floor = CT.fit('g.floor', [0.5] * 200 + [0.9] * 200, [False] * 400, 0.85, {}, floor=0.8)
            self.assertEqual(floor['threshold'], 0.9)

    def test_gates_read_calibrated_values(self):
        from clinical_core.agere.clinical import economical_planner as EP
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 't.json'
            CT.write([CT.fit('economical_planner.decision', [0.97] * 200, [False] * 200, 0.95, {})], target)
            with mock.patch.dict(os.environ, {'AGERE_THRESHOLDS_FILE': str(target)}):
                body = EP.decision_body('q', [{'id': 'sources', 'description': 'sources'}])
                r = EP.parse_decision({'answers': {'route': {'choice': 'sources', 'probabilities': {'sources': .96, 'frontier': .04}}}}, body)
                self.assertEqual(r['threshold'], 0.97); self.assertFalse(r['accepted']); self.assertTrue(r['threshold_calibrated'])
        r = EP.parse_decision({'answers': {'route': {'choice': 'sources', 'probabilities': {'sources': .96, 'frontier': .04}}}}, body)
        self.assertEqual(r['threshold'], EP.DECISION_THRESHOLD)


class Readings(unittest.TestCase):
    A = "-- DEFINITION: ED visits by event_date in 2028, counting visits\nSELECT count(*) FROM visit WHERE substr(event_date,1,4)='2028'"
    B = "-- DEFINITION: ED visits by available_date in 2028, counting visits\nSELECT count(*) FROM visit WHERE substr(available_date,1,4)='2028'"

    def test_definitional_difference(self):
        a, b = cand('primary', {'value': 120}, self.A), cand('second', {'value': 131}, self.B, order=1)
        diff = v2.definitional_difference(a, b)
        self.assertEqual(diff['dimensions'][0]['dimension'], 'date field')
        self.assertIsNone(v2.definitional_difference(a, cand('second', {'value': 1310}, self.B)))  # 10x apart: an error
        self.assertIsNone(v2.definitional_difference(a, cand('second', {'value': 131}, 'SELECT 1')))  # no stated definition
        self.assertIsNone(v2.definitional_difference(a, cand('second', {'patient_ids': ['p']}, self.B)))  # different shape

    def test_readings_output_and_ladder(self):
        a, b = cand('primary', {'value': 120}, self.A), cand('second', {'value': 131}, self.B, order=1)
        alt, diff = v2.alternative_reading(a, [a, b])
        r = v2.readings(a, alt, diff)
        self.assertEqual([x['default'] for x in r['readings']], [True, False])
        self.assertEqual(r['readings'][1]['result'], 131.0)
        self.assertIn('event_date', r['clarifying_question']); self.assertIn('available_date', r['clarifying_question'])
        answer = v2.attach_readings({'status': 'answered', 'answer_type': 'count', 'value': 120, 'warnings': []}, r)
        self.assertEqual(len(answer['readings']), 2)
        self.assertEqual(answer['value'], 120)  # the default's number is unchanged
        self.assertTrue(any(c['control'] == 'QC-READINGS' and c['fmea'] == 'FM-19' for c in answer['quality_checks']))
        self.assertTrue(any('Two defensible readings' in w for w in answer['warnings']))

    def test_ask_view_renders_readings_side_by_side(self):
        from clinical_core.agere import ask_view
        page = ask_view.render() if hasattr(ask_view, 'render') else (ROOT / 'clinical_core/agere/ask_view.py').read_text()
        self.assertIn('function readingsBox', page); self.assertIn('Two defensible readings', page)
        self.assertIn('warnBox(a)+readingsBox(a', page)


if __name__ == '__main__':
    unittest.main()
