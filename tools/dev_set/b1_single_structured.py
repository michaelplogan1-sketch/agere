"""Bucket 1: single-patient questions answerable from structured data (labs, meds, visits, coverage, care gaps).

Patients are picked by seeded random from pools chosen to mix rich charts (deep_chart / many labs) and sparse ones (claims only).
Every key is SQL over the semantic layer written against definitions.py.
"""
from __future__ import annotations

from tools.dev_set import definitions as D
from tools.dev_set.keys import Q

B = 'single_patient_structured'
LIV = D.LIVING_CTE


def _pdc(ctx, pid, drug_class, start, end):
    """Proportion of days covered for one class: covered days over [first fill in window, window end]; overlapping supply
    carried forward (a refill starts when the previous supply ends); requires D.PDC_MIN_FILLS fills."""
    import datetime as dt
    rows = ctx.rows("SELECT fill_date, days_supply FROM fill WHERE patient_id = ? AND drug_class = ? AND fill_date >= ? AND fill_date <= ? "
                    "ORDER BY fill_date, fill_id", (pid, drug_class, start, end))
    if len(rows) < D.PDC_MIN_FILLS:
        return None, len(rows)
    first = dt.date.fromisoformat(rows[0][0])
    last_day = dt.date.fromisoformat(end)
    covered, cursor = set(), first
    for fd, ds in rows:
        d = max(dt.date.fromisoformat(fd), cursor)
        for i in range(int(ds or 0)):
            day = d + dt.timedelta(days=i)
            if day <= last_day:
                covered.add(day)
        cursor = d + dt.timedelta(days=int(ds or 0))
    return len(covered) / ((last_day - first).days + 1), len(rows)


PDC_SQL_NOTE = ("PDC computed in tools/dev_set/b1_single_structured._pdc: fills of the class in the window; days covered with "
                "carry-forward of overlapping supply; denominator = days from first fill in window to window end; >=2 fills")


def build(ctx):
    out = []
    add = out.append

    def sx(pid):
        return ctx.one('SELECT sex_reported FROM patient WHERE patient_id = ?', (pid,))

    # ---- picks --------------------------------------------------------------------------------------------------------
    a1c_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM (
        SELECT patient_id, COUNT(DISTINCT event_date) n FROM ({D.any_lab('hba1c')}) GROUP BY 1)
        WHERE n >= 4 AND patient_id IN (SELECT patient_id FROM living)""")
    a1c_trend_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM (
        SELECT patient_id, COUNT(DISTINCT event_date) n FROM ({D.any_lab('hba1c')}) GROUP BY 1)
        WHERE n >= 3 AND patient_id IN (SELECT patient_id FROM living) AND patient_id <> '{a1c_pat}'""")
    egfr_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM measurement_latest WHERE concept = 'egfr' AND value_canonical < 60
        AND patient_id IN (SELECT patient_id FROM living)""")
    bp_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM (
        SELECT patient_id, COUNT(DISTINCT event_date) n FROM ({D.any_lab('systolic_bp')}) GROUP BY 1)
        WHERE n >= 6 AND patient_id IN (SELECT patient_id FROM living)""")
    creat_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM (
        SELECT patient_id, COUNT(*) n FROM ({D.any_lab('creatinine')}) GROUP BY 1) WHERE n >= 5
        AND patient_id IN (SELECT patient_id FROM living)""")
    med_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM medication_list_entry WHERE list_status = 'active'
        AND patient_id IN (SELECT patient_id FROM living) GROUP BY 1 HAVING COUNT(*) BETWEEN 4 AND 9""")
    statin_pat, statin_pdc_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM fill WHERE drug_class = 'statin'
        AND fill_date >= '2029-01-01' AND fill_date <= '{D.AS_OF}' AND patient_id IN (SELECT patient_id FROM living)
        GROUP BY 1 HAVING COUNT(*) >= 3""", k=2)
    ed_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM utilization_event WHERE kind = 'ed_visit' AND {D.in_last_year('event_date')}
        AND patient_id IN (SELECT patient_id FROM living) GROUP BY 1 HAVING COUNT(*) >= 2""")
    adm_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM utilization_event WHERE kind = 'inpatient' AND {D.in_year('event_date')}
        AND end_date IS NOT NULL AND patient_id IN (SELECT patient_id FROM living) GROUP BY 1 HAVING COUNT(*) >= 2""")
    readmit_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM stay WHERE readmitted_30d = 1 AND patient_id IN (SELECT patient_id FROM living)""")
    pl_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM problem_list_entry WHERE clinical_status = 'active'
        AND patient_id IN (SELECT patient_id FROM living) GROUP BY 1 HAVING COUNT(*) BETWEEN 3 AND 6""")
    cov_pat = ctx.pick(f"""WITH {LIV} SELECT e.patient_id FROM enrollment_span e JOIN patient p USING (patient_id)
        WHERE p.data_segment = 'claims_only' AND e.product IN ('medicare_advantage', 'medicaid', 'marketplace', 'dual_ma')
        AND e.patient_id IN (SELECT patient_id FROM living) GROUP BY 1 HAVING COUNT(*) = 1""")
    prac_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM patient WHERE pcp_practice_id IS NOT NULL AND data_segment = 'deep_chart'
        AND patient_id IN (SELECT patient_id FROM living)""")
    mammo_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM cancer_screening WHERE test = 'bilateral_screening_mammography'
        AND result_status = 'final' AND patient_id IN (SELECT patient_id FROM living)""")
    flu_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM immunization WHERE vaccine LIKE 'influenza%' AND status = 'completed'
        AND substr(event_date, 1, 4) = '2029' AND patient_id IN (SELECT patient_id FROM living)""")
    allergy_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM allergy_entry WHERE no_known_allergies = 0
        AND patient_id IN (SELECT patient_id FROM living) GROUP BY 1 HAVING COUNT(*) BETWEEN 2 AND 4""")
    need_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM social_need WHERE positive = 1
        AND patient_id IN (SELECT patient_id FROM living) GROUP BY 1 HAVING COUNT(DISTINCT need) >= 2""")
    paid_pat = ctx.pick(f"""WITH {LIV} SELECT c.patient_id FROM payer_claim c JOIN patient p USING (patient_id)
        WHERE c.counts_in_metrics = 1 AND {D.in_last_year('c.service_from')} AND p.data_segment = 'claims_only'
        AND c.patient_id IN (SELECT patient_id FROM living) GROUP BY 1 HAVING SUM(paid_cents) > 200000""")
    noshow_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM appointment WHERE final_status = 'no_show' AND {D.in_last_year('scheduled_date')}
        AND patient_id IN (SELECT patient_id FROM living) GROUP BY 1 HAVING COUNT(*) >= 2""")
    wt_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM (
        SELECT patient_id FROM ({D.any_lab('weight')}) WHERE {D.in_last_year('event_date')} GROUP BY 1 HAVING COUNT(DISTINCT event_date) >= 3)
        WHERE patient_id IN (SELECT patient_id FROM living)""")
    bpc_pat = ctx.pick(f"""WITH {LIV} SELECT s.patient_id FROM measurement_latest s JOIN measurement_latest d ON d.patient_id = s.patient_id
        AND d.concept = 'diastolic_bp' WHERE s.concept = 'systolic_bp' AND s.patient_id IN (SELECT patient_id FROM living)""")
    uacr_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM ({D.any_lab('uacr')}) WHERE patient_id IN (SELECT patient_id FROM living)
        GROUP BY 1 HAVING MAX(event_date) < '{D.LAST_YEAR_START}'""")
    ldl_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM measurement_latest WHERE concept = 'ldl' AND value_canonical > 100
        AND patient_id IN (SELECT patient_id FROM living)""")
    notfilled_pat = ctx.pick(f"""WITH {LIV} SELECT patient_id FROM prescription WHERE fill_status = 'not_filled'
        AND patient_id IN (SELECT patient_id FROM living) GROUP BY 1 HAVING COUNT(*) >= 1""")
    sparse_ed = ctx.pick(f"""WITH {LIV} SELECT u.patient_id FROM utilization_event u JOIN patient p USING (patient_id)
        WHERE u.kind = 'ed_visit' AND {D.in_last_year('u.event_date')} AND p.data_segment = 'claims_only'
        AND u.patient_id IN (SELECT patient_id FROM living)""")

    # ---- questions ----------------------------------------------------------------------------------------------------
    p = a1c_pat
    add(Q(B, 'a1c_latest', f"What's the most recent HbA1c on {p}?", 'value', unit='%', tolerance={'abs': 0.05},
          sql=f"SELECT value FROM ({D.latest_lab('hba1c')}) WHERE patient_id = '{p}'",
          notes='latest result by default (measurement_latest); also asked for its date in the evidence',
          evidence=[{'date': ctx.one(f"SELECT event_date FROM ({D.latest_lab('hba1c')}) WHERE patient_id = '{p}'")}]))
    add(Q(B, 'a1c_previous', "and the one before that?", 'value', unit='%', tolerance={'abs': 0.05}, follow_up_of='a1c_latest',
          standalone=f"What was the HbA1c result before the most recent one on {p}?",
          sql=f"""SELECT value FROM (SELECT value, ROW_NUMBER() OVER (ORDER BY event_date DESC, record_id DESC) rn
                  FROM ({D.any_lab('hba1c')}) WHERE patient_id = '{p}') WHERE rn = 2""",
          notes='"any result" history: the second most recent dated result'))

    p = a1c_trend_pat
    add(Q(B, 'a1c_trend', f"Is the diabetes control getting better or worse for {p}? Compare the last two A1c results.", 'label',
          accept={'improving': ['better', 'improv', 'lower', 'down', 'decreas', 'fell', 'drop'],
                  'worsening': ['worse', 'worsen', 'higher', 'up', 'increas', 'rose', 'rise', 'deteriorat'],
                  'unchanged': ['unchanged', 'same', 'stable', 'no change']},
          sql=f"""WITH r AS (SELECT value, ROW_NUMBER() OVER (ORDER BY event_date DESC, record_id DESC) rn
                       FROM ({D.any_lab('hba1c')}) WHERE patient_id = '{p}')
                  SELECT CASE WHEN a.value < b.value THEN 'improving' WHEN a.value > b.value THEN 'worsening' ELSE 'unchanged' END
                  FROM r a JOIN r b ON a.rn = 1 AND b.rn = 2""",
          notes='lower A1c = improving'))

    p = egfr_pat
    add(Q(B, 'egfr_latest', f"what is {p}'s latest eGFR", 'value', unit='mL/min/1.73m2', tolerance={'abs': 0.5},
          sql=f"SELECT value FROM ({D.latest_lab('egfr')}) WHERE patient_id = '{p}'"))

    p = bp_pat
    add(Q(B, 'sbp_last3', f"Show me the last three systolic blood pressure readings for {p}, newest first.", 'table',
          order_matters=True, tolerance={'abs': 0.5},
          sql=f"""SELECT event_date AS date, MAX(value) AS systolic FROM ({D.any_lab('systolic_bp')}) WHERE patient_id = '{p}'
                  GROUP BY event_date ORDER BY event_date DESC LIMIT 3""",
          notes='one reading per date (the highest if several the same day)'))

    p = creat_pat
    add(Q(B, 'creatinine_max', f"What's the highest creatinine we've ever recorded for {p}, and when was it?", 'table',
          tolerance={'abs': 0.01},
          sql=f"SELECT value AS creatinine, event_date AS date FROM ({D.any_lab('creatinine')}) WHERE patient_id = '{p}' "
              f"ORDER BY value DESC, event_date DESC LIMIT 1", notes='"ever/highest" = any result'))

    p = med_pat
    add(Q(B, 'active_meds', f"List the active medications for {p}.", 'list',
          sql=f"SELECT drug_key FROM medication_list_entry WHERE patient_id = '{p}' AND list_status = 'active'",
          full_names=[r[0] for r in ctx.rows("SELECT name FROM medication_list_entry WHERE patient_id = ? AND list_status = 'active' ORDER BY name", (p,))],
          notes='active = medication-list entry with list_status active (definitions.ACTIVE_MEDS); list items are drug keys, '
                'full list names in key.full_names; match on the ingredient'))

    p = statin_pat
    add(Q(B, 'statin_last_fill', f"When did {p} last pick up a statin?", 'date',
          sql=f"SELECT MAX(fill_date) FROM fill WHERE patient_id = '{p}' AND drug_class = 'statin'",
          notes='last row of fill (dispense + counted pharmacy claim, merged) in class statin'))

    p = statin_pdc_pat
    pdc, nf = _pdc(ctx, p, 'statin', '2029-01-01', '2029-12-31')
    add(Q(B, 'statin_pdc_2029', f"What was {p}'s adherence (PDC) on their statin in 2029?", 'value', unit='proportion',
          tolerance={'abs': 0.03}, answer=pdc, sql=PDC_SQL_NOTE + f" | patient {p}, class statin, window 2029-01-01..2029-12-31",
          notes='PDC definition in definitions.py (>=2 fills, overlap carried forward, denominator first fill to window end); '
                'a percentage form of the same number is accepted'))

    p = ed_pat
    add(Q(B, 'ed_visits_last_year', f"How many ED visits has {p} had in the past year?", 'count',
          sql=f"SELECT COUNT(*) FROM ({D.ED_VISITS}) WHERE patient_id = '{p}' AND {D.in_last_year('event_date')}"))

    p = adm_pat
    add(Q(B, 'admissions_2029', f"How many hospital admissions did {p} have in 2029?", 'count',
          sql=f"SELECT COUNT(*) FROM ({D.ADMISSIONS}) WHERE patient_id = '{p}' AND {D.in_year('admit_date')}"))
    add(Q(B, 'last_discharge', "when was the most recent one discharged?", 'date', follow_up_of='admissions_2029',
          standalone=f"When was the most recent 2029 hospital admission of {p} discharged?",
          sql=f"SELECT discharge_date FROM ({D.ADMISSIONS}) WHERE patient_id = '{p}' AND {D.in_year('admit_date')} "
              f"ORDER BY admit_date DESC LIMIT 1"))

    p = readmit_pat
    add(Q(B, 'readmitted', f"Was {p} ever readmitted within 30 days of a discharge?", 'yes_no',
          sql=f"SELECT MAX(readmitted_30d) FROM stay WHERE patient_id = '{p}'", notes='stay.readmitted_30d'))

    p = pl_pat
    add(Q(B, 'problem_list', f"What's on the active problem list for {p}?", 'list',
          sql=f"SELECT label FROM problem_list_entry WHERE patient_id = '{p}' AND clinical_status = 'active'",
          notes='problem_list_entry rows with clinical_status active (resolved problems excluded)'))

    p = cov_pat
    add(Q(B, 'coverage_product', f"What kind of insurance coverage does {p} have?", 'label',
          accept=None,
          sql=f"SELECT product FROM enrollment_span WHERE patient_id = '{p}' ORDER BY coverage_start DESC LIMIT 1",
          notes='product of the (single) enrollment span; underscores read as spaces (medicare_advantage = Medicare Advantage)'))

    p = prac_pat
    add(Q(B, 'practice', f"Which practice is {p} attributed to?", 'label',
          accept=None,
          sql=f"SELECT pcp_practice_id FROM patient WHERE patient_id = '{p}'",
          display_name=ctx.one("SELECT o.display_name FROM organization o JOIN patient p ON p.pcp_practice_id = o.organization_id WHERE p.patient_id = ?", (p,)),
          notes='patient.pcp_practice_id; the display name from organization also counts'))

    p = mammo_pat
    add(Q(B, 'last_mammogram', f"When was {p}'s last screening mammogram?", 'date',
          sql=f"SELECT MAX(event_date) FROM cancer_screening WHERE patient_id = '{p}' AND test = 'bilateral_screening_mammography' AND result_status = 'final'",
          notes='final mammography results in cancer_screening (billed-only claim rows are a separate source)'))

    p = flu_pat
    add(Q(B, 'last_flu', f"Did {p} get a flu shot this past season? If so, when?", 'date',
          sql=f"SELECT MAX(event_date) FROM immunization WHERE patient_id = '{p}' AND vaccine LIKE 'influenza%' AND status = 'completed'",
          notes='latest completed influenza vaccine (any formulation)'))

    p = allergy_pat
    add(Q(B, 'allergies', f"What allergies are recorded for {p}?", 'list',
          sql=f"SELECT substance FROM allergy_entry WHERE patient_id = '{p}' AND no_known_allergies = 0"))

    p = need_pat
    add(Q(B, 'social_needs', f"Which social needs screened positive for {p}?", 'list',
          sql=f"SELECT DISTINCT need FROM social_need WHERE patient_id = '{p}' AND positive = 1",
          notes='any positive screen (definitions.POSITIVE_NEED), any date'))

    p = paid_pat
    add(Q(B, 'paid_last_year', f"How much did the plan pay in claims for {p} over the last 12 months?", 'value', unit='USD',
          tolerance={'abs': 1.0}, sql=f"SELECT SUM(paid_cents) / 100.0 FROM payer_claim WHERE patient_id = '{p}' AND counts_in_metrics = 1 AND {D.in_last_year('service_from')}",
          notes='payer claims counted in metrics, paid amount by service date, 12 months ending as-of'))

    p = noshow_pat
    add(Q(B, 'no_shows', f"How many appointments did {p} no-show in the last year?", 'count',
          sql=f"SELECT COUNT(*) FROM appointment WHERE patient_id = '{p}' AND final_status = 'no_show' AND {D.in_last_year('scheduled_date')}"))

    p = wt_pat
    add(Q(B, 'weight_change', f"How much has {p}'s weight changed over the last year (first vs last reading in the window), in kg?", 'value',
          unit='kg', tolerance={'abs': 0.2},
          sql=f"""WITH w AS (SELECT event_date, value, ROW_NUMBER() OVER (ORDER BY event_date, record_id) a,
                             ROW_NUMBER() OVER (ORDER BY event_date DESC, record_id DESC) z
                      FROM ({D.any_lab('weight')}) WHERE patient_id = '{p}' AND {D.in_last_year('event_date')})
                  SELECT (SELECT value FROM w WHERE z = 1) - (SELECT value FROM w WHERE a = 1)""",
          notes='positive = gained; last minus first reading within the 12 months'))

    p = bpc_pat
    add(Q(B, 'bp_controlled', f"Is {p}'s blood pressure under control based on the latest reading (under 140/90)?", 'yes_no',
          sql=f"""SELECT (SELECT value_canonical FROM measurement_latest WHERE patient_id = '{p}' AND concept = 'systolic_bp') < 140
                  AND (SELECT value_canonical FROM measurement_latest WHERE patient_id = '{p}' AND concept = 'diastolic_bp') < 90""",
          notes='latest systolic and latest diastolic (measurement_latest) both under the threshold'))

    p = uacr_pat
    add(Q(B, 'uacr_last_year', f"Has {p} had a urine albumin/creatinine test in the last 12 months?", 'yes_no',
          sql=f"SELECT COUNT(*) > 0 FROM ({D.any_lab('uacr')}) WHERE patient_id = '{p}' AND {D.in_last_year('event_date')}"))

    p = ldl_pat
    add(Q(B, 'ldl_latest', f"What was {p}'s latest LDL?", 'value', unit='mg/dL', tolerance={'abs': 0.5},
          sql=f"SELECT value FROM ({D.latest_lab('ldl')}) WHERE patient_id = '{p}'"))
    pron = 'she' if sx(p) == 'F' else 'he'
    add(Q(B, 'ldl_statin', f"is {pron} on a statin?", 'yes_no', follow_up_of='ldl_latest',
          standalone=f"Is {p} on a statin?",
          sql=f"""SELECT (SELECT COUNT(*) FROM medication_list_entry WHERE patient_id = '{p}' AND list_status = 'active'
                    AND (lower(name) LIKE '%statin%')) > 0
                  OR (SELECT COUNT(*) FROM fill WHERE patient_id = '{p}' AND drug_class = 'statin' AND {D.in_last_year('fill_date')}) > 0""",
          notes='on a statin = an active statin on the medication list OR a statin fill in the last 12 months'))

    p = notfilled_pat
    add(Q(B, 'prescribed_not_filled', f"Which prescriptions written for {p} were never filled?", 'list',
          sql=f"SELECT drug_name FROM prescription WHERE patient_id = '{p}' AND fill_status = 'not_filled'",
          notes='prescription.fill_status = not_filled'))

    p = sparse_ed
    add(Q(B, 'ed_sparse', f"{p} — how many times did they go to the ER over the past year?", 'count',
          sql=f"SELECT COUNT(*) FROM ({D.ED_VISITS}) WHERE patient_id = '{p}' AND {D.in_last_year('event_date')}",
          notes='claims-only (sparse) patient: ED use comes from the claim-derived utilization events'))

    p = a1c_pat
    add(Q(B, 'absent_satisfaction', f"What was {p}'s patient-satisfaction (CAHPS) score after the last visit?", 'absent',
          reason='The warehouse holds no patient-experience or satisfaction survey data (no CAHPS, NPS or survey table).',
          notes='checked: no table or column carries survey / satisfaction / CAHPS content'))
    return out
