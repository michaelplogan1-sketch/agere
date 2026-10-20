"""Compile or run one contract's SQL and check the executed result against the contract (deterministic).

compile_metric  a governed metric (config/agere/metrics) is used only when its grain, output type and time anchor
                match the contract; otherwise the model's own SQL runs. The question's definition always wins.
run_sql         one read-only SELECT/WITH, with a timeout and a row cap; truncation is an error, never a partial answer.
to_answer       the typed answer (scorer contract) built from the executed rows, with the contract's group_by as the
                table key columns; no heuristics about which column is a key. A rows answer must return exactly the
                step's declared output columns; list columns arrive as JSON arrays (json_group_array) and are parsed.
checks          shape and sanity checks; a run whose checks fail does not vote.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

ROW_CAP = 50_000
_ENTITY = {'patient': ('person', 'chart'), 'member_month': ('member_month',)}


def _metrics():
    from clinical_core.agere.clinical import metrics
    return metrics


def compile_metric(contract, as_of):
    """(sql, None) when the named governed metric matches the contract, else (None, reason)."""
    mid = contract.get('metric_id')
    if not mid:
        return None, None
    metrics = _metrics()
    try:
        m = metrics.get(mid)
    except KeyError:
        return None, f'unknown metric {mid}'
    if not m.get('sql_template'):
        return None, f'metric {mid} has no SQL template'
    entity = (m.get('grain') or {}).get('entity')
    if contract['grain'].get('entity') not in _ENTITY.get(entity, (entity,)):
        return None, f"metric {mid} counts {entity}, the contract counts {contract['grain'].get('entity')}"
    if (m.get('output') or {}).get('returns') != contract['answer_type']:
        return None, f"metric {mid} returns {(m.get('output') or {}).get('returns')}, the contract needs {contract['answer_type']}"
    window = contract.get('window') or {}
    anchor = (m.get('time_anchor') or {}).get('field')
    if window.get('start') and anchor and window.get('anchor') != anchor:
        return None, f"metric {mid} anchors on {anchor}, the contract on {window.get('anchor')}"
    period = (window['start'], window['end'] or as_of) if window.get('start') else None
    return metrics.render_sql(m, metrics.parameters(m, '', as_of, period)), None


# The connection is read-only (mode=ro), so writes fail there; this guard only blocks what read-only mode does not:
# a second statement, ATTACH (another file) and PRAGMA. Functions such as replace() are fine.
_WRITE = re.compile(r'\b(attach|detach|pragma)\b', re.I)


def run_sql(db_path, sql, timeout_s=120.0):
    """(columns, rows, error). Read-only connection; one statement; the row cap is an error."""
    if not isinstance(sql, str) or not re.match(r'(?is)^\s*(select|with)\b', sql):
        return None, None, 'not a single SELECT/WITH'
    body = re.sub(r"'(?:[^']|'')*'", "''", sql)  # ignore string literals when looking for write keywords
    if _WRITE.search(body) or ';' in body.strip().rstrip(';'):
        return None, None, 'not a single read-only statement'
    if re.search(r'\bage_latest\b', body, re.I):
        return None, None, 'Stale feed age is not an analytical field; use age_at(birth_date, as_of).'
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    from ..clinical.age import register
    register(con)
    deadline = time.monotonic() + timeout_s
    con.set_progress_handler(lambda: int(time.monotonic() > deadline), 100_000)
    try:
        cur = con.execute(sql.strip().rstrip(';'))
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(ROW_CAP + 1)
        if len(rows) > ROW_CAP:
            return cols, None, f'more than {ROW_CAP} rows'
        return cols, rows, None
    except sqlite3.Error as exc:
        return None, None, 'timeout' if time.monotonic() > deadline else f'{type(exc).__name__}: {exc}'
    finally:
        con.close()


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def column_types(output):
    """A step's declared output {column: description} -> {column: str|int|number|list}."""
    def kind(desc):
        d = str(desc).strip().lower()
        return 'list' if d.startswith('list') else 'int' if d == 'int' else 'number' if d == 'number' else 'str'
    return {c: kind(d) for c, d in (output or {}).items()}


def _rows_answer(spec, cols, rows):
    types = spec['columns']
    if sorted(cols) != sorted(types) or len(set(cols)) != len(cols):
        return None, f"rows need exactly the columns {list(types)}, got {cols}"
    out = []
    for r in rows:
        row = {}
        for c, v in zip(cols, r):
            if types[c] == 'list' and isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    return None, f'{c} is not a JSON array: {v[:60]!r}'
            row[c] = v
        out.append(row)
    return {'rows': out}, None


def to_answer(contract, cols, rows, spec=None):
    """The scorer contract for the executed rows, or (None, reason). spec: {'columns': {name: type}, 'keys': [...]}
    for a rows answer."""
    at = contract['answer_type']
    if at == 'rows':
        return _rows_answer(spec, cols, rows)
    if at in ('count', 'value'):
        if len(rows) != 1:
            return None, f'{at} needs exactly one row, got {len(rows)}'
        nums = [v for v in rows[0] if _num(v)]
        if len(nums) != 1:
            return None, f'{at} needs exactly one numeric column, got {len(nums)}'
        return {at: nums[0]}, None
    if at == 'patient_set':
        if 'patient_id' not in cols:
            return None, 'patient_set needs a patient_id column'
        i = cols.index('patient_id')
        return {'patient_ids': [r[i] for r in rows]}, None
    keys = list(contract.get('group_by') or [])
    missing = [k for k in keys if k not in cols]
    if missing:
        return None, f"group_by columns not in the result: {', '.join(missing)}"
    measures = [c for c in cols if c not in keys]
    if len(measures) != 1:
        return None, f"a table needs exactly one measure column besides group_by, got {measures}"
    ki, vi = [cols.index(k) for k in keys], cols.index(measures[0])
    return {'table': [{'keys': [r[i] for i in ki], 'value': r[vi]} for r in rows]}, None


def _known_patients(db_path, ids):
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        known = {r[0] for r in con.execute('SELECT patient_id FROM patient')}
    finally:
        con.close()
    return [x for x in ids if x not in known]


def checks(contract, answer, db_path=None, spec=None):
    """Problems with an executed answer (empty = passed)."""
    at, unit = contract['answer_type'], str(contract.get('unit') or '').lower()
    out = []
    if at == 'rows':
        types, keys = spec['columns'], spec['keys']
        ks = [tuple(r[k] for k in keys) for r in answer['rows']]
        if any(not isinstance(k, str) or not k for kk in ks for k in kk):
            out.append('row keys must be non-empty strings')
        elif len(set(ks)) != len(ks):
            out.append(f'{len(ks) - len(set(ks))} duplicate row keys (the grain is not distinct)')
        for c, t in types.items():
            vals = [r[c] for r in answer['rows']]
            if t == 'int' and any(not _num(v) or float(v) != int(v) for v in vals):
                out.append(f'{c} must be an integer')
            elif t == 'number' and any(not _num(v) for v in vals):
                out.append(f'{c} must be a number')
            elif t == 'list' and any(not isinstance(v, list) or not v or any(not isinstance(x, str) for x in v) for v in vals):
                out.append(f'{c} must be a non-empty list of strings')
        if 'patient_id' in keys and db_path and ks and not out:
            unknown = _known_patients(db_path, sorted({r['patient_id'] for r in answer['rows']}))
            if unknown:
                out.append(f'{len(unknown)} ids are not patients (e.g. {unknown[0]})')
        return out
    if at in ('count', 'value'):
        v = answer[at]
        if v is None or not _num(v):
            out.append(f'{at} is not a number')
        elif v < 0 and unit not in ('usd', 'dollars', 'days_difference'):
            out.append(f'{at} is negative')
        elif at == 'count' and float(v) != int(v):
            out.append('count is not an integer')
        elif unit == 'percent' and not 0 <= v <= 100:
            out.append('percent outside 0-100')
    elif at == 'patient_set':
        ids = answer['patient_ids']
        if any(not isinstance(x, str) or not x for x in ids):
            out.append('patient ids must be non-empty strings')
        elif len(set(ids)) != len(ids):
            out.append(f'{len(ids) - len(set(ids))} duplicate patient ids (the grain is not distinct)')
        elif db_path and ids:
            unknown = _known_patients(db_path, ids)
            if unknown:
                out.append(f'{len(unknown)} ids are not patients (e.g. {unknown[0]})')
    else:
        keys = [tuple(r['keys']) for r in answer['table']]
        if contract.get('row_limit') and len(keys) > contract['row_limit']:
            out.append(f"{len(keys)} rows for a top-{contract['row_limit']} table")
        if any(k is None for row in keys for k in row):
            out.append('a table key is null')
        if len(set(keys)) != len(keys):
            out.append('duplicate table keys')
        if any(not _num(r['value']) for r in answer['table']):
            out.append('a table value is not a number')
        elif unit == 'percent' and any(not 0 <= r['value'] <= 100 for r in answer['table']):
            out.append('a percent outside 0-100')
    return out
