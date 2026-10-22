"""network_adequacy: specialty access per county / state from the realized network (wave 2).

Provider locations, coordinates, network directories and adequacy standards are not received, so adequacy is measured from what exists:
  * a clinician's service county = the county where most of their kept-appointment patients live (modelled), their state from the practice organisation;
  * members = patients (optionally by line of business) in the county / state;
  * realized access = share of members with a kept visit to the specialty in the window, local-visit share (clinician county = member county) and the
    out-of-network share of that specialty's payer claims;
  * adequacy is a relative flag against the pooled median (thin = members per provider above twice the median, or local share under half of it) unless
    a standard is stated (max_members_per_provider, min_local_share).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

import numpy as np

from . import hospital_ops as H

OP = 'network_adequacy'
VERSION = 'network-adequacy/1'
GEO = ('county', 'state')
GROUPS = ('specialty', 'geography')
DEFAULTS = {'geography': 'county', 'group_by': 'specialty', 'specialties': [], 'state': None, 'line_of_business': None, 'months': 12, 'start': None, 'end': None,
            'max_members_per_provider': None, 'min_local_share': None, 'min_members': 30}
SPEC = {'geography': ('enum', GEO), 'group_by': ('enum', GROUPS), 'state': ('str',), 'line_of_business': ('str',), 'months': ('int', 1, 120), 'start': ('date',),
        'end': ('date',), 'max_members_per_provider': ('num', 1, 1000000), 'min_local_share': ('num', 0, 1), 'min_members': ('int', 1, 100000)}
PRODUCT = {'population': 'optional', 'view': 'network_adequacy',
           'use': 'NETWORK ADEQUACY: specialty access by county or state - members per provider, share of members seen, local vs out-of-county visits, out-of-network claim share, '
                  'thin counties and counties with no local provider - from the realized network (provider locations modelled from patient residence).'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'geography': '|'.join(GEO), 'group_by': '|'.join(GROUPS), 'specialties': [], 'state': None, 'line_of_business': None, 'months': 12,
                           'max_members_per_provider': None, 'min_local_share': None, 'min_members': 30, 'start': None, 'end': None},
            'meaning': 'Per specialty (or geography): members, serving and locally located clinicians, members per provider, share of members seen, local-visit share, out-of-network claim share, '
                       'thin and no-local-provider geographies against stated or relative thresholds.'}
NATIVE = ('count', 'value')


def schema(obj, string, array):
    s = H.spec_schema(SPEC, obj, string, array)
    s['properties']['specialties'] = array({'type': 'string'}, 'specialty names, e.g. Cardiology')
    return s


def validate(p):
    out = H.spec_validate(OP, DEFAULTS, {k: v for k, v in SPEC.items()}, {k: v for k, v in (p or {}).items() if k != 'specialties'})
    sp = (p or {}).get('specialties') or []
    if not isinstance(sp, list) or any(not isinstance(x, str) for x in sp): raise ValueError('specialties: list of names')
    out['specialties'] = sp
    return out


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db, snap = H.connect(path)
    try:
        as_of = str(as_of or snap)[:10]
        keep = set(patient_ids) if patient_ids is not None else None
        start, end = H.window(as_of, p['months'], p['start'], p['end'])
        q = 'SELECT patient_id, county, state, line_of_business FROM patient'
        pats = {pid: (c, s, lob) for pid, c, s, lob in db.execute(q) if (keep is None or pid in keep) and c}
        spec = {cid: sp for cid, sp in db.execute('SELECT clinician_id, specialty FROM clinician')}
        npi_spec = {n: sp for n, sp in db.execute('SELECT npi, specialty FROM clinician WHERE npi IS NOT NULL')}
        visits = [(pid, cid, d_) for pid, cid, d_ in db.execute("SELECT patient_id, clinician_id, scheduled_date FROM appointment WHERE kept = 1 AND clinician_id IS NOT NULL")
                  if pid in pats and (not start or str(d_)[:10] >= start) and str(d_)[:10] <= end]
        claims = [(pid, npi, ns) for pid, npi, ns, d_ in db.execute("SELECT patient_id, rendering_npi, network_status, service_from FROM payer_claim WHERE rendering_npi IS NOT NULL AND counts_in_metrics = 1")
                  if pid in pats and (not start or str(d_)[:10] >= start) and str(d_)[:10] <= end]
    finally:
        db.close()
    notes = []
    base = {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'window': [start, end], 'facility': None, 'basis': 'patient + appointment + clinician + payer_claim', 'notes': notes, 'patient_ids': []}
    gi = 0 if p['geography'] == 'county' else 1
    def geo(pid): return pats[pid][gi]
    members = {pid for pid, (c, s, lob) in pats.items() if (not p['state'] or (s or '').lower() == p['state'].lower())
               and (not p['line_of_business'] or (lob or '').lower() == p['line_of_business'].lower())}
    if p['state'] and not members: notes.append(f"No patients in state '{p['state']}'.")
    # clinician service geography = modal member geography among their kept visits
    cl_geo_votes = defaultdict(Counter)
    for pid, cid, _ in visits: cl_geo_votes[cid][geo(pid)] += 1
    cl_geo = {cid: v.most_common(1)[0][0] for cid, v in cl_geo_votes.items()}
    specs = sorted({spec.get(c) for c in cl_geo if spec.get(c)})
    if p['specialties']:
        want = [w.lower() for w in p['specialties']]; specs = [s for s in specs if any(w in s.lower() for w in want)]
        if not specs: notes.append('No specialty matches ' + ', '.join(p['specialties']) + '.')
    mem_geo = Counter(geo(pid) for pid in members)
    rows = {}  # (specialty, geo) -> counts
    def cell(sp, g): return rows.setdefault((sp, g), {'seen': set(), 'visits': 0, 'local': 0, 'clin_here': set(), 'serving': set(), 'claims': 0, 'oon': 0})
    for pid, cid, _ in visits:
        sp = spec.get(cid)
        if pid not in members or sp not in specs: continue
        c = cell(sp, geo(pid)); c['seen'].add(pid); c['visits'] += 1; c['serving'].add(cid)
        if cl_geo.get(cid) == geo(pid): c['local'] += 1
    for cid, g in cl_geo.items():
        sp = spec.get(cid)
        if sp in specs: cell(sp, g)['clin_here'].add(cid)
    for pid, npi, ns in claims:
        sp = npi_spec.get(npi)
        if pid not in members or sp not in specs: continue
        c = cell(sp, geo(pid)); c['claims'] += 1; c['oon'] += (ns == 'out_of_network')
    small = {g for g, n in mem_geo.items() if n < p['min_members']}
    cells = []
    for (sp, g), c in rows.items():
        m = mem_geo.get(g, 0)
        if not m or g in small: continue
        cells.append({'specialty': sp, 'geography': g, 'members': m, 'clinicians_located': len(c['clin_here']), 'clinicians_serving_members': len(c['serving']),
                      'members_per_located_provider': None if not c['clin_here'] else round(m / len(c['clin_here']), 1), 'members_seen': len(c['seen']),
                      'share_members_seen': round(len(c['seen']) / m, 4), 'visits': c['visits'], 'local_visit_share': None if not c['visits'] else round(c['local'] / c['visits'], 3),
                      'claims': c['claims'], 'out_of_network_claim_share': None if not c['claims'] else round(c['oon'] / c['claims'], 3)})
    # geographies with members but no cell for a specialty are no-provider deserts; add them per specialty
    have = {(c['specialty'], c['geography']) for c in cells}
    for sp in specs:
        for g, m in mem_geo.items():
            if g not in small and (sp, g) not in have:
                cells.append({'specialty': sp, 'geography': g, 'members': m, 'clinicians_located': 0, 'clinicians_serving_members': 0, 'members_per_located_provider': None,
                              'members_seen': 0, 'share_members_seen': 0.0, 'visits': 0, 'local_visit_share': None, 'claims': 0, 'out_of_network_claim_share': None})
    med_ratio = {sp: float(np.median([c['members_per_located_provider'] for c in cells if c['specialty'] == sp and c['members_per_located_provider']] or [0])) for sp in specs}
    med_local = {sp: float(np.median([c['local_visit_share'] for c in cells if c['specialty'] == sp and c['local_visit_share'] is not None] or [0])) for sp in specs}
    for c in cells:
        sp = c['specialty']
        if p['max_members_per_provider'] or p['min_local_share'] is not None:
            thin_r = bool(p['max_members_per_provider']) and (c['members_per_located_provider'] is None or c['members_per_located_provider'] > p['max_members_per_provider'])
            thin_l = p['min_local_share'] is not None and (c['local_visit_share'] is None or c['local_visit_share'] < p['min_local_share'])
            basis = 'stated standard'
        else:
            thin_r = c['members_per_located_provider'] is None or (med_ratio[sp] and c['members_per_located_provider'] > 2 * med_ratio[sp])
            thin_l = c['local_visit_share'] is not None and med_local[sp] and c['local_visit_share'] < 0.5 * med_local[sp]
            basis = 'relative to pooled median'
        c['status'] = 'no local provider' if not c['clinicians_located'] else ('thin' if (thin_r or thin_l) else 'adequate'); c['standard'] = basis
    if p['group_by'] == 'specialty':
        table = []
        for sp in specs:
            cs = [c for c in cells if c['specialty'] == sp]
            if not cs: continue
            mem = sum(c['members'] for c in cs); seen = sum(c['members_seen'] for c in cs); vis = sum(c['visits'] for c in cs); loc = sum(c['visits'] * (c['local_visit_share'] or 0) for c in cs)
            cl = sum(c['claims'] for c in cs); oon = sum(c['claims'] * (c['out_of_network_claim_share'] or 0) for c in cs)
            located = sum(c['clinicians_located'] for c in cs)
            table.append({'specialty': sp, 'geographies': len(cs), 'members': mem, 'clinicians_located': located, 'members_per_located_provider': None if not located else round(mem / located, 1),
                          'share_members_seen': round(seen / mem, 4) if mem else None, 'local_visit_share': None if not vis else round(loc / vis, 3),
                          'out_of_network_claim_share': None if not cl else round(oon / cl, 3), 'geographies_no_local_provider': sum(c['status'] == 'no local provider' for c in cs),
                          'geographies_thin': sum(c['status'] == 'thin' for c in cs), 'geographies_adequate': sum(c['status'] == 'adequate' for c in cs)})
        table.sort(key=lambda t: -(t['geographies_no_local_provider'] + t['geographies_thin']) / max(t['geographies'], 1))
    else:
        by = defaultdict(list)
        for c in cells: by[c['geography']].append(c)
        table = []
        for g, cs in by.items():
            table.append({'geography': g, 'members': mem_geo[g], 'specialties_covered': sum(c['status'] != 'no local provider' for c in cs), 'specialties_total': len(cs),
                          'specialties_no_local_provider': [c['specialty'] for c in cs if c['status'] == 'no local provider'][:12],
                          'specialties_thin': [c['specialty'] for c in cs if c['status'] == 'thin'][:12]})
        table.sort(key=lambda t: (t['specialties_covered'] / max(t['specialties_total'], 1), -t['members']))
    receipt = {'count': len(table), 'members': len(members), 'geographies': len({c['geography'] for c in cells}), 'geographies_suppressed_small': len(small), 'specialties': len(specs),
               'cells': len(cells), 'cells_no_local_provider': sum(c['status'] == 'no local provider' for c in cells), 'cells_thin': sum(c['status'] == 'thin' for c in cells),
               'standard': cells[0]['standard'] if cells else None, 'visits': len(visits), 'claims': len(claims)}
    if small: notes.append(f'{len(small)} {'counties' if p['geography'] == 'county' else 'states'} with fewer than {p["min_members"]} members are left out.')
    return {**base, 'status': 'computed' if cells else 'unknown', 'table': table, 'detail': cells[:400], 'receipt': receipt,
            'method': ('Members from the patient table by county / state; clinician service geography = modal member geography of their kept appointments in the window; per specialty and geography: '
                       'members per located clinician, share of members with a kept visit, local-visit share, out-of-network share of payer claims for clinicians of that specialty; '
                       'thin / no-local-provider against stated thresholds or the pooled median.'),
            'assumptions': ['Provider addresses, coordinates, directories and CMS time-and-distance standards are not received, so no drive time or distance is computed; "local" means same county (or state).',
                            'A clinician with no kept appointment in the window has no modelled location and is not counted as located.',
                            'Without a stated standard, thin is relative: members per provider above twice the pooled median, or local-visit share under half the pooled median.']}


def headline(r):
    rc = r['receipt']
    if r['parameters']['group_by'] == 'specialty':
        w = r['table'][:3]
        return (f"{rc['specialties']} specialties across {rc['geographies']} {'counties' if r['parameters']['geography'] == 'county' else 'states'} for {rc['members']:,} members; {rc['cells_no_local_provider']} specialty-geography cells have no local provider and "
                f"{rc['cells_thin']} are thin ({rc['standard']}); weakest: " + '; '.join(f"{t['specialty']} ({t['geographies_no_local_provider']} none, {t['geographies_thin']} thin of {t['geographies']})" for t in w))
    w = r['table'][:3]
    return (f"{rc['geographies']} {'counties' if r['parameters']['geography'] == 'county' else 'states'}; thinnest coverage: " + '; '.join(f"{t['geography']} ({t['specialties_covered']}/{t['specialties_total']} specialties with a local provider, {t['members']} members)" for t in w))


def answer(report, plan, question):
    if report['status'] != 'computed':
        return {'status': 'unknown', 'answer_type': 'text', 'value': ' '.join(report['notes']) or 'No network data for the request.', 'method': report['method'], 'limitations': []}
    return H.generic_answer(OP, report, plan, headline(report) + '.')


NET = (r'\b(?:network adequacy|adequate network|provider network (?:gaps?|coverage|adequacy)|specialist access|access to (?:specialists?|specialty care|cardiolog\w+|dermatolog\w+|psychiatr\w+|oncolog\w+|ortho\w+)|'
       r'members per (?:provider|specialist|pcp|physician)|provider[- ]to[- ]member ratio|network gaps?|(?:no|without) (?:a )?(?:local )?(?:specialists?|providers?) in|out[- ]of[- ]network (?:rate|share|leakage|utili[sz]ation)|'
       r'thin (?:specialist |specialty )?networks?|are out of network|out[- ]of[- ]network (?:claims|visits)|share of .{0,25}out[- ]of[- ]network|time[- ]and[- ]distance|care deserts?|specialty deserts?|network leakage|thin networks?)\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(NET, low) or H.is_definition(low): return None
    params = {}
    if re.search(r'\bstate\b|\bstates\b', low) and not re.search(r'\bcount(?:y|ies)\b', low): params['geography'] = 'state'
    if re.search(r'\bwhich (?:count(?:y|ies)|areas?|states?)\b|\bby (?:count(?:y|ies)|state|geograph\w+)\b|\bdeserts?\b', low): params['group_by'] = 'geography'
    sp = [n for rx, n in ((r'cardiolog', 'Cardiology'), (r'dermatolog', 'Dermatology'), (r'psychiatr', 'Psychiatry'), (r'oncolog', 'Oncology'), (r'orthop', 'Orthopedics'),
                         (r'ophthalm|eye', 'Ophthalmology'), (r'gastro', 'Gastroenterology'), (r'neurolog', 'Neurology'), (r'pediatric', 'Pediatrics'), (r'obstet|ob/?gyn', 'Obstetrics'),
                         (r'primary care|pcp', 'Family Medicine'), (r'urolog', 'Urology'), (r'endocrin', 'Endocrinology'), (r'pulmon', 'Pulmonology'), (r'nephrolog', 'Nephrology')) if re.search(rx, low)]
    if sp: params['specialties'] = sp
    m = re.search(r'\b(?:last|past) (\d{1,3}) months?\b', low)
    if m: params['months'] = int(m.group(1))
    m = re.search(r'\b(?:more than|over|above|exceed\w*)\s+([\d,]+)\s+members per', low)
    if m: params['max_members_per_provider'] = float(m.group(1).replace(',', ''))
    for lob in ('medicare advantage', 'medicaid', 'commercial', 'medicare'):
        if re.search(rf'\b{lob}\b', low): params['line_of_business'] = lob; break
    return plan(OP, params, 'Specialty network adequacy from the realized network by county / state', question, ['network adequacy'])


TEMPLATES = ['which counties have thin specialist networks for our members',
             'network adequacy for cardiology by county',
             'members per provider by specialty across the network',
             'what share of specialty claims are out of network by specialty',
             'where are the specialty care deserts in our Medicaid population']
