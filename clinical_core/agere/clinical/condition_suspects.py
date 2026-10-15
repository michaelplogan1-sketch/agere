"""Suspected chronic conditions: conditions the received labs, vitals, screens and fills support but no diagnosis records.

Builds on the risk-adjustment suspect engine (risk_adjustment._evidence / suspects_for: KDIGO eGFR, ADA HbA1c, LVEF, BMI,
PHQ), tightens it to the stated confirmation rules below and adds criteria it does not cover. A suspect is a
documentation prompt with cited evidence rows, never a diagnosis. Every rule is a parameter (SUSPECT_DEFAULTS):

  diabetes (E11)        ADA Standards of Care 2024, section 2 (Diagnosis and Classification): HbA1c >= 6.5% or fasting
                        plasma glucose >= 126 mg/dL, confirmed by a second abnormal result on a different date (two HbA1c,
                        two FPG, or one of each). Alternatively >= min_fills fills of a diabetes-specific agent (insulin,
                        sulfonylurea, DPP-4 inhibitor); metformin (prediabetes, PCOS) and GLP-1 / SGLT2 agents (obesity, heart
                        failure, CKD) are excluded because their indications are not specific.
  CKD (N18)             KDIGO 2012 CKD guideline: eGFR < 60 mL/min/1.73 m2 on two results >= 90 days apart (risk_adjustment).
  heart failure (I50)   ACC/AHA/HFSA 2022 heart-failure guideline: LVEF <= 40% (HFrEF) in a non-negated, current note fact
                        or result (risk_adjustment).
  obesity (E66)         WHO / CDC adult BMI classes: BMI >= 30 kg/m2 on >= min_readings dates (BMI measured or computed from
                        weight and median height). BMI >= 40 is reported as morbid obesity (E66.01) with the same rule.
  hypertension (I10)    2017 ACC/AHA BP guideline (section 4.1, Table 7: 'use an average of >= 2 readings obtained on >= 2
                        occasions to estimate the individual's level of BP'): >= min_days distinct days whose lowest reading is
                        >= 140 systolic or >= 90 diastolic (stage 2, avoiding single white-coat days) AND the average of the
                        day readings over every measured day is >= 140/90 (hypertension_average). A few high days among many
                        normal ones (pain, acute illness, white-coat) are not the patient's BP level.
  hyperlipidemia (E78)  NCEP ATP III / 2018 AHA/ACC cholesterol guideline: LDL-C >= 190 mg/dL once (severe primary
                        hypercholesterolaemia) or >= 160 mg/dL on >= 2 dates.
  hypothyroidism (E03)  ATA 2014 hypothyroidism guideline: TSH >= 10 mIU/L on >= 2 dates, or >= min_fills levothyroxine fills
                        (levothyroxine's indications are hypothyroidism and TSH suppression after thyroid cancer).
  depression (F32)      PHQ-9 >= 10 (Kroenke 2001: sensitivity and specificity 88% for major depression) on >= min_screens
                        dates. Off by default: a screen is weaker evidence than the others (enable with depression_min_screens).

Each suspect is suppressed when the patient already has any documented code in the category.
"""
from collections import defaultdict
from datetime import date
import statistics

from . import risk_adjustment as ra

VERSION = 'condition-suspects/2'
SUSPECT_DEFAULTS = {'diabetes_confirmations': 2, 'diabetes_min_fills': 2, 'obesity_bmi': 30.0, 'obesity_min_readings': 2,
                    'hypertension_min_days': 2, 'hypertension_average': True, 'ldl_single': 190.0, 'ldl_repeat': 160.0, 'tsh_threshold': 10.0, 'thyroid_min_fills': 2,
                    'depression_min_screens': 0, 'ckd': True, 'heart_failure': True}
DIABETES_SPECIFIC = ('insulin', 'sulfonylurea', 'dpp4_inhibitor')
FILL_EVIDENCE = ('dispensed', 'claim', 'payer_claim', 'pharmacy_history')
CATEGORY = {'diabetes': 'E11', 'ckd': 'N18', 'heart_failure': 'I50', 'obesity': 'E66', 'hypertension': 'I10', 'hyperlipidemia': 'E78',
            'hypothyroidism': 'E03', 'depression': 'F32'}
CITATION = {'diabetes': 'ADA Standards of Care 2024, section 2', 'ckd': 'KDIGO 2012 CKD guideline', 'heart_failure': 'ACC/AHA/HFSA 2022 HF guideline',
            'obesity': 'WHO/CDC adult BMI classification', 'hypertension': '2017 ACC/AHA high blood pressure guideline',
            'hyperlipidemia': 'NCEP ATP III; 2018 AHA/ACC cholesterol guideline', 'hypothyroidism': 'ATA 2014 hypothyroidism guideline',
            'depression': 'Kroenke et al. 2001, PHQ-9 validation'}


def _chunks(ids, n=500):
    ids = sorted(ids)
    for i in range(0, len(ids), n): yield ids[i:i + n]


def extra_evidence(db, pids, start, end):
    """{pid: {kind: [(date, value, record_id)]}} for fasting glucose, LDL, TSH, BP days and diabetes / thyroid fills."""
    ev = defaultdict(lambda: defaultdict(list))
    for ch in _chunks(pids):
        q = ','.join('?' * len(ch))
        for pid, rid, concept, name, lo, val, day in db.execute(
                f"SELECT patient_id, record_id, concept, name, loinc, value_canonical, substr(event_date,1,10) FROM measurement "
                f"WHERE patient_id IN ({q}) AND event_date BETWEEN ? AND ? AND value_canonical IS NOT NULL "
                f"AND concept IN ('glucose','ldl','tsh','systolic_bp','diastolic_bp') AND coalesce(excluded_reason,'') = ''", (*ch, start, end)):
            if concept == 'glucose':
                if lo == '1558-6' or 'FAST' in (name or '').upper(): ev[pid]['fpg'].append((day, val, rid))
            else:
                ev[pid][concept].append((day, val, rid))
        for pid, rid, cls, day in db.execute(
                f"SELECT patient_id, record_id, drug_class, substr(event_date,1,10) FROM medication WHERE patient_id IN ({q}) "
                f"AND drug_class IN ('insulin','sulfonylurea','dpp4_inhibitor','levothyroxine') AND evidence IN ({','.join('?' * len(FILL_EVIDENCE))}) "
                f"AND coalesce(fill_reversed,0) = 0 AND event_date BETWEEN ? AND ?", (*ch, *FILL_EVIDENCE, start, end)):
            ev[pid]['fill_' + cls].append((day, cls, rid))
    return ev


def _dates(rows, pred):
    return sorted({d for d, v, _ in rows if d and v is not None and pred(float(v))})


def hypertension_days(systolic, diastolic, params=None):
    """Stage-2 elevated days supporting a hypertension suspect, or [] (see the module docstring).
    systolic / diastolic: [(date, value, record_id)]. A day's reading is its lowest (representative) value."""
    p = {**SUSPECT_DEFAULTS, **(params or {})}
    day_bp = defaultdict(dict)
    for concept, rows in (('systolic_bp', systolic), ('diastolic_bp', diastolic)):
        for d, v, _ in rows:
            cur = day_bp[d].get(concept)
            day_bp[d][concept] = float(v) if cur is None else min(cur, float(v))
    both = {d: r for d, r in day_bp.items() if 'systolic_bp' in r and 'diastolic_bp' in r}
    high = sorted(d for d, r in both.items() if r['systolic_bp'] >= 140 or r['diastolic_bp'] >= 90)
    if not p['hypertension_min_days'] or len(high) < p['hypertension_min_days']: return []
    if p['hypertension_average']:  # the patient's BP level is the average over all measured occasions
        if not (statistics.mean(r['systolic_bp'] for r in both.values()) >= 140 or
                statistics.mean(r['diastolic_bp'] for r in both.values()) >= 90): return []
    return high


def suspects(db, pids, documented, start, end, params=None, ages=None, sexes=None):
    """{pid: [{'icd10': category, 'condition', 'first_evidence': date, 'evidence': [...], 'citation'}]}.
    documented: {pid: set of documented 3-character categories}."""
    p = {**SUSPECT_DEFAULTS, **(params or {})}
    model = ra.load_model('V28')
    base = ra._evidence(db, pids, start, end)
    extra = extra_evidence(db, pids, start, end)
    out = defaultdict(list)
    for pid in pids:
        have = documented.get(pid, set())
        b, x = base.get(pid, {}), extra.get(pid, {})

        def add(cond, first, evidence, code=None):
            cat = CATEGORY[cond]
            if cat in have or any(s['icd10'] == cat for s in out[pid]): return
            out[pid].append({'icd10': cat, 'code': code or cat, 'condition': cond, 'first_evidence': first, 'evidence': evidence[:4],
                             'citation': CITATION[cond]})
        # diabetes: two abnormal glycaemic results on different dates, or diabetes-specific fills
        if not have & {'E08', 'E09', 'E10', 'E11', 'E13'}:
            a1c = sorted({(e['date'][:10], e['record_id']) for e in b.get('hba1c', []) if e['date'] and e['value'] >= 6.5})
            fpg = sorted({(d, r) for d, v, r in x.get('fpg', []) if float(v) >= 126})
            days = sorted({d for d, _ in a1c} | {d for d, _ in fpg})
            if len(days) >= p['diabetes_confirmations']:
                add('diabetes', days[p['diabetes_confirmations'] - 1], [f'HbA1c>=6.5 {d}' for d, _ in a1c] + [f'FPG>=126 {d}' for d, _ in fpg], 'E119')
            fills = sorted(d for k in DIABETES_SPECIFIC for d, _, _ in x.get('fill_' + k, []))
            if p['diabetes_min_fills'] and len(set(fills)) >= p['diabetes_min_fills']:
                add('diabetes', sorted(set(fills))[p['diabetes_min_fills'] - 1], [f'diabetes-specific fill {d}' for d in fills], 'E119')
        # CKD and HF from the risk-adjustment suspect engine (KDIGO / LVEF)
        if p['ckd'] or p['heart_failure']:
            age = (ages or {}).get(pid) or 70
            sex = (sexes or {}).get(pid) or 1
            for s in ra.suspects_for(b, sorted(c for c in have), set(), age, sex, model):
                if s['category'] == 'ckd_egfr' and p['ckd']:
                    ds = sorted(e['date'][:10] for e in s['evidence'] if e.get('date'))
                    add('ckd', ds[-1] if ds else None, [f"eGFR {e['value']} {e['date']}" for e in s['evidence']], s['illustrative_icd10'])
                elif s['category'] == 'hf_ejection_fraction' and p['heart_failure']:
                    ds = sorted(e['date'][:10] for e in s['evidence'] if e.get('date'))
                    add('heart_failure', ds[0] if ds else None, [f"LVEF {e['value']} {e['date']}" for e in s['evidence']], 'I5022')
        # obesity from BMI (measured, or weight over median height)
        bmis = [(e['date'][:10], float(e['value'])) for e in b.get('bmi', []) if e.get('date')]
        heights = [h['value'] for h in b.get('height', []) if h['value'] and 120 <= h['value'] <= 230]
        if heights:
            hm = statistics.median(heights) / 100
            bmis += [(e['date'][:10], e['value'] / (hm * hm)) for e in b.get('weight', []) if e.get('date')]
        obese = sorted({d for d, v in bmis if v >= p['obesity_bmi']})
        if len(obese) >= p['obesity_min_readings']:
            morbid = len({d for d, v in bmis if v >= 40}) >= p['obesity_min_readings']
            add('obesity', obese[p['obesity_min_readings'] - 1], [f'BMI>={p["obesity_bmi"]:g} {d}' for d in obese], 'E6601' if morbid else 'E669')
        # hypertension: stage-2 days, sustained on average
        high = hypertension_days(x.get('systolic_bp', []), x.get('diastolic_bp', []), p)
        if high:
            add('hypertension', high[p['hypertension_min_days'] - 1], [f'BP>=140/90 {d}' for d in high], 'I10')
        # hyperlipidemia
        ldl = x.get('ldl', [])
        severe = _dates(ldl, lambda v: v >= p['ldl_single'])
        high_ldl = _dates(ldl, lambda v: v >= p['ldl_repeat'])
        if severe or len(high_ldl) >= 2:
            add('hyperlipidemia', severe[0] if severe else high_ldl[1], [f'LDL {d}' for d in (severe or high_ldl)], 'E785')
        # hypothyroidism
        tsh = _dates(x.get('tsh', []), lambda v: v >= p['tsh_threshold'])
        thy = sorted({d for d, _, _ in x.get('fill_levothyroxine', [])})
        if len(tsh) >= 2 or (p['thyroid_min_fills'] and len(thy) >= p['thyroid_min_fills']):
            first = tsh[1] if len(tsh) >= 2 else thy[p['thyroid_min_fills'] - 1]
            add('hypothyroidism', first, [f'TSH>={p["tsh_threshold"]:g} {d}' for d in tsh] + [f'levothyroxine fill {d}' for d in thy], 'E039')
        # depression (off unless depression_min_screens > 0)
        if p['depression_min_screens']:
            dep = sorted({e['date'][:10] for e in b.get('phq9', []) if e.get('date') and e['value'] >= 10})
            if len(dep) >= p['depression_min_screens']:
                add('depression', dep[p['depression_min_screens'] - 1], [f'PHQ-9>=10 {d}' for d in dep], 'F321')
    return {k: v for k, v in out.items() if v}
