"""bundle_compliance: SEP-1 style all-or-none sepsis bundle compliance with element-level failure reasons (wave 2).

Cohort = hospital visits (ED or inpatient) with a sepsis / severe-sepsis diagnosis (A40, A41, R65.2x) dated inside the visit. Time zero is
the presentation clock (ED arrival, else inpatient admit, else the visit's first date at 00:00, flagged date-level). Elements are evaluated
from the received data and marked not_evaluable when the source is genuinely absent (blood cultures, IV fluid volumes, vasopressors are
not received); a case is compliant when every evaluable, applicable element is met, and the answer says which elements could not be judged.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import timedelta

from . import hospital_ops as H

OP = 'bundle_compliance'
VERSION = 'bundle-compliance/1'
SEPSIS_ICD = ('A40', 'A41', 'R65.2')
LACTATE_LOINC = ('2524-7', '32693-4', '2519-7')
ELEMENTS = {
    'initial_lactate': 'lactate measured within 3 hours of time zero (from 6 hours before)',
    'blood_culture_before_antibiotic': 'blood culture drawn before the first antibiotic (microbiology not received)',
    'broad_spectrum_antibiotic': 'broad-spectrum antibacterial given within 3 hours of time zero (from 24 hours before)',
    'repeat_lactate': 'repeat lactate within 6 hours of time zero when the initial lactate is above 2 mmol/L',
    'crystalloid_fluids': 'crystalloid volume when the initial lactate is 4 mmol/L or higher or systolic pressure is below 90 (IV fluid volumes not received)',
}
GROUPS = ('none', 'facility', 'month')
DEFAULTS = {'facility': None, 'months': None, 'start': None, 'end': None, 'group_by': 'none', 'lactate_window_hours': 3.0, 'antibiotic_window_hours': 3.0,
            'list_cases': False}
SPEC = {'facility': ('str',), 'months': ('int', 1, 120), 'start': ('date',), 'end': ('date',), 'group_by': ('enum', GROUPS),
        'lactate_window_hours': ('num', 0.5, 24), 'antibiotic_window_hours': ('num', 0.5, 24), 'list_cases': ('bool',)}
PRODUCT = {'population': 'optional', 'view': 'bundle_compliance',
           'use': 'CARE-BUNDLE COMPLIANCE (SEP-1 sepsis bundle): all-or-none compliance for sepsis visits with element-level results and failure reasons - lactate, '
                  'repeat lactate, broad-spectrum antibiotic within 3 hours of time zero, cultures and fluids where data exist - by facility or month.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'facility': None, 'months': None, 'group_by': '|'.join(GROUPS), 'lactate_window_hours': 3, 'antibiotic_window_hours': 3,
                           'list_cases': False, 'start': None, 'end': None},
            'meaning': 'Time-anchored all-or-none SEP-1 evaluation for sepsis visits: per-element applicable / met / failed / not evaluable counts, case-level failure '
                       'reasons, overall compliance among cases whose evaluable elements all passed.'}
NATIVE = ('count', 'value')


def schema(obj, string, array): return H.spec_schema(SPEC, obj, string, array)
def validate(p): return H.spec_validate(OP, DEFAULTS, SPEC, p)


def _num(x):
    try: return float(str(x).replace('<', '').replace('>', ''))
    except (TypeError, ValueError): return None


def _clock(day, time):
    return H.ts(time, day), bool(time) and H.timed(time)


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db, snap = H.connect(path)
    try:
        as_of = str(as_of or snap)[:10]
        keep = set(patient_ids) if patient_ids is not None else None
        rows, basis, (start, end), fac, notes = H.visits_in(db, as_of, p, patient_ids, need=lambda r: r['has_inpatient'] or r['has_ed'])
        dx = H.by_patient([{'patient_id': a, 'icd10': b, 'day': c} for a, b, c in H.dx_rows(db, SEPSIS_ICD, keep)])
        sepsis = []
        for v in rows:
            hits = [x for x in dx.get(v['patient_id'], ()) if v['start_date'] <= x['day'] <= (v['end_date'] if v['end_date'] and v['discharge_known'] else as_of)[:10]]
            if hits: sepsis.append((v, sorted(hits, key=lambda x: x['day'])[0]))
        pids = {v['patient_id'] for v, _ in sepsis}
        lact = defaultdict(list)
        if pids:
            q = ','.join('?' * len(LACTATE_LOINC))
            for pid, day, tm, val in db.execute(f'SELECT patient_id, event_date, event_time, value FROM measurement WHERE loinc IN ({q}) AND event_date IS NOT NULL', LACTATE_LOINC):
                if pid in pids:
                    t, timed = _clock(day, tm); lact[pid].append({'t': t, 'timed': timed, 'v': _num(val), 'day': str(day)[:10]})
            sbp = defaultdict(list)
            for pid, day, val in db.execute("SELECT patient_id, event_date, value FROM measurement WHERE name IN ('bp_systolic','sbp','Systolic blood pressure') AND event_date IS NOT NULL"):
                if pid in pids: sbp[pid].append((str(day)[:10], _num(val)))
        else:
            sbp = defaultdict(list)
        mar = defaultdict(list)
        for r in H.stream(db, 'medication_administrations', pids):
            if (r.get('administration_status') or '').lower() in ('given', 'administered', 'completed') and H.is_antibacterial(r.get('medication_name') or '', None):
                t, timed = _clock(r.get('event_date'), r.get('event_time'))
                mar[r['patient_id']].append({'t': t, 'timed': timed, 'spec': H.spectrum(r.get('medication_name'), None), 'day': str(r.get('event_date'))[:10]})
    finally:
        db.close()
    lw, aw = p['lactate_window_hours'], p['antibiotic_window_hours']
    cases, elem = [], {e: Counter() for e in ELEMENTS}
    reasons = Counter()
    for v, dxr in sepsis:
        t0 = H.ts(v['ed_arrival'] or v['inpatient_admit']) if (v['ed_arrival'] or v['inpatient_admit']) else None
        date_level = t0 is None
        t0 = t0 or H.ts(None, v['start_date'])
        t0d = t0.date().isoformat()
        def within(ev, before, after):
            """event within [t0-before, t0+after]; date-level events/time-zero match on calendar day."""
            if ev['t'] is None: return False
            if ev['timed'] and not date_level: return t0 - timedelta(hours=before) <= ev['t'] <= t0 + timedelta(hours=after)
            return abs((ev['t'].date() - t0.date()).days) <= (1 if before >= 24 else 0)
        res, why = {}, []
        ls = sorted([x for x in lact.get(v['patient_id'], ()) if within(x, 6, lw)], key=lambda x: x['t'])
        res['initial_lactate'] = 'met' if ls else 'failed'
        if not ls: why.append('no lactate within the window')
        ab = [x for x in mar.get(v['patient_id'], ()) if x['spec'] != 'narrow_or_community' and within(x, 24, aw)]
        res['broad_spectrum_antibiotic'] = 'met' if ab else 'failed'
        if not ab: why.append('no broad-spectrum antibacterial given within the window')
        res['blood_culture_before_antibiotic'] = 'not_evaluable'
        first_lac = ls[0]['v'] if ls else None
        if first_lac is not None and first_lac > 2:
            later = [x for x in lact.get(v['patient_id'], ()) if x['t'] is not None and x['t'] > ls[0]['t'] and within(x, 0, 6)]
            res['repeat_lactate'] = 'met' if later else 'failed'
            if not later: why.append(f'initial lactate {first_lac:g} above 2 with no repeat within 6 hours')
        else:
            res['repeat_lactate'] = 'not_applicable'
        hypo = any(a is not None and a < 90 and abs((H.d(d) - H.d(t0d)).days) <= 1 for d, a in sbp.get(v['patient_id'], ()))
        res['crystalloid_fluids'] = 'not_evaluable' if ((first_lac is not None and first_lac >= 4) or hypo) else 'not_applicable'
        for e, s in res.items(): elem[e][s] += 1
        failed = [e for e, s in res.items() if s == 'failed']
        compliant = not failed
        for w in why: reasons[w.split(' initial')[0] if w.startswith('initial') else w] += 1
        cases.append({'visit_group_id': v['visit_group_id'], 'patient_id': v['patient_id'], 'facility': v['organization_id'] or 'unknown facility', 'month': v['start_date'][:7],
                      'time_zero': H.iso(t0), 'time_zero_precision': 'date' if date_level else 'minute', 'sepsis_icd10': dxr['icd10'], 'elements': res,
                      'compliant_on_evaluable': compliant, 'failure_reasons': why})
    gk = {'none': lambda c: 'all', 'facility': lambda c: c['facility'], 'month': lambda c: c['month']}[p['group_by']]
    g = defaultdict(list)
    for c in cases: g[gk(c)].append(c)
    table = []
    for k, cs in sorted(g.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        ok = sum(c['compliant_on_evaluable'] for c in cs)
        row = {p['group_by'] if p['group_by'] != 'none' else 'group': k, 'sepsis_cases': len(cs), 'compliant_on_evaluable_elements': ok,
               'compliance_rate': round(ok / len(cs), 3)}
        for e in ELEMENTS:
            ap = [c['elements'][e] for c in cs if c['elements'][e] in ('met', 'failed')]
            row[f'{e}_met'] = f"{sum(1 for x in ap if x == 'met')}/{len(ap)}" if ap else 'n/a'
        table.append(row)
    el = [{'element': e, 'definition': d, **{k: elem[e].get(k, 0) for k in ('met', 'failed', 'not_evaluable', 'not_applicable')}} for e, d in ELEMENTS.items()]
    ok_all = sum(c['compliant_on_evaluable'] for c in cases)
    receipt = {'count': len(cases), 'sepsis_cases': len(cases), 'compliant_on_evaluable_elements': ok_all,
               'compliance_rate': None if not cases else round(ok_all / len(cases), 3), 'top_failure_reasons': dict(reasons.most_common(4)),
               'date_level_time_zero': sum(1 for c in cases if c['time_zero_precision'] == 'date'),
               'cohort_lactate_results': sum(len(v) for v in lact.values()), 'cohort_antibacterial_doses': sum(len(v) for v in mar.values())}
    if cases and not receipt['cohort_lactate_results']: notes.append('No lactate result is on file for any patient in this sepsis cohort, so the lactate elements fail by absence of data, not by a late result.')
    if cases and not receipt['cohort_antibacterial_doses']: notes.append('No administered antibacterial dose is on file for any patient in this sepsis cohort.')
    return {'schema': VERSION, 'status': 'computed' if cases else 'unknown', 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': fac, 'basis': basis,
            'table': table, 'detail': el, 'cases': cases[:500] if p['list_cases'] else None, 'receipt': receipt,
            'notes': notes + ([] if cases else ['No sepsis-coded hospital visits in the window.']), 'patient_ids': sorted(pids),
            'method': ('Sepsis visits = A40 / A41 / R65.2x diagnoses dated inside a governed visit (ED or inpatient); time zero = ED arrival or inpatient admit clock '
                       '(visit start date at 00:00 when only dates exist); all-or-none over the applicable, evaluable elements; failure reasons are listed per case.'),
            'assumptions': ['Blood-culture, IV-fluid-volume and vasopressor sources are not received, so those elements are reported as not_evaluable (never as passed or failed); '
                            'compliance is therefore "all evaluable elements met", not full CMS SEP-1 compliance.',
                            'Time zero is the presentation clock, not the documented severe-sepsis recognition time, which is not received.',
                            'Date-only events match on the calendar day of time zero (the day before for antibiotics given before arrival).',
                            'Hypotension for the fluid element uses systolic pressure below 90 on the day of time zero or adjacent days (vitals are mostly un-timed).']}


def headline(r):
    rc = r['receipt']
    ne = [e['element'].replace('_', ' ') for e in r['detail'] if e['not_evaluable']]
    txt = (f"{rc['sepsis_cases']} sepsis visits; {rc['compliant_on_evaluable_elements']} met every evaluable bundle element "
           f"({rc['compliance_rate']:.0%})" if rc['sepsis_cases'] else 'No sepsis visits')
    if rc['top_failure_reasons']: txt += '; main failures: ' + '; '.join(f'{k} ({n})' for k, n in rc['top_failure_reasons'].items())
    if ne: txt += '; not evaluable (data not received): ' + ', '.join(ne)
    return txt


def answer(report, plan, question):
    if report['status'] != 'computed':
        return {'status': 'unknown', 'answer_type': 'text', 'value': ' '.join(report['notes']), 'method': report['method'], 'limitations': []}
    return H.generic_answer(OP, report, plan, headline(report) + '.')


SEP = (r'\b(?:sepsis .{0,40}bundle|bundle .{0,40}sepsis|3[- ]hour bundle|sep[- ]?1|sepsis bundle|sepsis (?:bundle )?compliance|bundle compliance|care bundle|all[- ]or[- ]none bundle|hour[- ]?[13] bundle|'
       r'lactate (?:within|measured) .{0,20}hours|time[- ]zero|antibiotics? within (?:1|3|one|three) hours? .{0,25}sepsis|sepsis .{0,30}(?:lactate|antibiotics? within))\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(SEP, low) or H.is_definition(low): return None
    params = H.common_params(low)
    if re.search(r'\bby (?:facility|hospital|site)\b|\bper (?:facility|hospital)\b', low): params['group_by'] = 'facility'
    elif re.search(r'\bby month\b|\bmonthly\b|\btrend\b', low): params['group_by'] = 'month'
    if re.search(r'\blist\b|\bwhich (?:cases|patients)\b|\bshow (?:me )?(?:the )?cases\b', low): params['list_cases'] = True
    return plan(OP, params, 'SEP-1 sepsis bundle compliance with element-level failure reasons', question, ['bundle compliance'])


TEMPLATES = ['what is our SEP-1 sepsis bundle compliance by facility',
             'which sepsis cases failed the 3-hour bundle and why',
             'sepsis bundle compliance by month for the last 12 months',
             'how often do we get a lactate and broad-spectrum antibiotics within 3 hours of time zero',
             'care bundle compliance at hospital 2']
