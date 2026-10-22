"""Shared readers for the hospital-operations engines (wave 2): one connection rule, one clock parser, one censoring-aware
percentile, one facility resolver, one antimicrobial vocabulary.

Every hospital-ops engine (visit_groups, capacity_simulation, discharge_forecast, flow_decomposition, or_block, hai_surveillance,
antimicrobial_use, bundle_compliance, spatiotemporal_cluster, overbooking_fairness, network_adequacy) reads the read-only
semantic layer of the active warehouse through these helpers, so clocks, censoring and facility names mean the same thing in
every answer. Synthetic data only; no model calls.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

VERSION = 'hospital-ops/1'

# Antimicrobial vocabulary: drug_class values of the semantic medication table that are systemic antibacterials, with the
# NHSN-style spectrum group used for stewardship reporting (broad = used for hospital-onset / resistant infections).
ANTIBACTERIAL_CLASSES = {
    'penicillin': 'narrow_or_community', 'cephalosporin': 'broad_community', 'macrolide': 'narrow_or_community',
    'tetracycline_antibiotic': 'narrow_or_community', 'sulfonamide_antibiotic': 'narrow_or_community',
    'fluoroquinolone': 'broad_community', 'nitrofuran': 'narrow_or_community',
}
# Drug names that settle the spectrum group inside a class (third/fourth-generation cephalosporins and anti-pseudomonal agents).
BROAD_HOSPITAL_DRUGS = ('cefepime', 'ceftazidime', 'piperacillin', 'meropenem', 'imipenem', 'ertapenem', 'aztreonam', 'vancomycin',
                        'linezolid', 'daptomycin', 'tigecycline')
BROAD_COMMUNITY_DRUGS = ('ceftriaxone', 'cefotaxime', 'levofloxacin', 'moxifloxacin', 'ciprofloxacin', 'cefuroxime', 'amoxicillin-clavulanate',
                         'amoxicillin/clavulanate')
ANTIBACTERIAL_NAME = re.compile(r'(?:cillin|cef|ceph|penem|mycin|micin|cycline|floxacin|sulfameth|trimethoprim|nitrofurantoin|vancomycin|'
                                r'linezolid|metronidazole|clindamycin|aztreonam|daptomycin|azithro|clarithro|doxy)', re.I)


def spectrum(drug_name, drug_class):
    name = (drug_name or '').lower()
    if any(d in name for d in BROAD_HOSPITAL_DRUGS): return 'broad_hospital'
    if any(d in name for d in BROAD_COMMUNITY_DRUGS): return 'broad_community'
    return ANTIBACTERIAL_CLASSES.get(drug_class or '', 'narrow_or_community')


def is_antibacterial(drug_name, drug_class):
    return (drug_class in ANTIBACTERIAL_CLASSES) or bool(ANTIBACTERIAL_NAME.search(drug_name or ''))


# ---- connection --------------------------------------------------------------------------------------------------------
def stack_path(obj):
    """Stack object / stack directory / semantic.sqlite3 path -> the stack directory."""
    p = getattr(obj, 'path', None) or obj
    p = Path(p)
    return p.parent if p.suffix == '.sqlite3' else p


def connect(obj, *, write=False):
    """(read-only connection to the semantic layer, as_of). Accepts a Stack, the stack directory or an open connection."""
    if isinstance(obj, sqlite3.Connection):
        return obj, _as_of(obj)
    path = stack_path(obj)
    target = path / 'semantic.sqlite3'
    if not target.exists() or not write:
        from . import semantic_layer
        target = semantic_layer.ensure(path)
    db = sqlite3.connect(str(target), timeout=60) if write else sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    return db, _as_of(db)


def _as_of(db):
    try:
        return dict(db.execute("SELECT key, value FROM meta WHERE key = 'as_of'").fetchall()).get('as_of')
    except sqlite3.Error:
        return None


def has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?", (name,)).fetchone() is not None


def stream(db, name, patient_ids=None):
    """Decoded payloads of one received stream from the semantic record table (index record_stream)."""
    keep = set(patient_ids) if patient_ids is not None else None
    out = []
    for pid, payload in db.execute('SELECT patient_id, payload FROM record WHERE stream = ?', (name,)):
        if keep is not None and pid not in keep: continue
        try:
            row = json.loads(payload)
        except (TypeError, ValueError):
            continue
        row.setdefault('patient_id', pid)
        out.append(row)
    return out


# ---- clocks ------------------------------------------------------------------------------------------------------------
def ts(value, day=None):
    """ISO-8601 / HL7 (YYYYMMDDHHMM[SS]) / date -> naive UTC datetime; None when absent or unparseable."""
    if value in (None, ''):
        return ts(day) if day not in (None, '') else None
    s = str(value).strip()
    try:
        if re.fullmatch(r'\d{12}(\d{2})?', s):
            return datetime.strptime(s[:14].ljust(14, '0'), '%Y%m%d%H%M%S')
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is not None: d = d.astimezone(timezone.utc).replace(tzinfo=None)
        return d
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(s[:10]), datetime.min.time())
        except ValueError:
            return None


def timed(value):
    """True when the value carries a clock time (not a bare date)."""
    s = str(value or '')
    return ':' in s or bool(re.fullmatch(r'\d{12}(\d{2})?', s))


def d(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def hours(a, b):
    return None if a is None or b is None else (b - a).total_seconds() / 3600.0


def month(value):
    return str(value)[:7] if value else None


def window(as_of, months=None, start=None, end=None):
    """(start, end) ISO dates: stated dates win; else the trailing `months` before as_of; else everything up to as_of."""
    end = end or str(as_of)[:10]
    if start: return start, end
    if months:
        e = date.fromisoformat(end)
        y, m = divmod(e.year * 12 + e.month - 1 - int(months), 12)
        return date(y, m + 1, min(e.day, 28)).isoformat(), end
    return None, end


# ---- statistics --------------------------------------------------------------------------------------------------------
def quantile(values, q):
    v = sorted(x for x in values if x is not None)
    if not v: return None
    pos = (len(v) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def km_quantiles(durations, observed, qs=(0.5, 0.9)):
    """Kaplan-Meier quantiles of a duration with right censoring (observed False = still open at the snapshot).
    -> {q: value or None when the curve never falls that far}."""
    pairs = sorted((t, bool(e)) for t, e in zip(durations, observed) if t is not None and t >= 0)
    n, s, out = len(pairs), 1.0, {q: None for q in qs}
    i = 0
    while i < len(pairs):
        t = pairs[i][0]
        events = sum(1 for tt, e in pairs[i:] if tt == t and e)
        ties = sum(1 for tt, _ in pairs[i:] if tt == t)
        if events and n:
            s *= 1 - events / n
            for q in qs:
                if out[q] is None and s <= 1 - q: out[q] = t
        n -= ties; i += ties
    return out


def km_survival(durations, observed, grid):
    """S(t) at each grid point (Kaplan-Meier)."""
    pairs = sorted((t, bool(e)) for t, e in zip(durations, observed) if t is not None and t >= 0)
    n, s, steps, i = len(pairs), 1.0, [], 0
    while i < len(pairs):
        t = pairs[i][0]
        events = sum(1 for tt, e in pairs[i:] if tt == t and e)
        ties = sum(1 for tt, _ in pairs[i:] if tt == t)
        if events and n: s *= 1 - events / n
        steps.append((t, s)); n -= ties; i += ties
    out, j, cur = [], 0, 1.0
    for g in grid:
        while j < len(steps) and steps[j][0] <= g: cur = steps[j][1]; j += 1
        out.append(cur)
    return out


def poisson_ci(k, exposure, per=1000.0, z=1.96):
    """Rate per `per` units of exposure with a Byar / Wilson-Hilferty approximate 95% interval."""
    if not exposure: return None, None, None
    rate = k / exposure * per
    if k == 0: return 0.0, 0.0, round(3.689 / exposure * per, 4)
    lo = k * (1 - 1 / (9 * k) - z / (3 * math.sqrt(k))) ** 3
    k1 = k + 1
    hi = k1 * (1 - 1 / (9 * k1) + z / (3 * math.sqrt(k1))) ** 3
    return round(rate, 4), round(lo / exposure * per, 4), round(hi / exposure * per, 4)


# ---- facilities --------------------------------------------------------------------------------------------------------
def facilities(db):
    return {oid: (name or oid, cls or otype) for oid, name, cls, otype in
            db.execute('SELECT organization_id, display_name, facility_class, organization_type FROM organization')}


def resolve_facility(token, known):
    """Facility named in a question ('hospital 2', 'WH-HOSPITAL-2', a display name) -> organization_id, else None."""
    if not token: return None
    t = str(token).strip().lower()
    ids = list(known)
    for o in ids:
        if o.lower() == t: return o
    m = re.search(r'hospital[\s-]*(\d+)', t)
    if m:
        hits = sorted(o for o in ids if re.search(rf'hospital-{m.group(1)}$', o.lower()))
        if hits: return hits[0]
    names = known if isinstance(known, dict) else {}
    for o, v in names.items():
        label = (v[0] if isinstance(v, tuple) else v) or ''
        if label and label.lower() == t: return o
    return None


def ensure_list(v):
    return list(v) if isinstance(v, (list, tuple)) else ([] if v in (None, '') else [v])


def iso(dt_value):
    return dt_value.isoformat(timespec='minutes') if isinstance(dt_value, datetime) else dt_value


def add_days(day, n):
    return (date.fromisoformat(str(day)[:10]) + timedelta(days=n)).isoformat()


# ---- shared engine plumbing (wave-2 hospital-ops engines) -----------------------------------------------------------------
def spec_validate(name, defaults, spec, p):
    """Authoritative validator from a spec {param: (kind, *args)}: enum / list_enum / int / num / bool / date / str (None allowed)."""
    p = dict(p or {})
    if set(p) - set(defaults): raise ValueError(f'{name} parameters: ' + ', '.join(sorted(defaults)))
    out = {**defaults, **{k: v for k, v in p.items() if v is not None}}
    for k, (kind, *a) in spec.items():
        v = out[k]
        if kind == 'enum' and v not in a[0]: raise ValueError(f'{k}: {tuple(a[0])}')
        elif kind == 'list_enum':
            if not isinstance(v, list) or any(x not in a[0] for x in v): raise ValueError(f'{k}: {tuple(a[0])}')
        elif kind == 'int' and v is not None and (type(v) is not int or not a[0] <= v <= a[1]): raise ValueError(f'{k}: integer {a[0]}..{a[1]}')
        elif kind == 'num' and v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not a[0] <= v <= a[1]):
            raise ValueError(f'{k}: number {a[0]}..{a[1]}')
        elif kind == 'bool': out[k] = bool(v)
        elif kind == 'date' and v is not None and d(v) is None: raise ValueError(f'{k}: ISO date')
        elif kind == 'str' and v is not None and not isinstance(v, str): raise ValueError(f'{k}: text')
    return out


def spec_schema(spec, obj, string, array):
    props = {}
    for k, (kind, *a) in spec.items():
        if kind == 'enum': props[k] = string(list(a[0]), k)
        elif kind == 'list_enum': props[k] = array(string(list(a[0])), k)
        elif kind == 'int': props[k] = {'type': ['integer', 'null'], 'minimum': a[0], 'maximum': a[1], 'description': k}
        elif kind == 'num': props[k] = {'type': ['number', 'null'], 'minimum': a[0], 'maximum': a[1], 'description': k}
        elif kind == 'bool': props[k] = {'type': 'boolean', 'description': k}
        elif kind in ('date', 'str'): props[k] = {'type': ['string', 'null'], 'description': k}
    return obj(props)


def common_params(low):
    """Facility / trailing-window / calendar-year phrasing shared by every route."""
    out = {}
    m = re.search(r'\b(?:last|past|previous) (\d{1,3}) months?\b', low)
    if m and 1 <= int(m.group(1)) <= 120: out['months'] = int(m.group(1))
    elif re.search(r'\b(?:last|past) year\b|\blast 12 months\b', low): out['months'] = 12
    yr = re.search(r'\bin (20\d\d)\b', low)
    if yr: out.update(start=f'{yr.group(1)}-01-01', end=f'{yr.group(1)}-12-31')
    fac = re.search(r'\b(hospital[\s-]*\d+|wh[n]?-hospital-[\w-]+)\b', low)
    if fac: out['facility'] = fac.group(1)
    return out


def visits_in(db, as_of, p, patient_ids=None, classes=None, need=None):
    """Governed visits (visit_groups.load) restricted to the question window / facility / classes -> (rows, basis, (start, end), fac, notes)."""
    from . import visit_groups
    rows, basis = visit_groups.load(db, patient_ids, as_of)
    start, end = window(as_of, p.get('months'), p.get('start'), p.get('end'))
    fac, notes = None, []
    if p.get('facility'):
        fac = resolve_facility(p['facility'], facilities(db))
        if not fac: notes.append(f"No facility matches '{p['facility']}'; every facility is counted.")
    sel = [r for r in rows if (not start or r['start_date'] >= start) and r['start_date'] <= end and (not fac or r['organization_id'] == fac)
           and (not classes or r['visit_class'] in classes) and (not need or need(r))]
    return sel, basis, (start, end), fac, notes


def generic_answer(op, report, plan, headline_text):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    out = {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['table'], 'count': report['receipt'].get('count'),
           'patient_ids': report.get('patient_ids', []), 'value': headline_text, 'method': report['method'],
           'limitations': report['assumptions'] + report.get('notes', []) + [f"Source: {report['basis']}."], 'overall': {op: report['receipt']}}
    for k in ('detail', 'scenarios', 'clusters', 'cases'):
        if report.get(k): out['overall'][k] = report[k]
    return out


def run(connected_obj, as_of, patient_ids):
    """Open the semantic layer once -> (db, as_of date)."""
    db, snap = connect(connected_obj)
    return db, str(as_of or snap)[:10]
