"""Visit / encounter grouper: one governed hospital-visit table (`visit_group`) built from every hospital-capable source.

Encounters arrive several times over: an ED encounter in the EHR, its ADT registration (A04) and class change (A06), the
inpatient stay it became (EHR, ADT A01/A03, an inpatient claim) and the professional and facility claims of the same day.
Hospital-operations questions (flow, census, boarding, antimicrobial days present, bundles) need ONE row per hospital visit
with its stage clocks, so this pass groups them:

  episodes  governed ED visits (ed_definition: EHR + ADT + claims, one per patient-day), EHR emergency / inpatient /
            observation encounters (with clocks), ADT visits (messages folded per visit number, A08 updates applied in message
            time order) and distinct stays (stay table: EHR, ADT and payer-claim stays)
  grouping  per patient, in start order: an episode joins the open visit when its dates overlap the visit's span at a
            compatible facility (same facility, or one side unknown), or when an inpatient / observation episode starts the day
            after an ED-only visit ended (overnight ED -> admission). A different known facility starts a new visit flagged
            transfer_in when it begins within a day of the previous visit's end.
  clocks    ed_arrival (EHR ED start or ADT A04), ed_departure (EHR ED end, or ADT A03 of a visit that stayed class E),
            admit_decision (ADT A06 class change E -> I/O), inpatient_admit (stay admit time, ADT A01, EHR inpatient start),
            discharge (stay discharge time, ADT A03 of the inpatient visit, EHR end). Day-only sources leave the clock NULL and
            keep the date; time_precision says which.

The table is written into semantic.sqlite3 by the semantic-layer build (semantic_layer.build) and by `apply(stack_path)` for
an existing build, so the SQL analyst and planner can query it; engines read it through `load()`, which computes the same
rows in memory when an older build lacks the table (heal, never block). Synthetic data only; no model calls.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import timedelta

from . import hospital_ops as H

VERSION = 'visit-groups/1'
OP = 'visit_groups'
TABLE = 'visit_group'
DDL = f"""
CREATE TABLE {TABLE}(visit_group_id TEXT PRIMARY KEY, patient_id TEXT, organization_id TEXT, visit_class TEXT,
  has_ed INTEGER, has_observation INTEGER, has_inpatient INTEGER, start_date TEXT, end_date TEXT, discharge_known INTEGER,
  ed_arrival TEXT, ed_departure TEXT, admit_decision TEXT, inpatient_admit TEXT, discharge_time TEXT, ed_hours REAL,
  los_days REAL, los_hours REAL, time_precision TEXT, disposition TEXT, admit_reason TEXT, principal_icd10 TEXT, unit TEXT,
  transfer_in INTEGER, sources TEXT, members TEXT, n_members INTEGER, version TEXT);
CREATE INDEX {TABLE}_patient ON {TABLE}(patient_id, start_date);
CREATE INDEX {TABLE}_org_date ON {TABLE}(organization_id, start_date);
"""
COLUMNS = ('visit_group_id', 'patient_id', 'organization_id', 'visit_class', 'has_ed', 'has_observation', 'has_inpatient', 'start_date',
           'end_date', 'discharge_known', 'ed_arrival', 'ed_departure', 'admit_decision', 'inpatient_admit', 'discharge_time', 'ed_hours',
           'los_days', 'los_hours', 'time_precision', 'disposition', 'admit_reason', 'principal_icd10', 'unit', 'transfer_in', 'sources',
           'members', 'n_members', 'version')
KIND_ORDER = {'ed': 0, 'observation': 1, 'inpatient': 2}


# ---- source episodes ---------------------------------------------------------------------------------------------------
def _adt_visits(db, keep):
    """ADT messages folded per visit number -> episodes with A04/A06/A01/A03 clocks (message time order; A08 updates class)."""
    msgs = defaultdict(list)
    for m in H.stream(db, 'adt_messages', keep):
        vn = m.get('visit_number') or m.get('encounter_id')
        if vn: msgs[(m['patient_id'], vn)].append(m)
    out = []
    for (pid, vn), rows in msgs.items():
        rows.sort(key=lambda r: (H.ts(r.get('message_datetime')) or H.ts(r.get('event_date')) or H.ts('1900-01-01')))
        t = lambda trig: [H.ts(r.get('message_datetime')) for r in rows if r.get('trigger_event') == trig and H.ts(r.get('message_datetime'))]  # noqa: E731
        classes = [r.get('patient_class') for r in rows if r.get('patient_class')]
        first, final = (classes[0] if classes else None), (classes[-1] if classes else None)
        a04, a06, a01, a03 = t('A04'), t('A06'), t('A01'), t('A03')
        start = min([x for x in (a04[:1] + a01[:1] + a06[:1]) if x] or [H.ts(rows[0].get('event_date'))])
        end = a03[-1] if a03 else None
        kind_final = {'I': 'inpatient', 'O': 'observation', 'E': 'ed'}.get(final, 'ed')
        loc = [r.get('assigned_location') for r in rows if r.get('assigned_location')]
        dispo = [r.get('discharge_disposition') for r in rows if r.get('discharge_disposition')]
        reason = [r.get('admit_reason') for r in rows if r.get('admit_reason')]
        org = rows[0].get('organization_id')
        base = {'patient_id': pid, 'org': org, 'source': 'adt', 'member': f'adt:{vn}', 'admit_reason': reason[-1] if reason else None,
                'disposition': dispo[-1] if dispo else None, 'unit': loc[-1] if loc else None, 'precision': 'minute'}
        if first == 'E' or a04:
            ed_end = a06[0] if a06 else (end if kind_final == 'ed' else None)
            out.append({**base, 'kind': 'ed', 'start': start, 'end': ed_end or start, 'ed_arrival': (a04 or [start])[0],
                        'ed_departure': end if kind_final == 'ed' else None, 'admit_decision': a06[0] if a06 else None,
                        'discharged': kind_final == 'ed' and end is not None})
        if kind_final in ('inpatient', 'observation'):
            adm = (a01 or a06 or [start])[0]
            out.append({**base, 'kind': kind_final, 'start': adm, 'end': end, 'inpatient_admit': adm, 'discharge_time': end,
                        'admit_decision': a06[0] if a06 else None, 'discharged': end is not None})
    return out


def _ehr_visits(db, keep):
    out = []
    for pid, typ, st, en, ed, end_d, org, dispo, dx, eid, status in db.execute(
            "SELECT patient_id, type_concept, start_time, end_time, event_date, end_date, organization_id, disposition, principal_icd10, "
            "encounter_id, status FROM visit WHERE type_concept IN ('emergency_visit','inpatient_admission','observation_stay')"):
        if keep is not None and pid not in keep: continue
        if str(status or '').lower() in ('cancelled', 'canceled', 'entered-in-error', 'entered_in_error'): continue
        kind = {'emergency_visit': 'ed', 'inpatient_admission': 'inpatient', 'observation_stay': 'observation'}[typ]
        a, b = H.ts(st, ed), (H.ts(en, end_d) if (en or end_d) else None)
        row = {'patient_id': pid, 'kind': kind, 'org': org, 'start': a, 'end': b, 'source': 'ehr', 'member': f'ehr:{eid}',
               'disposition': dispo, 'principal': dx, 'precision': 'minute' if H.timed(st) else 'day', 'discharged': b is not None}
        if kind == 'ed':
            row.update(ed_arrival=a if H.timed(st) else None, ed_departure=b if H.timed(en) else None)
        else:
            row.update(inpatient_admit=a if H.timed(st) else None, discharge_time=b if H.timed(en) else None)
        out.append(row)
    return out


def _stays(db, keep):
    out = []
    for i, (pid, ad, dd, typ, org, known, at, dt, src) in enumerate(db.execute(
            'SELECT patient_id, admit_date, discharge_date, type, organization_id, discharge_known, admit_time_utc, discharge_time_utc, '
            'stay_source FROM stay')):
        if keep is not None and pid not in keep: continue
        kind = 'observation' if str(typ or '').lower().startswith('obs') else 'inpatient'
        a = H.ts(at) or H.ts(ad)
        b = (H.ts(dt) or H.ts(dd)) if known else None
        out.append({'patient_id': pid, 'kind': kind, 'org': org, 'start': a, 'end': b, 'source': f'stay_{src}',
                    'member': f'stay:{src}:{pid}:{ad}', 'inpatient_admit': H.ts(at) if at else None, 'discharge_time': H.ts(dt) if (dt and known) else None,
                    'precision': 'minute' if at else 'day', 'discharged': bool(known) and b is not None})
    return out


def _ed_events(db, keep):
    from . import ed_definition
    out = []
    for e in ed_definition.events(db, keep=keep):
        a = H.ts(e['day'])
        out.append({'patient_id': e['patient_id'], 'kind': 'ed', 'org': None, 'start': a, 'end': a, 'source': 'ed_definition',
                    'member': f"ed:{e['encounter_id']}", 'principal': e['principal'], 'ed_sources': e['sources'], 'admitted': e['admitted'],
                    'precision': 'day', 'discharged': True})
    return out


# ---- grouping ----------------------------------------------------------------------------------------------------------
def _compatible(a, b):
    return a is None or b is None or a == b


def group(episodes, as_of):
    """Episodes (dicts with patient_id, kind, org, start, end) -> visit groups (list of dicts, COLUMNS)."""
    snap = H.ts(as_of) + timedelta(days=1) if as_of else None
    by = defaultdict(list)
    for e in episodes:
        if e.get('start') is None: continue
        if snap and e['start'] >= snap: continue
        by[e['patient_id']].append(e)
    groups = []
    for pid, eps in by.items():
        eps.sort(key=lambda e: (e['start'].date(), KIND_ORDER[e['kind']], e['start']))
        cur = None
        for e in eps:
            s_day = e['start'].date()
            e_end = (e['end'] or snap or e['start'])
            if cur is not None:
                overlap = s_day <= cur['end_day'] and _compatible(cur['org'], e['org'])
                overnight = (e['kind'] != 'ed' and cur['kinds'] == {'ed'} and s_day == cur['end_day'] + timedelta(days=1)
                             and _compatible(cur['org'], e['org']))
                if overlap or overnight:
                    cur['eps'].append(e); cur['kinds'].add(e['kind'])
                    cur['org'] = cur['org'] or e['org']
                    cur['end_day'] = max(cur['end_day'], e_end.date() if e['kind'] != 'ed' or e['end'] else cur['end_day'])
                    continue
                transfer = (e['org'] and cur['org'] and e['org'] != cur['org'] and s_day <= cur['end_day'] + timedelta(days=1))
            else:
                transfer = False
            cur = {'patient_id': pid, 'org': e['org'], 'eps': [e], 'kinds': {e['kind']}, 'start_day': s_day,
                   'end_day': e_end.date() if e['kind'] != 'ed' or e['end'] else s_day, 'transfer_in': bool(transfer)}
            groups.append(cur)
    return [_row(g, as_of) for g in groups]


def _first(eps, key, kinds=None, pick=min):
    vals = [e.get(key) for e in eps if e.get(key) is not None and (kinds is None or e['kind'] in kinds)]
    return pick(vals) if vals else None


def _row(g, as_of):
    eps = g['eps']
    has_ed, has_obs, has_ip = 'ed' in g['kinds'], 'observation' in g['kinds'], 'inpatient' in g['kinds']
    admitted_flag = any(e.get('admitted') for e in eps)
    if has_ed and has_ip: cls = 'ed_to_inpatient'
    elif has_ed and has_obs: cls = 'ed_to_observation'
    elif has_ed: cls = 'emergency'
    elif has_obs and has_ip: cls = 'observation_to_inpatient'
    elif has_obs: cls = 'observation'
    else: cls = 'direct_inpatient'
    ed_arr = _first(eps, 'ed_arrival')
    ed_dep = _first(eps, 'ed_departure', pick=max)
    decision = _first(eps, 'admit_decision')
    ip_adm = _first(eps, 'inpatient_admit', ('inpatient', 'observation'))
    hosp = [e for e in eps if e['kind'] in ('inpatient', 'observation')]
    discharged = any(e.get('discharged') for e in hosp) if hosp else True  # one source's discharge settles it (ADT A03 is often missing)
    dis_t = _first(eps, 'discharge_time', ('inpatient', 'observation'), pick=max) if discharged else None
    if not hosp and ed_dep: dis_t = None
    end_dates = [e['end'] for e in (hosp or eps) if e.get('end') is not None]
    end_day = (max(end_dates).date().isoformat() if end_dates else g['end_day'].isoformat()) if discharged else None
    start = min(e['start'] for e in eps)
    start_hosp = min((e['start'] for e in hosp), default=None)
    los_days = (H.d(end_day) - start_hosp.date()).days if (hosp and end_day and start_hosp) else None
    los_hours = round(H.hours(ip_adm, dis_t), 2) if ip_adm and dis_t and dis_t > ip_adm else None
    ed_hours = None
    if ed_arr:
        stop = ed_dep or decision
        if stop and stop > ed_arr: ed_hours = round(H.hours(ed_arr, stop), 2)
    srcs = sorted({e['source'] for e in eps} | {s for e in eps for s in str(e.get('ed_sources') or '').split(',') if s})
    members = sorted({e['member'] for e in eps})
    pref = lambda key: next((e.get(key) for k in ('inpatient', 'observation', 'ed') for e in eps if e['kind'] == k and e.get(key)), None)  # noqa: E731
    precision = 'minute' if (ed_arr or ip_adm) else 'day'
    vid = hashlib.sha1(f"{g['patient_id']}|{start.date()}|{g['org']}|{cls}".encode()).hexdigest()[:16]
    return {'visit_group_id': f'VG-{vid}', 'patient_id': g['patient_id'], 'organization_id': g['org'], 'visit_class': cls,
            'has_ed': int(has_ed), 'has_observation': int(has_obs), 'has_inpatient': int(has_ip),
            'start_date': start.date().isoformat(), 'end_date': end_day, 'discharge_known': int(bool(discharged)),
            'ed_arrival': H.iso(ed_arr), 'ed_departure': H.iso(ed_dep), 'admit_decision': H.iso(decision), 'inpatient_admit': H.iso(ip_adm),
            'discharge_time': H.iso(dis_t), 'ed_hours': ed_hours, 'los_days': los_days, 'los_hours': los_hours, 'time_precision': precision,
            'disposition': pref('disposition'), 'admit_reason': pref('admit_reason'), 'principal_icd10': pref('principal'), 'unit': pref('unit'),
            'transfer_in': int(g['transfer_in']), 'sources': json.dumps(srcs), 'members': json.dumps(members), 'n_members': len(members),
            'version': VERSION, 'ed_admitted_claim_only': int(admitted_flag and not (has_ip or has_obs))}


def compute(db, patient_ids=None, as_of=None):
    """Visit groups computed from the source tables (same rows the stored table holds)."""
    as_of = as_of or H._as_of(db)
    keep = set(patient_ids) if patient_ids is not None else None
    episodes = _ed_events(db, keep) + _ehr_visits(db, keep) + _adt_visits(db, keep) + _stays(db, keep)
    return group(episodes, as_of), Counter(e['source'] for e in episodes)


def build(out, as_of=None):
    """Write the visit_group table into an open semantic-layer connection -> summary (stored in meta by the caller)."""
    rows, sources = compute(out, None, as_of)
    out.execute(f'DROP TABLE IF EXISTS {TABLE}')
    out.executescript(DDL)
    out.executemany(f'INSERT OR REPLACE INTO {TABLE} VALUES ({",".join("?" * len(COLUMNS))})', [tuple(r[c] for c in COLUMNS) for r in rows])
    return {'version': VERSION, 'visits': len(rows), 'source_episodes': dict(sources), 'by_class': dict(Counter(r['visit_class'] for r in rows))}


def apply(stack_path):
    """Add / refresh visit_group in an existing build's semantic.sqlite3 (additive; nothing else changes)."""
    db, _ = H.connect(stack_path, write=True)
    try:
        summary = build(db)
        db.execute("DELETE FROM meta WHERE key = 'visit_groups'")
        db.execute("INSERT INTO meta VALUES ('visit_groups', ?)", (json.dumps(summary),))
        db.commit()
        return summary
    finally:
        db.close()


def load(db, patient_ids=None, as_of=None):
    """Visit groups for the engines: the stored table when present and current, else computed in memory (stated in 'basis')."""
    if H.has_table(db, TABLE):
        cols = [r[1] for r in db.execute(f'PRAGMA table_info({TABLE})')]
        keep = set(patient_ids) if patient_ids is not None else None
        rows = [dict(zip(cols, r)) for r in db.execute(f'SELECT * FROM {TABLE}')]
        if rows and rows[0].get('version') == VERSION:
            return [r for r in rows if keep is None or r['patient_id'] in keep], f'stored {TABLE} table ({VERSION})'
    rows, _ = compute(db, patient_ids, as_of)
    return rows, f'computed in memory from the source tables ({VERSION}; the build has no current {TABLE} table)'


# ---- engine --------------------------------------------------------------------------------------------------------------
CLASSES = ('emergency', 'ed_to_observation', 'ed_to_inpatient', 'observation', 'observation_to_inpatient', 'direct_inpatient')
GROUP_BY = ('none', 'facility', 'month', 'visit_class', 'disposition', 'admit_reason', 'unit')
DEFAULTS = {'group_by': 'visit_class', 'visit_classes': [], 'facility': None, 'start': None, 'end': None, 'months': None, 'list_visits': False}
PRODUCT = {'population': 'optional', 'view': 'hospital_visits',
           'use': 'HOSPITAL VISITS from the governed visit grouper: ED, observation and inpatient encounters from EHR, ADT, stays and claims '
                  'grouped into one row per visit (ED-to-inpatient, direct admissions, observation), with stage clocks; counts by class, '
                  'facility, month, disposition or admit reason and how many source rows collapsed into each visit.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'group_by': '|'.join(GROUP_BY), 'visit_classes': list(CLASSES), 'facility': None, 'start': None, 'end': None,
                           'months': None, 'list_visits': False},
            'meaning': 'Counts governed hospital visits (visit_group table: overlapping ED / observation / inpatient episodes at one facility '
                       'grouped per patient, overnight ED -> admission joined) by the chosen dimension, with ED-to-inpatient share, median ED '
                       'hours, median LOS and the source rows grouped.'}
NATIVE = ('count', 'value')


def schema(obj, string, array):
    return obj({'group_by': string(GROUP_BY, 'dimension (default visit_class)'),
                'visit_classes': array(string(CLASSES), 'restrict to these visit classes'),
                'facility': {'type': ['string', 'null'], 'description': 'organization_id or "hospital N"'},
                'start': {'type': ['string', 'null'], 'description': 'ISO start date'}, 'end': {'type': ['string', 'null'], 'description': 'ISO end date'},
                'months': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 120, 'description': 'trailing months before the snapshot'},
                'list_visits': {'type': 'boolean', 'description': 'return the visit rows'}})


def validate(p):
    p = dict(p or {})
    if set(p) - set(DEFAULTS): raise ValueError('visit_groups parameters: ' + ', '.join(sorted(DEFAULTS)))
    out = {**DEFAULTS, **{k: v for k, v in p.items() if v is not None}}
    if out['group_by'] not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    if not isinstance(out['visit_classes'], list) or any(c not in CLASSES for c in out['visit_classes']): raise ValueError(f'visit_classes: {CLASSES}')
    if out['months'] is not None and (type(out['months']) is not int or not 1 <= out['months'] <= 120): raise ValueError('months 1..120')
    for k in ('start', 'end'):
        if out[k] is not None and H.d(out[k]) is None: raise ValueError(f'{k}: ISO date')
    out['list_visits'] = bool(out['list_visits'])
    return out


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db, snap = H.connect(path)
    try:
        as_of = str(as_of or snap)[:10]
        rows, basis = load(db, patient_ids, as_of)
        orgs = H.facilities(db)
    finally:
        db.close()
    start, end = H.window(as_of, p['months'], p['start'], p['end'])
    fac = H.resolve_facility(p['facility'], orgs) if p['facility'] else None
    notes = []
    if p['facility'] and not fac: notes.append(f"No facility matches '{p['facility']}'; every facility is counted.")
    sel = [r for r in rows if (not start or r['start_date'] >= start) and r['start_date'] <= end
           and (not p['visit_classes'] or r['visit_class'] in p['visit_classes']) and (not fac or r['organization_id'] == fac)]
    key = {'none': lambda r: 'all', 'facility': lambda r: r['organization_id'] or 'unknown facility', 'month': lambda r: r['start_date'][:7],
           'visit_class': lambda r: r['visit_class'], 'disposition': lambda r: r['disposition'] or 'not recorded',
           'admit_reason': lambda r: r['admit_reason'] or 'not recorded', 'unit': lambda r: r['unit'] or 'not recorded'}[p['group_by']]
    groups = defaultdict(list)
    for r in sel: groups[key(r)].append(r)
    table = []
    for k, rs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        ed = [r['ed_hours'] for r in rs if r['ed_hours'] is not None]
        los = [r['los_days'] for r in rs if r['los_days'] is not None]
        table.append({p['group_by'] if p['group_by'] != 'none' else 'group': k, 'visits': len(rs),
                      'ed_to_inpatient': sum(r['visit_class'] == 'ed_to_inpatient' for r in rs),
                      'inpatient_or_observation': sum(bool(r['has_inpatient'] or r['has_observation']) for r in rs),
                      'still_in_house': sum(1 for r in rs if (r['has_inpatient'] or r['has_observation']) and not r['discharge_known']),
                      'median_ed_hours': None if not ed else round(H.quantile(ed, 0.5), 2), 'timed_ed_visits': len(ed),
                      'median_los_days': None if not los else H.quantile(los, 0.5), 'source_rows_grouped': sum(r['n_members'] for r in rs),
                      'patients': len({r['patient_id'] for r in rs})})
    receipt = {'visits': len(sel), 'source_rows_grouped': sum(r['n_members'] for r in sel),
               'multi_source_visits': sum(1 for r in sel if r['n_members'] > 1), 'transfers_in': sum(r['transfer_in'] for r in sel),
               'by_class': dict(Counter(r['visit_class'] for r in sel)), 'timed': sum(1 for r in sel if r['time_precision'] == 'minute')}
    report = {'schema': VERSION, 'status': 'computed', 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': fac, 'basis': basis,
              'table': table, 'receipt': receipt, 'notes': notes,
              'visits': [{c: r[c] for c in COLUMNS if c not in ('members', 'version')} for r in sel[:500]] if p['list_visits'] else None,
              'patient_ids': sorted({r['patient_id'] for r in sel}),
              'method': ('visit_group: governed ED visits (EHR+ADT+claims) + EHR hospital encounters + ADT visits (A04/A06/A01/A03 folded per visit '
                         'number) + distinct stays, grouped per patient by overlapping dates at a compatible facility; overnight ED -> admission '
                         'joins; different facility within a day = transfer.'),
              'assumptions': ['A visit seen only in claims has a date but no clock; ED hours and LOS hours use only clocked sources.',
                              'ED departure for admitted patients (bed assigned / left ED) is not received; ED hours for admitted visits end at the '
                              'ADT class change (A06), i.e. the admit decision.',
                              'Payer-claim stays carry dates only; a stay with no discharge by the snapshot counts as still in house.']}
    return report


def headline(r):
    rc = r['receipt']
    win = f"{r['window'][0] or 'start of data'} to {r['window'][1]}"
    top = ', '.join(f"{list(t.values())[0]} {t['visits']}" for t in r['table'][:5])
    return (f"{rc['visits']} hospital visits ({win}{', ' + r['facility'] if r['facility'] else ''}) grouped from {rc['source_rows_grouped']} source "
            f"rows ({rc['multi_source_visits']} visits seen in more than one source); by {r['parameters']['group_by'].replace('_', ' ')}: {top}")


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    out = {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['table'], 'count': report['receipt']['visits'],
           'patient_ids': report['patient_ids'], 'value': headline(report) + '.', 'method': report['method'],
           'limitations': report['assumptions'] + report['notes'] + [f"Source: {report['basis']}."],
           'overall': {'visit_groups': report['receipt']}}
    if report.get('visits'): out['overall']['visits'] = report['visits']
    return out


# ---- free local-compiler route ----------------------------------------------------------------------------------------
VISIT = (r'\bhospital visits?\b|\bed[- ]to[- ](?:inpatient|admission|admit)\b|\badmitted (?:through|via|from) the (?:ed|emergency)\b|'
         r'\bvisit grouper\b|\bgroup(?:ed|ing)? (?:the )?encounters? into visits\b|\bencounters? (?:grouped|rolled up) (?:into|to) visits\b|'
         r'\bdirect admissions?\b|\bed[- ]to[- ]observation\b|\bhospital encounters? (?:as|into) visits\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(VISIT, low): return None
    if H.is_definition(low): return None
    params = {}
    if re.search(r'\bby (?:facility|hospital|site)\b|\bper (?:facility|hospital)\b|\beach (?:facility|hospital)\b', low): params['group_by'] = 'facility'
    elif re.search(r'\bby month\b|\bmonthly\b|\bper month\b|\beach month\b', low): params['group_by'] = 'month'
    elif re.search(r'\bby disposition\b|\bdischarged to\b', low): params['group_by'] = 'disposition'
    elif re.search(r'\bby (?:admit(?:ting)? )?reason\b', low): params['group_by'] = 'admit_reason'
    elif re.search(r'\bby unit\b', low): params['group_by'] = 'unit'
    classes = []
    if re.search(r'\bed[- ]to[- ](?:inpatient|admission|admit)\b|\badmitted (?:through|via|from) the', low): classes.append('ed_to_inpatient')
    if re.search(r'\bdirect admissions?\b', low): classes.append('direct_inpatient')
    if re.search(r'\bed[- ]to[- ]observation\b', low): classes.append('ed_to_observation')
    if classes: params['visit_classes'] = classes
    m = re.search(r'\b(?:last|past|previous) (\d{1,3}) months?\b', low)
    if m and 1 <= int(m.group(1)) <= 120: params['months'] = int(m.group(1))
    elif re.search(r'\b(?:last|past) year\b|\blast 12 months\b', low): params['months'] = 12
    yr = re.search(r'\bin (20\d\d)\b', low)
    if yr: params.update(start=f'{yr.group(1)}-01-01', end=f'{yr.group(1)}-12-31')
    fac = re.search(r'\b(hospital[\s-]*\d+|wh[n]?-hospital-[\w-]+)\b', low)
    if fac: params['facility'] = fac.group(1)
    if re.search(r'\blist\b|\bshow (?:me )?(?:the|each|all) visits\b|\bwhich visits\b', low): params['list_visits'] = True
    return plan(OP, params, 'Governed hospital visits from the visit grouper (EHR + ADT + stays + claims, one row per visit)', question,
                ['group_by ' + params.get('group_by', 'visit_class')])


TEMPLATES = ['how many hospital visits did we have by facility in the last 12 months',
             'count ED-to-inpatient visits by month',
             'group the encounters into visits and show how many were direct admissions',
             'hospital visits by disposition this year',
             'how many patients were admitted through the emergency department at hospital 2',
             'list the ED-to-observation visits in 2029',
             'visit grouper: hospital visits by admit reason']
