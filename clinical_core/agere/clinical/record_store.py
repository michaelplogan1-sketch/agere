"""Stream-clustered copy of the received records, plus an in-process decoded-record memo.

Population operators (cohort selection, explain, aggregate) read every record of a
few streams. In `stack.sqlite3` those rows are scattered across a ~10 GB file that
also holds passages and FTS indexes, so `WHERE stream IN (...)` turns into tens of
thousands of random B-tree seeks (~6 s for the encounter stream). This module keeps
an exact byte copy of `records(id, patient, stream, payload)` in
`stream-records.sqlite3`, physically ordered by (stream, patient, source rowid), so a
stream is one sequential rowid range (~0.1 s).

Exactness: payloads are copied verbatim and every reader gets per-patient lists in
the order the original query produced them (`order='stream'`: stream then source
rowid, the order of the original index scan; `order='id'`: record id, the original
`ORDER BY id`). Freshness: building installs triggers on `stack.sqlite3` that bump
`agere_record_version` on any insert/update/delete of `records`; the copy is used
only while its recorded version and source digest still match, otherwise readers
fall back to the original query. Nothing here changes what is computed.

Decoded records are memoised per (store, version, stream) within one process, bounded
by AGERE_RECORD_MEMO_MB of source JSON (default 512; 0 disables). Callers receive
fresh per-patient lists but shared record dicts: treat records as read-only.
"""
from collections import OrderedDict, defaultdict
from pathlib import Path
import json, os, sqlite3, threading, time

VERSION = 'stream-records/1'
FILE = 'stream-records.sqlite3'
TRIGGERS = """
CREATE TABLE IF NOT EXISTS agere_record_version(id INTEGER PRIMARY KEY CHECK(id = 1), n INTEGER NOT NULL);
INSERT OR IGNORE INTO agere_record_version VALUES (1, 0);
CREATE TRIGGER IF NOT EXISTS agere_record_version_insert AFTER INSERT ON records BEGIN UPDATE agere_record_version SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER IF NOT EXISTS agere_record_version_update AFTER UPDATE ON records BEGIN UPDATE agere_record_version SET n = n + 1 WHERE id = 1; END;
CREATE TRIGGER IF NOT EXISTS agere_record_version_delete AFTER DELETE ON records BEGIN UPDATE agere_record_version SET n = n + 1 WHERE id = 1; END;
"""

_LOCK = threading.Lock()
_MEMO = OrderedDict()  # (store path, token, stream) -> (bytes, {pid: [(rowid, id, record)]})
_MEMO_BYTES = [0]
_STATS = {'memo_hits': 0, 'memo_misses': 0, 'store_reads': 0, 'fallback_reads': 0}


def _source_digest(path):
    return json.loads((Path(path) / 'run.json').read_text())['source_digest']


def _record_version(stack_db):
    try:
        row = stack_db.execute('SELECT n FROM agere_record_version WHERE id = 1').fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


# Covering indexes for scan-heavy semantic-layer queries (same rows; index-only instead of payload-page scans).
SEMANTIC_INDEXES = {
    # monitor.feed_checks: records per stream x source x event month (~11 s full scan -> ~1 s).
    'record_feed': 'CREATE INDEX IF NOT EXISTS record_feed ON record(event_date, stream, organization_id, patient_id)',
}


def index_semantic(path):
    target = Path(path) / 'semantic.sqlite3'
    if not target.exists(): return []
    db = sqlite3.connect(target, timeout=60)
    try:
        for ddl in SEMANTIC_INDEXES.values(): db.execute(ddl)
        db.commit()
    finally:
        db.close()
    return sorted(SEMANTIC_INDEXES)


def build(path):
    """Write the clustered copy (exact payload bytes), install the change counter on stack.sqlite3 and the
    semantic-layer covering indexes."""
    path = Path(path)
    started = time.perf_counter()
    index_semantic(path)
    stack = sqlite3.connect(path / 'stack.sqlite3', timeout=60)
    try:
        stack.executescript(TRIGGERS)
        stack.commit()
        for _ in range(5):
            before = _record_version(stack)
            tmp = path / (FILE + '.tmp')
            tmp.unlink(missing_ok=True)
            out = sqlite3.connect(tmp)
            try:
                out.execute('PRAGMA journal_mode=OFF'); out.execute('PRAGMA synchronous=OFF')
                out.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);'
                                  'CREATE TABLE rec(stream TEXT, patient TEXT, src_rowid INTEGER, id TEXT, payload TEXT);'
                                  'CREATE TABLE ranges(stream TEXT PRIMARY KEY, lo INTEGER, hi INTEGER, rows INTEGER, bytes INTEGER);')
                streams = [r[0] for r in stack.execute('SELECT DISTINCT stream FROM records ORDER BY stream')]
                for stream in streams:
                    out.executemany('INSERT INTO rec VALUES (?,?,?,?,?)', stack.execute(
                        'SELECT stream, patient, rowid, id, payload FROM records WHERE stream = ? ORDER BY patient, rowid', (stream,)))
                out.execute('INSERT INTO ranges SELECT stream, min(rowid), max(rowid), count(*), sum(length(payload)) FROM rec GROUP BY stream')
                after = _record_version(stack)
                out.executemany('INSERT INTO meta VALUES (?,?)', [('version', VERSION), ('source_digest', _source_digest(path)),
                                                                 ('record_version', str(after))])
                out.commit()
            finally:
                out.close()
            if before == after:
                tmp.replace(path / FILE)
                return {'path': str(path / FILE), 'record_version': after, 'elapsed_seconds': round(time.perf_counter() - started, 1)}
        raise RuntimeError('records kept changing while the stream copy was built')
    finally:
        stack.close()


def _fresh_store(path):
    """(store connection, token) when the clustered copy matches the live records, else (None, None)."""
    path = Path(path)
    target = path / FILE
    if os.environ.get('AGERE_RECORD_STORE', 'on') == 'off' or not target.exists():
        return None, None
    stack = sqlite3.connect(f'file:{path / "stack.sqlite3"}?mode=ro', uri=True)
    try:
        live = _record_version(stack)
    finally:
        stack.close()
    if live is None:
        return None, None
    store = sqlite3.connect(f'file:{target}?mode=ro', uri=True, check_same_thread=False)
    meta = dict(store.execute('SELECT key, value FROM meta'))
    if meta.get('version') != VERSION or meta.get('source_digest') != _source_digest(path) or meta.get('record_version') != str(live):
        store.close()
        return None, None
    return store, (meta['source_digest'], meta['record_version'])


def token(path):
    """Records data version (source digest, change counter) while the clustered copy is fresh, else None.

    Lets callers memoise anything derived only from `records` for exactly as long as the records are unchanged."""
    store, tok = _fresh_store(path)
    if store is not None: store.close()
    return tok


def _scan_order(path, streams, patient):
    """Row order of the original unordered query, from SQLite's own plan for it on stack.sqlite3.

    Returns the per-patient sort fields ('stream', 'id', 'rowid' - patient is constant within a list), or
    None when the plan is one this copy cannot reproduce (the caller then runs the original query)."""
    sql = 'SELECT patient, payload FROM records WHERE stream IN (' + ','.join('?' * len(streams)) + ')' + (' AND patient = ?' if patient is not None else '')
    db = sqlite3.connect(f'file:{Path(path) / "stack.sqlite3"}?mode=ro', uri=True)
    try:
        plan = [row[-1] for row in db.execute('EXPLAIN QUERY PLAN ' + sql, [*streams, *([patient] if patient is not None else [])])]
        if len(plan) != 1: return None
        if plan[0] == 'SCAN records': return ('rowid',)
        import re
        m = re.fullmatch(r'SEARCH records USING (?:COVERING )?INDEX (\w+) \((.*)\)', plan[0])
        if not m: return None
        columns = [r[2] for r in db.execute(f'PRAGMA index_info({m.group(1)})')]
    finally:
        db.close()
    if not columns or columns[0] != 'stream' or any(c not in ('stream', 'patient', 'id') for c in columns): return None
    if patient is None and m.group(2) != 'stream=?': return None
    if patient is not None and m.group(2) not in ('stream=?', 'stream=? AND patient=?'): return None
    if columns[:2] != ['stream', 'patient'] and len(columns) > 1: return None  # rows of one patient would not be contiguous by stream
    return tuple(c for c in columns if c != 'patient') + ('rowid',)


def _memo_budget():
    return int(float(os.environ.get('AGERE_RECORD_MEMO_MB', '512')) * 1e6)


def _stream(store, key, stream):
    with _LOCK:
        hit = _MEMO.get(key)
        if hit is not None:
            _MEMO.move_to_end(key); _STATS['memo_hits'] += 1
            return hit[1]
    _STATS['memo_misses'] += 1
    span = store.execute('SELECT lo, hi, bytes FROM ranges WHERE stream = ?', (stream,)).fetchone()
    by_patient = defaultdict(list)
    if span:
        for pid, rowid, rid, payload in store.execute('SELECT patient, src_rowid, id, payload FROM rec WHERE rowid BETWEEN ? AND ?', span[:2]):
            by_patient[pid].append((rowid, rid, json.loads(payload)))
    size = span[2] if span else 0
    budget = _memo_budget()
    if size and size <= budget:
        with _LOCK:
            _MEMO[key] = (size, by_patient); _MEMO_BYTES[0] += size
            while _MEMO_BYTES[0] > budget and len(_MEMO) > 1:
                _, (old, _) = _MEMO.popitem(last=False); _MEMO_BYTES[0] -= old
    return by_patient


def load(path, streams, *, members=None, patient=None, order='stream', _fallback=False):
    """{patient_id: [record, ...]} for the given streams, exactly as the original records query returned them.

    order='stream': per patient in the order of the unordered `SELECT ... WHERE stream IN (...)` - read from SQLite's
    plan for that query (index on stream: stream then rowid; table scan: rowid); unrecognised plans run the query itself.
    order='id': per patient by record id - the order of the same query with `ORDER BY id`.
    members/patient restrict the patients returned (the original callers filtered the same way).
    """
    if order not in ('stream', 'id'): raise ValueError('order must be stream or id')
    streams = sorted(set(streams))
    records = defaultdict(list)
    if not streams: return records
    keep = (lambda pid: pid == patient) if patient is not None else (lambda pid: pid in members) if members is not None else (lambda pid: True)
    store, token = (None, None) if _fallback else _fresh_store(path)
    if store is None:
        _STATS['fallback_reads'] += 1
        db = sqlite3.connect(f'file:{Path(path) / "stack.sqlite3"}?mode=ro', uri=True)
        try:
            sql = 'SELECT patient, payload FROM records WHERE stream IN (' + ','.join('?' * len(streams)) + ')'
            args = list(streams)
            if patient is not None: sql += ' AND patient = ?'; args.append(patient)
            for pid, payload in db.execute(sql + (' ORDER BY id' if order == 'id' else ''), args):
                if keep(pid): records[pid].append(json.loads(payload))
        finally:
            db.close()
        return records
    fields = ('id',) if order == 'id' else _scan_order(path, streams, patient)
    if fields is None:
        store.close()
        return load(path, streams, members=members, patient=patient, order=order, _fallback=True)
    _STATS['store_reads'] += 1
    try:
        parts = [(s, _stream(store, (str(Path(path).resolve()), token, s), s)) for s in streams]
    finally:
        store.close()
    getters = {'stream': lambda s, row: s, 'id': lambda s, row: row[1], 'rowid': lambda s, row: row[0]}
    pids = set().union(*(part for _, part in parts))
    for pid in sorted(pids):
        if not keep(pid): continue
        rows = [(s, row) for s, part in parts for row in part.get(pid, ())]
        rows.sort(key=lambda x: tuple(getters[f](*x) for f in fields))
        records[pid] = [row[2] for _, row in rows]
    return records


def stats():
    return {**_STATS, 'memo_streams': len(_MEMO), 'memo_bytes': _MEMO_BYTES[0]}


def clear_memo():
    with _LOCK:
        _MEMO.clear(); _MEMO_BYTES[0] = 0


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3 or sys.argv[1] not in ('build', 'index'):
        raise SystemExit('usage: python -m clinical_core.agere.clinical.record_store build|index STACK_DIR')
    print(json.dumps(build(sys.argv[2]) if sys.argv[1] == 'build' else index_semantic(sys.argv[2])))
