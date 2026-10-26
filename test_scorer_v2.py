"""Scorer v2 (tools/dev_set/run.py): the three false positives of the 2026-10-25 audit, true positives that must stay correct, keys v2 overlay."""
import json
import unittest

from tools.dev_set import run


def note_q(answer, quote, question='Is the patient taking lisinopril?'):
    return {'id': 'n1', 'answer_type': 'note', 'question': question, 'evidence': [{'record_id': 'R1', 'quote': quote}],
            'key': {'kind': 'yes_no', 'answer': answer}}


def note_resp(headline, quote):
    return {'status': 'answered', 'answer': {'status': 'answered', 'headline': headline,
                                             'sources': [{'record_id': 'R1', 'quote': quote}]}}


def table_q(rows, columns=None, order=False):
    return {'id': 't1', 'answer_type': 'table', 'key': {'answer': rows, 'tolerance': {'abs': 0.01}, **({'columns': columns} if columns else {}), 'order_matters': order}}


def table_resp(rows):
    return {'status': 'answered', 'answer': {'status': 'answered', 'table': rows}}


def rubric_q(terms_patterns, mode='any'):
    return {'id': 'r1', 'answer_type': 'rubric', 'key': {'answer': {'required_facts': [
        {'id': 'direction', 'kind': 'terms', 'patterns': terms_patterns, 'mode': mode}], 'forbidden': []}}}


def text_resp(text):
    return {'status': 'answered', 'answer': {'status': 'answered', 'headline': text}}


class PassagePolarity(unittest.TestCase):
    def test_negating_passage_does_not_support_yes(self):
        q = note_q('yes', 'Patient takes lisinopril 10 mg daily')
        r = note_resp('Yes, takes lisinopril', 'Pharmacy does not recognize lisinopril as a current medicine')
        self.assertTrue(run.score(q, r, 'v1')[0])       # the v1 false positive
        ok, d = run.score(q, r, 'v2')
        self.assertFalse(ok)
        self.assertFalse(d['passage_supports_claim'])

    def test_affirming_passage_does_not_support_no(self):
        q = note_q('no', 'Patient stopped lisinopril last year')
        r = note_resp('No, not taking lisinopril', 'Currently takes lisinopril 10 mg daily')
        self.assertFalse(run.score(q, r, 'v2')[0])

    def test_true_positives_stay_correct(self):
        q = note_q('yes', 'Patient takes lisinopril 10 mg daily')
        self.assertTrue(run.score(q, note_resp('Yes, takes lisinopril', 'Patient takes lisinopril 10 mg daily'), 'v2')[0])
        q = note_q('no', 'Patient stopped lisinopril last year')
        self.assertTrue(run.score(q, note_resp('No, stopped lisinopril', 'Patient stopped lisinopril last year, not taking it'), 'v2')[0])
        q = note_q('no', 'Patient denies chest pain', 'Does the patient have chest pain?')
        self.assertTrue(run.score(q, note_resp('No chest pain', 'Patient denies chest pain'), 'v2')[0])


class TableBinding(unittest.TestCase):
    def test_value_in_wrong_column_fails(self):
        q = table_q([{'name': 'A', 'net_usd': 100.0}], columns=['name', 'net_usd'])
        r = table_resp([{'name': 'A', 'gross_usd': 100.0, 'net_usd': 90.0}])
        self.assertTrue(run.score(q, r, 'v1')[0])       # v1: 100 appears somewhere in the row
        ok, d = run.score(q, r, 'v2')
        self.assertFalse(ok)
        self.assertEqual(d['column_binding']['net_usd'], 'net_usd')

    def test_true_positives_stay_correct(self):
        q = table_q([{'year': 2028, 'pmpm_usd': 301.02}, {'year': 2029, 'pmpm_usd': 307.72}], order=True)
        self.assertTrue(run.score(q, table_resp([{'Year': 2028, 'PMPM': 301.02}, {'Year': 2029, 'PMPM': 307.72}]), 'v2')[0])
        q = table_q([{'tier': 'brand', 'reject_rate': 0.0454}])
        self.assertTrue(run.score(q, table_resp([{'tier': 'brand', 'reject_rate': 4.54}]), 'v2')[0])   # percent form
        q = table_q([{'dx': 'M17.11', 'paid_usd': 958414.9}], columns=['dx', 'paid_usd'])
        self.assertTrue(run.score(q, table_resp([{'diagnosis': 'M17.11', 'amount': 958414.9}]), 'v2')[0])  # bound by key column order


class RubricDirection(unittest.TestCase):
    PAT = [r'\b(fell|fall|declin\w*|decreas\w*|dropp?\w*|lower|down)\b']

    def test_opposite_asserted_and_fell_negated_fails(self):
        q = rubric_q(self.PAT)
        t = 'Admissions jumped from 533 to 578. Saying they fell is incorrect.'
        self.assertTrue(run.score(q, text_resp(t), 'v1')[0])
        self.assertFalse(run.score(q, text_resp(t), 'v2')[0])

    def test_did_not_fall_fails(self):
        self.assertFalse(run.score(rubric_q(self.PAT), text_resp('Admissions did not fall; they rose to 578.'), 'v2')[0])

    def test_true_positives_stay_correct(self):
        q = rubric_q(self.PAT)
        self.assertTrue(run.score(q, text_resp('Admissions fell from 578 to 533.'), 'v2')[0])
        self.assertTrue(run.score(q, text_resp('Admissions did not jump; they fell from 578 to 533.'), 'v2')[0])
        self.assertTrue(run.score(q, text_resp('Admissions fell, while ED visits rose in 2029.'), 'v2')[0] is False)  # same sentence asserts both
        self.assertTrue(run.score(q, text_resp('Admissions fell. ED visits rose in 2029.'), 'v2')[0])
        q = rubric_q([r'\b(more|higher|greater)\b'])
        self.assertTrue(run.score(q, text_resp('Barrier patients no-show more often (9.6% vs a lower 4.7%).'), 'v2')[0])

    def test_fact_share_reported(self):
        ok, d = run.score(rubric_q(self.PAT), text_resp('Admissions rose.'), 'v2')
        self.assertFalse(ok)
        self.assertEqual(d['fact_share'], 0.0)


class KeysV2(unittest.TestCase):
    def test_overlay_does_not_touch_v1_and_sets_fields(self):
        from tools.dev_set.dev3_keys import read
        v1 = [q for q in read() if q['id'] in ('x3-09', 'x5-03')]
        before = json.dumps(v1, sort_keys=True)
        v2 = run.apply_keys_v2(v1)
        self.assertEqual(json.dumps(v1, sort_keys=True), before)
        self.assertEqual(v2[0]['key']['answer'], 474)
        facts = {f['id']: f for f in v2[1]['key']['answer']['required_facts']}
        self.assertEqual(facts['a29']['value'], 635.0)

    def test_every_row_applies_and_v1_differs(self):
        from tools.dev_set.dev3_keys import read
        from tools.dev_set.dev3_keys_v2 import read as rows
        qs = run.apply_keys_v2(read())
        self.assertEqual(len(qs), len(read()))
        self.assertTrue(rows())


if __name__ == '__main__':
    unittest.main()
