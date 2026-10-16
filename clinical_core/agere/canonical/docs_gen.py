"""LLM-facing documentation generated from a built canonical database.

Mirrors clinical_core/agere/clinical/semantic_docs.py (re-exported as bench/semantic_context.py): the text a customer would publish as a semantic model for a text-to-SQL assistant.
Sections (all derived, never hand-written per build):

- snapshot line (meta: version, release, as_of);
- tables with exact columns, column descriptions and row counts. Descriptions come from, in order: a dbt
  `target/manifest.json` (the Tuva Project's model and column docs), `versions.TABLE_DOCS`, and an optional JSON file
  shaped {table: {"description": str, "columns": {col: str}}};
- the version-resolution rules (versions.RULES);
- value sets (config/agere/canonical/value_sets.json) with citations;
- which raw streams feed each table (config/agere/canonical/stream_map.json);
- verified examples (config/agere/canonical/examples.json): each example's SQL is executed against THIS database and
  included only when its result equals the recorded expected answer. Examples sourced from heldout sets or
  bench/capability are rejected outright.

    python -m clinical_core.agere.canonical.docs_gen --database <canonical db> [--dbt-manifest m.json] [--out docs.md]
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sqlite3

from clinical_core.agere.canonical import CONFIG, versions

EXAMPLES_PATH = CONFIG / 'examples.json'
VALUE_SETS_PATH = CONFIG / 'value_sets.json'
STREAM_MAP_PATH = CONFIG / 'stream_map.json'
FORBIDDEN_SOURCES = ('heldout', 'bench/capability', 'capability')


def dbt_docs(manifest_path):
    """{table: {description, columns}} from a dbt manifest (models and seeds), keyed by alias (the relation name)."""
    manifest = json.loads(Path(manifest_path).read_text())
    out = {}
    for node in manifest.get('nodes', {}).values():
        if node.get('resource_type') not in ('model', 'seed', 'snapshot'):
            continue
        name = node.get('alias') or node.get('name')
        out[name] = {'description': node.get('description') or '',
                     'columns': {c: (v or {}).get('description') or '' for c, v in (node.get('columns') or {}).items()}}
    return out


def merge_docs(*sources):
    out = {}
    for src in sources:
        for table, doc in (src or {}).items():
            cur = out.setdefault(table, {'description': '', 'columns': {}})
            if doc.get('description') and not cur['description']:
                cur['description'] = doc['description']
            for col, desc in (doc.get('columns') or {}).items():
                if desc and not cur['columns'].get(col):
                    cur['columns'][col] = desc
    return out


def load_examples(path=EXAMPLES_PATH):
    data = json.loads(Path(path).read_text())
    for e in data['examples']:
        source = str(e.get('source', '')).lower()
        if not source or any(f in source for f in FORBIDDEN_SOURCES):
            raise ValueError(f"Example {e.get('id')} must cite a non-heldout, non-capability source")
    return data['examples']


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=0.005)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_same(a[k], b[k]) for k in a)
    return a == b


def verify_examples(examples, query):
    """(verified, rejected) where query(sql) -> list of row dicts. Scalar expectations compare the single cell."""
    verified, rejected = [], []
    for e in examples:
        try:
            rows = query(e['sql'])
            if isinstance(e['expected'], list):
                got = rows
            else:
                got = next(iter(rows[0].values())) if len(rows) == 1 and len(rows[0]) == 1 else rows
            if _same(got, e['expected']):
                verified.append(e)
            else:
                rejected.append({**e, 'got': got})
        except Exception as exc:  # a failing example is never shown to the model
            rejected.append({**e, 'error': f'{type(exc).__name__}: {exc}'})
    return verified, rejected


def _tables(columns, docs, counts):
    lines = ['## Tables. Columns are exact; descriptions are the model documentation.']
    for name in sorted(columns):
        doc = docs.get(name, {})
        lines.append(f"### {name} ({counts.get(name, '?')} rows)" + (f": {doc['description']}" if doc.get('description') else ''))
        for col in columns[name]:
            desc = doc.get('columns', {}).get(col)
            lines.append(f'- {col}' + (f': {desc}' if desc else ''))
    return '\n'.join(lines)


def _rules():
    return '\n'.join(['## Version-resolution rules (applied before any table is built)'] +
                     [f'- {k}: {v}' for k, v in versions.RULES])


def _value_sets(value_sets):
    out = ['## Value sets (config/agere/canonical/value_sets.json; codes by system, with citations)']
    for name, fam in value_sets.get('families', {}).items():
        codes = fam.get('codes') or fam.get('families') or {}
        out.append(f"- {name}: {fam.get('label', '')}; " + '; '.join(
            f"{k} {','.join(v) if isinstance(v, list) else v}" for k, v in codes.items())
            + (f"; retired {json.dumps(fam['retired'])}" if fam.get('retired') else '')
            + f" [sources: {' | '.join(fam.get('citations', []))}]")
    return '\n'.join(out)


def _stream_feeds(stream_map, tables):
    feeds = {}
    for s, entry in stream_map.get('streams', {}).items():
        for t in entry.get('tables', []):
            feeds.setdefault(t, []).append(s)
    out = ['## Raw stream lineage (which received streams feed each canonical table; Tuva input target in brackets)']
    for t, meta in stream_map.get('tables', {}).items():
        built = ' (in this database)' if t in tables else ''
        out.append(f"- {t}{built} [{meta.get('tuva') or '-'}]: {', '.join(sorted(feeds.get(t, []))) or ('all streams' if meta.get('all_streams') else '-')}")
    unused = {s: e['unused'] for s, e in stream_map.get('streams', {}).items() if 'unused' in e}
    if unused:
        out.append('- unused streams: ' + '; '.join(f'{s} ({r})' for s, r in sorted(unused.items())))
    return '\n'.join(out)


def _examples(examples):
    out = ['## Verified example queries (each executed against this database and matched to an independently '
           'computed answer). Reuse their patterns; adapt definitions to what each new question asks.']
    for e in examples:
        out.append(f"### Q: {e['question']}\nanswer_type: {e.get('answer_type', 'value')}"
                   + (f"\nnote: {e['note']}" if e.get('note') else '') + f"\nSQL:\n{e['sql']}")
    return '\n'.join(out)


def build_docs(columns, query, meta, *, extra_docs=None, dbt_manifest=None, examples_path=EXAMPLES_PATH,
               value_sets_path=VALUE_SETS_PATH, stream_map_path=STREAM_MAP_PATH):
    """(documentation text, verified example ids, rejected examples). columns: {table: [cols]}; query(sql) -> rows."""
    docs = merge_docs(dbt_docs(dbt_manifest) if dbt_manifest else None, versions.TABLE_DOCS, extra_docs)
    counts = {}
    for t in columns:
        try:
            counts[t] = query(f'SELECT COUNT(*) AS n FROM "{t}"')[0]['n']
        except Exception:
            counts[t] = '?'
    examples = load_examples(examples_path) if examples_path and Path(examples_path).is_file() else []
    verified, rejected = verify_examples(examples, query)
    text = '\n\n'.join([
        '# Canonical model documentation',
        f"Snapshot: canonical {meta.get('version')} built from release {meta.get('release')} as of {meta.get('as_of')}.",
        _tables(columns, docs, counts),
        _rules(),
        _value_sets(json.loads(Path(value_sets_path).read_text())),
        _stream_feeds(json.loads(Path(stream_map_path).read_text()), set(columns)),
        _examples(verified),
    ])
    return text, frozenset(e['id'] for e in verified), rejected


def open_database(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                                      "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    columns = {n: [r[1] for r in db.execute(f'PRAGMA table_info("{n}")')] for n in names}
    meta = {}
    if 'meta' in columns:
        meta = {k: v for k, v in db.execute('SELECT key, value FROM meta') if k != 'receipt'}
    return db, columns, meta


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--dbt-manifest', type=Path)
    parser.add_argument('--docs', type=Path, help='extra JSON table/column docs')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    db, columns, meta = open_database(args.database)
    try:
        text, ids, rejected = build_docs(columns, lambda sql: [dict(r) for r in db.execute(sql)], meta,
                                         extra_docs=json.loads(args.docs.read_text()) if args.docs else None,
                                         dbt_manifest=args.dbt_manifest)
    finally:
        db.close()
    if args.out:
        args.out.write_text(text + '\n')
    else:
        print(text)
    print(json.dumps({'chars': len(text), 'verified_examples': sorted(ids),
                      'rejected_examples': [{k: e.get(k) for k in ('id', 'got', 'error')} for e in rejected]}),
          file=__import__('sys').stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
