"""antimicrobial_use: days of therapy (DOT) per 1,000 days present, spectrum mix, IV-to-oral switch, de-escalation and O/E by facility (wave 2).

Source = administered antibacterial doses of the medication-administration (MAR) stream with status given, attributed to inpatient /
observation visits of the governed visit_group table. DOT = distinct (patient, drug, calendar day); days present = midnight patient-days
of the same visits. The NHSN SAAR national baseline is not received: observed/expected uses the pooled rate of this same data.
"""
from __future__ import annotations

import re
from collections import defaultdict

from . import hospital_ops as H

OP = 'antimicrobial_use'
VERSION = 'antimicrobial-use/1'
GROUPS = ('none', 'spectrum', 'drug', 'facility', 'month', 'route')
RANK = {'narrow_or_community': 1, 'broad_community': 2, 'broad_hospital': 3}
DEFAULTS = {'facility': None, 'months': None, 'start': None, 'end': None, 'group_by': 'spectrum', 'drugs': [], 'stewardship': True}
SPEC = {'facility': ('str',), 'months': ('int', 1, 120), 'start': ('date',), 'end': ('date',), 'group_by': ('enum', GROUPS),
        'drugs': ('list_enum', ()), 'stewardship': ('bool',)}
PRODUCT = {'population': 'optional', 'view': 'antimicrobial_use',
           'use': 'ANTIMICROBIAL USE / STEWARDSHIP: days of therapy per 1,000 days present from administered doses, by drug, spectrum, facility, month or route, '
                  'IV-to-oral switch, de-escalation vs escalation within a stay, observed vs expected by facility.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'facility': None, 'months': None, 'group_by': '|'.join(GROUPS), 'drugs': [], 'stewardship': True, 'start': None, 'end': None},
            'meaning': 'Administered antibacterial days of therapy (MAR, status given) inside inpatient / observation visits per 1,000 days present with spectrum, '
                       'route and per-stay IV-to-oral and de-escalation measures; O/E vs the pooled rate.'}
NATIVE = ('count', 'value')


def schema(obj, string, array):
    s = H.spec_schema({k: v for k, v in SPEC.items() if k != 'drugs'}, obj, string, array)
    s['properties']['drugs'] = array({'type': 'string'}, 'drug name fragments, e.g. ceftriaxone')
    return s


def validate(p):
    out = H.spec_validate(OP, DEFAULTS, {k: v for k, v in SPEC.items() if k != 'drugs'}, {k: v for k, v in (p or {}).items() if k != 'drugs'})
    drugs = (p or {}).get('drugs') or []
    if not isinstance(drugs, list) or any(not isinstance(x, str) for x in drugs): raise ValueError('drugs: list of names')
    out['drugs'] = [x.lower() for x in drugs]
    return out


def base_name(name):
    n = (name or '').lower()
    n = re.split(r'\s\d', n)[0]
    return n.strip(' ,') or 'unknown'


def doses(db, keep):
    out = []
    for r in H.stream(db, 'medication_administrations', keep):
        if (r.get('administration_status') or '').lower() not in ('given', 'administered', 'completed'): continue
        name = r.get('medication_name') or ''
        if not H.is_antibacterial(name, None): continue
        day = H.d(r.get('event_time') or r.get('event_date'))
        if day is None: continue
        out.append({'patient_id': r['patient_id'], 'drug': base_name(name), 'day': day, 'route': (r.get('route') or 'unknown').lower(),
                    'spectrum': H.spectrum(name, None), 'org': r.get('organization_id')})
    return out


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db, snap = H.connect(path)
    try:
        as_of = str(as_of or snap)[:10]
        keep = set(patient_ids) if patient_ids is not None else None
        rows, basis, (start, end), fac, notes = H.visits_in(db, as_of, p, patient_ids, need=lambda r: r['has_inpatient'] or r['has_observation'])
        dd = doses(db, keep)
    finally:
        db.close()
    by = H.by_patient(rows)
    day_rows = {}  # (visit, drug, day) -> record: distinct days of therapy
    for x in dd:
        if p['drugs'] and not any(f in x['drug'] for f in p['drugs']): continue
        for v in by.get(x['patient_id'], ()):
            a, b = H.d(v['start_date']), H.stay_end(v, as_of)
            if a and b and a <= x['day'] <= b:
                day_rows.setdefault((v['visit_group_id'], x['drug'], x['day']), {**x, 'visit': v}); break
    present = defaultdict(float); total_present = 0.0
    vorg = {}
    for x in day_rows.values(): vorg.setdefault(x['visit']['visit_group_id'], x['org'])
    key = {'none': lambda x: 'all', 'spectrum': lambda x: x['spectrum'], 'drug': lambda x: x['drug'], 'route': lambda x: x['route'],
           'facility': lambda x: x['visit']['organization_id'] or x['org'] or 'unknown facility', 'month': lambda x: x['day'].isoformat()[:7]}[p['group_by']]
    vkey = {'none': lambda v: 'all', 'facility': lambda v: v['organization_id'] or vorg.get(v['visit_group_id']) or 'unknown facility', 'month': lambda v: v['start_date'][:7]}
    for v in rows:
        pdays = H.patient_days(v, as_of); total_present += pdays
        present[vkey.get(p['group_by'], lambda v: 'all')(v)] += pdays
    dot = defaultdict(int)
    for x in day_rows.values(): dot[key(x)] += 1
    total_dot = sum(dot.values())
    base_dot, base_present = total_dot, total_present
    if p['group_by'] == 'facility':  # pooled benchmark over facilities that are known, so O/E compares like with like
        base_dot = sum(n for g, n in dot.items() if g != 'unknown facility'); base_present = sum(v for g, v in present.items() if g != 'unknown facility')
        if present.get('unknown facility'): notes.append(f"{int(present['unknown facility'])} days present sit in visits with no facility received (shown as 'unknown facility').")
    table = []
    for g, n in sorted(dot.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        den = present.get(g) if p['group_by'] in ('facility', 'month') else total_present
        if p['group_by'] == 'facility' and not den: den = None
        exp = (base_dot / base_present * den) if den and base_present and g != 'unknown facility' else None
        table.append({p['group_by'] if p['group_by'] != 'none' else 'group': g, 'days_of_therapy': n, 'days_present': int(den) if den else None,
                      'dot_per_1000_days_present': None if not den else round(n / den * 1000, 1),
                      'share_of_dot': round(n / total_dot, 3) if total_dot else None,
                      'observed_over_expected': None if not exp or p['group_by'] not in ('facility', 'month') else round(n / exp, 2)})
    detail = None
    if p['stewardship']:
        per = defaultdict(list)
        for x in day_rows.values(): per[x['visit']['visit_group_id']].append(x)
        iv_stays = switched = deesc = esc = same = judged = 0; switch_days = []
        for vid, xs in per.items():
            xs.sort(key=lambda x: x['day']); d0 = xs[0]['day']
            ivd = [x['day'] for x in xs if x['route'].startswith('intra')]; oral = [x['day'] for x in xs if x['route'] in ('oral', 'po')]
            if len({d for d in ivd}) >= 1:
                iv_stays += 1
                later = [d for d in oral if d > min(ivd)]
                if later: switched += 1; switch_days.append((min(later) - d0).days + 1)
            early = [RANK[x['spectrum']] for x in xs if (x['day'] - d0).days < 2]; late = [RANK[x['spectrum']] for x in xs if (x['day'] - d0).days >= 2]
            if early and late:
                judged += 1
                if max(late) < max(early): deesc += 1
                elif max(late) > max(early): esc += 1
                else: same += 1
        detail = {'stays_with_antibacterials': len(per), 'stays_on_iv': iv_stays, 'iv_to_oral_switch': switched,
                  'iv_to_oral_share': None if not iv_stays else round(switched / iv_stays, 3),
                  'median_day_of_switch': None if not switch_days else H.quantile(switch_days, 0.5), 'stays_with_day3_judgement': judged,
                  'de_escalated': deesc, 'escalated': esc, 'unchanged_spectrum': same}
    receipt = {'count': total_dot, 'days_of_therapy': total_dot, 'days_present': int(total_present),
               'dot_per_1000_days_present': None if not total_present else round(total_dot / total_present * 1000, 1), 'stays': len({k[0] for k in day_rows}),
               'stewardship': detail}
    return {'schema': VERSION, 'status': 'computed', 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': fac, 'basis': basis,
            'table': table, 'detail': [detail] if detail else None, 'receipt': receipt, 'notes': notes,
            'patient_ids': sorted({x['patient_id'] for x in day_rows.values()}),
            'method': ('Given antibacterial doses of the MAR stream, one DOT per patient-drug-calendar day, kept when the day falls inside an inpatient / observation visit; '
                       'days present = midnight patient-days of the same visits; spectrum = NHSN-style groups from drug name; de-escalation = highest spectrum rank on '
                       'day 3+ below days 1-2 within a stay.'),
            'assumptions': ['The SAAR national benchmark is not received; observed/expected compares with the pooled rate of this data.',
                            'Doses not documented in the MAR stream (outside-hospital, ED-only) are not counted; ED doses before the inpatient visit fall in the visit span '
                            'only when the grouped visit starts at ED arrival.',
                            'A stay open at the snapshot contributes days present up to the snapshot.']}


def headline(r):
    rc = r['receipt']; st = rc.get('stewardship') or {}
    top = ', '.join(f"{list(t.values())[0]} {t['dot_per_1000_days_present'] if t['dot_per_1000_days_present'] is not None else t['days_of_therapy']}" for t in r['table'][:4])
    txt = f"{rc['days_of_therapy']:,} antibacterial days of therapy over {rc['days_present']:,} days present = {rc['dot_per_1000_days_present']} DOT per 1,000 days present ({top})"
    if st.get('stays_on_iv'): txt += f"; {st['iv_to_oral_switch']} of {st['stays_on_iv']} IV stays switched to oral, {st['de_escalated']} de-escalated and {st['escalated']} escalated at day 3"
    return txt


def answer(report, plan, question):
    return H.generic_answer(OP, report, plan, headline(report) + '.')


AMU = (r'\b(?:days of therapy|\bdot\b per 1,?000|antimicrobial (?:use|stewardship|consumption|utilization)|antibiotic (?:use|days|utilization|stewardship|consumption)|'
       r'iv[- ]to[- ]oral|switch\w* to oral|iv antibiotics?|de[- ]?escalat\w*|saar|stewardship|broad[- ]spectrum (?:use|days)|antibiotic spectrum)\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(AMU, low) or H.is_definition(low): return None
    params = H.common_params(low)
    if re.search(r'\bby (?:facility|hospital|site)\b|\bper (?:facility|hospital)\b', low): params['group_by'] = 'facility'
    elif re.search(r'\bby month\b|\bmonthly\b|\btrend\b', low): params['group_by'] = 'month'
    elif re.search(r'\bby (?:drug|agent|antibiotic)\b', low): params['group_by'] = 'drug'
    elif re.search(r'\bby route\b', low): params['group_by'] = 'route'
    elif re.search(r'\bby spectrum\b|\bbroad[- ]spectrum\b', low): params['group_by'] = 'spectrum'
    names = [n for n in ('ceftriaxone', 'vancomycin', 'cefepime', 'piperacillin', 'meropenem', 'azithromycin', 'ciprofloxacin', 'levofloxacin', 'doxycycline', 'amoxicillin')
             if re.search(rf'\b{n}\b', low)]
    if names: params['drugs'] = names
    return plan(OP, params, 'Antimicrobial days of therapy per 1,000 days present with stewardship measures', question, ['antimicrobial use'])


TEMPLATES = ['antibiotic days of therapy per 1,000 patient days by facility',
             'what share of IV antibiotic stays switched to oral',
             'ceftriaxone days of therapy per 1,000 days present by month',
             'how often do we de-escalate broad-spectrum antibiotics by day 3',
             'antimicrobial stewardship summary for the last 12 months']
