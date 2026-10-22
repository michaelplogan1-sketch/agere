"""hai_surveillance: hospital-onset infection surveillance with the NHSN day-3 window, rates per 1,000 patient-days and SPC (wave 2).

Case = a coded infection diagnosis on hospital day 3 or later of an inpatient visit (day 1 = admission date), same infection in the same
patient counted once per 14-day repeat-infection window. Rates use patient-days; device-days, microbiology and the NHSN predicted
count (SIR) are not received, so the engine reports rate with a Poisson interval, an observed/expected ratio against the pooled rate of
the same data (internal, not an NHSN SIR) and a u-chart on the monthly rate.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import timedelta

from . import hospital_ops as H

OP = 'hai_surveillance'
VERSION = 'hai-surveillance/1'
INFECTIONS = {  # name -> (ICD-10 prefixes, what the code stands for here)
    'cauti': (('N39.0',), 'catheter-associated UTI proxy: urinary tract infection code (catheter status is not received)'),
    'bloodstream': (('A41', 'A40', 'T80.211'), 'bloodstream infection / sepsis codes (CLABSI proxy: central-line status is not received)'),
    'ssi': (('T81.4',), 'surgical site infection codes'),
    'cdi': (('A04.7',), 'Clostridioides difficile enterocolitis'),
    'vap': (('J95.851',), 'ventilator-associated pneumonia code'),
    'hap': (('J18', 'J15'), 'hospital-acquired pneumonia proxy: pneumonia codes (ventilation status is not received)'),
}
GROUPS = ('infection', 'facility', 'month')
DEFAULTS = {'infections': [], 'facility': None, 'months': None, 'start': None, 'end': None, 'group_by': 'infection', 'onset_day': 3,
            'repeat_window_days': 14, 'list_cases': False}
SPEC = {'infections': ('list_enum', tuple(INFECTIONS)), 'facility': ('str',), 'months': ('int', 1, 120), 'start': ('date',), 'end': ('date',),
        'group_by': ('enum', GROUPS), 'onset_day': ('int', 2, 14), 'repeat_window_days': ('int', 1, 90), 'list_cases': ('bool',)}
PRODUCT = {'population': 'optional', 'view': 'hai_surveillance',
           'use': 'HOSPITAL-ACQUIRED INFECTION SURVEILLANCE: hospital-onset CAUTI, bloodstream, SSI, C. difficile and pneumonia cases (NHSN day-3 window, '
                  'repeat-infection dedupe) per 1,000 patient-days with Poisson intervals, observed vs expected by facility and monthly SPC control limits.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'infections': list(INFECTIONS), 'facility': None, 'months': None, 'group_by': '|'.join(GROUPS), 'onset_day': 3,
                           'repeat_window_days': 14, 'list_cases': False, 'start': None, 'end': None},
            'meaning': 'Hospital-onset infections from coded diagnoses on inpatient visits (onset on hospital day >= onset_day), deduplicated in a repeat window, '
                       'rates per 1,000 patient-days (Poisson 95% interval), observed / expected vs the pooled rate, u-chart outliers by month.'}
NATIVE = ('count', 'value')


def schema(obj, string, array): return H.spec_schema(SPEC, obj, string, array)
def validate(p): return H.spec_validate(OP, DEFAULTS, SPEC, p)


def cases(db, visits, as_of, p, keep):
    """Hospital-onset cases -> list of dicts (deduplicated)."""
    names = p['infections'] or list(INFECTIONS)
    inpatient = H.by_patient([v for v in visits if v['has_inpatient']])
    found, seen = [], {}
    for name in names:
        for pid, code, day in sorted(H.dx_rows(db, INFECTIONS[name][0], keep), key=lambda x: (x[0], x[2])):
            dd = H.d(day)
            for v in inpatient.get(pid, ()):
                a = H.d((v['inpatient_admit'] or v['start_date'])[:10]); b = H.stay_end(v, as_of)
                if a is None or b is None or not (a <= dd <= b + timedelta(1)): continue
                hday = (dd - a).days + 1
                key = (pid, name)
                if key in seen and (dd - seen[key]).days <= p['repeat_window_days']: break
                seen[key] = dd
                found.append({'patient_id': pid, 'infection': name, 'icd10': code, 'onset_date': day, 'hospital_day': hday, 'visit_group_id': v['visit_group_id'],
                              'facility': v['organization_id'] or 'unknown facility', 'month': day[:7], 'hospital_onset': hday >= p['onset_day']})
                break
    return found


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db, snap = H.connect(path)
    try:
        as_of = str(as_of or snap)[:10]
        keep = set(patient_ids) if patient_ids is not None else None
        rows, basis, (start, end), fac, notes = H.visits_in(db, as_of, p, patient_ids)
        every, *_ = H.visits_in(db, as_of, {k: v for k, v in p.items() if k not in ('months', 'start', 'end')}, patient_ids)
        allc = cases(db, every, as_of, p, keep)
    finally:
        db.close()
    inpt = [v for v in rows if v['has_inpatient']]
    ids = {v['visit_group_id'] for v in inpt}
    hit = [c for c in allc if c['visit_group_id'] in ids and (not start or c['onset_date'] >= start) and c['onset_date'] <= end]
    ho = [c for c in hit if c['hospital_onset']]
    gk = {'infection': lambda c: c['infection'], 'facility': lambda c: c['facility'], 'month': lambda c: c['month']}[p['group_by']]
    days = defaultdict(float); pooled_days = 0.0
    for v in inpt:
        g = {'infection': 'all', 'facility': v['organization_id'] or 'unknown facility', 'month': v['start_date'][:7]}[p['group_by']]
        days[g] += H.patient_days(v, as_of); pooled_days += H.patient_days(v, as_of)
    names = p['infections'] or list(INFECTIONS)
    pooled = defaultdict(int)
    for c in ho: pooled[c['infection']] += 1
    table = []
    if p['group_by'] == 'infection':
        for n in names:
            k = pooled.get(n, 0); r, lo, hi = H.poisson_ci(k, pooled_days)
            table.append({'infection': n, 'hospital_onset_cases': k, 'present_on_admission_or_early': sum(1 for c in hit if c['infection'] == n and not c['hospital_onset']),
                          'patient_days': int(pooled_days), 'rate_per_1000_patient_days': r, 'ci95_low': lo, 'ci95_high': hi, 'definition': INFECTIONS[n][1]})
    else:
        groups = defaultdict(int)
        for c in ho: groups[gk(c)] += 1
        total_k = sum(groups.values()); total_pd = sum(days.values()) or 1
        for g in sorted(set(groups) | set(days), key=lambda x: (-groups.get(x, 0), str(x)) if p['group_by'] == 'facility' else str(x)):
            k, pd_ = groups.get(g, 0), days.get(g, 0)
            if not pd_: continue
            r, lo, hi = H.poisson_ci(k, pd_); exp = total_k / total_pd * pd_
            table.append({p['group_by']: g, 'hospital_onset_cases': k, 'patient_days': int(pd_), 'rate_per_1000_patient_days': r, 'ci95_low': lo, 'ci95_high': hi,
                          'expected_at_pooled_rate': round(exp, 2), 'observed_over_expected': None if not exp else round(k / exp, 2)})
    # u-chart on the monthly rate for the selected infections
    mdays, mk = defaultdict(float), defaultdict(int)
    for v in inpt: mdays[v['start_date'][:7]] += H.patient_days(v, as_of)
    for c in ho: mk[c['month']] += 1
    cl = sum(mk.values()) / (sum(mdays.values()) or 1) * 1000
    spc = []
    for m in sorted(mdays):
        n = mdays[m] / 1000.0; rate = mk.get(m, 0) / n if n else 0
        ucl = cl + 3 * math.sqrt(cl / n) if n else None; lcl = max(cl - 3 * math.sqrt(cl / n), 0) if n else None
        spc.append({'month': m, 'cases': mk.get(m, 0), 'patient_days': int(mdays[m]), 'rate': round(rate, 3), 'center': round(cl, 3), 'ucl': None if ucl is None else round(ucl, 3),
                    'signal': 'above_ucl' if ucl is not None and rate > ucl and mk.get(m, 0) > 0 else None})
    receipt = {'count': len(ho), 'hospital_onset_cases': len(ho), 'early_onset_or_present_on_admission': len(hit) - len(ho), 'patient_days': int(pooled_days),
               'rate_per_1000_patient_days': H.poisson_ci(len(ho), pooled_days)[0], 'spc_signal_months': [s['month'] for s in spc if s['signal']][:12],
               'onset_day': p['onset_day']}
    return {'schema': VERSION, 'status': 'computed', 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': fac, 'basis': basis,
            'table': table, 'detail': spc[-24:], 'cases': ho[:500] if p['list_cases'] else None, 'receipt': receipt, 'notes': notes,
            'patient_ids': sorted({c['patient_id'] for c in ho}),
            'method': (f"Coded infection diagnoses (encounter_diagnosis) falling inside an inpatient visit of the governed visit_group table; hospital day = onset date minus "
                       f"admission date + 1; hospital-onset when day >= {p['onset_day']}; one case per patient and infection per {p['repeat_window_days']} days; "
                       "rate = cases / patient-days x 1,000 with a Wilson-Hilferty Poisson interval; u-chart limits at 3 sigma around the pooled monthly rate."),
            'assumptions': ['Device-days, microbiology, central-line / catheter / ventilator status and the NHSN predicted count are not received: rates use patient-days and '
                            'the infection definitions below are diagnosis-code proxies, not full NHSN criteria.',
                            'Observed/expected compares a group with the pooled rate of this same data (internal benchmark); it is not a national-baseline SIR.']
            + [f'{n}: {INFECTIONS[n][1]}.' for n in names]}


def headline(r):
    rc = r['receipt']
    top = ', '.join(f"{t.get('infection') or t.get('facility') or t.get('month')} {t['hospital_onset_cases']}" for t in r['table'][:5])
    return (f"{rc['hospital_onset_cases']} hospital-onset infections (day {rc['onset_day']}+) over {rc['patient_days']:,} patient-days = "
            f"{rc['rate_per_1000_patient_days']} per 1,000 patient-days; {top}"
            + (f"; SPC signals in {', '.join(rc['spc_signal_months'][:4])}" if rc['spc_signal_months'] else '; no month above the control limit'))


def answer(report, plan, question):
    return H.generic_answer(OP, report, plan, headline(report) + '.')


HAI = (r'\b(?:hai|hais|hospital[- ](?:acquired|onset|associated) (?:infections?|pneumonia|c\.? ?diff)|nosocomial|clabsi|cauti|ssi rate|surgical site infections?|'
       r'c\.? ?diff(?:icile)? (?:rate|cases|infections?)|ventilator[- ]associated|infection surveillance|infection rates?|nhsn|healthcare[- ]associated infections?|sir)\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(HAI, low) or re.match(r'(?:what (?:is|are) (?:a|an|the definition)|explain|define)\b', low): return None
    if re.search(r'\bsir\b', low) and not re.search(r'infect|hai|nhsn|hospital', low): return None
    params = H.common_params(low); names = []
    if re.search(r'\bcauti\b|catheter', low): names.append('cauti')
    if re.search(r'\bclabsi\b|bloodstream|central line|sepsis', low): names.append('bloodstream')
    if re.search(r'\bssi\b|surgical site', low): names.append('ssi')
    if re.search(r'c\.? ?diff|clostridi', low): names.append('cdi')
    if re.search(r'\bvap\b|ventilator', low): names.append('vap')
    if re.search(r'\bhap\b|pneumonia', low): names.append('hap')
    if names: params['infections'] = names
    if re.search(r'\bby (?:facility|hospital|site)\b|\bper (?:facility|hospital)\b', low): params['group_by'] = 'facility'
    elif re.search(r'\bby month\b|\bmonthly\b|\btrend\b|\bcontrol chart\b|\bspc\b', low): params['group_by'] = 'month'
    if re.search(r'\blist\b|\bshow (?:me )?(?:the )?cases\b', low): params['list_cases'] = True
    return plan(OP, params, 'Hospital-onset infection surveillance (NHSN day-3 window, rate per 1,000 patient-days, SPC)', question, ['hai surveillance'])


TEMPLATES = ['what is our hospital-onset C. diff rate per 1,000 patient-days by facility',
             'show HAI rates by month with control limits',
             'how many hospital-acquired CAUTI cases did we have in 2029',
             'nosocomial infection surveillance across hospitals in the last 12 months',
             'list the hospital-onset pneumonia cases']
