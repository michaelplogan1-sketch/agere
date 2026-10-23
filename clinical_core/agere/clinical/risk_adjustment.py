"""CMS-HCC risk adjustment: risk scores, coding gaps, recapture and suspects ('risk_adjustment' operation).

The Medicare Advantage / ACO REACH economics engine. Everything numeric comes from public CMS tables stored under
config/agere/risk_adjustment/ (manifest.json gives the cms.gov URL, payment year and sha256 of every table):

* 2024 CMS-HCC model (V28): ICD-10 -> CC crosswalk with MCE/age/sex edits, hierarchies, diagnostic categories,
  interactions and relative factors from the PY2026 CMS Python model software (CMS_HCC_v28_2026_T_package_v3); the
  CC223 heart-failure patch and payment-HCC count variables follow the same software (and SAS V2825T1M).
* 2020 CMS-HCC model (V24): crosswalk F2425P1M, relative factors C2419P1M, hierarchies V24H86H1 and the diagnostic
  categories / interactions / mandatory edits of V2425P1M and V24I0ED3 (PY2025 SAS software V2425.86.P1).
* Payment-year blend, normalization factors and the MA coding pattern difference adjustment from the CY2025 and CY2026
  Rate Announcements (payment_parameters.json); a program rulebook entry with a risk_adjustment block overrides them.

Diagnosis eligibility follows the CMS encounter-data filtering logic: inpatient facility claims (TOB 11x/41x) always
count; outpatient facility (TOB 12x/13x/43x/71x/73x/76x/77x/85x) and professional claims count only when a line carries a
code on the Medicare Risk Adjustment Eligible CPT/HCPCS list; independent-laboratory (POS 81), DME (POS 12 suppliers are
not separately identified) and other bill types are excluded; denied claims are excluded. Coded diagnoses on EHR visits
count when the visit is face to face or audio-video telehealth with an acceptable provider type (audio-only telephone
visits, nurse-only, pharmacist, radiology, therapy, dental and diagnostic-test visits are excluded). Problem-list entries
are never eligible: they feed the 'documented, not coded' suspects.

Suspects are documentation review prompts with their evidence rows, never diagnoses. Dollar figures are RAF deltas times a
base rate that is either supplied or labelled illustrative.
"""
from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path
import csv, json, math, re, sqlite3, statistics

VERSION = 'risk-adjustment/1'
ROOT = Path(__file__).resolve().parents[3]
TABLES = ROOT / 'config' / 'agere' / 'risk_adjustment'
PROGRAMS = ROOT / 'config' / 'agere'
MODELS = ('V24', 'V28')
FOCUS = ('all', 'raf', 'recapture', 'suspects', 'population', 'fairness')  # fairness: predictive ratios by group (ra_fairness.py)
GROUP_BY = ('none', 'practice', 'pcp', 'payer', 'coverage_type')
SEGMENTS = {'community_non_dual': ('CNA', 'CND'), 'community_full_dual': ('CFA', 'CFD'),
            'community_partial_dual': ('CPA', 'CPD'), 'institutional': ('INS', 'INS')}
SOURCES = ('claims_and_encounters', 'claims')
COVERAGE = ('medicare', 'all')
FIELDS = {'payment_year', 'year', 'coverage', 'segment', 'focus', 'group_by', 'base_rate_pmpm', 'lookback_days', 'sources', 'top', 'patient'}
ILLUSTRATIVE_BASE_RATE = 1000.0
V28_COLUMN = {'CNA': 'COMMUNITY_NA', 'CND': 'COMMUNITY_ND', 'CFA': 'COMMUNITY_FBA', 'CFD': 'COMMUNITY_FBD', 'CPA': 'COMMUNITY_PBA',
              'CPD': 'COMMUNITY_PBD', 'INS': 'INSTITUTIONAL'}
AGE_BANDS = ((0, 34), (35, 44), (45, 54), (55, 59), (60, 64), (65, 69), (70, 74), (75, 79), (80, 84), (85, 89), (90, 94), (95, None))

# V24 structure transcribed from SAS V2425P1M.TXT (step 3.6) - kept as SAS source in config/agere/risk_adjustment/v24_sas_*.
V24_CATEGORIES = {'CANCER': (8, 9, 10, 11, 12), 'DIABETES': (17, 18, 19), 'CARD_RESP_FAIL': (82, 83, 84), 'CHF': (85,),
                  'gCopdCF': (110, 111, 112), 'RENAL_V24': (134, 135, 136, 137, 138), 'SEPSIS': (2,),
                  'gSubstanceUseDisorder_V24': (54, 55, 56), 'gPsychiatric_V24': (57, 58, 59, 60), 'PRESSURE_ULCER': (157, 158, 159)}
V24_INTERACTIONS = {'HCC47_gCancer': ('HCC47', 'CANCER'), 'DIABETES_CHF': ('DIABETES', 'CHF'), 'CHF_gCopdCF': ('CHF', 'gCopdCF'),
                    'HCC85_gRenal_V24': ('HCC85', 'RENAL_V24'), 'gCopdCF_CARD_RESP_FAIL': ('gCopdCF', 'CARD_RESP_FAIL'),
                    'HCC85_HCC96': ('HCC85', 'HCC96'), 'gSubstanceUseDisorder_gPsych': ('gSubstanceUseDisorder_V24', 'gPsychiatric_V24'),
                    'SEPSIS_PRESSURE_ULCER': ('SEPSIS', 'PRESSURE_ULCER'), 'SEPSIS_ARTIF_OPENINGS': ('SEPSIS', 'HCC188'),
                    'ART_OPENINGS_PRESS_ULCER': ('HCC188', 'PRESSURE_ULCER'), 'gCopdCF_ASP_SPEC_B_PNEUM': ('gCopdCF', 'HCC114'),
                    'ASP_SPEC_B_PNEUM_PRES_ULC': ('HCC114', 'PRESSURE_ULCER'), 'SEPSIS_ASP_SPEC_BACT_PNEUM': ('SEPSIS', 'HCC114'),
                    'SCHIZOPHRENIA_gCopdCF': ('HCC57', 'gCopdCF'), 'SCHIZOPHRENIA_CHF': ('HCC57', 'CHF'),
                    'SCHIZOPHRENIA_SEIZURES': ('HCC57', 'HCC79'), 'DISABLED_HCC85': ('DISABL', 'HCC85'),
                    'DISABLED_PRESSURE_ULCER': ('DISABL', 'PRESSURE_ULCER'), 'DISABLED_HCC161': ('DISABL', 'HCC161'),
                    'DISABLED_HCC39': ('DISABL', 'HCC39'), 'DISABLED_HCC77': ('DISABL', 'HCC77'), 'DISABLED_HCC6': ('DISABL', 'HCC6')}
V24_COPD_UNDER18 = {'J410', 'J411', 'J418', 'J42', 'J430', 'J431', 'J432', 'J438', 'J439', 'J440', 'J441', 'J449', 'J982', 'J983', 'J4481', 'J4489'}

# Diagnosis eligibility (CMS encounter-data filtering; see module docstring)
INPATIENT_TOB = ('011', '041')
OUTPATIENT_TOB = ('012', '013', '043', '071', '073', '076', '077', '085')
EXCLUDED_POS = {'81': 'independent laboratory'}
EXCLUDED_SPECIALTIES = {'registered nursing', 'nursing', 'clinical pharmacy', 'diagnostic radiology', 'diagnostic technician',
                        'physical therapy', 'audiology', 'dentistry', 'social work'}
EXCLUDED_VISIT_TYPES = {'telephone': 'audio-only telephone', 'diagnostic_testing': 'diagnostic test only',
                        'diagnostic_test': 'diagnostic test only', 'nurse_immunization_visit': 'nurse-only visit'}
TELEHEALTH_VIDEO = {'virtual'}

SUSPECTS = {
    'ckd_egfr': {'label': 'eGFR < 60 twice >= 90 days apart, no CKD code', 'codes': ('N18',),
                 'reading': 'Persistently reduced eGFR is the KDIGO definition of CKD; review whether CKD and its stage are documented.'},
    'diabetes_a1c': {'label': 'HbA1c >= 6.5% without a diabetes code', 'codes': ('E08', 'E09', 'E10', 'E11', 'E13'), 'icd10': 'E119',
                     'reading': 'An HbA1c >= 6.5% meets the ADA diagnostic threshold when confirmed; review for undocumented diabetes.'},
    'diabetes_medication': {'label': 'Insulin / GLP-1 / SGLT2 fills without a diabetes code', 'codes': ('E08', 'E09', 'E10', 'E11', 'E13'),
                            'icd10': 'E119',
                            'reading': 'GLP-1 and SGLT2 drugs also treat obesity, heart failure and CKD; review the indication before any coding.'},
    'hf_ejection_fraction': {'label': 'Ejection fraction <= 40% without a heart-failure code', 'codes': ('I50', 'I110', 'I130', 'I132'),
                             'icd10': 'I5022', 'reading': 'Reduced EF in a note; review whether heart failure (HFrEF) is a documented diagnosis.'},
    'morbid_obesity': {'label': 'BMI >= 40 without a morbid-obesity code', 'codes': ('E6601', 'E662', 'Z6841', 'Z6842', 'Z6843', 'Z6844', 'Z6845'),
                       'icd10': 'Z6841', 'reading': 'Measured BMI >= 40; review whether severe obesity is documented with its BMI.'},
    'depression_screen': {'label': 'Positive depression screen without a depression code', 'codes': ('F32', 'F33'), 'icd10': 'F321',
                          'reading': 'A positive PHQ is a screen, not a diagnosis; review whether major depression was assessed and documented.'},
    'documented_not_coded': {'label': 'Problem-list condition in a payment HCC never coded on an eligible encounter', 'codes': (),
                             'reading': 'The condition is on the chart but on no eligible claim or visit this year; review and code if still present.'},
}
LIMITATIONS = [
    'Synthetic data. Diagnoses are filtered with the CMS encounter-data logic as far as the warehouse supports it; the provider '
    'specialty filter uses the visit attending clinician (claims carry no specialty), and unknown specialties are accepted.',
    'Demographic assumptions: community segment chosen by the segment parameter (default non-dual), aged (65+) vs disabled by age, '
    'no originally-disabled status, no ESRD, hospice or new-enrollee logic; age as of February 1 of the synthetic payment year.',
    'Published CMS parameter sets (PY2025, PY2026) are applied to the synthetic collection year, which runs ahead of published years.',
    'Suspects are documentation review prompts with cited evidence, never diagnoses; dollars are RAF deltas times a base rate.',
    'Recapture compares calendar years; a gap closes when the same HCC or a higher one in its hierarchy is coded.',
]


# ---- CMS tables ---------------------------------------------------------------------------------------------------------
def _csv(name):
    with open(TABLES / name, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def _hcc_num(s):
    return int(float(str(s).strip().replace('HCC', '').replace('CC', '')))


@lru_cache(maxsize=None)
def manifest():
    return json.loads((TABLES / 'manifest.json').read_text())


@lru_cache(maxsize=None)
def load_model(name):
    """{'name', 'mapping': {icd: [(cc, mce_age, age_edit, sex_edit)]}, 'hierarchy': [(hcc, excluded tuple)], 'coef': {seg: {var: v}},
    'categories': {cat: hccs}, 'interactions': {var: (a, b)}, 'labels': {hcc: label}, 'payment': set(hcc)}"""
    if name == 'V28':
        mapping = defaultdict(list)
        for r in _csv('v28_icd10_cc_py2026.csv'):
            sex = r['SEX_EDIT_CONDITION'].strip()
            mapping[r['ICD10'].strip()].append((int(float(r['CC'])), r['MCE_AGE_CONDITION'].strip() or None,
                                                r['AGE_EDIT_CONDITION'].strip() or None, int(float(sex)) if sex else None))
        hierarchy = [(_hcc_num(r['HCC']), tuple(_hcc_num(v) for k, v in r.items() if k != 'HCC' and v and v.strip()))
                     for r in _csv('v28_hierarchies.csv')]
        coef = defaultdict(dict)
        for r in _csv('v28_relative_factors.csv'):
            var = (r.get('Variable') or '').strip()
            if not var: continue
            for seg, col in V28_COLUMN.items():
                v = (r.get(col) or '').strip()
                if v: coef[seg][var] = float(v)
        categories = {r['diag_category'].strip(): tuple(_hcc_num(v) for k, v in r.items() if k != 'diag_category' and v and v.strip())
                      for r in _csv('v28_diagnosis_categories.csv')}
        interactions = {r['interaction'].strip(): (r['var_1'].strip(), r['var_2'].strip()) for r in _csv('v28_interactions.csv')}
        payment = {h for h, _ in hierarchy}
        labels = {_hcc_num(r['HCC']): r['LABEL'] for r in _csv('v28_labels.csv')}
    elif name == 'V24':
        mapping = defaultdict(list)
        for r in _csv('v24_icd10_cc_py2025.csv'):
            mapping[r['ICD10'].strip()].append((int(r['CC']), None, None, None))
        hierarchy = [(_hcc_num(r['HCC']), tuple(_hcc_num(x) for x in r['EXCLUDES'].split())) for r in _csv('v24_hierarchies.csv')]
        coef = defaultdict(dict)
        for r in _csv('v24_relative_factors.csv'):
            seg, var = r['_NAME_'].split('_', 1)
            coef[seg][var] = float(r['COL1'])
        categories, interactions = V24_CATEGORIES, V24_INTERACTIONS
        payment = {_hcc_num(v) for v in coef['CNA'] if re.fullmatch(r'HCC\d+', v)}
        labels = {_hcc_num(r['HCC']): r['LABEL'] for r in _csv('v24_labels.csv')}
    else:
        raise ValueError(f'unknown model {name}')
    return {'name': name, 'mapping': dict(mapping), 'hierarchy': hierarchy, 'coef': {k: dict(v) for k, v in coef.items()},
            'categories': categories, 'interactions': interactions, 'labels': labels, 'payment': payment}


@lru_cache(maxsize=None)
def eligible_cpt(year):
    loaded = sorted(int(p.stem[-4:]) for p in TABLES.glob('eligible_cpt_hcpcs_cy*.txt'))
    use = min(loaded, key=lambda y: (abs(y - year), -y))
    return use, frozenset(x.strip() for x in (TABLES / f'eligible_cpt_hcpcs_cy{use}.txt').read_text().split() if x.strip())


def _age_rule(expr, age):
    """CMS Python software age conditions: 'age < 50', 'age >= 18', '9 <= age <= 64', 'age 15+'."""
    e = re.sub(r'age\s*(\d+)\s*\+', r'age >= \1', expr.strip().lower())
    parts = re.split(r'\s*(<=|>=|<|>|=)\s*', e)
    vals = [age if p == 'age' else (int(p) if re.fullmatch(r'\d+', p) else p) for p in parts]
    ops = {'<': lambda a, b: a < b, '<=': lambda a, b: a <= b, '>': lambda a, b: a > b, '>=': lambda a, b: a >= b, '=': lambda a, b: a == b}
    ok = True
    for i in range(1, len(vals) - 1, 2):
        ok = ok and ops[vals[i]](vals[i - 1], vals[i + 1])
    return ok


def icd_key(code):
    return re.sub(r'[^A-Z0-9]', '', str(code or '').upper())


def ccs_for(model, codes, age, sex):
    """{cc: [icd codes]} after the model's age/sex edits. sex 1 = male, 2 = female (CMS coding)."""
    out = defaultdict(list)
    for raw in codes:
        icd = icd_key(raw)
        if not icd: continue
        if model['name'] == 'V24':  # mandatory edits of V24I0ED3 (MCE list edits are not published as a table for V24)
            if sex == 2 and icd in ('D66', 'D67'):
                out[48].append(icd); continue
            if age < 18 and icd in V24_COPD_UNDER18:
                out[112].append(icd); continue
            if icd == 'F3481' and (age < 6 or age > 18):
                continue
        for cc, mce, edit, sex_edit in model['mapping'].get(icd, ()):
            if mce and not _age_rule(mce, age): continue
            if edit and not _age_rule(edit, age): continue
            if sex_edit is not None and sex_edit != sex: continue
            out[cc].append(icd)
    if model['name'] == 'V28' and 223 in out and not any(c in out for c in (221, 222, 224, 225, 226)):
        out.pop(223)  # heart-failure interaction patch (V2825T1M / CMS Python transform)
    return dict(out)


def apply_hierarchy(model, ccs):
    """Return (kept HCCs, {dropped hcc: dominating hcc}); exclusions are evaluated against the CCs present (CMS Python)."""
    present = set(ccs) & model['payment']
    dropped = {}
    for hcc, excl in model['hierarchy']:
        if hcc in present:
            for e in excl:
                if e in present and e not in dropped: dropped[e] = hcc
    return sorted(present - set(dropped)), dropped


def age_on(birth, when):
    from .age import age_at
    return age_at(birth, when)


def scoring_age(birth, when):
    """Age on the CMS reference date for scoring. A valid birth date after that date (a newborn in a later delivery) is age 0,
    the youngest band; a missing or invalid birth date stays unknown (None) and is never replaced by feed age."""
    age = age_on(birth, when)
    if age is not None: return age
    try:
        day = lambda v: v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
        return 0 if day(birth) > day(when) else None
    except (TypeError, ValueError):
        return None


def _band(age):
    # a person born after the age reference date (a newborn in a later delivery: age < 0) scores in the youngest band
    age = max(int(age or 0), AGE_BANDS[0][0])
    for lo, hi in AGE_BANDS:
        if age >= lo and (hi is None or age <= hi): return f'{lo}_{hi if hi is not None else "GT"}'


def segment_code(segment, age):
    aged, disabled = SEGMENTS[segment]
    return aged if age >= 65 else disabled


def score(model_name, age, sex, codes, segment='community_non_dual', orec=None):
    """Raw (unnormalised) CMS-HCC score with every term. orec: 0 old age, 1 disability (default: 1 under 65 else 0)."""
    m = load_model(model_name)
    seg = segment_code(segment, age)
    coef = m['coef'][seg]
    orec = (1 if age < 65 else 0) if orec is None else orec
    disabl = int(age < 65 and orec != 0)
    origds = int(orec == 1 and not disabl)
    ccs = ccs_for(m, codes, age, sex)
    hccs, dropped = apply_hierarchy(m, ccs)
    flags = {f'HCC{h}': 1 for h in hccs}
    cats = {c: int(any(f'HCC{h}' in flags for h in hs)) for c, hs in m['categories'].items()}
    env = {**flags, **cats, 'DISABL': disabl}
    inter = {k: int(env.get(a, 0) and env.get(b, 0)) for k, (a, b) in m['interactions'].items()}
    n = len(hccs)
    terms = [(('F' if sex == 2 else 'M') + _band(age), 'demographic')]
    if origds and seg not in ('INS',):
        terms.append(('OriginallyDisabled_Female' if sex == 2 else 'OriginallyDisabled_Male', 'demographic'))
    if seg == 'INS' and origds: terms.append(('ORIGDIS', 'demographic'))
    terms += [(f'HCC{h}', 'hcc') for h in hccs] + [(k, 'interaction') for k, v in inter.items() if v]
    if n: terms.append(('D10P' if n >= 10 else f'D{n}', 'count'))
    parts = [{'variable': v, 'kind': k, 'coefficient': coef[v]} for v, k in terms if v in coef and coef[v] != 0]
    raw = round(sum(p['coefficient'] for p in parts), 3)
    return {'model': model_name, 'segment': seg, 'age': age, 'sex': sex, 'raw': raw, 'terms': parts,
            'hccs': [{'hcc': h, 'label': m['labels'].get(h, ''), 'codes': sorted(set(ccs[h]))} for h in hccs],
            'dropped_by_hierarchy': {h: by for h, by in sorted(dropped.items())},
            'interactions': sorted(k for k, v in inter.items() if v and k in coef), 'payment_hcc_count': n,
            'demographic_raw': round(sum(p['coefficient'] for p in parts if p['kind'] == 'demographic'), 3)}


# ---- payment year -----------------------------------------------------------------------------------------------------------
def _rulebook_override(payment_year):
    """A program rulebook entry {'risk_adjustment': {'payment_year', 'blend', 'normalization', 'ma_coding_pattern_adjustment', 'source'}}
    in config/agere/programs.json or config/agere/programs/*.json overrides the CMS defaults for that payment year."""
    files = [PROGRAMS / 'programs.json'] + sorted((PROGRAMS / 'programs').glob('*.json')) if (PROGRAMS / 'programs').is_dir() else [PROGRAMS / 'programs.json']
    for f in files:
        try: data = json.loads(f.read_text())
        except (OSError, ValueError): continue
        for e in (data if isinstance(data, list) else data.get('programs', []) if isinstance(data, dict) else []):
            ra = e.get('risk_adjustment') if isinstance(e, dict) else None
            if isinstance(ra, dict) and int(ra.get('payment_year', -1)) == payment_year and isinstance(ra.get('blend'), dict):
                return {**ra, 'origin': f'program rulebook {f.name}:{e.get("id")}'}
    return None


def payment_parameters(payment_year):
    cms = json.loads((TABLES / 'payment_parameters.json').read_text())['payment_years']
    if str(payment_year) not in cms: raise ValueError(f'risk_adjustment payment_year must be one of {sorted(cms)}')
    base = dict(cms[str(payment_year)], origin=f'CMS Rate Announcement PY{payment_year}')
    over = _rulebook_override(payment_year)
    if over:
        base.update({k: over[k] for k in ('blend', 'normalization', 'ma_coding_pattern_adjustment', 'source', 'origin') if k in over})
    return base


def payment_raf(raws, pay):
    """CMS: each model score / its normalization factor (rounded to 3), blended, times (1 - MA coding pattern adjustment)."""
    blended = sum(w * round(raws[m] / pay['normalization'][m], 3) for m, w in pay['blend'].items())
    return round(blended * (1 - pay['ma_coding_pattern_adjustment']), 3)


# ---- parameters -----------------------------------------------------------------------------------------------------------
def validate(p):
    if not isinstance(p, dict) or set(p) - FIELDS:
        raise ValueError(f'risk_adjustment parameters must be a subset of {sorted(FIELDS)}')
    years = sorted(json.loads((TABLES / 'payment_parameters.json').read_text())['payment_years'])
    out = {}
    py = p.get('payment_year', int(years[-1]))
    if isinstance(py, bool) or str(py) not in years: raise ValueError(f'risk_adjustment payment_year must be one of {years}')
    out['payment_year'] = int(py)
    y = p.get('year')
    if y is not None and (isinstance(y, bool) or not isinstance(y, int) or not 2000 <= y <= 2100):
        raise ValueError('risk_adjustment year must be a calendar year (data collection year)')
    out['year'] = y
    for k, allowed, default in (('coverage', COVERAGE, 'medicare'), ('segment', tuple(SEGMENTS), 'community_non_dual'),
                                ('focus', FOCUS, 'all'), ('group_by', GROUP_BY, 'none'), ('sources', SOURCES, 'claims_and_encounters')):
        v = p.get(k, default) or default
        if v not in allowed: raise ValueError(f'risk_adjustment {k} must be one of {allowed}')
        out[k] = v
    b = p.get('base_rate_pmpm')
    if b is not None and (isinstance(b, bool) or not isinstance(b, (int, float)) or not 0 < b <= 100000):
        raise ValueError('risk_adjustment base_rate_pmpm must be a positive monthly dollar amount')
    out['base_rate_pmpm'] = float(b) if b is not None else None
    lb = p.get('lookback_days', 730)
    if isinstance(lb, bool) or not isinstance(lb, int) or not 90 <= lb <= 3650: raise ValueError('risk_adjustment lookback_days must be 90-3650')
    out['lookback_days'] = lb
    t = p.get('top', 25)
    if isinstance(t, bool) or not isinstance(t, int) or not 1 <= t <= 500: raise ValueError('risk_adjustment top must be 1-500')
    out['top'] = t
    pt = p.get('patient', '') or ''
    if not isinstance(pt, str) or (pt and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,80}', pt)):
        raise ValueError('risk_adjustment patient must be an identifier or empty')
    out['patient'] = pt
    return out


# ---- warehouse --------------------------------------------------------------------------------------------------------------
def connect(path):
    """Read-only connection to an existing semantic layer; never builds or migrates one."""
    p = Path(path)
    target = p / 'semantic.sqlite3' if p.is_dir() else p
    if not target.exists(): raise FileNotFoundError(f'no semantic layer at {target}')
    return sqlite3.connect(f'file:{target}?mode=ro', uri=True)


def _columns(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def capability(db):
    """What the build supports. The live v1.23 build has no ICD-10 on encounters or claims."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    meta = dict(db.execute('SELECT key, value FROM meta').fetchall()) if 'meta' in tables else {}
    cond_cols = _columns(db, 'condition') if 'condition' in tables else set()
    gaps = []
    has_claim_dx = 'claim' in tables and {'principal_diagnosis', 'diagnosis_codes'} <= _columns(db, 'claim')
    has_visit_dx = 'encounter_diagnosis' in tables
    has_lines = 'claim_line' in tables
    has_birth = 'birth_date' in _columns(db, 'patient')
    if not has_claim_dx: gaps.append('claims carry no ICD-10 diagnosis codes (claim.principal_diagnosis / diagnosis_codes absent)')
    if not has_visit_dx: gaps.append('no encounter_diagnosis table: visits carry no coded ICD-10 diagnoses')
    if not has_lines: gaps.append('no claim_line CPT/HCPCS: the CMS eligible-procedure filter cannot run')
    if not has_birth: gaps.append('no patient birth_date: CMS age bands use reported age only')
    icd = None
    if 'icd10' in cond_cols:
        icd = {src: {'rows': n, 'with_icd10': k or 0} for src, n, k in db.execute(
            "SELECT source_table, count(DISTINCT record_id), count(DISTINCT CASE WHEN icd10 IS NOT NULL AND icd10 != '' THEN record_id END) "
            "FROM condition GROUP BY 1")}
        for src, c in icd.items():
            if c['rows'] and c['with_icd10'] < c['rows']: gaps.append(f"{c['rows'] - c['with_icd10']} of {c['rows']} {src} rows lack ICD-10")
    return {'semantic_version': meta.get('version'), 'as_of': meta.get('as_of'), 'claim_diagnoses': has_claim_dx,
            'visit_diagnoses': has_visit_dx, 'claim_lines': has_lines, 'birth_date': has_birth, 'condition_icd10': icd,
            'supported': has_claim_dx or has_visit_dx, 'gaps': gaps}


def _scope(db, patient_ids, coverage, patient):
    rows = db.execute('SELECT patient_id, birth_date, sex_reported, coverage_type, pcp_practice_id, pcp_clinician_id FROM patient').fetchall() \
        if 'birth_date' in _columns(db, 'patient') else \
        [(r[0], None, r[1], r[2], r[3], r[4]) for r in db.execute(
            'SELECT patient_id, sex_reported, coverage_type, pcp_practice_id, pcp_clinician_id FROM patient')]
    keep = set(patient_ids) if patient_ids is not None else None
    out = {}
    for pid, birth, sex, cov, prac, pcp in rows:
        if keep is not None and pid not in keep: continue
        if patient and pid != patient: continue
        if coverage == 'medicare' and (cov or '').lower() != 'medicare': continue
        out[pid] = {'patient_id': pid, 'birth_date': birth, 'sex': 2 if str(sex).upper().startswith('F') else 1,
                    'coverage_type': cov, 'practice': prac, 'pcp': pcp}
    return out


def _in(ids):
    return ','.join('?' * len(ids))


def _chunks(ids, n=500):
    ids = sorted(ids)
    for i in range(0, len(ids), n): yield ids[i:i + n]


def diagnoses(db, cap, pids, start, end, sources='claims_and_encounters'):
    """Eligible and excluded diagnosis rows in [start, end]: dicts with patient_id, icd10, date, kind, ref, eligible, reason."""
    out = []
    lines = defaultdict(set)
    if cap['claim_diagnoses']:
        for ch in _chunks(pids):
            if cap['claim_lines']:
                for cid, cpt, hcpcs in db.execute(f'SELECT l.claim_id, l.cpt, l.hcpcs FROM claim_line l JOIN claim c USING(claim_id) '
                                                  f'WHERE c.patient_id IN ({_in(ch)}) AND c.service_date BETWEEN ? AND ?', (*ch, start, end)):
                    for x in (cpt, hcpcs):
                        if x: lines[cid].add(str(x).strip().upper())
            for cid, pid, sd, st, pdx, dxs, pos, tob, cpt, hcpcs, payer in db.execute(
                    f'SELECT claim_id, patient_id, service_date, final_status, principal_diagnosis, diagnosis_codes, place_of_service, '
                    f'type_of_bill, cpt, hcpcs, payer FROM claim WHERE patient_id IN ({_in(ch)}) AND service_date BETWEEN ? AND ?', (*ch, start, end)):
                codes = []
                try: codes = json.loads(dxs) if dxs else []
                except ValueError: codes = [c for c in re.split(r'[,;| ]+', dxs or '') if c]
                codes = list(dict.fromkeys([c for c in [pdx, *codes] if c]))
                procs = set(lines.get(cid, ())) | {str(x).strip().upper() for x in (cpt, hcpcs) if x}
                tob = (tob or '').strip()
                tob3 = ('0' + tob)[:3] if tob and not tob.startswith('0') else tob[:3]
                cy, eligible_set = eligible_cpt(int(sd[:4]))
                if st == 'denied': ok, why = False, 'denied claim'
                elif tob3 in INPATIENT_TOB: ok, why = True, 'inpatient facility claim'
                elif tob3 in OUTPATIENT_TOB:
                    ok = bool(procs & eligible_set)
                    why = 'outpatient facility claim with eligible CPT/HCPCS' if ok else 'outpatient facility claim without eligible CPT/HCPCS'
                elif tob3: ok, why = False, f'bill type {tob} not risk-adjustment eligible'
                elif (pos or '') in EXCLUDED_POS: ok, why = False, f'place of service {pos} ({EXCLUDED_POS[pos]})'
                else:
                    ok = bool(procs & eligible_set)
                    why = f'professional claim with CY{cy} eligible CPT/HCPCS' if ok else f'professional claim without CY{cy} eligible CPT/HCPCS'
                for c in codes:
                    out.append({'patient_id': pid, 'icd10': icd_key(c), 'date': sd, 'kind': 'claim', 'ref': cid, 'eligible': ok,
                                'reason': why, 'payer': payer})
    if cap['visit_diagnoses'] and sources == 'claims_and_encounters':
        vcols = _columns(db, 'visit')
        inp = 'v.in_person' if 'in_person' in vcols else 'NULL'
        for ch in _chunks(pids):
            for enc, pid, icd, vtype, status, day, person, spec, rid in db.execute(
                    f'SELECT d.encounter_id, d.patient_id, d.icd10, v.type, v.status, v.event_date, {inp}, c.specialty, d.record_id '
                    f'FROM encounter_diagnosis d JOIN visit v ON v.encounter_id = d.encounter_id '
                    f'LEFT JOIN clinician c ON c.clinician_id = v.attending_clinician_id '
                    f'WHERE d.patient_id IN ({_in(ch)}) AND v.event_date BETWEEN ? AND ?', (*ch, start, end)):
                t = (vtype or '').lower()
                sp = (spec or '').lower()
                if t in EXCLUDED_VISIT_TYPES: ok, why = False, EXCLUDED_VISIT_TYPES[t]
                elif sp in EXCLUDED_SPECIALTIES: ok, why = False, f'provider type {spec} not acceptable'
                elif person == 0 and t not in TELEHEALTH_VIDEO: ok, why = False, 'not face to face'
                elif status not in (None, 'completed', 'finished'): ok, why = False, f'visit status {status}'
                else: ok, why = True, 'face-to-face visit' + ('' if spec else ' (provider type not recorded)')
                out.append({'patient_id': pid, 'icd10': icd_key(icd), 'date': day, 'kind': 'visit', 'ref': enc, 'record_id': rid,
                            'eligible': ok, 'reason': why, 'visit_type': vtype, 'specialty': spec})
    return out


def _model_hccs(model, codes, age, sex):
    return apply_hierarchy(model, ccs_for(model, codes, age, sex))[0]


def _same_or_higher(model, hcc, current):
    """Gap closed when the same HCC or one that dominates it in the hierarchy is coded."""
    if hcc in current: return True
    return any(hcc in excl and h in current for h, excl in model['hierarchy'])


# ---- evidence for suspects --------------------------------------------------------------------------------------------------
def _evidence(db, pids, start, end):
    ev = defaultdict(lambda: defaultdict(list))
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    mcols = _columns(db, 'measurement')
    loinc = 'loinc' if 'loinc' in mcols else 'NULL'
    for ch in _chunks(pids):
        for pid, rid, concept, lo, val, day, name in db.execute(
                f"SELECT patient_id, record_id, concept, {loinc}, value_canonical, event_date, name FROM measurement WHERE patient_id IN ({_in(ch)}) "
                f"AND event_date BETWEEN ? AND ? AND (concept IN ('egfr','hba1c','phq9','weight','bmi','lvef') OR {loinc} IN "
                f"('98979-8','33914-3','62238-1','48642-3','48643-1','4548-4','17856-6','44261-6','29463-7','39156-5','8302-2','10230-1','18043-0'))",
                (*ch, start, end)):
            if val is None: continue
            k = {'98979-8': 'egfr', '33914-3': 'egfr', '62238-1': 'egfr', '48642-3': 'egfr', '48643-1': 'egfr', '4548-4': 'hba1c', '17856-6': 'hba1c',
                 '44261-6': 'phq9', '29463-7': 'weight', '39156-5': 'bmi', '10230-1': 'lvef', '18043-0': 'lvef'}.get(lo, concept)
            ev[pid][k].append({'record_id': rid, 'date': day, 'value': val, 'source': 'measurement', 'name': name})
        for pid, rid, val in db.execute(f"SELECT patient_id, record_id, value_canonical FROM measurement WHERE patient_id IN ({_in(ch)}) "
                                        f"AND ({loinc} = '8302-2' OR name = 'height_cm') AND value_canonical IS NOT NULL", ch):
            ev[pid]['height'].append({'record_id': rid, 'value': val})
        if 'medication' in tables:
            for pid, rid, cls, ev_kind, day, name in db.execute(
                    f"SELECT patient_id, record_id, drug_class, evidence, event_date, drug_name FROM medication WHERE patient_id IN ({_in(ch)}) "
                    f"AND drug_class IN ('insulin','glp1_agonist','sglt2_inhibitor') AND evidence IN ('dispensed','claim','pharmacy_history') "
                    f"AND event_date BETWEEN ? AND ?", (*ch, start, end)):
                ev[pid]['diabetes_drug'].append({'record_id': rid, 'date': day, 'value': cls, 'source': f'medication ({ev_kind})', 'name': name})
        if 'note_fact' in tables:
            for pid, rid, val, day, text in db.execute(
                    f"SELECT patient_id, record_id, value, event_date, evidence_text FROM note_fact WHERE patient_id IN ({_in(ch)}) "
                    f"AND fact_type = 'ejection_fraction' AND COALESCE(negated,0) = 0 AND COALESCE(temporality,'current') != 'historical' "
                    f"AND event_date BETWEEN ? AND ?", (*ch, start, end)):
                try: v = float(val)
                except (TypeError, ValueError): continue
                ev[pid]['lvef'].append({'record_id': rid, 'date': day, 'value': v, 'source': 'note_fact', 'name': (text or '')[:120]})
        for stream, field in (('mood_questionnaires', '$.total'), ('health_risk_assessments', '$.phq2_total')):
            for pid, rid, val, day, inst in db.execute(
                    f"SELECT patient_id, record_id, json_extract(payload, '{field}'), event_date, json_extract(payload, '$.instrument') FROM record "
                    f"WHERE stream = ? AND patient_id IN ({_in(ch)}) AND event_date BETWEEN ? AND ?", (stream, *ch, start, end)):
                if val is None: continue
                key = 'phq2' if stream == 'health_risk_assessments' else ('phq9' if (inst or 'PHQ-9') == 'PHQ-9' else 'mood_other')
                ev[pid][key].append({'record_id': rid, 'date': day, 'value': float(val), 'source': stream, 'name': inst or 'PHQ-2'})
        if 'condition' in tables and 'icd10' in _columns(db, 'condition'):
            chronic = 'AND COALESCE(chronic, 1) = 1' if 'chronic' in _columns(db, 'condition') else ''
            for pid, rid, icd, label, day, src in db.execute(
                    f"SELECT DISTINCT patient_id, record_id, icd10, label, COALESCE(event_date, recorded_date, onset), source_table FROM condition "
                    f"WHERE patient_id IN ({_in(ch)}) AND icd10 IS NOT NULL {chronic}", ch):
                ev[pid]['problem_list'].append({'record_id': rid, 'date': day, 'value': icd_key(icd), 'source': src, 'name': label})
    return ev


def _has_code(codes, prefixes):
    return any(c.startswith(p) for c in codes for p in prefixes)


def ckd_icd(egfr):
    return 'N185' if egfr < 15 else 'N184' if egfr < 30 else 'N1832' if egfr < 45 else 'N1831'


def suspects_for(ev, codes, eligible_hccs, age, sex, model):
    """Documentation review prompts for one patient. codes: all ICD-10 coded on eligible sources (collection year + current YTD)."""
    out = []

    def add(cat, evidence, icd=None, extra=None):
        hcc = _model_hccs(model, [icd], age, sex) if icd else []
        out.append({'category': cat, 'label': SUSPECTS[cat]['label'], 'reading': SUSPECTS[cat]['reading'],
                    'evidence': evidence[:6], 'evidence_rows': len(evidence), 'illustrative_icd10': icd,
                    'hcc': hcc[0] if hcc else None, 'hcc_label': model['labels'].get(hcc[0]) if hcc else None, **(extra or {})})

    egfr = sorted((e for e in ev.get('egfr', []) if e['date'] and e['value'] < 60), key=lambda e: e['date'])
    if egfr and not _has_code(codes, SUSPECTS['ckd_egfr']['codes']):
        first = egfr[0]
        later = [e for e in egfr if (date.fromisoformat(e['date']) - date.fromisoformat(first['date'])).days >= 90]
        if later:
            low = min(first['value'], later[-1]['value'])
            add('ckd_egfr', [first, later[-1]], ckd_icd(max(first['value'], later[-1]['value'])), {'lowest_egfr': low})
    a1c = [e for e in ev.get('hba1c', []) if e['value'] >= 6.5]
    diabetic = _has_code(codes, SUSPECTS['diabetes_a1c']['codes'])
    if a1c and not diabetic:
        add('diabetes_a1c', sorted(a1c, key=lambda e: e['date'] or '', reverse=True), 'E119',
            {'highest_a1c': max(e['value'] for e in a1c), 'confirmed_by_repeat': len({e['date'] for e in a1c}) >= 2})
    drugs = ev.get('diabetes_drug', [])
    if drugs and not diabetic:
        add('diabetes_medication', sorted(drugs, key=lambda e: e['date'] or '', reverse=True), 'E119',
            {'drug_classes': sorted({e['value'] for e in drugs})})
    ef = [e for e in ev.get('lvef', []) if e['value'] <= 40]
    if ef and not _has_code(codes, SUSPECTS['hf_ejection_fraction']['codes']):
        add('hf_ejection_fraction', sorted(ef, key=lambda e: e['date'] or '', reverse=True), 'I5022', {'lowest_ef': min(e['value'] for e in ef)})
    bmis = [dict(e, value=round(e['value'], 1)) for e in ev.get('bmi', []) if e['value'] >= 40]
    heights = [h['value'] for h in ev.get('height', []) if h['value'] and 120 <= h['value'] <= 230]
    if heights:
        hm = statistics.median(heights) / 100
        for w in ev.get('weight', []):
            b = w['value'] / (hm * hm)
            if b >= 40: bmis.append(dict(w, value=round(b, 1), name=f'BMI from weight {w["value"]} kg, height {round(hm * 100)} cm'))
    if bmis and not _has_code(codes, SUSPECTS['morbid_obesity']['codes']):
        add('morbid_obesity', sorted(bmis, key=lambda e: e['date'] or '', reverse=True), 'Z6841', {'highest_bmi': max(e['value'] for e in bmis)})
    dep = [e for e in ev.get('phq9', []) if e['value'] >= 10] + [e for e in ev.get('phq2', []) if e['value'] >= 3]
    if dep and not _has_code(codes, SUSPECTS['depression_screen']['codes']):
        add('depression_screen', sorted(dep, key=lambda e: e['date'] or '', reverse=True), 'F321')
    seen = set()
    for e in ev.get('problem_list', []):
        hs = _model_hccs(model, [e['value']], age, sex)
        for h in hs:
            if h in seen or _same_or_higher(model, h, eligible_hccs): continue
            seen.add(h)
            rows = [x for x in ev['problem_list'] if h in _model_hccs(model, [x['value']], age, sex)]
            out.append({'category': 'documented_not_coded', 'label': SUSPECTS['documented_not_coded']['label'],
                        'reading': SUSPECTS['documented_not_coded']['reading'], 'evidence': rows[:6], 'evidence_rows': len(rows),
                        'illustrative_icd10': e['value'], 'hcc': h, 'hcc_label': model['labels'].get(h)})
    return out


# ---- engine ------------------------------------------------------------------------------------------------------------------
def _years(params, as_of):
    y = params['year'] or (as_of.year - 1)
    return y, date(y, 1, 1), date(y, 12, 31)


def _pct(xs, q):
    if not xs: return None
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def _dist(xs):
    return {'n': len(xs), 'mean': round(statistics.fmean(xs), 3) if xs else None, 'median': _pct(xs, 0.5), 'p10': _pct(xs, 0.1),
            'p25': _pct(xs, 0.25), 'p75': _pct(xs, 0.75), 'p90': _pct(xs, 0.9), 'min': min(xs) if xs else None, 'max': max(xs) if xs else None}


def execute(path, patient_ids, params, as_of=None):
    params = validate(params)
    db = connect(path)
    try:
        cap = capability(db)
        as_of_d = date.fromisoformat(str(as_of or cap['as_of'] or date.today().isoformat())[:10])
        return run(db, cap, patient_ids, params, as_of_d)
    finally:
        db.close()


def run(db, cap, patient_ids, params, as_of):
    year, start, end = _years(params, as_of)
    pay = payment_parameters(params['payment_year'])
    base_rate = params['base_rate_pmpm'] or ILLUSTRATIVE_BASE_RATE
    rate_note = (f'base rate ${base_rate:,.0f} PMPM supplied' if params['base_rate_pmpm']
                 else f'ILLUSTRATIVE base rate ${ILLUSTRATIVE_BASE_RATE:,.0f} PMPM (no rate supplied; not a contracted or county benchmark)')
    report = {'version': VERSION, 'parameters': params, 'capability': cap, 'synthetic': True,
              'period': {'collection_year': year, 'start': start.isoformat(), 'end': end.isoformat(),
                         'current_year': year + 1, 'current_to': min(as_of, date(year + 1, 12, 31)).isoformat(), 'as_of': as_of.isoformat()},
              'payment': {'payment_year': params['payment_year'], 'blend': pay['blend'], 'normalization': pay['normalization'],
                          'ma_coding_pattern_adjustment': pay['ma_coding_pattern_adjustment'], 'origin': pay['origin'], 'source': pay.get('url') or pay.get('source'),
                          'base_rate_pmpm': base_rate, 'base_rate_note': rate_note},
              'tables': {k: {'url': v['url'], 'payment_year': v['payment_year'], 'sha256': v['sha256'][:16]}
                         for k, v in manifest()['tables'].items() if not k.endswith('.txt') or k.startswith('eligible')},
              'assumptions': [f'CMS PY{params["payment_year"]} parameters applied to synthetic collection year {year}; ages as of '
                              f'{year + 1}-02-01; segment {params["segment"]} (aged 65+ / disabled under 65), no originally-disabled, ESRD, hospice or new-enrollee status.',
                              f'Coverage scope: {params["coverage"]}' + (' (CMS-HCC is a Medicare model; other coverage is scored illustratively).' if params['coverage'] == 'all' else '.'),
                              f'Diagnosis sources: {params["sources"]}.', rate_note],
              'method': ('CMS-HCC V24 and V28 scoring from the CMS model software tables (crosswalk with edits, hierarchies, interactions, '
                         'payment-HCC counts, community relative factors), CMS encounter-data diagnosis filtering, payment-year blend, '
                         'normalization and MA coding pattern adjustment from the Rate Announcement; recapture by calendar year; '
                         'evidence-based suspects as documentation review prompts.'),
              'limitations': LIMITATIONS, 'patients': [], 'population': {}, 'data_gaps': list(cap['gaps'])}
    if not cap['supported']:
        report['status'] = 'unsupported'
        report['data_gaps'].insert(0, 'This build carries no ICD-10 diagnosis codes on encounters or claims, so HCCs cannot be assigned. '
                                      'Use a build with semantic-layer/13 or later (warehouse v2.0).')
        return report
    scope = _scope(db, patient_ids, params['coverage'], params['patient'])
    report['population']['patients_in_scope'] = len(scope)
    if not scope:
        report['status'] = 'empty'
        return report
    pids = list(scope)
    prior_start, cur_end = date(year - 1, 1, 1), min(as_of, date(year + 1, 12, 31))
    dx = diagnoses(db, cap, pids, prior_start.isoformat(), cur_end.isoformat(), params['sources'])
    ev = _evidence(db, pids, date.fromordinal(as_of.toordinal() - params['lookback_days']).isoformat(), as_of.isoformat())
    by_pat = defaultdict(lambda: defaultdict(list))
    filt = Counter()
    for d in dx:
        y = int(d['date'][:4])
        period = 'prior' if y == year - 1 else 'year' if y == year else 'current' if y == year + 1 else None
        if period is None: continue
        by_pat[d['patient_id']][period].append(d)
        if period == 'year': filt[(d['kind'], d['eligible'], d['reason'])] += 1
    report['diagnosis_filter'] = [{'kind': k, 'eligible': e, 'reason': r, 'diagnosis_rows': n} for (k, e, r), n in filt.most_common()]
    models = {m: load_model(m) for m in MODELS}
    primary = 'V28'
    payer_of = {}
    for pid, per in by_pat.items():
        pc = Counter(d.get('payer') for d in per['year'] if d['kind'] == 'claim' and d.get('payer'))
        if pc: payer_of[pid] = pc.most_common(1)[0][0]
    pats, unknown_age = [], []
    for pid, info in scope.items():
        # CMS defines age on February 1 of the payment year. Missing/invalid
        # birth dates cannot be replaced with feed age or a zero-age score; a newborn born after the date is age 0.
        age = scoring_age(info['birth_date'], date(year + 1, 2, 1))
        if age is None:
            unknown_age.append(pid)
            continue
        sex = info['sex']
        per = by_pat.get(pid, {})
        elig = {k: [d for d in per.get(k, []) if d['eligible']] for k in ('prior', 'year', 'current')}
        codes = {k: [d['icd10'] for d in v] for k, v in elig.items()}
        scores = {m: score(m, age, sex, codes['year'], params['segment']) for m in MODELS}
        raws = {m: s['raw'] for m, s in scores.items()}
        paid = payment_raf(raws, pay)
        demo = payment_raf({m: s['demographic_raw'] for m, s in scores.items()}, pay)
        claim_codes = {d['icd10'] for d in elig['year'] if d['kind'] == 'claim'}
        ehr_only = [h['hcc'] for h in scores[primary]['hccs'] if not set(h['codes']) & claim_codes]
        # recapture: HCCs of the prior year vs this year (historic) and this year vs the current year to date (open gaps)
        def gaps(src_codes, dst_codes, src_rows):
            out = []
            m = models[primary]
            src = _model_hccs(m, src_codes, age, sex)
            dst = _model_hccs(m, dst_codes, age, sex)
            for h in src:
                if _same_or_higher(m, h, dst): continue
                support = sorted((d for d in src_rows if h in _model_hccs(m, [d['icd10']], age, sex)), key=lambda d: d['date'])
                last = support[-1] if support else None
                out.append({'hcc': h, 'label': m['labels'].get(h), 'last_supporting': last and {
                    'date': last['date'], 'kind': last['kind'], 'ref': last['ref'], 'icd10': last['icd10'], 'reason': last['reason']},
                    'codes': sorted({d['icd10'] for d in support})})
            return src, out
        prior_hccs, closed_hist = gaps(codes['prior'], codes['year'], elig['prior'])
        year_hccs, open_gaps = gaps(codes['year'], codes['current'], elig['year'])
        cur_raws = {m: score(m, age, sex, codes['current'], params['segment'])['raw'] for m in MODELS}
        full_codes = codes['current'] + [c for g in open_gaps for c in g['codes']]
        full_raws = {m: score(m, age, sex, full_codes, params['segment'])['raw'] for m in MODELS}
        for g in open_gaps:
            one = {m: score(m, age, sex, codes['current'] + g['codes'], params['segment'])['raw'] for m in MODELS}
            g['raf_delta'] = round(payment_raf(one, pay) - payment_raf(cur_raws, pay), 3)
            g['annual_value'] = round(g['raf_delta'] * base_rate * 12, 2)
        recapture_delta = round(payment_raf(full_raws, pay) - payment_raf(cur_raws, pay), 3)
        sus = suspects_for(ev.get(pid, {}), codes['year'] + codes['current'], set(_model_hccs(models[primary], codes['year'] + codes['current'], age, sex)),
                           age, sex, models[primary])
        base_codes = codes['year'] + codes['current']
        base_raws = {m: score(m, age, sex, base_codes, params['segment'])['raw'] for m in MODELS}
        for s in sus:
            if s['illustrative_icd10']:
                one = {m: score(m, age, sex, base_codes + [s['illustrative_icd10']], params['segment'])['raw'] for m in MODELS}
                s['raf_delta_if_documented'] = round(payment_raf(one, pay) - payment_raf(base_raws, pay), 3)
            else:
                s['raf_delta_if_documented'] = 0.0
            s['annual_value_if_documented'] = round(s['raf_delta_if_documented'] * base_rate * 12, 2)
        pats.append({'patient_id': pid, 'age': age, 'sex': 'F' if sex == 2 else 'M', 'coverage_type': info['coverage_type'],
                     'practice': info['practice'], 'pcp': info['pcp'], 'payer': payer_of.get(pid), 'segment': scores[primary]['segment'],
                     'raw': raws, 'payment_raf': paid, 'demographic_payment_raf': demo,
                     'hccs': {m: [{'hcc': h['hcc'], 'label': h['label'], 'codes': h['codes']} for h in scores[m]['hccs']] for m in MODELS},
                     'interactions': {m: scores[m]['interactions'] for m in MODELS},
                     'dropped_by_hierarchy': {m: scores[m]['dropped_by_hierarchy'] for m in MODELS},
                     'payment_hcc_count': {m: scores[m]['payment_hcc_count'] for m in MODELS},
                     # every coefficient term for small scopes (one patient, a short list); population runs keep the HCCs only
                     'terms': {m: scores[m]['terms'] for m in MODELS} if len(scope) <= 50 else None, 'ehr_only_hccs': ehr_only,
                     'eligible_diagnoses': len(elig['year']), 'excluded_diagnoses': len(per.get('year', [])) - len(elig['year']),
                     'prior_year_hccs': prior_hccs, 'not_recaptured_from_prior': closed_hist, 'open_recapture_gaps': open_gaps,
                     'current_year_payment_raf_to_date': payment_raf(cur_raws, pay), 'recapture_raf_at_stake': recapture_delta,
                     'recapture_value': round(recapture_delta * base_rate * 12, 2), 'suspects': sus,
                     'suspect_value': round(sum(s['annual_value_if_documented'] for s in sus), 2)})
    report['patients'] = sorted(pats, key=lambda p: -p['payment_raf'])
    report['population'] = population(pats, params, base_rate)
    report['population'].update(patients_in_scope=len(scope), patients_scored=len(pats),
                                patients_with_unknown_age=len(unknown_age))
    report['unknown_age_patient_ids'] = unknown_age
    if unknown_age:
        report['data_gaps'].append(f'{len(unknown_age)} patients lack a valid birth-date age at the CMS reference date; their RAF is unknown.')
    report['data_gaps'] += warehouse_findings(pats, filt, report['population'])
    if params['focus'] == 'fairness':  # does the risk score pay enough for each group? (Zink & Rose; ra_fairness.py)
        from . import ra_fairness
        report['fairness'] = ra_fairness.diagnose(db, pats, start.isoformat(), end.isoformat())
        report['method'] += ' Fairness: ' + report['fairness'].get('method', report['fairness'].get('note', ''))
    report['status'] = 'partial' if unknown_age else 'answered'
    return report


def population(pats, params, base_rate):
    pr = [p['payment_raf'] for p in pats]
    out = {'patients_in_scope': len(pats), 'payment_raf': _dist(pr), 'raw': {m: _dist([p['raw'][m] for p in pats]) for m in MODELS},
           'demographic_only_payment_raf': _dist([p['demographic_payment_raf'] for p in pats]),
           'patients_with_hcc': sum(1 for p in pats if p['hccs']['V28']), 'hcc_counts': {}}
    hc = Counter((h['hcc'], h['label']) for p in pats for h in p['hccs']['V28'])
    out['top_hccs'] = [{'hcc': h, 'label': l, 'patients': n} for (h, l), n in hc.most_common(15)]
    prior = sum(len(p['prior_year_hccs']) for p in pats)
    missed = sum(len(p['not_recaptured_from_prior']) for p in pats)
    year_hccs = sum(len(p['hccs']['V28']) for p in pats)
    open_gaps = sum(len(p['open_recapture_gaps']) for p in pats)
    out['recapture'] = {'prior_year_hccs': prior, 'recaptured_in_collection_year': prior - missed,
                        'recapture_rate': round((prior - missed) / prior, 3) if prior else None,
                        'collection_year_hccs': year_hccs, 'open_gaps_current_year': open_gaps,
                        'recaptured_current_year_to_date': year_hccs - open_gaps,
                        'recapture_rate_to_date': round((year_hccs - open_gaps) / year_hccs, 3) if year_hccs else None,
                        'patients_with_open_gaps': sum(1 for p in pats if p['open_recapture_gaps']),
                        'raf_at_stake': round(sum(p['recapture_raf_at_stake'] for p in pats), 3),
                        'annual_value': round(sum(p['recapture_value'] for p in pats), 2)}
    cats = Counter(s['category'] for p in pats for s in p['suspects'])
    val = defaultdict(float)
    for p in pats:
        for s in p['suspects']: val[s['category']] += s['annual_value_if_documented']
    out['suspects'] = [{'category': c, 'label': SUSPECTS[c]['label'], 'patients': n, 'annual_value_if_documented': round(val[c], 2)}
                       for c, n in cats.most_common()]
    suspect_hccs = sum(1 for p in pats for s in p['suspects'] if s.get('hcc'))
    denom = year_hccs + missed + suspect_hccs
    out['coding_completeness'] = {'captured_hccs': year_hccs, 'missed_prior_year_hccs': missed, 'suspect_hccs': suspect_hccs,
                                  'rate': round(year_hccs / denom, 3) if denom else None,
                                  'definition': 'captured HCCs / (captured + prior-year HCCs not recaptured + suspect HCCs), collection year'}
    out['ehr_only_hccs'] = sum(len(p['ehr_only_hccs']) for p in pats)
    out['dollars_at_stake'] = {'recapture': out['recapture']['annual_value'],
                               'suspects': round(sum(p['suspect_value'] for p in pats), 2), 'base_rate_pmpm': base_rate,
                               'note': 'annual = RAF delta x base rate x 12; suspects are valued as if documented and coded, which review may not support'}
    groups = {}
    for gb in ('practice', 'pcp', 'payer', 'coverage_type'):
        rows = defaultdict(list)
        for p in pats: rows[p.get(gb) or 'unassigned'].append(p)
        groups[gb] = sorted(({'group': g, 'patients': len(ps), 'mean_payment_raf': round(statistics.fmean(p['payment_raf'] for p in ps), 3),
                              'median_payment_raf': _pct([p['payment_raf'] for p in ps], 0.5),
                              'mean_raw_v24': round(statistics.fmean(p['raw']['V24'] for p in ps), 3),
                              'mean_raw_v28': round(statistics.fmean(p['raw']['V28'] for p in ps), 3),
                              'open_recapture_gaps': sum(len(p['open_recapture_gaps']) for p in ps),
                              'suspects': sum(len(p['suspects']) for p in ps),
                              'dollars_at_stake': round(sum(p['recapture_value'] + p['suspect_value'] for p in ps), 2)}
                             for g, ps in rows.items()), key=lambda r: -r['patients'])
    out['by'] = groups
    edges = [0, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, math.inf]
    out['histogram'] = [{'from': edges[i], 'to': None if edges[i + 1] == math.inf else edges[i + 1],
                         'patients': sum(1 for x in pr if edges[i] <= x < edges[i + 1])} for i in range(len(edges) - 1)]
    return out


def warehouse_findings(pats, filt, pop):
    """Implausible coding patterns worth sending to the data generator."""
    out = []
    lab = sum(n for (k, e, r), n in filt.items() if 'place of service 81' in r)
    if lab: out.append(f'{lab} diagnosis rows sit on claims with place of service 81 (independent laboratory), excluded; several such claims carry '
                       f'E/M, preventive, vaccine or imaging codes, which is implausible for POS 81.')
    tel = sum(n for (k, e, r), n in filt.items() if r == 'audio-only telephone')
    if tel: out.append(f'{tel} diagnosis rows come from audio-only telephone visits (not risk-adjustment eligible).')
    if pats and pop['patients_with_hcc'] / len(pats) < 0.3:
        out.append(f'Only {pop["patients_with_hcc"]} of {len(pats)} patients have any V28 payment HCC on an eligible source.')
    return out


# ---- answer --------------------------------------------------------------------------------------------------------------------
def _usd(v):
    return f'${v:,.0f}'


def summarize(report, limit=None):
    if report.get('status') == 'unsupported':
        return 'Risk adjustment is not available on this build: ' + '; '.join(report['data_gaps'][:2]), []
    if report.get('status') == 'empty':
        return 'No patients are in scope for risk adjustment (CMS-HCC applies to Medicare coverage by default).', []
    p, pop, par = report['payment'], report['population'], report['parameters']
    limit = limit or par['top']
    pr, rc, cc = pop['payment_raf'], pop['recapture'], pop['coding_completeness']
    blend = ' + '.join(f'{int(w * 100)}% {m}' for m, w in p['blend'].items())
    value = (f"{pop['patients_in_scope']} {par['coverage']} patients, collection year {report['period']['collection_year']}: mean payment RAF "
             f"{pr['mean']} (median {pr['median']}; PY{p['payment_year']} {blend}, normalised, minus {p['ma_coding_pattern_adjustment']:.1%} coding "
             f"adjustment); raw V24 {pop['raw']['V24']['mean']}, raw V28 {pop['raw']['V28']['mean']}. Coding completeness {cc['rate']:.1%}; "
             f"prior-year HCC recapture {rc['recapture_rate']:.1%}" if rc['recapture_rate'] is not None and cc['rate'] is not None else
             f"{pop['patients_in_scope']} patients: mean payment RAF {pr['mean']}")
    value += (f"; {rc['open_gaps_current_year']} open recapture gaps in {report['period']['current_year']} to date "
              f"({_usd(rc['annual_value'])}/yr at stake), {sum(s['patients'] for s in pop['suspects'])} suspect documentation prompts "
              f"({_usd(pop['dollars_at_stake']['suspects'])}/yr if documented). {p['base_rate_note']}.")
    focus, gb = par['focus'], par['group_by']
    if focus == 'fairness' and report.get('fairness'):
        from . import ra_fairness
        value = ra_fairness.summary(report['fairness']) + ' ' + value
        cols = ('dimension', 'group', 'patients', 'predictive_ratio', 'pr_ci90', 'predicted_cost', 'realized_cost', 'mean_payment_raf', 'status')
        return value, [{k: r[k] for k in cols} for r in report['fairness'].get('groups', [])][:max(limit, 60)]
    if gb != 'none' or focus == 'population':
        table = pop['by'][gb if gb != 'none' else 'practice'][:limit]
    elif focus == 'recapture':
        table = [{'patient_id': x['patient_id'], 'hcc': g['hcc'], 'label': g['label'], 'last_supporting_date': (g['last_supporting'] or {}).get('date'),
                  'last_supporting_source': (g['last_supporting'] or {}).get('kind'), 'icd10': (g['last_supporting'] or {}).get('icd10'),
                  'raf_delta': g['raf_delta'], 'annual_value': g['annual_value']}
                 for x in report['patients'] for g in x['open_recapture_gaps']]
        table = sorted(table, key=lambda r: -r['annual_value'])[:limit]
    elif focus == 'suspects':
        table = [{'patient_id': x['patient_id'], 'category': s['category'], 'label': s['label'], 'hcc': s.get('hcc'),
                  'evidence': '; '.join(f"{e.get('name') or e.get('source')} {e.get('value')} on {e.get('date')} [{e.get('record_id')}]" for e in s['evidence'][:3]),
                  'raf_delta_if_documented': s['raf_delta_if_documented'], 'annual_value_if_documented': s['annual_value_if_documented']}
                 for x in report['patients'] for s in x['suspects']]
        table = sorted(table, key=lambda r: -r['annual_value_if_documented'])[:limit]
    else:
        table = [{'patient_id': x['patient_id'], 'age': x['age'], 'sex': x['sex'], 'payment_raf': x['payment_raf'], 'raw_v24': x['raw']['V24'],
                  'raw_v28': x['raw']['V28'], 'hccs_v28': ', '.join(f"{h['hcc']} {h['label']}" for h in x['hccs']['V28']),
                  'open_recapture_gaps': len(x['open_recapture_gaps']), 'suspects': len(x['suspects']),
                  'dollars_at_stake': round(x['recapture_value'] + x['suspect_value'], 2)} for x in report['patients'][:limit]]
    return value, table


def follow_up():
    return ['What is the average RAF by practice?', 'Which patients have open HCC recapture gaps?',
            'Show suspect conditions without a diagnosis code', 'What is our coding completeness rate?']
