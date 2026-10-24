"""Answer keys for the COMPOSITION development set (split `dev2`): tools/dev_set/compose_questions.jsonl.

    .venv/bin/python -m tools.dev_set.compose_keys            # (re)build compose_questions.jsonl
    .venv/bin/python -m tools.dev_set.compose_keys --check    # recompute every key and compare with the file (no write)
    .venv/bin/python -m tools.dev_set.compose_keys --summary  # counts per family / bucket

Why it exists: the original 100 dev questions had been tuned against for so long that a gain on them no longer says anything. This set
is written fresh from the semantic tables (not paraphrases of dev questions) to exercise COMPOSITION: condition combinations, condition x
lab, condition x medication, utilization windows, shares with explicit denominators, grouped breakdowns, rankings in both directions,
two-group comparisons, claims money, pharmacy, SDOH and two-turn follow-ups. Its split is `dev2` -- never `heldout`.

Every key is plain SQL over the semantic layer (read-only), written against tools/dev_set/definitions.py and the decision context
(clinical_core/agere/clinical/decision_context.py): living patients and duplicate charts once; age from the birth date at the as-of date;
latest lab unless the question says ever / any; condition evidence = EHR + problem list + counted claim diagnoses (+ HbA1c >= 6.5 for diabetes,
two eGFR < 60 90 days apart for CKD); Medicare = coverage_type in (medicare, dual, medigap); "on" a drug = a picked-up fill in the 12 months
ending at the as-of date; rolling year = (as_of - 12 months, as_of]; frequent ED = 3+ visits; high-cost = $50,000+ paid. Event counts
(ED / inpatient / observation) are over living, de-duplicated patients (utilization_event is an event-grain relation); money (paid, allowed,
PMPM) counts every member's claims. No product SQL builder is imported.

Conventions the keys fix (each stated in the question or in the key notes):
  payer type for patients  commercial / Medicare (medicare + dual + medigap) / Medicaid by patient.coverage_type; uninsured, secondary-coverage
                           and no-coverage patients are outside those three groups
  practice                 PCP practice on the patient row, else the attributing practice of the latest enrollment span (definitions.PRACTICE_CTE)
  claims line of business  payer_claim.line_of_business as stored (medicare, dual and medigap are separate lines)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.dev_set import definitions as D
from tools.dev_set.b5_insight import lit, n as fact_n, rank as fact_rank, rubric as fact_rubric, t as fact_t  # rubric constructors only (pure helpers)
from tools.dev_set.keys import Ctx, Q, finish, read as read_dev

HERE = Path(__file__).resolve().parent
OUT = HERE / 'compose_questions.jsonl'
SPLIT = 'dev2'
SEED = 20261024
PCQ, CPS, INS = 'population_clinical_quality', 'population_claims_pharmacy_sdoh', 'insight'
BUCKETS = (PCQ, CPS, INS)
SHORT = {PCQ: 'c3', CPS: 'c4', INS: 'c5'}
FAMILIES = ('condition_combo', 'condition_lab', 'condition_med', 'utilization_window', 'share_denominator', 'grouped_breakdown',
            'top_n', 'group_comparison', 'claims_money', 'pharmacy', 'sdoh', 'follow_up', 'absent')

LIV = D.LIVING_CTE
LIVQ = 'patient_id IN (SELECT patient_id FROM living)'
ADULT = f"{D.age_of('p')} >= 18"
# patient-level payer type: the decision context's Medicare rule (coverage_type medicare / dual / medigap)
PAYER_TYPE = ("CASE WHEN p.coverage_type IN ('medicare', 'dual', 'medigap') THEN 'medicare' WHEN p.coverage_type = 'medicaid' THEN 'medicaid' "
              "WHEN p.coverage_type = 'commercial' THEN 'commercial' END")
AGE_BAND = (f"CASE WHEN {D.age_of('p')} < 40 THEN 'under_40' WHEN {D.age_of('p')} < 65 THEN '40_to_64' ELSE '65_plus' END")
Y29, Y28 = '2029', '2028'


def cond(*names):
    return ', '.join(D.condition_cte(x) for x in names)


def W(*names, extra=''):
    """WITH clause: living + condition CTEs (+ any extra CTE text)."""
    parts = [LIV] + ([cond(*names)] if names else []) + ([extra] if extra else [])
    return 'WITH ' + ', '.join(parts)


def latest(concept, alias):
    return f"JOIN measurement_latest {alias} ON {alias}.patient_id = {{pid}} AND {alias}.concept = '{concept}'"


def on_drug(*classes):
    return f"({D.filled_class_last_year(classes)})"


def spend(where=''):
    return f"SELECT * FROM payer_claim WHERE counts_in_metrics = 1 {where}"


S29 = spend(f"AND {D.in_year('service_from')}")
S28 = spend(f"AND {D.in_year('service_from', Y28)}")
SLY = spend(f"AND {D.in_last_year('service_from')}")
ED = f"SELECT patient_id, event_date FROM utilization_event WHERE kind = 'ed_visit' AND {LIVQ}"
IP = f"SELECT patient_id, event_date FROM utilization_event WHERE kind = 'inpatient' AND {LIVQ}"
OBS = f"SELECT patient_id, event_date FROM utilization_event WHERE kind = 'observation' AND {LIVQ}"
PHARM = D.PHARMACY_FILLS


class Builder:
    def __init__(self, ctx):
        self.ctx, self.items = ctx, []

    def add(self, family, bucket, key, text, typ, **kw):
        assert family in FAMILIES, family
        q = Q(bucket, key, text, typ, **kw)
        self.items.append((family, q))

    def absent(self, bucket, key, text, reason, notes=''):
        self.add('absent', bucket, key, text, 'absent', reason=reason, notes=notes)


# ====================================================================================================================================
def build(ctx):
    b = Builder(ctx)
    a = b.add
    one = ctx.one

    # ---- 1. condition AND / OR / NOT ---------------------------------------------------------------------------------------------
    a('condition_combo', PCQ, 'copd_and_hf', "How many patients carry both a COPD and a heart failure diagnosis?", 'count',
      sql=f"{W('copd', 'heart_failure')} SELECT COUNT(*) FROM has_copd WHERE patient_id IN (SELECT patient_id FROM has_heart_failure)")
    a('condition_combo', PCQ, 'htn_not_dm', "Number of hypertensive patients who have no diabetes?", 'count',
      sql=f"{W('hypertension', 'diabetes')} SELECT COUNT(*) FROM has_hypertension WHERE patient_id NOT IN (SELECT patient_id FROM has_diabetes)",
      notes='diabetes evidence includes the HbA1c >= 6.5 lab rule, so a hypertensive with a high A1c is a diabetic')
    a('condition_combo', PCQ, 'afib_or_hf', "Count the patients that have atrial fibrillation or heart failure (either one).", 'count',
      sql=f"{W('afib', 'heart_failure')} SELECT COUNT(*) FROM (SELECT patient_id FROM has_afib UNION SELECT patient_id FROM has_heart_failure)")
    a('condition_combo', PCQ, 'dm_htn_ckd', "How many of our patients have diabetes, hypertension and chronic kidney disease all at once?", 'count',
      sql=f"{W('diabetes', 'hypertension', 'ckd')} SELECT COUNT(*) FROM has_diabetes WHERE patient_id IN (SELECT patient_id FROM has_hypertension) "
          f"AND patient_id IN (SELECT patient_id FROM has_ckd)")
    a('condition_combo', PCQ, 'asthma_xor_copd', "How many patients have asthma or COPD but not both?", 'count',
      sql=f"{W('asthma', 'copd')} SELECT (SELECT COUNT(*) FROM has_asthma WHERE patient_id NOT IN (SELECT patient_id FROM has_copd)) + "
          f"(SELECT COUNT(*) FROM has_copd WHERE patient_id NOT IN (SELECT patient_id FROM has_asthma))")
    a('condition_combo', PCQ, 'hf_afib_not_ckd', "Who has heart failure and atrial fibrillation but is not flagged with kidney disease?", 'patient_set',
      sql=f"{W('heart_failure', 'afib', 'ckd')} SELECT patient_id FROM has_heart_failure WHERE patient_id IN (SELECT patient_id FROM has_afib) "
          f"AND patient_id NOT IN (SELECT patient_id FROM has_ckd)")
    a('condition_combo', PCQ, 'two_of_three', "How many patients have at least two of these three: diabetes, CKD, heart failure?", 'count',
      sql=f"{W('diabetes', 'ckd', 'heart_failure')} SELECT COUNT(*) FROM (SELECT patient_id, COUNT(*) k FROM (SELECT patient_id FROM has_diabetes "
          f"UNION ALL SELECT patient_id FROM has_ckd UNION ALL SELECT patient_id FROM has_heart_failure) GROUP BY 1 HAVING k >= 2)")

    a('condition_combo', PCQ, 'dementia_no_dep', "Patients with dementia who do not have a depression diagnosis, how many?", 'count',
      sql=f"{W('dementia', 'depression')} SELECT COUNT(*) FROM has_dementia WHERE patient_id NOT IN (SELECT patient_id FROM has_depression)")
    a('condition_combo', PCQ, 'cad_no_lipid_no_obesity', "Among patients with coronary artery disease, how many have neither hyperlipidemia nor obesity on record?", 'count',
      sql=f"{W('cad', 'hyperlipidemia', 'obesity')} SELECT COUNT(*) FROM has_cad WHERE patient_id NOT IN (SELECT patient_id FROM has_hyperlipidemia) "
          f"AND patient_id NOT IN (SELECT patient_id FROM has_obesity)")

    # ---- 2. condition x lab threshold (latest vs ever) ----------------------------------------------------------------------------
    a('condition_lab', PCQ, 'dm_a1c_10', "How many diabetics' most recent A1c is 10 or higher?", 'count',
      sql=f"{W('diabetes')} SELECT COUNT(*) FROM has_diabetes d JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' WHERE m.value_canonical >= 10")
    a('condition_lab', PCQ, 'dm_a1c_ever_11', "How many diabetic patients have ever had an A1c of 11 or above on any draw?", 'count',
      sql=f"{W('diabetes')} SELECT COUNT(DISTINCT d.patient_id) FROM has_diabetes d JOIN ({D.any_lab('hba1c')}) m ON m.patient_id = d.patient_id WHERE m.value >= 11",
      notes='ANY result (ever), not the latest')
    a('condition_lab', PCQ, 'ckd_egfr30', "Of our CKD patients, how many have a latest eGFR under 30?", 'count',
      sql=f"{W('ckd')} SELECT COUNT(*) FROM has_ckd c JOIN measurement_latest m ON m.patient_id = c.patient_id AND m.concept = 'egfr' WHERE m.value_canonical < 30")
    a('condition_lab', PCQ, 'htn_sbp150_share', "What share of hypertensive patients have a latest systolic pressure of 150 or more?", 'share',
      sql=f"{W('hypertension')} SELECT SUM(m.value_canonical >= 150), COUNT(*) FROM has_hypertension h JOIN measurement_latest m ON m.patient_id = h.patient_id AND m.concept = 'systolic_bp'",
      tolerance={'abs': 0.005}, notes='denominator = living hypertensives with a latest systolic reading')
    a('condition_lab', PCQ, 'dm_ckd_ldl130', "Which diabetics with CKD have a latest LDL over 130?", 'patient_set',
      sql=f"{W('diabetes', 'ckd')} SELECT d.patient_id FROM has_diabetes d JOIN has_ckd c USING (patient_id) JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'ldl' WHERE m.value_canonical > 130")
    a('condition_lab', PCQ, 'obese_a1c_latest_vs_ever', "For obese patients, how many have a latest A1c of 6.5 or more, and how many have ever had an A1c of 6.5 or more?", 'table',
      sql=f"{W('obesity')} SELECT (SELECT COUNT(*) FROM has_obesity o JOIN measurement_latest m ON m.patient_id = o.patient_id AND m.concept = 'hba1c' WHERE m.value_canonical >= 6.5) AS latest_6_5_plus, "
          f"(SELECT COUNT(DISTINCT o.patient_id) FROM has_obesity o JOIN ({D.any_lab('hba1c')}) m ON m.patient_id = o.patient_id WHERE m.value >= 6.5) AS ever_6_5_plus",
      notes='two columns: latest-result count then any-result count; ever >= latest by construction')

    a('condition_lab', PCQ, 'cad_ldl70_share', "What fraction of CAD patients have their latest LDL under 70?", 'share',
      sql=f"{W('cad')} SELECT SUM(m.value_canonical < 70), COUNT(*) FROM has_cad c JOIN measurement_latest m ON m.patient_id = c.patient_id AND m.concept = 'ldl'",
      tolerance={'abs': 0.005}, notes='denominator = living CAD patients with a latest LDL')
    a('condition_lab', PCQ, 'hf_bnp_ever', "How many heart failure patients have ever had an NT-proBNP above 1000?", 'count',
      sql=f"{W('heart_failure')} SELECT COUNT(DISTINCT h.patient_id) FROM has_heart_failure h JOIN ({D.any_lab('nt_probnp')}) m ON m.patient_id = h.patient_id WHERE m.value > 1000",
      notes='any result, ever')

    # ---- 3. condition x medication (on / not on = picked-up fill in the last 12 months) -----------------------------------------------
    a('condition_med', PCQ, 'htn_on_acei', "How many hypertensive patients are currently on an ACE inhibitor?", 'count',
      sql=f"{W('hypertension')} SELECT COUNT(*) FROM has_hypertension WHERE patient_id IN {on_drug('ace_inhibitor')}", notes='on = a picked-up fill in the last 12 months')
    a('condition_med', PCQ, 'dm_not_metformin', "Diabetics who are not taking metformin - how many?", 'count',
      sql=f"{W('diabetes')} SELECT COUNT(*) FROM has_diabetes WHERE patient_id NOT IN {on_drug('metformin')}")
    a('condition_med', PCQ, 'hf_loop_share', "What share of our heart failure patients are on a loop diuretic?", 'share',
      sql=f"{W('heart_failure')} SELECT SUM(patient_id IN {on_drug('loop_diuretic')}), COUNT(*) FROM has_heart_failure", tolerance={'abs': 0.005})
    a('condition_med', PCQ, 'copd_on_lama', "List the COPD patients who are on a long-acting muscarinic antagonist (LAMA).", 'patient_set',
      sql=f"{W('copd')} SELECT patient_id FROM has_copd WHERE patient_id IN {on_drug('lama')}")
    a('condition_med', PCQ, 'cad_no_statin_no_antiplatelet', "How many CAD patients are on neither a statin nor an antiplatelet?", 'count',
      sql=f"{W('cad')} SELECT COUNT(*) FROM has_cad WHERE patient_id NOT IN {on_drug('statin')} AND patient_id NOT IN {on_drug('antiplatelet')}")
    a('condition_med', PCQ, 'dm_glp1_or_sglt2_share', "What percent of diabetics have filled a GLP-1 or an SGLT2 inhibitor in the last 12 months?", 'share',
      sql=f"{W('diabetes')} SELECT SUM(patient_id IN {on_drug('glp1_agonist', 'sglt2_inhibitor')}), COUNT(*) FROM has_diabetes", tolerance={'abs': 0.005})
    a('condition_med', PCQ, 'dm_a1c9_no_intensification', "Of the diabetics with a latest A1c above 9, how many are on neither insulin nor a GLP-1?", 'count',
      sql=f"{W('diabetes')} SELECT COUNT(*) FROM has_diabetes d JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' "
          f"WHERE m.value_canonical > 9 AND d.patient_id NOT IN {on_drug('insulin')} AND d.patient_id NOT IN {on_drug('glp1_agonist')}")

    # ---- 4. utilization: calendar year vs rolling year ----------------------------------------------------------------------------
    a('utilization_window', PCQ, 'ed_patients_2029', "How many distinct patients went to the ED at least once in calendar year 2029?", 'count',
      sql=f"{W()} SELECT COUNT(DISTINCT patient_id) FROM ({ED}) WHERE {D.in_year('event_date')}")
    a('utilization_window', PCQ, 'ed_patients_ly', "How many distinct patients had an ED visit in the last year?", 'count',
      sql=f"{W()} SELECT COUNT(DISTINCT patient_id) FROM ({ED}) WHERE {D.in_last_year('event_date')}", notes='rolling 12 months ending 2030-05-27')
    a('utilization_window', PCQ, 'ed_visits_2029', "Total ED visits among our patients in 2029?", 'count',
      sql=f"{W()} SELECT COUNT(*) FROM ({ED}) WHERE {D.in_year('event_date')}", notes='visits of living, de-duplicated patients')
    a('utilization_window', PCQ, 'ed_visits_ly', "Total ED visits among our patients over the past 12 months?", 'count',
      sql=f"{W()} SELECT COUNT(*) FROM ({ED}) WHERE {D.in_last_year('event_date')}")
    a('utilization_window', PCQ, 'ip_2029', "How many inpatient admissions did we have in calendar 2029?", 'count',
      sql=f"{W()} SELECT COUNT(*) FROM ({IP}) WHERE {D.in_year('event_date')}")
    a('utilization_window', PCQ, 'ip_ly', "How many inpatient admissions have we had in the last twelve months?", 'count',
      sql=f"{W()} SELECT COUNT(*) FROM ({IP}) WHERE {D.in_last_year('event_date')}")
    a('utilization_window', PCQ, 'ed_and_ip_ly', "How many patients had both an ED visit and an inpatient admission in the past year?", 'count',
      sql=f"{W()} SELECT COUNT(*) FROM (SELECT patient_id FROM ({ED}) WHERE {D.in_last_year('event_date')}) WHERE patient_id IN "
          f"(SELECT patient_id FROM ({IP}) WHERE {D.in_last_year('event_date')})")
    a('utilization_window', PCQ, 'ed_2029_not_ly', "How many patients visited the ED in 2029 but not at all in the last 12 months?", 'count',
      sql=f"{W()} SELECT COUNT(DISTINCT patient_id) FROM ({ED}) WHERE {D.in_year('event_date')} AND patient_id NOT IN "
          f"(SELECT patient_id FROM ({ED}) WHERE {D.in_last_year('event_date')})",
      notes='calendar 2029 versus the rolling year 2029-05-28..2030-05-27: the two windows overlap from 2029-05-28 to 2029-12-31')

    # ---- 5. shares with explicit denominators --------------------------------------------------------------------------------------
    a('share_denominator', PCQ, 'htn_also_dm', "Of the patients with hypertension, what share also have diabetes?", 'share',
      sql=f"{W('hypertension', 'diabetes')} SELECT SUM(patient_id IN (SELECT patient_id FROM has_diabetes)), COUNT(*) FROM has_hypertension", tolerance={'abs': 0.005})
    a('share_denominator', PCQ, 'awv_medicare_share', "What share of Medicare patients (including dual and Medigap) completed an annual wellness visit in the last 12 months?", 'share',
      sql=f"{W()} SELECT SUM(p.patient_id IN (SELECT patient_id FROM wellness_visit WHERE awv = 1 AND status = 'completed' AND {D.in_last_year('event_date')})), COUNT(*) "
          f"FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND p.coverage_type IN ('medicare', 'dual', 'medigap')", tolerance={'abs': 0.005},
      notes='AWV = wellness_visit.awv = 1, completed; Medicare = medicare + dual + medigap coverage')
    a('share_denominator', PCQ, 'seniors_covid', "What percent of patients age 65 or older have a COVID-19 vaccine on record in the last 12 months?", 'share',
      sql=f"{W()} SELECT SUM(p.patient_id IN (SELECT patient_id FROM immunization WHERE vaccine LIKE 'COVID-19%' AND status = 'completed' AND {D.in_last_year('event_date')})), COUNT(*) "
          f"FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND {D.age_of('p')} >= 65", tolerance={'abs': 0.005})
    a('share_denominator', PCQ, 'ckd_egfr_tested_share', "What fraction of CKD patients have an eGFR result dated in the last year?", 'share',
      sql=f"{W('ckd')} SELECT SUM(patient_id IN (SELECT patient_id FROM ({D.any_lab('egfr')}) WHERE {D.in_last_year('event_date')})), COUNT(*) FROM has_ckd",
      tolerance={'abs': 0.005})

    # ---- 6. grouped breakdowns -----------------------------------------------------------------------------------------------------
    a('grouped_breakdown', PCQ, 'dm_by_sex', "How many diabetic patients are there for each sex?", 'table',
      sql=f"{W('diabetes')} SELECT p.sex_reported AS sex, COUNT(*) AS patients FROM has_diabetes d JOIN patient p USING (patient_id) GROUP BY 1 ORDER BY 1")
    a('grouped_breakdown', PCQ, 'htn_by_age_band', "Break hypertensive patients out by age: under 40, 40 to 64, and 65 and older.", 'table',
      sql=f"{W('hypertension')} SELECT {AGE_BAND} AS age_band, COUNT(*) AS patients FROM has_hypertension h JOIN patient p USING (patient_id) GROUP BY 1 ORDER BY 1",
      notes='age in whole years at 2030-05-27 from the birth date')
    a('grouped_breakdown', PCQ, 'ckd_by_payer', "CKD patients by payer type - commercial, Medicare (including dual and Medigap) and Medicaid.", 'table',
      sql=f"{W('ckd')} SELECT {PAYER_TYPE} AS payer_type, COUNT(*) AS patients FROM has_ckd c JOIN patient p USING (patient_id) WHERE {PAYER_TYPE} IS NOT NULL GROUP BY 1 ORDER BY 1",
      notes='coverage_type medicare + dual + medigap = Medicare; uninsured, secondary-coverage and no-coverage patients are outside the three groups')
    a('grouped_breakdown', PCQ, 'ed_patients_by_practice', "For each primary-care practice, how many patients had an ED visit in the last 12 months?", 'table',
      sql=f"{W(extra=D.PRACTICE_CTE)} SELECT pr.practice_id AS practice, COUNT(DISTINCT e.patient_id) AS patients FROM ({ED}) e JOIN practice_of pr USING (patient_id) "
          f"WHERE {D.in_last_year('e.event_date')} AND pr.practice_id IS NOT NULL AND pr.practice_id LIKE 'WH-PRACTICE-%' GROUP BY 1 ORDER BY 1",
      notes='practice = PCP practice, else attributing practice of the latest enrollment span; primary-care practices only (WH-PRACTICE-n)')
    a('grouped_breakdown', PCQ, 'ip_2029_by_age_band', "Inpatient admissions in 2029 split by age group: under 40, 40-64, 65+.", 'table',
      sql=f"{W()} SELECT {AGE_BAND} AS age_band, COUNT(*) AS admissions FROM ({IP}) u JOIN patient p USING (patient_id) WHERE {D.in_year('u.event_date')} GROUP BY 1 ORDER BY 1")
    a('grouped_breakdown', PCQ, 'copd_by_payer_ed', "By payer type (commercial, Medicare, Medicaid), how many COPD patients visited the ED in the last year?", 'table',
      sql=f"{W('copd')} SELECT {PAYER_TYPE} AS payer_type, COUNT(*) AS patients FROM has_copd c JOIN patient p USING (patient_id) WHERE {PAYER_TYPE} IS NOT NULL "
          f"AND c.patient_id IN (SELECT patient_id FROM ({ED}) WHERE {D.in_last_year('event_date')}) GROUP BY 1 ORDER BY 1")
    a('grouped_breakdown', PCQ, 'a1c_control_by_sex', "For diabetics, what share of women and of men have a latest A1c under 7?", 'table',
      sql=f"{W('diabetes')} SELECT p.sex_reported AS sex, ROUND(1.0 * SUM(m.value_canonical < 7) / COUNT(*), 4) AS share_under_7 FROM has_diabetes d JOIN patient p USING (patient_id) "
          f"JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' GROUP BY 1 ORDER BY 1", tolerance={'abs': 0.005},
      notes='denominator per sex = living diabetics with a latest A1c')

    # ---- 7. top-N rankings, highest AND lowest ---------------------------------------------------------------------------------------
    PR = "pr.practice_id LIKE 'WH-PRACTICE-%'"
    a('top_n', PCQ, 'practices_most_diabetics', "Which three practices have the most diabetic patients?", 'table', order_matters=True,
      sql=f"{W('diabetes', extra=D.PRACTICE_CTE)} SELECT pr.practice_id AS practice, COUNT(*) AS diabetics FROM has_diabetes d JOIN practice_of pr USING (patient_id) WHERE {PR} "
          f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3")
    a('top_n', PCQ, 'practices_fewest_diabetics', "And which two practices have the fewest diabetic patients?", 'table', order_matters=True,
      sql=f"{W('diabetes', extra=D.PRACTICE_CTE)} SELECT pr.practice_id AS practice, COUNT(*) AS diabetics FROM has_diabetes d JOIN practice_of pr USING (patient_id) WHERE {PR} "
          f"GROUP BY 1 ORDER BY 2, 1 LIMIT 2")
    a('top_n', PCQ, 'practice_best_statin', "Which practice has the highest share of its diabetics aged 40 to 75 on a statin (a fill in the last 12 months)?", 'table', order_matters=True,
      sql=f"{W('diabetes', extra=D.PRACTICE_CTE)} SELECT pr.practice_id AS practice, ROUND(1.0 * SUM(d.patient_id IN {on_drug('statin')}) / COUNT(*), 4) AS statin_share "
          f"FROM has_diabetes d JOIN practice_of pr USING (patient_id) JOIN patient p USING (patient_id) WHERE {PR} AND {D.age_of('p')} BETWEEN 40 AND 75 GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1", tolerance={'abs': 0.005})
    a('top_n', PCQ, 'practice_worst_statin', "And which practice has the lowest statin share among its diabetics 40 to 75?", 'table', order_matters=True,
      sql=f"{W('diabetes', extra=D.PRACTICE_CTE)} SELECT pr.practice_id AS practice, ROUND(1.0 * SUM(d.patient_id IN {on_drug('statin')}) / COUNT(*), 4) AS statin_share "
          f"FROM has_diabetes d JOIN practice_of pr USING (patient_id) JOIN patient p USING (patient_id) WHERE {PR} AND {D.age_of('p')} BETWEEN 40 AND 75 GROUP BY 1 ORDER BY 2, 1 LIMIT 1", tolerance={'abs': 0.005},
      notes='denominator per practice = living diabetics aged 40-75')
    a('top_n', PCQ, 'drug_classes_top5', "What are the five most-filled drug classes in the last 12 months, by number of fills?", 'table', order_matters=True,
      sql=f"SELECT drug_class, COUNT(*) AS fills FROM fill WHERE {D.in_last_year('fill_date')} AND drug_class IS NOT NULL AND patient_id IN ({D.LIVING}) GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5",
      notes='rows of the merged fill table of living patients')
    a('top_n', PCQ, 'drug_classes_bottom3', "Among drug classes with at least 100 fills in the last year, which three have the fewest fills?", 'table', order_matters=True,
      sql=f"SELECT drug_class, COUNT(*) AS fills FROM fill WHERE {D.in_last_year('fill_date')} AND drug_class IS NOT NULL AND patient_id IN ({D.LIVING}) "
          f"GROUP BY 1 HAVING COUNT(*) >= 100 ORDER BY 2, 1 LIMIT 3")
    a('top_n', PCQ, 'practices_ed_rate_high', "Top three practices by ED visits per 1,000 patients over the last 12 months (patients = living patients of the practice).", 'table', order_matters=True,
      sql=f"{W(extra=D.PRACTICE_CTE)} SELECT pr.practice_id AS practice, ROUND(1000.0 * SUM((SELECT COUNT(*) FROM ({ED}) e WHERE e.patient_id = pr.patient_id AND {D.in_last_year('e.event_date')})) / COUNT(*), 1) AS ed_per_1000 "
          f"FROM practice_of pr WHERE pr.patient_id IN (SELECT patient_id FROM living) AND {PR} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3", tolerance={'rel': 0.01, 'abs': 0.5})
    a('top_n', PCQ, 'practices_ed_rate_low', "Which two practices have the lowest ED visits per 1,000 patients in the last 12 months?", 'table', order_matters=True,
      sql=f"{W(extra=D.PRACTICE_CTE)} SELECT pr.practice_id AS practice, ROUND(1000.0 * SUM((SELECT COUNT(*) FROM ({ED}) e WHERE e.patient_id = pr.patient_id AND {D.in_last_year('e.event_date')})) / COUNT(*), 1) AS ed_per_1000 "
          f"FROM practice_of pr WHERE pr.patient_id IN (SELECT patient_id FROM living) AND {PR} GROUP BY 1 ORDER BY 2, 1 LIMIT 2", tolerance={'rel': 0.01, 'abs': 0.5})

    # ---- 8. comparisons between two groups --------------------------------------------------------------------------------------------
    a('group_comparison', PCQ, 'a1c9_by_sex', "Compare women and men with diabetes on the share whose latest A1c is above 9.", 'table',
      sql=f"{W('diabetes')} SELECT p.sex_reported AS sex, ROUND(1.0 * SUM(m.value_canonical > 9) / COUNT(*), 4) AS share_above_9 FROM has_diabetes d JOIN patient p USING (patient_id) "
          f"JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' GROUP BY 1 ORDER BY 1", tolerance={'abs': 0.005})
    a('group_comparison', PCQ, 'ed_medicare_vs_medicaid', "Do Medicare patients or Medicaid patients have more ED visits per patient in the last year? Give the visits per 1,000 for each.", 'table',
      sql=f"{W()} SELECT {PAYER_TYPE} AS payer_type, ROUND(1000.0 * SUM((SELECT COUNT(*) FROM ({ED}) e WHERE e.patient_id = p.patient_id AND {D.in_last_year('e.event_date')})) / COUNT(*), 1) AS ed_per_1000 "
          f"FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND ({PAYER_TYPE}) IN ('medicare', 'medicaid') GROUP BY 1 ORDER BY 1", tolerance={'rel': 0.01, 'abs': 0.5},
      notes='Medicare = medicare + dual + medigap coverage; denominator = living patients of the group')
    a('group_comparison', PCQ, 'ed_ckd_vs_dm_no_ckd', "How do ED visits per 1,000 patients over the last year compare between CKD patients and diabetics without CKD?", 'table',
      sql=f"{W('ckd', 'diabetes')}, ed_ly AS (SELECT patient_id, COUNT(*) n FROM ({ED}) WHERE {D.in_last_year('event_date')} GROUP BY 1) "
          f"SELECT g AS grp, COUNT(*) AS patients, ROUND(1000.0 * SUM(COALESCE(n, 0)) / COUNT(*), 1) AS ed_per_1000 FROM (SELECT 'ckd' g, patient_id FROM has_ckd UNION ALL "
          f"SELECT 'diabetes_without_ckd', patient_id FROM has_diabetes WHERE patient_id NOT IN (SELECT patient_id FROM has_ckd)) x LEFT JOIN ed_ly USING (patient_id) GROUP BY g ORDER BY g",
      tolerance={'rel': 0.01, 'abs': 0.5})
    a('group_comparison', PCQ, 'statin_dm_men_women', "Among diabetics 40 to 75, is a woman or a man more likely to have a statin fill in the last 12 months? Give both shares.", 'table',
      sql=f"{W('diabetes')} SELECT p.sex_reported AS sex, ROUND(1.0 * SUM(d.patient_id IN {on_drug('statin')}) / COUNT(*), 4) AS statin_share FROM has_diabetes d JOIN patient p USING (patient_id) "
          f"WHERE {D.age_of('p')} BETWEEN 40 AND 75 GROUP BY 1 ORDER BY 1", tolerance={'abs': 0.005})
    a('group_comparison', PCQ, 'admit_hf_vs_copd', "Which group is admitted more often in 2029: heart failure patients or COPD patients? Give admissions per 100 patients for each.", 'table',
      sql=f"{W('heart_failure', 'copd')} SELECT g AS grp, COUNT(*) AS patients, ROUND(100.0 * SUM((SELECT COUNT(*) FROM ({IP}) i WHERE i.patient_id = x.patient_id AND {D.in_year('i.event_date')})) / COUNT(*), 1) AS admissions_per_100 "
          f"FROM (SELECT 'heart_failure' g, patient_id FROM has_heart_failure UNION ALL SELECT 'copd', patient_id FROM has_copd) x GROUP BY g ORDER BY g",
      tolerance={'rel': 0.01, 'abs': 0.5}, notes='a patient with both conditions is in both groups')
    a('group_comparison', PCQ, 'a1c_testing_medicare_commercial', "What share of diabetics have an A1c result in the last 12 months: Medicare versus commercial?", 'table',
      sql=f"{W('diabetes')} SELECT {PAYER_TYPE} AS payer_type, ROUND(1.0 * SUM(d.patient_id IN (SELECT patient_id FROM ({D.any_lab('hba1c')}) WHERE {D.in_last_year('event_date')})) / COUNT(*), 4) AS tested_share "
          f"FROM has_diabetes d JOIN patient p USING (patient_id) WHERE ({PAYER_TYPE}) IN ('medicare', 'commercial') GROUP BY 1 ORDER BY 1", tolerance={'abs': 0.005})

    # ---- 9. claims money --------------------------------------------------------------------------------------------------------------
    a('claims_money', CPS, 'paid_28_29', "What was total medical paid in 2028 and in 2029?", 'table', tolerance={'rel': 0.001, 'abs': 1.0},
      sql=f"SELECT substr(service_from, 1, 4) AS year, ROUND(SUM(paid_cents) / 100.0, 2) AS paid_usd FROM payer_claim WHERE counts_in_metrics = 1 AND substr(service_from, 1, 4) IN ('2028', '2029') GROUP BY 1 ORDER BY 1")
    a('claims_money', CPS, 'paid_ly', "How much have we paid on medical claims over the last 12 months?", 'value', unit='USD', tolerance={'rel': 0.001, 'abs': 1.0},
      sql=f"SELECT SUM(paid_cents) / 100.0 FROM ({SLY})", notes='service_from in the rolling year (2029-05-27, 2030-05-27]')
    a('claims_money', CPS, 'pmpm_medicaid_2029', "What is the Medicaid PMPM for medical claims in 2029?", 'value', unit='USD', tolerance={'rel': 0.005, 'abs': 0.5},
      sql=f"SELECT (SELECT SUM(paid_cents) / 100.0 FROM ({S29}) WHERE line_of_business = 'medicaid') / (SELECT COUNT(*) FROM member_month WHERE substr(month, 1, 4) = '{Y29}' AND line_of_business = 'medicaid')",
      notes='paid / member-month rows of the same line in 2029')
    a('grouped_breakdown', CPS, 'paid_by_product_2029', "Break 2029 medical paid down by product (Medicare Advantage vs FFS, marketplace, and so on).", 'table', tolerance={'rel': 0.001, 'abs': 1.0},
      sql=f"SELECT product, ROUND(SUM(paid_cents) / 100.0, 2) AS paid_usd FROM ({S29}) GROUP BY 1 ORDER BY 2 DESC, 1")
    a('claims_money', CPS, 'high_cost_ly', "How many living patients accumulated $50,000 or more in paid claims over the past 12 months?", 'count',
      sql=f"{W()} SELECT COUNT(*) FROM (SELECT patient_id FROM ({SLY}) WHERE {LIVQ} GROUP BY patient_id HAVING SUM(paid_cents) >= 5000000)",
      notes='rolling year, high-cost = paid >= $50,000')
    a('grouped_breakdown', CPS, 'high_cost_by_lob_2029', "Of the 2029 high-cost claimants ($50k+ paid), how many are in each line of business?", 'table',
      sql=f"{W()}, hc AS (SELECT patient_id FROM ({S29}) WHERE {LIVQ} GROUP BY patient_id HAVING SUM(paid_cents) >= 5000000) "
          f"SELECT COALESCE(p.line_of_business, 'none') AS line_of_business, COUNT(*) AS patients FROM hc JOIN patient p USING (patient_id) GROUP BY 1 ORDER BY 2 DESC, 1",
      notes='line of business as stored on the patient row; a claimant with no line on file is "none"')
    a('group_comparison', CPS, 'avg_paid_oon_vs_in', "What's the average paid amount per medical claim in 2029, out of network versus in network?", 'table', tolerance={'rel': 0.005, 'abs': 0.5},
      sql=f"SELECT network_status, ROUND(AVG(paid_cents) / 100.0, 2) AS avg_paid_usd FROM ({S29}) WHERE final_status = 'paid' GROUP BY 1 ORDER BY 1",
      notes='paid claims only (denied claims pay nothing and would dilute the average)')
    a('claims_money', CPS, 'dm_spend_per_patient', "How much did the average living diabetic cost us in medical paid claims in 2029?", 'value', unit='USD', tolerance={'rel': 0.005, 'abs': 0.5},
      sql=f"{W('diabetes')} SELECT ROUND(SUM(COALESCE((SELECT SUM(paid_cents) FROM ({S29}) c WHERE c.patient_id = d.patient_id), 0)) / 100.0 / COUNT(*), 2) FROM has_diabetes d",
      notes='total 2029 paid of living diabetics divided by all living diabetics (zero-claim diabetics included)')
    a('top_n', CPS, 'lob_lowest_pmpm', "Among commercial, Medicare, Medicaid, dual and Medigap, which line of business had the lowest medical PMPM in 2029, and what was it?", 'table', tolerance={'rel': 0.005, 'abs': 0.5},
      sql=f"WITH s AS (SELECT line_of_business, SUM(paid_cents) / 100.0 AS paid FROM ({S29}) GROUP BY 1), m AS (SELECT line_of_business, COUNT(*) AS mm FROM member_month WHERE substr(month, 1, 4) = '{Y29}' GROUP BY 1) "
          f"SELECT s.line_of_business, ROUND(s.paid / m.mm, 2) AS pmpm_usd FROM s JOIN m USING (line_of_business) WHERE s.line_of_business IN ('commercial', 'medicare', 'medicaid', 'dual', 'medigap') ORDER BY 2, 1 LIMIT 1")
    a('top_n', CPS, 'payers_top3_paid', "Top three payers by 2029 paid amount, with the totals.", 'table', order_matters=True, tolerance={'rel': 0.001, 'abs': 1.0},
      sql=f"SELECT payer_id, ROUND(SUM(paid_cents) / 100.0, 2) AS paid_usd FROM ({S29}) GROUP BY 1 ORDER BY SUM(paid_cents) DESC, 1 LIMIT 3")
    a('claims_money', CPS, 'patient_resp_2029', "How much did members owe out of pocket (patient responsibility) on 2029 medical claims?", 'value', unit='USD', tolerance={'rel': 0.001, 'abs': 1.0},
      sql=f"SELECT SUM(patient_responsibility_cents) / 100.0 FROM ({S29}) WHERE final_status = 'paid'", notes='paid counted claims; reversals and denials carry no member cost')
    a('claims_money', CPS, 'er_vs_office_paid', "In 2029, how much was paid on emergency-room claims (place of service 23) compared with office claims (place of service 11)?", 'table', tolerance={'rel': 0.001, 'abs': 1.0},
      sql=f"SELECT place_of_service, ROUND(SUM(paid_cents) / 100.0, 2) AS paid_usd FROM ({S29}) WHERE place_of_service IN ('23', '11') GROUP BY 1 ORDER BY 1")
    a('claims_money', CPS, 'high_cost_share_ly', "Is more than a quarter of the last 12 months of medical spend concentrated in patients with $50k+ paid?", 'yes_no',
      sql=f"{W()}, hc AS (SELECT patient_id FROM ({SLY}) WHERE {LIVQ} GROUP BY patient_id HAVING SUM(paid_cents) >= 5000000) "
          f"SELECT 4.0 * (SELECT SUM(paid_cents) FROM ({SLY}) WHERE patient_id IN (SELECT patient_id FROM hc)) > (SELECT SUM(paid_cents) FROM ({SLY}))",
      notes='share of ALL counted paid dollars in the rolling year that belongs to high-cost living claimants, compared with 25%')

    # ---- 10. pharmacy fills / rejects ----------------------------------------------------------------------------------------------------
    a('pharmacy', CPS, 'fills_2029', "How many pharmacy fills were paid in 2029?", 'count',
      sql=f"SELECT COUNT(*) FROM ({PHARM}) WHERE {D.in_year('fill_date')}", notes='counts_as_fill = 1 (paid, not reversed / recouped)')
    a('pharmacy', CPS, 'fills_ly', "Count of paid prescription fills over the last 12 months?", 'count',
      sql=f"SELECT COUNT(*) FROM ({PHARM}) WHERE {D.in_last_year('fill_date')}")
    a('top_n', CPS, 'reject_codes_top3', "Top three pharmacy reject codes in 2029 and how many times each occurred.", 'table', order_matters=True,
      sql=f"SELECT reject_code, COUNT(*) AS rejects FROM payer_pharmacy_claim WHERE claim_status = 'rejected' AND {D.in_year('fill_date')} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3")
    a('group_comparison', CPS, 'reject_rate_by_type', "Compare the rejected-claim rate for mail-order versus retail pharmacies in 2029.", 'table', tolerance={'abs': 0.002},
      sql=f"SELECT pharmacy_type, ROUND(1.0 * SUM(claim_status = 'rejected') / COUNT(*), 4) AS reject_rate FROM payer_pharmacy_claim WHERE claim_status IN ('paid', 'rejected') "
          f"AND {D.in_year('fill_date')} AND pharmacy_type IN ('mail_order_pharmacy', 'retail_pharmacy') GROUP BY 1 ORDER BY 1",
      notes='attempts = paid + rejected claims; specialty pharmacy excluded (it has almost no claims)')
    a('group_comparison', CPS, 'avg_plan_paid_brand_vs_generic', "How much does the plan pay per fill on brand drugs versus generics in 2029 on average?", 'table', tolerance={'rel': 0.005, 'abs': 0.5},
      sql=f"SELECT formulary_tier, ROUND(AVG(plan_paid_cents) / 100.0, 2) AS avg_plan_paid_usd FROM ({PHARM}) WHERE {D.in_year('fill_date')} AND formulary_tier IN ('brand', 'generic') GROUP BY 1 ORDER BY 1")
    a('grouped_breakdown', CPS, 'plan_paid_by_tier_ly', "Plan-paid pharmacy dollars by formulary tier over the last 12 months?", 'table', tolerance={'rel': 0.001, 'abs': 1.0},
      sql=f"SELECT formulary_tier, ROUND(SUM(plan_paid_cents) / 100.0, 2) AS plan_paid_usd FROM ({PHARM}) WHERE {D.in_last_year('fill_date')} AND formulary_tier IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1",
      notes='fills with no tier on the claim are not assigned to a tier')
    a('top_n', CPS, 'top_drugs_plan_paid', "Which five drugs cost the plan the most in 2029 (plan-paid, by drug name)?", 'table', order_matters=True, tolerance={'rel': 0.001, 'abs': 1.0},
      sql=f"SELECT drug_name, ROUND(SUM(plan_paid_cents) / 100.0, 2) AS plan_paid_usd FROM ({PHARM}) WHERE {D.in_year('fill_date')} GROUP BY 1 ORDER BY SUM(plan_paid_cents) DESC, 1 LIMIT 5")
    a('share_denominator', CPS, 'mail_order_share_ly', "What share of fills in the last year came through mail order?", 'share', tolerance={'abs': 0.005},
      sql=f"SELECT SUM(pharmacy_type = 'mail_order_pharmacy'), COUNT(*) FROM ({PHARM}) WHERE {D.in_last_year('fill_date')}")
    a('pharmacy', CPS, 'patient_pay_ly', "How much did patients pay out of pocket at the pharmacy in the last 12 months?", 'value', unit='USD', tolerance={'rel': 0.001, 'abs': 1.0},
      sql=f"SELECT SUM(patient_pay_cents) / 100.0 FROM ({PHARM}) WHERE {D.in_last_year('fill_date')}")

    # ---- 11. SDOH (SVI, screened needs, purchased data) ------------------------------------------------------------------------------------
    SVI = "JOIN patient_svi s ON s.patient_id = {pid}"
    a('sdoh', CPS, 'high_svi_diabetics', "How many diabetics live in high-vulnerability areas (SVI 75 or above)?", 'count',
      sql=f"{W('diabetes')} SELECT COUNT(*) FROM has_diabetes d JOIN patient_svi s USING (patient_id) WHERE s.svi_overall >= 75")
    a('sdoh', CPS, 'a1c9_by_svi', "Among diabetics, compare the share with latest A1c above 9 in high-SVI (75+) and low-SVI (under 25) areas.", 'table', tolerance={'abs': 0.005},
      sql=f"{W('diabetes')} SELECT CASE WHEN s.svi_overall >= 75 THEN 'high_svi' ELSE 'low_svi' END AS grp, ROUND(1.0 * SUM(m.value_canonical > 9) / COUNT(*), 4) AS share_above_9 "
          f"FROM has_diabetes d JOIN patient_svi s USING (patient_id) JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' "
          f"WHERE s.svi_overall >= 75 OR s.svi_overall < 25 GROUP BY 1 ORDER BY 1")
    a('sdoh', CPS, 'positive_needs_table', "How many patients screened positive for each social need?", 'table',
      sql=f"{W()} SELECT need, COUNT(DISTINCT patient_id) AS patients FROM social_need WHERE positive = 1 AND {LIVQ} GROUP BY 1 ORDER BY 2 DESC, 1",
      notes='distinct living patients with at least one positive screen of that need, any date')
    a('sdoh', CPS, 'two_plus_needs', "How many patients have screened positive for two or more different social needs?", 'count',
      sql=f"{W()} SELECT COUNT(*) FROM (SELECT patient_id FROM social_need WHERE positive = 1 AND {LIVQ} GROUP BY patient_id HAVING COUNT(DISTINCT need) >= 2)")
    a('sdoh', CPS, 'hf_high_svi_ed', "How many heart failure patients in high-SVI areas (75+) had an ED visit in the last year?", 'count',
      sql=f"{W('heart_failure')} SELECT COUNT(*) FROM has_heart_failure h JOIN patient_svi s USING (patient_id) WHERE s.svi_overall >= 75 AND h.patient_id IN (SELECT patient_id FROM ({ED}) WHERE {D.in_last_year('event_date')})")
    a('share_denominator', CPS, 'transport_noshow_share', "What share of patients who screened positive for a transportation barrier missed at least one appointment in the last year?", 'share', tolerance={'abs': 0.005},
      sql=f"{W()} SELECT SUM(patient_id IN (SELECT patient_id FROM appointment WHERE final_status = 'no_show' AND {D.in_last_year('scheduled_date')})), COUNT(*) FROM "
          f"(SELECT DISTINCT patient_id FROM social_need WHERE need = 'transport' AND positive = 1 AND {LIVQ})")
    a('grouped_breakdown', CPS, 'svi_by_payer', "What share of commercial, Medicare and Medicaid patients live in high-SVI (75+) areas?", 'table', tolerance={'abs': 0.005},
      sql=f"{W()} SELECT {PAYER_TYPE} AS payer_type, ROUND(1.0 * SUM(s.svi_overall >= 75) / COUNT(*), 4) AS high_svi_share FROM patient p JOIN patient_svi s USING (patient_id) "
          f"WHERE p.patient_id IN (SELECT patient_id FROM living) AND s.svi_overall IS NOT NULL AND ({PAYER_TYPE}) IS NOT NULL GROUP BY 1 ORDER BY 1")
    a('sdoh', CPS, 'vendor_transport_risk', "How many patients have a transportation-risk score of 7 or higher in the purchased SDOH data?", 'count',
      sql=f"{W(extra=D.SDOH_LATEST_CTE)} SELECT COUNT(*) FROM sdoh_latest WHERE transportation_risk_score >= 7 AND {LIVQ}", notes='latest matched vendor delivery per patient')
    a('top_n', CPS, 'top_svi_counties', "Which five county codes have the most patients in high-SVI areas (75+)?", 'table', order_matters=True,
      sql=f"{W()} SELECT p.county, COUNT(*) AS patients FROM patient p JOIN patient_svi s USING (patient_id) WHERE p.patient_id IN (SELECT patient_id FROM living) AND s.svi_overall >= 75 AND p.county IS NOT NULL "
          f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5")
    _more(b)
    return b


def _more(b):
    a, ctx = b.add, b.ctx
    one, rows = ctx.one, ctx.rows
    CONDITION_RX = {'hypertension': r'hypertension|high blood pressure', 'diabetes': r'diabet', 'ckd': r'kidney|\bCKD\b', 'heart_failure': r'heart failure|\bHF\b|\bCHF\b',
                    'copd': r'\bCOPD\b|chronic obstructive', 'asthma': r'asthma', 'depression': r'depress', 'cad': r'coronary|\bCAD\b', 'afib': r'atrial fib|\bA-?fib\b',
                    'hyperlipidemia': r'hyperlipid|cholesterol|dyslipid', 'obesity': r'obes', 'dementia': r'dementia|alzheimer', 'osteoporosis': r'osteopor', 'hypothyroidism': r'hypothyroid|thyroid'}
    NEED_RX = {'food': r'food', 'transport': r'transport', 'cost_of_care': r'cost|afford', 'utilities': r'utilit', 'housing': r'hous', 'safety': r'safe'}

    # ---- more claims / pharmacy / sdoh items so both directions and every composition type are present in the claims bucket ------------
    a('top_n', CPS, 'lob_highest_pmpm', "Which line of business is the most expensive per member per month in 2029 (medical paid PMPM), among commercial, Medicare, Medicaid, dual and Medigap?", 'table', tolerance={'rel': 0.005, 'abs': 0.5},
      sql=f"WITH s AS (SELECT line_of_business, SUM(paid_cents) / 100.0 AS paid FROM ({S29}) GROUP BY 1), m AS (SELECT line_of_business, COUNT(*) AS mm FROM member_month WHERE substr(month, 1, 4) = '{Y29}' GROUP BY 1) "
          f"SELECT s.line_of_business, ROUND(s.paid / m.mm, 2) AS pmpm_usd FROM s JOIN m USING (line_of_business) WHERE s.line_of_business IN ('commercial', 'medicare', 'medicaid', 'dual', 'medigap') ORDER BY 2 DESC, 1 LIMIT 1")
    a('top_n', CPS, 'oon_by_payer_top3', "Which three payers had the most out-of-network paid dollars in 2029?", 'table', order_matters=True, tolerance={'rel': 0.001, 'abs': 1.0},
      sql=f"SELECT payer_id, ROUND(SUM(paid_cents) / 100.0, 2) AS oon_paid_usd FROM ({S29}) WHERE network_status = 'out_of_network' GROUP BY 1 ORDER BY SUM(paid_cents) DESC, 1 LIMIT 3")
    a('top_n', CPS, 'payers_lowest_denial', "Of payers with at least 500 claims in 2029, which three have the lowest denial rate?", 'table', order_matters=True, tolerance={'abs': 0.002},
      sql=f"SELECT payer_id, ROUND(1.0 * SUM(final_status IN ('denied', 'denied_upheld')) / COUNT(*), 4) AS denial_rate FROM ({S29}) GROUP BY 1 HAVING COUNT(*) >= 500 ORDER BY 2, 1 LIMIT 3")
    a('group_comparison', CPS, 'denial_oon_vs_in', "Compare the claim denial rate on out-of-network claims versus in-network claims in 2029.", 'table', tolerance={'abs': 0.002},
      sql=f"SELECT network_status, ROUND(1.0 * SUM(final_status IN ('denied', 'denied_upheld')) / COUNT(*), 4) AS denial_rate FROM ({S29}) GROUP BY 1 ORDER BY 1")
    a('share_denominator', CPS, 'food_need_dm_share', "What share of diabetics have ever screened positive for food insecurity?", 'share', tolerance={'abs': 0.005},
      sql=f"{W('diabetes')} SELECT SUM(patient_id IN (SELECT patient_id FROM social_need WHERE need = 'food' AND positive = 1)), COUNT(*) FROM has_diabetes",
      notes='denominator = all living diabetics, screened or not')
    a('claims_money', CPS, 'institutional_paid_ly', "How much did we pay on institutional claims in the last 12 months?", 'value', unit='USD', tolerance={'rel': 0.001, 'abs': 1.0},
      sql=f"SELECT SUM(paid_cents) / 100.0 FROM ({SLY}) WHERE claim_type = 'institutional'")

    a('pharmacy', CPS, 'reversed_2029', "How many pharmacy claims were reversed in 2029?", 'count',
      sql=f"SELECT COUNT(*) FROM payer_pharmacy_claim WHERE claim_status = 'reversed' AND {D.in_year('fill_date')}")
    a('pharmacy', CPS, 'avg_days_supply_2029', "What is the average days' supply per paid fill in 2029?", 'value', unit='days', tolerance={'abs': 0.2},
      sql=f"SELECT ROUND(AVG(days_supply), 1) FROM ({PHARM}) WHERE {D.in_year('fill_date')} AND days_supply IS NOT NULL")
    a('pharmacy', CPS, 'glp1_patients_ly', "How many patients filled a GLP-1 agonist in the last 12 months, and how many fills was that?", 'table',
      sql=f"SELECT COUNT(DISTINCT patient_id) AS patients, COUNT(*) AS fills FROM fill WHERE drug_class = 'glp1_agonist' AND {D.in_last_year('fill_date')} AND patient_id IN ({D.LIVING})")
    a('pharmacy', CPS, 'rejects_ly', "How many pharmacy claims were rejected over the past year?", 'count',
      sql=f"SELECT COUNT(*) FROM payer_pharmacy_claim WHERE claim_status = 'rejected' AND {D.in_last_year('fill_date')}")
    a('sdoh', CPS, 'transport_positive_dm', "How many diabetics screened positive for a transportation barrier?", 'count',
      sql=f"{W('diabetes')} SELECT COUNT(*) FROM has_diabetes WHERE patient_id IN (SELECT patient_id FROM social_need WHERE need = 'transport' AND positive = 1)")

    # ---- two-turn follow-ups (child text refers back; key.standalone is the fully resolved question) ---------------------------------
    HCLY = f"hc AS (SELECT patient_id FROM ({SLY}) WHERE {LIVQ} GROUP BY patient_id HAVING SUM(paid_cents) >= 5000000)"
    a('follow_up', PCQ, 'dm_a1c_10_no_insulin_glp1', "and of those, how many are on neither insulin nor a GLP-1?", 'count', follow_up_of='dm_a1c_10',
      standalone="Of the diabetics whose most recent A1c is 10 or higher, how many are on neither insulin nor a GLP-1 agonist (no fill in the last 12 months)?",
      sql=f"{W('diabetes')} SELECT COUNT(*) FROM has_diabetes d JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' "
          f"WHERE m.value_canonical >= 10 AND d.patient_id NOT IN {on_drug('insulin')} AND d.patient_id NOT IN {on_drug('glp1_agonist')}")
    a('follow_up', PCQ, 'htn_on_acei_by_practice', "break that down by practice", 'table', follow_up_of='htn_on_acei',
      standalone="For hypertensive patients currently on an ACE inhibitor, how many are there in each primary-care practice?",
      sql=f"{W('hypertension', extra=D.PRACTICE_CTE)} SELECT pr.practice_id AS practice, COUNT(*) AS patients FROM has_hypertension h JOIN practice_of pr USING (patient_id) "
          f"WHERE h.patient_id IN {on_drug('ace_inhibitor')} AND pr.practice_id LIKE 'WH-PRACTICE-%' GROUP BY 1 ORDER BY 1")
    a('follow_up', PCQ, 'ed_ly_over65', "how many of them are 65 or older?", 'count', follow_up_of='ed_patients_ly',
      standalone="Of the distinct patients who had an ED visit in the last year, how many are 65 or older?",
      sql=f"{W()} SELECT COUNT(DISTINCT e.patient_id) FROM ({ED}) e JOIN patient p USING (patient_id) WHERE {D.in_last_year('e.event_date')} AND {D.age_of('p')} >= 65")
    a('follow_up', CPS, 'high_cost_ly_share', "what share of total spend do they account for?", 'share', follow_up_of='high_cost_ly', tolerance={'abs': 0.005},
      standalone="What share of all medical paid spend in the last 12 months comes from the living patients who accumulated $50,000 or more in paid claims in that period?",
      sql=f"{W()}, {HCLY} SELECT (SELECT SUM(paid_cents) FROM ({SLY}) WHERE patient_id IN (SELECT patient_id FROM hc)), (SELECT SUM(paid_cents) FROM ({SLY}))",
      notes='numerator / denominator in cents; denominator = all counted paid in the rolling year')
    a('follow_up', CPS, 'fills_ly_avg_plan_paid', "and what was the average plan-paid amount per fill?", 'value', follow_up_of='fills_ly', unit='USD', tolerance={'rel': 0.005, 'abs': 0.5},
      standalone="What was the average plan-paid amount per paid pharmacy fill in the last 12 months?",
      sql=f"SELECT ROUND(AVG(plan_paid_cents) / 100.0, 2) FROM ({PHARM}) WHERE {D.in_last_year('fill_date')}")
    a('follow_up', PCQ, 'hf_loop_by_sex', "split that by sex", 'table', follow_up_of='hf_loop_share', tolerance={'abs': 0.005},
      standalone="What share of heart failure patients are on a loop diuretic (a fill in the last 12 months), for women and for men?",
      sql=f"{W('heart_failure')} SELECT p.sex_reported AS sex, ROUND(1.0 * SUM(h.patient_id IN {on_drug('loop_diuretic')}) / COUNT(*), 4) AS loop_diuretic_share FROM has_heart_failure h JOIN patient p USING (patient_id) GROUP BY 1 ORDER BY 1")
    a('follow_up', PCQ, 'copd_lama_ed', "how many of those had an ED visit in the last year?", 'count', follow_up_of='copd_on_lama',
      standalone="How many of the COPD patients on a long-acting muscarinic antagonist (LAMA) had an ED visit in the last year?",
      sql=f"{W('copd')} SELECT COUNT(*) FROM has_copd WHERE patient_id IN {on_drug('lama')} AND patient_id IN (SELECT patient_id FROM ({ED}) WHERE {D.in_last_year('event_date')})")
    a('follow_up', CPS, 'food_need_diabetics', "of the food-insecure ones, how many have diabetes?", 'count', follow_up_of='positive_needs_table',
      standalone="Of the patients who screened positive for food insecurity, how many have diabetes?",
      sql=f"{W('diabetes')} SELECT COUNT(*) FROM has_diabetes WHERE patient_id IN (SELECT patient_id FROM social_need WHERE need = 'food' AND positive = 1)")

    # ---- insight (rubric of required facts; every number computed here) ----------------------------------------------------------------------------
    # I1 which conditions go with ED visits
    ed_by_cond = sorted(((one(f"{W(c)} SELECT COUNT(*) FROM ({ED}) e WHERE {D.in_last_year('e.event_date')} AND e.patient_id IN (SELECT patient_id FROM has_{c})"), c) for c in CONDITION_RX), reverse=True)
    assert ed_by_cond[2][0] > ed_by_cond[3][0], ed_by_cond
    a('utilization_window', INS, 'ins_ed_by_condition', "Which chronic conditions account for the most of our ED visits in the last year?", 'rubric', answer=fact_rubric([
        fact_rank('rank', f"ED visits in the last 12 months by condition cohort: {', '.join(f'{c} ({k})' for k, c in ed_by_cond[:3])}", [CONDITION_RX[c] for k, c in ed_by_cond[:3]]),
        fact_n('top', f"{ed_by_cond[0][1]}: {ed_by_cond[0][0]} ED visits by patients with the condition", ed_by_cond[0][0], abs_=0, near=[CONDITION_RX[ed_by_cond[0][1]]]),
    ], variations='an ED visit counts toward every condition its patient has (cohorts overlap); living patients, standard condition evidence'),
        sql='ED visits in the rolling year by condition cohort (14 tracked conditions)')

    # I2 Medicare vs Medicaid ED use
    ed_rate = {r[0]: r for r in rows(f"""{W()} SELECT {PAYER_TYPE}, ROUND(1000.0 * SUM((SELECT COUNT(*) FROM ({ED}) e WHERE e.patient_id = p.patient_id AND {D.in_last_year('e.event_date')})) / COUNT(*), 1), COUNT(*)
                  FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND ({PAYER_TYPE}) IN ('medicare', 'medicaid') GROUP BY 1""")}
    old = {r[0]: r[1] for r in rows(f"""{W()} SELECT {PAYER_TYPE}, 1.0 * SUM({D.age_of('p')} >= 65) / COUNT(*) FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND ({PAYER_TYPE}) IN ('medicare', 'medicaid') GROUP BY 1""")}
    a('group_comparison', INS, 'ins_medicare_vs_medicaid_ed', "Why do our Medicare patients use the ED so much more than Medicaid patients?", 'rubric', answer=fact_rubric([
        fact_n('mcare', f"Medicare patients: {ed_rate['medicare'][1]} ED visits per 1,000 in the last 12 months", ed_rate['medicare'][1], rel=0.03, near=[r'medicare']),
        fact_n('mcaid', f"Medicaid patients: {ed_rate['medicaid'][1]} ED visits per 1,000", ed_rate['medicaid'][1], rel=0.03, near=[r'medicaid']),
        fact_n('age', f"{old['medicare']:.0%} of Medicare patients are 65 or older versus {old['medicaid']:.0%} of Medicaid patients", old['medicare'], abs_=0.02, unit='proportion', near=[r'65|older|age|elderly']),
    ], variations='Medicare = medicare + dual + medigap coverage; any driver beyond age is fine if the rates are right'),
        sql='ED visits per 1,000 living patients, last 12 months, by payer type; share 65+ by payer type')

    # I3 high-cost claimants, rolling year (+ child)
    hcly = f"hc AS (SELECT patient_id FROM ({SLY}) WHERE {LIVQ} GROUP BY patient_id HAVING SUM(paid_cents) >= 5000000)"
    n_hc = one(f"{W()}, {hcly} SELECT COUNT(*) FROM hc")
    hc_paid, tot_paid = rows(f"{W()}, {hcly} SELECT (SELECT SUM(paid_cents) FROM ({SLY}) WHERE patient_id IN (SELECT patient_id FROM hc)), (SELECT SUM(paid_cents) FROM ({SLY}))")[0]
    inst_share = one(f"{W()}, {hcly} SELECT 1.0 * SUM(CASE WHEN claim_type = 'institutional' THEN paid_cents END) / SUM(paid_cents) FROM ({SLY}) WHERE patient_id IN (SELECT patient_id FROM hc)")
    a('claims_money', INS, 'ins_high_cost_profile', "What is behind the spend of our $50k-plus patients over the last 12 months: how many are there, how much of the total do they take, and is it hospital or office care?", 'rubric', answer=fact_rubric([
        fact_n('n', f"{n_hc} living patients had $50,000+ paid in the last 12 months", n_hc, abs_=0, near=[r'claimant|patients|members']),
        fact_n('share', f"They account for {hc_paid / tot_paid:.1%} of medical paid spend in the window", hc_paid / tot_paid, abs_=0.01, unit='proportion', near=[r'spend|paid|total|%']),
        fact_n('inst', f"{inst_share:.0%} of their paid dollars are institutional (hospital) claims", inst_share, abs_=0.02, unit='proportion', near=[r'institutional|hospital|facility|inpatient']),
    ], variations='any one correct framing of the care mix with the right number earns that fact'),
        sql='living patients with rolling-year counted paid >= $50,000; their share of all paid; institutional share of their paid')

    cc = sorted(((one(f"{W(c)} SELECT COUNT(*) FROM has_{c} WHERE patient_id IN (SELECT patient_id FROM ({SLY}) WHERE {LIVQ} GROUP BY patient_id HAVING SUM(paid_cents) >= 5000000)"), c) for c in CONDITION_RX),
                reverse=True)
    top3 = cc[:3]
    assert cc[2][0] > cc[3][0], 'tie at the cutoff of the top-3 conditions among high-cost claimants'
    a('follow_up', INS, 'ins_high_cost_conditions', "which conditions are most common among them?", 'rubric', follow_up_of='ins_high_cost_profile', answer=fact_rubric([
        fact_t('top3', f"The three most common conditions among the {n_hc} patients with $50k+ paid in the last 12 months: {', '.join(f'{c} ({k})' for k, c in top3)}", [CONDITION_RX[c] for k, c in top3], mode='all'),
        fact_n('top', f"{top3[0][1]}: {top3[0][0]} of the {n_hc} patients", top3[0][0], abs_=0, near=[CONDITION_RX[top3[0][1]]]),
    ], variations='conditions counted by the standard evidence rule (EHR + claims + lab rules); a patient can have several'),
        standalone="Which chronic conditions are most common among the living patients who accumulated $50,000 or more in paid claims over the last 12 months?",
        sql='condition counts (14 tracked conditions) among the rolling-year high-cost claimants; the top three are scored as an unordered set')

    # I4 social needs among poorly controlled diabetics
    needs = sorted(((one(f"""{W('diabetes')} SELECT COUNT(DISTINCT d.patient_id) FROM has_diabetes d JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c'
                         WHERE m.value_canonical > 9 AND d.patient_id IN (SELECT patient_id FROM social_need WHERE need = '{nd}' AND positive = 1)"""), nd) for nd in NEED_RX), reverse=True)
    assert needs[2][0] > needs[3][0], needs
    a('sdoh', INS, 'ins_needs_poor_control', "What social barriers are most common among diabetics whose latest A1c is above 9?", 'rubric', answer=fact_rubric([
        fact_rank('rank', f"Positive screens among diabetics with A1c > 9: {', '.join(f'{nd} ({k})' for k, nd in needs[:3])}", [NEED_RX[nd] for k, nd in needs[:3]]),
        fact_n('top', f"{needs[0][1]}: {needs[0][0]} patients", needs[0][0], abs_=0, near=[NEED_RX[needs[0][1]]]),
    ], variations='distinct patients with any positive screen of the need (any date)'),
        sql='diabetics with latest A1c > 9 x positive social_need by need')

    # I5 food-insecure vs not: ED use
    fx = {r[0]: r for r in rows(f"""{W()} SELECT g, ROUND(1000.0 * SUM(n) / COUNT(*), 1), COUNT(*) FROM (SELECT CASE WHEN p.patient_id IN (SELECT patient_id FROM social_need WHERE need = 'food' AND positive = 1) THEN 'food' ELSE 'other' END AS g,
                  (SELECT COUNT(*) FROM ({ED}) e WHERE e.patient_id = p.patient_id AND {D.in_last_year('e.event_date')}) AS n FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living)) GROUP BY g""")}
    a('sdoh', INS, 'ins_food_ed', "Do patients who screened positive for food insecurity use the ED more than everyone else?", 'rubric', answer=fact_rubric([
        fact_n('food', f"Food-insecure patients: {fx['food'][1]} ED visits per 1,000 in the last 12 months ({fx['food'][2]} patients)", fx['food'][1], rel=0.03, near=[r'food']),
        fact_n('other', f"All other patients: {fx['other'][1]} ED visits per 1,000 ({fx['other'][2]} patients)", fx['other'][1], rel=0.03, near=[r'other|rest|without|not|everyone|no food|negative']),
        fact_t('direction', f"ED use is {'higher' if fx['food'][1] > fx['other'][1] else 'lower'} among food-insecure patients",
               [r'\b(higher|more|greater|elevated)\b'] if fx['food'][1] > fx['other'][1] else [r'\b(lower|fewer|less|no higher|not higher|no more)\b']),
    ], variations='any positive screen of the food need, any date; rate per 1,000 living patients in each group'),
        sql='ED visits per 1,000 living patients in the rolling year, positive food screen vs none')

    # I6 observation stays 2028 -> 2029
    ob = {r[0]: r[1] for r in rows(f"{W()} SELECT substr(event_date, 1, 4), COUNT(*) FROM utilization_event WHERE kind = 'observation' AND {LIVQ} AND substr(event_date, 1, 4) IN ('2028', '2029') GROUP BY 1")}
    assert ob['2029'] > ob['2028']
    a('utilization_window', INS, 'ins_obs_rise', "Why did observation stays go up in 2029?", 'rubric', answer=fact_rubric([
        fact_n('y28', f"{ob['2028']} observation stays in 2028", ob['2028'], abs_=0, near=[r'2028']),
        fact_n('y29', f"{ob['2029']} observation stays in 2029", ob['2029'], abs_=0, near=[r'2029']),
        fact_n('delta', f"A rise of {ob['2029'] - ob['2028']} stays (+{(ob['2029'] - ob['2028']) / ob['2028']:.0%})", ob['2029'] - ob['2028'], abs_=0, near=[r'increase|rise|rose|up|more']),
    ], variations='drivers are optional; the counts are required (events of living patients)'),
        sql='utilization_event kind observation by year, living patients')

    # I7 HF + ED + no loop
    hf_ed = rows(f"""{W('heart_failure')} SELECT COUNT(*), SUM(patient_id NOT IN {on_drug('loop_diuretic')}) FROM has_heart_failure WHERE patient_id IN (SELECT patient_id FROM ({ED}) WHERE {D.in_last_year('event_date')})""")[0]
    a('condition_med', INS, 'ins_hf_ed_no_loop', "Which heart failure patients came through the ED in the last year without being on a loop diuretic, and how big is that group?", 'rubric', answer=fact_rubric([
        fact_n('ed', f"{hf_ed[0]} heart failure patients had an ED visit in the last 12 months", hf_ed[0], abs_=0, near=[r'ED|emergency']),
        fact_n('no_loop', f"{hf_ed[1]} of them have no loop-diuretic fill in the last 12 months", hf_ed[1], abs_=0, near=[r'loop|diuretic|not on|without']),
        fact_n('share', f"{hf_ed[1] / hf_ed[0]:.0%} of HF ED patients are not on a loop diuretic", hf_ed[1] / hf_ed[0], abs_=0.01, unit='proportion', near=[r'loop|diuretic|not on|without|%']),
    ]), sql='HF patients with an ED visit in the rolling year x no loop-diuretic fill in 12 months')

    # I8 brand vs generic spend
    tier = {r[0]: r[1] for r in rows(f"SELECT formulary_tier, SUM(plan_paid_cents) / 100.0 FROM ({PHARM}) WHERE {D.in_last_year('fill_date')} AND formulary_tier IS NOT NULL GROUP BY 1")}
    topdrug = rows(f"SELECT drug_name, SUM(plan_paid_cents) FROM ({PHARM}) WHERE {D.in_last_year('fill_date')} AND formulary_tier = 'brand' GROUP BY 1 ORDER BY 2 DESC LIMIT 2")
    import re as _re
    brand_name = lambda nm: (_re.findall(r'\[(.+?)\]', nm) or [nm.split()[0]])[0]
    assert topdrug[0][1] > topdrug[1][1]
    a('pharmacy', INS, 'ins_brand_vs_generic', "How does plan pharmacy spend split between brand and generic drugs over the last year, and which brand drug costs the most?", 'rubric', answer=fact_rubric([
        fact_n('brand', f"Brand plan-paid ${tier['brand']:,.0f} in the last 12 months", tier['brand'], rel=0.02, near=[r'brand']),
        fact_n('generic', f"Generic plan-paid ${tier['generic']:,.0f}", tier['generic'], rel=0.02, near=[r'generic']),
        fact_t('drug', f"The costliest brand drug is {brand_name(topdrug[0][0])}", [_re.escape(brand_name(topdrug[0][0]))]),
    ], variations='fills with no formulary tier are left out of both totals'),
        sql='plan_paid by formulary tier, rolling year; top brand drug by plan_paid')

    # I9 out-of-network concentration by payer
    oon = rows(f"SELECT payer_id, SUM(paid_cents) FROM ({S29}) WHERE network_status = 'out_of_network' GROUP BY 1 ORDER BY 2 DESC, 1")
    oon_total = one(f"SELECT 1.0 * SUM(CASE WHEN network_status = 'out_of_network' THEN paid_cents END) / SUM(paid_cents) FROM ({S29})")
    assert oon[2][1] > oon[3][1]
    a('claims_money', INS, 'ins_oon_concentration', "Which payers are driving our out-of-network spend?", 'rubric', answer=fact_rubric([
        fact_rank('rank', f"Out-of-network paid in 2029 by payer: {', '.join(f'{r[0]} (${r[1] / 100:,.0f})' for r in oon[:3])}", [lit(r[0]) for r in oon[:3]]),
        fact_n('top', f"{oon[0][0]}: ${oon[0][1] / 100:,.0f} out of network", oon[0][1] / 100, rel=0.02, near=[lit(oon[0][0])]),
        fact_n('total', f"{oon_total:.1%} of all 2029 paid dollars were out of network", oon_total, abs_=0.005, unit='proportion', near=[r'out[- ]of[- ]network|OON|total|overall']),
    ]), sql='out-of-network paid by payer, 2029')

    # I10 diabetics with no urine albumin-creatinine ratio in the last year, by practice
    nb = rows(f"""{W('diabetes', extra=D.PRACTICE_CTE)} SELECT pr.practice_id, COUNT(*) FROM has_diabetes d JOIN practice_of pr USING (patient_id) WHERE pr.practice_id LIKE 'WH-PRACTICE-%'
                  AND d.patient_id NOT IN (SELECT patient_id FROM ({D.any_lab('uacr')}) WHERE {D.in_last_year('event_date')}) GROUP BY 1 ORDER BY 2 DESC, 1""")
    assert nb[1][1] > nb[2][1], nb
    a('condition_lab', INS, 'ins_uacr_gap', "Which practices have the biggest kidney-screening gap among their diabetics (no urine albumin-creatinine ratio in the last 12 months)?", 'rubric', answer=fact_rubric([
        fact_rank('rank', f"Diabetics with no UACR in the last year, by practice: {', '.join(f'{r[0]} ({r[1]})' for r in nb[:2])}", [lit(r[0]) for r in nb[:2]]),
        fact_n('top', f"{nb[0][0]} has {nb[0][1]} diabetics with no UACR in the last 12 months", nb[0][1], abs_=0, near=[lit(nb[0][0])]),
    ], variations='any UACR result dated in the rolling year counts as screened'),
        sql='living diabetics with no uacr result in the rolling year, by practice')

    # I11 readmissions: Medicare vs commercial
    rd = {r[0]: r for r in rows(f"""{W()} SELECT {PAYER_TYPE}, 1.0 * SUM(s.readmitted_30d) / COUNT(*), COUNT(*) FROM stay s JOIN patient p USING (patient_id) WHERE s.type = 'inpatient' AND s.discharge_known = 1
                  AND substr(s.discharge_date, 1, 4) = '2029' AND p.patient_id IN (SELECT patient_id FROM living) AND ({PAYER_TYPE}) IN ('medicare', 'commercial') GROUP BY 1""")}
    a('group_comparison', INS, 'ins_readmit_medicare', "Is 30-day readmission a bigger problem for our Medicare patients than for commercial in 2029?", 'rubric', answer=fact_rubric([
        fact_n('mcare', f"Medicare 30-day readmission rate {rd['medicare'][1]:.1%} ({rd['medicare'][2]} discharges)", rd['medicare'][1], abs_=0.01, unit='proportion', near=[r'medicare']),
        fact_n('comm', f"Commercial 30-day readmission rate {rd['commercial'][1]:.1%} ({rd['commercial'][2]} discharges)", rd['commercial'][1], abs_=0.01, unit='proportion', near=[r'commercial']),
    ], variations='inpatient stays with a known discharge date in 2029; Medicare = medicare + dual + medigap'),
        sql='readmitted_30d / discharges, 2029, by payer type')

    # I12 admissions through the ED
    vg = {r[0]: r[1] for r in rows(f"""{W()} SELECT visit_class, COUNT(*) FROM visit_group WHERE {LIVQ} AND substr(start_date, 1, 4) = '2029' AND visit_class IN ('ed_to_inpatient', 'direct_inpatient') GROUP BY 1""")}
    tot = vg['ed_to_inpatient'] + vg['direct_inpatient']
    a('share_denominator', INS, 'ins_admissions_via_ed', "Do most of our hospital admissions in 2029 come through the ED, or are they direct admissions?", 'rubric', answer=fact_rubric([
        fact_n('ed', f"{vg['ed_to_inpatient']} admissions started in the ED", vg['ed_to_inpatient'], abs_=0, near=[r'\bED\b|emergency']),
        fact_n('direct', f"{vg['direct_inpatient']} were direct admissions", vg['direct_inpatient'], abs_=0, near=[r'direct']),
        fact_n('share', f"{vg['ed_to_inpatient'] / tot:.0%} of inpatient visit groups began in the ED", vg['ed_to_inpatient'] / tot, abs_=0.01, unit='proportion', near=[r'%|percent|share|most']),
    ], variations='visit groups (ED-to-inpatient vs direct inpatient) starting in 2029, living patients'),
        sql='visit_group visit_class counts, 2029')

    # ---- data genuinely absent ---------------------------------------------------------------------------------------------------------------------
    b.absent(PCQ, 'absent_bmi', "What percent of adults have a measured BMI of 30 or higher?",
             'The warehouse stores weight but no height or BMI result; obesity exists only as a diagnosis, so a measured-BMI share cannot be computed.',
             notes='checked: measurement concepts contain no height / bmi; no height column in any table')
    b.absent(PCQ, 'absent_death_cause', "What are the leading causes of death among our deceased patients?",
             'The death table holds dates and sources only; it carries no cause of death.', notes='checked death columns')
    b.absent(PCQ, 'absent_gender_identity', "Break out our diabetes control by gender identity.",
             'The patient record has reported sex only; there is no gender identity or sexual orientation field.', notes='checked patient columns')
    b.absent(PCQ, 'absent_triage', "What is the average ED triage acuity (ESI level) of our frequent ED users?",
             'ED visit groups carry an arrival time but no triage level or acuity score.', notes='checked visit_group / visit columns')
    b.absent(PCQ, 'absent_care_mgmt', "How many patients are currently enrolled in a care management program?",
             'There is no care-management or program-enrollment table; only payer enrollment spans exist.', notes='checked table list')
    b.absent(CPS, 'absent_prior_auth', "What is the prior authorization approval rate for advanced imaging by payer?",
             'No prior-authorization requests or determinations are in the warehouse.', notes='checked claim, adjudication and pharmacy tables')
    b.absent(CPS, 'absent_mssp_savings', "How much shared savings did we earn on the MSSP contract for 2029?",
             'Claims carry the MSSP payer id but no benchmark, shared-savings or settlement amounts.', notes='no savings / benchmark / settlement columns')
    b.absent(CPS, 'absent_deductible', "Which members reached their annual deductible in 2029?",
             'Claims hold patient responsibility per claim, but plan benefit design (deductibles, out-of-pocket maximums) and accumulators are not in the warehouse.')
    b.absent(CPS, 'absent_hrrp_penalty', "What readmission penalty dollars are we facing under the CMS HRRP program?",
             'Readmission flags exist, but no CMS payment-adjustment or penalty amounts are in the warehouse.')
    b.absent(INS, 'absent_productivity', "What explains the difference in RVU productivity per clinician between our practices?",
             'The warehouse has no RVU, wRVU or productivity measure per clinician.', notes='checked clinician / procedure columns')


# ====================================================================================================================================
def rows_of(b: Builder) -> list[dict]:
    out = []
    for family, q in b.items:
        r = finish(q, b.ctx)
        r['family'] = family
        out.append(r)
    return out


def assign_ids(rows: list[dict]) -> list[dict]:
    """ids c3-NN / c4-NN / c5-NN per bucket in build order; split dev2 for every row; follow_up_of keys -> parent ids."""
    key_to_id, counters = {}, Counter()
    for r in rows:
        counters[r['bucket']] += 1
        r['id'] = f"{SHORT[r['bucket']]}-{counters[r['bucket']]:02d}"
        key_to_id[r['key_name']] = r['id']
    by_id = {r['id']: r for r in rows}
    for r in rows:
        r['split'] = SPLIT
        if r['follow_up_of']:
            parent = key_to_id[r['follow_up_of']]
            assert parent != r['id'] and by_id[parent]['bucket'] == r['bucket'], r['key_name']
            r['follow_up_of'] = parent
            assert r['key'].get('standalone'), r['key_name']
    return rows


def build_rows() -> list[dict]:
    ctx = Ctx(seed=SEED)
    rows = rows_of(build(ctx))
    names = [r['key_name'] for r in rows]
    assert len(names) == len(set(names)), [k for k, c in Counter(names).items() if c > 1]
    return assign_ids(rows)


def write(rows, path=OUT):
    with open(path, 'w') as fh:
        for r in rows:
            o = {k: r[k] for k in ('id', 'bucket', 'split', 'family', 'question', 'follow_up_of', 'answer_type', 'key', 'evidence', 'notes')}
            o['key_name'] = r['key_name']
            fh.write(json.dumps(o, ensure_ascii=False) + '\n')


def read(path=OUT):
    return [json.loads(line) for line in open(path) if line.strip()]


def summary(rows):
    print(len(rows), 'questions; split', dict(Counter(r['split'] for r in rows)))
    print('by bucket:', dict(Counter(r['bucket'] for r in rows)))
    print('by family:', dict(sorted(Counter(r['family'] for r in rows).items())))
    print('by answer_type:', dict(sorted(Counter(r['answer_type'] for r in rows).items())))
    print('follow-ups:', sum(1 for r in rows if r['follow_up_of']))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', action='store_true', help='recompute every key and compare with compose_questions.jsonl; write nothing')
    ap.add_argument('--summary', action='store_true')
    args = ap.parse_args(argv)
    rows = build_rows()
    if args.summary:
        summary(rows)
        return 0
    if args.check:
        have = {r['id']: r for r in read()}
        bad = [r['id'] for r in rows if json.dumps(have.get(r['id'], {}).get('key'), sort_keys=True) != json.dumps(r['key'], sort_keys=True)]
        print(f'{len(rows)} questions recomputed; {len(bad)} keys differ: {bad[:10]}')
        return 1 if bad or len(have) != len(rows) else 0
    write(rows)
    summary(rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
