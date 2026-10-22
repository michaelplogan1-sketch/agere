"""Guideline conformance of care pathways over an object-centric event log (OCEL 2.0; ocel.py).

FRONTIER_RESEARCH_2026-10 #44: model pathways as object-centric logs (flattening one case per event misleads, arXiv
2403.01975) and check declarative guideline rules (Leonardi/Montani-style conformance). Rules the warehouse supports:
  HF-FU7     heart-failure inpatient discharge -> outpatient follow-up (primary care, specialty or telehealth) within
             follow_up_days (default 7). Basis: 2022 AHA/ACC/HFSA heart failure guideline (early follow-up, generally within
             7 days of discharge); CMS/NQF transitions-of-care follow-up.
  HF-GDMT30  heart-failure inpatient discharge -> on guideline-directed medical therapy: a fill of an evidence-based beta
             blocker, ACE inhibitor, ARB, ARNI, MRA or SGLT2 inhibitor whose supply covers the discharge or that is filled
             within gdmt_days (default 30). Basis: 2022 AHA/ACC/HFSA guideline (initiate/continue GDMT before and after discharge).
  DM-A1C6    diabetes -> HbA1c at least every 6 months: a result in each half of the trailing 12 months. Basis: ADA Standards
             of Care 2025, section 6 (A1C at least twice yearly when meeting goals).
  DM-KED     diabetes -> eGFR and urine albumin-creatinine ratio in the trailing 12 months. Basis: ADA Standards of Care 2025
             section 11; NCQA HEDIS KED.
  CKD-MON    chronic kidney disease -> eGFR and uACR in the trailing 12 months. Basis: KDIGO 2024 CKD guideline
             (monitor GFR and albuminuria at least annually).
Each rule reports eligible objects, conformant objects, fitness with a Wilson interval, not-yet-evaluable objects (follow-up
window still open at the as-of date) and the deviating patients with the source events. Stays are stay OBJECTS merged
across ADT / encounter / payer-claim reports; object-centric metrics state what a flattened log would have miscounted.
Synthetic warehouse only; conformance to a rule is a process observation, not a clinical quality judgment.
"""
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import math
import re
import sqlite3

from . import ocel

VERSION = 'guideline-conformance/1'
OP = 'guideline_conformance'
PATHWAYS = ('heart_failure', 'diabetes', 'ckd', 'all')
GDMT = ('hf_evidence_based_beta_blocker', 'ace_inhibitor', 'arb', 'arni', 'mra', 'sglt2_inhibitor')
RULES = {
    'HF-FU7': ('heart_failure', 'Heart-failure discharge followed by an outpatient visit within {follow_up_days} days',
               '2022 AHA/ACC/HFSA Guideline for the Management of Heart Failure (early post-discharge follow-up, generally within 7 days)'),
    'HF-GDMT30': ('heart_failure', 'Heart-failure discharge on guideline-directed medical therapy (supply at discharge or fill within {gdmt_days} days)',
                  '2022 AHA/ACC/HFSA Guideline for the Management of Heart Failure (GDMT initiated or continued around discharge)'),
    'DM-A1C6': ('diabetes', 'Diabetes with an HbA1c in each half of the trailing {window_months} months',
                'ADA Standards of Care in Diabetes 2025, section 6 (A1C at least twice yearly)'),
    'DM-KED': ('diabetes', 'Diabetes with eGFR and uACR in the trailing {window_months} months',
               'ADA Standards of Care in Diabetes 2025, section 11; NCQA HEDIS Kidney Health Evaluation (KED)'),
    'CKD-MON': ('ckd', 'Chronic kidney disease with eGFR and uACR in the trailing {window_months} months',
                'KDIGO 2024 Clinical Practice Guideline for CKD (GFR and albuminuria monitored at least annually)'),
}
CONCEPT = {'heart_failure': ('heart_failure', 'heart_failure_reduced_ef'), 'diabetes': ('diabetes', 'type_1_diabetes', 'type_2_diabetes'),
           'ckd': ('chronic_kidney_disease',)}

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'GUIDELINE PATHWAY CONFORMANCE over an object-centric (OCEL 2.0) event log: HF discharge -> 7-day follow-up and '
                  'GDMT, diabetes HbA1c every 6 months and kidney testing, CKD eGFR+uACR; fitness per rule and deviating patients.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'pathway': 'heart_failure|diabetes|ckd|all', 'window_months': 12, 'follow_up_days': 7, 'gdmt_days': 30,
                           'export': 'summary|ocel', 'top': 50},
            'meaning': 'Builds an OCEL 2.0 object-centric log (patients, hospital stays merged across ADT/encounter/claim sources, '
                       'outpatient encounters, fills, lab results) and checks declarative guideline rules: HF discharge followed by an '
                       'outpatient visit within 7 days (2022 AHA/ACC/HFSA), HF discharge on GDMT within 30 days, diabetes HbA1c every 6 '
                       'months (ADA 2025), diabetes eGFR+uACR yearly (HEDIS KED), CKD eGFR+uACR yearly (KDIGO 2024). Reports fitness, '
                       'Wilson intervals, open (not yet evaluable) cases, deviating patients with source events and the object-centric '
                       'overcounts a flattened log would make.'}
NATIVE = ('value',)


def schema(obj, string, array):
    return obj({'pathway': string(PATHWAYS),
                'window_months': {'type': 'integer', 'minimum': 3, 'maximum': 36, 'description': 'Trailing window ending at the as-of date'},
                'follow_up_days': {'type': 'integer', 'minimum': 1, 'maximum': 60, 'description': 'HF discharge follow-up window (default 7)'},
                'gdmt_days': {'type': 'integer', 'minimum': 1, 'maximum': 90, 'description': 'HF discharge GDMT window (default 30)'},
                'export': string(('summary', 'ocel')), 'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}})


def validate(p):
    allowed = {'pathway', 'window_months', 'follow_up_days', 'gdmt_days', 'export', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('guideline_conformance parameters: ' + ', '.join(sorted(allowed)))
    out = {'pathway': p.get('pathway', 'all'), 'window_months': p.get('window_months', 12), 'follow_up_days': p.get('follow_up_days', 7),
           'gdmt_days': p.get('gdmt_days', 30), 'export': p.get('export', 'summary'), 'top': p.get('top', 50)}
    if out['pathway'] not in PATHWAYS: raise ValueError(f'pathway: {PATHWAYS}')
    for k, lo, hi in (('window_months', 3, 36), ('follow_up_days', 1, 60), ('gdmt_days', 1, 90), ('top', 1, 500)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k}: integer {lo}..{hi}')
    if out['export'] not in ('summary', 'ocel'): raise ValueError('export: summary|ocel')
    return out


def wilson(k, n, z=1.96):
    if not n: return None, None
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)


def _d(s): return date.fromisoformat(str(s)[:10])


def _cohort(db, pathway, keep):
    names = CONCEPT[pathway]
    rows = db.execute(f"SELECT patient_id, min(first_date) FROM concept_patient WHERE kind = 'condition' AND concept IN "
                      f"({','.join('?' * len(names))}) GROUP BY patient_id", names).fetchall()
    return {pid: str(d)[:10] for pid, d in rows if d and (keep is None or pid in keep)}


def _labs(db, pids, concepts_, start, end):
    if not pids: return []
    out = []
    for pid, rid, d, c in db.execute(f"SELECT patient_id, record_id, event_date, concept FROM measurement WHERE concept IN "
                                     f"({','.join('?' * len(concepts_))}) AND event_date BETWEEN ? AND ? AND "
                                     f"coalesce(result_current, 1) = 1", (*concepts_, start, end + 'T99')):
        if pid in pids: out.append((rid, pid, str(d)[:10], c))
    return out


def run(db, patient_ids, p, as_of):
    keep = set(patient_ids) if patient_ids is not None else None
    end = str(as_of)[:10]
    start = (_d(end) - timedelta(days=round(p['window_months'] * 30.44))).isoformat()
    wanted = ('heart_failure', 'diabetes', 'ckd') if p['pathway'] == 'all' else (p['pathway'],)
    stays, visits, fills, labs, cohorts = [], [], [], [], {}
    for pw in wanted: cohorts[pw] = _cohort(db, pw, keep)
    if 'heart_failure' in wanted:
        hf = cohorts['heart_failure']
        raw = [r for r in db.execute("SELECT patient_id, admit_date, discharge_date, stay_source, organization_id FROM stay WHERE "
                                     "lower(type) LIKE 'inpatient%' AND discharge_date BETWEEN ? AND ?", (start, end + 'T99'))
               if r[0] in hf and hf[r[0]] <= str(r[2])[:10]]
        stays = ocel.merge_stays(raw)
        horizon = (_d(end)).isoformat()
        visits = [(e, pid, str(d)[:10], s) for e, pid, d, s in db.execute(
            f"SELECT encounter_id, patient_id, event_date, setting FROM visit WHERE setting IN ({','.join('?' * len(ocel.OUTPATIENT))}) "
            "AND event_date BETWEEN ? AND ?", (*ocel.OUTPATIENT, start, horizon + 'T99')) if pid in hf]
        lookback = (_d(start) - timedelta(days=365)).isoformat()
        fill_rows = [r for r in db.execute(f"SELECT fill_id, patient_id, fill_date, drug_class, supply_end FROM fill WHERE drug_class IN "
                                           f"({','.join('?' * len(GDMT))}) AND fill_date BETWEEN ? AND ?", (*GDMT, lookback, horizon + 'T99'))
                     if r[1] in hf]
        fills = [(f, pid, str(d)[:10], c) for f, pid, d, c, _ in fill_rows]
        supply = {f'fill:{f}': str(s)[:10] if s else None for f, _, _, _, s in fill_rows}
    if 'diabetes' in wanted:
        labs += _labs(db, set(cohorts['diabetes']), ('hba1c', 'egfr', 'uacr'), start, end)
    if 'ckd' in wanted:
        labs += _labs(db, set(cohorts['ckd']), ('egfr', 'uacr'), start, end)
    labs = list(dict.fromkeys(labs))
    log = ocel.build(stays, visits, fills, labs, [(pid, {'pathways': pw}) for pw, c in cohorts.items() for pid in c])
    rules, deviations = [], []

    def add_rule(rid, eligible, conformant, open_, devs, **extra):
        pw, title, basis = RULES[rid]
        lo, hi = wilson(conformant, eligible)
        rules.append({'rule': rid, 'pathway': pw, 'title': title.format(**p), 'basis': basis, 'eligible': eligible,
                      'conformant': conformant, 'deviating': eligible - conformant, 'fitness': round(conformant / eligible, 4) if eligible else None,
                      'fitness_low': lo, 'fitness_high': hi, 'not_yet_evaluable': open_, **extra})
        deviations.extend({'rule': rid, **d} for d in devs)

    if 'heart_failure' in wanted:
        sids = [s['id'] for s in stays]
        fu = ocel.link_followups(log, sids, 'visit', p['follow_up_days'])
        gd = ocel.link_followups(log, sids, 'fill', p['gdmt_days'], qualifier='gdmt_window')
        by_patient_fills = defaultdict(list)
        for e in log.events:
            if e['type'] == 'fill':
                by_patient_fills[e['relationships'][0][0]].append(e)
        for rid, days in (('HF-FU7', p['follow_up_days']), ('HF-GDMT30', p['gdmt_days'])):
            eligible = conf = open_ = 0; devs = []
            for s in stays:
                due = _d(s['discharge']) + timedelta(days=days)
                hits = fu[s['id']] if rid == 'HF-FU7' else gd[s['id']]
                covered = False
                if rid == 'HF-GDMT30':
                    for e in by_patient_fills.get(f"patient:{s['patient_id']}", []):
                        oid = e['relationships'][1][0]
                        if e['time'] <= s['discharge'] and (supply.get(oid) or '') >= s['discharge']: covered = True; break
                if not hits and not covered and due > _d(end): open_ += 1; continue
                eligible += 1
                if hits or covered: conf += 1; continue
                devs.append({'patient_id': s['patient_id'], 'object': s['id'], 'discharge': s['discharge'],
                             'sources': sorted(set(s['sources'])), 'deviation': (f'no outpatient visit within {days} days' if rid == 'HF-FU7'
                                                                                else f'no GDMT supply at discharge or fill within {days} days')})
            add_rule(rid, eligible, conf, open_, devs, unit='hospital stay objects')
    lab_by = defaultdict(lambda: defaultdict(list))
    for rid_, pid, d, c in labs: lab_by[pid][c].append(d)
    mid = (_d(start) + (_d(end) - _d(start)) / 2).isoformat()
    for pw, rid in (('diabetes', 'DM-A1C6'), ('diabetes', 'DM-KED'), ('ckd', 'CKD-MON')):
        if pw not in wanted: continue
        pts = {pid: d for pid, d in cohorts[pw].items() if d <= start}  # diagnosed before the window: the whole window applies
        conf, devs = 0, []
        for pid in sorted(pts):
            got = lab_by.get(pid, {})
            if rid == 'DM-A1C6':
                a = got.get('hba1c', [])
                ok = any(d < mid for d in a) and any(d >= mid for d in a)
                why = None if ok else ('no HbA1c in the window' if not a else f"HbA1c only in one half (last {max(a)})")
            else:
                ok = bool(got.get('egfr')) and bool(got.get('uacr'))
                why = None if ok else 'missing ' + ' and '.join(c for c in ('egfr', 'uacr') if not got.get(c))
            if ok: conf += 1
            else: devs.append({'patient_id': pid, 'object': f'patient:{pid}', 'diagnosed': pts[pid], 'deviation': why})
        add_rule(rid, len(pts), conf, 0, devs, unit='patients diagnosed before the window',
                 newly_diagnosed_not_evaluated=sum(1 for d in cohorts[pw].values() if start < d <= end))
    report = {'schema': VERSION, 'ocel_version': ocel.VERSION, 'as_of': end, 'window': {'start': start, 'end': end}, 'parameters': p,
              'rules': rules, 'deviations': deviations[:p['top']], 'deviations_total': len(deviations),
              'object_centric': ocel.metrics(log),
              'cohorts': {pw: len(c) for pw, c in cohorts.items()},
              'method': ('Object-centric event log (OCEL 2.0): patients, hospital-stay objects merged across ADT/encounter/payer-claim '
                         'reports (discharges within 1 day), outpatient encounters, GDMT fills and lab results, with qualified '
                         'event-object relationships; declarative rules checked per object; fitness = conformant / eligible with '
                         'Wilson 95% intervals; cases whose rule window is still open at the as-of date are reported, not judged.'),
              'assumptions': ['Synthetic warehouse; a guideline rule is checked as a process observation, not as clinical appropriateness.',
                              'Heart-failure membership = heart_failure concept first documented on or before the discharge.',
                              'Follow-up = any primary care, specialty or telehealth visit; readmissions, deaths and hospice are not excluded.',
                              'GDMT = a fill of any listed class (evidence-based beta blocker, ACEi, ARB, ARNI, MRA, SGLT2i); contraindications '
                              'and ejection fraction are not assessed.',
                              'Diabetes/CKD rules evaluate patients diagnosed before the window start; results with result_current = 0 are ignored.']}
    if p['export'] == 'ocel':
        report['ocel'] = ocel.to_ocel_json(log, max_objects=20000, max_events=40000)
    return report


def execute(path, patient_ids, params, as_of):
    from . import semantic_layer
    p = validate(params)
    target = semantic_layer.ensure(path) if (Path(path) / 'run.json').exists() else Path(path) / 'semantic.sqlite3'
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        return run(db, patient_ids, p, str(as_of)[:10])
    finally:
        db.close()


def headline(r):
    parts = [f"{x['rule']} {x['fitness']:.0%} ({x['conformant']}/{x['eligible']})" if x['fitness'] is not None else f"{x['rule']} no eligible cases"
             for x in r['rules']]
    oc = r['object_centric']
    extra = (f"; {oc['stay_objects']} stay objects from {oc['discharge_reports']} discharge reports"
             if oc.get('discharge_reports') else '')
    return f"Guideline conformance {r['window']['start']} to {r['window']['end']}: " + '; '.join(parts) + extra


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    ids = sorted({d['patient_id'] for d in report['deviations']})
    out = {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['rules'], 'patient_ids': ids,
           'value': headline(report) + '.', 'method': report['method'], 'limitations': report['assumptions'],
           'definitions': [{'term': x['rule'], 'basis': x['basis']} for x in report['rules']],
           'membership_role': 'evaluated',
           'overall': {'guideline_conformance': {'window': report['window'], 'object_centric': report['object_centric'],
                                                 'deviations': report['deviations'], 'deviations_total': report['deviations_total'],
                                                 **({'ocel': report['ocel']} if 'ocel' in report else {})}}}
    if len(report['rules']) == 1 and report['rules'][0]['fitness'] is not None: out['number'] = report['rules'][0]['fitness']
    return out


# ---- free local-compiler route ----------------------------------------------------------------------------------------
CONFORM_RX = (r'\b(?:guideline|pathway|protocol)\s+(?:conformance|compliance|adherence|deviations?)\b|'
              r'\bconform(?:ance|ing)?\b.*\b(?:guideline|pathway)\b|\b(?:follow(?:ing)?|adher(?:e|ing|ence) to)\s+(?:the\s+)?'
              r'(?:guideline|recommended)\s+(?:care\s+)?(?:pathway|process|path)\b|\bobject[- ]centric\b|\bocel\b|'
              r'\bdeviat(?:e|ions?|ing) from (?:the )?(?:guideline|pathway)\b')
PATHWAY_RX = (('heart_failure', r'\bheart failure\b|\bchf\b|\bhf\b|\bhfref\b'), ('diabetes', r'\bdiabet|\bt2dm?\b|\ba1c\b'),
              ('ckd', r'\bckd\b|\bchronic kidney\b|\bkidney disease\b'))


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(CONFORM_RX, low): return None
    params = {}
    hits = [k for k, rx in PATHWAY_RX if re.search(rx, low)]
    if len(hits) == 1: params['pathway'] = hits[0]
    m = re.search(r'\bwithin (\d{1,2}) days\b', low)
    if m and params.get('pathway') == 'heart_failure': params['follow_up_days'] = int(m.group(1))
    m = re.search(r'\b(?:last|past|trailing) (\d{1,2}) months\b', low)
    if m and 3 <= int(m.group(1)) <= 36: params['window_months'] = int(m.group(1))
    if re.search(r'\bocel\b|\bexport\b|\bevent log\b', low): params['export'] = 'ocel'
    return plan(OP, params, 'Guideline pathway conformance over an object-centric event log', question,
                [f"pathway {params.get('pathway', 'all')}"])


TEMPLATES = ['Are our heart failure discharges following the guideline pathway?', 'pathway conformance for diabetes',
             'How many CKD patients deviate from the guideline monitoring pathway?', 'guideline adherence for heart failure discharges within 14 days',
             'Export the object-centric event log for heart failure', 'Show guideline conformance across our chronic disease pathways',
             'Which diabetic patients deviate from the guideline pathway?']
