"""answer_checks.check: the one post-execution hook that puts every answer-checking component on the shared question path
(zero-result probe, answer guard suspicion + dense catalog probe, paid cross-check when live and uncertified, unchecked-zero
guard, answer-shape review, cascade certificates / gate policy). Synthetic fixtures only; no model, network or paid call."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import answer_checks as A
from clinical_core.agere.clinical import answer_guard as AG

ENV = {'AGERE_CATALOG_DENSE': 'off', 'AGERE_LOCAL_TYPED': 'off', 'AGERE_LOCAL_NORMALIZER': 'off'}
UNCAL = {'tier': 'local_compiler', 'status': 'uncalibrated', 'accept': True, 'certified': False, 'reason': 'no calibration record',
         'statement': 'local_compiler is uncalibrated (no calibration record); it answered as before'}
UNVERIFIED = {'level': 'unverified', 'reason': 'no Learn-then-Test certificate for: local_compiler.answer', 'gates': []}


def warehouse(root):
    db = sqlite3.connect(Path(root)/'semantic.sqlite3')
    db.executescript("""
      CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, payload TEXT);
      CREATE TABLE medication(record_id TEXT, patient_id TEXT, source_stream TEXT, evidence TEXT, drug_name TEXT, drug_class TEXT,
        status TEXT, event_date TEXT);
      CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, label TEXT, icd10 TEXT, event_date TEXT);
      CREATE TABLE encounter_diagnosis(encounter_id TEXT, patient_id TEXT, rank INTEGER, icd10 TEXT, type TEXT, record_id TEXT,
        source TEXT, event_date TEXT);
    """)
    db.executemany('INSERT INTO record VALUES (?,?,?,?,?)', [('r1', 'p1', 'medication_lists', '2029-01-01', '{}'),
                                                             ('r2', 'p2', 'pharmacy_dispenses', '2029-02-01', '{}')])
    db.executemany('INSERT INTO medication VALUES (?,?,?,?,?,?,?,?)', [
        ('m1', 'p1', 'pharmacy_dispenses', 'dispensed', 'Atorvastatin', 'statin', 'dispensed', '2029-03-01'),
        ('m2', 'p2', 'pharmacy_dispenses', 'dispensed', 'Rosuvastatin', 'statin', 'dispensed', '2029-04-01'),
        ('m3', 'p3', 'medication_lists', 'listed', 'Lisinopril', 'ace_inhibitor', 'active', '2029-01-05')])
    db.executemany('INSERT INTO condition VALUES (?,?,?,?,?,?,?)', [
        ('c1', 'p1', 'conditions', 'heart_failure', 'Heart failure', 'I50.9', '2029-01-01')])
    db.executemany('INSERT INTO encounter_diagnosis VALUES (?,?,?,?,?,?,?,?)', [
        ('e1', 'p4', 1, 'I50.22', 'principal', 'x', 'payer_claim', '2029-05-01')])
    db.commit(); db.close()


def sql_graph(sql, returns='count'):
    return {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': returns}}]}


def count(n, sql=''):
    return {'answer': {'status': 'answered', 'answer_type': 'count', 'count': n, 'query': sql, 'patient_ids': [], 'missing': []}, 'results': []}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        warehouse(self.tmp.name)
        self.stack = SimpleNamespace(path=self.tmp.name, allow_live=False)
        env = patch.dict(os.environ, ENV); env.start(); self.addCleanup(env.stop)
        for target, value in (('clinical_core.agere.clinical.cascade.certify_route', UNCAL),
                              ('clinical_core.agere.clinical.gate_policy.assess', UNVERIFIED)):
            p = patch(target, return_value=value); p.start(); self.addCleanup(p.stop)

    def check(self, question, sql, result, **kw):
        kw.setdefault('profile', {'streams': {}})
        return A.check(self.stack, question, sql_graph(sql), {'route': 'local_compiler'}, result, **kw)


class ZeroResultProbe(Base):
    MED_LIST = "SELECT count(DISTINCT patient_id) FROM medication WHERE source_stream = 'medication_lists' AND drug_class = 'statin'"

    def test_zero_with_the_data_in_another_source_rejects_naming_it(self):
        v = self.check('How many patients are on a statin?', self.MED_LIST, count(0, self.MED_LIST))
        self.assertFalse(v['accept'])
        self.assertIn('pharmacy_dispenses', ' '.join(v['reasons']))
        self.assertEqual(v['probe']['flagged'], ['statin'])
        self.assertNotIn('medication_lists', [p['literal'] for p in v['probe']['predicates']])  # a source selector, not a predicate
        self.assertEqual(v['checks_run'][0], 'zero_result_probe_shared')

    def test_condition_evidence_in_encounter_diagnoses_via_concept_codes(self):
        sql = "SELECT count(DISTINCT patient_id) FROM condition WHERE concept = 'heart_failure' AND event_date >= '2029-04-01'"
        v = self.check('How many patients had heart failure diagnosed in 2029?', sql, count(0, sql))
        self.assertTrue(v['accept'], v['reasons'])  # condition holds heart_failure in 2029: the zero comes from another filter
        sql2 = "SELECT count(DISTINCT patient_id) FROM condition WHERE source_table = 'problem_lists' AND concept = 'heart_failure'"
        v2 = self.check('How many patients have heart failure?', sql2, count(0, sql2))
        self.assertFalse(v2['accept'])
        self.assertIn('encounter_diagnosis', ' '.join(v2['reasons']))

    def test_a_zero_that_no_other_source_holds_is_confirmed(self):
        sql = "SELECT count(DISTINCT patient_id) FROM medication WHERE drug_class = 'sglt2_inhibitor'"
        v = self.check('How many patients are on an SGLT2 inhibitor?', sql, count(0, sql))
        self.assertTrue(v['accept'])
        self.assertFalse(v['probe']['flagged'])
        self.assertFalse(any('Unchecked zero' in a for a in v['annotations']))  # the probe confirmed it

    def test_non_zero_and_absence_questions_are_not_probed(self):
        self.assertIsNone(self.check('How many patients are on a statin?', self.MED_LIST, count(5, self.MED_LIST))['probe'])
        v = self.check('How many patients are not on a statin?', self.MED_LIST, count(0, self.MED_LIST))
        self.assertTrue(v['accept']); self.assertFalse(v['probe']['flagged'])

    def test_period_applies(self):
        sql = "SELECT count(*) FROM medication WHERE source_stream = 'medication_lists' AND drug_class = 'statin'"
        v = self.check('How many statin users in 2031?', sql, count(0, sql))
        self.assertTrue(v['accept'])  # no statin anywhere in 2031

    def test_all_unknown_and_empty_triggers(self):
        self.assertEqual(A._zero_kind({'status': 'answered', 'answer_type': 'table', 'table': []}), 'empty')
        self.assertEqual(A._zero_kind({'status': 'answered', 'answer_type': 'table', 'table': [{'g': 'a', 'n': None}]}), 'all_unknown')
        self.assertEqual(A._zero_kind({'status': 'answered', 'answer_type': 'patient_set', 'patient_ids': []}), 'empty')
        self.assertIsNone(A._zero_kind({'status': 'unsupported', 'answer_type': 'unsupported'}))


class Guard(Base):
    def test_unread_likely_catalog_source_rejects(self):
        cand = {'stream': 'wellness_visit_services', 'likely': True, 'evidence': [{'field': 'hcpcs_code', 'value': 'G0438', 'kind': 'value',
                'records': 9, 'period_records': 4}], 'period': ['2029-01-01', '2029-12-31'], 'records': 9}
        probe = {'terms': ['wellness'], 'unmatched_terms': [], 'period': ['2029-01-01', '2029-12-31'], 'streams_searched': 3, 'candidates': [cand]}
        sql = "SELECT count(*) FROM visit WHERE type = 'preventive'"
        with patch.object(AG, 'probe', return_value=probe) as p:
            v = self.check('How many annual wellness visits in 2029?', sql, count(0, sql))
        self.assertEqual(p.call_args.kwargs.get('encoder'), None)
        self.assertFalse(v['accept'])
        self.assertIn("wellness_visit_services has 4 records in 2029 with hcpcs_code 'G0438'", ' '.join(v['reasons']))
        self.assertIn('answer_guard_dense_catalog', v['checks_run'])

    def test_spelling_contradiction_rejects(self):
        profile = {'streams': {'appointments': {'records': 5, 'fields': {'reason': {'populated': 5, 'values': {'transportation': 5}}}}}}
        sql = "SELECT count(*) FROM record WHERE stream = 'appointments' AND json_extract(payload,'$.reason') = 'Transportation'"
        v = self.check('How many appointments were missed for transportation reasons?', sql, count(0, sql), profile=profile)
        self.assertFalse(v['accept'])
        self.assertIn('a spelling no record uses', ' '.join(v['reasons']))

    def test_dollar_question_answered_with_a_count_rejects(self):
        sql = 'SELECT count(*) FROM claim'
        v = self.check('What was the total paid amount in dollars?', sql, count(12, sql))
        self.assertFalse(v['accept'])
        self.assertIn('asks for dollars', ' '.join(v['reasons']))

    def test_zero_with_nothing_anywhere_is_annotated(self):
        sql = "SELECT count(*) FROM medication WHERE drug_class = 'sglt2_inhibitor'"
        v = self.check('How many patients are on an SGLT2 inhibitor?', sql, count(0, sql))
        self.assertTrue(any(a.startswith('Catalog check') for a in v['annotations']))

    def test_no_profile_is_annotated_not_rejected(self):
        sql = "SELECT count(*) FROM medication WHERE drug_class = 'sglt2_inhibitor'"
        with patch.object(A, '_profile', return_value=None):
            v = self.check('How many patients are on an SGLT2 inhibitor?', sql, count(0, sql), profile=None)
        self.assertTrue(v['accept'])
        self.assertTrue(any('no source profile' in a for a in v['annotations']))


class CrossCheck(Base):
    def fake(self, out):
        class Pending:
            def __init__(s, *a, **k): Pending.made = True
            def provide(s, answer): pass
            def result(s): return out
        Pending.made = False
        return Pending

    def test_off_unless_live(self):
        P = self.fake(None)
        with patch.object(AG, 'PendingCheck', P):
            self.check('How many patients have diabetes?', 'SELECT 1', count(7))
        self.assertFalse(P.made)

    def test_live_uncertified_disagreement_rejects_and_counts_cost(self):
        self.stack.allow_live = True
        other = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 9}}
        with patch.object(AG, 'PendingCheck', self.fake(('hosted_verified_sql', {}, {}, other, 0.12, 3))):
            v = self.check('How many patients have diabetes?', 'SELECT 1', count(7), ledger=object())
        self.assertFalse(v['accept'])
        self.assertIn('independent path (hosted_verified_sql)', ' '.join(v['reasons']))
        self.assertEqual(v['cost'], {'new_paid_api_calls': 3, 'usd': 0.12})

    def test_agreement_and_relaxed_disagreement_annotate(self):
        self.stack.allow_live = True
        same = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 7}}
        with patch.object(AG, 'PendingCheck', self.fake(('hosted_verified_sql', {}, {}, same, 0.1, 2))):
            v = self.check('How many patients have diabetes?', 'SELECT 1', count(7), ledger=object())
        self.assertTrue(v['accept']); self.assertTrue(any(a.startswith('Cross-checked') for a in v['annotations']))
        diff = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 3}}
        with patch.object(AG, 'PendingCheck', self.fake(('local_compiler (relaxed)', {}, None, diff, 0.0, 0))):
            v = self.check('How many patients have diabetes?', 'SELECT 1', count(7), ledger=object())
        self.assertTrue(v['accept'])

    def test_certified_answers_are_not_cross_checked(self):
        self.stack.allow_live = True
        P = self.fake(None)
        cert = {**UNCAL, 'status': 'certified', 'certified': True, 'statement': 'certified tier'}
        with patch.object(AG, 'PendingCheck', P), patch('clinical_core.agere.clinical.cascade.certify_route', return_value=cert):
            v = self.check('How many patients have diabetes?', 'SELECT 1', count(7), ledger=object())
        self.assertFalse(P.made)
        self.assertEqual(v['certificate']['status'], 'certified')


class UncheckedZero(Base):
    def test_unconfirmed_zero_is_low_confidence_not_rejected(self):
        with patch.object(A, 'zero_result_probe', return_value=None), patch.object(A, '_guard'):
            v = self.check('How many patients have gout?', "SELECT count(*) FROM condition WHERE concept = 'gout'", count(0))
        self.assertTrue(v['accept'])
        self.assertEqual(v['confidence'], 'low')
        self.assertIn('unchecked_zero_guard', v['checks_run'])

    def test_analyst_executed_zero_is_confirmed(self):
        with patch.object(A, 'zero_result_probe', return_value=None), patch.object(A, '_guard'):
            v = A.check(self.stack, 'How many patients have gout?', sql_graph('SELECT 1'), {'route': 'hybrid_analysts'}, count(0),
                        profile={'streams': {}})
        self.assertNotIn('confidence', v)


class Shape(Base):
    def test_unambiguous_reshape(self):
        res = {'answer': {'status': 'answered', 'answer_type': 'patient_set', 'patient_ids': ['p1', 'p2'], 'count': 2, 'headline': 'x'}}
        v = self.check('How many patients have heart failure?', 'SELECT patient_id FROM condition', res)
        self.assertTrue(v['accept'])
        self.assertEqual((v['result']['answer']['answer_type'], v['result']['answer']['count']), ('count', 2))
        self.assertNotIn('headline', v['result']['answer'])

    def test_multi_row_table_for_how_many_rejects(self):
        res = {'answer': {'status': 'answered', 'answer_type': 'table', 'table': [{'payer': 'a', 'n': 1}, {'payer': 'b', 'n': 2}]}}
        v = self.check('How many patients have heart failure?', 'SELECT payer, count(*) n FROM claim GROUP BY payer', res)
        self.assertFalse(v['accept'])
        self.assertIn('answer shape', ' '.join(v['reasons']))

    def test_already_withheld_rejects(self):
        res = {'answer': {'status': 'partial', 'answer_type': 'unsupported', 'withheld': {'rows': 2},
                          'missing': ['Answer shape: the question asks for a count; the route returned a table']}}
        v = self.check('How many patients have heart failure?', 'SELECT 1', res)
        self.assertFalse(v['accept'])


class Certificates(Base):
    def test_below_threshold_rejects_uncalibrated_annotates(self):
        below = {'tier': 'semantic_cache', 'status': 'below_threshold', 'accept': False, 'certified': False, 'score': 0.4,
                 'threshold': 0.56, 'statement': 'semantic_cache deferred: score 0.4 is below its certified threshold'}
        with patch('clinical_core.agere.clinical.cascade.certify_route', return_value=below):
            v = self.check('How many patients have heart failure?', 'SELECT 1', count(4))
        self.assertFalse(v['accept']); self.assertIn('certificate:', ' '.join(v['reasons']))
        v = self.check('How many patients have heart failure?', 'SELECT 1', count(4))
        self.assertTrue(v['accept'])
        self.assertEqual(v['certificate']['status'], 'uncalibrated')
        self.assertEqual(v['checks_run'][-1], 'cascade_certificates')

    def test_not_certified_tiers_still_propose(self):
        nc = {**UNCAL, 'status': 'not_certified', 'accept': False, 'statement': 'local_compiler deferred'}
        with patch('clinical_core.agere.clinical.cascade.certify_route', return_value=nc):
            self.assertTrue(self.check('How many patients have heart failure?', 'SELECT 1', count(4))['accept'])


class Isolation(Base):
    def test_one_failing_check_never_stops_the_others(self):
        with patch.object(A, 'zero_result_probe', side_effect=RuntimeError('boom')):
            v = self.check('How many patients have heart failure?', 'SELECT 1', count(4))
        self.assertTrue(any('zero_result_probe_shared failed: RuntimeError: boom' in a for a in v['annotations']))
        self.assertEqual(v['checks_run'], ['zero_result_probe_shared', 'answer_guard_suspicion', 'answer_guard_dense_catalog',
                                           'answer_guard_cross_check', 'unchecked_zero_guard', 'release_drift_guard', 'answer_shape_review',
                                           'cascade_certificates'])

    def test_no_answer_object(self):
        self.assertTrue(A.check(self.stack, 'q', {}, {}, {'results': []})['accept'])


class Gate(Base):
    def cascade(self):
        log = []
        return SimpleNamespace(stack=self.stack, question=None, catalog=SimpleNamespace(identity={}), ledger=None,
                               reject=lambda n, r: log.append((n, r)), spend=lambda *a: log.append(('spend', a)), log=log)

    def test_rejection_goes_to_cascade_reject(self):
        c = self.cascade(); c.question = 'How many patients are on a statin?'
        sql = ZeroResultProbe.MED_LIST
        with patch.object(A, '_profile', return_value={'streams': {}}):
            out = A.gate(c, 'semantic_cache', sql_graph(sql), {'route': 'semantic_cache'}, count(0, sql))
        self.assertIsNone(out)
        self.assertEqual(c.log[0][0], 'semantic_cache'); self.assertIn('pharmacy_dispenses', c.log[0][1][0])

    def test_accepted_result_carries_annotations_and_receipt(self):
        c = self.cascade(); c.question = 'How many patients have heart failure?'
        receipt = {'route': 'local_compiler'}
        with patch.object(A, '_profile', return_value={'streams': {}}):
            out = A.gate(c, 'exact_local_plan', sql_graph('SELECT 1'), receipt, count(4))
        self.assertIn('Certificate: uncalibrated (no calibration record).', out['answer']['limitations'])
        self.assertTrue(receipt['answer_checks']['accept'])
        self.assertEqual(out['answer_checks']['checks_run'][0], 'zero_result_probe_shared')


if __name__ == '__main__':
    unittest.main()
