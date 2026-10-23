"""Quarantine what benchmark runs taught the stack.

A benchmark that went through the normal compose route left rows in the stack's learning stores and lines in the training
traffic files (verified pairs, outcomes, routing, saved plans, ...). This tool finds them by TIME WINDOW (and, for stores
without a timestamp, by the hash of the question the stack's own answer log recorded inside the window) and moves them out
of the live stores into a sibling quarantine database. It never deletes: --write copies the touched files first and moves
rows into <stack>/quarantine-<ts>.sqlite3; --restore puts them back.

  python tools/eval_quarantine.py --stack artifacts/<build>/stack --from-manifests bench/results/truth          # dry run
  python tools/eval_quarantine.py --stack S --since 2026-10-22T20:00 --until 2026-10-23T14:00                    # one window
  python tools/eval_quarantine.py --stack S --from-manifests bench/results/truth --write                        # move rows
  python tools/eval_quarantine.py --stack S --restore S/quarantine-20261023T150000Z.sqlite3                      # undo

Output is table names and counts only; no question text is ever selected for display. Dry run is the default. Times are
epoch seconds or ISO-8601 (without a zone = local time). --from-manifests reads only created_at / finished_at from each
run's manifest.json (a run without finished_at ends at the last modification of its rows/partial file) plus --grace-seconds
for background work. Stores: learning.sqlite3 (verified, outcomes, routing, quarantined, corrections, procedure_events,
procedure_uses), vcache.sqlite3, answer-stability.sqlite3, terminology-map.sqlite3, question-plans.sqlite3 (shared_requests,
request_shapes, analysis_shapes), and the training traffic files (<training dir>/traffic/*.jsonl); answers.sqlite3 is the
answer LOG and is only touched with --include-logs. The semantic cache and the learned-SQL / paraphrase reuse are read from
these tables, so moving the rows removes them from reuse.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (file, table, how the time is read): 'col:<name>' real epoch column, 'iso:<name>' local ISO text column,
# 'json:<key>' epoch inside the payload column, 'sha:<json path>' no time: match question hash from the window's answer log
TARGETS = [
    ('learning.sqlite3', 'verified', 'col:created'),
    ('learning.sqlite3', 'outcomes', 'col:created'),
    ('learning.sqlite3', 'routing', 'col:created'),
    ('learning.sqlite3', 'quarantined', 'col:created'),
    ('learning.sqlite3', 'corrections', 'col:created'),
    ('learning.sqlite3', 'procedure_events', 'col:created'),
    ('learning.sqlite3', 'procedure_uses', 'json:created'),
    ('vcache.sqlite3', 'reuse', 'col:created'),
    ('vcache.sqlite3', 'observation', 'col:created'),
    ('answer-stability.sqlite3', 'stability', 'col:created'),
    ('terminology-map.sqlite3', 'mapping', 'iso:created'),
    ('question-plans.sqlite3', 'shared_requests', 'sha:question'),
    ('question-plans.sqlite3', 'request_shapes', 'json:created'),
    ('question-plans.sqlite3', 'analysis_shapes', 'sha:question'),
]
LOG_TARGETS = [('answers.sqlite3', 'answers', 'col:at'), ('answers.sqlite3', 'answer_costs', 'col:at')]
TRAFFIC_TIME_KEY = 'at'


# -- time windows ------------------------------------------------------------------------------------------------------
def parse_time(text):
    text = str(text).strip()
    try:
        return float(text)
    except ValueError:
        pass
    when = datetime.fromisoformat(text.replace('Z', '+00:00'))
    return when.timestamp()  # a naive time is local (datetime.timestamp does that)


def iso_local(text):
    try:
        return datetime.fromisoformat(str(text)).timestamp()
    except (ValueError, TypeError):
        return None


def manifest_windows(root, grace=300.0):
    """[(start, end, run_name)] from manifest.json files under root. Only created_at / finished_at are read."""
    out, skipped = [], 0
    for path in sorted(Path(root).rglob('manifest.json')):
        try:
            m = json.loads(path.read_text())
            start = datetime.fromisoformat(str(m['created_at']).replace('Z', '+00:00')).timestamp()
        except (OSError, ValueError, KeyError, TypeError):
            skipped += 1
            continue
        end = None
        if m.get('finished_at'):
            try:
                end = datetime.fromisoformat(str(m['finished_at']).replace('Z', '+00:00')).timestamp()
            except ValueError:
                end = None
        if end is None:  # a run from before finished_at existed: the last time it wrote anything
            times = [(path.parent / n).stat().st_mtime for n in ('rows.jsonl', 'partial.jsonl', 'cost.json', 'score.txt')
                     if (path.parent / n).is_file()]
            end = max(times) if times else start
        out.append((start, max(end, start) + grace, path.parent.name))
    return out, skipped


def merge(windows):
    spans = sorted((a, b) for a, b, *_ in windows)
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def in_window(t, windows):
    return t is not None and any(a <= t <= b for a, b in windows)


# -- reading -----------------------------------------------------------------------------------------------------------
def ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30)


def sha(text):
    return hashlib.sha256(str(text).encode()).hexdigest()


def window_question_hashes(stack, windows):
    """sha256 of every question the stack's own answer log recorded inside a window (hashes only; no text is read out)."""
    path = Path(stack) / 'answers.sqlite3'
    if not path.is_file():
        return set()
    db = ro(path)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='answers'").fetchone():
            return set()
        return {h for at, h in db.execute('SELECT at, question_sha FROM answers') if in_window(at, windows) and h}
    finally:
        db.close()


def matching_rowids(db, table, how, windows, hashes):
    kind, _, key = how.partition(':')
    ids = []
    if kind == 'col':
        for rowid, t in db.execute(f'SELECT rowid, "{key}" FROM "{table}"'):
            if in_window(t if isinstance(t, (int, float)) else None, windows):
                ids.append(rowid)
    elif kind == 'iso':
        for rowid, t in db.execute(f'SELECT rowid, "{key}" FROM "{table}"'):
            if in_window(iso_local(t), windows):
                ids.append(rowid)
    elif kind == 'json':
        for rowid, payload in db.execute(f'SELECT rowid, payload FROM "{table}"'):
            try:
                t = json.loads(payload).get(key)
            except (ValueError, AttributeError):
                continue
            if in_window(t if isinstance(t, (int, float)) else None, windows):
                ids.append(rowid)
    elif kind == 'sha':
        for rowid, payload in db.execute(f'SELECT rowid, payload FROM "{table}"'):
            try:
                item = json.loads(payload)
            except ValueError:
                continue
            q = item.get(key) or item.get('original_question')
            if q and sha(q) in hashes:
                ids.append(rowid)
    return ids


def survey(stack, windows, include_logs=False):
    """[(file, table, matching_rowids, total_rows)] for every store table present in the stack."""
    stack = Path(stack)
    hashes = window_question_hashes(stack, windows)
    found = []
    for name, table, how in TARGETS + (LOG_TARGETS if include_logs else []):
        path = stack / name
        if not path.is_file():
            continue
        db = ro(path)
        try:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            total = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            found.append((name, table, matching_rowids(db, table, how, windows, hashes), total))
        finally:
            db.close()
    return found


def traffic_files(training_dir):
    d = Path(training_dir) / 'traffic'
    return sorted(d.glob('*.jsonl')) if d.is_dir() else []


def traffic_survey(training_dir, windows):
    """[(path, matching line indexes, total lines)]"""
    out = []
    for path in traffic_files(training_dir):
        idx, total = [], 0
        with path.open() as f:
            for i, line in enumerate(f):
                if not line.strip():
                    continue
                total += 1
                try:
                    t = iso_local(json.loads(line).get(TRAFFIC_TIME_KEY))
                except (ValueError, AttributeError):
                    t = None
                if in_window(t, windows):
                    idx.append(i)
        out.append((path, idx, total))
    return out


# -- writing -----------------------------------------------------------------------------------------------------------
def backup_dir(stack, stamp):
    return Path(stack) / f'quarantine-{stamp}-backup'


def backup(src, dest_dir):
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(src).name
    if str(src).endswith('.sqlite3'):
        a, b = sqlite3.connect(f'file:{src}?mode=ro', uri=True), sqlite3.connect(dest)
        try:
            a.backup(b)
        finally:
            a.close(); b.close()
    else:
        shutil.copy2(src, dest)
    return dest


def quarantine_db(stack, stamp):
    path = Path(stack) / f'quarantine-{stamp}.sqlite3'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS _log(id INTEGER PRIMARY KEY, store TEXT, tbl TEXT, rows INTEGER, kind TEXT, '
               'source TEXT, restored INTEGER DEFAULT 0, at REAL)')
    return path, db


def move_rows(stack, qdb, name, table, rowids):
    """Copy rows (with their rowid) into the quarantine database, then delete them from the live table."""
    if not rowids:
        return 0
    live = sqlite3.connect(Path(stack) / name, timeout=30)
    try:
        live.execute('CREATE TEMP TABLE _ids(i INTEGER PRIMARY KEY)')
        live.executemany('INSERT INTO _ids VALUES (?)', [(i,) for i in rowids])
        cols = [r[1] for r in live.execute(f'PRAGMA table_info("{table}")')]
        quoted = ','.join(f'"{c}"' for c in cols)
        target = f'{name}__{table}'
        qdb.execute(f'CREATE TABLE IF NOT EXISTS "{target}"(_rowid_ INTEGER, {quoted})')
        rows = live.execute(f'SELECT rowid, {quoted} FROM "{table}" WHERE rowid IN (SELECT i FROM _ids)').fetchall()
        qdb.executemany(f'INSERT INTO "{target}" VALUES ({",".join("?" * (len(cols) + 1))})', rows)
        qdb.execute('INSERT INTO _log(store, tbl, rows, kind, source, at) VALUES (?,?,?,?,?,?)',
                    (name, table, len(rows), 'sqlite', name, time.time()))
        qdb.commit()  # the quarantine copy is durable before anything is removed from the live store
        with live:
            live.execute(f'DELETE FROM "{table}" WHERE rowid IN (SELECT i FROM _ids)')
        return len(rows)
    finally:
        live.close()


def move_lines(qdb, path, indexes):
    if not indexes:
        return 0
    wanted, kept, moved = set(indexes), [], []
    with path.open() as f:
        for i, line in enumerate(f):
            (moved if i in wanted else kept).append((i, line))
    target = f'traffic__{path.name}'
    qdb.execute(f'CREATE TABLE IF NOT EXISTS "{target}"(_rowid_ INTEGER, line TEXT)')
    qdb.executemany(f'INSERT INTO "{target}" VALUES (?,?)', moved)
    qdb.execute('INSERT INTO _log(store, tbl, rows, kind, source, at) VALUES (?,?,?,?,?,?)',
                ('traffic', path.name, len(moved), 'jsonl', str(path), time.time()))
    qdb.commit()
    tmp = path.with_suffix('.jsonl.tmp')
    tmp.write_text(''.join(line for _, line in kept))
    os.replace(tmp, path)
    return len(moved)


def restore(stack, qfile):
    """Put every not-yet-restored quarantined row back where it came from. -> {store.table: rows}"""
    out = {}
    qdb = sqlite3.connect(qfile)
    try:
        for lid, name, table, kind, source in qdb.execute('SELECT id, store, tbl, kind, source FROM _log WHERE restored=0').fetchall():
            if kind == 'sqlite':
                target = f'{name}__{table}'
                cols = [r[1] for r in qdb.execute(f'PRAGMA table_info("{target}")') if r[1] != '_rowid_']
                quoted = ','.join(f'"{c}"' for c in cols)
                live = sqlite3.connect(Path(stack) / name, timeout=30)
                try:
                    with live:
                        rows = qdb.execute(f'SELECT _rowid_, {quoted} FROM "{target}"').fetchall()
                        live.executemany(f'INSERT OR IGNORE INTO "{table}"(rowid,{quoted}) VALUES ({",".join("?" * (len(cols) + 1))})', rows)
                finally:
                    live.close()
                out[f'{name}.{table}'] = len(rows)
            else:
                path = Path(source)
                lines = [r[0] for r in qdb.execute(f'SELECT line FROM "traffic__{table}" ORDER BY _rowid_')]
                with path.open('a') as f:
                    f.write(''.join(lines))
                out[f'traffic.{table}'] = len(lines)
            qdb.execute('UPDATE _log SET restored=1 WHERE id=?', (lid,))
        qdb.commit()
    finally:
        qdb.close()
    return out


# -- command line ------------------------------------------------------------------------------------------------------
def default_training_dir():
    return Path(os.environ.get('AGERE_TRAINING_DIR') or ROOT / 'artifacts' / 'training')


def run(args, out=print):
    stack = Path(args.stack)
    if args.restore:
        done = restore(stack, args.restore)
        for k, n in sorted(done.items()):
            out(f'restored {k}: {n}')
        out('nothing to restore' if not done else 'restore complete')
        return 0
    windows = []
    if args.since is not None or args.until is not None:
        a = parse_time(args.since) if args.since is not None else 0.0
        b = parse_time(args.until) if args.until is not None else time.time()
        windows.append((a, b))
    for root in args.from_manifests or []:
        found, skipped = manifest_windows(root, args.grace_seconds)
        windows += found
        out(f'manifests under {root}: {len(found)} runs' + (f', {skipped} unreadable' if skipped else ''))
    if not windows:
        out('no time window: give --since/--until and/or --from-manifests')
        return 2
    spans = merge(windows)
    out(f'windows: {len(spans)} merged ({datetime.fromtimestamp(spans[0][0], timezone.utc):%Y-%m-%d %H:%M}Z .. '
        f'{datetime.fromtimestamp(spans[-1][1], timezone.utc):%Y-%m-%d %H:%M}Z)')
    found = survey(stack, spans, args.include_logs)
    traffic = traffic_survey(args.training_dir or default_training_dir(), spans)
    out(('WRITE' if args.write else 'DRY RUN') + f' stack={stack}')
    for name, table, ids, total in found:
        out(f'  {name}.{table}: {len(ids)} of {total} rows in window')
    for path, idx, total in traffic:
        out(f'  traffic/{path.name}: {len(idx)} of {total} lines in window')
    grand = sum(len(i) for _, _, i, _ in found) + sum(len(i) for _, i, _ in traffic)
    out(f'total rows/lines in window: {grand}')
    if not args.write:
        out('dry run only; add --write to move them into quarantine (originals are copied first; --restore undoes it)')
        return 0
    if not grand:
        out('nothing to move')
        return 0
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    backups = backup_dir(stack, stamp)
    touched = sorted({name for name, _, ids, _ in found if ids})
    for name in touched:
        backup(stack / name, backups)
    for path, idx, _ in traffic:
        if idx:
            backup(path, backups / 'traffic')
    qpath, qdb = quarantine_db(stack, stamp)
    try:
        for name, table, ids, _ in found:
            n = move_rows(stack, qdb, name, table, ids)
            if n:
                out(f'  moved {name}.{table}: {n}')
        for path, idx, _ in traffic:
            n = move_lines(qdb, path, idx)
            if n:
                out(f'  moved traffic/{path.name}: {n}')
    finally:
        qdb.close()
    out(f'quarantine: {qpath}')
    out(f'backups: {backups}')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--stack', required=True, type=Path)
    p.add_argument('--since', help='window start (epoch or ISO-8601; no zone = local)')
    p.add_argument('--until', help='window end (default: now when --since is given)')
    p.add_argument('--from-manifests', action='append', type=Path, metavar='DIR',
                   help='windows from every manifest.json under DIR (created_at .. finished_at/last write + grace); repeatable')
    p.add_argument('--grace-seconds', type=float, default=300.0, help='added after each run for background work (default 300)')
    p.add_argument('--training-dir', type=Path, help='training dir holding traffic/ (default AGERE_TRAINING_DIR or artifacts/training)')
    p.add_argument('--include-logs', action='store_true', help='also move answers.sqlite3 answer-log rows (cost history) in the window')
    p.add_argument('--write', action='store_true', help='move the rows (default: dry run, counts only)')
    p.add_argument('--restore', type=Path, metavar='QUARANTINE_DB', help='put the rows of a quarantine database back')
    args = p.parse_args(argv)
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
