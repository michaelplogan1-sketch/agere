"""Read-only parity report between two Agere semantic layers (e.g. v1.23 and v2.0 builds).

Opens each semantic.sqlite3 immutable/read-only and computes the same metrics inside a common
as_of window (records with event_date and arrival on or before the window end), plus the newer
build at its own cutoff. Metrics target the generator defects logged in SYNTHETIC_DATA_ISSUES.md.

    .venv/bin/python tools/warehouse_parity.py --old artifacts/agere-healthcare-v123/stack \
        --new artifacts/agere-healthcare-v20/stack --window 2029-08-15 --out artifacts/agere-healthcare-v20/parity
"""
import argparse
import json
import sqlite3
from pathlib import Path

GDMT = ('sglt2_inhibitor', 'mra', 'arni', 'hf_evidence_based_beta_blocker', 'glp1_agonist', 'ace_inhibitor', 'arb')


def connect(stack):
    return sqlite3.connect(f'file:{Path(stack) / "semantic.sqlite3"}?immutable=1', uri=True)


def has(db, table, col=None):
    cols = [r[1] for r in db.execute(f'PRAGMA table_info({table})')]
    return bool(cols) and (col is None or col in cols)


def metrics(db, w):
    one = lambda sql, *a: db.execute(sql, a).fetchone()
    rows = lambda sql, *a: db.execute(sql, a).fetchall()
    m = {'window_end': w}
    m['patients'] = one('SELECT count(*) FROM patient')[0]
    m['records_visible'] = one('SELECT count(*) FROM record WHERE (event_date IS NULL OR event_date <= ?) AND available_date <= ?', w, w)[0]
    vis = "event_date <= ? AND coalesce(first_available_date, available_date) <= ?"
    m['visits_by_type'] = dict(rows(f'SELECT type, count(*) FROM visit WHERE {vis} GROUP BY 1 ORDER BY 2 DESC LIMIT 15', w, w))
    m['visits_by_concept'] = dict(rows(f"SELECT coalesce(type_concept,'unmapped'), count(*) FROM visit WHERE {vis} GROUP BY 1 ORDER BY 2 DESC", w, w))
    # D1: discharge time on inpatient stays / inpatient encounters
    n, known = one('SELECT count(*), sum(discharge_known) FROM stay WHERE admit_date <= ?', w)
    m['D1_stays'] = {'stays': n, 'with_discharge': known, 'pct_with_discharge': round(100 * (known or 0) / n, 1) if n else None}
    n, ended = one(f"SELECT count(*), count(end_time) FROM visit WHERE type_concept = 'inpatient_admission' AND {vis}", w, w)
    m['D1_inpatient_encounters'] = {'encounters': n, 'with_end_time': ended, 'pct': round(100 * ended / n, 1) if n else None}
    # D2/D4/D15: monthly ED and inpatient
    monthly = {}
    for c, mo, k in rows(f"""SELECT type_concept, substr(event_date,1,7), count(*) FROM visit
                           WHERE type_concept IN ('emergency_visit','inpatient_admission') AND {vis} GROUP BY 1, 2""", w, w):
        monthly.setdefault(c, {})[mo] = k
    m['monthly_acute'] = monthly
    gap = [f'{y}-{mm:02d}' for y in (2026, 2027, 2028) for mm in range(1, 13) if '2026-11' <= f'{y}-{mm:02d}' <= '2028-09']
    m['D2_gap_2026_11_to_2028_09'] = {c: {'visits': sum(v.get(x, 0) for x in gap), 'zero_months': sum(1 for x in gap if not v.get(x))}
                                      for c, v in monthly.items()}
    ed = monthly.get('emergency_visit', {})
    m['D4_ed_2026_08_09_10'] = [ed.get('2026-08', 0), ed.get('2026-09', 0), ed.get('2026-10', 0)]
    lo = str(int(w[:4]) - 1) + w[4:]
    m['D15_ed_per_1000_last_12m'] = round(1000 * sum(v for k, v in ed.items() if lo[:7] < k <= w[:7]) / m['patients'], 1)
    m['D15_stays_total'] = n
    # D3: social screens / intake gaps
    for stream, a, b in (('social_screens', '2026-10', '2028-06'), ('care_intake', '2026-11', '2028-07')):
        m[f'D3_{stream}_{a}_{b}'] = one(f"SELECT count(*) FROM record WHERE stream = ? AND substr(event_date,1,7) BETWEEN ? AND ? AND available_date <= ?",
                                        stream, a, b, w)[0]
    # D5: cancellations by year
    m['D5_cancellations_by_year'] = dict(rows('SELECT substr(scheduled_date,1,4), sum(ever_cancelled) FROM appointment WHERE scheduled_date <= ? GROUP BY 1', w))
    m['D5_reschedules'] = one('SELECT sum(ever_rescheduled) FROM appointment WHERE scheduled_date <= ?', w)[0]
    # D6: cost-of-care difficulty by payer
    m['D6_cost_difficulty_by_payer'] = {k: [pos, scr] for k, pos, scr in rows(
        """SELECT p.coverage_type, count(DISTINCT CASE WHEN s.positive = 1 THEN s.patient_id END), count(DISTINCT s.patient_id)
           FROM social_need s JOIN patient p USING(patient_id) WHERE s.need = 'cost_of_care' AND (s.event_date IS NULL OR s.event_date <= ?)
           GROUP BY 1""", w)}
    # D8 / D19: A1c
    a1c = "concept = 'hba1c' AND value_canonical IS NOT NULL AND event_date <= ? AND available_date <= ?"
    tested, over9 = one(f'SELECT count(DISTINCT patient_id), count(DISTINCT CASE WHEN value_canonical > 9 THEN patient_id END) FROM measurement WHERE {a1c}', w, w)
    latest = rows(f"""SELECT patient_id, value_canonical FROM (SELECT patient_id, value_canonical,
                      row_number() OVER (PARTITION BY patient_id ORDER BY event_date DESC, record_id DESC) rn FROM measurement WHERE {a1c}) WHERE rn = 1""", w, w)
    diab = {r[0] for r in rows("SELECT DISTINCT patient_id FROM condition WHERE concept IN ('diabetes','type_2_diabetes','type_1_diabetes')")}
    ld = [v for p, v in latest if p in diab]
    m['D8_a1c'] = {'patients_tested': tested, 'patients_ever_over_9': over9, 'diabetics': len(diab),
                   'diabetics_latest_over_9': sum(v > 9 for v in ld), 'diabetics_with_a1c': len(ld),
                   'pct_diabetics_latest_over_9': round(100 * sum(v > 9 for v in ld) / len(ld), 1) if ld else None,
                   'max_a1c': one(f'SELECT max(value_canonical) FROM measurement WHERE {a1c}', w, w)[0]}
    m['D19_a1c_ge_6_5_without_diabetes_dx'] = len({p for (p,) in rows(f'SELECT DISTINCT patient_id FROM measurement WHERE {a1c} AND value_canonical >= 6.5', w, w)} - diab)
    # D9: GDMT among heart failure
    hf = {r[0] for r in rows("SELECT DISTINCT patient_id FROM condition WHERE concept IN ('heart_failure','heart_failure_reduced_ef')")}
    hfref = {r[0] for r in rows("SELECT DISTINCT patient_id FROM condition WHERE concept = 'heart_failure_reduced_ef'")}
    g = {}
    for cls in GDMT:
        ever = {r[0] for r in rows('SELECT DISTINCT patient_id FROM medication WHERE drug_class = ? AND (event_date IS NULL OR event_date <= ?) AND available_date <= ?', cls, w, w)}
        disp = {r[0] for r in rows("SELECT DISTINCT patient_id FROM medication WHERE drug_class = ? AND evidence = 'dispensed' AND event_date <= ? AND available_date <= ?", cls, w, w)}
        g[cls] = {'hf_any_evidence': len(ever & hf), 'hf_dispensed': len(disp & hf), 'all_patients_any': len(ever)}
    m['D9_gdmt'] = {'hf_patients': len(hf), 'hfref_patients': len(hfref), 'by_class': g}
    # D10: care management
    cm = rows("""SELECT r.patient_id, json_extract(r.payload,'$.status'), p.coverage_type, coalesce(p.age_latest, p.age_reported)
                 FROM record r JOIN patient p USING(patient_id) WHERE r.stream = 'care_management_contacts' AND r.available_date <= ?""", w)
    agreed = {p for p, s, *_ in cm if s == 'agrees'}
    med65 = {p for (p,) in rows("SELECT patient_id FROM patient WHERE coverage_type = 'medicare' AND coalesce(age_latest, age_reported) >= 65")}
    m['D10_care_management'] = {'contacted': len({x[0] for x in cm}), 'agreed': len(agreed), 'medicare_65plus': len(med65),
                                'medicare_65plus_agreed': len(agreed & med65),
                                'pct_agreed_who_are_medicare_65plus': round(100 * len(agreed & med65) / len(agreed), 1) if agreed else None}
    # D11 / D14: incident starts and diagnoses by year
    m['D11_first_dispense_year'] = {cls: dict(rows("""SELECT substr(f,1,4), count(*) FROM (SELECT patient_id, min(event_date) f FROM medication
                                    WHERE drug_class = ? AND evidence = 'dispensed' AND event_date <= ? GROUP BY 1) GROUP BY 1""", cls, w))
                                    for cls in ('statin', 'sglt2_inhibitor', 'glp1_agonist', 'mra', 'arni')}
    m['D14_first_dated_diabetes_record_year'] = dict(rows("""SELECT substr(f,1,4), count(*) FROM (SELECT patient_id, min(event_date) f FROM condition
                                    WHERE concept IN ('diabetes','type_2_diabetes','type_1_diabetes') AND event_date <= ? GROUP BY 1) GROUP BY 1""", w))
    # D13: coverage churn
    m['D13_coverage_records_per_patient_max'] = one("SELECT max(n) FROM (SELECT count(*) n FROM record WHERE stream = 'coverage' AND available_date <= ? GROUP BY patient_id)", w)[0]
    # D7 / D17
    n, undated = one("SELECT count(*), sum(event_date IS NULL) FROM record WHERE stream = 'conditions' AND available_date <= ?", w)
    m['D7_conditions_undated'] = [undated, n]
    m['D17_negative_lab_values'] = one("SELECT count(*) FROM measurement WHERE source_table = 'labs' AND value < 0 AND (event_date IS NULL OR event_date <= ?)", w)[0]
    m['payer_mix'] = dict(rows('SELECT coverage_type, count(*) FROM patient GROUP BY 1'))
    return m


def standards(db):
    """Demographics and standard-code coverage (columns exist only on semantic-layer/13+)."""
    if not has(db, 'patient', 'race'): return None
    rows = lambda sql: db.execute(sql).fetchall()
    one = lambda sql: db.execute(sql).fetchone()
    pct = lambda a, b: round(100 * a / b, 1) if b else None
    s = {}
    for col in ('race', 'ethnicity', 'preferred_language', 'interpreter_needed', 'marital_status', 'state'):
        s[col] = dict(rows(f"SELECT coalesce(CAST({col} AS TEXT), '(null)'), count(*) FROM patient GROUP BY 1 ORDER BY 2 DESC"))
    s['birth_date_populated'] = one('SELECT count(birth_date), count(*) FROM patient')
    s['counties'] = one('SELECT count(DISTINCT county) FROM patient')[0]
    n, c = one("SELECT count(*), count(CASE WHEN icd10_source = 'source' THEN 1 END) FROM (SELECT record_id, max(icd10_source) icd10_source FROM condition WHERE source_table = 'conditions' GROUP BY 1)")
    s['pct_conditions_with_icd10'] = pct(c, n)
    n, src, any_ = one("""SELECT count(*), count(CASE WHEN src = 'source' THEN 1 END), count(CASE WHEN src IS NOT NULL THEN 1 END)
                          FROM (SELECT record_id, max(loinc_source) src FROM measurement WHERE source_table = 'labs' GROUP BY 1)""")
    s['pct_labs_with_source_loinc'] = pct(src, n); s['pct_labs_with_any_loinc'] = pct(any_, n); s['lab_rows'] = n
    s['labs_source_vs_dictionary_disagree'] = one("SELECT count(DISTINCT record_id) FROM measurement WHERE loinc_source = 'source' AND loinc_dictionary IS NOT NULL AND loinc_dictionary <> loinc")[0]
    n, c = one('SELECT count(*), count(CASE WHEN cpt IS NOT NULL OR hcpcs IS NOT NULL THEN 1 END) FROM claim_line')
    s['pct_claim_lines_cpt_or_hcpcs'] = pct(c, n); s['claim_lines'] = n
    s['claim_line_code_system'] = dict(rows("SELECT coalesce(code_system,'(null)'), count(*) FROM claim_line GROUP BY 1"))
    s['meds_rxnorm_by_stream'] = {st: [pct(src, n), pct(anyc, n), pct(ndc, n), n] for st, n, src, anyc, ndc in rows(
        """SELECT source_stream, count(*), count(CASE WHEN s = 'source' THEN 1 END), count(CASE WHEN s IS NOT NULL THEN 1 END), count(ndc)
           FROM (SELECT record_id, source_stream, max(rxnorm_source) s, max(ndc) ndc FROM medication GROUP BY 1, 2) GROUP BY 1""")}
    n, src = one("SELECT count(*), count(CASE WHEN s = 'source' THEN 1 END) FROM (SELECT record_id, max(rxnorm_source) s FROM medication GROUP BY 1)")
    s['pct_med_records_with_source_rxnorm'] = pct(src, n)
    n, c = one('SELECT count(*), count(cvx) FROM immunization'); s['pct_immunizations_with_cvx'] = pct(c, n)
    s['claims_with_drg'] = one('SELECT count(drg) FROM claim')[0]
    s['inpatient_claims_drg_top'] = dict(rows('SELECT drg, count(*) FROM claim WHERE drg IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10'))
    n, c = one("SELECT count(*), count(principal_icd10) FROM visit"); s['pct_visits_with_principal_icd10'] = pct(c, n)
    s['clinicians_with_npi'] = one('SELECT count(npi), count(*) FROM clinician')
    s['organizations_with_npi_tin'] = one('SELECT count(npi), count(tin), count(*) FROM organization')
    return s


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--old', type=Path, required=True); p.add_argument('--new', type=Path, required=True)
    p.add_argument('--window', default='2029-08-15'); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    old, new = connect(a.old), connect(a.new)
    cutoff = dict(new.execute('SELECT key, value FROM meta'))['as_of']
    result = {'synthetic': True, 'old': str(a.old), 'new': str(a.new), 'window': a.window,
              'method': 'Same SQL on each semantic layer; window = event_date and arrival on/before window end. '
                        'Stays are collapsed at each build cutoff, so a stay admitted before the window may show a later discharge.',
              'old_window': metrics(old, a.window), 'new_window': metrics(new, a.window), 'new_cutoff': metrics(new, cutoff),
              'new_standards': standards(new)}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix('.json').write_text(json.dumps(result, indent=1) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('old_window', 'new_window', 'new_cutoff', 'new_standards')}))


if __name__ == '__main__':
    main()
