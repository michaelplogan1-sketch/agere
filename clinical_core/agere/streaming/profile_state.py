"""Source profile maintained from value counts, so a tick does not re-scan whole streams.

source_profile.build reads every record of every stream with json_each (81 s at pilot scale). Everything it reports is
an aggregate over record field values: counts per (stream, key, type), count of distinct values, numeric min / max, the
value counts of low-cardinality text fields, the element counts of text arrays, and per (name, unit) measurement counts
and value min / max. All of these follow from reference counts of (stream, key, type, value), which a tick updates
from the records it added and removed (old payloads out, new payloads in). The profile of a touched stream is then
re-derived from the counts with the same rules (LOW_CARDINALITY, SKIP_VALUE_SUFFIXES, the 5000-element cap in value
order) and checked equal to a json_each rebuild by the equivalence check.

Values longer than LONG characters are kept as a hash (they only feed distinct counts); if such a value belongs to a
field whose values are enumerated, the stream falls back to a json_each recount.

State lives in <stack>/streaming-state.sqlite3: pc (key values), pe (text array elements), pm (measurement values).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from ..clinical import source_profile

LONG = 512
DDL = """
CREATE TABLE IF NOT EXISTS pc(stream TEXT, key TEXT, type TEXT, vkey TEXT, value, long INTEGER, n INTEGER,
  PRIMARY KEY(stream, key, type, vkey));
CREATE TABLE IF NOT EXISTS pe(stream TEXT, key TEXT, value TEXT, n INTEGER, PRIMARY KEY(stream, key, value));
CREATE TABLE IF NOT EXISTS pm(stream TEXT, gkey TEXT, name, unit, numeric INTEGER, value, n INTEGER, PRIMARY KEY(stream, gkey));
CREATE TABLE IF NOT EXISTS pmeta(key TEXT PRIMARY KEY, value TEXT);
"""


def _sha(v):
    return hashlib.sha1(v.encode()).hexdigest()


def _connect(state_path):
    db = sqlite3.connect(state_path)
    db.executescript(DDL)
    db.create_function('agere_sha1', 1, lambda v: None if v is None else _sha(str(v)), deterministic=True)
    return db


def ready(state_path):
    db = _connect(state_path)
    try:
        return db.execute("SELECT value FROM pmeta WHERE key = 'built'").fetchone() is not None
    finally:
        db.close()


_ADD = {
    'pc': """INSERT INTO pc(stream, key, type, vkey, value, long, n)
             SELECT d.stream, j.key, j.type,
                    CASE WHEN j.type = 'null' THEN '' WHEN length(CAST(j.value AS TEXT)) > {long} THEN 'h:' || agere_sha1(j.value)
                         ELSE j.type || ':' || CAST(j.value AS TEXT) END,
                    CASE WHEN length(CAST(j.value AS TEXT)) > {long} THEN NULL ELSE j.value END,
                    length(CAST(j.value AS TEXT)) > {long}, sum(d.sign)
             FROM {src} d, json_each(d.payload) j WHERE substr(j.key, 1, 1) <> '_' {flt}
             GROUP BY 1, 2, 3, 4
             ON CONFLICT(stream, key, type, vkey) DO UPDATE SET n = n + excluded.n""",
    'pe': """INSERT INTO pe(stream, key, value, n)
             SELECT d.stream, j.key, e.value, sum(d.sign) FROM {src} d, json_each(d.payload) j, json_each(j.value) e
             WHERE j.type = 'array' AND e.type = 'text' AND substr(j.key, 1, 1) <> '_' {flt}
             GROUP BY 1, 2, 3
             ON CONFLICT(stream, key, value) DO UPDATE SET n = n + excluded.n""",
}


def _pm_sql(src, flt):
    parts = []
    for stream, (nf, uf, vf) in source_profile.MEASUREMENT_STREAMS.items():
        parts.append(f"""SELECT d.stream AS stream, json_extract(d.payload, '$.{nf}') AS name, json_extract(d.payload, '$.{uf}') AS unit,
                                json_type(d.payload, '$.{vf}') IN ('integer', 'real') AS numeric,
                                json_extract(d.payload, '$.{vf}') AS value, d.sign AS sign FROM {src} d WHERE d.stream = '{stream}' {flt}""")
    union = ' UNION ALL '.join(parts)
    return f"""INSERT INTO pm(stream, gkey, name, unit, numeric, value, n)
               SELECT stream, json_array(name, unit, numeric, typeof(value), value), name, unit, numeric, value, sum(sign)
               FROM ({union}) GROUP BY 1, 2
               ON CONFLICT(stream, gkey) DO UPDATE SET n = n + excluded.n"""


def build(state_path, stack_path):
    """Initial counts from every visible record of the store."""
    db = _connect(state_path)
    try:
        with db:
            db.execute('DELETE FROM pc'); db.execute('DELETE FROM pe'); db.execute('DELETE FROM pm')
            db.execute('ATTACH ? AS s', (str(stack_path),))
            src = '(SELECT stream, payload, 1 AS sign FROM s.records)'
            db.execute(_ADD['pc'].format(long=LONG, src=src, flt=''))
            db.execute(_ADD['pe'].format(src=src, flt=''))
            db.execute(_pm_sql(src, ''))
            db.execute("INSERT OR REPLACE INTO pmeta VALUES ('built', datetime('now'))")
        db.execute('DETACH s')
    finally:
        db.close()


def apply(state_path, removed, added):
    """removed / added: iterables of (stream, payload) for visible records leaving / entering the store."""
    db = _connect(state_path)
    try:
        with db:
            db.execute('CREATE TEMP TABLE IF NOT EXISTS delta(stream TEXT, payload TEXT, sign INTEGER)')
            db.execute('DELETE FROM delta')
            db.executemany('INSERT INTO delta VALUES (?,?,-1)', removed)
            db.executemany('INSERT INTO delta VALUES (?,?,1)', added)
            db.execute(_ADD['pc'].format(long=LONG, src='delta', flt=''))
            db.execute(_ADD['pe'].format(src='delta', flt=''))
            db.execute(_pm_sql('delta', ''))
            db.execute('DELETE FROM pc WHERE n = 0'); db.execute('DELETE FROM pe WHERE n = 0'); db.execute('DELETE FROM pm WHERE n = 0')
            db.execute('DELETE FROM delta')
    finally:
        db.close()


def profile_streams(state_path, stack_path, streams):
    """Same output as semantic.profile_streams (json_each over the store), derived from the counts.
    -> (streams dict, measurements dict, streams that need a json_each recount)."""
    db = _connect(state_path)
    stack = sqlite3.connect(f'file:{stack_path}?mode=ro', uri=True)
    out, meas, recount = {}, {}, set()
    try:
        for s in sorted(streams):
            n = stack.execute('SELECT count(*) FROM records WHERE stream = ?', (s,)).fetchone()[0]
            if not n:
                continue
            fields = {}
            for key, typ, cnt, distinct in db.execute(
                    "SELECT key, type, sum(n), sum(CASE WHEN type = 'null' THEN 0 ELSE 1 END) FROM pc WHERE stream = ? AND n > 0 "
                    'GROUP BY key, type ORDER BY key, type', (s,)):
                f = fields.setdefault(key, {'populated': 0, 'types': {}, 'distinct': 0})
                f['types'][typ] = cnt
                if typ != 'null':
                    f['populated'] += cnt
                    f['distinct'] = max(f['distinct'], distinct)
            for key, lo, hi in db.execute(
                    "SELECT key, min(value), max(value) FROM pc WHERE stream = ? AND n > 0 AND type IN ('integer', 'real') GROUP BY key", (s,)):
                fields[key]['numeric_range'] = [lo, hi]
            wanted = {k for k, f in fields.items() if f['types'].get('text') and f['distinct'] <= source_profile.LOW_CARDINALITY
                      and not k.endswith(source_profile.SKIP_VALUE_SUFFIXES)}
            for key, value, cnt, long in db.execute(
                    "SELECT key, value, n, long FROM pc WHERE stream = ? AND n > 0 AND type = 'text' ORDER BY key, value", (s,)):
                if key in wanted:
                    if long:
                        recount.add(s)
                        break
                    fields[key].setdefault('values', {})[value] = cnt
            for key, value, cnt in db.execute('SELECT key, value, n FROM pe WHERE stream = ? AND n > 0 ORDER BY key, value', (s,)):
                f = fields.get(key)
                if f is None:
                    continue
                el = f.setdefault('element_values', {})
                if len(el) < 5000: el[value] = cnt
            out[s] = {'records': n, 'fields': fields}
            if s in source_profile.MEASUREMENT_STREAMS and source_profile.MEASUREMENT_STREAMS[s][0] in fields:
                groups = {}
                for name, unit, numeric, value, cnt in db.execute(
                        'SELECT name, unit, numeric, value, n FROM pm WHERE stream = ? AND n > 0 ORDER BY name, unit', (s,)):
                    g = groups.setdefault((name, unit), {'name': name, 'unit': unit, 'records': 0, 'numeric': 0, 'lo': None, 'hi': None, 'vals': []})
                    g['records'] += cnt
                    g['numeric'] += cnt if numeric else 0
                    g['vals'].append(value)
                rows = []
                for (name, unit), g in groups.items():
                    lo, hi = _minmax(db, g['vals'])
                    rows.append({'name': name, 'unit': unit, 'records': g['records'], 'numeric': g['numeric'], 'range': [lo, hi]})
                meas[s] = rows
    finally:
        db.close()
        stack.close()
    return out, meas, recount


def _minmax(db, vals):
    """SQLite min / max over mixed-type values (NULL ignored; numbers < text < blob), as the profile query computes them."""
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None
    db.execute('CREATE TEMP TABLE IF NOT EXISTS mm(v)')
    db.execute('DELETE FROM mm')
    db.executemany('INSERT INTO mm VALUES (?)', ((v,) for v in vals))
    r = db.execute('SELECT min(v), max(v) FROM mm').fetchone()
    db.execute('DELETE FROM mm')
    return r[0], r[1]
