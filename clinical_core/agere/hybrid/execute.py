"""Compile or run one contract's SQL and check the executed result against the contract (deterministic).

compile_metric  a governed metric (config/agere/metrics) is used only when its grain, output type and time anchor
                match the contract; otherwise the model's own SQL runs. The question's definition always wins.
run_sql         one read-only SELECT/WITH, with a timeout and a row cap; truncation is an error, never a partial answer.
to_answer       the typed answer (scorer contract) built from the executed rows, with the contract's group_by as the
                table key columns; no heuristics about which column is a key.
checks          shape and sanity checks; a run whose checks fail does not vote.
"""
from __future__ import annotations

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
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
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


def to_answer(contract, cols, rows):
    """The scorer contract for the executed rows, or (None, reason)."""
    at = contract['answer_type']
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


def checks(contract, answer, db_path=None):
    """Problems with an executed answer (empty = passed)."""
    at, unit = contract['answer_type'], str(contract.get('unit') or '').lower()
    out = []
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
            con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
            try:
                known = {r[0] for r in con.execute('SELECT patient_id FROM patient')}
            finally:
                con.close()
            unknown = [x for x in ids if x not in known]
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
