"""spatiotemporal_cluster: space-time permutation scan statistic over ED syndromes by ZIP / county (wave 2).

Kulldorff's space-time permutation model needs only cases: the expected count for a zone and time window is (cases in the zone x cases in
the window) / all cases, so no population-at-risk table or ED volume baseline is needed and slow background drift in ED use is absorbed.
Zones = every ZIP, every 3-digit ZIP prefix and every county (or every county and state when counties are the unit); windows = 1..max_window weeks ending in
any week. Significance by Monte Carlo permutation of the week labels among cases (seeded). Coordinates are not received, so
neighbourhood zones are administrative (ZIP3 / county / state) rather than distance circles.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta

import numpy as np
from scipy import sparse

from . import hospital_ops as H

OP = 'spatiotemporal_cluster'
VERSION = 'spatiotemporal-cluster/1'
SYNDROMES = {  # name -> ICD-10 prefixes of the ED principal diagnosis
    'respiratory': ('J0', 'J1', 'J20', 'J21', 'J22', 'J40', 'J45', 'J96', 'R05', 'R06'),
    'gastrointestinal': ('A00', 'A01', 'A02', 'A03', 'A04', 'A05', 'A08', 'A09', 'K52', 'R11', 'R19.7'),
    'urinary': ('N39', 'N30', 'N10'),
    'injury': ('S', 'T0', 'T1'),
    'chest_pain': ('R07', 'I20', 'I21'),
    'fever_infection': ('R50', 'A41', 'A49', 'B34', 'B99'),
    'behavioral': ('F',),
    'cardiovascular': ('I',),
    'all_ed': ('',),
}
UNITS = ('zip', 'county')
DEFAULTS = {'syndrome': 'respiratory', 'unit': 'zip', 'months': 24, 'start': None, 'end': None, 'max_window_weeks': 4, 'min_cases': 3,
            'permutations': 199, 'alpha': 0.05, 'top_k': 5, 'facility': None}
SPEC = {'syndrome': ('enum', tuple(SYNDROMES)), 'unit': ('enum', UNITS), 'months': ('int', 1, 120), 'start': ('date',), 'end': ('date',),
        'max_window_weeks': ('int', 1, 26), 'min_cases': ('int', 2, 100), 'permutations': ('int', 19, 999), 'alpha': ('num', 0.001, 0.5),
        'top_k': ('int', 1, 20), 'facility': ('str',)}
PRODUCT = {'population': 'optional', 'view': 'cluster_detection',
           'use': 'OUTBREAK / CLUSTER DETECTION: space-time permutation scan over ED visits by syndrome (respiratory, GI, urinary, injury, chest pain, ...) across ZIP codes, '
                  'ZIP3 areas and counties and weekly windows - which areas had more cases than expected in some weeks, relative risk and permutation p-value.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'syndrome': '|'.join(SYNDROMES), 'unit': 'zip|county', 'months': 24, 'max_window_weeks': 4, 'min_cases': 3, 'permutations': 199,
                           'alpha': 0.05, 'top_k': 5, 'facility': None, 'start': None, 'end': None},
            'meaning': 'Kulldorff space-time permutation scan: ED visits with the syndrome\'s principal diagnosis, residence ZIP / county, weekly windows up to max_window_weeks; '
                       'ranked non-overlapping clusters with observed, expected, relative risk, log-likelihood ratio and Monte Carlo p-value.'}
NATIVE = ('count', 'value')


def schema(obj, string, array): return H.spec_schema(SPEC, obj, string, array)
def validate(p): return H.spec_validate(OP, DEFAULTS, SPEC, p)


def zones(units, unit, geo):
    """-> (labels, sparse zone x unit membership). geo[unit] = (zip3 / state, county) metadata for hierarchy."""
    idx = {u: i for i, u in enumerate(units)}
    members, labels = [], []
    for u in units:
        members.append([idx[u]]); labels.append(('ZIP ' if unit == 'zip' else 'county ') + u)
    groups = defaultdict(list)
    for u in units:
        groups[('ZIP3 ' + u[:3] + 'xx') if unit == 'zip' else ('state ' + geo.get(u, ('', ''))[0])].append(idx[u])
    if unit == 'zip':
        for u in units: groups['county ' + geo.get(u, ('', ''))[1]].append(idx[u])
        groups = {k: v for k, v in groups.items() if not k.endswith(' ')}
    for k, v in groups.items():
        if len(v) > 1: members.append(v); labels.append(f'{k} ({len(v)} {unit}s)')
    r = [i for i, m in enumerate(members) for _ in m]; c = [x for m in members for x in m]
    return labels, sparse.csr_matrix((np.ones(len(r)), (r, c)), shape=(len(members), len(units)))


def llr(c, e, n, min_cases):
    with np.errstate(divide='ignore', invalid='ignore'):
        out = c * np.log(c / e) + (n - c) * np.log((n - c) / (n - e))
    out = np.where((c > e) & (c >= min_cases) & np.isfinite(out), out, 0.0)
    return out


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db, snap = H.connect(path)
    try:
        as_of = str(as_of or snap)[:10]
        rows, basis, (start, end), fac, notes = H.visits_in(db, as_of, {**p, 'months': None if p['start'] else p['months']}, patient_ids, need=lambda r: r['has_ed'])
        geo_rows = {pid: (z, c, s) for pid, z, c, s in db.execute('SELECT patient_id, zip, county, state FROM patient')}
    finally:
        db.close()
    pref = SYNDROMES[p['syndrome']]
    cases = []
    for v in rows:
        icd = v['principal_icd10'] or ''
        if not any(icd.startswith(x) for x in pref): continue
        z, c, s = geo_rows.get(v['patient_id'], (None, None, None))
        loc = (z or '')[:5] if p['unit'] == 'zip' else (c or '')
        if not loc: continue
        cases.append((v['patient_id'], loc, H.d(v['start_date']), z, c, s))
    n = len(cases)
    base = {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': fac, 'basis': basis, 'notes': notes, 'patient_ids': [], 'clusters': None}
    if n < 10:
        return {**base, 'status': 'unknown', 'table': [], 'receipt': {'count': n, 'cases': n}, 'notes': notes + [f'Only {n} {p["syndrome"]} ED visits with a residence in the window; too few to scan.'],
                'method': 'space-time permutation scan', 'assumptions': []}
    d0 = min(c[2] for c in cases); d0 -= timedelta(days=d0.weekday())
    weeks = np.array([(c[2] - d0).days // 7 for c in cases]); nt = int(weeks.max()) + 1
    units = sorted({c[1] for c in cases}); uidx = {u: i for i, u in enumerate(units)}
    geo = {}
    for c in cases: geo[c[1]] = ((c[3] or '')[:3], c[4] or '') if p['unit'] == 'zip' else (c[5] or '', c[4] or '')
    sp = np.array([uidx[c[1]] for c in cases])
    labels, Z = zones(units, p['unit'], geo)
    W = min(p['max_window_weeks'], nt)
    cs_time = np.bincount(weeks, minlength=nt).astype(float)
    ct = np.concatenate([[0], np.cumsum(cs_time)])
    zone_tot = np.asarray(Z @ np.bincount(sp, minlength=len(units)).astype(float)).ravel()
    ends = np.arange(nt)[:, None]; lens = np.arange(1, W + 1)[None, :]
    lo = np.clip(ends - lens + 1, 0, None)
    valid = (ends - lens + 1) >= 0
    win_tot = (ct[ends + 1] - ct[lo])  # (nt, W)
    exp = zone_tot[:, None, None] * win_tot[None, :, :] / n
    # observed counts per (zone, end, length): cm[end+1] - cm[end+1-length]
    def counts(wk):
        A = np.zeros((len(units), nt)); np.add.at(A, (sp, wk), 1.0)
        M = np.asarray(Z @ A); cm = np.concatenate([np.zeros((M.shape[0], 1)), np.cumsum(M, axis=1)], axis=1)
        hi = np.broadcast_to(ends + 1, (nt, W)); lw = np.where(valid, ends + 1 - lens, 0)
        return cm[:, hi] - cm[:, lw]
    obs = counts(weeks)
    stat = np.where(valid[None], llr(obs, exp, n, p['min_cases']), 0.0)
    rng = np.random.default_rng(20500)
    maxima = np.empty(p['permutations'])
    for i in range(p['permutations']):
        maxima[i] = np.where(valid[None], llr(counts(rng.permutation(weeks)), exp, n, p['min_cases']), 0.0).max()
    order = np.argsort(stat, axis=None)[::-1]
    picked, table = [], []
    unit_sets = [set(Z[z].indices) for z in range(Z.shape[0])]
    for flat in order:
        if stat.flat[flat] <= 0 or len(picked) >= p['top_k']: break
        z, e, l = np.unravel_index(flat, stat.shape)
        a_, b_ = int(e - lens[0, l] + 1), int(e)
        if any(unit_sets[z] & unit_sets[z2] and not (b_ < a2 or a_ > b2) for z2, a2, b2 in picked): continue
        picked.append((z, a_, b_))
        pval = (1 + int((maxima >= stat.flat[flat]).sum())) / (p['permutations'] + 1)
        ws, we = d0 + timedelta(weeks=a_), d0 + timedelta(weeks=b_, days=6)
        o, ex = float(obs[z, e, l]), float(exp[z, e, l])
        members = unit_sets[z]
        pids = sorted({c[0] for c in cases if uidx[c[1]] in members and a_ <= (c[2] - d0).days // 7 <= b_})
        table.append({'rank': len(picked), 'zone': labels[z], 'window_start': ws.isoformat(), 'window_end': we.isoformat(), 'weeks': b_ - a_ + 1,
                      'observed': int(o), 'expected': round(ex, 2), 'relative_risk': round(o / ex, 2) if ex else None, 'llr': round(float(stat.flat[flat]), 3),
                      'p_value': round(pval, 4), 'significant': pval <= p['alpha'], 'patients': len(pids), 'patient_ids': pids[:200]})
    sig = [t for t in table if t['significant']]
    receipt = {'count': len(sig), 'cases': n, 'units': len(units), 'weeks': nt, 'zones': len(labels), 'significant_clusters': len(sig),
               'permutations': p['permutations'], 'alpha': p['alpha'], 'syndrome': p['syndrome'], 'unit': p['unit']}
    return {**base, 'status': 'computed', 'table': [{k: v for k, v in t.items() if k != 'patient_ids'} for t in table], 'clusters': table, 'receipt': receipt,
            'patient_ids': sorted({x for t in sig for x in t['patient_ids']}),
            'method': (f"Space-time permutation scan: {n} {p['syndrome']} ED visits by residence {p['unit']}; zones = single units plus administrative groupings; windows 1-{W} weeks; "
                       f"expected = zone cases x window cases / all cases; Monte Carlo p from {p['permutations']} permutations of week labels (seed fixed)."),
            'assumptions': ['Syndrome = principal ICD-10 of the ED visit (prefixes in the syndrome definition); chief complaint text is not used.',
                            'Residence is the patient\'s current ZIP / county, not the address at the visit; coordinates are not received, so zones are administrative groups, not distance circles.',
                            'The permutation model finds clusters relative to the same data\'s own space and time totals; it will not flag a uniform citywide surge.']}


def headline(r):
    rc = r['receipt']
    sig = [t for t in r['table'] if t['significant']]
    if not sig:
        top = r['table'][0] if r['table'] else None
        return (f"No significant {rc['syndrome']} cluster among {rc['cases']} ED visits (alpha {rc['alpha']})" +
                (f"; strongest candidate {top['zone']} {top['window_start']} to {top['window_end']}: {top['observed']} vs {top['expected']} expected, p={top['p_value']}" if top else ''))
    return f"{len(sig)} significant {rc['syndrome']} cluster(s) among {rc['cases']} ED visits: " + '; '.join(
        f"{t['zone']} {t['window_start']} to {t['window_end']}: {t['observed']} vs {t['expected']} expected (RR {t['relative_risk']}, p={t['p_value']})" for t in sig[:3])


def answer(report, plan, question):
    if report['status'] != 'computed':
        return {'status': 'unknown', 'answer_type': 'text', 'value': ' '.join(report['notes']), 'method': report.get('method', ''), 'limitations': []}
    return H.generic_answer(OP, report, plan, headline(report) + '.')


CLU = (r'\b(?:clusters?|outbreaks?|hot ?spots?|syndromic|space[- ]time|spatiotemporal|scan statistic|unusual(?:ly)? (?:number|spike|surge|rise)s? of|'
       r'surge in .{0,40}(?:zip|county|area)|geographic(?:ally)? concentrat\w+|spikes? in .{0,40}(?:zip|county))\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(CLU, low) or H.is_definition(low): return None
    if not re.search(r'\bed\b|emergency|zip|county|counties|outbreak|syndrom|visits?|cases', low): return None
    if re.search(r'\bcare gaps?\b|\bpatient clusters?\b|\bcluster(?:ing)? of patients by\b|\bk-?means\b|\bsegments?\b', low): return None
    params = {}
    for name, rx in (('respiratory', r'respirat|flu\b|influenza|covid|pneumonia|asthma|cough'), ('gastrointestinal', r'\bgi\b|gastro|diarrh|vomit|norovirus'),
                     ('urinary', r'\buti\b|urinary'), ('injury', r'injur|trauma|fall'), ('chest_pain', r'chest pain'), ('behavioral', r'behavio|psych|mental'),
                     ('fever_infection', r'fever|sepsis|infection')):
        if re.search(rx, low): params['syndrome'] = name; break
    if re.search(r'\bcount(?:y|ies)\b', low) and not re.search(r'\bzip', low): params['unit'] = 'county'
    m = re.search(r'\b(?:last|past) (\d{1,3}) months?\b', low)
    if m: params['months'] = int(m.group(1))
    elif re.search(r'\b(?:last|past) year\b', low): params['months'] = 12
    yr = re.search(r'\bin (20\d\d)\b', low)
    if yr: params.update(start=f'{yr.group(1)}-01-01', end=f'{yr.group(1)}-12-31')
    return plan(OP, params, 'Space-time permutation scan of ED syndrome visits by ZIP / county', question, ['spatiotemporal cluster'])


TEMPLATES = ['were there any respiratory illness clusters in the ED by ZIP code in the last 12 months',
             'detect unusual spikes of gastrointestinal ED visits by county',
             'syndromic surveillance: injury cluster scan in 2029',
             'is there an outbreak signal for urinary infections in emergency visits',
             'space-time scan statistic for chest pain ED visits']
