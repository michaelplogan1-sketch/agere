"""Diagnostic delay look-back (Look-Back / SPADE-style): missed opportunities before a diagnosis, and signals without one.

Answers "how long before diagnosis did CKD / diabetes / heart failure / colorectal cancer signals appear?", "how many visits
were missed opportunities?", "which patients show the signals today with no diagnosis?" and (look-forward) "how many ED
treat-and-release patients with dizziness were admitted for stroke within 30 days?". Everything here is a REVIEW PROMPT for a
clinician, never a diagnosis.

Per target condition:
  diagnosis date   earliest of: condition / problem-list records (concept or ICD-10 prefix; onset, else recorded date),
                   incident_diagnosis.diagnosis_date, encounter diagnoses (visit date) and claim diagnoses (service date).
                   Incident = diagnosed at least `washout_days` after the patient's first observed record (else prevalent,
                   excluded from the look-back because earlier signals cannot be seen).
  criterion        the earliest date the recorded data met a diagnostic criterion (labs / measurements / note facts):
                     ckd               eGFR < 60 twice >= 90 days apart, or uACR >= 30 twice >= 90 days apart (KDIGO)
                     diabetes          two results on different days with HbA1c >= 6.5% or random glucose >= 200 mg/dL (ADA)
                     hypertension      SBP >= 140 or DBP >= 90 on 3 separate days within a year
                     heart_failure     EF <= 40% (reports, imaging fragments, note facts), or NT-proBNP >= 125 pg/mL with
                                       a dyspnea / edema note fact within 90 days
                     colorectal_cancer positive FIT, iron-deficiency anemia (Hgb < 12 F / < 13 M with ferritin < 30 within
                                       90 days) or rectal bleeding in a note (red flags warranting evaluation)
  related visits   SPADE look-back: completed visits with a related symptom code (visit principal ICD-10) or symptom note
                   fact in the look-back window before the diagnosis.
Look-back: delay = diagnosis date - criterion date (incident cases with a prior criterion); missed-opportunity visits = visits
between the criterion and the diagnosis; distribution (median, IQR, P90, shares over 90/180/365 days) and by practice/PCP.
Undiagnosed: patients meeting the criterion (or one qualifying value never repeated: unconfirmed signal) with no diagnosis by
the snapshot, as review prompts with the evidence, visits since and the PCP. The same evidence feeds risk_adjustment suspects
for CKD / diabetes / heart failure; here the frame is diagnostic safety (was a diagnosis missed or delayed?), not coding.
Look-forward: ED treat-and-release index visits (no admission within 1 day), outcome = admission within `horizon_days` with the
target diagnosed within 7 days of admission; symptom-coded index visits vs other ED discharges (risk ratio).
"""
from collections import Counter, defaultdict
import json, math, re

from . import safety_data as sd

VERSION = 'diagnostic-delay/1'
TARGETS = {
    'ckd': {'label': 'chronic kidney disease', 'concepts': ('chronic_kidney_disease',), 'icd10': ('N18',),
            'symptom_icd': ('R80', 'R60', 'R39'), 'symptom_facts': ('edema',), 'suspect': 'ckd_egfr'},
    'diabetes': {'label': 'diabetes', 'concepts': ('diabetes', 'type_2_diabetes', 'type_1_diabetes'), 'icd10': ('E08', 'E09', 'E10', 'E11', 'E13'),
                 'symptom_icd': ('R73', 'R35', 'R63.1', 'R63.4'), 'symptom_facts': (), 'suspect': 'diabetes_a1c'},
    'hypertension': {'label': 'hypertension', 'concepts': ('hypertension',), 'icd10': ('I10', 'I11', 'I12', 'I13', 'I15', 'I16'),
                     'symptom_icd': ('R03.0', 'R51'), 'symptom_facts': (), 'suspect': None},
    'heart_failure': {'label': 'heart failure', 'concepts': ('heart_failure', 'heart_failure_reduced_ef'), 'icd10': ('I50', 'I11.0', 'I13.0', 'I13.2'),
                      'symptom_icd': ('R06', 'R60', 'R00.0'), 'symptom_facts': ('dyspnea', 'edema'), 'suspect': 'hf_ejection_fraction'},
    'colorectal_cancer': {'label': 'colorectal cancer', 'concepts': (), 'icd10': ('C18', 'C19', 'C20'),
                          'symptom_icd': ('K62.5', 'K92.1', 'K92.2', 'D50', 'D64.9', 'R19.4', 'R19.5'), 'symptom_facts': ('bleeding', 'constipation', 'diarrhea'),
                          'suspect': None},
    'stroke': {'label': 'stroke', 'concepts': ('stroke',), 'icd10': ('I60', 'I61', 'I63', 'G45'),
               'symptom_icd': ('R42', 'R26', 'R29.8', 'H81', 'R47', 'R51', 'R55'), 'symptom_facts': ('dizziness', 'syncope'), 'suspect': None},
    'myocardial_infarction': {'label': 'myocardial infarction', 'concepts': ('myocardial_infarction',), 'icd10': ('I21', 'I22'),
                              'symptom_icd': ('R07', 'R06', 'R55', 'R10.1'), 'symptom_facts': ('chest_pain', 'syncope'), 'suspect': None},
    'sepsis': {'label': 'sepsis', 'concepts': ('sepsis',), 'icd10': ('A40', 'A41', 'R65.2'),
               'symptom_icd': ('R50', 'R11', 'R41.0', 'N39.0'), 'symptom_facts': ('fever',), 'suspect': None},
}
CRITERION_TARGETS = ('ckd', 'diabetes', 'hypertension', 'heart_failure', 'colorectal_cancer')
MODES = ('all', 'lookback', 'undiagnosed', 'look_forward')
GROUP_BY = ('practice', 'pcp', 'none')
FIELDS = {'target', 'mode', 'lookback_days', 'washout_days', 'horizon_days', 'period', 'group_by', 'top', 'symptom_only'}
IN_PERSON = {'primary_care_visit', 'specialist_visit', 'urgent_care_visit', 'emergency_visit', 'telehealth_contact', None}
LAB_CONCEPTS = ('egfr', 'uacr', 'hba1c', 'glucose', 'nt_probnp', 'hemoglobin', 'ferritin', 'systolic_bp', 'diastolic_bp')
DM_DRUGS = ('metformin', 'insulin', 'sglt2_inhibitor', 'glp1_agonist', 'sulfonylurea', 'dpp4_inhibitor', 'thiazolidinedione')
BINS = [(0, 30), (31, 90), (91, 180), (181, 365), (366, 730), (731, None)]


def validate(p):
    if not isinstance(p, dict) or set(p) - FIELDS: raise ValueError(f'diagnostic_delay parameters: {sorted(FIELDS)}')
    target = p.get('target') or 'ckd'
    if target not in TARGETS: raise ValueError(f'target must be one of {sorted(TARGETS)}')
    mode = p.get('mode') or ('look_forward' if target not in CRITERION_TARGETS else 'all')
    if mode not in MODES: raise ValueError(f'mode must be one of {MODES}')
    if mode in ('lookback', 'undiagnosed', 'all') and target not in CRITERION_TARGETS and mode != 'lookback':
        raise ValueError(f'{target} has no laboratory diagnostic criterion: use mode lookback (related-symptom visits) or look_forward')
    ints = {'lookback_days': (730, 90, 3650), 'washout_days': (365, 0, 1825), 'horizon_days': (30, 1, 365), 'top': (50, 1, 1000)}
    out = {'target': target, 'mode': mode}
    for k, (d, lo, hi) in ints.items():
        v = p.get(k, d)
        if type(v) is not int or not lo <= v <= hi: raise ValueError(f'{k} {lo}..{hi}')
        out[k] = v
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or any(v and sd.day(v) != v for v in period.values()):
        raise ValueError('period takes ISO start/end dates')
    group_by = p.get('group_by') or 'practice'
    if group_by not in GROUP_BY: raise ValueError(f'group_by must be one of {GROUP_BY}')
    so = p.get('symptom_only', False)
    if type(so) is not bool: raise ValueError('symptom_only: true/false')
    out.update(period={'start': period.get('start', ''), 'end': period.get('end', '')}, group_by=group_by, symptom_only=so)
    return out


# ---- pure rules ------------------------------------------------------------------------------------------------------------
def criterion_date(target, s):
    """Earliest date the signals meet the target's criterion, with the evidence; s = {kind: sorted [(date, value)]}."""
    def twice_apart(rows, min_gap=90):
        first = None
        for d, v in rows:
            if first is None: first = (d, v); continue
            if sd.days_between(first[0], d) >= min_gap: return d, [first, (d, v)]
        return None
    if target == 'ckd':
        cands = []
        for kind, label in (('egfr_low', 'eGFR < 60'), ('uacr_high', 'uACR >= 30')):
            got = twice_apart(s.get(kind, []))
            if got: cands.append((got[0], f"{label} on {got[1][0][0]} ({got[1][0][1]:g}) and {got[1][1][0]} ({got[1][1][1]:g})"))
        return min(cands) if cands else None
    if target == 'diabetes':
        rows = sorted(s.get('a1c_high', []) + s.get('glucose_high', []))
        days = []
        for d, v in rows:
            if d not in [x[0] for x in days]: days.append((d, v))
            if len(days) == 2: return d, f'two diagnostic-range results: {days[0][0]} ({days[0][1]:g}) and {d} ({v:g})'
        return None
    if target == 'hypertension':
        days = sorted({d for d, _ in s.get('bp_high', [])})
        for i in range(2, len(days)):
            if sd.days_between(days[i - 2], days[i]) <= 365: return days[i], f'elevated BP on 3 days: {days[i - 2]}, {days[i - 1]}, {days[i]}'
        return None
    if target == 'heart_failure':
        cands = [(d, f'EF {v:g}%') for d, v in s.get('ef_low', [])]
        sym = [d for d, _ in s.get('hf_symptom', [])]
        for d, v in s.get('bnp_high', []):
            near = [x for x in sym if abs(sd.days_between(x, d)) <= 90]
            if near: cands.append((max(d, min(near, key=lambda x: abs(sd.days_between(x, d)))), f'NT-proBNP {v:g} with dyspnea/edema documented'))
        return min(cands) if cands else None
    if target == 'colorectal_cancer':
        cands = [(d, 'positive FIT') for d, _ in s.get('fit_positive', [])]
        cands += [(d, 'rectal bleeding documented') for d, _ in s.get('rectal_bleeding', [])]
        ferr = s.get('ferritin_low', [])
        for d, v in s.get('hgb_low', []):
            near = [(x, fv) for x, fv in ferr if abs(sd.days_between(x, d)) <= 90]
            if near: cands.append((max(d, near[0][0]), f'iron-deficiency anemia (Hgb {v:g}, ferritin {near[0][1]:g})'))
        return min(cands) if cands else None
    return None


def first_signal(target, s):
    kinds = {'ckd': ('egfr_low', 'uacr_high'), 'diabetes': ('a1c_high', 'glucose_high', 'dm_drug'), 'hypertension': ('bp_high',),
             'heart_failure': ('ef_low', 'bnp_high', 'hf_symptom'), 'colorectal_cancer': ('fit_positive', 'rectal_bleeding', 'hgb_low')}.get(target, ())
    rows = sorted((d, k) for k in kinds for d, _ in s.get(k, []))
    return rows[0] if rows else None


def unconfirmed(target, s):
    """One qualifying value that was never repeated (no criterion): the repeat test itself is the open loop."""
    kinds = {'ckd': ('egfr_low', 'uacr_high'), 'diabetes': ('a1c_high', 'glucose_high'), 'hypertension': ('bp_high',)}.get(target, ())
    rows = sorted((d, k, v) for k in kinds for d, v in s.get(k, []))
    return rows[0] if rows else None


def delay_summary(delays):
    if not delays: return {'n': 0}
    q = lambda x: round(sd.quantile(delays, x), 1)
    return {'n': len(delays), 'median_days': q(0.5), 'p25_days': q(0.25), 'p75_days': q(0.75), 'p90_days': q(0.9), 'mean_days': round(sum(delays) / len(delays), 1),
            'share_over_90': round(sum(d > 90 for d in delays) / len(delays), 4), 'share_over_180': round(sum(d > 180 for d in delays) / len(delays), 4),
            'share_over_365': round(sum(d > 365 for d in delays) / len(delays), 4),
            'histogram': [{'from': lo, 'to': hi, 'patients': sum(lo <= d and (hi is None or d <= hi) for d in delays)} for lo, hi in BINS]}


# ---- data ------------------------------------------------------------------------------------------------------------------
def _icd_match(code, prefixes):
    c = (code or '').upper().replace('.', '')
    return bool(c) and any(c.startswith(px.replace('.', '')) for px in prefixes)


def _partial(value):
    """Full date, or the first day of a partial year/month date (approximate)."""
    d = sd.day(value)
    if d: return d, False
    m = re.match(r'^(\d{4})(?:-(\d{2}))?$', str(value or '').strip())
    if m and 1900 < int(m.group(1)) < 2100: return f"{m.group(1)}-{m.group(2) or '01'}-01", True
    return None, False


def diagnoses(db, cap, target, pids=None):
    """{patient: (earliest diagnosis date, source, approximate)} for the target from every received source."""
    t = TARGETS[target]
    best = {}

    def put(pid, d, src, approx=False):
        if not d or (pids is not None and pid not in pids): return
        if pid not in best or d < best[pid][0]: best[pid] = (d, src, approx)
    if 'condition' in cap['tables']:
        cols = sd.columns(db, 'condition')
        dcol = 'diagnosis_date' if 'diagnosis_date' in cols else 'NULL'
        for pid, concept, icd, onset, ev, rec, dd, src in db.execute(
                f'SELECT patient_id, concept, icd10, onset, event_date, recorded_date, {dcol}, source_table FROM condition'):
            if concept in t['concepts'] or _icd_match(icd, t['icd10']):
                d, approx = _partial(dd or onset)
                if not d: d, approx = sd.day(ev) or sd.day(rec), False
                put(pid, d, src or 'condition', approx)
    if 'incident_diagnosis' in cap['tables']:
        cols = sd.columns(db, 'incident_diagnosis')
        icd = 'icd10' if 'icd10' in cols else 'NULL'; concept = 'concept' if 'concept' in cols else 'NULL'
        for pid, c, code, d in db.execute(f'SELECT patient_id, {concept}, {icd}, diagnosis_date FROM incident_diagnosis'):
            if c in t['concepts'] or _icd_match(code, t['icd10']): put(pid, sd.day(d), 'incident_diagnosis')
    if 'encounter_diagnosis' in cap['tables']:
        vdate = {e: d for e, d in db.execute('SELECT encounter_id, event_date FROM visit')}
        for pid, enc, code in db.execute('SELECT patient_id, encounter_id, icd10 FROM encounter_diagnosis'):
            if _icd_match(code, t['icd10']): put(pid, sd.day(vdate.get(enc)), 'encounter_diagnosis')
    if 'claim' in cap['tables'] and 'diagnosis_codes' in sd.columns(db, 'claim'):
        for pid, d, principal, codes in db.execute('SELECT patient_id, service_date, principal_diagnosis, diagnosis_codes FROM claim'):
            try:
                allc = [principal] + (json.loads(codes) if codes else [])
            except (TypeError, ValueError):
                allc = [principal]
            if any(_icd_match(c, t['icd10']) for c in allc if isinstance(c, str)): put(pid, sd.day(d), 'claim')
    if 'payer_claim_diagnosis' in cap['tables'] and 'payer_claim' in cap['tables']:
        pcd = sd.columns(db, 'payer_claim')
        date_col = next((c for c in ('service_date', 'service_from_date', 'first_service_date') if c in pcd), None)
        if date_col:
            for pid, d, code in db.execute(f'SELECT pc.patient_id, pc.{date_col}, d.icd10 FROM payer_claim_diagnosis d JOIN payer_claim pc USING (payer_claim_id)'):
                if _icd_match(code, t['icd10']): put(pid, sd.day(d), 'payer_claim')
    return best


def signals(db, cap, pids=None):
    """{patient: {kind: sorted [(date, value)]}} for every criterion signal kind."""
    out = defaultdict(lambda: defaultdict(list))
    cols = sd.columns(db, 'measurement')
    cur = ' AND result_current = 1' if 'result_current' in cols else ''
    exc = ' AND excluded_reason IS NULL' if 'excluded_reason' in cols else ''
    sexes = {pid: (sex or '').upper()[:1] for pid, sex in db.execute('SELECT patient_id, sex_reported FROM patient')}
    for pid, concept, v, d in db.execute(f"SELECT patient_id, concept, value_canonical, coalesce(event_date, recorded_date) FROM measurement "
                                         f"WHERE concept IN ({','.join('?' * len(LAB_CONCEPTS))}) AND value_canonical IS NOT NULL{cur}{exc}", LAB_CONCEPTS):
        if pids is not None and pid not in pids: continue
        d = sd.day(d)
        if not d: continue
        if concept == 'egfr' and v < 60: out[pid]['egfr_low'].append((d, v))
        elif concept == 'uacr' and v >= 30: out[pid]['uacr_high'].append((d, v))
        elif concept == 'hba1c' and v >= 6.5: out[pid]['a1c_high'].append((d, v))
        elif concept == 'glucose' and v >= 200: out[pid]['glucose_high'].append((d, v))
        elif concept == 'nt_probnp' and v >= 125: out[pid]['bnp_high'].append((d, v))
        elif concept == 'hemoglobin' and v < (12 if sexes.get(pid) == 'F' else 13): out[pid]['hgb_low'].append((d, v))
        elif concept == 'ferritin' and v < 30: out[pid]['ferritin_low'].append((d, v))
        elif concept == 'systolic_bp' and v >= 140: out[pid]['bp_high'].append((d, v))
        elif concept == 'diastolic_bp' and v >= 90: out[pid]['bp_high'].append((d, v))
    if 'note_fact' in cap['tables']:
        for pid, ft, v, d in db.execute("SELECT patient_id, fact_type, value, event_date FROM note_fact WHERE coalesce(negated, 0) = 0 "
                                        "AND coalesce(temporality, 'current') NOT IN ('historical', 'hypothetical', 'family') "
                                        "AND fact_type IN ('ejection_fraction', 'dyspnea', 'edema', 'symptom')"):
            if pids is not None and pid not in pids: continue
            d = sd.day(d)
            if not d: continue
            if ft == 'ejection_fraction':
                try:
                    ef = float(v)
                except (TypeError, ValueError):
                    continue
                if ef <= 40: out[pid]['ef_low'].append((d, ef))
            elif ft in ('dyspnea', 'edema') and str(v) not in ('none', 'absent'):
                out[pid]['hf_symptom'].append((d, 1.0))
            elif ft == 'symptom':
                out[pid]['symptom:' + str(v)].append((d, 1.0))
                if str(v) == 'bleeding': out[pid]['rectal_bleeding_candidate'].append((d, 1.0))
    for stream in ('diagnostic_test_reports', 'received_diagnostic_reports', 'imaging_report_fragments', 'inpatient_cardiac_reports'):
        if stream not in cap['streams']: continue
        for pid, d, payload in db.execute('SELECT patient_id, coalesce(event_date, recorded_date, available_date), payload FROM record WHERE stream = ?', (stream,)):
            if pids is not None and pid not in pids: continue
            r = json.loads(payload); d = sd.day(d)
            m = r.get('measurements') if isinstance(r.get('measurements'), dict) else {}
            ef = m.get('LVEF_percent')
            if ef is None and '% LVEF' in str(r.get('unit') or ''): ef = r.get('value')
            if d and isinstance(ef, (int, float)) and ef <= 40: out[pid]['ef_low'].append((d, float(ef)))
    for stream in ('cancer_screening_results', 'received_screening_reports'):
        if stream not in cap['streams']: continue
        for pid, d, payload in db.execute('SELECT patient_id, coalesce(event_date, available_date), payload FROM record WHERE stream = ?', (stream,)):
            if pids is not None and pid not in pids: continue
            r = json.loads(payload)
            if re.search(r'fit|fecal|occult|stool', ' '.join(str(r.get(k) or '') for k in ('test', 'method')).lower()) and \
                    str(r.get('result') or '').lower() in ('positive', 'abnormal', 'detected'):
                d = sd.day(r.get('performed_date')) or sd.day(d)
                if d: out[pid]['fit_positive'].append((d, 1.0))
    # rectal bleeding: a bleeding symptom documented with lower-GI wording in the evidence text
    if 'note_fact' in cap['tables']:
        for pid, d, ev in db.execute("SELECT patient_id, event_date, evidence_text FROM note_fact WHERE fact_type = 'symptom' AND value = 'bleeding' "
                                     "AND coalesce(negated, 0) = 0"):
            if (pids is None or pid in pids) and sd.day(d) and re.search(r'rect|stool|bowel|hematochezia|melena|blood per', (ev or '').lower()):
                out[pid]['rectal_bleeding'].append((sd.day(d), 1.0))
    for stream in ('endoscopy_reports', 'received_procedure_reports', 'procedures'):  # colonoscopy = the red flag was evaluated
        if stream not in cap['streams']: continue
        for pid, d, payload in db.execute('SELECT patient_id, event_date, payload FROM record WHERE stream = ?', (stream,)):
            if (pids is None or pid in pids) and sd.day(d) and 'colonoscop' in payload.lower(): out[pid]['colonoscopy'].append((sd.day(d), 1.0))
    if 'medication' in cap['tables']:
        for pid, d in db.execute(f"SELECT patient_id, event_date FROM medication WHERE drug_class IN ({','.join('?' * len(DM_DRUGS))}) "
                                 f"AND evidence IN ('dispensed', 'claim', 'ordered', 'payer_claim')", DM_DRUGS):
            if (pids is None or pid in pids) and sd.day(d): out[pid]['dm_drug'].append((sd.day(d), 1.0))
    for kinds in out.values():
        for k in kinds: kinds[k].sort()
    return out


def capability(db):
    t, st = sd.tables(db), sd.streams(db)
    cap = {'version': sd.meta(db).get('version'), 'tables': t, 'streams': st}
    gaps = []
    if 'incident_diagnosis' not in t: gaps.append('No incident_diagnosis table (warehouse before v2.2): diagnosis dates come from condition onset/recorded dates, '
                                                  'encounter and claim diagnoses; partial onset dates (year or month only) are dated to their first day.')
    if 'encounter_diagnosis' not in t: gaps.append('No encounter diagnoses: related-complaint visits use note facts only.')
    if 'note_fact' not in t: gaps.append('No note facts: symptoms, EF in notes and rectal bleeding are not available.')
    if 'chief_complaint' not in st and 'visit' in t and 'principal_icd10' not in sd.columns(db, 'visit'):
        gaps.append('No chief complaint or visit principal diagnosis: look-forward symptom index visits cannot be identified.')
    cap['data_gaps'] = gaps
    return cap


# ---- analyses ----------------------------------------------------------------------------------------------------------------
def _related(target, v, sig_days, sym_days):
    """A visit is related when its principal ICD-10 is a target symptom code, or a symptom/target lab was recorded that day."""
    d, kind, org, clin, tc, code = v
    return _icd_match(code, TARGETS[target]['symptom_icd']) or d in sig_days or d in sym_days


def lookback(p, dx, sig, visits, obs_start, people, as_of):
    target = p['target']; t = TARGETS[target]
    cases, prevalent, approx = [], 0, 0
    for pid, (ddate, src, approx_d) in dx.items():
        if p['period']['start'] and ddate < p['period']['start']: continue
        if p['period']['end'] and ddate > p['period']['end']: continue
        start = obs_start.get(pid)
        if not start or sd.days_between(start, ddate) < p['washout_days']:
            prevalent += 1; continue
        if approx_d: approx += 1
        s = sig.get(pid, {})
        before = {k: [x for x in rows if x[0] < ddate] for k, rows in s.items()}
        crit = criterion_date(target, before) if target in CRITERION_TARGETS else None
        first = first_signal(target, before)
        lo = sd.shift(ddate, -p['lookback_days'])
        sym_kinds = [k for k in before if k.split(':')[-1] in t['symptom_facts'] or (k == 'hf_symptom' and target == 'heart_failure')]
        sym_days = {x[0] for k in sym_kinds for x in before[k]}
        sig_days = {x[0] for k, rows in before.items() if not k.startswith('symptom') for x in rows}
        vs = [v for v in visits.get(pid, ()) if v[1] == 'visit' and lo <= v[0] < ddate and v[4] in IN_PERSON]
        related = [v for v in vs if _related(target, v, sig_days, sym_days)]
        missed = [v for v in vs if crit and v[0] >= crit[0]]
        pat = people.get(pid) or {}
        cases.append({'patient_id': pid, 'diagnosis_date': ddate, 'diagnosis_source': src, 'approximate_date': approx_d,
                      'criterion_date': crit[0] if crit else None, 'criterion': crit[1] if crit else None,
                      'delay_days': sd.days_between(crit[0], ddate) if crit else None,
                      'first_signal_date': first[0] if first else None, 'signal_lead_days': sd.days_between(first[0], ddate) if first else None,
                      'missed_opportunity_visits': len(missed), 'related_visits_in_lookback': len(related),
                      'visits_in_lookback': len(vs), 'practice': pat.get('practice') or 'unattributed', 'pcp': pat.get('pcp') or 'unassigned'})
    cases.sort(key=lambda c: (-(c['delay_days'] if c['delay_days'] is not None else -1), c['patient_id']))
    delays = [c['delay_days'] for c in cases if c['delay_days'] is not None]
    by = defaultdict(list)
    for c in cases: by[c['practice'] if p['group_by'] == 'practice' else c['pcp'] if p['group_by'] == 'pcp' else 'all'].append(c)
    groups = []
    for g, rows in by.items():
        dl = [c['delay_days'] for c in rows if c['delay_days'] is not None]
        groups.append({'group': g, 'incident_diagnoses': len(rows), 'with_prior_criterion': len(dl), 'median_delay_days': sd.median(dl),
                       'over_180_days': sum(x > 180 for x in dl), 'missed_opportunity_visits': sum(c['missed_opportunity_visits'] for c in rows),
                       'related_visits': sum(c['related_visits_in_lookback'] for c in rows)})
    groups.sort(key=lambda r: (-(r['median_delay_days'] or 0), str(r['group'])))
    return {'diagnosed_patients': len(dx), 'incident': len(cases), 'prevalent_excluded': prevalent, 'approximate_dates': approx,
            'with_prior_criterion': len(delays), 'with_prior_signal': sum(c['first_signal_date'] is not None for c in cases),
            'with_related_visits': sum(c['related_visits_in_lookback'] > 0 for c in cases),
            'missed_opportunity_visits': sum(c['missed_opportunity_visits'] for c in cases),
            'patients_with_missed_visits': sum(c['missed_opportunity_visits'] > 0 for c in cases),
            'delay': delay_summary(delays), 'by_group': groups, 'cases': cases}


def undiagnosed(p, dx, sig, visits, people, as_of):
    target = p['target']
    prompts = []
    for pid, s in sig.items():
        if pid in dx or pid not in people: continue
        crit = criterion_date(target, s)
        kind = 'criterion_met'
        if not crit:
            u = unconfirmed(target, s)
            if not u: continue
            crit, kind = (u[0], f"one {u[1].replace('_', ' ')} value ({u[2]:g}) on {u[0]}, never repeated"), 'unconfirmed_signal'
            later = [x for x in s.get(u[1], []) if x[0] > u[0]]
            if later: continue  # repeated but criterion not met (e.g. within 90 days): not a prompt
        if target == 'colorectal_cancer' and any(d >= crit[0] for d, _ in s.get('colonoscopy', [])): continue  # red flag evaluated
        vs = [v for v in visits.get(pid, ()) if v[1] == 'visit' and v[0] >= crit[0]]
        pat = people[pid]
        review = ('Review whether the red flag was evaluated (colonoscopy or a documented reason): review prompt, not a diagnosis.'
                  if target == 'colorectal_cancer' else f"Review whether {TARGETS[target]['label']} is present and documented (review prompt, not a diagnosis).")
        prompts.append({'patient_id': pid, 'prompt': kind, 'evidence': crit[1], 'criterion_date': crit[0], 'days_since': sd.days_between(crit[0], as_of),
                        'visits_since': len(vs), 'last_visit': vs[-1][0] if vs else None, 'practice': pat.get('practice') or 'unattributed',
                        'pcp': pat.get('pcp') or 'unassigned', 'risk_adjustment_suspect': TARGETS[target]['suspect'] if kind == 'criterion_met' else None,
                        'review': review})
    prompts.sort(key=lambda r: (r['prompt'] != 'criterion_met', -r['visits_since'], -r['days_since'], r['patient_id']))
    by = Counter(r['practice'] if p['group_by'] == 'practice' else r['pcp'] if p['group_by'] == 'pcp' else 'all' for r in prompts if r['prompt'] == 'criterion_met')
    return {'patients': len(prompts), 'criterion_met': sum(r['prompt'] == 'criterion_met' for r in prompts),
            'unconfirmed_signal': sum(r['prompt'] == 'unconfirmed_signal' for r in prompts),
            'with_visit_since': sum(r['visits_since'] > 0 and r['prompt'] == 'criterion_met' for r in prompts),
            'by_group': [{'group': g, 'criterion_met': n} for g, n in by.most_common()], 'prompts': prompts}


def look_forward(p, db, cap, visits, dx_all, people, as_of):
    """ED treat-and-release index visits -> admission within the horizon with the target diagnosed around the admission."""
    target = p['target']; t = TARGETS[target]
    stays = defaultdict(list)
    for pid, a in db.execute('SELECT patient_id, admit_date FROM stay'):
        if sd.day(a): stays[pid].append(sd.day(a))
    for v in stays.values(): v.sort()
    tdates = defaultdict(list)  # every dated target diagnosis record, not only the first
    for pid, rows in dx_all.items(): tdates[pid] = rows
    end = p['period']['end'] or sd.shift(as_of, -p['horizon_days'])
    start = p['period']['start'] or sd.shift(end, -730)
    index, rows = [], []
    for pid, vs in visits.items():
        for v in vs:
            if v[4] != 'emergency_visit' or not start <= v[0] <= end: continue
            if sd.first_in(stays.get(pid, []), v[0], sd.shift(v[0], 1)): continue  # admitted from the ED: not treat-and-release
            sym = _icd_match(v[5], t['symptom_icd'])
            adm = sd.first_in(stays.get(pid, []), sd.shift(v[0], 2), sd.shift(v[0], p['horizon_days']))
            hit = bool(adm and target != 'any' and any(abs(sd.days_between(adm, d)) <= 7 or adm <= d <= sd.shift(adm, 7) for d in tdates.get(pid, [])))
            index.append((sym, hit))
            if hit: rows.append({'patient_id': pid, 'ed_date': v[0], 'ed_principal_icd10': v[5], 'symptom_index': sym, 'admission_date': adm,
                                 'days_to_admission': sd.days_between(v[0], adm), 'organization': v[2],
                                 'practice': (people.get(pid) or {}).get('practice') or 'unattributed',
                                 'review': f"Review the ED visit for a missed {t['label']} (review prompt)."})
    n_sym = sum(s for s, _ in index); k_sym = sum(h for s, h in index if s)
    n_oth = len(index) - n_sym; k_oth = sum(h for s, h in index if not s)
    rr = None
    if n_sym and n_oth and k_oth: rr = round((k_sym / n_sym) / (k_oth / n_oth), 3)
    rows.sort(key=lambda r: (not r['symptom_index'], r['days_to_admission']))
    return {'period': {'start': start, 'end': end}, 'index_visits': len(index), 'symptom_index_visits': n_sym, 'other_index_visits': n_oth,
            'returns_with_target': k_sym + k_oth, 'symptom_returns': k_sym, 'other_returns': k_oth,
            'symptom_rate': round(k_sym / n_sym, 5) if n_sym else None, 'other_rate': round(k_oth / n_oth, 5) if n_oth else None,
            'symptom_rate_ci95': sd.wilson(k_sym, n_sym) if n_sym else None, 'risk_ratio': rr, 'cases': rows,
            'symptom_codes': list(t['symptom_icd'])}


# ---- entry points ----------------------------------------------------------------------------------------------------------
def run(db, patient_ids, params, as_of=None):
    p = validate(params)
    as_of = sd.as_of_of(db, as_of)
    cap = capability(db)
    pids = set(patient_ids) if patient_ids is not None else None
    people = sd.patients(db, pids)
    report = {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'target_label': TARGETS[p['target']]['label'], 'data_gaps': cap['data_gaps'],
              'capability': {k: v for k, v in cap.items() if k not in ('tables', 'streams', 'data_gaps')}, 'limitations': LIMITATIONS}
    dx = diagnoses(db, cap, p['target'], pids)
    visits = sd.visits(db, pids)
    if p['mode'] == 'look_forward':
        if 'stay' not in cap['tables']:
            report.update(status='unsupported', reason='No hospital stays: the look-forward outcome (admission) cannot be measured.')
        else:
            dx_all = defaultdict(list)
            for pid, (d, _, _) in dx.items(): dx_all[pid].append(d)
            report['look_forward'] = look_forward(p, db, cap, visits, _all_dx_dates(db, cap, p['target'], pids) or dx_all, people, as_of)
            report['status'] = 'answered' if report['look_forward']['index_visits'] else 'empty'
    else:
        sig = signals(db, cap, pids)
        obs = {}
        for pid, vs in visits.items():
            if vs: obs[pid] = vs[0][0]
        for pid, kinds in sig.items():
            first = min((rows[0][0] for rows in kinds.values() if rows), default=None)
            if first and (pid not in obs or first < obs[pid]): obs[pid] = first
        if p['mode'] in ('all', 'lookback'): report['lookback'] = lookback(p, dx, sig, visits, obs, people, as_of)
        if p['mode'] in ('all', 'undiagnosed'): report['undiagnosed'] = undiagnosed(p, dx, sig, visits, people, as_of)
        lb, ud = report.get('lookback') or {}, report.get('undiagnosed') or {}
        report['status'] = 'answered' if (lb.get('incident') or ud.get('patients') or lb.get('diagnosed_patients')) else 'empty'
    report['assumptions'] = _assumptions(p)
    report['method'] = method(report)
    return report


def _all_dx_dates(db, cap, target, pids):
    """Every dated record of the target diagnosis per patient (look-forward needs the admission-time diagnosis, not the first)."""
    t = TARGETS[target]; out = defaultdict(list)
    if 'condition' in cap['tables']:
        for pid, concept, icd, onset, ev, rec in db.execute('SELECT patient_id, concept, icd10, onset, event_date, recorded_date FROM condition'):
            if (pids is None or pid in pids) and (concept in t['concepts'] or _icd_match(icd, t['icd10'])):
                d = sd.day(onset) or sd.day(ev) or sd.day(rec)
                if d: out[pid].append(d)
    if 'encounter_diagnosis' in cap['tables']:
        vdate = {e: d for e, d in db.execute('SELECT encounter_id, event_date FROM visit')}
        for pid, enc, code in db.execute('SELECT patient_id, encounter_id, icd10 FROM encounter_diagnosis'):
            if (pids is None or pid in pids) and _icd_match(code, t['icd10']) and sd.day(vdate.get(enc)): out[pid].append(sd.day(vdate[enc]))
    return out


def execute(path, patient_ids, params, as_of=None):
    db = sd.connect(path)
    try:
        return run(db, patient_ids, params, as_of)
    finally:
        db.close()


LIMITATIONS = ['Review prompts, never diagnoses: a signal can have a benign explanation (acute illness, dehydration, steroid hyperglycaemia, '
               'white-coat blood pressure) that only chart review settles.',
               'Diagnosis dates are the first received record; care before the warehouse window or outside the received feeds is invisible, '
               'so delays are lower bounds on what the data can show and upper bounds where an earlier diagnosis went unrecorded.',
               'Criteria are simplified versions of KDIGO / ADA / ACC-AHA / red-flag definitions (no fasting status, no repeat within the '
               'same acute episode excluded, BP setting not checked).',
               'Synthetic data.']


def _assumptions(p):
    out = [f"Look-back window {p['lookback_days']} days before each diagnosis; incident = diagnosed >= {p['washout_days']} days after the first observed record."]
    if p['mode'] == 'look_forward':
        out = [f"Index = ED visits without admission within 1 day; outcome = admission 2-{p['horizon_days']} days later with {TARGETS[p['target']]['label']} "
               f"diagnosed within 7 days of admission; symptom index = ED principal ICD-10 in {', '.join(TARGETS[p['target']]['symptom_icd'])}."]
    return out


def method(report):
    p = report['parameters']; t = TARGETS[p['target']]
    if p['mode'] == 'look_forward':
        return ('SPADE look-forward: ED treat-and-release visits followed forward for a hospital admission with the target diagnosis; rates in '
                'symptom-coded index visits vs other ED discharges with a risk ratio and Wilson interval. Cases are review prompts.')
    return (f"Diagnostic delay look-back for {t['label']}: earliest diagnosis per patient across condition/problem-list, incident-diagnosis, "
            f"encounter and claim records (ICD-10 {', '.join(t['icd10'])}"
            + (f"; concepts {', '.join(t['concepts'])}" if t['concepts'] else '') + '); incident cases only (washout). Criterion dates from labs, '
            'vitals, reports and note facts (see module rules); delay = diagnosis - criterion; missed-opportunity visits = completed visits between '
            'criterion and diagnosis; related visits = visits in the look-back window with a related symptom code or note fact or a target '
            'signal that day. Undiagnosed = criterion met (or one qualifying value never repeated) and no diagnosis by the snapshot: review prompts.')


def summarize(report, limit=None):
    p = report['parameters']; t = TARGETS[p['target']]
    lim = limit or p['top']
    if report.get('status') == 'unsupported': return 'Diagnostic delay unavailable: ' + report.get('reason', ''), []
    if p['mode'] == 'look_forward':
        f = report.get('look_forward') or {}
        if not f.get('index_visits'): return f"No ED treat-and-release visits in {f.get('period', {}).get('start')}..{f.get('period', {}).get('end')}.", []
        txt = (f"Look-forward for missed {t['label']}: {f['index_visits']} ED treat-and-release visits ({f['symptom_index_visits']} with a related symptom code); "
               f"{f['returns_with_target']} were admitted within {p['horizon_days']} days with {t['label']} ({f['symptom_returns']} after a symptom visit"
               + (f", rate {f['symptom_rate']:.2%}" if f['symptom_rate'] is not None else '') + ')'
               + (f"; risk ratio vs other ED discharges {f['risk_ratio']}" if f['risk_ratio'] else '') + '. Cases are review prompts, not diagnoses.')
        return txt, f['cases'][:lim]
    parts = []
    lb, ud = report.get('lookback'), report.get('undiagnosed')
    if lb:
        d = lb['delay']
        parts.append(f"{lb['incident']} incident {t['label']} diagnoses ({lb['prevalent_excluded']} prevalent excluded); "
                     + (f"{lb['with_prior_criterion']} met the criterion before diagnosis: median delay {d['median_days']:g} days "
                        f"(IQR {d['p25_days']:g}-{d['p75_days']:g}), {d['share_over_180']:.0%} over 180 days, "
                        f"{lb['missed_opportunity_visits']} missed-opportunity visits in {lb['patients_with_missed_visits']} patients"
                        if d.get('n') else 'none met the criterion before diagnosis')
                     + f"; {lb['with_related_visits']} had related-symptom visits in the {p['lookback_days']}-day look-back.")
    if ud:
        parts.append(f"{ud['criterion_met']} patients meet the {t['label']} criterion with no diagnosis ({ud['with_visit_since']} seen since), "
                     f"{ud['unconfirmed_signal']} more have one qualifying value never repeated: review prompts, not diagnoses.")
    if not parts: return 'Nothing in scope.', []
    table = (ud or {}).get('prompts') if p['mode'] in ('all', 'undiagnosed') else (lb or {}).get('cases')
    return ' '.join(parts), (table or [])[:lim]


def follow_up():
    return ['How long were CKD diagnoses delayed after eGFR first fell below 60?', 'Which patients meet diabetes criteria without a diabetes diagnosis?',
            'Missed opportunities before heart failure diagnoses', 'Look-back for colorectal cancer: iron deficiency or bleeding before diagnosis']
