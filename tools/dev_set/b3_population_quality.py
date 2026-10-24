"""Bucket 3: population clinical quality -- care gaps, control rates, GDMT gaps, readmissions, follow-up, screening, by group.

Every key is SQL over the semantic layer written against definitions.py. Shares carry numerator and denominator.
"""
from __future__ import annotations

from tools.dev_set import definitions as D
from tools.dev_set.keys import Q

B = 'population_clinical_quality'
LIV = D.LIVING_CTE
DIA = D.condition_cte('diabetes')
HTN = D.condition_cte('hypertension')
HF = D.condition_cte('heart_failure')
CKD = D.condition_cte('ckd')
CAD = D.condition_cte('cad')
COPD = D.condition_cte('copd')
ASTH = D.condition_cte('asthma')
DEP = D.condition_cte('depression')
AFIB = D.condition_cte('afib')
ADULT = f"{D.age_of()} >= 18"
LOB_OF = ("lob AS (SELECT patient_id, line_of_business FROM (SELECT patient_id, line_of_business, "
          "ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY coverage_start DESC) rn FROM enrollment_span) WHERE rn = 1)")


def build(ctx):
    out = []
    add = out.append

    add(Q(B, 'a1c_poor_share',
          "What share of our adult diabetic patients have an A1c above 9 on their latest result?", 'share',
          sql=f"""WITH {LIV}, {DIA}
                  SELECT SUM(m.value_canonical > {D.A1C_POOR_CONTROL}), COUNT(*)
                  FROM has_diabetes d JOIN patient p USING (patient_id)
                  JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c'
                  WHERE {D.age_of('p')} >= 18""",
          tolerance={'abs': 0.005},
          notes='denominator = living adult diabetics (EHR + problem list + counted claims + HbA1c >= 6.5) WITH a latest HbA1c; '
                'missing A1c is a separate care gap (a1c_missing)'))
    add(Q(B, 'a1c_poor_list', "give me the list of those patients", 'patient_set', follow_up_of='a1c_poor_share',
          standalone='List the adult diabetic patients whose latest A1c is above 9.',
          sql=f"""WITH {LIV}, {DIA}
                  SELECT d.patient_id FROM has_diabetes d JOIN patient p USING (patient_id)
                  JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c'
                  WHERE {D.age_of('p')} >= 18 AND m.value_canonical > {D.A1C_POOR_CONTROL}"""))
    add(Q(B, 'a1c_poor_by_lob', "break that down by line of business", 'table', follow_up_of='a1c_poor_list',
          standalone='For adult diabetic patients with a latest A1c above 9, how many are there in each line of business?',
          sql=f"""WITH {LIV}, {DIA}, {LOB_OF}
                  SELECT COALESCE(l.line_of_business, 'unknown') AS line_of_business, COUNT(*) AS patients
                  FROM has_diabetes d JOIN patient p USING (patient_id)
                  JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c'
                  LEFT JOIN lob l ON l.patient_id = d.patient_id
                  WHERE {D.age_of('p')} >= 18 AND m.value_canonical > {D.A1C_POOR_CONTROL}
                  GROUP BY 1 ORDER BY 2 DESC, 1""",
          notes='line of business of the most recent enrollment span; patients without a span are "unknown"'))

    add(Q(B, 'a1c_missing', "How many of our diabetics haven't had an A1c in the past year?", 'count',
          sql=f"""WITH {LIV}, {DIA}
                  SELECT COUNT(*) FROM has_diabetes d JOIN patient p USING (patient_id)
                  WHERE {D.age_of('p')} >= 18 AND d.patient_id NOT IN (SELECT patient_id FROM ({D.any_lab('hba1c')}) WHERE {D.in_last_year('event_date')})""",
          notes='adult living diabetics with no HbA1c result dated in the 12 months ending as-of'))

    add(Q(B, 'a1c_goal_by_practice',
          "Which practices have the weakest diabetes control? Show the three lowest share of diabetics under 8, practices with at least 15 diabetics.",
          'table', order_matters=True, tolerance={'abs': 0.005},
          sql=f"""WITH {LIV}, {DIA}, {D.PRACTICE_CTE}
                  SELECT pr.practice_id AS practice, COUNT(*) AS diabetics,
                         ROUND(1.0 * SUM(m.value_canonical < {D.A1C_GOAL}) / COUNT(*), 4) AS share_under_8
                  FROM has_diabetes d JOIN patient p USING (patient_id) JOIN practice_of pr USING (patient_id)
                  JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c'
                  WHERE {D.age_of('p')} >= 18 AND pr.practice_id IS NOT NULL
                  GROUP BY 1 HAVING COUNT(*) >= 15 ORDER BY share_under_8, practice LIMIT 3""",
          notes='controlled = latest A1c < 8; denominator per practice = living adult diabetics with a latest A1c; '
                'practice = pcp practice, else attributing practice of latest enrollment span'))

    add(Q(B, 'bp_control_share', "What percent of patients with hypertension have their blood pressure under control?", 'share',
          tolerance={'abs': 0.005},
          sql=f"""WITH {LIV}, {HTN}
                  SELECT SUM(s.value_canonical < {D.BP_CONTROL[0]} AND d.value_canonical < {D.BP_CONTROL[1]}), COUNT(*)
                  FROM has_hypertension h JOIN patient p USING (patient_id)
                  JOIN measurement_latest s ON s.patient_id = h.patient_id AND s.concept = 'systolic_bp'
                  JOIN measurement_latest d ON d.patient_id = h.patient_id AND d.concept = 'diastolic_bp'
                  WHERE {D.age_of('p')} BETWEEN 18 AND 85""",
          notes='living hypertensives age 18-85 with a latest systolic and diastolic; controlled = <140 and <90'))
    add(Q(B, 'bp_control_by_lob', "same thing by payer type", 'table', follow_up_of='bp_control_share', tolerance={'abs': 0.005},
          standalone='What percent of patients with hypertension have their blood pressure under control, by line of business?',
          sql=f"""WITH {LIV}, {HTN}, {LOB_OF}
                  SELECT COALESCE(l.line_of_business, 'unknown') AS line_of_business, COUNT(*) AS hypertensives,
                         ROUND(1.0 * SUM(s.value_canonical < {D.BP_CONTROL[0]} AND d.value_canonical < {D.BP_CONTROL[1]}) / COUNT(*), 4) AS controlled_share
                  FROM has_hypertension h JOIN patient p USING (patient_id)
                  JOIN measurement_latest s ON s.patient_id = h.patient_id AND s.concept = 'systolic_bp'
                  JOIN measurement_latest d ON d.patient_id = h.patient_id AND d.concept = 'diastolic_bp'
                  LEFT JOIN lob l ON l.patient_id = h.patient_id
                  WHERE {D.age_of('p')} BETWEEN 18 AND 85 GROUP BY 1 ORDER BY 2 DESC, 1"""))

    pillar_sql = {k: D.filled_class_last_year(v) for k, v in D.GDMT_PILLARS.items()}
    add(Q(B, 'hf_no_betablocker', "Which of our heart failure patients aren't on an evidence-based beta blocker?", 'patient_set',
          sql=f"""WITH {LIV}, {HF} SELECT patient_id FROM has_heart_failure
                  WHERE patient_id NOT IN ({pillar_sql['beta_blocker']})""",
          notes='on = at least one fill of a heart-failure beta blocker (carvedilol, metoprolol succinate, bisoprolol) in the last 12 months'))
    add(Q(B, 'hf_no_sglt2_count', "how many of those aren't on an SGLT2 inhibitor either?", 'count', follow_up_of='hf_no_betablocker',
          standalone='How many heart failure patients are on neither an evidence-based beta blocker nor an SGLT2 inhibitor?',
          sql=f"""WITH {LIV}, {HF} SELECT COUNT(*) FROM has_heart_failure
                  WHERE patient_id NOT IN ({pillar_sql['beta_blocker']}) AND patient_id NOT IN ({pillar_sql['sglt2']})"""))
    add(Q(B, 'hf_none', "How many HF patients are on no GDMT medication at all?", 'count',
          sql=f"""WITH {LIV}, {HF} SELECT COUNT(*) FROM has_heart_failure
                  WHERE patient_id NOT IN ({pillar_sql['beta_blocker']}) AND patient_id NOT IN ({pillar_sql['raas']})
                    AND patient_id NOT IN ({pillar_sql['mra']}) AND patient_id NOT IN ({pillar_sql['sglt2']})"""))

    add(Q(B, 'readmit_rate_2029', "What was our 30-day readmission rate for hospital discharges in 2029?", 'share', tolerance={'abs': 0.005},
          sql=f"""WITH {LIV} SELECT SUM(readmitted_30d), COUNT(*) FROM stay
                  WHERE type = 'inpatient' AND discharge_known = 1 AND substr(discharge_date, 1, 4) = '2029'
                    AND patient_id IN (SELECT patient_id FROM living)""",
          notes='stays with a known discharge in 2029 (inpatient only), index-stay readmitted_30d flag; living patients'))
    add(Q(B, 'readmit_hf', "Among heart failure patients, how many had at least one readmission within 30 days in 2029?", 'count',
          sql=f"""WITH {LIV}, {HF} SELECT COUNT(DISTINCT s.patient_id) FROM stay s JOIN has_heart_failure h USING (patient_id)
                  WHERE s.type = 'inpatient' AND s.readmitted_30d = 1 AND substr(s.discharge_date, 1, 4) = '2029'"""))

    add(Q(B, 'ed_followup_7d', "After an ED visit where the patient went home, what share got a follow-up visit within 7 days (2029 visits)?",
          'share', tolerance={'abs': 0.005},
          sql=f"""WITH {LIV}
                  SELECT SUM(EXISTS (SELECT 1 FROM ({D.FOLLOW_UP_VISIT}) f WHERE f.patient_id = e.patient_id
                                     AND f.event_date > e.end_date AND julianday(f.event_date) - julianday(e.end_date) <= 7)), COUNT(*)
                  FROM ({D.ED_VISIT_GROUP}) e WHERE substr(e.start_date, 1, 4) = '2029' AND e.end_date IS NOT NULL
                    AND e.patient_id IN (SELECT patient_id FROM living)""",
          notes='ED treat-and-release visit groups (visit_class emergency) starting in 2029; follow-up = completed outpatient visit '
                '(definitions.FOLLOW_UP_VISIT) strictly after the ED end date and within 7 days'))
    add(Q(B, 'ed_followup_30d', "and within 30 days?", 'share', follow_up_of='ed_followup_7d', tolerance={'abs': 0.005},
          standalone='After an ED visit where the patient went home, what share got a follow-up visit within 30 days (2029 visits)?',
          sql=f"""WITH {LIV}
                  SELECT SUM(EXISTS (SELECT 1 FROM ({D.FOLLOW_UP_VISIT}) f WHERE f.patient_id = e.patient_id
                                     AND f.event_date > e.end_date AND julianday(f.event_date) - julianday(e.end_date) <= 30)), COUNT(*)
                  FROM ({D.ED_VISIT_GROUP}) e WHERE substr(e.start_date, 1, 4) = '2029' AND e.end_date IS NOT NULL
                    AND e.patient_id IN (SELECT patient_id FROM living)"""))
    add(Q(B, 'mammogram_rate', "What's our mammography screening rate for women 50 to 74 over the last two years?", 'share', tolerance={'abs': 0.005},
          sql=f"""WITH {LIV} SELECT SUM(p.patient_id IN (SELECT patient_id FROM ({D.MAMMOGRAM_EVIDENCE}) WHERE event_date > '2028-05-27' AND event_date <= '{D.AS_OF}')), COUNT(*)
                  FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND p.sex_reported = 'F' AND {D.age_of('p')} BETWEEN 50 AND 74""",
          notes='women 50-74 living; screened = final mammogram result OR counted screening-mammogram claim (CPT 77067) in the 24 months ending as-of'))
    add(Q(B, 'colorectal_rate', "How many patients 45-75 are up to date on colorectal screening (FIT in the last year or colonoscopy in the last ten)?",
          'share', tolerance={'abs': 0.005},
          sql=f"""WITH {LIV} SELECT SUM(p.patient_id IN (
                      SELECT patient_id FROM cancer_screening WHERE test = 'FIT' AND {D.in_last_year('event_date')}
                      UNION SELECT patient_id FROM procedure_line WHERE cpt IN ('82274') AND counts_in_metrics = 1 AND {D.in_last_year('service_date')}
                      UNION SELECT patient_id FROM procedure_line WHERE cpt IN ('45378', '45380', '45385') AND counts_in_metrics = 1)), COUNT(*)
                  FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND {D.age_of('p')} BETWEEN 45 AND 75""",
          notes='claims history only starts 2026-09, so a colonoscopy "in the last ten years" can only be seen for the observed window; '
                'FIT results and FIT claims (82274) in the last year count'))
    add(Q(B, 'flu_65', "What percentage of patients 65 and older got a flu shot last season?", 'share', tolerance={'abs': 0.005},
          sql=f"""WITH {LIV} SELECT SUM(p.patient_id IN (SELECT patient_id FROM immunization WHERE vaccine LIKE 'influenza%' AND status = 'completed'
                                                         AND event_date >= '2029-07-01' AND event_date <= '{D.AS_OF}')), COUNT(*)
                  FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND {D.age_of('p')} >= 65""",
          notes='season = 2029-07-01 to as-of; any completed influenza product'))
    add(Q(B, 'awv_medicare', "How many Medicare patients completed an annual wellness visit in 2029?", 'count',
          sql=f"""WITH {LIV}, {LOB_OF}
                  SELECT COUNT(DISTINCT w.patient_id) FROM wellness_visit w JOIN lob l USING (patient_id)
                  WHERE w.awv = 1 AND w.status = 'completed' AND substr(w.event_date, 1, 4) = '2029' AND l.line_of_business IN ('medicare', 'dual', 'medigap')
                    AND w.patient_id IN (SELECT patient_id FROM living)""",
          notes='AWV = wellness_visit.awv = 1, completed, dated 2029; Medicare = line of business of the latest enrollment span'))

    add(Q(B, 'statin_diabetics', "What share of diabetics aged 40 to 75 filled a statin in the past year?", 'share', tolerance={'abs': 0.005},
          sql=f"""WITH {LIV}, {DIA} SELECT SUM(d.patient_id IN ({D.filled_class_last_year(['statin'])})), COUNT(*)
                  FROM has_diabetes d JOIN patient p USING (patient_id) WHERE {D.age_of('p')} BETWEEN 40 AND 75"""))
    add(Q(B, 'cad_no_statin', "List coronary artery disease patients with no statin fill in the last 12 months.", 'patient_set',
          sql=f"""WITH {LIV}, {CAD} SELECT patient_id FROM has_cad WHERE patient_id NOT IN ({D.filled_class_last_year(['statin'])})"""))
    add(Q(B, 'ckd_no_uacr', "How many CKD patients are missing a urine albumin-creatinine ratio in the last year?", 'count',
          sql=f"""WITH {LIV}, {CKD} SELECT COUNT(*) FROM has_ckd
                  WHERE patient_id NOT IN (SELECT patient_id FROM ({D.any_lab('uacr')}) WHERE {D.in_last_year('event_date')})""",
          notes='CKD includes the lab rule (two eGFR < 60 at least 90 days apart)'))
    add(Q(B, 'dm_ckd_no_raas', "Which diabetics with CKD are not on an ACE inhibitor or ARB?", 'patient_set',
          sql=f"""WITH {LIV}, {DIA}, {CKD} SELECT d.patient_id FROM has_diabetes d JOIN has_ckd c USING (patient_id)
                  WHERE d.patient_id NOT IN ({D.filled_class_last_year(['ace_inhibitor', 'arb', 'arni'])})""",
          notes='"not on" = no ACEi / ARB / ARNI fill in the last 12 months'))
    add(Q(B, 'copd_ed_last_year', "What share of COPD patients had an ED visit in the last year?", 'share', tolerance={'abs': 0.005},
          sql=f"""WITH {LIV}, {COPD} SELECT SUM(c.patient_id IN (SELECT patient_id FROM ({D.ED_VISITS}) WHERE {D.in_last_year('event_date')})), COUNT(*)
                  FROM has_copd c"""))
    add(Q(B, 'dep_phq9', "Among patients with depression, how many have no PHQ-9 on file in the last 12 months?", 'count',
          sql=f"""WITH {LIV}, {DEP} SELECT COUNT(*) FROM has_depression
                  WHERE patient_id NOT IN (SELECT patient_id FROM ({D.any_lab('phq9')}) WHERE {D.in_last_year('event_date')})"""))
    add(Q(B, 'asthma_no_controller', "Asthma patients with no inhaled corticosteroid fill in the past year — how many?", 'count',
          sql=f"""WITH {LIV}, {ASTH} SELECT COUNT(*) FROM has_asthma
                  WHERE patient_id NOT IN ({D.filled_class_last_year(['inhaled_corticosteroid'])})"""))
    add(Q(B, 'afib_anticoag', "Of the patients with atrial fibrillation, what fraction are on an anticoagulant?", 'share', tolerance={'abs': 0.005},
          sql=f"""WITH {LIV}, {AFIB} SELECT SUM(patient_id IN ({D.filled_class_last_year(['anticoagulant'])})), COUNT(*) FROM has_afib""",
          notes='anticoagulant fill in the last 12 months; stroke-risk eligibility (CHA2DS2-VASc) is not applied'))
    add(Q(B, 'frequent_ed', "Who are our frequent ED users — patients with 3 or more ED visits in the last year?", 'patient_set',
          sql=f"""WITH {LIV} SELECT patient_id FROM ({D.ED_VISITS}) WHERE {D.in_last_year('event_date')}
                  AND patient_id IN (SELECT patient_id FROM living) GROUP BY patient_id HAVING COUNT(*) >= 3"""))
    add(Q(B, 'htn_uncontrolled_no_visit', "Which hypertensive patients have a latest systolic of 160 or higher and haven't been seen in an office visit in the last 6 months?",
          'patient_set',
          sql=f"""WITH {LIV}, {HTN} SELECT h.patient_id FROM has_hypertension h
                  JOIN measurement_latest s ON s.patient_id = h.patient_id AND s.concept = 'systolic_bp'
                  WHERE s.value_canonical >= 160 AND h.patient_id NOT IN (
                      SELECT patient_id FROM ({D.FOLLOW_UP_VISIT}) WHERE event_date > '2029-11-27' AND event_date <= '{D.AS_OF}')""",
          notes='six months ending as-of start 2029-11-28; visit = definitions.FOLLOW_UP_VISIT'))
    add(Q(B, 'noshow_by_practice', "Which three practices had the most no-shows in the last year, and how many?", 'table', order_matters=True,
          sql=f"""SELECT organization_id AS practice, COUNT(*) AS no_shows FROM appointment
                  WHERE final_status = 'no_show' AND {D.in_last_year('scheduled_date')} AND organization_id IS NOT NULL
                    AND patient_id IN (SELECT patient_id FROM patient WHERE patient_id NOT IN (SELECT patient_id FROM death))
                  GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3""",
          notes='appointment.organization_id; living patients; ties broken by practice id'))
    add(Q(B, 'absent_cervical', "What is our cervical cancer screening rate for women 21 to 65?", 'absent',
          reason='The warehouse has no cervical-screening evidence: no Pap or HPV test results or CPT codes (cancer_screening holds only mammography and FIT; procedure lines carry no 88141-88175 / 87624).',
          notes='checked in procedure_line, cancer_screening and measurement'))
    return out
