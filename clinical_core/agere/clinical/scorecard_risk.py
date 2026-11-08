"""Auditable credit-scorecard-style risk models: a points table a CMO can read line by line, fit and validated on the warehouse.

Outcomes (parameter `outcome`):
  admission    any inpatient stay (stay.type = inpatient; observation excluded) admitted in (index, index + horizon]
  ed_visit     any emergency visit (encounter type emergency / emergency (ADT) / emergency (claim), ADT patient class E;
               one per patient-day) in (index, index + horizon]
  death        death_date in (index, index + horizon]
  readmission  unit = inpatient stay with a known discharge; index = discharge date; label = stay.readmitted_30d
Features (binned from data known on or before the index date; bins are fixed and ordered where ordinal):
  age band, sex, coverage (commercial / medicaid / medicare / dual / other), ten chronic-condition flags (ICD-10 category of a
  condition recorded by the index date: heart failure I50, diabetes E10/E11/E13, CKD N18, COPD J43/J44, CAD I20/I21/I25,
  hypertension I10-I13, depression F32/F33, obesity E66, atrial fibrillation I48, cancer C00-C97), chronic-condition count,
  inpatient admissions in the prior 12 months, ED visit days in the prior 12 months, distinct drug classes filled in the prior
  180 days (polypharmacy), any positive social-need screen in the prior 24 months.
Method (Siddiqi 2006, Credit Risk Scorecards; Sullivan et al. 2004 Framingham points system):
  weight of evidence per bin WoE = ln(share of events / share of non-events) (positive = riskier; +0.5 smoothing) and
  information value IV = sum (share events - share non-events) x WoE on the training sample; L2-penalised logistic regression
  on bin indicators (first bin of each feature = reference, 0 points); ordinal features constrained monotone afterwards by
  pooling adjacent violators (weighted by bin size); points:
    scale = 'pdo'      points = round(beta x PDO / ln 2); base points = base_score + PDO/ln2 x (intercept - ln base_odds), so a
                       score of base_score means odds base_odds and every PDO points doubles the odds (higher = riskier)
    scale = 'integer'  points = round(beta / point_unit) (default unit ln2/2: two points double the odds), base 0 (LACE-style)
Time split (out-of-time validation): patient outcomes train at index = as_of - 2 x horizon and validate at index = as_of -
  horizon (each with a full horizon of follow-up); readmission trains on discharges 13-36 months before the as-of date and
  validates on discharges 1-12 months before it. Validation reports the scorecard's own integer score: AUROC, Brier, score
  bands (training quintiles) with observed vs predicted rates. Current scores use index = as_of (open readmission risk:
  discharges in the last 30 days).
Patients enter a sample when alive at the index and seen (encounter, fill or stay) in the 24 months before it.
Points are associations for prioritisation, never causal effects or a clinical decision.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
import json
import math
import re

import numpy as np

from . import revenue_common as rc

VERSION = 'scorecard-risk/3'
OP = 'scorecard_risk'
OUTCOMES = ('admission', 'ed_visit', 'death', 'readmission')
OUTCOME_LABEL = {'admission': 'inpatient admission', 'ed_visit': 'emergency department visit', 'death': 'death',
                 'readmission': '30-day readmission after an inpatient discharge'}
CONDITIONS = {'heart_failure': ('I50',), 'diabetes': ('E10', 'E11', 'E13'), 'ckd': ('N18',), 'copd': ('J43', 'J44'),
              'cad': ('I20', 'I21', 'I25'), 'hypertension': ('I10', 'I11', 'I12', 'I13'), 'depression': ('F32', 'F33'),
              'obesity': ('E66',), 'atrial_fibrillation': ('I48',), 'cancer': tuple(f'C{i:02d}' for i in range(98))}
# feature -> (ordered bins, ordinal)
BINS = {'age_band': (('<45', '45-64', '65-74', '75-84', '85+'), True), 'sex': (('F', 'M', 'unknown'), False),
        'coverage': (('commercial', 'medicaid', 'medicare', 'dual', 'other'), False),
        **{f'condition_{c}': (('no', 'yes'), True) for c in CONDITIONS},
        'chronic_count': (('0', '1', '2', '3-4', '5+'), True), 'prior_admissions_12m': (('0', '1', '2+'), True),
        'prior_ed_days_12m': (('0', '1', '2-3', '4+'), True), 'drug_classes_180d': (('0-4', '5-9', '10+'), True),
        'social_need_24m': (('no', 'yes'), True)}
FEATURES = tuple(BINS)
SCALES = ('pdo', 'integer')
VIEWS = ('scorecard', 'patients')
MODES = ('fit', 'evidence')
NO_FIT = re.compile(r"\b(?:do\s+not|don't|must\s+not|without)\s+(?:fit|train|retrain)\b", re.I)
EVIDENCE_ORIGIN = re.compile(r'\b(?:inputs?|information|data)\s+(?:(?:that\s+)?were\s+)?available\s+(?:at|by|as\s+of)\s+(\d{4}-\d{2}-\d{2})\b', re.I)
EVIDENCE_DAYS = re.compile(r'\bnext\s+(\d+)\s+days?\b', re.I)
ED_TYPES = ('emergency', 'emergency (ADT)', 'emergency (claim)')

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'Read-only prediction EVIDENCE mode inspects exact origin/day-window target and source clocks without fitting; '
                  'fit mode builds an AUDITABLE POINTS-BASED RISK SCORECARD for admission, ED visit, death or 30-day readmission: '
                  'binned features with WoE/IV, logistic points (PDO or small integers), out-of-time AUROC and calibration by score band, '
                  'line-by-line points for each patient and the highest-scoring patients now.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'outcome': 'admission|ed_visit|death|readmission', 'horizon_months': 12, 'scale': 'pdo|integer', 'pdo': 20,
                           'base_score': 100, 'base_odds': 0.0526, 'point_unit': 0.3466, 'l2': 1.0, 'features': [], 'top': 25,
                           'patient': '', 'view': 'scorecard|patients', 'bands': 5,
                           'mode': 'fit|evidence', 'origin_date': 'YYYY-MM-DD (evidence only)', 'horizon_days': 'exact day horizon (evidence only)'},
            'meaning': 'Fixed bins of age, sex, coverage, chronic conditions, prior admissions / ED days, polypharmacy and social need known at '
                       'the index date; WoE/IV; L2 logistic on bin indicators with monotone ordinal bins; points = beta x PDO/ln2 (or '
                       'beta / unit); trained at as_of - 2 x horizon, validated out of time at as_of - horizon (AUROC, bands); current '
                       'scores with each patient\'s points line by line.'}
NATIVE = ('value',)


def executable_evidence(parameters):
    """Execution guarantees of a validated evidence mode, without model claims."""
    p = validate(parameters)
    if p.get('mode') != 'evidence':
        return {}
    return {'model_fitting_permitted': False,
            'target_type': OUTCOME_LABEL[p['outcome']],
            'label_support': 'recorded positive labels; unrecorded labels remain unknown'}


def schema(obj, string, array):
    return obj({'outcome': string(OUTCOMES), 'mode': string(MODES, 'fit constructs a new scorecard; evidence only inspects received target/clock support and attachment metadata, with no fitting'),
                'origin_date': {'type': 'string', 'description': 'YYYY-MM-DD input cutoff, required for evidence mode'},
                'horizon_days': {'type': 'integer', 'minimum': 1, 'description': 'Exact requested day horizon in evidence mode; distinct from fitting horizon_months'},
                'horizon_months': {'type': 'integer', 'minimum': 1, 'maximum': 36, 'description': 'Outcome window (patient outcomes)'},
                'scale': string(SCALES), 'pdo': {'type': 'number', 'minimum': 1, 'maximum': 200, 'description': 'Points to double the odds'},
                'base_score': {'type': 'number', 'description': 'Score at the base odds (pdo scale)'},
                'base_odds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 100, 'description': 'Event odds at the base score'},
                'point_unit': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 5, 'description': 'Log-odds per point (integer scale)'},
                'l2': {'type': 'number', 'minimum': 0, 'maximum': 1000}, 'features': array(string(FEATURES), 'Feature subset; empty = all'),
                'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}, 'patient': {'type': 'string', 'description': 'One patient id to explain'},
                'view': string(VIEWS), 'bands': {'type': 'integer', 'minimum': 2, 'maximum': 10}})


def validate(p):
    if isinstance(p, dict) and p.get('mode') == 'evidence':
        if set(p) - {'mode', 'outcome', 'origin_date', 'horizon_days'}:
            raise ValueError('scorecard_risk evidence accepts only mode, outcome, origin_date and horizon_days; it does not fit or score')
        origin = p.get('origin_date')
        if not isinstance(origin, str) or _d(origin) is None or _d(origin).isoformat() != origin:
            raise ValueError('Evidence mode requires an explicit YYYY-MM-DD origin_date')
        days = p.get('horizon_days')
        if type(days) is not int or days < 1: raise ValueError('Evidence mode requires positive integer horizon_days')
        outcome = p.get('outcome', 'admission')
        if outcome not in OUTCOMES: raise ValueError(f'outcome: {OUTCOMES}')
        return {'mode': 'evidence', 'outcome': outcome, 'origin_date': origin, 'horizon_days': days}
    allowed = {'outcome', 'horizon_months', 'scale', 'pdo', 'base_score', 'base_odds', 'point_unit', 'l2', 'features', 'top', 'patient', 'view', 'bands', 'mode'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('scorecard_risk parameters: ' + ', '.join(sorted(allowed)))
    if p.get('mode', 'fit') != 'fit': raise ValueError(f'scorecard_risk mode: {MODES}')
    out = {'outcome': p.get('outcome') or 'admission', 'horizon_months': p.get('horizon_months', 12), 'scale': p.get('scale') or 'pdo',
           'pdo': p.get('pdo', 20), 'base_score': p.get('base_score', 100), 'base_odds': p.get('base_odds', 1 / 19),
           'point_unit': p.get('point_unit', math.log(2) / 2), 'l2': p.get('l2', 1.0), 'features': sorted(set(p.get('features') or [])),
           'top': p.get('top', 25), 'patient': str(p.get('patient') or ''), 'view': p.get('view') or 'scorecard', 'bands': p.get('bands', 5)}
    if out['outcome'] not in OUTCOMES: raise ValueError(f'outcome: {OUTCOMES}')
    if out['scale'] not in SCALES: raise ValueError(f'scale: {SCALES}')
    if out['view'] not in VIEWS: raise ValueError(f'view: {VIEWS}')
    if type(out['horizon_months']) is not int or not 1 <= out['horizon_months'] <= 36: raise ValueError('horizon_months: 1..36')
    for k, lo, hi in (('pdo', 1, 200), ('base_odds', 1e-6, 100), ('point_unit', 1e-3, 5), ('l2', 0, 1000), ('base_score', -1e6, 1e6)):
        v = out[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi: raise ValueError(f'{k}: {lo}..{hi}')
        out[k] = float(v)
    if any(f not in FEATURES for f in out['features']): raise ValueError(f'features: subset of {FEATURES}')
    if type(out['top']) is not int or not 1 <= out['top'] <= 500: raise ValueError('top: 1..500')
    if type(out['bands']) is not int or not 2 <= out['bands'] <= 10: raise ValueError('bands: 2..10')
    return out


def validate_request(question, parameters):
    """Enforce an explicit no-fit request and directly stated evidence cutoff before data loading."""
    if NO_FIT.search(question or '') and parameters.get('mode', 'fit') != 'evidence':
        raise ValueError('This request explicitly forbids fitting. Use scorecard_risk mode=evidence with the original origin_date and exact horizon_days; do not fit a new model.')
    if parameters.get('mode') == 'evidence':
        origins = set(EVIDENCE_ORIGIN.findall(question or ''))
        days = set(EVIDENCE_DAYS.findall(question or ''))
        if len(origins) == 1 and parameters.get('origin_date') != next(iter(origins)):
            raise ValueError('Evidence origin_date contradicts the explicit input-availability cutoff in the original question')
        if len(days) == 1 and parameters.get('horizon_days') != int(next(iter(days))):
            raise ValueError('Evidence horizon_days contradicts the exact next-day window in the original question')


# ---- data -------------------------------------------------------------------------------------------------------------
def _d(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _shift(d, months):
    return d - timedelta(days=round(months * 30.4375))


class Data:
    """Everything the features and labels need, loaded once (read-only)."""

    def __init__(self, db, keep):
        tabs = rc.tables(db)
        self.keep = keep
        ok = lambda pid: keep is None or pid in keep
        self.patient = {}
        pc = rc.columns(db, 'patient')
        for pid, birth, sex, lob in db.execute(f"SELECT patient_id, {'birth_date' if 'birth_date' in pc else 'NULL'}, "
                                               f"{'sex_reported' if 'sex_reported' in pc else 'NULL'}, {'line_of_business' if 'line_of_business' in pc else 'NULL'} FROM patient"):
            if ok(pid): self.patient[pid] = (_d(birth), sex or 'unknown', lob or '')
        self.dual = set()
        if 'enrollment_span' in tabs and 'dual_status' in rc.columns(db, 'enrollment_span'):
            self.dual = {r[0] for r in db.execute("SELECT DISTINCT patient_id FROM enrollment_span WHERE COALESCE(dual_status,'') <> ''")}
        self.stays = defaultdict(list)   # pid -> [(admit, discharge, readmitted, record)]
        if 'stay' in tabs:
            for pid, a, dch, ra in db.execute("SELECT patient_id, admit_date, discharge_date, readmitted_30d FROM stay WHERE type='inpatient'"):
                if ok(pid) and _d(a): self.stays[pid].append((_d(a), _d(dch), ra))
        self.ed = defaultdict(set)
        if 'encounter' in tabs:
            q = 'SELECT patient_id, event_date FROM encounter WHERE type IN (%s)' % ','.join('?' * len(ED_TYPES))
            for pid, d in db.execute(q, ED_TYPES):
                if ok(pid) and _d(d): self.ed[pid].add(_d(d))
        if 'adt_visit' in tabs:
            for pid, d in db.execute("SELECT patient_id, admit_date FROM adt_visit WHERE patient_class='E'"):
                if ok(pid) and _d(d): self.ed[pid].add(_d(d))
        self.seen = defaultdict(list)
        if 'encounter' in tabs:
            for pid, d in db.execute('SELECT patient_id, event_date FROM encounter'):
                if ok(pid) and _d(d): self.seen[pid].append(_d(d))
        self.conditions = defaultdict(list)  # pid -> [(known date, icd10 category, chronic)]
        if 'condition' in tabs:
            cc = rc.columns(db, 'condition')
            known = 'COALESCE(%s)' % ', '.join(c for c in ('available_date', 'recorded_date', 'event_date') if c in cc)
            cat = 'icd10_category' if 'icd10_category' in cc else 'substr(icd10,1,3)'
            chronic = 'chronic' if 'chronic' in cc else '0'
            for pid, d, c, ch in db.execute(f'SELECT patient_id, {known}, {cat}, {chronic} FROM condition WHERE icd10 IS NOT NULL'):
                if ok(pid) and _d(d) and c: self.conditions[pid].append((_d(d), str(c)[:3].upper(), bool(ch)))
        self.fills = defaultdict(list)
        if 'fill' in tabs:
            for pid, d, cls in db.execute('SELECT patient_id, fill_date, COALESCE(drug_class, drug_name) FROM fill'):
                if ok(pid) and _d(d):
                    self.fills[pid].append((_d(d), cls)); self.seen[pid].append(_d(d))
        for pid, rows in self.stays.items(): self.seen[pid].extend(r[0] for r in rows)
        self.social = defaultdict(list)
        if 'social_need' in tabs:
            for pid, d in db.execute('SELECT patient_id, event_date FROM social_need WHERE positive=1'):
                if ok(pid) and _d(d): self.social[pid].append(_d(d))
        self.death = {}
        if 'death' in tabs:
            for pid, d in db.execute('SELECT patient_id, death_date FROM death'):
                if ok(pid) and _d(d): self.death[pid] = _d(d)

    def eligible(self, pid, index):
        dd = self.death.get(pid)
        if dd and dd <= index: return False
        lo = _shift(index, 24)
        return any(lo < d <= index for d in self.seen.get(pid, ()))

    def features(self, pid, index, exclude_admit=None):
        birth, sex, lob = self.patient.get(pid, (None, 'unknown', ''))
        age = (index.year - birth.year - ((index.month, index.day) < (birth.month, birth.day))) if birth else None
        f = {'age_band': '<45' if age is None or age < 45 else '45-64' if age < 65 else '65-74' if age < 75 else '75-84' if age < 85 else '85+',
             'sex': sex if sex in ('F', 'M') else 'unknown',
             'coverage': 'dual' if (pid in self.dual or lob == 'dual') else lob if lob in ('commercial', 'medicaid', 'medicare') else 'other'}
        cats = {c for d, c, ch in self.conditions.get(pid, ()) if d <= index}
        chronic = {c for d, c, ch in self.conditions.get(pid, ()) if d <= index and ch}
        for name, codes in CONDITIONS.items(): f[f'condition_{name}'] = 'yes' if cats & set(codes) else 'no'
        n = len(chronic); f['chronic_count'] = '0' if n == 0 else '1' if n == 1 else '2' if n == 2 else '3-4' if n <= 4 else '5+'
        lo12 = _shift(index, 12)
        adm = sum(1 for a, _, _ in self.stays.get(pid, ()) if lo12 < a <= index and a != exclude_admit)
        f['prior_admissions_12m'] = '0' if adm == 0 else '1' if adm == 1 else '2+'
        ed = sum(1 for d in self.ed.get(pid, ()) if lo12 < d <= index)
        f['prior_ed_days_12m'] = '0' if ed == 0 else '1' if ed == 1 else '2-3' if ed <= 3 else '4+'
        lo6 = index - timedelta(days=180)
        k = len({c for d, c in self.fills.get(pid, ()) if lo6 < d <= index and c})
        f['drug_classes_180d'] = '0-4' if k < 5 else '5-9' if k < 10 else '10+'
        lo24 = _shift(index, 24)
        f['social_need_24m'] = 'yes' if any(lo24 < d <= index for d in self.social.get(pid, ())) else 'no'
        counts = {'age': age, 'chronic_categories': sorted(chronic), 'prior_admissions_12m': adm, 'prior_ed_days_12m': ed, 'drug_classes_180d': k}
        return f, counts

    def label(self, pid, outcome, index, end):
        if outcome == 'admission': return int(any(index < a <= end for a, _, _ in self.stays.get(pid, ())))
        if outcome == 'ed_visit': return int(any(index < d <= end for d in self.ed.get(pid, ())))
        dd = self.death.get(pid)
        return int(bool(dd and index < dd <= end))


def sample(data, outcome, index, end, ids):
    """Patient-level sample at one index: [(unit_id, pid, index, features, counts, label)]."""
    rows = []
    for pid in ids:
        if not data.eligible(pid, index): continue
        f, c = data.features(pid, index)
        rows.append((pid, pid, index, f, c, data.label(pid, outcome, index, end) if end else None))
    return rows


def stay_sample(data, lo, hi, ids, labelled=True):
    rows = []
    for pid in ids:
        for a, dch, ra in data.stays.get(pid, ()):
            if not dch or not (lo < dch <= hi): continue
            f, c = data.features(pid, dch, exclude_admit=a)
            rows.append((f'{pid}|{a.isoformat()}', pid, dch, f, c, (int(bool(ra)) if labelled else None)))
    return rows


# ---- model ------------------------------------------------------------------------------------------------------------
def design(rows, feats):
    cols = [(f, b) for f in feats for b in BINS[f][0][1:]]
    X = np.zeros((len(rows), 1 + len(cols))); X[:, 0] = 1
    pos = {c: i + 1 for i, c in enumerate(cols)}
    for r, row in enumerate(rows):
        for f in feats:
            j = pos.get((f, row[3][f]))
            if j: X[r, j] = 1
    return X, cols


def woe_table(rows, feats):
    y = np.array([r[5] for r in rows]); E = int(y.sum()); N = len(y) - E
    out, iv = {}, {}
    for f in feats:
        bins = BINS[f][0]; k = len(bins); total = 0.0
        for b in bins:
            m = [r[5] for r in rows if r[3][f] == b]
            e = sum(m); n = len(m) - e
            pe, pn = (e + 0.5) / (E + 0.5 * k), (n + 0.5) / (N + 0.5 * k)
            w = math.log(pe / pn)
            total += (pe - pn) * w
            out[(f, b)] = {'n': len(m), 'events': int(e), 'event_rate': round(e / len(m), 4) if m else None, 'woe': round(w, 4)}
        iv[f] = round(total, 4)
    return out, iv


def pav(values, weights, increasing=True):
    """Pool-adjacent-violators isotonic fit."""
    v = list(values) if increasing else [-x for x in values]
    blocks = [[x, w, 1] for x, w in zip(v, weights)]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0] + 1e-12:
            a, b = blocks[i], blocks[i + 1]
            w = a[1] + b[1]
            blocks[i] = [(a[0] * a[1] + b[0] * b[1]) / w if w else (a[0] + b[0]) / 2, w, a[2] + b[2]]
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    out = []
    for x, _, c in blocks: out += [x] * c
    return out if increasing else [-x for x in out]


def fit(rows, feats, l2):
    X, cols = design(rows, feats)
    y = np.array([r[5] for r in rows], dtype=float)
    beta = rc.logistic_fit(X, y, l2=l2)
    coef = {c: float(beta[i + 1]) for i, c in enumerate(cols)}
    intercept = float(beta[0])
    counts = Counter((f, r[3][f]) for r in rows for f in feats)
    monotone = []
    for f in feats:
        bins, ordinal = BINS[f]
        if not ordinal or len(bins) < 3: continue
        seq = [0.0] + [coef[(f, b)] for b in bins[1:]]
        w = [max(counts.get((f, b), 0), 1e-6) for b in bins]
        inc = np.polyfit(range(len(seq)), seq, 1, w=np.sqrt(w))[0] >= 0
        fixed = pav(seq, w, inc)
        if any(abs(a - b) > 1e-9 for a, b in zip(seq, fixed)): monotone.append(f)
        intercept += fixed[0]
        for b, v in zip(bins[1:], fixed[1:]): coef[(f, b)] = v - fixed[0]
    return intercept, coef, monotone


def points_for(intercept, coef, p):
    if p['scale'] == 'pdo':
        factor = p['pdo'] / math.log(2)
        base = round(p['base_score'] + factor * (intercept - math.log(p['base_odds'])))
        pts = {c: int(round(v * factor)) for c, v in coef.items()}
        unit, offset = 1 / factor, p['base_score'] - factor * math.log(p['base_odds'])  # logit = (score - offset) * unit
    else:
        base = 0
        pts = {c: int(round(v / p['point_unit'])) for c, v in coef.items()}
        unit, offset = p['point_unit'], -intercept / p['point_unit']
    return base, pts, unit, offset


def score(f, feats, base, pts):
    lines = [(k, f[k], pts.get((k, f[k]), 0)) for k in feats]
    return base + sum(x[2] for x in lines), lines


def predicted(s, unit, offset):
    return float(rc.sigmoid((s - offset) * unit))


def bands_of(train_scores, k):
    qs = np.quantile(train_scores, np.linspace(0, 1, k + 1)[1:-1]) if len(train_scores) else []
    return sorted(set(float(x) for x in qs))


def band_index(s, cuts):
    return sum(1 for c in cuts if s > c)


# ---- run --------------------------------------------------------------------------------------------------------------
def run(db, patient_ids, p, as_of):
    keep = set(patient_ids) if patient_ids is not None else None
    if p['patient']: keep = None if keep is None else keep | {p['patient']}
    data = Data(db, keep)
    ids = sorted(data.patient)
    A = date.fromisoformat(as_of)
    feats = p['features'] or list(FEATURES)
    h = p['horizon_months']
    if p['outcome'] == 'readmission':
        hi_v = A - timedelta(days=30); lo_v = _shift(hi_v, 12); lo_t = _shift(lo_v, 24)
        train, valid = stay_sample(data, lo_t, lo_v, ids), stay_sample(data, lo_v, hi_v, ids)
        current = stay_sample(data, hi_v, A, ids, labelled=False)
        periods = {'train': f'discharges {lo_t} to {lo_v}', 'validation': f'discharges {lo_v} to {hi_v}', 'current': f'discharges {hi_v} to {A}'}
    else:
        t1, t2 = _shift(A, 2 * h), _shift(A, h)
        train, valid = sample(data, p['outcome'], t1, t2, ids), sample(data, p['outcome'], t2, A, ids)
        current = sample(data, p['outcome'], A, None, ids)
        periods = {'train': f'index {t1}, outcome to {t2}', 'validation': f'index {t2}, outcome to {A}', 'current': f'index {A}'}
    assumptions = [f'Synthetic warehouse; outcome {OUTCOME_LABEL[p["outcome"]]}' + ('' if p['outcome'] == 'readmission' else f' within {h} months') + '.',
                   'Features use only data known on or before each index date; condition dates use the record availability date when received.',
                   'Sample: alive at the index and seen (encounter, fill or stay) in the prior 24 months; deaths before an outcome count as no event.',
                   'Out-of-time validation: the model is fit on the earlier index and scored unchanged on the later one; current scores use the same points.',
                   'Points are associations for prioritisation, not causal effects or a clinical decision.']
    report = {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'outcome_label': OUTCOME_LABEL[p['outcome']], 'periods': periods,
              'train_n': len(train), 'train_events': sum(r[5] for r in train), 'validation_n': len(valid),
              'validation_events': sum(r[5] for r in valid), 'assumptions': assumptions, 'method': METHOD}
    if len(train) < 20 or report['train_events'] < 5 or report['train_events'] == len(train):
        report.update(status='partial', scorecard=[], bands=[], rows=[], patients=[], validation={})
        assumptions.insert(0, f"Too few training events to fit a scorecard ({report['train_events']} of {len(train)} units): points are unknown "
                              'for this population and window.')
        return report
    woe, iv = woe_table(train, feats)
    intercept, coef, monotone = fit(train, feats, p['l2'])
    base, pts, unit, offset = points_for(intercept, coef, p)
    if monotone: assumptions.append('Monotone constraint pooled adjacent bins for: ' + ', '.join(monotone) + '.')
    scorecard = [{'feature': 'base', 'bin': '', 'points': base, 'beta': round(intercept, 4), 'n': len(train), 'events': report['train_events'],
                  'event_rate': round(report['train_events'] / len(train), 4), 'woe': None, 'iv': None}]
    for f in feats:
        for b in BINS[f][0]:
            w = woe[(f, b)]
            scorecard.append({'feature': f, 'bin': b, 'points': pts.get((f, b), 0), 'beta': round(coef.get((f, b), 0.0), 4), 'n': w['n'],
                              'events': w['events'], 'event_rate': w['event_rate'], 'woe': w['woe'], 'iv': iv[f], 'reference': b == BINS[f][0][0]})
    tr_s = [score(r[3], feats, base, pts)[0] for r in train]
    va_s = [score(r[3], feats, base, pts)[0] for r in valid]
    cuts = bands_of(tr_s, p['bands'])
    bands = []
    for k in range(len(cuts) + 1):
        lo = cuts[k - 1] if k else None; hi = cuts[k] if k < len(cuts) else None
        tr = [(s, r[5]) for s, r in zip(tr_s, train) if band_index(s, cuts) == k]
        va = [(s, r[5]) for s, r in zip(va_s, valid) if band_index(s, cuts) == k]
        bands.append({'band': k + 1, 'score_range': f"{'' if lo is None else f'>{lo:g}'}{' ' if lo is not None and hi is not None else ''}{'' if hi is None else f'<={hi:g}'}" or 'all',
                      'train_n': len(tr), 'train_observed': round(sum(y for _, y in tr) / len(tr), 4) if tr else None,
                      'validation_n': len(va), 'validation_observed': round(sum(y for _, y in va) / len(va), 4) if va else None,
                      'validation_predicted': round(float(np.mean([predicted(s, unit, offset) for s, _ in va])), 4) if va else None})
    vy = [r[5] for r in valid]
    vp = [predicted(s, unit, offset) for s in va_s]
    validation = {'train_auroc': rc.auroc([r[5] for r in train], tr_s), 'validation_auroc': rc.auroc(vy, va_s),
                  'validation_brier': round(float(np.mean((np.array(vp) - np.array(vy)) ** 2)), 4) if vy else None,
                  'validation_observed': round(float(np.mean(vy)), 4) if vy else None, 'validation_predicted': round(float(np.mean(vp)), 4) if vp else None}
    for k in ('train_auroc', 'validation_auroc'):
        if validation[k] is not None: validation[k] = round(validation[k], 3)
    scored = []
    for unit_id, pid, idx, f, c, _ in current:
        s, lines = score(f, feats, base, pts)
        scored.append({'unit': unit_id, 'patient_id': pid, 'index_date': idx.isoformat(), 'score': s, 'predicted_risk': round(predicted(s, unit, offset), 4),
                       'band': band_index(s, cuts) + 1, 'lines': [{'feature': k, 'bin': b, 'points': v} for k, b, v in lines if v],
                       'evidence': c})
    scored.sort(key=lambda r: (-r['score'], r['patient_id']))
    explain = [r for r in scored if r['patient_id'] == p['patient']] if p['patient'] else []
    if p['patient'] and not explain:
        assumptions.append(f"Patient {p['patient']} is not in the current scoring sample (not seen in the prior 24 months, deceased, or "
                           + ('no discharge in the last 30 days).' if p['outcome'] == 'readmission' else 'not in scope).'))
    if p['scale'] == 'pdo':
        assumptions.append(f"Points: score {p['base_score']:g} = odds {p['base_odds']:.4g}; every {p['pdo']:g} points doubles the odds; higher = riskier.")
    else:
        assumptions.append(f"Points: one point = {p['point_unit']:.3f} log-odds ({math.exp(p['point_unit']):.2f}x odds); higher = riskier.")
    report.update(status='answered', scorecard=scorecard, information_value=iv, bands=bands, validation=validation, base_points=base,
                  patients=scored[:p['top']], explained=explain, current_n=len(scored),
                  rows=scorecard)
    return report


METHOD = ('Fixed ordered bins known at the index date -> WoE / IV per bin (Siddiqi 2006) -> L2 logistic regression on bin indicators '
          '(reference bin 0 points) with monotone pooling of ordinal bins -> integer points (beta x PDO/ln2, or beta / unit) -> '
          'out-of-time validation of the integer score (AUROC, Brier, observed vs predicted by training score band) -> current scores '
          'with each patient\'s points line by line.')


def evidence(db, patient_ids, p, as_of, path):
    """Read source/attachment evidence only. No feature frame, private partition, weight or fit is opened."""
    origin = p['origin_date']; requested_end = (date.fromisoformat(origin) + timedelta(days=p['horizon_days'])).isoformat()
    if origin > as_of: raise ValueError('Predictive evidence origin is after the received snapshot')
    observed_end = min(requested_end, as_of)
    keep = None if patient_ids is None else sorted(set(patient_ids))
    bindings = {'scope': None if keep is None else json.dumps(keep), 'origin': origin, 'end': observed_end}
    scoped = 'patient_id IN (SELECT patient_id FROM patient) AND (:scope IS NULL OR patient_id IN (SELECT value FROM json_each(:scope)))'
    tabs = rc.tables(db)
    received = {r[0] for r in db.execute('SELECT patient_id FROM patient WHERE '+scoped, bindings)}
    inventory = defaultdict(Counter); input_subjects = set()
    if 'record' in tabs:
        clock_sql = """SELECT patient_id,stream,COUNT(*),
          SUM(CASE WHEN substr(event_date,1,10)<=:origin AND substr(available_date,1,10)<=:origin THEN 1 ELSE 0 END),
          SUM(CASE WHEN substr(event_date,1,10)<=:origin AND substr(available_date,1,10)>:origin THEN 1 ELSE 0 END),
          SUM(CASE WHEN substr(available_date,1,10)<=:origin AND(event_date IS NULL OR event_date='')THEN 1 ELSE 0 END),
          SUM(CASE WHEN substr(event_date,1,10)<=:origin AND(available_date IS NULL OR available_date='')THEN 1 ELSE 0 END)
          FROM record WHERE """+scoped+' GROUP BY patient_id,stream'
        for pid, stream, total, both, late, unknown_event, unknown_available in db.execute(clock_sql, bindings):
            inventory[stream].update(received_rows=total, both_clocks_by_origin=both, past_event_arrived_later=late,
                                     source_event_time_unknown=unknown_event, availability_unknown=unknown_available)
            if both: input_subjects.add(pid)
    known_dead, retrospectively_dead = set(), set()
    if 'death' in tabs:
        cols = rc.columns(db, 'death'); arrival = 'first_reported_date' if 'first_reported_date' in cols else 'NULL'
        for pid, died, reported in db.execute(f'SELECT patient_id,death_date,{arrival} FROM death WHERE '+scoped, bindings):
            if died and died <= origin:
                (known_dead if reported and reported[:10] <= origin else retrospectively_dead).add(pid)
    candidates = input_subjects - known_dead
    target_kind = {'admission': 'inpatient', 'ed_visit': 'ed_visit'}.get(p['outcome'])
    event_rows, positive, observation_rows, observation_subjects = 0, set(), 0, set()
    unknown_dates, target_status = None, 'required_target_relation_unavailable'
    if target_kind and 'utilization_event' in tabs:
        for kind, pid, n in db.execute('SELECT kind,patient_id,COUNT(*) FROM utilization_event WHERE '+scoped+
                " AND event_date>:origin AND event_date<=:end AND kind IN ('inpatient','observation','ed_visit') GROUP BY kind,patient_id", bindings):
            if kind == target_kind: event_rows += n; positive.add(pid)
            if kind == 'observation': observation_rows += n; observation_subjects.add(pid)
        unknown_dates = db.execute('SELECT COUNT(*) FROM utilization_event WHERE '+scoped+
                                  ' AND kind=:kind AND(event_date IS NULL OR event_date=\'\')', {**bindings, 'kind': target_kind}).fetchone()[0]
        target_status = 'recorded_positive_support_only'
    elif p['outcome'] == 'death' and 'death' in tabs:
        for pid, d in db.execute('SELECT patient_id,death_date FROM death WHERE '+scoped, bindings):
            if d and origin < d <= observed_end: event_rows += 1; positive.add(pid)
        unknown_dates = db.execute('SELECT COUNT(*) FROM death WHERE '+scoped+" AND(death_date IS NULL OR death_date='')", bindings).fetchone()[0]
        target_status = 'recorded_positive_support_only'
    elif p['outcome'] == 'readmission':
        target_status = 'episode_index_contract_required_not_a_fixed_population_origin'
    target_available = target_status == 'recorded_positive_support_only'
    attachments = {}
    for name in ('prediction-qualification-link.json', 'forecast-link.json'):
        pointer = Path(getattr(path, 'path', path))/name
        # Pointer existence is deliberately weaker than a verified matching model.
        # Do not traverse datasets, protected partition rows, predictions or weights.
        attachments[name] = pointer.exists()
    attached = any(attachments.values())
    qualified = None if attached else False
    summary = {'received_subjects': len(received), 'subjects_with_both_clock_inputs': len(input_subjects),
               'known_dead_by_origin_excluded': len(input_subjects & known_dead), 'origin_input_candidates': len(candidates),
               'retrospective_preorigin_deaths_not_known_by_origin': len(input_subjects & retrospectively_dead),
               'recorded_target_event_rows': event_rows if target_available else None,
               'recorded_positive_subjects_all_received': len(positive) if target_available else None,
               'recorded_positive_origin_candidates': len(positive & candidates) if target_available else None,
               'no_recorded_positive_origin_candidates_capture_unknown': len(candidates - positive) if target_available else None,
               'observation_event_rows_excluded_from_admission': observation_rows if p['outcome'] == 'admission' else None,
               'observation_only_subjects_excluded_from_admission': len(observation_subjects - positive) if p['outcome'] == 'admission' else None,
               'unknown_target_dates_all_received': unknown_dates,
               'calendar_window_mature': requested_end <= as_of, 'feature_frame_origin_state_qualified': False,
               'negative_labels_established': False, 'source_capture_established': False,
               'qualified_predictor_available': qualified, 'qualification_status': 'attached_contract_unverified' if attached else 'none_attached',
               'calibration_brier': None, 'calibration_ece': None, 'model_vs_simple_baseline': None,
               'model_fits': 0, 'model_scores_produced': 0, 'private_partition_or_weight_reads': 0}
    rows = [{'category': 'target_and_origin_support', 'source': target_kind or p['outcome'], **summary}]
    rows += [{'category': 'source_clock_inventory', 'source': stream, **dict(counts)} for stream, counts in sorted(inventory.items())]
    # Make every unknown explicit in the queryable native table.
    fields = sorted(set().union(*(row.keys() for row in rows)))
    rows = [{**{k: row.get(k) for k in fields}, 'origin_date': origin, 'horizon_days': p['horizon_days'],
             'requested_target_end': requested_end, 'observed_target_through': observed_end,
             'outcome': p['outcome'], 'parameters_json': json.dumps(p, sort_keys=True)} for row in rows]
    limitations = [
        'Positive support means a recorded endpoint; absent records are not established negative labels or absence of care.',
        'Origin input candidates have at least one received record with both source-event and availability dates through the origin day; only deaths known by that day are excluded. This is not clinical eligibility or complete capture.',
        'Source counts are bound to received patient IDs in the requested scope; unattributed records are not treated as patient inputs.',
        'Current snapshot rows filtered by clocks cannot reconstruct all superseded/quarantined historical feature states; feature-level leakage safety remains unqualified.',
        'Candidate scorecard inputs are not a qualified historical feature frame: some use event dates/current patient fields/lifetime attributes without complete availability-bound state proofs.',
        'Only active-runtime attachment presence is inspected. No protected partition, prediction row, dataset or model weight is read; present attachments still require exact target/origin/window/source and qualification verification.',
        'No fitting, calibration run, model score, simple-baseline comparison or clinical prediction is performed by evidence mode. Missing metrics remain unknown.',
        'Inpatient targets exclude observation stays; requested calendar maturity does not establish complete follow-up or label capture.'
    ]
    return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'status': 'partial', 'outcome_label': OUTCOME_LABEL[p['outcome']],
            'rows': rows, 'summary': summary, 'attachments': attachments, 'target_status': target_status,
            'missing': ['A qualified predictor and matching origin-bound feature, label, calibration and simple-baseline evidence are not established by these received observations.'],
            'clinical_prediction_authorized': False, 'automatic_promotion': False, 'model_fitting_performed': False,
            'method': 'Read-only canonical recorded-target and two-clock source inventory, with active-runtime attachment presence only; no predictive evaluation or fitting.',
            'limitations': limitations, 'assumptions': limitations}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    from .decision_context import current
    validate_request(getattr(current(), 'question', ''), p)
    with rc.connect(path) as db:
        if p.get('mode') == 'evidence': return evidence(db, patient_ids, p, str(as_of)[:10], path)
        return run(db, patient_ids, p, str(as_of)[:10])


def _lines(r):
    return ', '.join(f"{x['feature']}={x['bin']} {x['points']:+d}" for x in r['lines'])


def headline(r):
    if r['parameters'].get('mode') == 'evidence':
        s = r['summary']
        support = (f"{s['recorded_positive_origin_candidates']} recorded positive subjects" if s['recorded_positive_origin_candidates'] is not None
                   else 'recorded positive support unavailable for this target contract')
        return (f"Received evidence for {r['outcome_label']}: {support} among "
                f"{s['origin_input_candidates']} origin input candidates; qualification {s['qualification_status']}. "
                'Zero fitting or scoring; absent targets and missing calibration/comparator evidence remain unknown')
    if r.get('status') != 'answered':
        return f"Scorecard for {r['outcome_label']}: not enough training events ({r['train_events']} of {r['train_n']})"
    v = r['validation']
    top = sorted((x for x in r['scorecard'] if x['feature'] != 'base'), key=lambda x: -x['points'])[:3]
    s = (f"Points scorecard for {r['outcome_label']}: base {r['base_points']} points; largest additions "
         + ', '.join(f"{x['feature']}={x['bin']} {x['points']:+d}" for x in top)
         + f"; trained on {r['train_n']:,} ({r['train_events']:,} events)")
    if v.get('validation_auroc') is not None:
        s += (f", out-of-time AUROC {v['validation_auroc']} on {r['validation_n']:,} (observed {v['validation_observed']:.1%} vs "
              f"predicted {v['validation_predicted']:.1%})")
    if r.get('explained'):
        e = r['explained'][0]
        s += f"; {e['patient_id']} scores {e['score']} (predicted {e['predicted_risk']:.1%}): base {r['base_points']}, {_lines(e) or 'no added points'}"
    elif r.get('patients'):
        e = r['patients'][0]
        s += f"; highest now {e['patient_id']} at {e['score']} points ({e['predicted_risk']:.1%})"
    return s


def answer(report, plan, question):
    p = report['parameters']
    if p.get('mode') == 'evidence':
        out = rc.answer(report, plan, value=headline(report)+'.', table=report['rows'], patient_ids=[], membership_role='evaluated',
                        definitions=[{'term': 'positive support', 'basis': 'Recorded canonical endpoint in the declared future day window; not complete clinical outcome capture'}])
        out['overall'] = {'prediction_evidence': report['summary'], 'parameters': p, 'attachments': report['attachments'],
                          'target_status': report['target_status'], 'model_fitting_performed': False}
        out['missing'] = list(report['missing'])
        return out
    if report.get('explained'):
        e = report['explained'][0]
        table = [{'patient_id': e['patient_id'], 'feature': 'base', 'bin': '', 'points': report['base_points']}] + \
                [{'patient_id': e['patient_id'], **x} for x in e['lines']] + \
                [{'patient_id': e['patient_id'], 'feature': 'total', 'bin': '', 'points': e['score'], 'predicted_risk': e['predicted_risk']}]
        ids, role = [e['patient_id']], 'evaluated'
    elif p['view'] == 'patients':
        table = [{k: x[k] for k in ('patient_id', 'unit', 'index_date', 'score', 'predicted_risk', 'band')} | {'points': _lines(x)} for x in report.get('patients') or []]
        ids, role = [x['patient_id'] for x in report.get('patients') or []], 'selected'
    else:
        table = report.get('scorecard') or []
        ids, role = [], 'evaluated'
    out = rc.answer(report, plan, value=headline(report) + '.', table=table, patient_ids=list(dict.fromkeys(ids)), membership_role=role,
                    number=(report.get('validation') or {}).get('validation_auroc'),
                    definitions=[{'term': 'weight of evidence', 'basis': 'ln(share of events / share of non-events) per bin (Siddiqi 2006)'},
                                 {'term': 'points to double the odds (PDO)', 'basis': 'points = beta x PDO / ln 2'},
                                 {'term': 'AUROC', 'basis': 'probability a random event unit outscores a random non-event unit (out-of-time sample)'}])
    out['overall'] = {'scorecard_risk': {k: report.get(k) for k in ('outcome_label', 'periods', 'train_n', 'train_events', 'validation_n',
                                                                     'validation_events', 'validation', 'bands', 'information_value', 'base_points',
                                                                     'scorecard', 'patients', 'explained', 'current_n')}}
    return out


# ---- free local-compiler route ----------------------------------------------------------------------------------------
SCORECARD_RX = (r'\bscore ?cards?\b|\bpoints?[- ]based\b|\bpoint scores?\b|\bpoints? system\b|\brisk points\b|\bpoints? (?:model|table)\b|'
                r'\bauditable (?:risk )?(?:model|score)s?\b|\b(?:transparent|explainable|interpretable) (?:points?|risk) (?:model|score)s?\b|'
                r'\bcredit[- ]scor\w+ style\b|\b(?:lace|hospital)[- ]style\b|\bline by line\b.{0,40}\b(?:risk|score|points)\b|'
                r'\bweight of evidence\b|\binformation value\b')
OUTCOME_RX = [('readmission', r'\breadmi\w*'), ('ed_visit', r'\bed visits?\b|\bemergency\b|\ber visits?\b|\bed use\b|\bed utili\w*'),
              ('death', r'\bdeath\b|\bmortality\b|\bdying\b|\bdie\b'), ('admission', r'\badmission\w*|\bhospitali\w*|\binpatient\b|\badmit\w*')]


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if NO_FIT.search(q):
        origins, days = set(EVIDENCE_ORIGIN.findall(q)), set(EVIDENCE_DAYS.findall(q))
        outcomes = {k for k, rx in OUTCOME_RX if re.search(rx, q)}
        if re.search(r'\b(?:evidence|readiness|qualification)\b', q) and len(origins) == len(days) == len(outcomes) == 1:
            params = {'mode': 'evidence', 'outcome': next(iter(outcomes)), 'origin_date': next(iter(origins)), 'horizon_days': int(next(iter(days)))}
            try: validate(params)
            except ValueError: return None
            return plan(OP, params, 'Inspect exact recorded-target, origin-clock and qualification dependencies without fitting', question,
                        ['explicit no-fit request', 'input availability origin', 'exact day horizon', 'evidence-only target'])
        return None  # Do not silently fall through to constructing a scorecard.
    if not re.search(SCORECARD_RX, q): return None
    if re.search(r'\b(?:star|hedis|quality|balanced|mips|vendor|supplier|cahps|bsc)\b.{0,20}\bscore ?cards?\b|\bscore ?cards?\b.{0,20}\b(?:stars?|hedis|quality measures?|mips|kpis?)\b', q):
        return None  # performance / quality scorecards are other engines
    if rc.knowledge_only(q) and not re.search(r'\b(?:our|build|fit|make|each)\b|' + '|'.join(rx for _, rx in OUTCOME_RX), q): return None  # 'what is a scorecard' stays knowledge
    params = {}
    oc = next((k for k, rx in OUTCOME_RX if re.search(rx, q)), None)
    if oc: params['outcome'] = oc
    m = re.search(r'\b(\d{1,2})[- ]months?\b', q)
    if m and oc != 'readmission' and 1 <= int(m.group(1)) <= 36: params['horizon_months'] = int(m.group(1))
    elif re.search(r'\b(?:6|six)[- ]month\b', q): params['horizon_months'] = 6
    pid = re.search(r'\b((?:p|pt|whp|mrn)-[0-9a-z]{3,})\b', q)
    if pid: params['patient'] = pid.group(1).upper()
    elif re.search(r'\b(?:which|who|list|top|highest|rank)\b.{0,40}\b(?:patients|members|people)\b|\bpatients\b.{0,30}\b(?:score|scoring)\b.{0,20}\bhighest\b', q):
        params['view'] = 'patients'
    if re.search(r'\b(?:small )?integer points?\b|\blace[- ]style\b|\bhospital[- ]style\b|\bsimple points?\b', q): params['scale'] = 'integer'
    pdo = re.search(r'\b(\d{1,3}) points? to double\b|\bpdo (?:of )?(\d{1,3})\b', q)
    if pdo: params['pdo'] = float(pdo.group(1) or pdo.group(2))
    return plan(OP, params, 'Auditable points-based risk scorecard with out-of-time validation and line-by-line patient points', question,
                [f"outcome {params.get('outcome', 'admission')}"])


TEMPLATES = ['Build an auditable points-based risk model for 12-month hospital admission',
             'Give me a credit-scorecard style risk score for ED visits that our CMO can audit line by line',
             'Which patients have the highest admission risk on the points scorecard?',
             'Show the scorecard points behind P-1001\'s readmission risk',
             'Create a simple integer points system, LACE-style, for 30-day readmission',
             'What is the weight of evidence and information value of each factor in a mortality scorecard?',
             'Point score for 6-month emergency visits with 20 points to double the odds',
             'Transparent points model for hospitalization risk, validated out of time']
