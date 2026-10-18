"""Make streamed flat-file ingest identical to a full ingest of the same files.

ingest/flat.py keeps a process-lifetime header registry per landing table: the first file it parses becomes the baseline,
and a later file whose header differs (or is spelled differently) gets a flat_schema_drift row. That row is yielded
before the file's data rows, so it also shifts their _row numbers, and _row is part of every canonical record's lineage
(record ids are a hash of it). The result therefore depends on which files one process parsed, in which order.

A full build parses every file in one process in path order. Streaming parses files as they arrive. To give the same
landing (and so the same record ids), streaming replays the registry in full-build order: for each flat landing table
it walks every delivered file of that table in path order, computes the drift row each file gets in a full build, presets
the registry to that point before parsing a new file, and re-parses an earlier file whose drift row changed (a late
file that sorts before the old baseline, or a new spelling variant that now appears first).

Header facts (table, cleaned columns, raw header) are cached per (relpath, size, mtime) in the streaming state.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sqlite3

DRIFT_FIELDS = ('landing_table', 'relpath', 'issue', 'added_columns', 'removed_columns', 'header_signature', 'n_columns',
                'baseline_n_columns', 'baseline_file', 'raw_header')
CACHE_DDL = 'CREATE TABLE IF NOT EXISTS flat_header(relpath TEXT PRIMARY KEY, size INTEGER, mtime REAL, info TEXT)'


def _order(rel):
    return Path(rel).parts


def header_info(path, ctx):
    """(table, cols, raw_header, rel) as flat.parse sees the file, or None when it yields no drift check."""
    from ..ingest import flat
    info = flat.classify(path, ctx)
    if info is None:
        return None
    raw = Path(path).read_bytes()
    if not raw.strip():
        return None
    text = flat._decode(raw)
    delim = flat.sniff_delimiter(text)
    if delim is None:
        return None
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=delim, quotechar='"')
    for rec in reader:
        if not rec or all(not x.strip() for x in rec):
            continue
        cols = flat._clean_header(rec, info['split_camel'])
        return info['table'], cols, rec, '/'.join((info['feed'],) + tuple(info['rest']))
    return None


def plan(raw_dir, landing_path, new_relpaths, state_path):
    """-> (presets {relpath: (table, baseline or None, variants)}, reparse [relpaths], stats). new_relpaths: files about
    to be ingested; flat files among them and every flat file already in landing take part in the replay."""
    from ..ingest import flat, landing
    raw_dir = Path(raw_dir).resolve()
    land = sqlite3.connect(f'file:{landing_path}?mode=ro', uri=True)
    st = sqlite3.connect(state_path)
    st.execute(CACHE_DDL)
    try:
        have = {r[0] for r in land.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        old_flat = [r[0] for r in land.execute("SELECT relpath FROM ingest_files WHERE parser = 'flat'")] if 'ingest_files' in have else []
        mods = landing.parsers()
        new_flat = []
        for rel in new_relpaths:
            p = raw_dir / rel
            if not p.is_file():
                continue
            ctx = landing.context_for(p, raw_dir)
            mod = next((m for m in mods if landing._safe_detect(m, p, ctx)), None)
            if mod is not None and mod.__name__.endswith('.flat'):
                new_flat.append(ctx['relpath'])
        cache = {r[0]: (r[1], r[2], r[3]) for r in st.execute('SELECT relpath, size, mtime, info FROM flat_header')}
        infos, fresh = {}, []
        for rel in set(old_flat) | set(new_flat):
            p = raw_dir / rel
            if not p.is_file():
                continue
            s = p.stat()
            c = cache.get(rel)
            if c and c[0] == s.st_size and abs(c[1] - s.st_mtime) < 1e-6:
                infos[rel] = json.loads(c[2])
            else:
                hi = header_info(p, landing.context_for(p, raw_dir))
                infos[rel] = list(hi) if hi else None
                fresh.append((rel, s.st_size, s.st_mtime, json.dumps(infos[rel])))
        with st:
            st.executemany('INSERT OR REPLACE INTO flat_header VALUES (?,?,?,?)', fresh)
        by_table = {}
        for rel, hi in infos.items():
            if hi:
                by_table.setdefault(hi[0], []).append(rel)
        saved = (flat._BASELINE, flat._RAW_VARIANTS)
        expected, presets = {}, {}
        try:
            for table, rels in by_table.items():
                flat._BASELINE, flat._RAW_VARIANTS = {}, {}
                for rel in sorted(rels, key=_order):
                    _, cols, raw_header, drift_rel = infos[rel]
                    base = flat._BASELINE.get(table)
                    presets[rel] = (table, [list(base[0]), base[1]] if base else None, sorted(flat._RAW_VARIANTS.get(table, ())))
                    expected[rel] = [r for _, r in flat._drift_rows(table, cols, raw_header, drift_rel)]
        finally:
            flat._BASELINE, flat._RAW_VARIANTS = saved
        reparse = []
        new_set = set(new_flat)
        if 'flat_schema_drift' in have:
            cols = [r[1] for r in land.execute('PRAGMA table_info(flat_schema_drift)')]
            actual = {}
            for row in land.execute('SELECT * FROM flat_schema_drift'):
                d = dict(zip(cols, row))
                actual.setdefault(d['_file'], []).append({k: d.get(k) for k in DRIFT_FIELDS})
        else:
            actual = {}
        for rel in old_flat:
            if rel in new_set or rel not in expected:
                continue
            want = [{k: r.get(k) for k in DRIFT_FIELDS} for r in expected[rel]]
            if want != actual.get(rel, []):
                reparse.append(rel)
        return presets, sorted(reparse, key=_order), {'flat_files': len(infos), 'headers_read': len(fresh), 'reparse': len(reparse)}
    finally:
        land.close()
        st.close()


def preset_hook(presets):
    """before_file callback for landing.ingest_paths: put the registry where a full build would have it."""
    from ..ingest import flat

    def hook(relpath):
        p = presets.get(relpath)
        if not p:
            return
        table, base, variants = p
        if base is None:
            flat._BASELINE.pop(table, None)
            flat._RAW_VARIANTS.pop(table, None)
        else:
            flat._BASELINE[table] = (base[0], base[1])
            flat._RAW_VARIANTS[table] = set(variants)
    return hook
