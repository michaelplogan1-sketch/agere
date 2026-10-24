"""Tests for the composition development set (split dev2): schema, families, chains, key recomputation, no overlap with the dev questions.

    .venv/bin/python -m unittest tools.dev_set.test_compose_set
The key-recompute test opens the semantic layer read-only and skips when it is absent. Lines of questions.jsonl whose split is
"heldout" are filtered out BEFORE they are parsed (this test never reads a held-out question).
"""
from __future__ import annotations

import difflib
import json
import re
import unittest
from collections import Counter
from pathlib import Path

from tools.dev_set import compose_keys as CK
from tools.dev_set import keys, run, run_inprocess

ROWS = CK.read()
TYPES = {'count', 'value', 'share', 'patient_set', 'table', 'list', 'label', 'date', 'yes_no', 'absent', 'rubric', 'note'}
FIELDS = {'id', 'bucket', 'split', 'family', 'question', 'follow_up_of', 'answer_type', 'key', 'evidence', 'notes', 'key_name'}


def have_db():
    try:
        keys.db_path()
        return True
    except FileNotFoundError:
        return False


def existing_dev_texts():
    """Question text (and follow-up standalone text) of every non-held-out question already in tools/dev_set (dev + paraphrases)."""
    out = []
    for name in ('questions.jsonl', 'paraphrases.jsonl'):
        for line in (Path(keys.HERE) / name).read_text().splitlines():
            if not line.strip() or '"split": "heldout"' in line:
                continue
            q = json.loads(line)
            out.append(q['question'])
            if q.get('key', {}).get('standalone'):
                out.append(q['key']['standalone'])
    return out


def norm(text):
    return re.sub(r'[^a-z0-9 ]+', '', text.lower()).strip()


class SchemaTests(unittest.TestCase):
    def test_size_and_split(self):
        self.assertGreaterEqual(len(ROWS), 115)
        self.assertEqual({r['split'] for r in ROWS}, {'dev2'})
        self.assertEqual({r['bucket'] for r in ROWS}, set(CK.BUCKETS))

    def test_row_schema(self):
        ids = set()
        for r in ROWS:
            self.assertEqual(set(r), FIELDS)
            self.assertIn(r['answer_type'], TYPES)
            self.assertIn(r['family'], CK.FAMILIES)
            self.assertTrue(r['question'].strip())
            self.assertNotIn(r['id'], ids)
            ids.add(r['id'])
            self.assertIn('answer', r['key'])
            if r['answer_type'] == 'absent':
                self.assertTrue(r['key'].get('reason'))
            else:
                self.assertIsNotNone(r['key']['answer'], r['id'])
            if r['answer_type'] == 'share':
                self.assertTrue(r['key']['denominator'], r['id'])
                self.assertIn('tolerance', r['key'])

    def test_every_family_covered_evenly(self):
        c = Counter(r['family'] for r in ROWS)
        self.assertEqual(set(c), set(CK.FAMILIES))
        for fam, n in c.items():
            self.assertGreaterEqual(n, 8, fam)
        self.assertGreaterEqual(c['absent'], 10)

    def test_follow_up_chains(self):
        by = {r['id']: r for r in ROWS}
        n = 0
        for r in ROWS:
            if r['follow_up_of']:
                n += 1
                parent = by[r['follow_up_of']]
                self.assertEqual(parent['bucket'], r['bucket'])
                self.assertEqual(r['family'], 'follow_up')
                self.assertTrue(r['key'].get('standalone'), r['id'])
                self.assertFalse(parent['follow_up_of'], 'chains are two turns')
        self.assertGreaterEqual(n, 8)

    def test_chains_group_under_run_py(self):
        qs = ROWS
        chains = run.chains(qs, ROWS)
        self.assertEqual(sum(len(c) for c in chains), len(ROWS))
        self.assertEqual(sum(1 for c in chains if len(c) == 2), sum(1 for r in ROWS if r['follow_up_of']))

    def test_runners_accept_the_set(self):
        import argparse
        a = argparse.Namespace(set='compose', split=None, bucket=None, ids=None, limit=None)
        qs = run.select(a)
        self.assertEqual(len(qs), len(ROWS))
        self.assertEqual({q['split'] for q in qs}, {'dev2'})
        self.assertEqual(len(run_inprocess.read('compose')), len(ROWS))

    def test_rubric_facts_have_kinds(self):
        for r in ROWS:
            if r['answer_type'] == 'rubric':
                self.assertTrue(r['key']['answer']['required_facts'], r['id'])
                for f in r['key']['answer']['required_facts']:
                    self.assertIn(f['kind'], ('number', 'terms', 'ranking'))


class OverlapTests(unittest.TestCase):
    def test_no_duplicate_text_within_set(self):
        texts = [norm(r['question']) for r in ROWS if not r['follow_up_of']]
        self.assertEqual(len(texts), len(set(texts)))

    def test_no_question_duplicates_an_existing_dev_question(self):
        old = {norm(t) for t in existing_dev_texts()}
        self.assertGreater(len(old), 100)
        for r in ROWS:
            for t in (r['question'], r['key'].get('standalone') or ''):
                if t:
                    self.assertNotIn(norm(t), old, f"{r['id']}: duplicates an existing dev question: {t}")

    def test_no_near_duplicate_of_an_existing_dev_question(self):
        old = [norm(t) for t in existing_dev_texts()]
        for r in ROWS:
            if r['follow_up_of']:
                continue
            t = norm(r['question'])
            best = max(difflib.SequenceMatcher(None, t, o).ratio() for o in old)
            self.assertLess(best, 0.85, f"{r['id']} is nearly identical to an existing dev question ({best:.2f}): {r['question']}")

    def test_heldout_lines_are_never_in_this_file(self):
        self.assertNotIn('heldout', Path(CK.OUT).read_text())


@unittest.skipUnless(have_db(), 'semantic layer not available')
class KeyTests(unittest.TestCase):
    def test_every_key_computes_and_matches_the_file(self):
        rows = CK.build_rows()
        have = {r['id']: r for r in ROWS}
        self.assertEqual(len(rows), len(have))
        for r in rows:
            h = have[r['id']]
            self.assertEqual(r['question'], h['question'])
            self.assertEqual(json.dumps(r['key'], sort_keys=True), json.dumps(h['key'], sort_keys=True), r['id'])
            if r['answer_type'] != 'absent':
                self.assertIsNotNone(r['key']['answer'], r['id'])

    def test_scorer_accepts_each_key_as_its_own_answer(self):
        """A response built from the key itself scores correct (the key and the scorer agree on every schema)."""
        for r in ROWS:
            t, k = r['answer_type'], r['key']
            if t in ('absent', 'rubric', 'patient_set', 'yes_no'):
                continue
            if t in ('count', 'value'):
                resp = {'status': 'answered', 'answer': {'status': 'answered', 'value': k['answer']}}
            elif t == 'share':
                resp = {'status': 'answered', 'answer': {'status': 'answered', 'value': k['answer']}}
            elif t == 'table':
                resp = {'status': 'answered', 'answer': {'status': 'answered', 'table': k['answer']}}
            else:
                continue
            ok, detail = run.score(r, resp)
            self.assertTrue(ok, (r['id'], detail))


if __name__ == '__main__':
    unittest.main()
