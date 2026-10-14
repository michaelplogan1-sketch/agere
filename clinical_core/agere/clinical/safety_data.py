"""Shared read-only data access for the clinical quality and safety engines (loop_closure, diagnostic_delay, risk_outcomes).

Everything here reads an existing semantic layer (semantic.sqlite3) with a read-only connection and degrades to what the
build carries: each engine asks `tables()` / `columns()` and names the missing inputs instead of failing.
"""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import json, re, sqlite3

ISO = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')
VISIT_EXCLUDED_STATUS = {'cancelled', 'canceled', 'no_show', 'noshow', 'entered_in_error', 'planned', 'booked', 'scheduled', 'proposed'}
TELEPHONE_TYPES = {'telephone', 'phone', 'telephone_call'}


def connect(path):
    """Read-only connection to the semantic layer. Builds one only for a stack that has none yet (never migrates an existing one)."""
    p = Path(path)
    target = p / 'semantic.sqlite3' if p.is_dir() else p
    if not target.exists() and p.is_dir() and (p / 'run.json').exists():
        from . import semantic_layer
        target = semantic_layer.ensure(p)
    if not target.exists(): raise FileNotFoundError(f'no semantic layer at {target}')
    return sqlite3.connect(f'file:{target}?mode=ro', uri=True)


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def columns(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def meta(db):
    try:
        return dict(db.execute('SELECT key, value FROM meta').fetchall())
    except sqlite3.Error:
        return {}


def streams(db):
    return {r[0] for r in db.execute('SELECT DISTINCT stream FROM record')} if 'record' in tables(db) else set()


def day(value):
    """ISO date string (YYYY-MM-DD) or None for partial / free-text dates ('2020', '2016-09', 'today')."""
    if not value: return None
    m = ISO.match(str(value))
    if not m: return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def to_date(s):
    return date.fromisoformat(s) if s else None


def days_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def shift(s, days):
    return (date.fromisoformat(s) + timedelta(days=days)).isoformat()


def as_of_of(db, as_of=None):
    return day(str(as_of)) if as_of else day(meta(db).get('as_of')) or date.today().isoformat()


def patients(db, patient_ids=None):
    """{patient_id: {pcp, practice, age, sex}} for the roster (optionally restricted)."""
    cols = columns(db, 'patient')
    age = 'age_latest' if 'age_latest' in cols else 'age_reported'
    out = {}
    for pid, pcp, prac, a, sex in db.execute(f'SELECT patient_id, pcp_clinician_id, pcp_practice_id, {age}, sex_reported FROM patient'):
        if patient_ids is not None and pid not in patient_ids: continue
        out[pid] = {'pcp': pcp, 'practice': prac, 'age': a, 'sex': (sex or '').upper()[:1] or None}
    return out


def visits(db, pids=None):
    """{patient: sorted [(date, kind, organization, clinician, type_concept, principal_icd10)]}. kind = visit | telephone_contact."""
    vcols = columns(db, 'visit')
    icd = 'principal_icd10' if 'principal_icd10' in vcols else 'NULL'
    out = defaultdict(list)
    for pid, d, typ, tc, status, org, clin, code in db.execute(
            f'SELECT patient_id, event_date, type, type_concept, status, organization_id, attending_clinician_id, {icd} FROM visit'):
        if pids is not None and pid not in pids: continue
        d = day(d)
        if not d or (status or '').lower() in VISIT_EXCLUDED_STATUS: continue
        kind = 'telephone_contact' if (typ or '').lower() in TELEPHONE_TYPES else 'visit'
        out[pid].append((d, kind, org, clin, tc, code))
    for v in out.values(): v.sort(key=lambda r: r[0])
    return out


def first_in(sorted_dates, lo, hi):
    """First date in a sorted list with lo <= d <= hi (ISO strings), else None."""
    i = bisect_left(sorted_dates, lo)
    return sorted_dates[i] if i < len(sorted_dates) and sorted_dates[i] <= hi else None


def count_in(sorted_dates, lo, hi):
    return bisect_right(sorted_dates, hi) - bisect_left(sorted_dates, lo)


def clinicians(db):
    if 'clinician' not in tables(db): return {}
    return {cid: {'organization': org, 'specialty': spec} for cid, org, spec in db.execute('SELECT clinician_id, organization_id, specialty FROM clinician')}


def organizations(db):
    if 'organization' not in tables(db): return {}
    return {oid: {'type': typ, 'name': name} for oid, typ, name in db.execute('SELECT organization_id, organization_type, display_name FROM organization')}


def payloads(db, record_ids, fields=None):
    """{record_id: (stream, event_date, payload dict)} for the given ids (chunked IN queries on the primary key)."""
    out, ids = {}, list(record_ids)
    for i in range(0, len(ids), 800):
        chunk = ids[i:i + 800]
        for rid, stream, d, av, payload in db.execute(f'SELECT record_id, stream, event_date, available_date, payload FROM record WHERE record_id IN ({",".join("?" * len(chunk))})', chunk):
            p = json.loads(payload)
            out[rid] = (stream, day(d) or day(av), {k: p.get(k) for k in fields} if fields else p)
    return out


def links_to(db, target_ids, streams=None):
    """[(target_record_id, source stream, field, source record_id)] for records referring to the targets (link table), optionally
    only from the given source streams (probes the (target, stream) index)."""
    if 'link' not in tables(db): return []
    out, ids = [], list(target_ids)
    only = f' AND stream IN ({",".join("?" * len(streams))})' if streams else ''
    for i in range(0, len(ids), 800):
        chunk = ids[i:i + 800]
        out += db.execute(f'SELECT target_record_id, stream, field, record_id FROM link WHERE target_record_id IN ({",".join("?" * len(chunk))}){only}',
                          chunk + list(streams or ())).fetchall()
    return out


def median(xs):
    xs = sorted(xs)
    if not xs: return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def quantile(xs, q):
    xs = sorted(xs)
    if not xs: return None
    k = (len(xs) - 1) * q
    lo = int(k); hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def wilson(k, n, z=1.959964):
    if not n: return None, None
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / d
    return round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)
