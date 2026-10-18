"""Ingest a raw native delivery folder into a landing SQLite database.

    ingest(raw_dir, landing_path) -> summary dict

Walks raw_dir, derives each file's context from its path (feed = first folder under raw/, then instance and drop
folders when present), hands the file to the first parser whose detect() accepts it, and writes every yielded row
into its landing table with lineage columns (_feed, _instance, _drop, _file, _row). Columns are added as new fields
appear, so files of the same kind with drifting headers union cleanly. ingest_files records every file (parser,
rows, size, mtime, error) and a rerun skips files already ingested unchanged. mtime is the local file's modification
time (when it was copied to disk), not when it was delivered; delivery dates come from the delivery manifest
(ingest/delivery.py builds delivery_file from ingest_files, the parse-issue tables and raw/manifest.json).
Nothing in raw_dir is modified.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import time

LINEAGE = ('_feed', '_instance', '_drop', '_file', '_row')
DROP = re.compile(r'^\d{4}-\d{2}(-\d{2})?$')
BATCH = 2000


def parsers():
    """Parser modules in detection order (most specific first). Missing modules are skipped."""
    import importlib
    out = []
    for name in ('x12', 'hl7', 'ncpdp', 'fhir', 'flat'):
        try:
            out.append(importlib.import_module(f'{__package__}.{name}'))
        except ImportError:
            continue
    return out


def context_for(path, raw_dir):
    rel = Path(path).relative_to(raw_dir)
    parts = rel.parts
    feed = parts[0] if len(parts) > 1 else None
    folders = list(parts[1:-1])
    drop = next((p for p in reversed(folders) if DROP.match(p)), None)
    instance = None
    if feed == 'ehr' and folders:  # ehr/<vendor>/<instance>/<drop>
        instance = '/'.join(f for f in folders[:2] if not DROP.match(f)) or None
    elif folders:
        instance = next((f for f in folders if not DROP.match(f)), None)
    return {'feed': feed, 'instance': instance, 'drop': drop, 'relpath': str(rel)}


def _ident(name):
    s = re.sub(r'[^A-Za-z0-9_]', '_', str(name)).strip('_').lower() or 'col'
    return ('c_' + s) if s[0].isdigit() else s


def _scalar(v):
    if v is None or isinstance(v, (str, int, float)):
        return v
    if isinstance(v, bool):
        return int(v)
    return json.dumps(v, default=str)


class Landing:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS ingest_files (relpath TEXT PRIMARY KEY, feed TEXT, parser TEXT,
            bytes INTEGER, mtime REAL, rows INTEGER, tables TEXT, error TEXT, seconds REAL, ingested_at TEXT)''')
        self.columns = {}
        for (t,) in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            self.columns[t] = [r[1] for r in self.db.execute(f'PRAGMA table_info("{t}")')]
        self.pending = {}

    def seen(self, relpath):
        return self.db.execute('SELECT 1 FROM ingest_files WHERE relpath = ?', (relpath,)).fetchone() is not None

    def done(self, relpath, size, mtime):
        r = self.db.execute('SELECT bytes, mtime, error FROM ingest_files WHERE relpath = ?', (relpath,)).fetchone()
        return r is not None and r[0] == size and abs((r[1] or 0) - mtime) < 1e-6 and r[2] is None

    def _ensure(self, table, keys):
        cols = self.columns.get(table)
        if cols is None:
            cols = list(LINEAGE)
            self.db.execute(f'CREATE TABLE "{table}" (' + ', '.join(f'"{c}"' for c in cols) + ')')
            self.columns[table] = cols
        for k in keys:
            if k not in cols:
                self.db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{k}"')
                cols.append(k)

    def add(self, table, row):
        self.pending.setdefault(table, []).append(row)
        if len(self.pending[table]) >= BATCH:
            self.flush(table)

    def flush(self, table=None):
        for t in ([table] if table else list(self.pending)):
            rows = self.pending.pop(t, [])
            if not rows:
                continue
            keys = []
            for r in rows:
                for k in r:
                    if k not in keys:
                        keys.append(k)
            self._ensure(t, keys)
            sql = f'INSERT INTO "{t}" (' + ', '.join(f'"{k}"' for k in keys) + ') VALUES (' + ', '.join('?' * len(keys)) + ')'
            self.db.executemany(sql, [tuple(r.get(k) for k in keys) for r in rows])

    def record(self, relpath, feed, parser, size, mtime, rows, tables, error, seconds):
        self.db.execute('INSERT OR REPLACE INTO ingest_files VALUES (?,?,?,?,?,?,?,?,?,datetime(\'now\'))',
                        (relpath, feed, parser, size, mtime, rows, json.dumps(tables), error, seconds))

    def remove_file_rows(self, relpath):
        for t, cols in self.columns.items():
            if '_file' in cols:
                self.db.execute(f'DELETE FROM "{t}" WHERE _file = ?', (relpath,))

    def commit(self):
        self.flush()
        self.db.commit()

    def close(self):
        self.commit()
        self.db.close()


def ingest(raw_dir, landing_path, *, progress=None, limit=None):
    raw_dir = Path(raw_dir).resolve()
    if not raw_dir.is_dir():
        raise FileNotFoundError(raw_dir)
    mods = parsers()
    land = Landing(landing_path)
    stats = {'files': 0, 'skipped': 0, 'unparsed': 0, 'errors': 0, 'rows': 0, 'by_parser': {}}
    started = time.time()
    try:
        files = sorted(p for p in raw_dir.rglob('*') if p.is_file() and not p.name.startswith('.')
                       and 'private' not in p.relative_to(raw_dir).parts)
        for i, path in enumerate(files):
            if limit is not None and stats['files'] >= limit:
                break
            ctx = context_for(path, raw_dir)
            st = path.stat()
            if land.done(ctx['relpath'], st.st_size, st.st_mtime):
                stats['skipped'] += 1
                continue
            mod = next((m for m in mods if _safe_detect(m, path, ctx)), None)
            if mod is None:
                land.record(ctx['relpath'], ctx['feed'], None, st.st_size, st.st_mtime, 0, {}, 'no parser', 0)
                stats['unparsed'] += 1
                continue
            if land.seen(ctx['relpath']):
                land.flush()
                land.remove_file_rows(ctx['relpath'])  # a changed or failed file replaces its earlier rows
            t0, n, tables, error = time.time(), 0, {}, None
            try:
                for table, row in mod.parse(path, ctx):
                    table = _ident(table)
                    out = {_ident(k): _scalar(v) for k, v in row.items()}
                    out.update(_feed=ctx['feed'], _instance=ctx['instance'], _drop=ctx['drop'], _file=ctx['relpath'], _row=n)
                    land.add(table, out)
                    tables[table] = tables.get(table, 0) + 1
                    n += 1
            except Exception as exc:  # noqa: BLE001 - a bad file is recorded, never fatal to the delivery
                error = f'{type(exc).__name__}: {exc}'[:500]
                stats['errors'] += 1
            name = mod.__name__.rsplit('.', 1)[-1]
            land.record(ctx['relpath'], ctx['feed'], name, st.st_size, st.st_mtime, n, tables, error, round(time.time() - t0, 3))
            stats['files'] += 1
            stats['rows'] += n
            stats['by_parser'][name] = stats['by_parser'].get(name, 0) + 1
            if stats['files'] % 500 == 0:
                land.commit()
                if progress:
                    progress(i + 1, len(files), stats)
        land.commit()
        stats['tables'] = {t: land.db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                           for t in sorted(land.columns) if t != 'ingest_files'}
        stats['seconds'] = round(time.time() - started, 1)
        return stats
    finally:
        land.close()


def _safe_detect(mod, path, ctx):
    try:
        return bool(mod.detect(path)) if mod.detect.__code__.co_argcount < 2 else bool(mod.detect(path, ctx))
    except Exception:  # noqa: BLE001
        return False
