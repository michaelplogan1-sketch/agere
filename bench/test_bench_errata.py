"""Errata schema, errata scoring (canonical / stated alternative / precision), unchanged defaults, pass^k."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.common import ROOT, empty_answer, load_questions
from bench.errata import (CATEGORIES, ERRATA_PATH, load_errata, score_with_errata, states_definition,
                          validate_errata)
from bench.errata_replay import errata_match, rows_match
from bench.score import pass_k, rescore, score_answer, summarize

REL = 'release-x'


def count_answer(n, **extra):
    return {**empty_answer('count'), 'count': n, **extra}


def value_answer(v, **extra):
    return {**empty_answer('value'), 'value': v, **extra}


Q_COUNT = {'id': 'T01', 'answer_type': 'count', 'question': 'How many seniors?'}
Q_VALUE = {'id': 'T02', 'answer_type': 'value', 'question': 'What share?'}
ENTRY_C = {'category': 'c', 'evidence': 'e',
           'canonical': {'reading': 'latest age', 'source': 'errata', 'expected': {REL: {'count': 48}}},
           'alternatives': [{'id': 'age_roster', 'reading': 'roster age', 'source': 'reference',
                             'stated_any_of': [['roster age', 'as captured']]}]}
ENTRY_B = {'category': 'b', 'evidence': 'e', 'canonical': {'reading': 'ref', 'source': 'reference'},
           'alternatives': [{'id': 'day0', 'reading': 'same day counts', 'expected': {REL: {'count': 80}},
                             'stated_any_of': [['0-7', 'same day']]}]}


class ErrataFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions = {q['id']: q for q in load_questions()}
        cls.errata = load_errata(ERRATA_PATH, cls.questions)

    def test_every_question_has_one_entry_and_valid_category(self):
        # Splits written after the audit (e.g. later blind sets) are not yet audited: only audited splits must match.
        audited = {self.questions[q].get('split') for q in self.errata['questions'] if q in self.questions}
        self.assertEqual(set(self.errata['questions']), {q for q, v in self.questions.items() if v.get('split') in audited})
        self.assertTrue(all(e['category'] in CATEGORIES for e in self.errata['questions'].values()))

    def test_capability_split_not_included(self):
        self.assertFalse(any(q.get('split') == 'capability' for q in self.questions.values()))

    def test_errata_sql_files_exist_under_sql_errata(self):
        for qid, e in self.errata['questions'].items():
            for part in [e['canonical']] + e['alternatives']:
                if part.get('sql'):
                    self.assertTrue((ROOT / part['sql']).is_file(), qid)
                    self.assertTrue(part['sql'].startswith('sql_errata/'), qid)

    def test_non_a_entries_carry_a_different_reading(self):
        for qid, e in self.errata['questions'].items():
            if e['category'] in ('c', 'd'):
                self.assertEqual(e['canonical'].get('source'), 'errata', qid)
            if e['category'] == 'b':
                self.assertTrue(e['alternatives'], qid)

    def test_conventions_documented(self):
        self.assertIn('C1_age', self.errata['conventions'])


class SchemaValidationTests(unittest.TestCase):
    def base(self, entry):
        return {'schema': 'bench-errata/1', 'questions': {'T01': entry}}

    def test_accepts_valid_entries(self):
        validate_errata(self.base(ENTRY_C), {'T01': Q_COUNT})
        validate_errata(self.base(ENTRY_B), {'T01': Q_COUNT})

    def test_rejects_bad_entries(self):
        bad = []
        e = copy.deepcopy(ENTRY_B); e['alternatives'] = []; bad.append(e)              # b needs an alternative
        e = copy.deepcopy(ENTRY_C); e['canonical']['source'] = 'reference'; bad.append(e)  # c needs an errata answer
        e = copy.deepcopy(ENTRY_C); e['category'] = 'd'; bad.append(e)                  # d needs corrected SQL
        e = copy.deepcopy(ENTRY_B); e['alternatives'][0]['stated_any_of'] = []; bad.append(e)
        e = copy.deepcopy(ENTRY_B); e['alternatives'][0]['expected'] = {REL: {'value': 1}}; bad.append(e)
        e = copy.deepcopy(ENTRY_B); e['category'] = 'z'; bad.append(e)
        e = copy.deepcopy(ENTRY_B); e['precision'] = 2; bad.append(e)                     # precision only on value/table
        for entry in bad:
            with self.assertRaises(ValueError):
                validate_errata(self.base(entry), {'T01': Q_COUNT})
        with self.assertRaises(ValueError):
            validate_errata({'schema': 'bench-errata/1', 'questions': {'ZZZ': ENTRY_B}}, {'T01': Q_COUNT})
        with self.assertRaises(ValueError):
            validate_errata({'schema': 'other', 'questions': {}})


class ErrataScoringTests(unittest.TestCase):
    ref = count_answer(41)

    def test_canonical_match_is_correct_without_stating(self):
        s = score_with_errata(Q_COUNT, self.ref, count_answer(48), ENTRY_C, REL)
        self.assertTrue(s['correct']); self.assertEqual(s['errata'], 'canonical')

    def test_alternative_needs_stated_definition(self):
        stated = count_answer(41, interpretation='Age = roster age_reported as captured on the profile.')
        s = score_with_errata(Q_COUNT, self.ref, stated, ENTRY_C, REL)
        self.assertTrue(s['correct']); self.assertEqual(s['reason'], 'correct_alternative')
        silent = count_answer(41, interpretation='Count patients 65 and older.')
        s = score_with_errata(Q_COUNT, self.ref, silent, ENTRY_C, REL)
        self.assertFalse(s['correct']); self.assertEqual(s['reason'], 'alternative_unstated')
        self.assertFalse(s['false_assertion'])

    def test_definition_may_be_stated_in_readings_or_computed_as(self):
        a = count_answer(80, readings=[{'default': True, 'definition': 'visits 0-7 days after'}])
        self.assertTrue(score_with_errata(Q_COUNT, count_answer(81), a, ENTRY_B, REL)['correct'])
        a = count_answer(80, computed_as='window counts the same day')
        self.assertTrue(states_definition(a, ENTRY_B['alternatives'][0]['stated_any_of']))

    def test_wrong_answer_stays_wrong(self):
        s = score_with_errata(Q_COUNT, self.ref, count_answer(7, interpretation='roster age'), ENTRY_C, REL)
        self.assertFalse(s['correct']); self.assertTrue(s['false_assertion'])

    def test_reference_canonical_works_for_any_release_and_unknown_release_falls_back(self):
        self.assertTrue(score_with_errata(Q_COUNT, count_answer(81), count_answer(81), ENTRY_B, 'other')['correct'])
        s = score_with_errata(Q_COUNT, self.ref, count_answer(41), ENTRY_C, 'unknown-release')
        self.assertTrue(s['correct']); self.assertEqual(s['errata'], 'unavailable_for_release')

    def test_run_time_computed_expected_overrides_stored(self):
        s = score_with_errata(Q_COUNT, self.ref, count_answer(50), ENTRY_C, REL, {'canonical': count_answer(50)})
        self.assertTrue(s['correct'])

    def test_precision_convention(self):
        entry = {'category': 'a', 'evidence': 'e', 'canonical': {'reading': 'r', 'source': 'reference'},
                 'alternatives': [], 'precision': 1}
        ref = value_answer(24.4)
        self.assertTrue(score_with_errata(Q_VALUE, ref, value_answer(24.44), entry)['correct'])
        self.assertTrue(score_with_errata(Q_VALUE, value_answer(1.7), value_answer(1.65), entry)['correct'])
        self.assertFalse(score_with_errata(Q_VALUE, ref, value_answer(24.5), entry)['correct'])
        # default scoring is untouched
        self.assertFalse(score_answer(Q_VALUE, ref, value_answer(24.44))['correct'])


class DefaultsUnchangedTests(unittest.TestCase):
    def test_rescore_without_errata_is_the_strict_scorer(self):
        row = {'expected': count_answer(41), 'answer': count_answer(48)}
        self.assertEqual(rescore(row, Q_COUNT), score_answer(Q_COUNT, row['expected'], row['answer']))
        self.assertFalse(rescore(row, Q_COUNT)['correct'])

    def test_summary_numbers_do_not_depend_on_errata_module(self):
        rows = [{'score': score_answer(Q_COUNT, count_answer(1), count_answer(1)), 'estimated_cost_usd': 0.0, 'latency_s': 1},
                {'score': score_answer(Q_COUNT, count_answer(1), count_answer(2)), 'estimated_cost_usd': 0.0, 'latency_s': 2}]
        s = summarize(rows, 2)
        self.assertEqual((s['correct'], s['false_assertions'], s['attempted']), (1, 1, 2))
        self.assertNotIn('pass_k', s)


class PassKTests(unittest.TestCase):
    def attempts(self, *flags):
        return [{'started_at': f'2026-10-1{i}', 'score': {'correct': f}} for i, f in enumerate(flags)]

    def test_pass3_requires_three_correct_latest_attempts(self):
        res = pass_k([self.attempts(True, True, True), self.attempts(True, False, True),
                      self.attempts(True, True), self.attempts(False, True, True, True)], k=3)
        self.assertEqual(res['questions_measured'], 3)   # the 2-attempt question is not measured
        self.assertEqual(res['pass_k'], 2)               # latest three of the 4-attempt question are correct
        self.assertAlmostEqual(res['pass_k_rate'], 2 / 3)

    def test_reference_errors_do_not_count_as_attempts(self):
        rows = self.attempts(True, True) + [{'started_at': '2026-10-19', 'score': None, 'reference_error': 'x'}]
        self.assertEqual(pass_k([rows], 3)['questions_measured'], 0)

    def test_scorecard_reports_pass_k(self):
        from bench.score import write_scorecard
        questions = load_questions()
        q = next(x for x in questions if x['answer_type'] == 'count')
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'agere'; out.mkdir()
            exp = count_answer(3)
            lines = [json.dumps({'id': q['id'], 'system': 'agere', 'started_at': f'2026-10-1{i}T00:00', 'expected': exp,
                                 'answer': exp, 'score': None, 'estimated_cost_usd': 0.0, 'latency_s': 1}) for i in range(3)]
            (out / 'r.jsonl').write_text('\n'.join(lines))
            summaries = write_scorecard(Path(tmp), Path(tmp) / 'SC.md')
            self.assertEqual(summaries['agere']['pass_k'], {'k': 3, 'questions_measured': 1, 'pass_k': 1, 'pass_k_rate': 1.0})
            self.assertIn('Reliability (pass^3)', (Path(tmp) / 'SC.md').read_text())


class ReplayMatchTests(unittest.TestCase):
    def test_rows_match_ignores_column_names_and_uses_tolerance(self):
        q = {'answer_type': 'table'}
        exp = {'table': [{'year': 2026, 'avg': 24.4}, {'year': 2027, 'avg': 1.7}]}
        self.assertTrue(rows_match(q, exp, [[2027, 1.7], [2026, 24.4]]))
        self.assertFalse(rows_match(q, exp, [[2026, 24.44], [2027, 1.65]]))
        self.assertTrue(rows_match(q, exp, [[2026, 24.44], [2027, 1.65]], 0.05 + 1e-9))

    def test_errata_match_with_stated_alternative(self):
        ok, how = errata_match(Q_COUNT, count_answer(41), [[41]], ENTRY_C, REL, 'uses roster age')
        self.assertTrue(ok); self.assertEqual(how, 'alternative:age_roster')
        ok, how = errata_match(Q_COUNT, count_answer(41), [[48]], ENTRY_C, REL, '')
        self.assertTrue(ok); self.assertEqual(how, 'canonical')
        ok, how = errata_match(Q_COUNT, count_answer(41), [[41]], ENTRY_C, REL, '')
        self.assertFalse(ok); self.assertEqual(how, 'alternative_unstated:age_roster')


if __name__ == '__main__':
    unittest.main()
