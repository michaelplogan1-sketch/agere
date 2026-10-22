"""Clinical documentation improvement (CDI) opportunities: expected vs coded MS-DRG severity on inpatient claims.

Population   inpatient institutional claims with an MS-DRG (payer_claim, counts_in_metrics=1, admission..discharge), one row
             per claim (the final adjudicated version). Coded diagnoses = payer_claim.diagnosis_codes + payer_claim_diagnosis.
Indicators   EHR evidence inside the stay window (admission - 1 day .. discharge), each mapped to the secondary diagnosis a CDI
             specialist would query (never assigned automatically):
  hyponatremia        sodium < 130 mmol/L, or two values < 135                                 -> E87.1  CC
  hypernatremia       sodium > 150 mmol/L                                                     -> E87.0  CC
  acute kidney injury creatinine rise >= 0.3 mg/dL within 48 h or >= 1.5 x the lowest value in the 7 days before/at
                      admission (KDIGO 2012 AKI guideline, section 2.1.1)                     -> N17.9  CC
  acute blood loss    hemoglobin fall >= 2 g/dL during the stay (query requires a documented bleed) -> D62 CC
  sepsis              EHR sepsis diagnosis in the window, or >= 2 SIRS criteria (heart rate > 90, WBC > 12 or < 4 x10^3/uL;
                      Bone et al., Chest 1992) with an infection principal diagnosis           -> A41.9  MCC
  acute resp. failure SpO2 < 91% with a respiratory / heart-failure principal diagnosis        -> J96.01 MCC
  acute on chronic HF heart failure documented or coded, NT-proBNP >= 900 pg/mL in the stay, claim carries only unspecified /
                      chronic HF (I50.9, I50.x2)                                               -> I50.23 (EF <= 40%) / I50.33 MCC
  CKD stage 4 / 5     eGFR < 30 (< 15) in the stay and also >= 90 days earlier (chronicity, KDIGO 2012 CKD), claim lacks
                      N18.4-N18.6                                                             -> N18.4 / N18.5 CC
  malnutrition        weight loss > 5% within 7-30 days or > 7.5% within 90 days before discharge, not on heart-failure principal
                      stays (diuresis); ASPEN/AND consensus, White et al., JPEN 2012                                                 -> E43 MCC (severe) / E44.0 CC
  morbid obesity      BMI >= 40 (only when a BMI measurement is received)                     -> E66.01 + Z68.41 CC
  EHR-documented      any EHR condition dated in the window whose ICD-10 is in the CC/MCC table and is not on the claim
CC/MCC designations: CMS MS-DRG v41 Definitions Manual, Appendix C / FY2024 IPPS final rule Tables 6I (MCC) and 6J (CC);
curated subset in CC_MCC below (verify against the grouper year in use). CC exclusion is approximated by skipping a candidate
whose 3-character category equals the principal diagnosis category.
Severity     MS-DRG families (DRG_FAMILIES: three-way MCC / CC / without, or two-way MCC / without, or CC-or-MCC / without)
             give the coded tier from the billed DRG and the expected tier from coded + supported secondary diagnoses.
Dollars      expected impact = observed mean allowed of the target-tier DRG minus the coded DRG (this warehouse, >= 3 claims
             each), else the coded DRG's mean allowed x the stated step ratio (tier_step, default CC +25%, MCC +60%: an
             approximation of typical MS-DRG relative-weight steps, not a CMS weight). Opportunities are query prompts.
"""
from collections import defaultdict
from datetime import date, timedelta
import json
import re
import sqlite3

from . import revenue_common as rc

VERSION = 'cdi-opportunity/1'
OP = 'cdi_opportunity'
SOURCE = 'CMS MS-DRG v41 Definitions Manual Appendix C; FY2024 IPPS final rule Tables 6I/6J (curated subset)'
# ICD-10-CM -> (CC|MCC, label)
CC_MCC = {
    'A41.9': ('MCC', 'Sepsis, unspecified organism'), 'A41.89': ('MCC', 'Other specified sepsis'), 'R65.20': ('MCC', 'Severe sepsis without shock'),
    'R65.21': ('MCC', 'Severe sepsis with septic shock'), 'J96.01': ('MCC', 'Acute respiratory failure with hypoxia'),
    'J96.02': ('MCC', 'Acute respiratory failure with hypercapnia'), 'J96.21': ('MCC', 'Acute on chronic respiratory failure with hypoxia'),
    'I50.21': ('MCC', 'Acute systolic heart failure'), 'I50.23': ('MCC', 'Acute on chronic systolic heart failure'),
    'I50.31': ('MCC', 'Acute diastolic heart failure'), 'I50.33': ('MCC', 'Acute on chronic diastolic heart failure'),
    'I50.41': ('MCC', 'Acute combined heart failure'), 'I50.43': ('MCC', 'Acute on chronic combined heart failure'),
    'I50.22': ('CC', 'Chronic systolic heart failure'), 'I50.32': ('CC', 'Chronic diastolic heart failure'), 'I50.42': ('CC', 'Chronic combined heart failure'),
    'G93.41': ('MCC', 'Metabolic encephalopathy'), 'E43': ('MCC', 'Unspecified severe protein-calorie malnutrition'),
    'E44.0': ('CC', 'Moderate protein-calorie malnutrition'), 'E44.1': ('CC', 'Mild protein-calorie malnutrition'),
    'N17.0': ('MCC', 'Acute kidney failure with tubular necrosis'), 'N17.9': ('CC', 'Acute kidney failure, unspecified'),
    'N18.4': ('CC', 'Chronic kidney disease, stage 4'), 'N18.5': ('CC', 'Chronic kidney disease, stage 5'), 'N18.6': ('MCC', 'End stage renal disease'),
    'E87.0': ('CC', 'Hyperosmolality and hypernatremia'), 'E87.1': ('CC', 'Hypo-osmolality and hyponatremia'),
    'D62': ('CC', 'Acute posthemorrhagic anemia'), 'E66.01': ('CC', 'Morbid (severe) obesity due to excess calories'),
    'Z68.41': ('CC', 'BMI 40.0-44.9, adult'), 'J18.9': ('CC', 'Pneumonia, unspecified organism'), 'J44.1': ('CC', 'COPD with acute exacerbation'),
    'I48.1': ('CC', 'Persistent atrial fibrillation'), 'I48.2': ('CC', 'Chronic atrial fibrillation'), 'E11.10': ('CC', 'Type 2 diabetes with ketoacidosis'),
}
TIERS = ('none', 'CC', 'MCC')
# MS-DRG family -> {drg: tier}; 'split' = 3 (MCC/CC/without), 'mcc' (MCC/without), 'ccmcc' (CC or MCC / without)
_F = [('Intracranial hemorrhage or cerebral infarction', 3, '064 065 066'), ('COPD', 3, '190 191 192'), ('Simple pneumonia & pleurisy', 3, '193 194 195'),
      ('Bronchitis & asthma', 'ccmcc', '202 203'), ('Cardiac valve & other major cardiothoracic procedures w/o cath', 3, '219 220 221'),
      ('Coronary bypass w/o cardiac cath', 'mcc', '235 236'), ('Heart failure & shock', 3, '291 292 293'), ('Hypertension', 'mcc', '304 305'),
      ('Cardiac arrhythmia & conduction disorders', 3, '308 309 310'), ('Major small & large bowel procedures', 3, '329 330 331'),
      ('GI hemorrhage', 3, '377 378 379'), ('Laparoscopic cholecystectomy w/o CDE', 3, '417 418 419'), ('Cirrhosis & alcoholic hepatitis', 3, '432 433 434'),
      ('Disorders of pancreas except malignancy', 3, '438 439 440'), ('Major hip & knee joint replacement', 'mcc', '469 470'),
      ('Hip replacement w principal diagnosis of hip fracture', 'mcc', '521 522'), ('Cellulitis', 'mcc', '602 603'), ('Diabetes', 3, '637 638 639'),
      ('Renal failure', 3, '682 683 684'), ('Kidney & urinary tract infections', 'mcc', '689 690'), ('Cesarean section w/o sterilization', 3, '786 787 788'),
      ('Vaginal delivery w/o sterilization/D&C', 3, '805 806 807'), ('Septicemia w/o MV > 96 hours', 'mcc', '871 872'),
      ('Alcohol, drug abuse or dependence w/o rehabilitation', 'mcc', '896 897'), ('Poisoning & toxic effects of drugs', 'mcc', '917 918'),
      ('Signs & symptoms', 'mcc', '947 948')]
DRG_FAMILIES = {}
for _name, _kind, _codes in _F:
    _c = _codes.split()
    _tiers = {3: ('MCC', 'CC', 'none'), 'mcc': ('MCC', 'none'), 'ccmcc': ('CCMCC', 'none')}[_kind]
    for _d, _t in zip(_c, _tiers): DRG_FAMILIES[_d] = (_name, _kind, _t, tuple(_c))
INFECTION_PDX = ('A', 'J1', 'J2', 'N39.0', 'N10', 'N30', 'L03', 'K35', 'K57', 'K65', 'K81', 'T81.4', 'J44.0')
RESP_PDX = ('J', 'I50', 'I11.0', 'I13')
RULES = ('hyponatremia', 'hypernatremia', 'aki', 'blood_loss_anemia', 'sepsis', 'respiratory_failure', 'acute_heart_failure', 'ckd_stage',
         'malnutrition', 'morbid_obesity', 'ehr_documented')
DEFAULT_STEP = {'CC': 0.25, 'MCC': 0.60}

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'CDI OPPORTUNITIES: inpatient stays where labs/vitals/EHR diagnoses support a CC/MCC secondary diagnosis missing from the '
                  'claim (hyponatremia, AKI, sepsis, acute respiratory failure, acute-on-chronic HF, malnutrition, CKD 4/5...), coded vs expected '
                  'MS-DRG severity tier, estimated dollar impact, query worklist with evidence record ids.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'period_months': 24, 'rules': [], 'drgs': [], 'products': [], 'only_tier_change': False, 'tier_step': {'CC': 0.25, 'MCC': 0.6},
                           'top': 50},
            'meaning': 'Per inpatient DRG claim: EHR clinical indicators in the stay window (KDIGO AKI, sodium, hemoglobin fall, SIRS/sepsis, SpO2, '
                       'NT-proBNP with HF, chronic eGFR, ASPEN weight loss, BMI, EHR-documented CC/MCC conditions) mapped to the CC/MCC code a CDI '
                       'specialist would query (CMS MS-DRG v41 Tables 6I/6J subset); coded vs expected MS-DRG tier within the family; dollar impact '
                       '= observed mean allowed of the target DRG minus coded DRG, else stated relative-weight step. Query prompts, never codes.'}
NATIVE = ('count', 'value')


def schema(obj, string, array):
    return obj({'period_months': {'type': 'integer', 'minimum': 1, 'maximum': 120, 'description': 'Discharges in the months before the as-of date'},
                'rules': array(string(RULES), 'Indicator rules; empty = all'),
                'drgs': array({'type': 'string', 'pattern': '^[0-9]{3}$'}, 'MS-DRGs to review; empty = all'),
                'products': array({'type': 'string'}, 'Product lines (payer_claim.product); empty = all'),
                'only_tier_change': {'type': 'boolean', 'description': 'Only stays whose expected MS-DRG tier is higher than coded'},
                'tier_step': {'type': 'object', 'additionalProperties': {'type': 'number'}, 'description': 'Fallback dollar step ratio by target tier {CC, MCC}'},
                'top': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'description': 'Worklist rows returned'}})


def validate(p):
    allowed = {'period_months', 'rules', 'drgs', 'products', 'only_tier_change', 'tier_step', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('cdi_opportunity parameters: ' + ', '.join(sorted(allowed)))
    out = {'period_months': p.get('period_months', 24), 'rules': sorted(set(p.get('rules') or [])), 'drgs': sorted({str(d).zfill(3) for d in p.get('drgs') or []}),
           'products': sorted(set(p.get('products') or [])), 'only_tier_change': p.get('only_tier_change', False),
           'tier_step': {**DEFAULT_STEP, **(p.get('tier_step') or {})}, 'top': p.get('top', 50)}
    if type(out['period_months']) is not int or not 1 <= out['period_months'] <= 120: raise ValueError('period_months: 1..120')
    if any(r not in RULES for r in out['rules']): raise ValueError(f'rules: subset of {RULES}')
    if any(not re.fullmatch(r'\d{3}', d) for d in out['drgs']): raise ValueError('drgs: three-digit MS-DRG codes')
    if type(out['only_tier_change']) is not bool: raise ValueError('only_tier_change: true|false')
    if set(out['tier_step']) - {'CC', 'MCC'} or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 5 for v in out['tier_step'].values()):
        raise ValueError('tier_step: {CC, MCC}: ratio 0..5')
    if type(out['top']) is not int or not 1 <= out['top'] <= 1000: raise ValueError('top: 1..1000')
    return out


# ---- data -------------------------------------------------------------------------------------------------------------
def _codes(text):
    try:
        v = json.loads(text) if text else []
    except (TypeError, ValueError):
        v = re.split(r'[,;| ]+', str(text))
    return {str(c).strip().upper() for c in v if c}


def claims(db, start, end, keep, p):
    cols = rc.columns(db, 'payer_claim')
    rows = {}
    q = ('SELECT payer_claim_id, patient_id, product, admission_date, discharge_date, service_from, service_to, drg, principal_diagnosis, '
         'diagnosis_codes, billing_npi, allowed_cents, paid_cents, record_id FROM payer_claim WHERE drg IS NOT NULL AND drg <> ""'
         + (' AND counts_in_metrics = 1' if 'counts_in_metrics' in cols else ''))
    for r in db.execute(q):
        cid, pid, product, adm, dis, sf, st, drg, pdx, dx, npi, allowed, paid, rid = r
        adm, dis = (adm or sf or '')[:10], (dis or st or adm or sf or '')[:10]
        if not adm or not (start <= dis <= end): continue
        if keep is not None and pid not in keep: continue
        drg = str(drg).zfill(3)
        if p['drgs'] and drg not in p['drgs']: continue
        if p['products'] and product not in p['products']: continue
        rows[cid] = {'claim_id': cid, 'patient_id': pid, 'product': product, 'admission': adm, 'discharge': dis, 'drg': drg,
                     'principal': (pdx or '').upper(), 'codes': _codes(dx) | ({(pdx or '').upper()} if pdx else set()), 'billing_npi': npi,
                     'allowed': (allowed or 0) / 100, 'paid': (paid or 0) / 100, 'record_id': rid}
    if rows and 'payer_claim_diagnosis' in rc.tables(db):
        for cid, icd in db.execute('SELECT payer_claim_id, icd10 FROM payer_claim_diagnosis'):
            if cid in rows and icd: rows[cid]['codes'].add(icd.upper())
    return list(rows.values())


def _measures(db, pid, concepts, lo, hi):
    out = defaultdict(list)
    q = (f"SELECT concept, event_date, value_canonical, record_id FROM measurement WHERE concept IN ({','.join('?' * len(concepts))}) "
         "AND patient_id = ? AND event_date BETWEEN ? AND ? AND excluded_reason IS NULL AND COALESCE(result_current, 1) = 1 "
         "AND value_canonical IS NOT NULL ORDER BY event_date")
    for concept, d, v, rid in db.execute(q, (*concepts, pid, lo, hi)):
        try: out[concept].append((str(d)[:19], float(v), rid))
        except (TypeError, ValueError): continue
    return out


def _conditions(db, pid, lo, hi):
    return [(c, (icd or '').upper(), str(d)[:10], rid) for c, icd, d, rid in db.execute(
        'SELECT concept, icd10, COALESCE(diagnosis_date, onset, event_date), record_id FROM condition WHERE patient_id = ? '
        'AND COALESCE(diagnosis_date, onset, event_date) <= ?', (pid, hi))]


def _days(a, b):
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def _has(codes, *prefixes):
    return any(c.startswith(pre) for c in codes for pre in prefixes)


# ---- indicator rules ------------------------------------------------------------------------------------------------
def indicators(c, m, conds, notes_ef):
    """[(rule, icd10, evidence text, record ids)] supported by the stay's EHR data and not already coded."""
    out, codes, pdx, adm, dis = [], c['codes'], c['principal'], c['admission'], c['discharge']
    na = [x for x in m.get('sodium', []) if adm_lo(adm) <= x[0][:10] <= dis]
    low = [x for x in na if x[1] < 135]
    if (any(x[1] < 130 for x in na) or len(low) >= 2) and not _has(codes, 'E87.1'):
        mn = min(na, key=lambda x: x[1]); out.append(('hyponatremia', 'E87.1', f'sodium {mn[1]:g} mmol/L on {mn[0][:10]}', [x[2] for x in low][:4]))
    hi = [x for x in na if x[1] > 150]
    if hi and not _has(codes, 'E87.0'):
        out.append(('hypernatremia', 'E87.0', f'sodium {max(x[1] for x in hi):g} mmol/L', [x[2] for x in hi][:4]))
    cr = m.get('creatinine', [])
    if cr and not _has(codes, 'N17'):
        base = [x for x in cr if _days(x[0], adm) <= 7 and x[0][:10] <= adm] or [x for x in cr if adm_lo(adm) <= x[0][:10] <= dis][:1]
        stay = [x for x in cr if adm_lo(adm) <= x[0][:10] <= dis]
        hit = None
        for i, a in enumerate(stay):
            for b in stay[i + 1:]:
                if 0 <= _days(a[0], b[0]) <= 2 and b[1] - a[1] >= 0.3: hit = (a, b); break
            if hit: break
        if not hit and base and stay:
            lo_ = min(base, key=lambda x: x[1]); hi_ = max(stay, key=lambda x: x[1])
            if lo_[1] > 0 and hi_[1] >= 1.5 * lo_[1] and hi_[0] >= lo_[0]: hit = (lo_, hi_)
        if hit:
            out.append(('aki', 'N17.9', f'creatinine {hit[0][1]:g} -> {hit[1][1]:g} mg/dL ({hit[0][0][:10]} to {hit[1][0][:10]}; KDIGO)', [hit[0][2], hit[1][2]]))
    hb = [x for x in m.get('hemoglobin', []) if adm_lo(adm) <= x[0][:10] <= dis]
    if len(hb) >= 2 and not _has(codes, 'D62'):
        top = max(hb[:-1], key=lambda x: x[1]); after = [x for x in hb if x[0] > top[0]]
        if after:
            lo_ = min(after, key=lambda x: x[1])
            if top[1] - lo_[1] >= 2: out.append(('blood_loss_anemia', 'D62', f'hemoglobin {top[1]:g} -> {lo_[1]:g} g/dL (query for documented bleeding)', [top[2], lo_[2]]))
    if not _has(codes, 'A40', 'A41', 'R65.2'):
        sep = [x for x in conds if (x[0] == 'sepsis' or x[1].startswith(('A41', 'R65.2'))) and adm_lo(adm) <= x[2] <= dis]
        hr = [x for x in m.get('heart_rate', []) if adm_lo(adm) <= x[0][:10] <= dis and x[1] > 90]
        wbc = [x for x in m.get('wbc', []) if adm_lo(adm) <= x[0][:10] <= dis and (x[1] > 12 or x[1] < 4)]
        if sep:
            out.append(('sepsis', 'A41.9', f'EHR sepsis diagnosis {sep[0][1] or sep[0][0]} on {sep[0][2]}', [x[3] for x in sep][:3]))
        elif hr and wbc and pdx.startswith(INFECTION_PDX):
            out.append(('sepsis', 'A41.9', f'SIRS: heart rate {max(x[1] for x in hr):g}, WBC {wbc[0][1]:g} with infection principal {pdx}', [hr[0][2], wbc[0][2]]))
    spo2 = [x for x in m.get('spo2', []) if adm_lo(adm) <= x[0][:10] <= dis and x[1] < 91]
    if spo2 and pdx.startswith(RESP_PDX) and not _has(codes, 'J96'):
        mn = min(spo2, key=lambda x: x[1]); out.append(('respiratory_failure', 'J96.01', f'SpO2 {mn[1]:g}% on {mn[0][:10]} with principal {pdx}', [x[2] for x in spo2][:3]))
    hf = _has(codes, 'I50', 'I11.0', 'I13.0', 'I13.2') or any(x[0] in ('heart_failure', 'heart_failure_reduced_ef') or x[1].startswith('I50') for x in conds)
    acute = _has(codes, 'I50.21', 'I50.23', 'I50.31', 'I50.33', 'I50.41', 'I50.43', 'I50.1', 'I50.81')
    bnp = [x for x in m.get('nt_probnp', []) if adm_lo(adm) <= x[0][:10] <= dis and x[1] >= 900]
    if hf and bnp and not acute:
        ef = notes_ef
        reduced = any(x[0] == 'heart_failure_reduced_ef' for x in conds) or (ef is not None and ef <= 40)
        code = 'I50.23' if reduced else 'I50.33'
        out.append(('acute_heart_failure', code, f'NT-proBNP {max(x[1] for x in bnp):g} pg/mL with heart failure; claim lacks acuity'
                    + (f'; EF {ef:g}%' if ef is not None else ''), [x[2] for x in bnp][:3]))
    egfr = m.get('egfr', [])
    if egfr and not _has(codes, 'N18.4', 'N18.5', 'N18.6'):
        stay = [x for x in egfr if adm_lo(adm) <= x[0][:10] <= dis]
        for cut, code in ((15, 'N18.5'), (30, 'N18.4')):
            now = [x for x in stay if x[1] < cut]; before = [x for x in egfr if x[1] < cut and _days(x[0], adm) >= 90]
            if now and before:
                out.append(('ckd_stage', code, f'eGFR {now[0][1]:g} in stay and {before[-1][1]:g} on {before[-1][0][:10]} (>= 90 days)', [now[0][2], before[-1][2]]))
                break
    wt = m.get('weight', [])
    if wt and not _has(codes, 'E40', 'E41', 'E42', 'E43', 'E44', 'E46') and not pdx.startswith(('I50', 'I11.0', 'I13')):  # HF diuresis is fluid, not nutrition
        last = [x for x in wt if x[0][:10] <= dis]
        if last:
            cur = last[-1]
            sev = mod = None
            for x in last[:-1]:
                d = _days(x[0], cur[0]); loss = (x[1] - cur[1]) / x[1] if x[1] else 0
                if d < 7: continue  # day-to-day weights are fluid shifts
                if (d <= 30 and loss > 0.05) or (d <= 90 and loss > 0.075): sev = (x, loss)
                elif d <= 90 and loss >= 0.05 and not sev: mod = (x, loss)
            hit = sev or mod
            if hit:
                code = 'E43' if sev else 'E44.0'
                out.append(('malnutrition', code, f'weight {hit[0][1]:g} -> {cur[1]:g} kg ({hit[1]:.1%} loss in {_days(hit[0][0], cur[0])} days; ASPEN)', [hit[0][2], cur[2]]))
    bmi = [x for x in m.get('bmi', []) if adm_lo(adm) <= x[0][:10] <= dis and x[1] >= 40]
    if bmi and not _has(codes, 'E66.01', 'Z68.4'):
        out.append(('morbid_obesity', 'E66.01', f'BMI {bmi[0][1]:g}', [bmi[0][2]]))
    found = {o[1] for o in out}
    for concept, icd, d, rid in conds:
        if icd in CC_MCC and icd not in codes and icd not in found and adm_lo(adm) <= d <= dis:
            out.append(('ehr_documented', icd, f'EHR condition {icd} ({concept}) recorded {d}', [rid])); found.add(icd)
    pcat = pdx[:3]
    return [o for o in out if o[1][:3] != pcat]  # CC exclusion approximation: same category as the principal


def adm_lo(adm):
    return (date.fromisoformat(adm[:10]) - timedelta(days=1)).isoformat()


def _ef(db, pid, hi):
    try:
        rows = db.execute("SELECT value FROM note_fact WHERE patient_id = ? AND fact_type = 'ejection_fraction' AND event_date <= ? "
                          "AND COALESCE(negated, 0) = 0 ORDER BY event_date DESC LIMIT 1", (pid, hi)).fetchall()
        return float(re.findall(r'\d+(?:\.\d+)?', str(rows[0][0]))[0]) if rows else None
    except (IndexError, ValueError, TypeError, sqlite3.Error):  # note facts are optional evidence
        return None


def coded_tier(codes, pdx):
    t = 'none'
    for c in codes:
        if c == pdx or c[:3] == pdx[:3]: continue
        lvl = CC_MCC.get(c, (None,))[0]
        if lvl == 'MCC': return 'MCC'
        if lvl == 'CC': t = 'CC'
    return t


def family_tier(drg, sec_tier):
    """(family, billed-tier, expected DRG for sec_tier) from the MS-DRG family; None outside the curated families."""
    f = DRG_FAMILIES.get(drg)
    if not f: return None
    name, kind, billed, members = f
    if kind == 3: target = {'MCC': members[0], 'CC': members[1], 'none': members[2]}[sec_tier]
    elif kind == 'mcc': target = members[0] if sec_tier == 'MCC' else members[1]
    else: target = members[0] if sec_tier in ('CC', 'MCC') else members[1]
    return name, billed, target


def run(db, patient_ids, p, as_of):
    start, end = rc.window(as_of, p['period_months'])
    keep = set(patient_ids) if patient_ids is not None else None
    cl = claims(db, start, end, keep, p)
    mean_allowed = defaultdict(list)
    for c in cl: mean_allowed[c['drg']].append(c['allowed'])
    concepts = ('sodium', 'creatinine', 'hemoglobin', 'heart_rate', 'wbc', 'spo2', 'nt_probnp', 'egfr', 'weight', 'bmi')
    rows, rule_counts = [], defaultdict(int)
    for c in cl:
        lo = (date.fromisoformat(c['admission']) - timedelta(days=400)).isoformat()
        m = _measures(db, c['patient_id'], concepts, lo, c['discharge'])
        conds = _conditions(db, c['patient_id'], lo, c['discharge'])
        found = [x for x in indicators(c, m, conds, _ef(db, c['patient_id'], c['discharge'])) if not p['rules'] or x[0] in p['rules']]
        if not found: continue
        coded = coded_tier(c['codes'], c['principal'])
        sup = [CC_MCC.get(x[1], ('CC',))[0] for x in found]
        expected = 'MCC' if 'MCC' in sup or coded == 'MCC' else 'CC' if 'CC' in sup or coded == 'CC' else 'none'
        fam = family_tier(c['drg'], expected)
        target, impact, basis = None, 0.0, 'DRG outside the curated MS-DRG families: tier impact not estimated'
        billed = None
        if fam:
            name, billed, target = fam
            if target != c['drg']:
                cur, tgt = mean_allowed.get(c['drg'], []), mean_allowed.get(target, [])
                if len(cur) >= 3 and len(tgt) >= 3:
                    impact = sum(tgt) / len(tgt) - sum(cur) / len(cur); basis = f'observed mean allowed DRG {target} minus DRG {c["drg"]}'
                else:
                    ref = sum(cur) / len(cur) if cur else c['allowed']
                    impact = ref * p['tier_step']['MCC' if expected == 'MCC' else 'CC']; basis = f'stated step ratio x DRG {c["drg"]} mean allowed (approximate)'
                impact = max(impact, 0.0)
            else:
                basis = 'expected tier already billed (documentation quality / risk-adjustment value only)'
        tier_change = bool(fam and target != c['drg'])
        if p['only_tier_change'] and not tier_change: continue
        for x in found: rule_counts[x[0]] += 1
        rows.append({'claim_id': c['claim_id'], 'patient_id': c['patient_id'], 'product': c['product'], 'admission': c['admission'],
                     'discharge': c['discharge'], 'drg': c['drg'], 'drg_family': fam[0] if fam else None, 'billed_tier': billed,
                     'coded_secondary_tier': coded, 'expected_tier': expected, 'expected_drg': target, 'tier_change': tier_change,
                     'estimated_impact': round(impact, 2), 'impact_basis': basis, 'allowed': round(c['allowed'], 2),
                     'opportunities': [{'rule': x[0], 'icd10': x[1], 'cc_mcc': CC_MCC.get(x[1], ('CC', ''))[0], 'label': CC_MCC.get(x[1], ('', x[1]))[1],
                                        'evidence': x[2], 'record_ids': [r for r in x[3] if r]} for x in found],
                     'record_ids': [c['record_id'], *[r for x in found for r in x[3] if r]][:10]})
    rows.sort(key=lambda r: (-r['estimated_impact'], -len(r['opportunities'])))
    total = sum(r['estimated_impact'] for r in rows)
    assumptions = [f'Synthetic warehouse; inpatient DRG claims discharged {start} to {end} (payer feed, final versions).',
                   f'CC/MCC designations: {SOURCE}; CC exclusions approximated by the principal-diagnosis category only.',
                   'Indicators are query prompts for a CDI specialist; the physician documents and the coder assigns. Nothing here is a code assignment.',
                   'Dollar impact: observed mean allowed of the target MS-DRG minus the billed MS-DRG where both have >= 3 claims, else the billed '
                   f"DRG mean allowed x stated step (CC {p['tier_step']['CC']:.0%}, MCC {p['tier_step']['MCC']:.0%}; approximate, not CMS relative weights).",
                   'Present-on-admission status, procedure-driven DRGs (MCC/CC splits by surgical family) and DRG reassignment by a new principal '
                   'diagnosis are not modelled; albumin and lactate are not received, so malnutrition uses ASPEN weight loss and sepsis uses SIRS + infection.']
    return {'schema': VERSION, 'as_of': str(as_of)[:10], 'parameters': p, 'period': {'start': start, 'end': end}, 'claims_reviewed': len(cl),
            'stays_with_opportunity': len(rows), 'tier_changes': sum(r['tier_change'] for r in rows), 'estimated_impact': round(total, 2),
            'by_rule': dict(sorted(rule_counts.items(), key=lambda kv: -kv[1])), 'rows': rows[:p['top']], 'all_rows': len(rows),
            'assumptions': assumptions,
            'method': ('Inpatient DRG claims x EHR clinical indicators in the stay window (KDIGO AKI creatinine rule, sodium, hemoglobin fall, SIRS / EHR '
                       'sepsis, SpO2 with respiratory/HF principal, NT-proBNP with HF and no acuity code, chronic eGFR < 30/15, ASPEN weight loss, BMI, '
                       'EHR-documented CC/MCC conditions) -> CC/MCC query candidates absent from the claim; expected vs billed MS-DRG tier in the '
                       'curated family table; impact from observed DRG allowed differences or a stated step ratio.')}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def answer(report, plan, question):
    rows = report['rows']
    table = [{'claim_id': r['claim_id'], 'patient_id': r['patient_id'], 'discharge': r['discharge'], 'drg': r['drg'], 'expected_drg': r['expected_drg'],
              'coded_tier': r['coded_secondary_tier'], 'expected_tier': r['expected_tier'], 'estimated_impact': r['estimated_impact'],
              'queries': '; '.join(f"{o['icd10']} {o['cc_mcc']} ({o['evidence']})" for o in r['opportunities']),
              'record_ids': r['record_ids']} for r in rows]
    top = ', '.join(f"{k} {v}" for k, v in list(report['by_rule'].items())[:5])
    value = (f"CDI review of {report['claims_reviewed']} inpatient DRG claims ({report['period']['start']} to {report['period']['end']}): "
             f"{report['all_rows']} stays have EHR indicators for an uncoded CC/MCC, {report['tier_changes']} would move to a higher MS-DRG tier, "
             f"estimated {rc.money(report['estimated_impact'])}" + (f"; most common: {top}" if top else '') + '.')
    return rc.answer(report, plan, value=value, table=table, number=report['all_rows'],
                     patient_ids=sorted({r['patient_id'] for r in rows}), membership_role='selected',
                     definitions=[{'term': 'CC / MCC', 'basis': SOURCE}, {'term': 'AKI', 'basis': 'KDIGO 2012: creatinine rise >= 0.3 mg/dL in 48 h or >= 1.5x baseline'},
                                  {'term': 'malnutrition', 'basis': 'ASPEN/AND consensus weight-loss criteria (White et al., JPEN 2012)'}],
                     sources=[{'record_id': rid, 'claim_id': r['claim_id']} for r in rows[:25] for rid in r['record_ids'][:3]])


# ---- free local-compiler route ------------------------------------------------------------------------------------------
CDI_RX = (r'\bcdi\b|\bclinical documentation (?:improvement|integrity|opportunit\w*)\b|\bdocumentation (?:improvement|opportunit\w*|quer\w*)\b'
          r'|\b(?:cc|mcc)s?\b.{0,40}\b(?:missing|missed|uncoded|not coded|capture|opportunit\w*)\b|\b(?:missing|missed|uncoded|capture)\b.{0,40}\b(?:cc|mcc)s?\b'
          r'|\bdrg (?:severity|optimi[sz]ation|opportunit\w*|under-?coding|downcod\w*)\b|\bunder-?coded (?:stays|admissions|drgs?|inpatient)\b'
          r'|\bseverity (?:capture|of illness capture)\b|\bquery opportunit\w*\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if not re.search(CDI_RX, q): return None
    if rc.knowledge_only(q): return None
    params = {}
    rules = [r for r, rx in (('hyponatremia', r'hyponatr'), ('aki', r'\baki\b|acute kidney'), ('sepsis', r'\bsep(?:sis|tic)\b'),
                             ('respiratory_failure', r'respiratory failure'), ('acute_heart_failure', r'heart failure|\bchf\b'),
                             ('malnutrition', r'malnutrition'), ('blood_loss_anemia', r'blood loss|posthemorrhagic'),
                             ('ckd_stage', r'\bckd\b|chronic kidney')) if re.search(rx, q)]
    if rules: params['rules'] = rules
    d = re.findall(r'\bdrgs? (\d{3})\b', q)
    if d: params['drgs'] = d
    if re.search(r'\bhigher (?:drg|tier)\b|\bmove (?:up|the drg)\b|\bdrg (?:shift|change)\b|\bmcc capture\b', q): params['only_tier_change'] = True
    m = re.search(r'\b(?:last|past) (\d{1,2}) months\b', q)
    if m: params['period_months'] = int(m.group(1))
    elif re.search(r'\b(?:last|past) year\b', q): params['period_months'] = 12
    return plan(OP, params, 'CDI opportunities: EHR-supported CC/MCC missing from inpatient claims, expected vs coded MS-DRG tier', question,
                [f"rules {rules or 'all'}"])


TEMPLATES = ['Which inpatient stays have CDI opportunities?', 'Find admissions where labs support an MCC that was not coded',
             'Where are we missing CCs or MCCs on our DRG claims?', 'CDI query worklist for heart failure admissions',
             'How much DRG severity are we leaving on the table from uncoded MCCs?', 'clinical documentation improvement opportunities last 12 months',
             'Which stays had AKI by creatinine but no AKI code?', 'Show undercoded inpatient stays that would move to a higher DRG']
