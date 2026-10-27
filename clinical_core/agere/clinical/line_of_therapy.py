"""Line of therapy: treatment patterns from received fills (diabetes, HF GDMT, hypertension, depression, CKD, RA).

Fills are received dispensing events only: pharmacy dispenses (status dispensed/completed), provider-side pharmacy
claims and payer pharmacy claims (status paid). Fills reversed by a B2 return to stock (payer_pharmacy_claim
counts_as_fill = 0, medication.fill_reversed = 1) and dispenses returned to stock never count. Orders, medication
lists and patient reports are not fills.

Rules (every one a stated parameter):
  episode      consecutive fills of one component (drug class or named ingredient group) whose supply, carried forward
               when fills overlap (PQA), leaves no gap longer than gap_days; the episode ends on its last covered day.
  new user     first fill at least washout_days after the fill feed starts (earlier therapy cannot be ruled out before);
               prevalent users are excluded from line numbering (counted).
  line 1       every component whose first episode starts within regimen_window_days of the first therapy fill.
  add-on       a new component starts while every component of the current regimen continues: next line (oncology
               convention, add_on_new_line = true) or an augmented regimen in the same line (false).
  switch       a new component starts and one or more regimen components end within switch_window_days of that start
               (or ended earlier): next line; the stopped components leave the regimen.
  restart      a regimen component starts a new episode after a gap: same line (a restart is not a new regimen).
  drop         a regimen component ends while others continue and nothing new starts: same line, smaller regimen.
  discontinued every regimen component has ended and nothing else starts within gap_days: the line ends.
Time on treatment = line start to the last covered day of the line (censored when the line is still covered within
gap_days of the snapshot); time to next treatment = line start to the next line's start (censored at the snapshot);
persistence = time from a component's first fill to the end of its first episode (Kaplan-Meier, survival.py).
"""
from collections import Counter, defaultdict
from datetime import date
import json, math, re, sqlite3

from . import semantic_layer
from .drug_classes import CLASSES
from .survival import kaplan_meier, km_at, median_time

VERSION = 'line-of-therapy/1'
FILL_EVIDENCE = ('dispensed', 'claim', 'payer_claim')
FILL_STATUSES = ('dispensed', 'completed', 'paid')
DEFAULT_DAYS_SUPPLY = 30
SMALL = 11

# Components not in the shared drug-class registry (matched on the received drug name, ingredient words only).
EXTRA_COMPONENTS = {
    'thiazolidinedione': r'\b(?:pioglitazone|rosiglitazone)\b',
    'snri': r'\b(?:venlafaxine|desvenlafaxine|duloxetine|levomilnacipran|milnacipran)\b',
    'bupropion': r'\bbupropion\b',
    'mirtazapine': r'\bmirtazapine\b',
    'tricyclic_antidepressant': r'\b(?:amitriptyline|nortriptyline|imipramine|desipramine|doxepin|clomipramine)\b',
    'methotrexate': r'\bmethotrexate\b',
    'conventional_dmard': r'\b(?:hydroxychloroquine|sulfasalazine|leflunomide)\b',
    'tnf_inhibitor': r'\b(?:adalimumab|etanercept|infliximab|certolizumab|golimumab)\b',
    'il6_inhibitor': r'\b(?:tocilizumab|sarilumab)\b',
    'abatacept': r'\babatacept\b',
    'rituximab': r'\brituximab\b',
    'jak_inhibitor': r'\b(?:tofacitinib|baricitinib|upadacitinib)\b',
}
COMPONENTS = {**{k: v for k, v in CLASSES.items()}, **EXTRA_COMPONENTS}
AREAS = {
    'diabetes': {'label': 'type 2 diabetes', 'concepts': ['type_2_diabetes', 'diabetes'], 'icd10': ['E11'],
                 'components': ['metformin', 'sulfonylurea', 'dpp4_inhibitor', 'sglt2_inhibitor', 'glp1_agonist', 'thiazolidinedione', 'insulin']},
    'heart_failure': {'label': 'heart failure (GDMT)', 'concepts': ['heart_failure', 'heart_failure_reduced_ef'], 'icd10': ['I50'],
                      'components': ['ace_inhibitor', 'arb', 'arni', 'hf_evidence_based_beta_blocker', 'mra', 'sglt2_inhibitor']},
    'hypertension': {'label': 'hypertension', 'concepts': ['hypertension'], 'icd10': ['I10', 'I11', 'I12', 'I13', 'I15', 'I16'],
                     'components': ['ace_inhibitor', 'arb', 'calcium_channel_blocker', 'thiazide_diuretic', 'beta_blocker', 'mra']},
    'depression': {'label': 'depression', 'concepts': ['depression'], 'icd10': ['F32', 'F33'],
                   'components': ['ssri', 'snri', 'bupropion', 'mirtazapine', 'tricyclic_antidepressant']},
    'chronic_kidney_disease': {'label': 'chronic kidney disease', 'concepts': ['chronic_kidney_disease'], 'icd10': ['N18'],
                               'components': ['ace_inhibitor', 'arb', 'sglt2_inhibitor', 'mra', 'glp1_agonist']},
    'rheumatoid_arthritis': {'label': 'rheumatoid arthritis', 'concepts': [], 'icd10': ['M05', 'M06'],
                             'components': ['methotrexate', 'conventional_dmard', 'tnf_inhibitor', 'il6_inhibitor', 'abatacept', 'rituximab', 'jak_inhibitor']},
}
FOCUS = ('all', 'lines', 'switching', 'persistence', 'sequences')


def _int(p, key, default, lo, hi):
    v = p.get(key, default)
    if type(v) is not int or not lo <= v <= hi: raise ValueError(f'{key} must be an integer {lo}..{hi}')
    return v


def validate(p):
    allowed = {'area', 'components', 'gap_days', 'regimen_window_days', 'switch_window_days', 'washout_days', 'require_new_user',
               'require_condition', 'add_on_new_line', 'max_lines', 'focus', 'top', 'period'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('line_of_therapy parameters: ' + ', '.join(sorted(allowed)))
    area = p.get('area', '')
    if area and area not in AREAS: raise ValueError(f'area must be one of {sorted(AREAS)}')
    comps = p.get('components') or []
    if not isinstance(comps, list) or len(comps) > 12 or len(set(comps)) != len(comps) or any(c not in COMPONENTS for c in comps):
        raise ValueError('components: up to 12 distinct registered drug classes / ingredient groups')
    if not area and not comps: raise ValueError('line_of_therapy needs an area or named components')
    for key in ('require_new_user', 'require_condition', 'add_on_new_line'):
        if key in p and not isinstance(p[key], bool): raise ValueError(f'{key} must be true/false')
    focus = p.get('focus', 'all')
    if focus not in FOCUS: raise ValueError(f'focus must be one of {FOCUS}')
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or any(v and (not isinstance(v, str) or len(v) != 10) for v in period.values()):
        raise ValueError('period takes ISO start/end (first-line start dates)')
    for v in period.values():
        if v: date.fromisoformat(v)
    out = {'area': area, 'components': comps or list(AREAS[area]['components']), 'gap_days': _int(p, 'gap_days', 60, 7, 365),
           'regimen_window_days': _int(p, 'regimen_window_days', 30, 0, 120), 'switch_window_days': _int(p, 'switch_window_days', 30, 0, 180),
           'washout_days': _int(p, 'washout_days', 180, 0, 730), 'require_new_user': p.get('require_new_user', True),
           'require_condition': p.get('require_condition', bool(area)), 'add_on_new_line': p.get('add_on_new_line', True),
           'max_lines': _int(p, 'max_lines', 4, 2, 8), 'focus': focus, 'top': _int(p, 'top', 15, 1, 100),
           'period': {'start': period.get('start', ''), 'end': period.get('end', '')}}
    if out['require_condition'] and not area: raise ValueError('require_condition needs an area')
    return out


def _day(s): return date.fromisoformat(s[:10]).toordinal()
def _iso(d): return date.fromordinal(int(d)).isoformat()


def _cols(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def reversed_ids(db):
    """Record ids that are not fills: payer claims reversed by a B2 return to stock and dispenses returned to stock."""
    try:
        from .client_data import reversed_fill_ids
        return reversed_fill_ids(db)
    except Exception:
        return set()


def fill_rows(db, keep=None, as_of=None):
    """Every received fill: [(patient_id, ordinal day, days supply or None, drug name, drug_class)]; reversals excluded."""
    cols = _cols(db, 'medication')
    rev = reversed_ids(db)
    ev, st = ','.join('?' * len(FILL_EVIDENCE)), ','.join('?' * len(FILL_STATUSES))
    sql = (f"SELECT m.record_id, m.patient_id, m.event_date, m.drug_name, m.drug_class, json_extract(r.payload, '$.days_supply_reported'), "
           f"json_extract(r.payload, '$.days_supply') FROM medication m LEFT JOIN record r ON r.record_id = m.record_id "
           f"WHERE m.evidence IN ({ev}) AND (m.status IN ({st}) OR (m.status IS NULL AND m.evidence <> 'dispensed')) AND m.event_date IS NOT NULL"
           + (' AND coalesce(m.fill_reversed, 0) = 0' if 'fill_reversed' in cols else ''))
    args = [*FILL_EVIDENCE, *FILL_STATUSES]
    if as_of:
        sql += ' AND substr(m.event_date,1,10) <= ?'; args.append(str(as_of)[:10])
        if 'available_date' in _cols(db, 'record'):
            sql += ' AND (r.available_date IS NULL OR substr(r.available_date,1,10) <= ?)'
            args.append(str(as_of)[:10])
    out, seen = [], set()
    for rid, pid, d, name, cls, ds1, ds2 in db.execute(sql, args):
        if rid in rev or (keep is not None and pid not in keep): continue
        key = (rid, cls)
        if key in seen: continue
        seen.add(key)
        supply = None
        for s in (ds1, ds2):
            try:
                if s is not None and float(s) > 0: supply = int(float(s)); break
            except (TypeError, ValueError):
                pass
        out.append((pid, _day(d), supply, name or '', cls))
    return out, len(rev)


def component_fills(rows, components):
    """{pid: {component: {day: supply}}}; several fills of one component on one day count once with the longest supply."""
    rx = {c: re.compile(COMPONENTS[c], re.I) for c in components}
    out = defaultdict(lambda: defaultdict(dict))
    for pid, day, supply, name, cls in rows:
        for c, r in rx.items():
            if cls == c or r.search(name):
                s = supply or DEFAULT_DAYS_SUPPLY
                out[pid][c][day] = max(out[pid][c].get(day, 0), s)
    return out


def episodes(days, gap_days):
    """Fills {day: supply} -> [(start, last covered day, fills)]; overlapping supply shifts forward (PQA carry-forward)."""
    eps, cur = [], None
    for d in sorted(days):
        s = days[d]
        if cur and d <= cur[1] + 1 + gap_days:
            cur[1] = cur[1] + s if d <= cur[1] else d + s - 1  # overlapping supply is used after the current supply runs out
            cur[2] += 1
        else:
            if cur: eps.append(tuple(cur))
            cur = [d, d + s - 1, 1]
    if cur: eps.append(tuple(cur))
    return eps


def pdc(days, start, end):
    """Proportion of days covered in [start, end] with carry-forward of overlapping supply."""
    if end < start: return None
    covered, cursor = 0, -math.inf  # cursor = first day not yet covered by earlier fills
    for d in sorted(days):
        if d > end: break
        lo = max(cursor, d)
        hi = lo + days[d] - 1
        a, b = max(lo, start), min(hi, end)
        if b >= a: covered += b - a + 1
        cursor = hi + 1
    return covered / (end - start + 1)


def derive_lines(eps, p, as_of_day):
    """One patient's episodes {component: [(start, last covered day, fills)]} -> (lines, events) under the stated rules.
    lines: [{line, start, regimen (sorted components), via, next_start, last_covered, censored_tot, discontinued}]"""
    starts = sorted((e[0], c, i) for c, es in eps.items() for i, e in enumerate(es))
    if not starts: return [], []
    W, S = p['regimen_window_days'], p['switch_window_days']
    current, idx, t1 = {}, 0, starts[0][0]  # component -> index of its current episode
    while idx < len(starts) and starts[idx][0] <= t1 + W:
        _, c, i = starts[idx]; current[c] = i; idx += 1
    lines = [{'line': 1, 'start': t1, 'regimen': sorted(current), 'via': 'first_line'}]
    events = [{'day': t1, 'kind': 'first_line', 'components': sorted(current)}]

    def end(x): return eps[x][current[x]][1]

    def resumes(x, by): return any(e[0] <= by for e in eps[x][current[x] + 1:])
    while idx < len(starts):
        d, c, i = starts[idx]; idx += 1
        if c in current:  # a regimen component starts a new episode after a gap: restart, same line
            current[c] = i
            events.append({'day': d, 'kind': 'restart', 'components': [c]})
            continue
        joining = {c: i}
        while idx < len(starts) and starts[idx][0] <= d + W:  # components starting together form one regimen change
            dd, cc, ii = starts[idx]; idx += 1
            if cc in current:
                current[cc] = ii; events.append({'day': dd, 'kind': 'restart', 'components': [cc]})
            else:
                joining.setdefault(cc, ii)
        dropped = [x for x in sorted(current) if end(x) < d - S and not resumes(x, d + S)]  # ended well before: a drop
        for x in dropped:
            events.append({'day': end(x) + 1, 'kind': 'drop', 'components': [x]}); current.pop(x)
        stopped = [x for x in sorted(current) if end(x) < d + S and not resumes(x, d + S)]  # ends around the new start: replaced
        if not current: stopped = dropped  # the whole regimen had ended: a new agent after a treatment-free interval
        for x in stopped: current.pop(x, None)
        current.update(joining)
        kind = 'switch' if stopped else 'add_on'
        events.append({'day': d, 'kind': kind, 'components': sorted(joining), 'stopped': stopped})
        if kind == 'add_on' and not p['add_on_new_line']:
            lines[-1]['regimen'] = sorted(set(lines[-1]['regimen']) | set(joining))
            continue
        if len(lines) >= p['max_lines']:
            lines[-1]['truncated'] = True
            break
        lines.append({'line': len(lines) + 1, 'start': d, 'regimen': sorted(current), 'via': kind})
    for j, ln in enumerate(lines):  # line ends: next line start; last covered day of any regimen component inside the line
        nxt = lines[j + 1]['start'] if j + 1 < len(lines) else None
        hi = nxt if nxt is not None else math.inf
        covered = [min(e[1], hi - 1) for c in ln['regimen'] for e in eps[c] if e[0] < hi and e[1] >= ln['start']]
        ln['next_start'] = nxt
        ln['last_covered'] = max(covered) if covered else ln['start']
        ln['censored_tot'] = nxt is None and ln['last_covered'] + p['gap_days'] >= as_of_day
        ln['discontinued'] = nxt is None and not ln['censored_tot']
    return lines, events


def label(regimen):
    return ' + '.join(regimen) if regimen else 'none'


def _km_summary(times, events, points=(90, 180, 365)):
    if not times: return {'units': 0}
    km = kaplan_meier(times, events)
    out = {'units': len(times), 'events': int(sum(events)), 'median_days': median_time(km)}
    for t in points:
        s, lo, hi, n = km_at(km, t)
        out[f'still_on_day_{t}'] = round(s, 4); out[f'ci95_day_{t}'] = [round(lo, 4), round(hi, 4)]
    out['curve'] = [{'day': int(r['time']), 'at_risk': r['at_risk'], 'survival': round(r['survival'], 4)} for r in km][:300]
    return out


def execute(path, patient_ids, params, as_of=None):
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        return run(db, patient_ids, params, as_of)
    finally:
        db.close()


def condition_patients(db, concepts_, icd10, as_of=None):
    """{patient_id: first documented date} for a condition by library concept or ICD-10 prefix."""
    first = {}
    terms, args = [], []
    if concepts_:
        terms.append('concept IN (' + ','.join('?' * len(concepts_)) + ')'); args += concepts_
    if icd10 and 'icd10' in _cols(db, 'condition'):
        terms += ['icd10 LIKE ?'] * len(icd10); args += [f'{x}%' for x in icd10]
    if not terms: return first
    cutoff = ''
    if as_of:
        cutoff = ' AND coalesce(substr(event_date,1,10), substr(recorded_date,1,10)) <= ?'
        args.append(str(as_of)[:10])
        if 'available_date' in _cols(db, 'condition'):
            cutoff += ' AND (available_date IS NULL OR substr(available_date,1,10) <= ?)'
            args.append(str(as_of)[:10])
    for pid, d in db.execute('SELECT patient_id, min(coalesce(substr(event_date,1,10), substr(recorded_date,1,10))) FROM condition WHERE ('
                             + ' OR '.join(terms) + ')' + cutoff + ' GROUP BY patient_id', args):
        first[pid] = d
    return first


def run(db, patient_ids, params, as_of):
    p = validate(params)
    as_of_day = _day(as_of)
    keep = set(patient_ids) if patient_ids is not None else None
    area = AREAS.get(p['area']) or {}
    rows, n_reversed = fill_rows(db, keep, as_of)
    feed = min((r[1] for r in rows), default=None)
    by_patient = component_fills(rows, p['components'])
    cond = condition_patients(db, area.get('concepts'), area.get('icd10'), as_of) if p['area'] else {}
    counts = Counter()
    counts['patients_with_any_component_fill'] = len(by_patient)
    if p['area']: counts['patients_with_condition'] = len(cond if keep is None else {k: v for k, v in cond.items() if k in keep})
    lo = _day(p['period']['start']) if p['period']['start'] else None
    hi = _day(p['period']['end']) if p['period']['end'] else None
    patients, all_lines, all_events = {}, {}, {}
    persistence = defaultdict(lambda: ([], []))
    pdcs = defaultdict(list)
    for pid in sorted(by_patient):
        comp = by_patient[pid]
        if p['require_condition'] and pid not in cond: counts['excluded_no_condition_record'] += 1; continue
        eps = {c: episodes(days, p['gap_days']) for c, days in comp.items()}
        first = min(e[0][0] for e in eps.values())
        if first > as_of_day: continue
        if p['require_new_user'] and (feed is None or first < feed + p['washout_days']):
            counts['excluded_possible_prevalent_users'] += 1; continue
        if (lo and first < lo) or (hi and first > hi): counts['excluded_outside_period'] += 1; continue
        if pid in cond and cond[pid] and _day(cond[pid]) > first: counts['therapy_before_first_condition_record'] += 1
        lines, events = derive_lines(eps, p, as_of_day)
        all_lines[pid], all_events[pid] = lines, events
        for c in lines[0]['regimen']:
            e0 = eps[c][0]
            ongoing = e0[1] + p['gap_days'] >= as_of_day and len(eps[c]) == 1
            t = min(e0[1], as_of_day) - e0[0] + 1
            persistence[c][0].append(max(t, 1)); persistence[c][1].append(0 if ongoing else 1)
            end = min(e0[0] + 364, as_of_day)
            if end - e0[0] >= 89: pdcs[c].append(pdc(comp[c], e0[0], end))
    n = len(all_lines)
    counts['analysed_new_users'] = n
    # per-line statistics
    line_rows, regimen_rows = [], []
    for k in range(1, p['max_lines'] + 1):
        lns = [(pid, ls[k - 1]) for pid, ls in all_lines.items() if len(ls) >= k]
        if not lns: break
        tot = [(min(ln['last_covered'], as_of_day) - ln['start'] + 1, 0 if ln['censored_tot'] else 1) for _, ln in lns]
        ttnt = [((ln['next_start'] if ln['next_start'] else as_of_day) - ln['start'], 1 if ln['next_start'] else 0) for _, ln in lns]
        km_tot = _km_summary([max(t, 1) for t, _ in tot], [e for _, e in tot])
        km_ttnt = _km_summary([max(t, 1) for t, _ in ttnt], [e for _, e in ttnt])
        via = Counter(ln['via'] for _, ln in lns)
        regs = Counter(label(ln['regimen']) for _, ln in lns)
        line_rows.append({'line': k, 'patients': len(lns), 'share_of_new_users': round(len(lns) / n, 4) if n else None,
                          'via_switch': via.get('switch', 0), 'via_add_on': via.get('add_on', 0),
                          'median_time_on_treatment_days': km_tot.get('median_days'), 'on_treatment_day_365': km_tot.get('still_on_day_365'),
                          'median_time_to_next_treatment_days': km_ttnt.get('median_days'),
                          'next_line_by_day_365': round(1 - km_ttnt['still_on_day_365'], 4) if km_ttnt.get('units') else None,
                          'discontinued_without_next_line': sum(1 for _, ln in lns if ln['discontinued']),
                          'top_regimen': regs.most_common(1)[0][0], 'small_group': len(lns) < SMALL})
        for reg, c in regs.most_common(p['top']):
            regimen_rows.append({'line': k, 'regimen': reg, 'patients': c, 'share_of_line': round(c / len(lns), 4)})
    # transitions between consecutive lines and component switching matrix
    trans, matrix = Counter(), Counter()
    for pid, ls in all_lines.items():
        for a, b in zip(ls, ls[1:]):
            trans[(a['line'], label(a['regimen']), label(b['regimen']), b['via'])] += 1
        for ev in all_events[pid]:
            if ev['kind'] == 'switch':
                for s in ev['stopped'] or ['(none)']:
                    for c in ev['components']: matrix[(s, c)] += 1
    transitions = [{'from_line': k, 'from_regimen': f, 'to_regimen': t, 'kind': kind, 'patients': c}
                   for (k, f, t, kind), c in sorted(trans.items(), key=lambda kv: (-kv[1], kv[0]))][:60]
    switch_matrix = [{'from_component': a, 'to_component': b, 'switches': c} for (a, b), c in sorted(matrix.items(), key=lambda kv: (-kv[1], kv[0]))]
    seqs = Counter(' → '.join(label(ln['regimen']) for ln in ls[:3]) for ls in all_lines.values())
    sequences = [{'sequence': s, 'lines': s.count('→') + 1, 'patients': c, 'share': round(c / n, 4) if n else None} for s, c in seqs.most_common(p['top'])]
    sankey = []
    for pid, ls in all_lines.items():
        for a, b in zip(ls[:3], ls[1:3]):
            sankey.append((f"L{a['line']}: {label(a['regimen'])}", f"L{b['line']}: {label(b['regimen'])}"))
        if len(ls) < 3 and ls[-1]['discontinued']:
            sankey.append((f"L{ls[-1]['line']}: {label(ls[-1]['regimen'])}", 'discontinued'))
    links = Counter(sankey)
    persist_rows = []
    for c, (t, e) in sorted(persistence.items(), key=lambda kv: -len(kv[1][0])):
        s = _km_summary(t, e)
        persist_rows.append({'component': c, 'first_line_starters': s['units'], 'discontinued': s.get('events'), 'median_persistence_days': s.get('median_days'),
                             'persistent_day_180': s.get('still_on_day_180'), 'persistent_day_365': s.get('still_on_day_365'),
                             'ci95_day_365': s.get('ci95_day_365'), 'median_pdc_first_year': round(float(sorted(pdcs[c])[len(pdcs[c]) // 2]), 3) if pdcs[c] else None,
                             'pdc_ge_80_share': round(sum(x >= 0.8 for x in pdcs[c]) / len(pdcs[c]), 3) if pdcs[c] else None,
                             'small_group': s['units'] < SMALL, 'curve': s.get('curve', [])})
    warnings = ['Synthetic data: treatment patterns describe a generated warehouse, not real prescribing.']
    warnings.append('Clinical dates and known receipt dates must be on or before the snapshot. Missing receipt dates remain unknown; '
                    'current received reversal flags are used, so historical reversal-state reconstruction is not established.')
    if feed is None: warnings.append('No received fills (dispenses, pharmacy claims or payer pharmacy claims): no line of therapy can be derived.')
    if n and n < 30: warnings.append(f'Only {n} new users with fills of these components: line shares and medians are unstable.')
    if p['area'] and counts['patients_with_condition'] and n == 0 and feed is not None:
        warnings.append(f"No {area['label']} patient has a qualifying first fill of {', '.join(p['components'])} after the {p['washout_days']}-day washout.")
    if counts['excluded_possible_prevalent_users']:
        warnings.append(f"{counts['excluded_possible_prevalent_users']} patients filled before the washout ended (fill feed starts {_iso(feed)}); "
                        'their first line cannot be identified and they are excluded from line numbering.')
    missing = [c for c in p['components'] if not any(c in v for v in by_patient.values())]
    if missing: warnings.append('No received fills for: ' + ', '.join(missing) + ' (absent from this warehouse, or not dispensed).')
    if counts['therapy_before_first_condition_record']:
        warnings.append(f"{counts['therapy_before_first_condition_record']} patients filled before their first documented {area.get('label', 'condition')} record "
                        '(documentation date is cohort entry, not onset).')
    warnings.append('Fills outside the received pharmacy feeds (cash, other pharmacies, samples) are invisible: gaps may be over-counted and '
                    'switches may look like discontinuation.')
    if n_reversed: warnings.append(f'{n_reversed} reversed / returned-to-stock fills excluded (counts_as_fill).')
    status = 'answered' if n else 'no_data'
    reading = summarize_text(p, area, n, line_rows, persist_rows, transitions, sequences)
    rules = {'gap_days': p['gap_days'], 'regimen_window_days': p['regimen_window_days'], 'switch_window_days': p['switch_window_days'],
             'washout_days': p['washout_days'], 'add_on_new_line': p['add_on_new_line'], 'require_new_user': p['require_new_user'],
             'fill_definition': 'dispensed/completed pharmacy dispenses and paid pharmacy or payer claims; reversed (B2) and returned-to-stock fills excluded; '
                                f'missing days supply = {DEFAULT_DAYS_SUPPLY}'}
    method = ('Fill episodes per component with supply carried forward; a gap longer than '
              f"{p['gap_days']} days ends an episode. Line 1 = components started within {p['regimen_window_days']} days of the first fill "
              f"(new users only: first fill >= {p['washout_days']} days after the fill feed starts). A new component with a regimen component "
              f"ending within {p['switch_window_days']} days is a switch (new line); with every component continuing it is an add-on "
              + ('(new line)' if p['add_on_new_line'] else '(same line)') + '; restarts and drops stay in the line. Time on treatment and time to '
              'next treatment by Kaplan-Meier (censored at the snapshot); persistence = first episode of each first-line component (Kaplan-Meier); '
              'PDC over the first year with carry-forward. Observational treatment patterns, not effectiveness.')
    return {'schema': VERSION, 'status': status, 'parameters': p, 'as_of': as_of, 'area': p['area'], 'area_label': area.get('label'),
            'fill_feed_start': _iso(feed) if feed else None, 'counts': dict(counts), 'rules': rules, 'lines': line_rows,
            'regimens': regimen_rows, 'transitions': transitions, 'switch_matrix': switch_matrix, 'sequences': sequences,
            'sankey': [{'source': a, 'target': b, 'patients': c} for (a, b), c in links.most_common(40)],
            'persistence': persist_rows, 'warnings': warnings, 'reading': reading, 'method': method,
            'examples': [{'patient_id': pid, 'lines': [{'line': ln['line'], 'start': _iso(ln['start']), 'regimen': label(ln['regimen']), 'via': ln['via'],
                                                         'last_covered': _iso(ln['last_covered'])} for ln in ls]}
                         for pid, ls in sorted(all_lines.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:5]]}


def summarize_text(p, area, n, line_rows, persist_rows, transitions, sequences):
    what = area.get('label') or ', '.join(p['components'])
    if not n: return f'No new users of {", ".join(p["components"])} could be lined up for {what}: see warnings.'
    parts = [f"{n} new users of {what} therapy"]
    for r in line_rows[:3]:
        parts.append(f"line {r['line']}: {r['patients']} patients (top regimen {r['top_regimen']}"
                     + (f", median {r['median_time_on_treatment_days']:g} days on treatment" if r['median_time_on_treatment_days'] is not None else '')
                     + (f"; {r['via_switch']} by switch, {r['via_add_on']} by add-on" if r['line'] > 1 else '') + ')')
    text = '; '.join(parts) + '.'
    if sequences: text += f" Most common sequence: {sequences[0]['sequence']} ({sequences[0]['patients']} patients)."
    if persist_rows:
        r = persist_rows[0]
        if r['persistent_day_365'] is not None:
            text += f" First-line {r['component']} persistence at 1 year {r['persistent_day_365']:.0%} (Kaplan-Meier, {r['first_line_starters']} starters)."
    return text + ' Observational treatment patterns from received fills, not effectiveness.'


def summarize(report):
    """(headline text, table) for the answer card."""
    focus = report['parameters']['focus']
    table = {'switching': report['switch_matrix'] or report['transitions'], 'persistence': [{k: v for k, v in r.items() if k != 'curve'} for r in report['persistence']],
             'sequences': report['sequences']}.get(focus) or report['lines']
    return report['reading'], table
