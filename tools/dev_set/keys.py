"""Compute the development question set's answer keys from the semantic layer, reproducibly.

    .venv/bin/python -m tools.dev_set.keys            # (re)build tools/dev_set/questions.jsonl
    .venv/bin/python -m tools.dev_set.keys --check    # recompute every SQL key and compare with the file (no write)

Keys are INDEPENDENT of Agere's planners: plain SQL (or a short Python computation) over the semantic layer opened read-only,
written against definitions.py. The questions come from the bucket modules (b1_*.py ... b5_*.py), each a function
`build(ctx) -> list[Q]`; picks of patients / practices are seeded (SEED) so a rebuild gives the same file.

Unstructured items (bucket 2) are labeled by reading the note text (labels/unstructured.json: two independent passes, agreed
items only); keys.py re-verifies every quote against the stored text.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.dev_set import definitions as D

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
QUESTIONS = HERE / 'questions.jsonl'
SEED = 20261023
BUCKETS = ('single_patient_structured', 'single_patient_unstructured', 'population_clinical_quality',
           'population_claims_pharmacy_sdoh', 'insight')
HELDOUT_SHARE = 1 / 3          # exactly one third of EACH bucket, chosen by seeded random


def db_path() -> Path:
    for base in (ROOT, Path('/Users/mikel/clinical-insights-local')):
        p = base / D.SEMANTIC_DB_RELATIVE
        if p.exists():
            return p
    raise FileNotFoundError(D.SEMANTIC_DB_RELATIVE)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f'file:{db_path()}?mode=ro', uri=True)
    conn.execute('PRAGMA query_only = 1')
    as_of = conn.execute("SELECT value FROM meta WHERE key = 'as_of'").fetchone()[0]
    if as_of != D.AS_OF:
        raise RuntimeError(f'semantic layer as-of {as_of} differs from definitions.AS_OF {D.AS_OF}')
    return conn


class Ctx:
    """Read-only connection plus a seeded random stream, handed to every bucket module."""

    def __init__(self, conn=None, seed=SEED):
        self.conn = conn or connect()
        self.rng = random.Random(seed)
        self.n = 0

    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        r = self.conn.execute(sql, params).fetchone()
        return r[0] if r else None

    def dicts(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def pick(self, sql, params=(), k=1):
        """Seeded choice(s) from a sorted candidate list (first column), without replacement."""
        pool = sorted({r[0] for r in self.conn.execute(sql, params).fetchall()})
        if len(pool) < k:
            raise ValueError(f'only {len(pool)} candidates for pick({k}): {sql[:80]}')
        got = self.rng.sample(pool, k)
        return got[0] if k == 1 else got


class Q(dict):
    """One question. answer_type: count | value | share | patient_set | table | list | label | date | yes_no | absent | rubric | note."""

    def __init__(self, bucket, key_name, question, answer_type, *, sql=None, answer=None, follow_up_of=None, evidence=None,
                 notes='', **key):
        super().__init__(bucket=bucket, key_name=key_name, question=question, answer_type=answer_type, sql=sql, answer=answer,
                         follow_up_of=follow_up_of, evidence=evidence or [], notes=notes, key_extra=key)


def _jsonable(v):
    if isinstance(v, (set, frozenset)):
        return sorted(v)
    if isinstance(v, float):
        return round(v, 6)
    return v


def finish(q: Q, ctx: Ctx) -> dict:
    """Turn a Q into the questions.jsonl row (minus id/split). Computes the answer from the SQL unless given."""
    ans, sql = q['answer'], q['sql']
    extra = dict(q['key_extra'])
    t = q['answer_type']
    if ans is None and sql and t not in ('absent', 'rubric'):
        cols_rows = ctx.rows(sql)
        if t in ('count', 'value', 'date', 'label', 'yes_no'):
            ans = cols_rows[0][0] if cols_rows else None
            if t == 'yes_no' and ans is not None:
                ans = bool(ans)
        elif t == 'share':
            num, den = cols_rows[0][0], cols_rows[0][1]
            extra['numerator'], extra['denominator'] = num, den
            ans = (num / den) if den else None
        elif t == 'patient_set':
            ans = sorted(r[0] for r in cols_rows)
            extra['count'] = len(ans)
        elif t == 'list':
            ans = sorted({str(r[0]) for r in cols_rows if r[0] is not None})
        elif t == 'table':
            cur = ctx.conn.execute(sql)
            cols = [c[0] for c in cur.description]
            ans = [dict(zip(cols, [_jsonable(x) for x in r])) for r in cur.fetchall()]
            extra['columns'] = cols
    return {'bucket': q['bucket'], 'key_name': q['key_name'], 'question': q['question'], 'follow_up_of': q['follow_up_of'],
            'answer_type': t,
            'key': {'sql': sql, 'answer': _jsonable(ans), **{k: _jsonable(v) for k, v in extra.items()}},
            'evidence': q['evidence'], 'notes': q['notes']}


def assign_ids_and_splits(rows: list[dict], seed=SEED) -> list[dict]:
    """ids <bucket>-NN in build order; split: exactly one third of EACH bucket heldout, by seeded random. A follow-up always
    shares its parent's split (a chain is never half dev, half held out): chains are moved as units."""
    rng = random.Random(seed + 1)
    by_bucket: dict[str, list[dict]] = {b: [] for b in BUCKETS}
    for r in rows:
        by_bucket[r['bucket']].append(r)
    short = {b: f'b{i + 1}' for i, b in enumerate(BUCKETS)}
    key_to_id = {}
    for b, items in by_bucket.items():
        for i, r in enumerate(items, 1):
            r['id'] = f'{short[b]}-{i:02d}'
            key_to_id[r['key_name']] = r['id']
    for r in rows:
        if r['follow_up_of']:
            r['follow_up_of'] = key_to_id[r['follow_up_of']]
    for b, items in by_bucket.items():
        n_held = round(len(items) * HELDOUT_SHARE)
        parents = {r['id']: r for r in items}
        chains: dict[str, list[dict]] = {}
        for r in items:
            root = r
            while root['follow_up_of']:
                root = parents[root['follow_up_of']]
            chains.setdefault(root['id'], []).append(r)
        order = sorted(chains)
        rng.shuffle(order)
        held = 0
        # take whole chains in shuffled order while they fit; fill the remainder with single questions
        for root in order:
            size = len(chains[root])
            if held + size <= n_held:
                for r in chains[root]:
                    r['split'] = 'heldout'
                held += size
        for r in items:
            r.setdefault('split', 'dev')
        if held != n_held:
            raise RuntimeError(f'{b}: heldout {held} != {n_held}; adjust chain sizes')
    return rows


BUCKET_MODULES = ('b1_single_structured', 'b2_single_unstructured', 'b3_population_quality', 'b4_claims_pharmacy_sdoh', 'b5_insight')


def load_buckets():
    import importlib
    return [importlib.import_module(f'tools.dev_set.{m}') for m in BUCKET_MODULES]


def build(only=None) -> list[dict]:
    ctx = Ctx()
    rows = []
    for mod in load_buckets():
        if only and mod.__name__.split('.')[-1][:2] not in only:
            continue
        for q in mod.build(Ctx(ctx.conn, SEED + len(rows))):
            rows.append(finish(q, ctx))
    return assign_ids_and_splits(rows)


def write(rows: list[dict], path=QUESTIONS):
    with open(path, 'w') as fh:
        for r in rows:
            ordered = {k: r[k] for k in ('id', 'bucket', 'split', 'question', 'follow_up_of', 'answer_type', 'key', 'evidence', 'notes')}
            ordered['key_name'] = r['key_name']
            fh.write(json.dumps(ordered, ensure_ascii=False, sort_keys=False) + '\n')


def read(path=QUESTIONS) -> list[dict]:
    return [json.loads(line) for line in open(path) if line.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', action='store_true', help='recompute and compare with questions.jsonl; write nothing')
    args = ap.parse_args(argv)
    rows = build()
    if args.check:
        have = {r['id']: r for r in read()}
        bad = [r['id'] for r in rows if json.dumps(have.get(r['id'], {}).get('key'), sort_keys=True) != json.dumps(r['key'], sort_keys=True)]
        print(f'{len(rows)} questions recomputed; {len(bad)} keys differ: {bad[:10]}')
        return 1 if bad or len(have) != len(rows) else 0
    write(rows)
    from collections import Counter
    print(len(rows), 'questions written;', dict(Counter((r['bucket'], r['split']) for r in rows)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
