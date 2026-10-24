"""Tests for the development question set: schema, split, chains, definitions, key reproducibility, scorer behaviour.

    .venv/bin/python -m unittest tools.dev_set.test_dev_set
The key-recompute tests open the semantic layer read-only and skip when it is absent.
"""
from __future__ import annotations

import json
import unittest
from collections import Counter

from tools.dev_set import definitions as D
from tools.dev_set import keys, run

ROWS = keys.read()
TYPES = {'count', 'value', 'share', 'patient_set', 'table', 'list', 'label', 'date', 'yes_no', 'absent', 'rubric', 'note'}


def have_db():
    try:
        keys.db_path()
        return True
    except FileNotFoundError:
        return False


class SchemaTests(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(len(ROWS), 150)
        c = Counter(r['bucket'] for r in ROWS)
        self.assertEqual(set(c), set(keys.BUCKETS))
        self.assertTrue(all(n == 30 for n in c.values()), c)

    def test_row_schema(self):
        ids = set()
        for r in ROWS:
            self.assertEqual(set(r) - {'key_name'}, {'id', 'bucket', 'split', 'question', 'follow_up_of', 'answer_type', 'key', 'evidence', 'notes'})
            self.assertIn(r['answer_type'], TYPES)
            self.assertIn(r['split'], ('dev', 'heldout'))
            self.assertTrue(r['question'].strip())
            self.assertNotIn(r['id'], ids)
            ids.add(r['id'])
            self.assertIn('answer', r['key'])
            if r['answer_type'] == 'absent':
                self.assertTrue(r['key'].get('reason'))
            else:
                self.assertIsNotNone(r['key']['answer'], r['id'])

    def test_one_third_heldout_per_bucket(self):
        for b in keys.BUCKETS:
            held = [r for r in ROWS if r['bucket'] == b and r['split'] == 'heldout']
            self.assertEqual(len(held), 10, b)

    def test_follow_up_chains(self):
        by = {r['id']: r for r in ROWS}
        n = 0
        for r in ROWS:
            if r['follow_up_of']:
                n += 1
                parent = by[r['follow_up_of']]
                self.assertEqual(parent['bucket'], r['bucket'])
                self.assertEqual(parent['split'], r['split'], 'a chain is never split across dev / heldout')
                self.assertLess(ROWS.index(parent), ROWS.index(r))
                self.assertTrue(r['key'].get('standalone'), r['id'] + ' needs a standalone question')
        self.assertTrue(12 <= n <= 20, n)

    def test_absent_share(self):
        n = sum(r['answer_type'] == 'absent' for r in ROWS)
        self.assertTrue(5 <= n <= 10, n)

    def test_note_items_have_evidence_or_not_documented(self):
        for r in ROWS:
            if r['answer_type'] == 'note' and r['key']['kind'] != 'not_documented':
                self.assertTrue(r['evidence'], r['id'])
                for e in r['evidence']:
                    self.assertTrue(e['record_id'] and e['quote'])


class DefinitionTests(unittest.TestCase):
    def test_window_constants(self):
        self.assertEqual(D.months_before(D.AS_OF, 12), D.LAST_YEAR_START)
        self.assertIn(D.LAST_YEAR_START, D.in_last_year('x'))

    def test_every_key_sql_is_built_from_definitions(self):
        # population / patient-set / share keys must go through the living-patient definition or a definitions fragment
        marks = ('FROM living', 'patient_id IN (SELECT patient_id FROM living)', 'member_month', 'payer_claim WHERE counts_in_metrics = 1',
                 'FROM ({', 'counts_in_metrics = 1', 'utilization_event', 'counts_as_fill = 1', 'payer_pharmacy_claim', 'computed in')
        for r in ROWS:
            if r['bucket'] in ('population_clinical_quality', 'population_claims_pharmacy_sdoh') and r['answer_type'] != 'absent':
                sql = r['key']['sql'] or ''
                self.assertTrue(any(m in sql for m in marks) or 'returned_fill' in sql or 'appointment' in sql, r['id'])

    def test_patient_sets_exclude_the_deceased(self):
        if not have_db():
            self.skipTest('semantic layer not available')
        ctx = keys.Ctx()
        dead = {x[0] for x in ctx.rows('SELECT patient_id FROM death')}
        for r in ROWS:
            if r['bucket'].startswith('population') and r['answer_type'] == 'patient_set':
                self.assertFalse(dead & set(r['key']['answer']), r['id'])

    def test_condition_cte_runs(self):
        if not have_db():
            self.skipTest('semantic layer not available')
        ctx = keys.Ctx()
        for name in D.CONDITIONS:
            self.assertGreaterEqual(ctx.one(f'WITH {D.LIVING_CTE}, {D.condition_cte(name)} SELECT COUNT(*) FROM has_{name}'), 0)


class KeyTests(unittest.TestCase):
    def test_sql_keys_recompute(self):
        if not have_db():
            self.skipTest('semantic layer not available')
        ctx = keys.Ctx()
        checked = 0
        for r in ROWS:
            sql = r['key']['sql']
            if r['answer_type'] in ('count', 'value', 'date', 'yes_no', 'label') and sql and sql.lstrip().upper().startswith(('SELECT', 'WITH')):
                got = ctx.rows(sql)[0][0]
                exp = r['key']['answer']
                if r['answer_type'] == 'yes_no':
                    got = bool(got)
                if isinstance(exp, float):
                    self.assertAlmostEqual(got, exp, places=4, msg=r['id'])
                else:
                    self.assertEqual(got, exp, r['id'])
                checked += 1
        self.assertGreaterEqual(checked, 30)

    def test_quotes_are_exact_substrings(self):
        if not have_db():
            self.skipTest('semantic layer not available')
        from tools.dev_set.b2_single_unstructured import record_text
        ctx = keys.Ctx()
        n = 0
        for r in ROWS:
            for e in r['evidence']:
                if r['bucket'] == 'single_patient_unstructured':
                    self.assertIn(e['quote'], record_text(ctx, e['record_id']), r['id'])
                    n += 1
        self.assertGreater(n, 20)

    def test_rubric_numbers_come_from_sql(self):
        for r in ROWS:
            if r['answer_type'] == 'rubric':
                facts = r['key']['answer']['required_facts']
                self.assertGreaterEqual(len(facts), 1, r['id'])
                self.assertTrue(any(f['kind'] == 'number' for f in facts) or r['id'], r['id'])


class ScorerTests(unittest.TestCase):
    def q(self, **kw):
        return {'answer_type': kw.pop('t'), 'key': kw.pop('key'), 'evidence': kw.pop('evidence', [])}

    def resp(self, **answer):
        return {'answer': {'status': 'answered', **answer}}

    def test_count_and_value(self):
        self.assertTrue(run.score(self.q(t='count', key={'answer': 5}), self.resp(answer_type='count', count=5))[0])
        self.assertFalse(run.score(self.q(t='count', key={'answer': 5}), self.resp(answer_type='count', count=6))[0])
        self.assertTrue(run.score(self.q(t='value', key={'answer': 7.0, 'tolerance': {'abs': 0.05}}), self.resp(value=7.04))[0])

    def test_share_accepts_percent(self):
        k = {'answer': 0.1442, 'tolerance': {'abs': 0.005}}
        self.assertTrue(run.score(self.q(t='share', key=k), self.resp(value=14.4))[0])
        self.assertTrue(run.score(self.q(t='share', key=k), self.resp(value=0.144))[0])
        self.assertFalse(run.score(self.q(t='share', key=k), self.resp(value=0.2))[0])

    def test_patient_set_exact(self):
        k = {'answer': ['A', 'B']}
        self.assertTrue(run.score(self.q(t='patient_set', key=k), self.resp(patient_ids=['B', 'A']))[0])
        self.assertFalse(run.score(self.q(t='patient_set', key=k), self.resp(patient_ids=['A']))[0])

    def test_refusal_is_not_scored_wrong_and_absent_wants_refusal(self):
        r = {'status': 'unsupported', 'answer': {'status': 'unsupported', 'answer_type': 'unsupported'}}
        self.assertIsNone(run.score(self.q(t='count', key={'answer': 1}), r)[0])
        self.assertTrue(run.score(self.q(t='absent', key={'answer': None, 'reason': 'x'}), r)[0])
        self.assertFalse(run.score(self.q(t='absent', key={'answer': None, 'reason': 'x'}), self.resp(count=3))[0])

    def test_absent_needs_a_stated_absence(self):
        k = self.q(t='absent', key={'answer': None, 'reason': 'x'})
        self.assertTrue(run.score(k, {'status': 'not_available', 'answer': {'status': 'not_available'}})[0])
        self.assertTrue(run.score(k, {'status': 'not_available'})[0])
        for bad in ({'status': 'error', 'error': 'timed out'}, {'status': 'not_understood'},
                    {'answer': {'status': 'not_understood'}}, {'status': 'internal_error'}, {}):
            self.assertFalse(run.score(k, bad)[0], bad)

    def test_trace_and_report_card(self):
        from tools.dev_set import report_card
        resp = {'answer': {'status': 'answered'}, 'cost_receipt': {'usd': 0.02, 'claude_calls_by_model': {'haiku': 2}},
                'planner_receipt': {'interpretation': {'answer_timing': {'total_seconds': 3.0}, 'proposers': [
                    {'proposer': 'exact_local_plan', 'outcome': 'no proposal', 'seconds': 0.1},
                    {'proposer': 'haiku_plan', 'outcome': 'rejected', 'seconds': None},
                    {'proposer': 'sonnet_plan', 'outcome': 'accepted', 'seconds': 2.5, 'rung': 'escalation'}]}}}
        tr = run.trace_of(resp)
        self.assertEqual(tr['answer_timing']['total_seconds'], 3.0)
        self.assertEqual([t['tier'] for t in tr['tiers']], ['exact_local_plan', 'haiku_plan', 'sonnet_plan'])
        self.assertEqual(tr['claude_calls_by_model'], {'haiku': 2})
        self.assertEqual(run.trace_of({'status': 'error'}), {'answer_timing': None, 'tiers': [], 'cost_receipt': None, 'claude_calls_by_model': None})
        failed = run.trace_of({'stage_diagnostics': {'events': [{'proposer': 'x', 'outcome': 'declined'}]}})
        self.assertEqual(failed['tiers'][0]['tier'], 'x')
        rows = [{'answered': True, 'correct': True, 'seconds': 3.0, 'usd': 0.02, 'route': 'sonnet_plan', 'trace': tr},
                {'answered': True, 'correct': False, 'seconds': 1.0, 'usd': 0.0, 'route': 'sonnet_plan', 'trace': tr},
                {'answered': True, 'correct': True, 'seconds': 0.5, 'usd': None, 'route': 'old_route'},  # older run: no trace
                {'answered': False, 'correct': None, 'seconds': 9.0, 'usd': None, 'route': 'none'}]
        c = report_card.card(rows)
        self.assertEqual((c['sonnet_plan']['reached'], c['sonnet_plan']['proposed'], c['sonnet_plan']['accepted'], c['sonnet_plan']['correct']), (2, 2, 2, 1))
        self.assertEqual(c['haiku_plan']['proposed'], 2)
        self.assertEqual(c['haiku_plan']['accepted'], 0)
        self.assertEqual(c['old_route']['accepted'], 1)
        text = report_card.render(rows)
        self.assertIn('50.0%', text)
        self.assertIn('p50', text)
        self.assertIn('rows with trace: 2/4', text)
        self.assertIn('unknown', report_card.render([{'answered': False, 'seconds': 1.0}]))

    def test_baseline_precision_and_rescore_skips(self):
        import io, tempfile, contextlib, pathlib
        from tools.dev_set import run_baseline
        f = pathlib.Path(tempfile.mkdtemp()) / 'saved.json'
        f.write_text(json.dumps({'rows': [{'id': 'x1', 'bucket': 'b'}, {'id': 'x2', 'bucket': 'b'}]}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_baseline.main(['--rescore', str(f)])
        self.assertIn('SKIPPED (no saved raw response) 2', buf.getvalue())

    def test_oracle_summary_separates_count_only(self):
        from tools import dev_oracle
        rows = [{'status': 'answered', 'match': True}, {'status': 'answered', 'match': True, 'count_only': True},
                {'status': 'answered', 'match': False}, {'status': 'refused'}]
        line = dev_oracle.summary_line(rows)
        self.assertIn('exact match 1', line)
        self.assertIn('count-only 1', line)
        self.assertIn('answered 3/4', line)

    def test_rubric(self):
        rub = {'required_facts': [{'id': 'a', 'kind': 'number', 'value': 339, 'tolerance': {'abs': 0}},
                                  {'id': 'b', 'kind': 'terms', 'patterns': [r'\bfell\b'], 'mode': 'any'}], 'forbidden': []}
        ok, d = run.score_rubric(rub, 'ED visits fell to 339 in Q3', 'ed visits fell to 339 in q3')
        self.assertTrue(ok)
        ok, d = run.score_rubric(rub, 'ED visits rose', 'ed visits rose')
        self.assertFalse(ok)
        self.assertEqual(d['fact_share'], 0.0)

    def test_note_requires_cited_record(self):
        q = {'answer_type': 'note', 'key': {'kind': 'short_text', 'key_groups': ['cost']}, 'evidence': [{'record_id': 'REC-1', 'quote': 'q'}]}
        self.assertTrue(run.score(q, {'answer': {'status': 'answered', 'summary': 'stopped for cost', 'grounding': ['REC-1']}})[0])
        self.assertFalse(run.score(q, {'answer': {'status': 'answered', 'summary': 'stopped for cost'}})[0])


if __name__ == '__main__':
    unittest.main()
