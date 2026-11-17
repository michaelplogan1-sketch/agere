"""Answer key for eval/tiered/clinical_v1.json, computed from the raw warehouse tables.

Written by an independent evaluator who did not read the product code, its clinical
knowledge store or earlier question sets. Standard library only. Every function takes an
open sqlite3 connection (open it read-only: file:...?mode=ro) and returns a list of rows:
[[figure]] for a count or rate, [[label, figure], ...] for a breakdown.

Data-handling conventions (applied by every function unless its docstring says otherwise)
- Lab results: table measurement. A result counts when result_current = 1 (superseded
  originals and duplicate interface messages are dropped; the corrected replacement is
  kept), result_status is empty, 'final' or 'corrected' (cancelled, 'not_calculated' and
  billed-test-without-result rows are dropped), excluded_reason is empty (hemolysed
  specimens and tests not performed are dropped), event_date is present and a value is
  present. Labs with a concept use value_canonical; labs without a concept (bicarbonate,
  MCV, transferrin saturation, height and so on) are found by name and use value.
- An eGFR reported as ">60" (value_censor '>') is treated as 60 or above: never below 60.
- Year = calendar year of the result event_date, fill_date or diagnosis date.
- Age = whole years from patient.birth_date to the reference date given in the function.
  Sex = patient.sex_reported.
- Diagnoses: union of condition.icd10 (dated by the first full YYYY-MM-DD date among
  diagnosis_date, onset, event_date, recorded_date), encounter_diagnosis.icd10 (event_date) and
  payer_claim_diagnosis.icd10 (service_from). "Has diagnosis by year end" means any such
  row dated on or before 31 December of the year (an undated condition row counts).
- Medication exposure: table fill. A fill covers fill_date through supply_end inclusive
  (supply_end = fill_date + days_supply - 1). "Active on a date" means some fill of the
  class covers that date. "Overlap" means at least one calendar day covered by every class
  in question within the year. Combination products count for each of their classes.
- Qualifying outpatient visit (eCQM-style denominators): a completed encounter
  (encounter.source_table = 'encounters', status = 'completed') of type office,
  primary_care, telehealth, telephone, specialist_office, specialty_followup or
  pediatric_office in the year.
- Blood pressure: concepts systolic_bp and diastolic_bp from outpatient organizations
  (organization_type not 'hospital'); for a given day the lowest systolic and the lowest
  diastolic of that day form the day's reading (CMS165 rule).
- Rates are shares rounded to 4 decimals; other decimals to 1 decimal.
"""

from collections import defaultdict
from datetime import date, timedelta

LAB_OK = (
    "result_current = 1 AND (result_status IS NULL OR result_status IN ('final','corrected')) "
    "AND excluded_reason IS NULL AND event_date IS NOT NULL"
)

OUTPATIENT_VISIT_TYPES = (
    "office", "primary_care", "telehealth", "telephone",
    "specialist_office", "specialty_followup", "pediatric_office",
)


# ---------------------------------------------------------------- helpers

def _d(s):
    return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def _iso(s):
    """True for a full YYYY-MM-DD date string."""
    return bool(s) and len(s) >= 10 and s[4] == "-" and s[7] == "-" and s[:4].isdigit() and s[5:7].isdigit() and s[8:10].isdigit()


def _bounds(year):
    y = int(year)
    return date(y, 1, 1), date(y, 12, 31)


def _rate(num, den):
    return round(num / den, 4) if den else None


def _age(birth, on):
    if not birth:
        return None
    b = _d(birth)
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


def _patients(db):
    return {pid: (sex, birth) for pid, sex, birth in
            db.execute("SELECT patient_id, sex_reported, birth_date FROM patient")}


def _lab(db, concept=None, names=None, start=None, end=None):
    """Valid lab results as {patient: [(date, value, censor), ...]} sorted by date."""
    if concept:
        sql = ("SELECT patient_id, event_date, value_canonical, value_censor FROM measurement "
               "WHERE concept = ? AND value_canonical IS NOT NULL AND " + LAB_OK)
        params = [concept]
    else:
        marks = ",".join("?" * len(names))
        sql = ("SELECT patient_id, event_date, value, value_censor FROM measurement "
               "WHERE concept IS NULL AND name IN (" + marks + ") AND value IS NOT NULL AND " + LAB_OK)
        params = list(names)
    out = defaultdict(list)
    for pid, ed, v, c in db.execute(sql, params):
        dt = _d(ed)
        if start and dt < start:
            continue
        if end and dt > end:
            continue
        out[pid].append((dt, float(v), c))
    for pid in out:
        out[pid].sort(key=lambda r: r[0])
    return out


def _below(value, censor, threshold):
    """True when a result is strictly below the threshold; '>60' is never below 60."""
    if censor == ">":
        return False
    return value < threshold


def _dx(db, prefixes, end=None, start=None):
    """Patients with a diagnosis code starting with any prefix (dots ignored), dated in range."""
    def match(code):
        if not code:
            return False
        c = code.replace(".", "").upper()
        return any(c.startswith(p.replace(".", "").upper()) for p in prefixes)

    def in_range(ds):
        if ds is None:
            return start is None
        dt = _d(ds)
        if end and dt > end:
            return False
        if start and dt < start:
            return False
        return True

    pats = set()
    q = ("SELECT patient_id, icd10, diagnosis_date, onset, event_date, recorded_date "
         "FROM condition WHERE icd10 IS NOT NULL")
    for pid, code, *cands in db.execute(q):
        ds = next((c for c in cands if _iso(c)), None)
        if match(code) and in_range(ds):
            pats.add(pid)
    for pid, code, ds in db.execute("SELECT patient_id, icd10, event_date FROM encounter_diagnosis"):
        if match(code) and in_range(ds):
            pats.add(pid)
    for pid, code, ds in db.execute("SELECT patient_id, icd10, service_from FROM payer_claim_diagnosis"):
        if match(code) and in_range(ds):
            pats.add(pid)
    return pats


def _visit_patients(db, year):
    s, e = _bounds(year)
    marks = ",".join("?" * len(OUTPATIENT_VISIT_TYPES))
    q = ("SELECT DISTINCT patient_id FROM encounter WHERE source_table = 'encounters' "
         "AND status = 'completed' AND type IN (" + marks + ") AND event_date BETWEEN ? AND ?")
    return {r[0] for r in db.execute(q, list(OUTPATIENT_VISIT_TYPES) + [s.isoformat(), e.isoformat()])}


def _fills(db, classes=None, name_like=None):
    """{patient: [(start, end, drug_name_lower, class), ...]}"""
    q = "SELECT patient_id, fill_date, supply_end, lower(drug_name), drug_class FROM fill WHERE fill_date IS NOT NULL AND supply_end IS NOT NULL"
    params = []
    if classes:
        q += " AND drug_class IN (" + ",".join("?" * len(classes)) + ")"
        params += list(classes)
    out = defaultdict(list)
    for pid, fd, se, name, cls in db.execute(q, params):
        if name_like and not any(n in (name or "") for n in name_like):
            continue
        out[pid].append((_d(fd), _d(se), name or "", cls))
    return out


def _fill_in_year(fills, year):
    s, e = _bounds(year)
    return {pid for pid, rows in fills.items() if any(s <= r[0] <= e for r in rows)}


def _active(rows, day):
    return any(r[0] <= day <= r[1] for r in rows)


def _days_covered(rows, s, e):
    days = set()
    for a, b, *_ in rows:
        a2, b2 = max(a, s), min(b, e)
        while a2 <= b2:
            days.add(a2)
            a2 += timedelta(days=1)
    return days


def _overlap_patients(db, groups, year):
    """Patients with >=1 day in the year covered by every group (each group = list of classes)."""
    s, e = _bounds(year)
    per_group = [_fills(db, classes=g) for g in groups]
    common = set(per_group[0])
    for g in per_group[1:]:
        common &= set(g)
    hits = set()
    for pid in common:
        days = _days_covered(per_group[0][pid], s, e)
        for g in per_group[1:]:
            if not days:
                break
            days &= _days_covered(g[pid], s, e)
        if days:
            hits.add(pid)
    return hits


def _bp_days(db, start, end):
    """{patient: {date: (min_sbp, min_dbp)}} from outpatient organizations."""
    q = ("SELECT m.patient_id, m.event_date, m.concept, m.value_canonical FROM measurement m "
         "LEFT JOIN organization o ON o.organization_id = m.organization_id "
         "WHERE m.concept IN ('systolic_bp','diastolic_bp') AND m.value_canonical IS NOT NULL "
         "AND COALESCE(o.organization_type,'') <> 'hospital' AND m.event_date BETWEEN ? AND ? AND "
         + LAB_OK.replace("result_current", "m.result_current").replace("result_status", "m.result_status")
         .replace("excluded_reason", "m.excluded_reason").replace("event_date IS", "m.event_date IS"))
    sb = defaultdict(lambda: defaultdict(list))
    db_ = defaultdict(lambda: defaultdict(list))
    for pid, ed, c, v in db.execute(q, [start.isoformat(), end.isoformat()]):
        (sb if c == "systolic_bp" else db_)[pid][_d(ed)].append(v)
    out = defaultdict(dict)
    for pid in sb:
        for day, vals in sb[pid].items():
            if day in db_[pid]:
                out[pid][day] = (min(vals), min(db_[pid][day]))
    return out


def _last_bp(db, year):
    s, e = _bounds(year)
    days = _bp_days(db, s, e)
    return {pid: d[max(d)] for pid, d in days.items() if d}


PREGNANCY = ["O", "Z33.1", "Z34", "Z3A"]
DIABETES_ECQM = ["E10", "E11", "E13"]
DIABETES_ANY = ["E08", "E09", "E10", "E11", "E13"]
ASCVD = ["I20", "I21", "I22", "I23", "I24", "I25", "I63", "I69.3", "I70", "G45", "Z95.1", "Z95.5", "Z98.61"]
HYPERTENSION = ["I10"]
STATIN = ["statin"]
RAS = ["ace_inhibitor", "arb", "arni"]
HFREF = ["I50.2"]
HF_ANY = ["I50", "I11.0", "I13.0", "I13.2"]


# ---------------------------------------------------------------- kidney

def ref_ckd_egfr_sustained(db, year):
    """Patients with two eGFR results < 60 at least 90 days apart within the year (KDIGO chronicity)."""
    s, e = _bounds(year)
    n = 0
    for pid, rows in _lab(db, "egfr", start=s, end=e).items():
        low = [r[0] for r in rows if _below(r[1], r[2], 60)]
        if low and (max(low) - min(low)).days >= 90:
            n += 1
    return [[n]]


CKD_CODES = ["N18", "E08.22", "E09.22", "E10.22", "E11.22", "E13.22", "I12", "I13", "Z99.2", "Z94.0", "Z49"]


def ref_ckd_lab_uncoded(db, year):
    """Patients meeting the KDIGO lab definition in the year (two eGFR < 60 at least 90 days apart) with no
    chronic kidney disease code on or before year end (N18.x, diabetic CKD E08-E13 .22, hypertensive CKD
    I12/I13, dialysis Z99.2/Z49, kidney transplant Z94.0)."""
    s, e = _bounds(year)
    coded = _dx(db, CKD_CODES, end=e)
    n = 0
    for pid, rows in _lab(db, "egfr", start=s, end=e).items():
        low = [r[0] for r in rows if _below(r[1], r[2], 60)]
        if low and (max(low) - min(low)).days >= 90 and pid not in coded:
            n += 1
    return [[n]]


def ref_egfr_category_g3_g5(db, year):
    """Patients by KDIGO GFR category of their last eGFR in the year (G3a..G5 only)."""
    s, e = _bounds(year)
    counts = {"G3a": 0, "G3b": 0, "G4": 0, "G5": 0}
    for pid, rows in _lab(db, "egfr", start=s, end=e).items():
        last = max(rows, key=lambda r: r[0])
        same = [r for r in rows if r[0] == last[0]]
        v, c = min(((r[1], r[2]) for r in same), key=lambda t: t[0])
        if not _below(v, c, 60):
            continue
        if v >= 45:
            counts["G3a"] += 1
        elif v >= 30:
            counts["G3b"] += 1
        elif v >= 15:
            counts["G4"] += 1
        else:
            counts["G5"] += 1
    return [[k, v] for k, v in counts.items()]


def ref_albuminuria_category(db, year):
    """Patients by KDIGO albuminuria category of their last UACR in the year (A1 <30, A2 30-300, A3 >300 mg/g)."""
    s, e = _bounds(year)
    counts = {"A1": 0, "A2": 0, "A3": 0}
    for pid, rows in _lab(db, "uacr", start=s, end=e).items():
        last_day = max(r[0] for r in rows)
        v = max(r[1] for r in rows if r[0] == last_day)
        if v < 30:
            counts["A1"] += 1
        elif v <= 300:
            counts["A2"] += 1
        else:
            counts["A3"] += 1
    return [[k, v] for k, v in counts.items()]


def ref_metformin_egfr_below_30(db, year):
    """Patients with an eGFR < 30 in the year on a day a metformin fill was active (FDA contraindication)."""
    s, e = _bounds(year)
    fills = _fills(db, classes=["metformin"])
    n = 0
    for pid, rows in _lab(db, "egfr", start=s, end=e).items():
        if pid not in fills:
            continue
        if any(_below(v, c, 30) and _active(fills[pid], d) for d, v, c in rows):
            n += 1
    return [[n]]



def ref_kidney_health_evaluation(db, year):
    """CMS951-style: diabetes (E10/E11/E13 by year end), age 18-85 at year end, outpatient visit in
    the year, no CKD stage 5 / ESRD (N18.5, N18.6, Z99.2) by year end. Share with both an eGFR and a
    UACR (or a quantitative urine albumin with a urine creatinine on the same day) in the year."""
    s, e = _bounds(year)
    pts = _patients(db)
    den = (_dx(db, DIABETES_ECQM, end=e) & _visit_patients(db, year)) - _dx(db, ["N18.5", "N18.6", "Z99.2"], end=e)
    den = {p for p in den if p in pts and (a := _age(pts[p][1], e)) is not None and 18 <= a <= 85}
    egfr = set(_lab(db, "egfr", start=s, end=e))
    uacr = set(_lab(db, "uacr", start=s, end=e))
    alb = _lab(db, names=["Urine albumin"], start=s, end=e)
    ucr = _lab(db, names=["Urine creatinine"], start=s, end=e)
    for pid in alb:
        if pid in ucr and {r[0] for r in alb[pid]} & {r[0] for r in ucr[pid]}:
            uacr.add(pid)
    num = sum(1 for p in den if p in egfr and p in uacr)
    return [[_rate(num, len(den))]]


def ref_ras_in_diabetic_albuminuria(db, year):
    """Patients with diabetes (E10/E11/E13 by year end) and a UACR >= 30 mg/g in the year: share with an
    ACE inhibitor, ARB or ARNI fill in the year."""
    s, e = _bounds(year)
    dm = _dx(db, DIABETES_ECQM, end=e)
    den = {p for p, rows in _lab(db, "uacr", start=s, end=e).items() if p in dm and any(r[1] >= 30 for r in rows)}
    ras = _fill_in_year(_fills(db, classes=RAS), year)
    return [[_rate(len(den & ras), len(den))]]


def ref_sglt2_in_t2d_ckd(db, year):
    """Type 2 diabetes (E11 by year end) with CKD by labs in the year (last eGFR 20-59, or last eGFR >= 20
    with any UACR >= 30 mg/g): share with an SGLT2 inhibitor fill in the year (KDIGO 2022 / ADA)."""
    s, e = _bounds(year)
    t2 = _dx(db, ["E11"], end=e)
    egfr = _lab(db, "egfr", start=s, end=e)
    uacr = _lab(db, "uacr", start=s, end=e)
    den = set()
    for pid in t2:
        if pid not in egfr:
            continue
        last_day = max(r[0] for r in egfr[pid])
        v, c = min(((r[1], r[2]) for r in egfr[pid] if r[0] == last_day), key=lambda t: t[0])
        if v < 20 and c != ">":
            continue
        ckd = _below(v, c, 60) or any(r[1] >= 30 for r in uacr.get(pid, []))
        if ckd:
            den.add(pid)
    sg = _fill_in_year(_fills(db, classes=["sglt2_inhibitor"]), year)
    return [[_rate(len(den & sg), len(den))]]


# ---------------------------------------------------------------- diabetes

def ref_a1c_poor_control(db, year):
    """CMS122-style: diabetes (E10/E11/E13 by year end), age 18-75 at year end, outpatient visit in the
    year. Share whose most recent HbA1c in the year is > 9.0% or who had no HbA1c result (inverse measure)."""
    s, e = _bounds(year)
    pts = _patients(db)
    den = _dx(db, DIABETES_ECQM, end=e) & _visit_patients(db, year)
    den = {p for p in den if p in pts and (a := _age(pts[p][1], e)) is not None and 18 <= a <= 75}
    a1c = _lab(db, "hba1c", start=s, end=e)
    num = 0
    for p in den:
        rows = a1c.get(p)
        if not rows:
            num += 1
            continue
        last_day = max(r[0] for r in rows)
        v = max(r[1] for r in rows if r[0] == last_day)
        if v > 9.0:
            num += 1
    return [[_rate(num, len(den))]]


def ref_undiagnosed_diabetes_a1c(db, year):
    """Patients with an HbA1c >= 6.5% in the year and no diabetes code (E08-E13) on or before year end."""
    s, e = _bounds(year)
    dm = _dx(db, DIABETES_ANY, end=e)
    n = sum(1 for p, rows in _lab(db, "hba1c", start=s, end=e).items()
            if p not in dm and any(r[1] >= 6.5 for r in rows))
    return [[n]]


def _prediabetes(db, year, lo):
    s, e = _bounds(year)
    dm = _dx(db, DIABETES_ANY, end=e)
    n = 0
    for p, rows in _lab(db, "hba1c", start=s, end=e).items():
        if p in dm:
            continue
        last_day = max(r[0] for r in rows)
        v = max(r[1] for r in rows if r[0] == last_day)
        if lo <= v < 6.5:
            n += 1
    return n


def ref_prediabetes_a1c_ada(db, year):
    """Patients without a diabetes code whose last HbA1c in the year is 5.7-6.4% (ADA)."""
    return [[_prediabetes(db, year, 5.7)]]


def ref_prediabetes_a1c_iec(db, year):
    """Patients without a diabetes code whose last HbA1c in the year is 6.0-6.4% (International Expert
    Committee 2009 / NICE)."""
    return [[_prediabetes(db, year, 6.0)]]



def ref_statin_in_diabetes(db, year):
    """Patients with diabetes (E10/E11/E13 by year end) aged 40-75 at year end with an outpatient visit in
    the year: share with a statin fill in the year."""
    s, e = _bounds(year)
    pts = _patients(db)
    den = _dx(db, DIABETES_ECQM, end=e) & _visit_patients(db, year)
    den = {p for p in den if p in pts and (a := _age(pts[p][1], e)) is not None and 40 <= a <= 75}
    st = _fill_in_year(_fills(db, classes=STATIN), year)
    return [[_rate(len(den & st), len(den))]]


# ---------------------------------------------------------------- cardiovascular

def ref_bp_control(db, year):
    """CMS165-style: essential hypertension (I10) diagnosed on or before 30 June of the year, age 18-85 at
    year end, outpatient visit in the year. Share whose most recent outpatient BP day in the year has
    lowest systolic < 140 and lowest diastolic < 90; no reading counts as not controlled. Measure
    exclusions not applied."""
    s, e = _bounds(year)
    pts = _patients(db)
    den = _dx(db, HYPERTENSION, end=date(int(year), 6, 30)) & _visit_patients(db, year)
    den = {p for p in den if p in pts and (a := _age(pts[p][1], e)) is not None and 18 <= a <= 85}
    last = _last_bp(db, year)
    num = sum(1 for p in den if p in last and last[p][0] < 140 and last[p][1] < 90)
    return [[_rate(num, len(den))]]


def _htn_range(db, year, sbp, dbp):
    s, e = _bounds(year)
    pts = _patients(db)
    last = _last_bp(db, year)
    den = num = 0
    for p, (sv, dv) in last.items():
        if p not in pts:
            continue
        a = _age(pts[p][1], e)
        if a is None or a < 18:
            continue
        den += 1
        if sv >= sbp or dv >= dbp:
            num += 1
    return [[_rate(num, den)]]


def ref_htn_range_acc_aha(db, year):
    """Adults with an outpatient BP in the year: share whose last BP day is >= 130 systolic or >= 80
    diastolic (ACC/AHA 2017 stage 1 or higher)."""
    return _htn_range(db, year, 130, 80)


def ref_htn_range_esc(db, year):
    """Same population: share whose last BP day is >= 140 systolic or >= 90 diastolic (ESC/ESH)."""
    return _htn_range(db, year, 140, 90)



def _ascvd_den(db, year, lo, hi):
    s, e = _bounds(year)
    pts = _patients(db)
    den = _dx(db, ASCVD, end=e) & _visit_patients(db, year)
    return {p for p in den if p in pts and (a := _age(pts[p][1], e)) is not None and lo <= a <= hi}


def ref_statin_ascvd(db, year):
    """CMS347 population 1 style: clinical ASCVD by year end, age >= 21, outpatient visit in the year:
    share with a statin fill in the year."""
    den = _ascvd_den(db, year, 21, 200)
    st = _fill_in_year(_fills(db, classes=STATIN), year)
    return [[_rate(len(den & st), len(den))]]


HIGH_INTENSITY = [("atorvastatin", (40, 80)), ("rosuvastatin", (20, 40))]


def _strength(name):
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*mg", name)
    return float(m.group(1)) if m else None


def ref_high_intensity_statin_ascvd(db, year):
    """Clinical ASCVD, age 21-75 at year end, outpatient visit, with a statin fill in the year: share
    whose last statin fill in the year is high intensity (atorvastatin 40-80 mg, rosuvastatin 20-40 mg;
    ACC/AHA 2018). Fills without a stated strength are not high intensity."""
    s, e = _bounds(year)
    den = _ascvd_den(db, year, 21, 75)
    fills = _fills(db, classes=STATIN)
    d = n = 0
    for p in den:
        rows = [r for r in fills.get(p, []) if s <= r[0] <= e]
        if not rows:
            continue
        d += 1
        last = max(rows, key=lambda r: (r[0], r[2]))
        mg = _strength(last[2])
        if mg is not None and any(drug in last[2] and lo <= mg <= hi for drug, (lo, hi) in HIGH_INTENSITY):
            n += 1
    return [[_rate(n, d)]]


def _ldl_goal(db, year, goal):
    s, e = _bounds(year)
    asc = _dx(db, ASCVD, end=e)
    den = num = 0
    for p, rows in _lab(db, "ldl", start=s, end=e).items():
        if p not in asc:
            continue
        den += 1
        last_day = max(r[0] for r in rows)
        v = min(r[1] for r in rows if r[0] == last_day)
        if v < goal:
            num += 1
    return [[_rate(num, den)]]


def ref_ldl_goal_ascvd_acc_aha(db, year):
    """Patients with clinical ASCVD by year end and an LDL-C in the year: share whose last LDL-C is < 70 mg/dL
    (2018 AHA/ACC threshold)."""
    return _ldl_goal(db, year, 70)


def ref_ldl_goal_ascvd_esc(db, year):
    """Same population: share whose last LDL-C is < 55 mg/dL (2019 ESC/EAS very-high-risk goal)."""
    return _ldl_goal(db, year, 55)


def ref_ldl190_no_statin(db, year):
    """Patients aged 20-75 at year end with any LDL-C >= 190 mg/dL in the year and no statin fill in the
    year (ACC/AHA 2018 severe primary hypercholesterolaemia)."""
    s, e = _bounds(year)
    pts = _patients(db)
    st = _fill_in_year(_fills(db, classes=STATIN), year)
    n = 0
    for p, rows in _lab(db, "ldl", start=s, end=e).items():
        a = _age(pts[p][1], e) if p in pts else None
        if a is None or not 20 <= a <= 75:
            continue
        if any(r[1] >= 190 for r in rows) and p not in st:
            n += 1
    return [[n]]


def ref_low_hdl_by_sex(db, year):
    """Patients whose last HDL-C in the year is low by sex: < 40 mg/dL men, < 50 mg/dL women (NCEP ATP III)."""
    s, e = _bounds(year)
    pts = _patients(db)
    n = 0
    for p, rows in _lab(db, "hdl", start=s, end=e).items():
        if p not in pts:
            continue
        last_day = max(r[0] for r in rows)
        v = min(r[1] for r in rows if r[0] == last_day)
        sex = pts[p][0]
        if (sex == "M" and v < 40) or (sex == "F" and v < 50):
            n += 1
    return [[n]]


def _af_oac_gap(db, year, sex_point):
    s, e = _bounds(year)
    pts = _patients(db)
    af = _dx(db, ["I48"], end=e)
    hf = _dx(db, HF_ANY, end=e)
    htn = _dx(db, ["I10", "I11", "I12", "I13", "I15"], end=e)
    dm = _dx(db, DIABETES_ANY, end=e)
    stroke = _dx(db, ["I63", "I64", "G45", "I69.3", "Z86.73"], end=e)
    vasc = _dx(db, ["I21", "I22", "I25.2", "I70", "I73.9", "Z95.1", "Z95.5"], end=e)
    oac = _fill_in_year(_fills(db, classes=["anticoagulant"]), year)
    n = 0
    for p in af & _visit_patients(db, year):
        if p not in pts:
            continue
        sex, birth = pts[p]
        a = _age(birth, e)
        if a is None or a < 18:
            continue
        score = (p in hf) + (p in htn) + (p in dm) + 2 * (p in stroke) + (p in vasc)
        score += 2 if a >= 75 else (1 if a >= 65 else 0)
        if sex_point:
            score += 1 if sex == "F" else 0
            need = 3 if sex == "F" else 2
        else:
            need = 2
        if score >= need and p not in oac:
            n += 1
    return n


def ref_af_oac_gap_cha2ds2_vasc(db, year):
    """Adults with atrial fibrillation (I48) by year end and an outpatient visit in the year whose
    CHA2DS2-VASc is >= 2 (men) or >= 3 (women) and who had no oral anticoagulant fill in the year
    (ACC/AHA/ACCP/HRS 2023)."""
    return [[_af_oac_gap(db, year, True)]]



def _latest_before(rows, day):
    prior = [r for r in rows if r[0] <= day]
    return max(prior, key=lambda r: r[0]) if prior else None




# ---------------------------------------------------------------- heart failure

def ref_ntprobnp_age_rule_in(db, year):
    """Patients with an NT-proBNP in the year above the age-specific acute heart failure rule-in cut-off
    (> 450 pg/mL under 50, > 900 at 50-75, > 1800 over 75; age at the test)."""
    s, e = _bounds(year)
    pts = _patients(db)
    n = 0
    for p, rows in _lab(db, "nt_probnp", start=s, end=e).items():
        if p not in pts:
            continue
        for d, v, c in rows:
            a = _age(pts[p][1], d)
            if a is None:
                continue
            cut = 450 if a < 50 else (900 if a <= 75 else 1800)
            if v > cut:
                n += 1
                break
    return [[n]]



def ref_hfref_non_evidence_bb(db, year):
    """Patients with HFrEF (I50.2x by year end) whose beta-blocker fills in the year are all agents without
    HFrEF mortality evidence (no carvedilol, metoprolol succinate or bisoprolol fill in the year)."""
    s, e = _bounds(year)
    hf = _dx(db, HFREF, end=e)
    bb = _fill_in_year(_fills(db, classes=["beta_blocker"]), year)
    ebb = _fill_in_year(_fills(db, classes=["hf_evidence_based_beta_blocker"]), year)
    return [[len((hf & bb) - ebb)]]


def ref_hfref_gdmt_pillars(db, year):
    """Patients with HFrEF coded as systolic heart failure (I50.2x) by year end and an outpatient visit in
    the year, by number of the four GDMT pillars filled in the year: evidence-based beta-blocker
    (carvedilol, metoprolol succinate, bisoprolol), ACE inhibitor/ARB/ARNI, MRA, SGLT2 inhibitor."""
    s, e = _bounds(year)
    den = _dx(db, HFREF, end=e) & _visit_patients(db, year)
    sets = [_fill_in_year(_fills(db, classes=c), year)
            for c in (["hf_evidence_based_beta_blocker"], RAS, ["mra"], ["sglt2_inhibitor"])]
    counts = {str(k): 0 for k in range(5)}
    for p in den:
        counts[str(sum(p in x for x in sets))] += 1
    return [[k + " pillars", v] for k, v in counts.items()]


def ref_hf_iron_deficiency(db, year):
    """Patients with heart failure (I50, I11.0, I13.0, I13.2 by year end) with a ferritin in the year:
    share meeting the ESC definition of iron deficiency on any test day (ferritin < 100 ng/mL, or 100-299
    with transferrin saturation < 20% on the same day)."""
    s, e = _bounds(year)
    hf = _dx(db, HF_ANY, end=e)
    fer = _lab(db, "ferritin", start=s, end=e)
    tsat = _lab(db, names=["Transferrin saturation"], start=s, end=e)
    den = num = 0
    for p, rows in fer.items():
        if p not in hf:
            continue
        den += 1
        ts = {d: v for d, v, c in tsat.get(p, [])}
        if any(v < 100 or (v < 300 and d in ts and ts[d] < 20) for d, v, c in rows):
            num += 1
    return [[_rate(num, den)]]


# ---------------------------------------------------------------- hematology

def _anaemic(db, year):
    """{patient: lowest Hb} for adults (>= 18 at test) meeting WHO sex-specific anaemia (< 13 men,
    < 12 non-pregnant women); women with a pregnancy code in the year are excluded."""
    s, e = _bounds(year)
    pts = _patients(db)
    preg = _dx(db, PREGNANCY, end=e, start=s)
    out = {}
    for p, rows in _lab(db, "hemoglobin", start=s, end=e).items():
        if p not in pts:
            continue
        sex, birth = pts[p]
        if sex == "F" and p in preg:
            continue
        cut = 13.0 if sex == "M" else 12.0
        lows = [v for d, v, c in rows if (a := _age(birth, d)) is not None and a >= 18 and v < cut]
        if lows:
            out[p] = (sex, min(lows))
    return out


def ref_anaemia_who(db, year):
    """Adults with any haemoglobin in the year below the WHO cut-off (< 13 g/dL men, < 12 g/dL
    non-pregnant women)."""
    return [[len(_anaemic(db, year))]]


def ref_anaemia_severity(db, year):
    """Those adults by WHO severity of their lowest Hb: severe < 8; moderate 8-10.9; mild 11-12.9 (men)
    or 11-11.9 (women)."""
    counts = {"mild": 0, "moderate": 0, "severe": 0}
    for p, (sex, v) in _anaemic(db, year).items():
        if v < 8:
            counts["severe"] += 1
        elif v < 11:
            counts["moderate"] += 1
        else:
            counts["mild"] += 1
    return [[k, v] for k, v in counts.items()]


def ref_microcytic_anaemia(db, year):
    """Adults with a WHO-anaemic haemoglobin and an MCV < 80 fL on the same day in the year."""
    s, e = _bounds(year)
    pts = _patients(db)
    preg = _dx(db, PREGNANCY, end=e, start=s)
    mcv = _lab(db, names=["MCV"], start=s, end=e)
    n = 0
    for p, rows in _lab(db, "hemoglobin", start=s, end=e).items():
        if p not in pts or p not in mcv:
            continue
        sex, birth = pts[p]
        if sex == "F" and p in preg:
            continue
        cut = 13.0 if sex == "M" else 12.0
        low_mcv_days = {d for d, v, c in mcv[p] if v < 80}
        if any(v < cut and d in low_mcv_days and (_age(birth, d) or 0) >= 18 for d, v, c in rows):
            n += 1
    return [[n]]


# ---------------------------------------------------------------- electrolytes

def ref_hyperkalaemia_gt5(db, year):
    """Patients with any potassium > 5.0 mmol/L in the year (hemolysed specimens excluded)."""
    s, e = _bounds(year)
    return [[sum(1 for rows in _lab(db, "potassium", start=s, end=e).values() if any(r[1] > 5.0 for r in rows))]]


def ref_hyperkalaemia_ge55(db, year):
    """Patients with any potassium >= 5.5 mmol/L in the year (ERC / UK Kidney Association)."""
    s, e = _bounds(year)
    return [[sum(1 for rows in _lab(db, "potassium", start=s, end=e).values() if any(r[1] >= 5.5 for r in rows))]]


def ref_hyponatraemia_severity(db, year):
    """Patients by European (2014) hyponatraemia severity of their lowest sodium in the year: mild
    130-134, moderate 125-129, profound < 125 mmol/L."""
    s, e = _bounds(year)
    counts = {"mild": 0, "moderate": 0, "profound": 0}
    for rows in _lab(db, "sodium", start=s, end=e).values():
        v = min(r[1] for r in rows)
        if v < 125:
            counts["profound"] += 1
        elif v < 130:
            counts["moderate"] += 1
        elif v < 135:
            counts["mild"] += 1
    return [[k, v] for k, v in counts.items()]





# ---------------------------------------------------------------- medication safety


def ref_triple_whammy(db, year):
    """Patients with >= 1 day in the year covered by an ACE inhibitor/ARB/ARNI, a diuretic (loop,
    thiazide or MRA) and an NSAID at once."""
    return [[len(_overlap_patients(db, [RAS, ["loop_diuretic", "thiazide_diuretic", "mra"], ["nsaid"]], year))]]


def ref_anticoagulant_nsaid(db, year):
    """Patients with >= 1 day in the year covered by an oral anticoagulant and an NSAID at once."""
    return [[len(_overlap_patients(db, [["anticoagulant"], ["nsaid"]], year))]]


def ref_beers_avoid(db, year):
    """Patients aged >= 65 at the fill with a fill in the year of a 2023 AGS Beers 'avoid' drug from these
    groups: benzodiazepines, first-generation antihistamines, skeletal muscle relaxants, meperidine."""
    s, e = _bounds(year)
    pts = _patients(db)
    fills = _fills(db, classes=["benzodiazepine", "first_generation_antihistamine", "skeletal_muscle_relaxant", "opioid"])
    n = 0
    for p, rows in fills.items():
        if p not in pts:
            continue
        for f in rows:
            if not s <= f[0] <= e:
                continue
            if f[3] == "opioid" and "meperidine" not in f[2]:
                continue
            a = _age(pts[p][1], f[0])
            if a is not None and a >= 65:
                n += 1
                break
    return [[n]]



# ---------------------------------------------------------------- behavioral health

def ref_phq9_severity(db, year):
    """Patients by PHQ-9 severity of their last score in the year: minimal 0-4, mild 5-9, moderate 10-14,
    moderately severe 15-19, severe 20-27 (Kroenke 2001)."""
    s, e = _bounds(year)
    labels = ["minimal", "mild", "moderate", "moderately severe", "severe"]
    counts = dict.fromkeys(labels, 0)
    for rows in _lab(db, "phq9", start=s, end=e).values():
        last_day = max(r[0] for r in rows)
        v = max(r[1] for r in rows if r[0] == last_day)
        idx = 0 if v < 5 else 1 if v < 10 else 2 if v < 15 else 3 if v < 20 else 4
        counts[labels[idx]] += 1
    return [[k, v] for k, v in counts.items()]



ANTIDEPRESSANTS = ["ssri", "snri", "ndri", "sari_antidepressant", "tetracyclic_antidepressant"]


def ref_phq9_positive_antidepressant(db, year):
    """Patients with any PHQ-9 >= 10 in the year: share with an antidepressant fill in the year (SSRI, SNRI,
    bupropion, trazodone, mirtazapine; benzodiazepines, antipsychotics and lithium do not count)."""
    s, e = _bounds(year)
    pos = {p for p, rows in _lab(db, "phq9", start=s, end=e).items() if any(r[1] >= 10 for r in rows)}
    ad = _fill_in_year(_fills(db, classes=ANTIDEPRESSANTS), year)
    return [[_rate(len(pos & ad), len(pos))]]

# ---------------------------------------------------------------- metabolic

def ref_obesity_class(db, year):
    """Adults (>= 18 at year end) with a weight in the year and a recorded height (any date): counts by
    WHO BMI class of last weight in the year / tallest recorded height squared - class I 30-34.9,
    class II 35-39.9, class III >= 40. Pregnant women (pregnancy code in the year) excluded."""
    s, e = _bounds(year)
    pts = _patients(db)
    preg = _dx(db, PREGNANCY, end=e, start=s)
    wt = _lab(db, "weight", start=s, end=e)
    q = ("SELECT patient_id, MAX(value) FROM measurement WHERE name = 'height_cm' AND value IS NOT NULL "
         "AND result_current = 1 AND excluded_reason IS NULL GROUP BY patient_id")
    ht = {p: v for p, v in db.execute(q)}
    counts = {"class I": 0, "class II": 0, "class III": 0}
    for p, rows in wt.items():
        if p not in pts or p not in ht or not ht[p]:
            continue
        sex, birth = pts[p]
        a = _age(birth, e)
        if a is None or a < 18 or (sex == "F" and p in preg):
            continue
        last = max(rows, key=lambda r: r[0])
        bmi = last[1] / (ht[p] / 100.0) ** 2
        if bmi >= 40:
            counts["class III"] += 1
        elif bmi >= 35:
            counts["class II"] += 1
        elif bmi >= 30:
            counts["class I"] += 1
    return [[k, v] for k, v in counts.items()]


# ---------------------------------------------------------------- infection

ANTIBIOTICS = ["penicillin", "cephalosporin", "macrolide", "tetracycline_antibiotic", "fluoroquinolone",
               "sulfonamide_antibiotic", "nitrofuran"]
URI_CODES = ["J00", "J06"]
COMPETING = ["J01", "J02", "J03", "J04", "J13", "J14", "J15", "J16", "J17", "J18", "J20", "J32", "J35",
             "H66", "H67", "N30", "N34", "N39.0", "L01", "L02", "L03", "L08", "A38", "A46", "A49", "A69.2"]


def _dx_rows(db):
    rows = defaultdict(list)
    for pid, code, ds in db.execute("SELECT patient_id, icd10, event_date FROM encounter_diagnosis WHERE event_date IS NOT NULL"):
        rows[pid].append((_d(ds), (code or "").replace(".", "").upper()))
    for pid, code, ds in db.execute("SELECT patient_id, icd10, service_from FROM payer_claim_diagnosis WHERE service_from IS NOT NULL"):
        rows[pid].append((_d(ds), (code or "").replace(".", "").upper()))
    return rows


def ref_uri_antibiotic(db, year):
    """HEDIS URI-style: episodes = distinct patient-days in the year with an encounter or claim diagnosis of
    acute upper respiratory infection (J00, J06.x), patient aged >= 3 months. Excluded: an antibiotic fill
    in the 30 days before; a competing bacterial diagnosis (sinusitis, pharyngitis/tonsillitis,
    pneumonia, bronchitis, otitis media, UTI, skin infection, etc.) on the episode day through 3 days after.
    Figure = share of episodes with an antibiotic fill on the episode day through 3 days after (the
    inappropriate-treatment rate; HEDIS reports 1 minus this)."""
    s, e = _bounds(year)
    pts = _patients(db)
    dx = _dx_rows(db)
    abx = _fills(db, classes=ANTIBIOTICS)
    uri = tuple(c.replace(".", "") for c in URI_CODES)
    comp = tuple(c.replace(".", "") for c in COMPETING)
    den = num = 0
    for p, rows in dx.items():
        days = sorted({d for d, c in rows if s <= d <= e and c.startswith(uri)})
        if not days or p not in pts or not pts[p][1]:
            continue
        for d in days:
            if (d - _d(pts[p][1])).days < 90:
                continue
            if any(0 <= (cd - d).days <= 3 and c.startswith(comp) for cd, c in rows):
                continue
            fills = abx.get(p, [])
            if any(1 <= (d - f[0]).days <= 30 for f in fills):
                continue
            den += 1
            if any(0 <= (f[0] - d).days <= 3 for f in fills):
                num += 1
    return [[_rate(num, den)]]
