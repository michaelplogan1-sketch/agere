"""Answer keys for the INDEPENDENT development set (split `dev3`): tools/dev_set/dev3_questions.jsonl.

    .venv/bin/python -m tools.dev_set.dev3_keys            # (re)build dev3_questions.jsonl
    .venv/bin/python -m tools.dev_set.dev3_keys --check    # recompute every key and compare with the file (no write)
    .venv/bin/python -m tools.dev_set.dev3_keys --summary  # counts per bucket / answer type / source

Why it exists: the stack scored 93% on its dev set and 72% on a composition set, both written by the same team that wrote the planners, but
only 36% on an independent held-out set. dev / dev2 share the builders' phrasing and blind spots. This set was written by a different
process: the wording comes from public population-health / value-based-care material (HEDIS measure descriptions, ACO REACH / MSSP
reporting, Part D Star Ratings adherence measures, AHRQ PQIs, payer analytics and job-posting question lists, risk-stratification and SVI
guides; see README section "dev3") and the keys are plain SQL (or a short Python computation) written from the warehouse schema and the
decision context. Nothing here imports or reads the product's question grammar / compiler.

Conventions (decision_context.py + definitions.py): living, de-duplicated patients; latest lab unless the question says ever / any;
condition evidence = EHR + problem list + counted claim diagnoses (+ HbA1c >= 6.5 for diabetes, two eGFR < 60 90 days apart for CKD); "last year" =
the 12 months ending 2030-05-27; "in 2029" = that calendar year; "on" a drug = a picked-up fill in the 12 months ending 2030-05-27; Medicare =
medicare + dual + medigap; ED / inpatient events from utilization_event; money = counted payer claims by service_from.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.dev_set import definitions as D
from tools.dev_set.b5_insight import lit, n as fact_n, rank as fact_rank, rubric as fact_rubric, t as fact_t  # pure rubric constructors
from tools.dev_set.keys import Ctx, Q, finish

HERE = Path(__file__).resolve().parent
OUT = HERE / 'dev3_questions.jsonl'
SPLIT = 'dev3'
SEED = 20261025
B1, B2, PCQ, CPS, INS = ('single_patient_structured', 'single_patient_unstructured', 'population_clinical_quality',
                         'population_claims_pharmacy_sdoh', 'insight')
BUCKETS = (B1, B2, PCQ, CPS, INS)
SHORT = {B1: 'x1', B2: 'x2', PCQ: 'x3', CPS: 'x4', INS: 'x5'}

# --- public sources the wording / question types come from (URL per tag; each question carries its tags in `source`) ----------------
SOURCES = {
    'hedis_cbp': 'https://healthplan.org/application/files/3117/2537/0636/Controlling_High_Blood_Pressure.pdf',
    'hedis_cbp_bcbsnc': 'https://bluecrossnc.com/content/dam/bcbsnc/pdf/providers/network-programs/blue-medicare/ncbcbs-cr-075514-24-cpn74974-controlling-high-blood-pressure-cbp-2025.pdf',
    'hedis_hbd_acp': 'https://acponline.org/clinical-information/performance-measures/comprehensive-diabetes-care-hemoglobin-a1c-hgba1c',
    'hedis_cdc_centene': 'https://network.carolinacompletehealth.com/content/dam/centene/carolinacompletehealth/pdfs/Comprehensive_Diabetes_Care.pdf',
    'molina_quality': 'https://www.cleverprep.com/companies/molina-healthcare/quality-analyst',
    'molina_data': 'https://www.cleverprep.com/companies/molina-healthcare/data-analyst',
    'aco_reach': 'https://pearlhealth.com/blog/healthcare-insights/quality-metrics-aco-reach',
    'mssp_measures': 'https://www.qualityforum.org/MAP_Archives/Coordinating_Committee/2016-2017/2016-2017_Current_Program_Measure_Sets/MSSP_Measure_Set.aspx',
    'ahrq_pqi': 'https://qualityindicators.ahrq.gov/Downloads/Modules/PQI/V2024/TechSpecs/PQI_90_Prevention_Quality_Overall_Composite.pdf',
    'stars_pdc': 'https://www.nimblerx.com/articles/the-independent-pharmacists-guide-to-cms-star-ratings',
    'pdc_outcomes': 'https://outcomes.com/knowledge-base/proportion-of-days-covered-pdc-in-outcomes',
    'rising_risk_ajmc': 'https://www.ajmc.com/view/preventing-tomorrow-s-high-cost-claims-the-rising-risk-patient-opportunity-in-medicaid',
    'nachc_risk_strat': 'https://www.nachc.org/wp-content/uploads/2023/04/PHM_Risk-Stratification-AG-Jan-2022.pdf',
    'healthcatalyst_caremgmt': 'https://www.healthcatalyst.com/insights/care-management-analytics-6-ways-data-drives-success',
    'cdc_svi': 'https://policymap.com/data/sources/cdc-social-vulnerability-index',
    'claims_kpis_knowi': 'https://www.knowi.com/blog/payer-analytics-dashboards-claims/',
    'claims_trend_simplify': 'https://simplifyhealthcare.com/insights1/',
    'claims_trend_cassidy': 'https://www.cassidyai.com/solutions/ai-claims-trend-analysis-agent',
    'previsit_mango': 'https://www.mangoapps.com/templates/tasks/enhanced-rooming-previsit-planning-checklist',
    'awv_checklist': 'https://carepatron.com/files/medicare-annual-wellness-visit-checklist.pdf',
    'aafp_awv': 'https://www.aafp.org/pubs/fpm/issues/2011/0700/p10.pdf',
}

LIV = D.LIVING_CTE
LIVQ = 'patient_id IN (SELECT patient_id FROM living)'
COUNTED_CLAIMS = D.SPEND_CLAIMS


def cond(*names):
    return ', '.join(D.condition_cte(x) for x in names)


def W(*names, extra=''):
    parts = [LIV] + ([cond(*names)] if names else []) + ([extra] if extra else [])
    return 'WITH ' + ', '.join(parts)


def pdc(fills, start, end):
    """Proportion of days covered (CMS / PQA style): days from the first fill in the window to the window end; each fill covers
    days_supply days; a fill that overlaps earlier supply starts when the earlier supply ends (carry-forward). Needs >= 2 fills."""
    d0 = dt.date.fromisoformat(start)
    d1 = dt.date.fromisoformat(end)
    fs = sorted((dt.date.fromisoformat(d), int(s or 0)) for d, s in fills if start <= d <= end)
    if len(fs) < 2:
        return None
    first = fs[0][0]
    covered, cursor = set(), first
    for d, s in fs:
        begin = max(d, cursor)
        for k in range(s):
            day = begin + dt.timedelta(days=k)
            if day <= d1:
                covered.add(day)
        cursor = begin + dt.timedelta(days=s)
    return len(covered) / ((d1 - first).days + 1)


class Builder:
    def __init__(self, ctx):
        self.ctx, self.items = ctx, []

    def add(self, bucket, key, text, typ, src, **kw):
        for s in src:
            assert s in SOURCES, s
        self.items.append((src, Q(bucket, key, text, typ, **kw)))

    def absent(self, bucket, key, text, reason, src, notes=''):
        self.add(bucket, key, text, 'absent', src, reason=reason, notes=notes)


# ====================================================================================================================================
def build_b1(b: Builder):
    ctx, a, one, rows = b.ctx, b.add, b.ctx.one, b.ctx.rows
    pk = ctx.pick

    def latest(c, p):
        return f"SELECT value_canonical FROM measurement_latest WHERE concept = '{c}' AND patient_id = '{p}'"

    # 1-2: last BP, then at goal?
    p = pk("SELECT s.patient_id FROM measurement_latest s JOIN measurement_latest d ON d.patient_id = s.patient_id AND d.concept = 'diastolic_bp' "
           "WHERE s.concept = 'systolic_bp' AND s.value_canonical >= 140 AND s.patient_id LIKE 'WHP-%'")
    a(B1, 'bp_last', f"Pull up the last BP reading we have on {p}.", 'list', ['previsit_mango', 'hedis_cbp'],
      sql=f"SELECT CAST(CAST(value_canonical AS INT) AS TEXT) FROM measurement_latest WHERE patient_id = '{p}' AND concept IN ('systolic_bp', 'diastolic_bp')",
      notes='latest systolic and diastolic (measurement_latest); both numbers must appear')
    a(B1, 'bp_goal', "is that at goal (under 140/90)?", 'yes_no', ['hedis_cbp'], follow_up_of='bp_last',
      sql=f"SELECT (SELECT value_canonical FROM measurement_latest WHERE patient_id = '{p}' AND concept = 'systolic_bp') < 140 AND "
          f"(SELECT value_canonical FROM measurement_latest WHERE patient_id = '{p}' AND concept = 'diastolic_bp') < 90",
      standalone=f"Is the latest blood pressure on {p} under 140/90 mm Hg?", notes='CBP control rule: systolic < 140 AND diastolic < 90')

    # 3-4: PHQ-9
    p = pk("SELECT patient_id FROM measurement_latest WHERE concept = 'phq9' AND value_canonical >= 5")
    a(B1, 'phq9_last', f"What was {p}'s last PHQ-9 score?", 'value', ['awv_checklist'], sql=latest('phq9', p),
      unit='points', tolerance={'abs': 0.01}, notes='latest phq9 result')
    a(B1, 'phq9_positive', "was that a positive depression screen, 10 or above?", 'yes_no', ['awv_checklist'], follow_up_of='phq9_last',
      sql=f"SELECT value_canonical >= 10 FROM measurement_latest WHERE concept = 'phq9' AND patient_id = '{p}'",
      standalone=f"Was the latest PHQ-9 on {p} a positive depression screen (a score of 10 or higher)?")

    # 5: SGLT2 right now (lapsed: filled before, none in the last 12 months)
    p = pk(f"SELECT patient_id FROM fill WHERE drug_class = 'sglt2_inhibitor' GROUP BY patient_id HAVING MAX(fill_date) < '{D.LAST_YEAR_START}'")
    a(B1, 'sglt2_now', f"Is {p} currently on an SGLT2 inhibitor?", 'yes_no', ['previsit_mango'],
      sql=f"SELECT COUNT(*) > 0 FROM fill WHERE patient_id = '{p}' AND drug_class = 'sglt2_inhibitor' AND {D.in_last_year('fill_date')}",
      notes='"currently on" = a picked-up fill in the 12 months ending the as-of date; this patient filled one only before that window')

    # 6: number of active meds
    p = pk("SELECT patient_id FROM medication_list_entry WHERE list_status = 'active' GROUP BY patient_id HAVING COUNT(*) BETWEEN 5 AND 12")
    a(B1, 'n_active_meds', f"how many meds is pt {p} on per the active list", 'count', ['previsit_mango'],
      sql=f"SELECT COUNT(*) FROM ({D.ACTIVE_MEDS}) WHERE patient_id = '{p}'", notes='medication-list entries with list_status active')

    # 7: coverage
    p = pk("SELECT patient_id FROM patient WHERE coverage_type = 'dual'")
    a(B1, 'coverage', f"What kind of coverage does {p} carry?", 'label', ['molina_data'],
      sql=f"SELECT coverage_type FROM patient WHERE patient_id = '{p}'", notes='patient.coverage_type')

    # 8: age
    p = pk("SELECT patient_id FROM patient WHERE patient_id LIKE 'WHP-%' AND coverage_type = 'medicare'")
    a(B1, 'age', f"Quick one: how old is {p}?", 'value', ['awv_checklist'],
      sql=f"SELECT {D.age_of('p')} FROM patient p WHERE patient_id = '{p}'", unit='years', tolerance={'abs': 0.01},
      notes='age in whole years at the as-of date from birth_date (never the stale age_latest)')

    # 9-10: deceased
    p = pk("SELECT patient_id FROM death WHERE patient_id LIKE 'WHP-%' OR patient_id LIKE 'SYN-%'")
    a(B1, 'alive', f"Is {p} still alive?", 'yes_no', ['molina_data'], sql=f"SELECT COUNT(*) = 0 FROM death WHERE patient_id = '{p}'",
      notes='a named deceased patient is not excluded; the answer is no')
    a(B1, 'death_date', "when did they die?", 'date', ['molina_data'], follow_up_of='alive',
      sql=f"SELECT death_date FROM death WHERE patient_id = '{p}'", standalone=f"What is the date of death of {p}?")

    # 11: no-shows
    p = pk(f"SELECT patient_id FROM appointment WHERE final_status = 'no_show' AND {D.in_last_year('scheduled_date')} GROUP BY patient_id HAVING COUNT(*) >= 2")
    a(B1, 'no_shows', f"How many appointments has {p} no-showed on in the last year?", 'count', ['hedis_cdc_centene'],
      sql=f"SELECT COUNT(*) FROM appointment WHERE patient_id = '{p}' AND final_status = 'no_show' AND {D.in_last_year('scheduled_date')}",
      notes='appointment.final_status no_show, scheduled in the 12 months ending the as-of date')

    # 12: last office visit
    p = pk(f"SELECT patient_id FROM ({D.FOLLOW_UP_VISIT}) WHERE event_date <= '{D.AS_OF}' AND patient_id LIKE 'SYN-%'")
    a(B1, 'last_office_visit', f"When did {p} last come in for an office or primary care visit?", 'date', ['previsit_mango'],
      sql=f"SELECT MAX(event_date) FROM ({D.FOLLOW_UP_VISIT}) WHERE patient_id = '{p}' AND event_date <= '{D.AS_OF}'",
      notes='last completed outpatient visit (office, primary care, specialty, telehealth)')

    # 13: peak LDL
    p = pk("SELECT patient_id FROM measurement WHERE concept = 'ldl' AND value_canonical IS NOT NULL GROUP BY patient_id HAVING COUNT(*) >= 4")
    a(B1, 'ldl_peak', f"What's the highest LDL ever recorded for {p}?", 'value', ['previsit_mango'],
      sql=f"SELECT MAX(value) FROM ({D.any_lab('ldl')}) WHERE patient_id = '{p}'", unit='mg/dL', tolerance={'abs': 0.5},
      notes='"highest ever" = any result, not the latest')

    # 14: pneumococcal
    p = pk("SELECT patient_id FROM immunization WHERE vaccine LIKE 'pneumococcal%'")
    a(B1, 'pneumo', f"Has {p} had a pneumococcal vaccine?", 'yes_no', ['awv_checklist'],
      sql=f"SELECT COUNT(*) > 0 FROM immunization WHERE patient_id = '{p}' AND vaccine LIKE 'pneumococcal%'")

    # 15: smoking
    p = pk("SELECT patient_id FROM smoking_status WHERE status = 'former' AND patient_id LIKE 'SYN-%'")
    a(B1, 'smoking', f"What's the smoking status on file for {p}?", 'label', ['awv_checklist'],
      sql=f"SELECT status FROM smoking_status WHERE patient_id = '{p}'", notes='smoking_status.status')

    # 16: social needs flagged
    p = pk("SELECT patient_id FROM social_need WHERE positive = 1 GROUP BY patient_id HAVING COUNT(DISTINCT need) >= 2")
    a(B1, 'open_needs', f"Which social needs has {p} screened positive for?", 'list', ['cdc_svi', 'healthcatalyst_caremgmt'],
      sql=f"SELECT need FROM ({D.POSITIVE_NEED}) WHERE patient_id = '{p}'", notes='any positive screen counts; need codes as stored')

    # 17: admitted last 12 months
    p = pk(f"SELECT patient_id FROM utilization_event WHERE kind = 'inpatient' AND {D.in_last_year('event_date')}")
    a(B1, 'admitted_ly', f"Was {p} in the hospital at all in the past 12 months?", 'yes_no', ['aco_reach'],
      sql=f"SELECT COUNT(*) > 0 FROM utilization_event WHERE patient_id = '{p}' AND kind = 'inpatient' AND {D.in_last_year('event_date')}",
      notes='inpatient utilization_event in the window (observation does not count)')

    # 18: LOS
    p = pk("SELECT patient_id FROM utilization_event WHERE kind = 'inpatient' AND end_date IS NOT NULL AND julianday(end_date) - julianday(event_date) >= 3 "
           "GROUP BY patient_id HAVING COUNT(*) = 1")
    a(B1, 'los', f"How many days was {p} in hospital?", 'value', ['aco_reach'],
      sql=f"SELECT julianday(end_date) - julianday(event_date) FROM utilization_event WHERE patient_id = '{p}' AND kind = 'inpatient'",
      unit='days', tolerance={'abs': 1.0}, notes='the patient has exactly one inpatient stay; days = discharge date minus admit date')

    # 19: SVI
    p = pk("SELECT patient_id FROM patient_svi WHERE svi_overall IS NOT NULL AND patient_id LIKE 'WHP-%'")
    a(B1, 'svi', f"What SVI score does {p} have?", 'value', ['cdc_svi'],
      sql=f"SELECT svi_overall FROM patient_svi WHERE patient_id = '{p}'", unit='percentile (0-100)', tolerance={'abs': 0.5})

    # 20: pharmacy paid
    p = pk(f"SELECT patient_id FROM ({D.PHARMACY_FILLS}) WHERE {D.in_year('fill_date')} GROUP BY patient_id HAVING SUM(plan_paid_cents) > 100000")
    a(B1, 'pt_rx_paid_2029', f"Total plan-paid pharmacy cost for {p} in 2029?", 'value', ['claims_kpis_knowi'],
      sql=f"SELECT ROUND(SUM(plan_paid_cents) / 100.0, 2) FROM ({D.PHARMACY_FILLS}) WHERE patient_id = '{p}' AND {D.in_year('fill_date')}",
      unit='USD', tolerance={'rel': 0.005, 'abs': 0.5}, notes='counted pharmacy fills (counts_as_fill = 1), plan_paid, by fill date in 2029')

    # 21: statin PDC 2029 (python)
    pool = ctx.rows("SELECT patient_id FROM fill WHERE drug_class = 'statin' AND substr(fill_date, 1, 4) = '2029' GROUP BY patient_id HAVING COUNT(*) >= 6")
    p = ctx.rng.choice(sorted(r[0] for r in pool))
    fl = ctx.rows(f"SELECT fill_date, days_supply FROM fill WHERE patient_id = '{p}' AND drug_class = 'statin' AND substr(fill_date, 1, 4) = '2029'")
    val = round(pdc(fl, '2029-01-01', '2029-12-31'), 4)
    a(B1, 'statin_pdc', f"What's {p}'s adherence on statins for 2029 — PDC?", 'value', ['stars_pdc', 'pdc_outcomes'], answer=val,
      sql=f"SELECT fill_date, days_supply FROM fill WHERE patient_id = '{p}' AND drug_class = 'statin' AND substr(fill_date, 1, 4) = '2029'",
      unit='proportion', tolerance={'abs': 0.03},
      notes='PDC = covered days / days from the first 2029 statin fill to 2029-12-31, overlapping fills carried forward (computed in Python from the fill rows)')

    # 22: opioid + benzo (ever)
    p = pk("SELECT patient_id FROM fill WHERE drug_class = 'opioid' INTERSECT SELECT patient_id FROM fill WHERE drug_class = 'benzodiazepine'")
    a(B1, 'opioid_benzo', f"Has {p} ever filled both an opioid and a benzo?", 'yes_no', ['aco_reach'],
      sql=f"SELECT COUNT(DISTINCT drug_class) = 2 FROM fill WHERE patient_id = '{p}' AND drug_class IN ('opioid', 'benzodiazepine')",
      notes='both classes appear in the fill history (any time)')

    # 23-24: eGFR and its date
    p = pk("SELECT patient_id FROM measurement_latest WHERE concept = 'egfr' AND value_canonical < 60 AND patient_id LIKE 'WHP-%'")
    a(B1, 'egfr_last', f"What's the most recent eGFR on {p}?", 'value', ['previsit_mango'], sql=latest('egfr', p),
      unit='mL/min/1.73m2', tolerance={'abs': 0.5})
    a(B1, 'egfr_when', "and when was that drawn?", 'date', ['previsit_mango'], follow_up_of='egfr_last',
      sql=f"SELECT event_date FROM measurement_latest WHERE concept = 'egfr' AND patient_id = '{p}'",
      standalone=f"On what date was the most recent eGFR on {p} drawn?")

    # 25: medical paid 2028
    p = pk(f"SELECT patient_id FROM ({COUNTED_CLAIMS}) WHERE {D.in_year('service_from', '2028')} GROUP BY patient_id HAVING SUM(paid_cents) > 300000")
    a(B1, 'medical_paid_2028', f"Medical claims paid on {p} for calendar 2028?", 'value', ['claims_kpis_knowi'],
      sql=f"SELECT ROUND(SUM(paid_cents) / 100.0, 2) FROM ({COUNTED_CLAIMS}) WHERE patient_id = '{p}' AND {D.in_year('service_from', '2028')}",
      unit='USD', tolerance={'rel': 0.005, 'abs': 0.5}, notes='counted payer claims by service date in 2028, paid net of reversals')

    # 26: distinct drug classes
    p = pk(f"SELECT patient_id FROM fill WHERE drug_class IS NOT NULL AND {D.in_last_year('fill_date')} GROUP BY patient_id HAVING COUNT(DISTINCT drug_class) BETWEEN 4 AND 9")
    a(B1, 'n_classes', f"how many different drug classes did {p} fill in the last year", 'count', ['aco_reach'],
      sql=f"SELECT COUNT(DISTINCT drug_class) FROM fill WHERE patient_id = '{p}' AND drug_class IS NOT NULL AND {D.in_last_year('fill_date')}",
      notes='distinct fill.drug_class values with a picked-up fill in the last 12 months')

    # 27: A1c results in the last year
    p = pk(f"SELECT patient_id FROM ({D.any_lab('hba1c')}) WHERE {D.in_last_year('event_date')} GROUP BY patient_id HAVING COUNT(*) >= 3")
    a(B1, 'a1c_count_ly', f"How many HbA1c results does {p} have in the past year?", 'count', ['hedis_hbd_acp'],
      sql=f"SELECT COUNT(*) FROM ({D.any_lab('hba1c')}) WHERE patient_id = '{p}' AND {D.in_last_year('event_date')}",
      notes='every current result (any_lab) dated in the 12 months ending the as-of date')

    # absent
    p = pk("SELECT patient_id FROM patient WHERE sex_reported = 'F' AND patient_id LIKE 'SYN-%' AND " + D.age_of() + " BETWEEN 30 AND 55")
    b.absent(B1, 'absent_pap', f"When was {p}'s last Pap smear and what did it show?", 'The warehouse holds no Pap / HPV test results or codes (no cervical screening data).',
             ['molina_quality'], notes='checked cancer_screening (FIT / mammography only), procedure_line, notes, scanned text')
    p = pk("SELECT patient_id FROM fill WHERE drug_class = 'antiretroviral'")
    b.absent(B1, 'absent_viral_load', f"What is {p}'s most recent HIV viral load?", 'No HIV RNA / viral-load test exists among the measurement concepts.',
             ['previsit_mango'], notes='measurement concepts are a fixed list without HIV RNA; antiretroviral fills do exist')
    p = pk("SELECT patient_id FROM patient WHERE sex_reported = 'F' AND patient_id LIKE 'WHP-%' AND " + D.age_of() + " BETWEEN 40 AND 70")
    b.absent(B1, 'absent_genetic', f"Did {p} ever have BRCA or other genetic testing, and was it positive?", 'The warehouse has no genetic / genomic test results.',
             ['aco_reach'], notes='no genetic result table or concept; checked notes and scanned text')


# ====================================================================================================================================
def build_pcq(b: Builder):
    ctx, a = b.ctx, b.add
    ADULT = lambda lo, hi: f"{D.age_of('p')} BETWEEN {lo} AND {hi}"

    # --- HEDIS-style measures -------------------------------------------------------------------------------------------------
    a(PCQ, 'cbp_rate', "What's our controlling high blood pressure rate? Adults 18 to 85 with hypertension whose most recent BP is under 140/90 — a missing reading counts as not controlled.",
      'share', ['hedis_cbp', 'hedis_cbp_bcbsnc'],
      sql=f"""{W('hypertension')}
        SELECT SUM(s.value_canonical < 140 AND d.value_canonical < 90), COUNT(*)
          FROM has_hypertension h JOIN patient p USING (patient_id)
          LEFT JOIN measurement_latest s ON s.patient_id = h.patient_id AND s.concept = 'systolic_bp'
          LEFT JOIN measurement_latest d ON d.patient_id = h.patient_id AND d.concept = 'diastolic_bp'
         WHERE {ADULT(18, 85)}""", tolerance={'abs': 0.005},
      notes='NCQA CBP: denominator = living hypertensives 18-85 (age at the as-of date); numerator = latest systolic < 140 AND latest diastolic < 90; no reading = not controlled')

    a(PCQ, 'hba1c_testing', "What percent of our diabetics aged 18 to 75 had at least one HbA1c test during 2029?", 'share',
      ['hedis_hbd_acp', 'hedis_cdc_centene'],
      sql=f"""{W('diabetes')}
        SELECT SUM(EXISTS (SELECT 1 FROM ({D.any_lab('hba1c')}) m WHERE m.patient_id = h.patient_id AND {D.in_year('m.event_date')})), COUNT(*)
          FROM has_diabetes h JOIN patient p USING (patient_id) WHERE {ADULT(18, 75)}""", tolerance={'abs': 0.005},
      notes='HbA1c testing in calendar 2029 among living diabetics 18-75; any current result counts')

    poor = f"""{W('diabetes')}, last29 AS (SELECT patient_id, value FROM (SELECT patient_id, value, ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY event_date DESC, record_id DESC) rn
                 FROM ({D.any_lab('hba1c')}) WHERE {D.in_year('event_date')}) WHERE rn = 1)"""
    a(PCQ, 'a1c_poor_control', "HbA1c poor control: share of diabetics 18-75 whose most recent 2029 A1c is above 9, counting anyone with no A1c in 2029 as poorly controlled.",
      'share', ['hedis_hbd_acp', 'molina_quality'],
      sql=f"""{poor} SELECT SUM(l.value IS NULL OR l.value > 9.0), COUNT(*) FROM has_diabetes h JOIN patient p USING (patient_id)
               LEFT JOIN last29 l ON l.patient_id = h.patient_id WHERE {ADULT(18, 75)}""", tolerance={'abs': 0.005},
      notes='inverse HEDIS rule: no 2029 A1c = poor control; latest result within calendar 2029')
    a(PCQ, 'a1c_under8', "what about the share under 8, same rule?", 'share', ['hedis_hbd_acp'], follow_up_of='a1c_poor_control',
      sql=f"""{poor} SELECT SUM(l.value < 8.0), COUNT(*) FROM has_diabetes h JOIN patient p USING (patient_id)
               LEFT JOIN last29 l ON l.patient_id = h.patient_id WHERE {ADULT(18, 75)}""", tolerance={'abs': 0.005},
      standalone="What share of diabetics aged 18 to 75 have a most recent 2029 HbA1c under 8.0, counting anyone with no A1c in 2029 as not controlled?")

    a(PCQ, 'spd_statin', "Statin therapy for patients with diabetes: of diabetics 40 to 75, what percentage filled a statin at least once in calendar 2029?", 'share',
      ['hedis_cdc_centene', 'stars_pdc'],
      sql=f"""{W('diabetes')} SELECT SUM(EXISTS (SELECT 1 FROM fill f WHERE f.patient_id = h.patient_id AND f.drug_class = 'statin' AND {D.in_year('f.fill_date')})), COUNT(*)
               FROM has_diabetes h JOIN patient p USING (patient_id) WHERE {ADULT(40, 75)}""", tolerance={'abs': 0.005},
      notes='calendar-2029 fill (the SPD measurement year), not the rolling year')

    a(PCQ, 'ked', "Kidney health evaluation in diabetes: share of diabetics 18 to 85 who got BOTH an eGFR and a urine albumin-creatinine ratio in 2029", 'share',
      ['hedis_cdc_centene'],
      sql=f"""{W('diabetes')} SELECT SUM(EXISTS (SELECT 1 FROM ({D.any_lab('egfr')}) m WHERE m.patient_id = h.patient_id AND {D.in_year('m.event_date')})
                                         AND EXISTS (SELECT 1 FROM ({D.any_lab('uacr')}) m WHERE m.patient_id = h.patient_id AND {D.in_year('m.event_date')})), COUNT(*)
               FROM has_diabetes h JOIN patient p USING (patient_id) WHERE {ADULT(18, 85)}""", tolerance={'abs': 0.005})

    a(PCQ, 'plan_readmit', "What was our all-cause 30-day readmission rate for inpatient stays admitted in 2029?", 'share', ['aco_reach', 'ahrq_pqi'],
      sql=f"WITH {LIV} SELECT SUM(readmitted_30d), COUNT(*) FROM utilization_event WHERE kind = 'inpatient' AND {LIVQ} AND {D.in_year('event_date')}",
      tolerance={'abs': 0.005}, notes='index stays = inpatient utilization events admitted in 2029 (living patients); numerator = readmitted_30d = 1')

    a(PCQ, 'ed_followup7', "Follow-up after ED visit: what share of 2029 ED visits were followed by an office, primary care, specialty or telehealth visit within 7 days?", 'share',
      ['aco_reach'],
      sql=f"""WITH {LIV}
        SELECT SUM(EXISTS (SELECT 1 FROM ({D.FOLLOW_UP_VISIT}) v WHERE v.patient_id = u.patient_id AND v.event_date > u.event_date
                           AND julianday(v.event_date) - julianday(u.event_date) <= 7)), COUNT(*)
          FROM utilization_event u WHERE u.kind = 'ed_visit' AND u.patient_id IN (SELECT patient_id FROM living) AND {D.in_year('u.event_date')}""",
      tolerance={'abs': 0.005}, notes='visit counts as follow-up when the date is after the ED date and within 7 days; outpatient types per definitions.FOLLOW_UP_VISIT')

    a(PCQ, 'no_followup14', "How many hospital discharges in 2029 had no outpatient follow-up visit within 14 days?", 'count', ['aco_reach', 'molina_quality'],
      sql=f"""WITH {LIV}
        SELECT COUNT(*) FROM utilization_event u WHERE u.kind = 'inpatient' AND u.end_date IS NOT NULL AND u.patient_id IN (SELECT patient_id FROM living)
           AND substr(u.end_date, 1, 4) = '2029'
           AND NOT EXISTS (SELECT 1 FROM ({D.FOLLOW_UP_VISIT}) v WHERE v.patient_id = u.patient_id AND v.event_date > u.end_date
                           AND julianday(v.event_date) - julianday(u.end_date) <= 14)""",
      notes='discharges = inpatient events with a known discharge date in 2029, living patients; follow-up = completed outpatient visit 1-14 days after discharge')

    a(PCQ, 'colorectal_rate', "Colorectal cancer screening: what share of patients 45 to 75 have a FIT or colonoscopy on record?", 'share', ['hedis_cdc_centene', 'molina_quality'],
      sql=f"""WITH {LIV} SELECT SUM(EXISTS (SELECT 1 FROM ({D.COLORECTAL_EVIDENCE}) c WHERE c.patient_id = p.patient_id)), COUNT(*)
               FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND {ADULT(45, 75)}""", tolerance={'abs': 0.005},
      notes='FIT results or billed colonoscopy / FIT claims anywhere in the observed window (data begins 2026-09)')

    a(PCQ, 'pneumo65', "Percent of patients 65 and over with a pneumococcal vaccine on record?", 'share', ['awv_checklist'],
      sql=f"""WITH {LIV} SELECT SUM(EXISTS (SELECT 1 FROM immunization i WHERE i.patient_id = p.patient_id AND i.vaccine LIKE 'pneumococcal%')), COUNT(*)
               FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND {D.age_of('p')} >= 65""", tolerance={'abs': 0.005})

    # --- clinical conditions -------------------------------------------------------------------------------------------------
    a(PCQ, 'phq9_pos', "How many patients have a positive depression screen (PHQ-9 of 10+) on their latest result?", 'count', ['awv_checklist'],
      sql=f"WITH {LIV} SELECT COUNT(*) FROM measurement_latest WHERE concept = 'phq9' AND value_canonical >= 10 AND {LIVQ}")

    pillars = ' + '.join(f"(h.patient_id IN ({D.filled_class_last_year(cl)}))" for cl in D.GDMT_PILLARS.values())
    a(PCQ, 'hf_three_pillars', "How many HF patients are on at least 3 of the 4 pillars of GDMT?", 'count', ['aco_reach', 'molina_quality'],
      sql=f"{W('heart_failure')} SELECT COUNT(*) FROM has_heart_failure h WHERE {pillars} >= 3",
      notes='pillars = a fill in the last 12 months in beta blocker (HF evidence-based), RAAS (ACEi / ARB / ARNI), MRA and SGLT2 classes')
    a(PCQ, 'hf_sglt2_share', "What share of heart failure patients filled an SGLT2 inhibitor in the last 12 months?", 'share', ['aco_reach'],
      sql=f"{W('heart_failure')} SELECT SUM(h.patient_id IN ({D.filled_class_last_year(['sglt2_inhibitor'])})), COUNT(*) FROM has_heart_failure h",
      tolerance={'abs': 0.005})
    a(PCQ, 'cad_statin', "Among patients with coronary artery disease, what percent are on a statin?", 'share', ['molina_quality'],
      sql=f"{W('cad')} SELECT SUM(h.patient_id IN ({D.filled_class_last_year(['statin'])})), COUNT(*) FROM has_cad h", tolerance={'abs': 0.005},
      notes='"on a statin" = a statin fill in the 12 months ending the as-of date')
    a(PCQ, 'dm_ldl100', "Of the diabetics who have an LDL on file, what share are under 100 on their latest?", 'share', ['hedis_cdc_centene'],
      sql=f"{W('diabetes')} SELECT SUM(m.value_canonical < 100), COUNT(*) FROM has_diabetes h JOIN measurement_latest m ON m.patient_id = h.patient_id AND m.concept = 'ldl'",
      tolerance={'abs': 0.005})
    a(PCQ, 'ckd_htn_no_raas', "CKD pts who also have HTN but aren't on an ACEi, ARB or ARNI — how many?", 'count', ['hedis_cdc_centene'],
      sql=f"{W('ckd', 'hypertension')} SELECT COUNT(*) FROM has_ckd c JOIN has_hypertension h USING (patient_id) "
          f"WHERE c.patient_id NOT IN ({D.filled_class_last_year(['ace_inhibitor', 'arb', 'arni'])})",
      notes='not on = no fill of those classes in the last 12 months')

    # --- utilization / safety ------------------------------------------------------------------------------------------------
    a(PCQ, 'polypharmacy', "How many patients 65 or older have ten or more active medications on their list?", 'count', ['aco_reach'],
      sql=f"""WITH {LIV} SELECT COUNT(*) FROM (SELECT m.patient_id FROM ({D.ACTIVE_MEDS}) m JOIN patient p USING (patient_id)
               WHERE m.patient_id IN (SELECT patient_id FROM living) AND {D.age_of('p')} >= 65 GROUP BY m.patient_id HAVING COUNT(*) >= 10)""",
      notes='active medication-list entries (list_status active), living patients, age at the as-of date')
    a(PCQ, 'opioid_ly', "How many patients filled an opioid prescription in the past year?", 'count', ['aco_reach'],
      sql=f"SELECT COUNT(*) FROM ({D.filled_class_last_year(['opioid'])}) WHERE patient_id IN (SELECT patient_id FROM patient WHERE patient_id NOT IN (SELECT patient_id FROM death) AND data_segment <> 'duplicate_chart')")
    a(PCQ, 'opioid_65', "how many of these are 65 and up?", 'count', ['aco_reach'], follow_up_of='opioid_ly',
      sql=f"SELECT COUNT(*) FROM ({D.filled_class_last_year(['opioid'])}) o JOIN patient p USING (patient_id) WHERE p.patient_id NOT IN (SELECT patient_id FROM death) "
          f"AND p.data_segment <> 'duplicate_chart' AND {D.age_of('p')} >= 65",
      standalone='How many living patients who filled an opioid prescription in the past year are 65 or older?')
    a(PCQ, 'deaths_2029', "How many patients died in 2029?", 'count', ['molina_data'],
      sql=f"SELECT COUNT(*) FROM death d JOIN patient p USING (patient_id) WHERE p.data_segment <> 'duplicate_chart' AND {D.in_year('d.death_date')}",
      notes='deceased are asked for explicitly; death_date in calendar 2029, duplicate charts once')
    a(PCQ, 'kids_asthma', "How many children under 18 carry an asthma dx?", 'count', ['ahrq_pqi'],
      sql=f"{W('asthma')} SELECT COUNT(*) FROM has_asthma h JOIN patient p USING (patient_id) WHERE {D.age_of('p')} < 18")
    a(PCQ, 'avg_los_2029', "What was the average length of stay on 2029 inpatient admissions?", 'value', ['aco_reach', 'claims_kpis_knowi'],
      sql=f"WITH {LIV} SELECT ROUND(AVG(julianday(end_date) - julianday(event_date)), 3) FROM utilization_event WHERE kind = 'inpatient' AND end_date IS NOT NULL AND {LIVQ} AND {D.in_year('event_date')}",
      unit='days', tolerance={'abs': 0.1}, notes='stays admitted in 2029 with a known discharge date; days = discharge minus admit date')
    a(PCQ, 'no_shows_2029', "How many scheduled appointments ended as no-shows in 2029?", 'count', ['claims_kpis_knowi', 'molina_data'],
      sql=f"SELECT COUNT(*) FROM appointment WHERE final_status = 'no_show' AND {D.in_year('scheduled_date')}",
      notes='appointment.final_status = no_show, scheduled in 2029 (a patient-event count over every patient)')
    a(PCQ, 'avg_a1c', "What's the average latest HbA1c across our diabetic population?", 'value', ['hedis_hbd_acp'],
      sql=f"{W('diabetes')} SELECT ROUND(AVG(m.value_canonical), 3) FROM has_diabetes h JOIN measurement_latest m ON m.patient_id = h.patient_id AND m.concept = 'hba1c'",
      unit='%', tolerance={'abs': 0.05}, notes='mean of the latest A1c of living diabetics who have one')
    a(PCQ, 'mammo_gap', "How many women 50 to 74 have no mammogram on record in the last two years?", 'count', ['molina_quality', 'hedis_cdc_centene'],
      sql=f"""WITH {LIV} SELECT COUNT(*) FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND p.sex_reported = 'F' AND {ADULT(50, 74)}
               AND NOT EXISTS (SELECT 1 FROM ({D.MAMMOGRAM_EVIDENCE}) m WHERE m.patient_id = p.patient_id AND m.event_date > date('{D.AS_OF}', '-24 months') AND m.event_date <= '{D.AS_OF}')""",
      notes='result or billed screening mammogram in the 24 months ending the as-of date')
    a(PCQ, 'multi_ip_2029', "Number of patients with two or more inpatient admisions in 2029", 'count', ['aco_reach', 'rising_risk_ajmc'],
      sql=f"""WITH {LIV} SELECT COUNT(*) FROM (SELECT patient_id FROM utilization_event WHERE kind = 'inpatient' AND {D.in_year('event_date')} AND {LIVQ}
               GROUP BY patient_id HAVING COUNT(*) >= 2)""")

    b.absent(PCQ, 'absent_cervical', "What's our cervical cancer screening rate for women 21 to 64?", 'There are no Pap or HPV test results or codes in the warehouse.',
             ['molina_quality', 'hedis_cdc_centene'], notes='cancer_screening holds FIT and mammography only')
    b.absent(PCQ, 'absent_door_to_provider', "What's the median ED door-to-provider time in minutes?", 'ED visits carry arrival and departure times but no time seen by a provider.',
             ['claims_kpis_knowi'], notes='visit_group has ed_arrival / ed_departure, no provider-seen timestamp')
    b.absent(PCQ, 'absent_genetic_pop', "How many patients have a positive BRCA result on file?", 'The warehouse has no genetic or genomic results.',
             ['aco_reach'], notes='no genetic result table or measurement concept')


# ====================================================================================================================================
def build_cps(b: Builder):
    ctx, a, one = b.ctx, b.add, b.ctx.one
    C = COUNTED_CLAIMS
    PC = D.PHARMACY_FILLS
    LIVP = "(SELECT patient_id FROM patient WHERE patient_id NOT IN (SELECT patient_id FROM death) AND data_segment <> 'duplicate_chart')"

    a(CPS, 'pmpm_trend', "Can you give me total medical PMPM for 2028 next to 2029?", 'table', ['claims_kpis_knowi', 'claims_trend_simplify', 'claims_trend_cassidy'],
      sql=f"""SELECT CAST(y AS INT) AS year, ROUND(paid / mm, 2) AS pmpm_usd FROM (
               SELECT y, (SELECT SUM(paid_cents) / 100.0 FROM ({C}) WHERE substr(service_from, 1, 4) = y) AS paid,
                         (SELECT COUNT(*) FROM member_month WHERE substr(month, 1, 4) = y) AS mm
                 FROM (SELECT '2028' AS y UNION ALL SELECT '2029')) ORDER BY y""",
      tolerance={'rel': 0.005, 'abs': 0.5}, order_matters=True, notes='counted paid / member-month rows of the year (all members)')

    a(CPS, 'active_enrollment', "How many members have active coverage as of today's data date?", 'count', ['claims_kpis_knowi'],
      sql=f"SELECT COUNT(DISTINCT patient_id) FROM enrollment_span WHERE coverage_start <= '{D.AS_OF}' AND (coverage_end IS NULL OR coverage_end >= '{D.AS_OF}')",
      notes='distinct patients with an enrollment span covering the as-of date (the living count is identical)')

    a(CPS, 'member_months_lob', "Member months in 2029 by line of business, please", 'table', ['claims_kpis_knowi'],
      sql=f"SELECT line_of_business, COUNT(*) AS member_months FROM member_month WHERE {D.in_year('month')} GROUP BY 1 ORDER BY 2 DESC",
      tolerance={'abs': 0.5}, notes='member_month rows dated in 2029 per line of business as stored')

    a(CPS, 'top_dx_paid', "What are the top 5 primary diagnosis codes driving paid spend in 2029?", 'table', ['claims_trend_cassidy', 'claims_kpis_knowi'],
      sql=f"""SELECT principal_diagnosis AS diagnosis, ROUND(SUM(paid_cents) / 100.0, 2) AS paid_usd FROM ({C}) WHERE {D.in_year('service_from')} AND principal_diagnosis IS NOT NULL
               GROUP BY 1 ORDER BY 2 DESC LIMIT 5""", tolerance={'rel': 0.001, 'abs': 1.0}, order_matters=True,
      notes='payer_claim.principal_diagnosis, counted claims, service year 2029')

    a(CPS, 'top_drg', "Top three DRGs by paid dollars for 2029?", 'table', ['claims_trend_cassidy', 'aco_reach'],
      sql=f"SELECT drg, ROUND(SUM(paid_cents) / 100.0, 2) AS paid_usd FROM ({C}) WHERE {D.in_year('service_from')} AND drg IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
      tolerance={'rel': 0.001, 'abs': 1.0}, order_matters=True)

    a(CPS, 'top10_members', "What did our ten most expensive members cost in total for 2029?", 'value', ['rising_risk_ajmc', 'healthcatalyst_caremgmt'],
      sql=f"SELECT ROUND(SUM(p) / 100.0, 2) FROM (SELECT SUM(paid_cents) p FROM ({C}) WHERE {D.in_year('service_from')} GROUP BY patient_id ORDER BY p DESC LIMIT 10)",
      unit='USD', tolerance={'rel': 0.005, 'abs': 1.0}, notes='counted 2029 paid per patient, the ten largest summed (all members, claims grain)')

    a(CPS, 'allowed_to_billed', "Across all counted claims in 2029, what share of billed charges ends up as allowed amount?", 'share', ['claims_kpis_knowi'],
      sql=f"SELECT SUM(allowed_cents), SUM(charge_cents) FROM ({C}) WHERE {D.in_year('service_from')}", tolerance={'abs': 0.01},
      notes='sum allowed / sum charge over counted 2029 claims')

    a(CPS, 'urgent_care_claims', "How many urgent care claims (place of service 20) did we have in 2029?", 'count', ['claims_kpis_knowi'],
      sql=f"SELECT COUNT(*) FROM ({C}) WHERE place_of_service = '20' AND {D.in_year('service_from')}")

    a(CPS, 'rx_paid_2029', "What did the plan pay out on pharmacy claims in 2029?", 'value', ['claims_kpis_knowi', 'claims_trend_simplify'],
      sql=f"SELECT ROUND(SUM(plan_paid_cents) / 100.0, 2) FROM ({PC}) WHERE {D.in_year('fill_date')}", unit='USD', tolerance={'rel': 0.002, 'abs': 1.0})
    a(CPS, 'rx_pmpm_2029', "what does that work out to PMPM?", 'value', ['claims_kpis_knowi'], follow_up_of='rx_paid_2029',
      sql=f"SELECT ROUND((SELECT SUM(plan_paid_cents) / 100.0 FROM ({PC}) WHERE {D.in_year('fill_date')}) / (SELECT COUNT(*) FROM member_month WHERE {D.in_year('month')}), 2)",
      unit='USD', tolerance={'rel': 0.005, 'abs': 0.05}, standalone='What was the pharmacy plan-paid PMPM (per member per month) in 2029?')

    a(CPS, 'ninety_day_share', "What share of paid fills in 2029 were 90-day supplies?", 'share', ['claims_trend_simplify', 'stars_pdc'],
      sql=f"SELECT SUM(days_supply >= 84), COUNT(*) FROM ({PC}) WHERE {D.in_year('fill_date')}", tolerance={'abs': 0.005},
      notes='90-day supply = days_supply of 84 or more')

    a(CPS, 'top_pharmacies', "Which five pharmacies took the most plan-paid dollars in 2029?", 'table', ['claims_kpis_knowi'],
      sql=f"SELECT pharmacy_name, ROUND(SUM(plan_paid_cents) / 100.0, 2) AS plan_paid_usd FROM ({PC}) WHERE {D.in_year('fill_date')} GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
      tolerance={'rel': 0.001, 'abs': 1.0}, order_matters=True)

    a(CPS, 'ozempic_spend', "How much did we spend on Ozempic in 2029?", 'value', ['claims_trend_cassidy'],
      sql=f"SELECT ROUND(SUM(plan_paid_cents) / 100.0, 2) FROM ({PC}) WHERE {D.in_year('fill_date')} AND drug_name LIKE '%Ozempic%'", unit='USD',
      tolerance={'rel': 0.002, 'abs': 1.0}, notes='plan-paid on counted fills whose drug name is Ozempic (semaglutide pen)')

    a(CPS, 'reject_by_tier', "What share of brand pharmacy claims were rejected in 2029, versus generic?", 'table', ['claims_kpis_knowi', 'molina_data'],
      sql=f"""SELECT formulary_tier AS tier, ROUND(SUM(claim_status = 'rejected') * 1.0 / COUNT(*), 4) AS reject_rate FROM payer_pharmacy_claim
               WHERE formulary_tier IN ('brand', 'generic') AND {D.in_year('fill_date')} GROUP BY 1 ORDER BY 1""", tolerance={'abs': 0.001},
      notes='rejected claims / every pharmacy claim row of that tier dated in 2029 (paid, rejected, reversed, recouped)')

    # Part D adherence: PDC >= 80% with at least two fills in 2029 (python, from fill rows)
    def adherence(classes):
        marks = ', '.join(f"'{c}'" for c in classes)
        rows = ctx.rows(f"SELECT patient_id, fill_date, days_supply FROM fill WHERE drug_class IN ({marks}) AND substr(fill_date, 1, 4) = '2029' "
                        f"AND patient_id IN {LIVP}")
        by = {}
        for p, d, s in rows:
            by.setdefault(p, []).append((d, s))
        vals = [pdc(v, '2029-01-01', '2029-12-31') for v in by.values()]
        vals = [x for x in vals if x is not None]
        return sum(1 for x in vals if x >= 0.8), len(vals)

    num, den = adherence(['statin'])
    a(CPS, 'statin_adherence', "Part D statin adherence: of members with at least two statin fills in 2029, what share had a PDC of 80% or better?", 'share',
      ['stars_pdc', 'pdc_outcomes'], answer=num / den, numerator=num, denominator=den, tolerance={'abs': 0.01},
      sql="python: fill rows of class statin dated 2029 per living patient; PDC = covered days / days from first 2029 fill to 2029-12-31 (carry-forward); >= 2 fills",
      notes='Star-rating style PDC computed in Python; exclusions (hospice / ESRD) are not applicable in this warehouse')
    num, den = adherence(['ace_inhibitor', 'arb'])
    a(CPS, 'ras_adherence', "and the same for RAS antagonists (ACE inhibitors and ARBs)?", 'share', ['stars_pdc'], follow_up_of='statin_adherence',
      answer=num / den, numerator=num, denominator=den, tolerance={'abs': 0.01},
      sql="python: fill rows of classes ace_inhibitor + arb dated 2029 pooled per living patient; PDC >= 0.8, >= 2 fills",
      standalone='Of members with at least two ACE inhibitor or ARB fills in 2029 (pooled as one RAS antagonist class), what share had a PDC of 80% or better?')

    a(CPS, 'high_svi_count', "How many of our patients live in a county with an overall SVI of 75 or above?", 'count', ['cdc_svi'],
      sql=f"SELECT COUNT(*) FROM patient_svi WHERE svi_overall >= {D.HIGH_SVI} AND patient_id IN {LIVP}", notes='patient_svi.svi_overall is a 0-100 percentile')

    a(CPS, 'svi_by_coverage', "Average SVI for commercial, Medicare and Medicaid patients", 'table', ['cdc_svi'],
      sql=f"""SELECT CASE WHEN p.coverage_type IN ('medicare', 'dual', 'medigap') THEN 'medicare' ELSE p.coverage_type END AS payer_type, ROUND(AVG(s.svi_overall), 2) AS avg_svi
               FROM patient p JOIN patient_svi s USING (patient_id) WHERE p.patient_id IN {LIVP} AND p.coverage_type IN ('commercial', 'medicare', 'dual', 'medigap', 'medicaid')
               GROUP BY 1 ORDER BY 1""", tolerance={'abs': 0.1},
      notes='Medicare = medicare + dual + medigap; living patients with an SVI')

    a(CPS, 'housing_safety', "How many patients flagged a housing or personal safety need on their screening?", 'count', ['cdc_svi', 'healthcatalyst_caremgmt'],
      sql=f"SELECT COUNT(DISTINCT patient_id) FROM ({D.POSITIVE_NEED}) WHERE need IN ('housing', 'safety') AND patient_id IN {LIVP}",
      notes='distinct living patients with a positive housing or safety screen')

    a(CPS, 'utilities_and_food', "Of patients with a positive utilities screen, what fraction also screened positive for food insecurity?", 'share', ['cdc_svi', 'healthcatalyst_caremgmt'],
      sql=f"""SELECT SUM(patient_id IN (SELECT patient_id FROM ({D.POSITIVE_NEED}) WHERE need = 'food')), COUNT(*)
               FROM (SELECT DISTINCT patient_id FROM ({D.POSITIVE_NEED}) WHERE need = 'utilities' AND patient_id IN {LIVP})""", tolerance={'abs': 0.01})

    a(CPS, 'medicaid_fin_stress', "In the purchased SDOH data, what's the average financial stress score for our Medicaid patients?", 'value', ['cdc_svi'],
      sql=f"WITH {D.SDOH_LATEST_CTE} SELECT ROUND(AVG(s.financial_stress_score), 3) FROM sdoh_latest s JOIN patient p USING (patient_id) WHERE p.coverage_type = 'medicaid' AND p.patient_id IN {LIVP}",
      unit='score (1-10)', tolerance={'abs': 0.05}, notes='each patient\'s latest matched vendor delivery; living Medicaid patients')

    a(CPS, 'housing_and_food', "How many patients have a vendor housing-instability score of 7 or more and also screened positive for food insecurity?", 'count', ['cdc_svi'],
      sql=f"""WITH {D.SDOH_LATEST_CTE} SELECT COUNT(*) FROM sdoh_latest s WHERE s.housing_instability_score >= {D.VENDOR_HIGH_RISK}
               AND s.patient_id IN (SELECT patient_id FROM ({D.POSITIVE_NEED}) WHERE need = 'food') AND s.patient_id IN {LIVP}""",
      notes='latest matched vendor row per patient; positive food screen any time')

    a(CPS, 'cost_need_ed', "How many patients who said cost of care was a problem went to the ED in the last year?", 'count', ['healthcatalyst_caremgmt', 'rising_risk_ajmc'],
      sql=f"""SELECT COUNT(DISTINCT n.patient_id) FROM ({D.POSITIVE_NEED}) n JOIN ({D.ED_VISITS}) e USING (patient_id)
               WHERE n.need = 'cost_of_care' AND {D.in_last_year('e.event_date')} AND n.patient_id IN {LIVP}""", notes='positive cost_of_care screen; ED visit in the 12 months ending the as-of date')

    a(CPS, 'reversed_recouped', "How many claims were reversed or recouped for dates of service in 2029?", 'count', ['claims_kpis_knowi'],
      sql=f"SELECT COUNT(*) FROM ({C}) WHERE final_status IN ('reversed', 'recouped') AND {D.in_year('service_from')}", notes='counted claims by final status')

    a(CPS, 'oon_ed_share', "What share of ER claims (place of service 23) in 2029 were out of network?", 'share', ['claims_kpis_knowi', 'claims_trend_simplify'],
      sql=f"SELECT SUM(network_status = 'out_of_network'), COUNT(*) FROM ({C}) WHERE place_of_service = '23' AND {D.in_year('service_from')}", tolerance={'abs': 0.005})

    a(CPS, 'top_counties', "Top 3 counties by number of pateints?", 'table', ['cdc_svi'],
      sql=f"SELECT county, COUNT(*) AS patients FROM patient WHERE patient_id IN {LIVP} AND county IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
      tolerance={'abs': 0.5}, order_matters=True, notes='patient.county FIPS code of living patients')

    a(CPS, 'ma_vs_ffs', "What did Medicare Advantage cost us in 2029 compared with traditional fee-for-service Medicare?", 'table', ['claims_kpis_knowi', 'claims_trend_simplify'],
      sql=f"""SELECT product, ROUND(SUM(paid_cents) / 100.0, 2) AS paid_usd FROM ({C}) WHERE {D.in_year('service_from')} AND product IN ('medicare_advantage', 'medicare_ffs')
               GROUP BY 1 ORDER BY 1""", tolerance={'rel': 0.002, 'abs': 1.0}, notes='payer_claim.product; traditional FFS = medicare_ffs only')

    b.absent(CPS, 'absent_rebates', "How much did we recover in pharmacy rebates in 2029, and what does that do to net cost per fill?",
             'The warehouse has plan-paid pharmacy amounts but no manufacturer rebate or net-cost data.', ['claims_trend_simplify'],
             notes='no rebate / net drug cost column in payer_pharmacy_claim or any other table')
    b.absent(CPS, 'absent_medicare_pct', "What do our commercial hospital contracts pay as a percentage of Medicare rates?",
             'Allowed amounts exist but there is no Medicare fee schedule or contract rate table to compare against.', ['claims_trend_cassidy'],
             notes='no fee schedule / contract rate data')
    b.absent(CPS, 'absent_satisfaction', "What's the overall patient satisfaction score by practice?",
             'There are no patient-experience or satisfaction survey results in the warehouse.', ['aco_reach'], notes='no survey / CAHPS tables')


# ====================================================================================================================================
def build_ins(b: Builder):
    ctx, a, one, rows = b.ctx, b.add, b.ctx.one, b.ctx.rows
    LIVP = "(SELECT patient_id FROM patient WHERE patient_id NOT IN (SELECT patient_id FROM death) AND data_segment <> 'duplicate_chart')"
    C = COUNTED_CLAIMS
    pct = lambda x: f'{100 * x:.1f}%'

    def ins(key, text, src, facts, sql, notes='', forbidden=(), variations='', **kw):
        a(INS, key, text, 'rubric', src, answer=fact_rubric(facts, forbidden=forbidden, variations=variations), sql=sql, notes=notes, **kw)

    # 1: readmissions, overall + hospital (follow-up)
    rate = one(f"SELECT AVG(readmitted_30d) FROM utilization_event WHERE kind = 'inpatient' AND {D.in_year('event_date')} AND patient_id IN {LIVP}")
    cond_rates = []
    for name in ('diabetes', 'heart_failure', 'ckd', 'copd', 'hypertension'):
        r = one(f"{W(name)} SELECT AVG(u.readmitted_30d) FROM utilization_event u WHERE u.kind = 'inpatient' AND {D.in_year('u.event_date')} "
                f"AND u.patient_id IN (SELECT patient_id FROM has_{name})")
        cond_rates.append((r, name))
    cond_rates.sort(reverse=True)
    top_c = cond_rates[0]
    words = {'diabetes': r'diabet', 'heart_failure': r'heart failure|\bHF\b|CHF', 'ckd': r'CKD|kidney', 'copd': r'COPD|chronic obstructive', 'hypertension': r'hypertens|\bHTN\b'}
    ins('readmit_drivers', "What's behind our readmission rate? Where are the 30-day bounce-backs coming from?", ['aco_reach', 'ahrq_pqi'],
        [fact_n('overall', f'30-day readmission rate on 2029 admissions is {pct(rate)}', rate, abs_=0.01, unit='proportion', near=[r'readmi']),
         fact_n('top_cond', f'{top_c[1]} patients readmit most among the major conditions ({pct(top_c[0])})', top_c[0], abs_=0.015, unit='proportion', near=[words[top_c[1]]]),
         fact_t('top_cond_named', f'the answer names {top_c[1]} as the highest-readmission condition group', [words[top_c[1]]])],
        sql='2029 inpatient utilization_event readmitted_30d overall and by condition cohort (diabetes, HF, CKD, COPD, hypertension; living patients)',
        notes='conditions compared: ' + ', '.join(f'{n} {pct(r)}' for r, n in cond_rates))
    hosp = rows(f"""SELECT organization_id, COUNT(*), AVG(readmitted_30d) FROM utilization_event WHERE kind = 'inpatient' AND {D.in_year('event_date')} AND patient_id IN {LIVP}
                    GROUP BY 1 HAVING COUNT(*) >= 20 ORDER BY 3 DESC""")
    h1, h2 = hosp[0], hosp[1]
    ins('readmit_hospital', "is it concentrated in one hospital?", ['aco_reach'],
        [fact_t('top_hospital', f'{h1[0]} has the highest 2029 readmission rate ({pct(h1[2])} of {h1[1]} stays)', [lit(h1[0])]),
         fact_n('top_rate', f'{h1[0]} readmission rate {pct(h1[2])}', h1[2], abs_=0.015, unit='proportion', near=[lit(h1[0])]),
         fact_n('second_rate', f'{h2[0]} is next at {pct(h2[2])}', h2[2], abs_=0.015, unit='proportion', near=[lit(h2[0])])],
        sql='2029 inpatient events by organization_id with >= 20 stays: readmitted_30d rate', follow_up_of='readmit_drivers',
        standalone='Are 30-day readmissions on 2029 admissions concentrated in one hospital? Compare hospitals with at least 20 admissions.',
        notes='the spread between hospitals is small (all within ~6 points); the answer should name the top hospital and give the rates')

    # 3: false premise: admissions fell
    ip28 = one(f"SELECT COUNT(*) FROM utilization_event WHERE kind = 'inpatient' AND {D.in_year('event_date', '2028')}")
    ip29 = one(f"SELECT COUNT(*) FROM utilization_event WHERE kind = 'inpatient' AND {D.in_year('event_date')}")
    ins('admits_premise', "Why did hospital admissions jump in 2029 compared with 2028?", ['aco_reach'],
        [fact_n('a28', f'Inpatient admissions in 2028 were {ip28}', ip28, abs_=0),
         fact_n('a29', f'Inpatient admissions in 2029 were {ip29}', ip29, abs_=0),
         fact_t('direction', 'admissions fell (did not jump) from 2028 to 2029: the premise is false',
                [r'\b(fell|fall|declin\w*|decreas\w*|dropp?\w*|lower|down)\b', r"(did not|didn't|didn’t|no) (actually )?(jump|rise|increase)", r'premise'])],
        sql='inpatient utilization_event counts by admit year', variations='any wording that corrects the premise; drivers of the decline are optional',
        notes=f'FALSE PREMISE: inpatient admissions fell from {ip28} (2028) to {ip29} (2029); all patients counted')

    # 4: ED concentration: how few patients make up half the visits
    ed = rows(f"SELECT patient_id, COUNT(*) c FROM utilization_event WHERE kind = 'ed_visit' AND {D.in_last_year('event_date')} AND patient_id IN {LIVP} GROUP BY 1 ORDER BY c DESC, patient_id")
    tot = sum(c for _, c in ed)
    run, k = 0, 0
    for _, c in ed:
        run += c
        k += 1
        if run >= tot / 2:
            break
    top5 = sum(c for _, c in ed[:max(1, round(0.05 * len(ed)))]) / tot
    ins('ed_concentration', "Is our ED volume driven by a small group of patients? How concentrated is it?", ['rising_risk_ajmc', 'nachc_risk_strat'],
        [fact_n('users', f'{len(ed)} patients had at least one ED visit in the last 12 months', len(ed), abs_=0, near=[r'patients?']),
         fact_n('half', f'The most frequent {k} ED users account for half of the {tot} visits', k, abs_=2, near=[r'half|50']),
         fact_n('top5', f'The top 5% of ED users ({max(1, round(0.05 * len(ed)))} patients) account for {pct(top5)} of visits', top5, abs_=0.02, unit='proportion', near=[r'5%|five percent|top 5'])],
        sql='ED visits per living patient in the 12 months ending the as-of date, ordered by count',
        notes=f'{tot} ED visits among {len(ed)} living patients; fact "half" = smallest number of patients covering 50% of visits (ties broken by patient id; +/-2 tolerated)')

    # 5: weakest practices on statin use among diabetics
    prs = rows(f"""WITH {LIV}, {D.condition_cte('diabetes')}, {D.PRACTICE_CTE}
        SELECT po.practice_id, SUM(h.patient_id IN ({D.filled_class_last_year(['statin'])})) * 1.0 / COUNT(*), COUNT(*)
          FROM has_diabetes h JOIN patient p USING (patient_id) JOIN practice_of po USING (patient_id)
         WHERE {D.age_of('p')} BETWEEN 40 AND 75 AND po.practice_id IS NOT NULL GROUP BY 1 HAVING COUNT(*) >= 15 ORDER BY 2, 1""")
    w1, w2 = prs[0], prs[1]
    ins('statin_practice_gap', "Which practices are lagging on statin use in diabetics 40 to 75, and how far behind are they?", ['hedis_cdc_centene', 'molina_quality'],
        [fact_t('lowest', f'{w1[0]} has the lowest diabetic statin share ({pct(w1[1])})', [lit(w1[0])]),
         fact_n('lowest_rate', f'{w1[0]} share {pct(w1[1])} (n={w1[2]})', w1[1], abs_=0.015, unit='proportion', near=[lit(w1[0])]),
         fact_n('second_rate', f'{w2[0]} next lowest at {pct(w2[1])}', w2[1], abs_=0.015, unit='proportion', near=[lit(w2[0])]),
         fact_rank('order', f'{w1[0]} below {w2[0]}', [lit(w1[0]), lit(w2[0])])],
        sql='diabetics 40-75 with a statin fill in the last 12 months by practice (PCP practice else attributing practice), practices with >= 15 diabetics',
        notes='all practices: ' + ', '.join(f'{p} {pct(r)} (n={c})' for p, r, c in prs))

    # 6: no-show rates by payer type and by practice
    ns = rows(f"""SELECT CASE WHEN p.coverage_type IN ('medicare', 'dual', 'medigap') THEN 'medicare' ELSE p.coverage_type END g,
                         AVG(a.final_status = 'no_show'), COUNT(*)
                    FROM appointment a JOIN patient p USING (patient_id)
                   WHERE a.final_status IN ('fulfilled', 'attended', 'arrived', 'no_show') AND {D.in_year('a.scheduled_date')}
                     AND p.coverage_type IN ('commercial', 'medicare', 'dual', 'medigap', 'medicaid') GROUP BY 1 ORDER BY 2 DESC""")
    nsd = {g: (r, c) for g, r, c in ns}
    ins('noshow_payer', "Who's missing the most appointments — is it a payer thing?", ['claims_kpis_knowi', 'healthcatalyst_caremgmt'],
        [fact_t('top_group', f'{ns[0][0]} patients have the highest 2029 no-show rate ({pct(ns[0][1])})', [ns[0][0]]),
         *[fact_n(f'rate_{g}', f'{g} no-show rate {pct(r)}', r, abs_=0.01, unit='proportion', near=[g]) for g, (r, c) in nsd.items()]],
        sql="2029 appointments with final_status in (fulfilled, attended, arrived, no_show): no_show share by patient payer type (Medicare = medicare + dual + medigap)",
        notes='rate = no_show / (fulfilled + attended + arrived + no_show) for appointments scheduled in 2029')

    # 7: transportation barrier vs no-shows
    tr = rows(f"""SELECT t.patient_id IS NOT NULL AS barrier, AVG(a.final_status = 'no_show'), COUNT(*) FROM appointment a LEFT JOIN (SELECT DISTINCT patient_id FROM social_need WHERE need = 'transport' AND positive = 1) t USING (patient_id)
                   WHERE a.final_status IN ('fulfilled', 'attended', 'arrived', 'no_show') AND {D.in_last_year('a.scheduled_date')} AND a.patient_id IN {LIVP} GROUP BY 1 ORDER BY 1""")
    r_no, r_yes = tr[0][1], tr[1][1]
    ins('transport_noshow', "Do patients with a transportation barrier actually miss more visits than everyone else?", ['cdc_svi', 'healthcatalyst_caremgmt'],
        [fact_n('barrier', f'No-show rate for patients who screened positive for a transportation barrier: {pct(r_yes)}', r_yes, abs_=0.01, unit='proportion', near=[r'transport|barrier']),
         fact_n('no_barrier', f'No-show rate for everyone else: {pct(r_no)}', r_no, abs_=0.01, unit='proportion', near=[r'everyone else|other|without|no barrier|not']),
         fact_t('direction', f'transport-barrier patients no-show {"more" if r_yes > r_no else "less"} often', [r'\b(more|higher|greater)\b'] if r_yes > r_no else [r'\b(less|lower|fewer|not more)\b'])],
        sql='appointments scheduled in the last 12 months (fulfilled / attended / arrived / no_show) by whether the living patient has ever screened positive for transport',
        notes=f'no_show share {pct(r_yes)} (barrier, n={tr[1][2]}) vs {pct(r_no)} (no barrier, n={tr[0][2]})')

    # 8: PMPM change 2028 -> 2029 and what moved
    def pm(y):
        return one(f"SELECT SUM(paid_cents) / 100.0 FROM ({C}) WHERE substr(service_from, 1, 4) = '{y}'") / one(f"SELECT COUNT(*) FROM member_month WHERE substr(month, 1, 4) = '{y}'")
    p28, p29 = pm('2028'), pm('2029')
    prod = rows(f"""SELECT product, SUM(CASE WHEN substr(service_from, 1, 4) = '2029' THEN paid_cents END) / 100.0, SUM(CASE WHEN substr(service_from, 1, 4) = '2028' THEN paid_cents END) / 100.0
                      FROM ({C}) GROUP BY 1 ORDER BY (SUM(CASE WHEN substr(service_from, 1, 4) = '2029' THEN paid_cents END) - SUM(CASE WHEN substr(service_from, 1, 4) = '2028' THEN paid_cents END)) DESC""")
    pr = prod[0]
    ins('pmpm_change', "What moved our medical PMPM between 2028 and 2029?", ['claims_trend_simplify', 'claims_trend_cassidy'],
        [fact_n('p28', f'Medical paid PMPM in 2028 was ${p28:,.0f}', p28, rel=0.01, near=[r'2028']),
         fact_n('p29', f'Medical paid PMPM in 2029 was ${p29:,.0f}', p29, rel=0.01, near=[r'2029']),
         fact_t('top_product', f'{pr[0]} grew the most in dollars (+${pr[1] - pr[2]:,.0f})', [pr[0].replace('_', '[ _]')] + ([r'\bMA\b'] if pr[0] == 'medicare_advantage' else [])),
         fact_n('top_delta', f'{pr[0]} paid rose from ${pr[2]:,.0f} to ${pr[1]:,.0f}', pr[1] - pr[2], rel=0.05, near=[pr[0].replace('_', '[ _]')])],
        sql='counted payer claims by service year / member_month rows by year; paid change by product',
        notes='product-level paid change 2028 -> 2029: ' + ', '.join(f'{x[0]} {x[2]:,.0f}->{x[1]:,.0f}' for x in prod))

    # 9: GLP-1 / SGLT2 share of pharmacy cost
    g = r"(Ozempic|Mounjaro|Trulicity|Farxiga|Jardiance)"
    rx = lambda y, w='': one(f"SELECT SUM(plan_paid_cents) / 100.0 FROM ({D.PHARMACY_FILLS}) WHERE {D.in_year('fill_date', y)} {w}")
    like = " AND (drug_name LIKE '%Ozempic%' OR drug_name LIKE '%Mounjaro%' OR drug_name LIKE '%Trulicity%' OR drug_name LIKE '%Farxiga%' OR drug_name LIKE '%Jardiance%')"
    r28, r29, g29 = rx('2028'), rx('2029'), rx('2029', like)
    ins('glp1_sglt2_cost', "Are the GLP-1s and SGLT2s what's pushing up our pharmacy bill?", ['claims_trend_simplify', 'claims_trend_cassidy'],
        [fact_n('r28', f'Plan-paid pharmacy spend in 2028 was ${r28:,.0f}', r28, rel=0.01, near=[r'2028']),
         fact_n('r29', f'Plan-paid pharmacy spend in 2029 was ${r29:,.0f}', r29, rel=0.01, near=[r'2029']),
         fact_n('share', f'Ozempic, Mounjaro, Trulicity, Farxiga and Jardiance are {pct(g29 / r29)} of 2029 plan-paid pharmacy dollars (${g29:,.0f})', g29 / r29, abs_=0.03, unit='proportion', near=[r'GLP|SGLT|Ozempic|semaglutide|brand'])],
        sql='counted pharmacy fills plan_paid by fill year; share of 2029 from the five brand GLP-1 / SGLT2 products',
        notes='the five brands are the five largest brand drugs by 2029 plan-paid that belong to these two classes')

    # 10: HF GDMT gap by pillar
    pil = []
    den = one(f"{W('heart_failure')} SELECT COUNT(*) FROM has_heart_failure")
    for name, cls in D.GDMT_PILLARS.items():
        num = one(f"{W('heart_failure')} SELECT COUNT(*) FROM has_heart_failure h WHERE h.patient_id IN ({D.filled_class_last_year(cls)})")
        pil.append((num / den, name))
    pil.sort()
    lab = {'beta_blocker': r'beta[- ]?blocker', 'raas': r'RAAS|ACE|ARB|ARNI|renin', 'mra': r'MRA|mineralocorticoid|spironolactone', 'sglt2': r'SGLT2|SGLT-2|gliflozin'}
    ins('hf_pillar_gap', "Where's the biggest hole in our heart failure medication therapy?", ['aco_reach', 'molina_quality'],
        [fact_t('weakest', f'the weakest GDMT pillar is {pil[0][1]} ({pct(pil[0][0])} of HF patients)', [lab[pil[0][1]]]),
         fact_n('weakest_rate', f'{pil[0][1]} coverage is {pct(pil[0][0])}', pil[0][0], abs_=0.02, unit='proportion', near=[lab[pil[0][1]]]),
         fact_n('strongest_rate', f'{pil[-1][1]} coverage is {pct(pil[-1][0])}', pil[-1][0], abs_=0.02, unit='proportion', near=[lab[pil[-1][1]]])],
        sql='living HF patients with a fill in the last 12 months in each GDMT pillar class set',
        notes=f'HF denominator {den}; ' + ', '.join(f'{n} {pct(r)}' for r, n in pil))

    # 11: diabetes monitoring gaps
    h = f"{W('diabetes')}"
    no_a1c = one(f"{h} SELECT COUNT(*) FROM has_diabetes WHERE patient_id NOT IN (SELECT patient_id FROM ({D.any_lab('hba1c')}) WHERE {D.in_last_year('event_date')})")
    hi_nov = one(f"""{h} SELECT COUNT(*) FROM has_diabetes d JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' AND m.value_canonical > 9.0
                       WHERE d.patient_id NOT IN (SELECT patient_id FROM ({D.FOLLOW_UP_VISIT}) WHERE event_date > date('{D.AS_OF}', '-6 months') AND event_date <= '{D.AS_OF}')""")
    ins('diabetes_monitoring', "Which diabetics are slipping through on monitoring? Give me the sizes of the groups I should chase.", ['hedis_hbd_acp', 'previsit_mango'],
        [fact_n('no_a1c', f'{no_a1c} diabetics have no HbA1c in the last 12 months', no_a1c, abs_=0, near=[r'no (HbA1c|A1c)|without|not (had|tested)|missing|overdue']),
         fact_n('hi_no_visit', f'{hi_nov} diabetics with a latest A1c above 9 have had no office visit in the last 6 months', hi_nov, abs_=0, near=[r'above 9|over 9|>\s?9|uncontrolled|poor'])],
        sql='living diabetics: no A1c in the last year; latest A1c > 9 and no completed outpatient visit in the 6 months ending the as-of date',
        notes='both group sizes are patient counts')

    # 12: discharges without follow-up in the last 90 days
    lo, hi_ = '2030-02-26', '2030-05-13'
    nd = one(f"WITH {LIV} SELECT COUNT(*) FROM utilization_event u WHERE u.kind = 'inpatient' AND u.end_date >= '{lo}' AND u.end_date <= '{hi_}' AND u.patient_id IN (SELECT patient_id FROM living)")
    nf = one(f"""WITH {LIV} SELECT COUNT(*) FROM utilization_event u WHERE u.kind = 'inpatient' AND u.end_date >= '{lo}' AND u.end_date <= '{hi_}' AND u.patient_id IN (SELECT patient_id FROM living)
                   AND NOT EXISTS (SELECT 1 FROM ({D.FOLLOW_UP_VISIT}) v WHERE v.patient_id = u.patient_id AND v.event_date > u.end_date AND julianday(v.event_date) - julianday(u.end_date) <= 14)""")
    ins('discharge_gap', "Between 2030-02-26 and 2030-05-13, how many people left the hospital and then never got an outpatient visit within two weeks? Is that most of them?", ['aco_reach', 'nachc_risk_strat'],
        [fact_n('discharges', f'{nd} inpatient discharges in the window', nd, abs_=0, near=[r'discharg']),
         fact_n('no_follow', f'{nf} of them had no outpatient visit within 14 days ({pct(nf / nd)})', nf, abs_=0, near=[r'no (outpatient )?(follow|visit)|without|never'])],
        sql='inpatient events discharged in the window (living patients), no completed outpatient visit 1-14 days after discharge',
        notes=f'window chosen so the 14-day follow-up period ends on or before the as-of date; share {pct(nf / nd)}')

    # 13: ED use by age band
    bands = rows(f"""WITH {LIV} SELECT CASE WHEN {D.age_of('p')} < 18 THEN 'under 18' WHEN {D.age_of('p')} < 40 THEN '18 to 39' WHEN {D.age_of('p')} < 65 THEN '40 to 64' ELSE '65 plus' END band,
                           COUNT(DISTINCT p.patient_id),
                           (SELECT COUNT(*) FROM utilization_event u JOIN patient q USING (patient_id) WHERE u.kind = 'ed_visit' AND {D.in_last_year('u.event_date')}
                              AND q.patient_id IN (SELECT patient_id FROM living) AND (CASE WHEN {D.age_of('q')} < 18 THEN 'under 18' WHEN {D.age_of('q')} < 40 THEN '18 to 39' WHEN {D.age_of('q')} < 65 THEN '40 to 64' ELSE '65 plus' END) = (CASE WHEN {D.age_of('p')} < 18 THEN 'under 18' WHEN {D.age_of('p')} < 40 THEN '18 to 39' WHEN {D.age_of('p')} < 65 THEN '40 to 64' ELSE '65 plus' END))
                      FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) GROUP BY 1""")
    br = sorted(((v / n * 1000, k, v) for k, n, v in bands), reverse=True)
    pat = {'under 18': r'under 18|children|pediatric|kids', '18 to 39': r'18[- ]?(to|-|–)[- ]?39|young adult', '40 to 64': r'40[- ]?(to|-|–)[- ]?64|middle', '65 plus': r'65|older|senior|elderly'}
    ins('ed_age_band', "Which age group leans on the ED the most relative to its size?", ['rising_risk_ajmc', 'claims_trend_cassidy'],
        [fact_t('top_band', f'{br[0][1]} has the highest ED visits per 1,000 patients ({br[0][0]:.0f})', [pat[br[0][1]]]),
         fact_n('top_rate', f'{br[0][1]}: {br[0][0]:.0f} ED visits per 1,000 patients in the last 12 months', br[0][0], rel=0.03, near=[pat[br[0][1]]]),
         fact_n('low_rate', f'{br[-1][1]}: {br[-1][0]:.0f} per 1,000', br[-1][0], rel=0.05, near=[pat[br[-1][1]]])],
        sql='ED visits (utilization_event) in the last 12 months per 1,000 living patients by age band (<18, 18-39, 40-64, 65+)',
        notes='bands: ' + ', '.join(f'{k} {r:.0f}/1000' for r, k, v in br))

    # 14: inpatient share of spend and average inpatient claim
    inst = one(f"SELECT SUM(paid_cents) FILTER (WHERE admission_date IS NOT NULL) * 1.0 / SUM(paid_cents) FROM ({C}) WHERE {D.in_year('service_from')}")
    avg_ip = one(f"SELECT AVG(paid_cents) / 100.0 FROM ({C}) WHERE admission_date IS NOT NULL AND final_status = 'paid' AND {D.in_year('service_from')}")
    ins('inpatient_cost', "How much of our 2029 medical spend is hospital inpatient, and what does an average inpatient claim run us?", ['claims_trend_cassidy', 'claims_kpis_knowi'],
        [fact_n('share', f'Claims with an inpatient admission are {pct(inst)} of 2029 paid dollars', inst, abs_=0.02, unit='proportion', near=[r'inpatient|admission|hospital']),
         fact_n('avg', f'The average paid inpatient claim is ${avg_ip:,.0f}', avg_ip, rel=0.03, near=[r'average|avg|per claim|each'])],
        sql='counted 2029 payer claims with an admission_date: share of paid and average paid per paid claim',
        notes='inpatient claim = payer_claim.admission_date is not null; the average is over paid-status claims')

    # 15: denial pattern by place of service
    dn = rows(f"""SELECT place_of_service, AVG(final_status IN ('denied', 'denied_upheld')), COUNT(*) FROM ({C}) WHERE {D.in_year('service_from')} AND place_of_service IS NOT NULL
                   GROUP BY 1 HAVING COUNT(*) >= 500 ORDER BY 2 DESC, 1""")
    pos = {'11': 'office (11)', '81': 'independent lab (81)', '22': 'outpatient hospital (22)', '23': 'emergency room (23)', '24': 'ambulatory surgical center (24)',
           '20': 'urgent care (20)', '21': 'inpatient hospital (21)', '02': 'telehealth (02)', '41': 'ambulance (41)'}
    d1 = dn[0]
    POS_WORDS = {'11': 'office', '81': 'lab', '22': 'outpatient hospital', '23': 'emergency|ER\\b', '24': 'surgical|ASC', '20': 'urgent', '21': 'inpatient', '02': 'telehealth|virtual', '41': 'ambulance'}
    ins('denial_pos', "Where are claims getting denied — which place of service has the worst denial rate in 2029?", ['claims_kpis_knowi', 'claims_trend_simplify'],
        [fact_t('pos', f'place of service {d1[0]} has the highest denial rate ({pct(d1[1])})', [rf'\b{d1[0]}\b|' + POS_WORDS.get(d1[0], 'zzzz')]),
         fact_n('rate', f'denial rate at place of service {d1[0]} is {pct(d1[1])}', d1[1], abs_=0.01, unit='proportion', near=[rf'\b{d1[0]}\b|denial'])],
        sql='counted 2029 payer claims by place_of_service (>= 500 claims): share with final_status denied or denied_upheld',
        notes='all POS: ' + ', '.join(f'{p} {pct(r)} ({c})' for p, r, c in dn))

    # 16: flu vaccination by payer type
    fl = rows(f"""SELECT CASE WHEN p.coverage_type IN ('medicare', 'dual', 'medigap') THEN 'medicare' ELSE p.coverage_type END g,
                         AVG(EXISTS (SELECT 1 FROM immunization i WHERE i.patient_id = p.patient_id AND i.vaccine LIKE 'influenza%' AND {D.in_last_year('i.event_date')})), COUNT(*)
                    FROM patient p WHERE p.patient_id IN {LIVP} AND p.coverage_type IN ('commercial', 'medicare', 'dual', 'medigap', 'medicaid') GROUP BY 1 ORDER BY 2""")
    ins('flu_payer', "Is anyone lagging on flu shots — break it out commercial, Medicare and Medicaid?", ['awv_checklist', 'molina_quality'],
        [fact_t('lowest', f'{fl[0][0]} has the lowest flu vaccination share ({pct(fl[0][1])})', [fl[0][0]]),
         *[fact_n(f'rate_{g}', f'{g} flu vaccination share {pct(r)}', r, abs_=0.015, unit='proportion', near=[g]) for g, r, c in fl]],
        sql='living patients with an influenza immunization dated in the last 12 months by patient payer type', notes='denominator = every living patient of the payer type, all ages')

    # 17: complex patients and cost
    cc = rows(f"""WITH {LIV}, {cond('diabetes', 'hypertension', 'ckd', 'heart_failure', 'copd')},
          n AS (SELECT p.patient_id, (p.patient_id IN (SELECT patient_id FROM has_diabetes)) + (p.patient_id IN (SELECT patient_id FROM has_hypertension)) + (p.patient_id IN (SELECT patient_id FROM has_ckd))
                      + (p.patient_id IN (SELECT patient_id FROM has_heart_failure)) + (p.patient_id IN (SELECT patient_id FROM has_copd)) AS k FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living)),
          paid AS (SELECT patient_id, SUM(paid_cents) / 100.0 AS usd FROM ({C}) WHERE {D.in_year('service_from')} GROUP BY 1)
          SELECT k >= 3 AS multi, COUNT(*), AVG(COALESCE(usd, 0)) FROM n LEFT JOIN paid USING (patient_id) GROUP BY 1 ORDER BY 1""")
    ins('multimorbid_cost', "How much more do patients with three or more chronic conditions cost than the rest (diabetes, hypertension, CKD, heart failure, COPD)?", ['nachc_risk_strat', 'rising_risk_ajmc'],
        [fact_n('multi_n', f'{cc[1][1]} patients have three or more of the five conditions', cc[1][1], abs_=0, near=[r'three|3\+|3 or more']),
         fact_n('multi_avg', f'Average 2029 medical paid per patient for them is ${cc[1][2]:,.0f}', cc[1][2], rel=0.04, near=[r'three|3\+|3 or more|multi']),
         fact_n('other_avg', f'Average for everyone else is ${cc[0][2]:,.0f}', cc[0][2], rel=0.04, near=[r'rest|everyone else|other|fewer'])],
        sql='living patients by number of the five conditions (>= 3 vs fewer): average 2029 counted paid per patient (zero-claim patients included)',
        notes='average over every living patient in the group, including those with no 2029 claims')

    # 18: rising risk
    rr = one(f"""WITH {LIV} SELECT COUNT(*) FROM (SELECT patient_id FROM utilization_event WHERE kind = 'ed_visit' AND {D.in_last_year('event_date')} AND patient_id IN (SELECT patient_id FROM living)
                   GROUP BY patient_id HAVING COUNT(*) >= 2) WHERE patient_id NOT IN (SELECT patient_id FROM utilization_event WHERE kind = 'inpatient' AND {D.in_last_year('event_date')})""")
    rr_cost = one(f"""WITH {LIV} SELECT SUM(paid_cents) / 100.0 FROM ({C}) WHERE {D.in_last_year('service_from')} AND patient_id IN (
                   SELECT patient_id FROM (SELECT patient_id FROM utilization_event WHERE kind = 'ed_visit' AND {D.in_last_year('event_date')} AND patient_id IN (SELECT patient_id FROM living)
                   GROUP BY patient_id HAVING COUNT(*) >= 2) WHERE patient_id NOT IN (SELECT patient_id FROM utilization_event WHERE kind = 'inpatient' AND {D.in_last_year('event_date')}))""")
    ins('rising_risk', "Who's rising risk here? I mean two or more ED visits in the last year but no hospital admission yet — how many, and what do they cost?", ['rising_risk_ajmc', 'nachc_risk_strat'],
        [fact_n('n', f'{rr} patients had 2+ ED visits and no inpatient admission in the last 12 months', rr, abs_=0, near=[r'patients?|rising']),
         fact_n('cost', f'They accounted for ${rr_cost:,.0f} of medical paid in the last 12 months', rr_cost, rel=0.04, near=[r'paid|cost|spend|\$'])],
        sql='living patients with >= 2 ED visits and no inpatient event in the last 12 months; their counted paid in the same window',
        notes='rising-risk operationalised as in the question')

    # 19: high-SVI cost
    sv = rows(f"""WITH paid AS (SELECT patient_id, SUM(paid_cents) / 100.0 AS usd FROM ({C}) WHERE {D.in_year('service_from')} GROUP BY 1)
                  SELECT CASE WHEN s.svi_overall >= 75 THEN 'high' WHEN s.svi_overall < 25 THEN 'low' END g, COUNT(*), AVG(COALESCE(usd, 0)) FROM patient_svi s LEFT JOIN paid USING (patient_id)
                   WHERE s.patient_id IN {LIVP} AND s.svi_overall IS NOT NULL AND (s.svi_overall >= 75 OR s.svi_overall < 25) GROUP BY 1 ORDER BY 1""")
    sd = {g: (c, v) for g, c, v in sv}
    ins('svi_cost', "Do patients in the most socially vulnerable areas (SVI 75 and up) cost more than those in the least vulnerable (under 25)?", ['cdc_svi', 'healthcatalyst_caremgmt'],
        [fact_n('high', f'High-SVI patients average ${sd["high"][1]:,.0f} medical paid in 2029', sd['high'][1], rel=0.05, near=[r'high|75|vulnerable']),
         fact_n('low', f'Low-SVI patients average ${sd["low"][1]:,.0f}', sd['low'][1], rel=0.05, near=[r'low|under 25|least']),
         fact_t('direction', f'high-SVI patients cost {"more" if sd["high"][1] > sd["low"][1] else "less"}', [r'\b(more|higher|greater)\b'] if sd['high'][1] > sd['low'][1] else [r'\b(less|lower|not more|no more)\b'])],
        sql='living patients with an SVI: 2029 counted paid per patient averaged for SVI >= 75 and SVI < 25 (zero-claim patients included)',
        notes=f'groups n: high {sd["high"][0]}, low {sd["low"][0]}')

    # 20: ED diagnoses
    ed_dx = rows(f"""WITH {LIV} SELECT principal_icd10, COUNT(*) FROM visit_group WHERE visit_class IN ('emergency', 'ed_to_inpatient', 'ed_to_observation') AND principal_icd10 IS NOT NULL
                       AND {D.in_last_year('start_date')} AND patient_id IN (SELECT patient_id FROM living) GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3""")
    ins('ed_top_dx', "What are people actually coming to the ED for? Top three diagnosis codes in the last year.", ['claims_trend_cassidy', 'rising_risk_ajmc'],
        [fact_rank('order', 'top three ED principal diagnoses in order: ' + ', '.join(f'{c} ({n})' for c, n in ed_dx), [re.escape(c) for c, _ in ed_dx]),
         fact_n('top_n', f'{ed_dx[0][0]} was the principal diagnosis on {ed_dx[0][1]} ED visits', ed_dx[0][1], abs_=0, near=[re.escape(ed_dx[0][0])])],
        sql='visit_group rows that began in the ED (emergency, ed_to_inpatient, ed_to_observation) in the last 12 months by principal_icd10, living patients',
        notes='ties broken by code; top 3 codes shown in the order')

    # 21: ambulatory-care sensitive admissions (PQI-style)
    acsc = one(f"""WITH {LIV} SELECT COUNT(*) FROM visit_group WHERE has_inpatient = 1 AND {D.in_year('start_date')} AND patient_id IN (SELECT patient_id FROM living)
                    AND (principal_icd10 LIKE 'I50%' OR principal_icd10 LIKE 'J44%' OR principal_icd10 LIKE 'J45%' OR principal_icd10 LIKE 'E11.0%' OR principal_icd10 LIKE 'E11.1%' OR principal_icd10 LIKE 'E10.1%' OR principal_icd10 LIKE 'I10%')""")
    allip = one(f"""WITH {LIV} SELECT COUNT(*) FROM visit_group WHERE has_inpatient = 1 AND {D.in_year('start_date')} AND patient_id IN (SELECT patient_id FROM living)""")
    ins('acsc_share', "How many of our 2029 hospital stays were for the usual preventable suspects — heart failure, COPD, asthma, uncontrolled diabetes, hypertension — and what share of all stays is that?", ['ahrq_pqi'],
        [fact_n('n', f'{acsc} inpatient visits in 2029 had a principal diagnosis in those groups', acsc, abs_=0, near=[r'stays?|admissions?|visits?|preventable']),
         fact_n('share', f'That is {pct(acsc / allip)} of the {allip} inpatient visits', acsc / allip, abs_=0.01, unit='proportion', near=[r'%|percent|share'])],
        sql="visit_group rows with has_inpatient = 1 starting in 2029 (living patients); principal_icd10 in I50, J44, J45, E11.0/E11.1, E10.1, I10",
        notes='PQI-style ambulatory-care-sensitive set approximated by principal ICD-10 prefixes named in the key; denominator = all inpatient visit groups of 2029')

    # 22: colorectal screening gap by practice
    cr = rows(f"""WITH {LIV}, {D.PRACTICE_CTE}
        SELECT po.practice_id, AVG(EXISTS (SELECT 1 FROM ({D.COLORECTAL_EVIDENCE}) c WHERE c.patient_id = p.patient_id)), COUNT(*)
          FROM patient p JOIN practice_of po USING (patient_id) WHERE p.patient_id IN (SELECT patient_id FROM living) AND {D.age_of('p')} BETWEEN 45 AND 75 AND po.practice_id IS NOT NULL
         GROUP BY 1 HAVING COUNT(*) >= 30 ORDER BY 2, 1""")
    ins('crc_practice', "Which practice has the worst colorectal screening rate for 45 to 75 year olds, and which is best?", ['molina_quality', 'hedis_cdc_centene'],
        [fact_t('worst', f'{cr[0][0]} has the lowest colorectal screening rate ({pct(cr[0][1])})', [lit(cr[0][0])]),
         fact_n('worst_rate', f'{cr[0][0]}: {pct(cr[0][1])}', cr[0][1], abs_=0.015, unit='proportion', near=[lit(cr[0][0])]),
         fact_t('best', f'{cr[-1][0]} has the highest ({pct(cr[-1][1])})', [lit(cr[-1][0])]),
         fact_n('best_rate', f'{cr[-1][0]}: {pct(cr[-1][1])}', cr[-1][1], abs_=0.015, unit='proportion', near=[lit(cr[-1][0])])],
        sql='patients 45-75 by practice (PCP practice else attributing practice) with any FIT / colonoscopy evidence; practices with >= 30 patients',
        notes='all practices: ' + ', '.join(f'{p} {pct(r)} (n={c})' for p, r, c in cr))

    # 23: pharmacy rejects by payer
    rj = rows(f"""SELECT payer_id, AVG(claim_status = 'rejected'), COUNT(*) FROM payer_pharmacy_claim WHERE {D.in_year('fill_date')} AND claim_status IN ('paid', 'rejected') GROUP BY 1 HAVING COUNT(*) >= 1000 ORDER BY 2 DESC, 1""")
    rc = rows(f"SELECT reject_code, COUNT(*) FROM payer_pharmacy_claim WHERE {D.in_year('fill_date')} AND claim_status = 'rejected' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1")[0]
    ins('reject_payer', "Which payer's pharmacy claims bounce the most, and what's the usual reason?", ['claims_kpis_knowi', 'molina_data'],
        [fact_t('payer', f'{rj[0][0]} has the highest 2029 pharmacy reject rate ({pct(rj[0][1])})', [lit(rj[0][0])]),
         fact_n('rate', f'{rj[0][0]} reject rate {pct(rj[0][1])}', rj[0][1], abs_=0.01, unit='proportion', near=[lit(rj[0][0])]),
         fact_t('code', f'the most common reject code is {rc[0]} ({rc[1]} claims)', [re.escape(str(rc[0]))])],
        sql='2029 pharmacy claims (paid + rejected) by payer_id with >= 1,000 claims: rejected share; most common reject_code overall',
        notes='all payers: ' + ', '.join(f'{p} {pct(r)} ({c})' for p, r, c in rj) + f'; top reject code {rc}')

    # 24: composite diabetic care gaps
    gaps = one(f"""{W('diabetes')} SELECT COUNT(*) FROM (SELECT h.patient_id,
          (h.patient_id NOT IN (SELECT patient_id FROM ({D.any_lab('hba1c')}) WHERE {D.in_year('event_date')})) +
          (h.patient_id NOT IN (SELECT patient_id FROM ({D.any_lab('uacr')}) WHERE {D.in_year('event_date')})) +
          (h.patient_id NOT IN (SELECT patient_id FROM ({D.any_lab('egfr')}) WHERE {D.in_year('event_date')})) +
          (h.patient_id NOT IN (SELECT patient_id FROM fill WHERE drug_class = 'statin' AND {D.in_year('fill_date')})) AS g FROM has_diabetes h) WHERE g >= 3""")
    ins('diabetes_multi_gap', "How many diabetics have three or more open care gaps in 2029 — no A1c, no urine ACR, no eGFR, no statin fill?", ['hedis_cdc_centene', 'healthcatalyst_caremgmt'],
        [fact_n('n', f'{gaps} diabetics are missing at least three of the four 2029 items', gaps, abs_=0, near=[r'three|3|diabetic'])],
        sql='living diabetics: count of the four 2029 gaps (no A1c, no uACR, no eGFR, no statin fill in calendar 2029) >= 3')

    b.absent(INS, 'absent_raf', "Which of our patients have uncaptured HCC conditions we should chase for risk adjustment?", 'There are no HCC / RAF scores or risk-adjustment data in the warehouse.', ['aco_reach'],
             notes='diagnoses exist but no HCC mapping or risk-score tables')
    b.absent(INS, 'absent_provider_variation', "Why do our physicians' RVU productivity numbers vary so much between practices?", 'The warehouse has no RVU / wRVU or productivity measure per clinician.', ['claims_kpis_knowi'],
             notes='no RVU data')
    b.absent(INS, 'absent_sdoh_roi', "What was the return on investment of our community health worker program?", 'No care-management program, enrollment, intervention cost or outcome data exists in the warehouse.', ['healthcatalyst_caremgmt', 'rising_risk_ajmc'],
             notes='no program or intervention tables')


def build_ins2(b: Builder):
    ctx, a, one, rows = b.ctx, b.add, b.ctx.one, b.ctx.rows
    LIVP = "(SELECT patient_id FROM patient WHERE patient_id NOT IN (SELECT patient_id FROM death) AND data_segment <> 'duplicate_chart')"
    C = COUNTED_CLAIMS
    pct = lambda x: f'{100 * x:.1f}%'

    def ins(key, text, src, facts, sql, notes='', **kw):
        a(INS, key, text, 'rubric', src, answer=fact_rubric(facts), sql=sql, notes=notes, **kw)

    # chain on rising risk: how many are diabetic
    rr_sql = (f"SELECT patient_id FROM (SELECT patient_id FROM utilization_event WHERE kind = 'ed_visit' AND {D.in_last_year('event_date')} AND patient_id IN {LIVP} "
              f"GROUP BY patient_id HAVING COUNT(*) >= 2) WHERE patient_id NOT IN (SELECT patient_id FROM utilization_event WHERE kind = 'inpatient' AND {D.in_last_year('event_date')})")
    rd = one(f"{W('diabetes')} SELECT COUNT(*) FROM has_diabetes WHERE patient_id IN ({rr_sql})")
    rh = one(f"{W('hypertension')} SELECT COUNT(*) FROM has_hypertension WHERE patient_id IN ({rr_sql})")
    ins('rising_risk_conditions', "how many of them are diabetic, and how many hypertensive?", ['rising_risk_ajmc', 'nachc_risk_strat'],
        [fact_n('dm', f'{rd} of the rising-risk patients have diabetes', rd, abs_=0, near=[r'diabet']),
         fact_n('htn', f'{rh} of the rising-risk patients have hypertension', rh, abs_=0, near=[r'hypertens|\bHTN\b'])],
        sql='the rising-risk cohort (2+ ED visits, no inpatient in the last 12 months, living) intersected with has_diabetes / has_hypertension', follow_up_of='rising_risk',
        standalone='Among living patients with two or more ED visits and no hospital admission in the last 12 months, how many have diabetes and how many have hypertension?')

    # which conditions show up most among 2029 admissions
    shares = []
    tot = one(f"WITH {LIV} SELECT COUNT(*) FROM utilization_event WHERE kind = 'inpatient' AND {D.in_year('event_date')} AND patient_id IN (SELECT patient_id FROM living)")
    for name in ('diabetes', 'hypertension', 'heart_failure', 'ckd', 'copd', 'cad'):
        c = one(f"{W(name)} SELECT COUNT(*) FROM utilization_event u WHERE u.kind = 'inpatient' AND {D.in_year('u.event_date')} AND u.patient_id IN (SELECT patient_id FROM has_{name})")
        shares.append((c / tot, name, c))
    shares.sort(reverse=True)
    words = {'diabetes': r'diabet', 'heart_failure': r'heart failure|\bHF\b|CHF', 'ckd': r'CKD|kidney', 'copd': r'COPD|chronic obstructive', 'hypertension': r'hypertens|\bHTN\b', 'cad': r'CAD|coronary'}
    ins('admit_conditions', "Which chronic conditions show up most among our 2029 hospital admissions?", ['ahrq_pqi', 'nachc_risk_strat'],
        [fact_t('top', f'{shares[0][1]} patients account for the most admissions ({shares[0][2]} of {tot}, {pct(shares[0][0])})', [words[shares[0][1]]]),
         fact_n('top_share', f'{shares[0][1]}: {pct(shares[0][0])} of 2029 admissions', shares[0][0], abs_=0.02, unit='proportion', near=[words[shares[0][1]]]),
         fact_n('second_share', f'{shares[1][1]}: {pct(shares[1][0])} of 2029 admissions', shares[1][0], abs_=0.02, unit='proportion', near=[words[shares[1][1]]])],
        sql='2029 inpatient events of living patients, share whose patient has each condition (diabetes, hypertension, HF, CKD, COPD, CAD; overlapping)',
        notes='denominator = all 2029 admissions of living patients; a patient with several conditions counts under each: ' + ', '.join(f'{n} {pct(s)}' for s, n, c in shares))

    # most expensive month
    mo = rows(f"SELECT substr(service_from, 1, 7), ROUND(SUM(paid_cents) / 100.0, 2) FROM ({C}) WHERE {D.in_year('service_from')} GROUP BY 1 ORDER BY 2 DESC")
    names = {'01': 'January', '02': 'February', '03': 'March', '04': 'April', '05': 'May', '06': 'June', '07': 'July', '08': 'August', '09': 'September', '10': 'October', '11': 'November', '12': 'December'}
    m1, m2 = mo[0], mo[-1]
    ins('spend_month', "Which month of 2029 was our most expensive for medical claims, and which was the cheapest?", ['claims_trend_simplify', 'claims_kpis_knowi'],
        [fact_t('high', f'{names[m1[0][5:]]} 2029 was the most expensive month (${m1[1]:,.0f})', [names[m1[0][5:]] + r'|' + m1[0]]),
         fact_n('high_amt', f'${m1[1]:,.0f} paid in {names[m1[0][5:]]}', m1[1], rel=0.01, near=[names[m1[0][5:]] + r'|' + m1[0]]),
         fact_t('low', f'{names[m2[0][5:]]} 2029 was the cheapest month (${m2[1]:,.0f})', [names[m2[0][5:]] + r'|' + m2[0]]),
         fact_n('low_amt', f'${m2[1]:,.0f} paid in {names[m2[0][5:]]}', m2[1], rel=0.01, near=[names[m2[0][5:]] + r'|' + m2[0]])],
        sql='counted payer claims paid by service_from month in 2029', notes='months by paid: ' + ', '.join(f'{m} {v:,.0f}' for m, v in mo))


# ====================================================================================================================================
# Bucket 2: single-patient questions answered from note / document text. The keys are READ: each item below was written after reading the
# patient's documents; the evidence quote is located by a pattern in ONE named document (patient, document kind, date) and must match exactly
# once, so a changed warehouse fails loudly. Not-documented items are checked against EVERY document of the patient.
def patient_texts(ctx, pid):
    """[(record_id, kind, event_date, text)] for every note, HIE document and scanned document of the patient."""
    out = []
    for rid, d, pl in ctx.rows("SELECT n.record_id, n.event_date, r.payload FROM clinical_note n JOIN record r USING (record_id) WHERE n.patient_id = ? ORDER BY n.event_date", (pid,)):
        out.append((rid, 'note', d, json.loads(pl).get('text') or ''))
    for rid, d, pl in ctx.rows("SELECT h.record_id, h.event_date, r.payload FROM hie_document h JOIN record r USING (record_id) WHERE h.patient_id = ? ORDER BY h.event_date", (pid,)):
        out.append((rid, 'hie', d, json.loads(pl).get('text') or ''))
    for rid, d, tx in ctx.rows("SELECT s.record_id, s.event_date, t.text FROM scanned_document s JOIN scanned_document_text t USING (record_id) WHERE s.patient_id = ? ORDER BY s.event_date", (pid,)):
        out.append((rid, 'scan', d, tx or ''))
    return out


# (key, patient, doc kind, date, quote pattern, question, answer kind, answer, key_groups, follow_up_of, standalone)
NOTE_ITEMS = [
    ('u01', 'WHP-4D20127FAD4C', 'note', '2028-06-09', r'HbA1c 9\.6 %', "What did the HbA1c come back as in her June 2028 annual physical? Patient WHP-4D20127FAD4C.",
     'value', '9.6', [], None, None),
    ('u02', 'WHP-4D20127FAD4C', 'note', '2028-06-09', r'PHQ-9 7 today', "and her PHQ-9 that day?", 'value', '7', [], 'u01',
     "What PHQ-9 score was recorded at the June 2028 annual physical of WHP-4D20127FAD4C?"),
    ('u03', 'WHP-4D20127FAD4C', 'note', '2028-06-09', r'Increase SSRI dose\.', "did they bump the SSRI dose at that visit?", 'yes_no', 'yes', [], 'u02',
     "Was the SSRI dose increased at the June 2028 annual physical of WHP-4D20127FAD4C?"),
    ('u04', 'WHP-4D20127FAD4C', 'scan', '2029-11-05', r'IMPRESSION: BI-RADS 1: Negative\.', "Where did the November 2029 screening mammogram land for WHP-4D20127FAD4C?",
     'short_text', 'BI-RADS 1: Negative', [r'BI-?RADS 1|negative'], None, None),
    ('u05', 'WHP-4D20127FAD4C', 'scan', '2027-10-08', r'PLAN: I recommendedwe me1oxicam 15 mg daily as needed; PT referral\.',
     "Which anti-inflammatory did the orthopedics letter from October 2027 suggest for WHP-4D20127FAD4C?", 'short_text', 'meloxicam 15 mg daily as needed', [r'me[l1]oxicam'], None, None),
    ('u06', 'WHP-4D20127FAD4C', 'note', '2030-05-03', r'Reports cost has made it hard to keep up with refills\.',
     "At the May 2030 follow-up, did WHP-4D20127FAD4C say cost was making refills hard?", 'yes_no', 'yes', [], None, None),
    ('u07', 'WHP-8C11864F6E6E', 'note', '2028-09-25', r'Social: Current smoker\.', "Is WHP-8C11864F6E6E still smoking? It should be in the September 2028 annual visit.",
     'yes_no', 'yes', [], None, None),
    ('u08', 'SYN-E415665B-B79', 'note', '2027-05-10', r'Primary follow up recommended around 2027-05-17\.',
     "When did the May 2027 discharge paperwork for SYN-E415665B-B79 say to see primary care?", 'short_text', 'around 2027-05-17', [r'2027-05-17'], None, None),
    ('u09', 'SYN-E415665B-B79', 'note', '2027-05-10', r'amLODIPine 2\.5 MG Oral Tablet \(dose and frequency unconfirmed\); reported status: not using',
     "was he taking the amlodipine when he came in?", 'yes_no', 'no', [], 'u08',
     "At the May 2027 emergency visit, was SYN-E415665B-B79 reported to be using amlodipine?"),
    ('u10', 'SYN-1D9F8A3D-DB3', 'note', '2027-06-16', r'Serial hs-cTnI 8 and 8 ng/L; no significant rise in this series\.',
     "Did the troponin climb during SYN-1D9F8A3D-DB3's June 2027 ED visit for chest discomfort?", 'yes_no', 'no', [], None, None),
    ('u11', 'SYN-1D9F8A3D-DB3', 'note', '2027-06-16', r'ECG without acute ST elevation', "what did the ECG show?", 'short_text', 'ECG without acute ST elevation',
     [r'(without|no) acute ST'], 'u10', "What did the ECG show during the June 2027 ED visit of SYN-1D9F8A3D-DB3?"),
    ('u12', 'SYN-A1DE3B86-9B9', 'note', '2028-12-11', r'lisinopril 20 MG Oral Tablet: reports taking this medicine',
     "On the December 2028 medication history call, does SYN-A1DE3B86-9B9 say they take lisinopril?", 'yes_no', 'yes', [], None, None),
    ('u13', 'SYN-A1DE3B86-9B9', 'note', '2028-12-11', r'Furosemide 40 MG Oral Tablet: does not recognize this as a current medicine\.',
     "and furosemide?", 'yes_no', 'no', [], 'u12', "On the December 2028 medication history call, does SYN-A1DE3B86-9B9 say they take furosemide?"),
    ('u14', 'SYN-B2064C3E-566', 'note', '2027-05-13', r'fluticasone propionate HFA 44 mcg/actuation: reports use on 0 of 14 days',
     "How many of the last 14 days did SYN-B2064C3E-566 say they used the fluticasone inhaler, per the May 2027 callback?", 'value', '0', [], None, None),
    ('u15', 'SYN-1B30CAC9-6C6', 'note', '2030-02-26', r'Current self-harm thoughts denied\.',
     "Any current thoughts of self-harm at SYN-1B30CAC9-6C6's mood visit on 2030-02-26?", 'yes_no', 'no', [], None, None),
    ('u16', 'SYN-1B30CAC9-6C6', 'note', '2030-02-26', r'PHQ-9 10/27 today', "what was the PHQ-9 that day?", 'value', '10', [], 'u15',
     "What PHQ-9 score was recorded at the 2030-02-26 mood visit of SYN-1B30CAC9-6C6?"),
    ('u17', 'P-2004', 'note', '2029-11-26', r'PHQ-2 3/6\.', "What PHQ-2 score did P-2004 have at the November 2029 preventive review?", 'value', '3', [], None, None),
    ('u18', 'SYN-FDF7A02A-E8A', 'note', '2029-10-31', r'Lung: No smoking history meeting routine lung-screening criteria reported\.',
     "Does SYN-FDF7A02A-E8A meet criteria for lung cancer screening according to the October 2029 prevention review?", 'yes_no', 'no', [], None, None),
    ('u19', 'SYN-D9886E78-849', 'note', '2029-08-21', r'Pain reported 5/10, compared with 5/10 at examination', "What pain score did the nurse record when rechecking SYN-D9886E78-849's wrist on 2029-08-21?",
     'value', '5', [], None, None),
    ('u21', 'WHP-00E3D5B1C133', 'hie', '2027-12-27', r'Principal diagnosis: Type 2 diabetes with hyperglycemia',
     "What was WHP-00E3D5B1C133 admitted for in December 2027, per the outside discharge summary?", 'short_text', 'Type 2 diabetes with hyperglycemia', [r'diabetes', r'hyperglycemia'], None, None),
    ('u22', 'WHP-00E3D5B1C133', 'hie', '2027-12-27', r'Glucose 540 without DKA', "was it DKA?", 'yes_no', 'no', [], 'u21',
     "Was the December 2027 admission of WHP-00E3D5B1C133 for diabetic ketoacidosis (DKA)?"),
    ('u24', 'WHP-00E3D5B1C133', 'scan', '2028-03-23', r'PLAN: I recommended we start metoprolol succinate 25 mg daily\.',
     "What did the specialist letter from March 2028 recommend starting for WHP-00E3D5B1C133?", 'short_text', 'metoprolol succinate 25 mg daily', [r'metoprolol'], None, None),
    ('u25', 'WHP-00C2869A7EB5', 'scan', '2028-11-10', r'IMPRESSION: Smll centraldisc protrusion at L5-S1 without nervc root compression\.',
     "What was the impression on the November 2028 MRI report scanned in for WHP-00C2869A7EB5?", 'short_text', 'small central disc protrusion at L5-S1 without nerve root compression',
     [r'L5-S1', r'protrusion'], None, None),
    ('u26', 'WHP-00C2869A7EB5', 'scan', '2029-07-30', r'IMPRESSION: 4 mm right lower lobe pulmonary nodule', "how big was the lung nodule on the July 2029 imaging report for WHP-00C2869A7EB5, in mm?",
     'value', '4', [], None, None),
    ('u27', 'WHP-9A2E93F6FF24', 'note', '2029-07-02', r'My daughter thinks I should get checked\.', "Who pushed WHP-9A2E93F6FF24 to get seen at the July 2029 acute visit?",
     'short_text', 'his daughter', [r'daughter'], None, None),
]
# not documented: (key, patient, question, forbidden pattern (must match in NO document), allowed pattern (matches that are fine), follow-up)
NOT_DOCUMENTED = [
    ('u23', 'WHP-00E3D5B1C133', "What did surgical pathology show after WHP-00E3D5B1C133's colectomy in June 2028?", r'(?i)\bpatholog\w*\b(?! pending at discharge)', None),
    ('u28', 'WHP-9A2E93F6FF24', "Is the amount of alcohol WHP-9A2E93F6FF24 drinks written down anywhere in the chart?", r'(?i)\bdrinks?\b|drinking|beer|wine|liquor|binge|units? of alcohol|AUDIT', None),
]


def build_b2(b: Builder):
    ctx, a = b.ctx, b.add
    scope_cache = {}

    def scope(pid):
        if pid not in scope_cache:
            scope_cache[pid] = patient_texts(ctx, pid)
        return scope_cache[pid]

    items = []
    for (key, pid, kind, date, pat, q, akind, ans, groups, fu, standalone) in NOTE_ITEMS:
        hits = [(rid, re.search(pat, tx)) for rid, k, d, tx in scope(pid) if k == kind and d == date and re.search(pat, tx)]
        if len(hits) != 1:
            raise ValueError(f'{key}: pattern {pat!r} must match exactly one {kind} document dated {date} for {pid}, got {len(hits)}')
        rid, m = hits[0]
        items.append((key, pid, q, akind, ans, groups, fu, standalone, [{'record_id': rid, 'quote': m.group(0)}]))
    for (key, pid, q, forbidden, fu) in NOT_DOCUMENTED:
        bad = [(rid, d) for rid, k, d, tx in scope(pid) if re.search(forbidden, tx)]
        if bad:
            raise ValueError(f'{key}: not-documented check failed for {pid}: {bad[:3]}')
        items.append((key, pid, q, 'not_documented', 'not documented', [], fu, None, []))
    order = sorted(items, key=lambda it: int(it[0][1:]))
    for key, pid, q, akind, ans, groups, fu, standalone, ev in order:
        sc = [rid for rid, _, _, _ in scope(pid)]
        extra = dict(kind=akind, key_groups=groups, patient_id=pid, scope_records=sc)
        if standalone:
            extra['standalone'] = standalone
        notes = {'not_documented': 'correct answer is "not documented": every note, HIE and scanned document of the patient was checked and none addresses it',
                 'yes_no': 'answer yes / no; cite a supporting record id', 'value': 'numeric answer; cite the record id',
                 'short_text': 'every key group must be present in the answer; cite a supporting record id'}[akind]
        a(B2, f'note_{key}', q, 'note', ['previsit_mango', 'awv_checklist'] if key in ('u17', 'u18') else ['previsit_mango'], answer=ans, notes=notes,
          follow_up_of=f'note_{fu}' if fu else None, evidence=ev, **extra)

    b.absent(B2, 'absent_occupation', "What does WHP-9A2E93F6FF24 do for work, and who is the employer?", 'No note, HIE document or scanned document in the warehouse records occupation or employer.',
             ['awv_checklist'], notes='text-searched all notes, HIE and scanned documents for occupation / employer / job: no content')
    pid = ctx.pick("SELECT patient_id FROM measurement_latest WHERE concept = 'egfr' AND value_canonical < 20")
    b.absent(B2, 'absent_dialysis', f"How many dialysis sessions a week is {pid} on, according to the notes?", 'No dialysis content exists anywhere in the notes, HIE or scanned documents.',
             ['aafp_awv'], notes='text search for dialysis returns nothing across all document text; eGFR below 20 does not imply dialysis documentation')
    pid = ctx.pick("SELECT patient_id FROM condition WHERE icd10 LIKE 'I48%' AND patient_id LIKE 'SYN-%'")
    b.absent(B2, 'absent_holter', f"What did the Holter monitor show for {pid}?", 'No Holter / ambulatory rhythm monitor results exist in any note or document.',
             ['previsit_mango'], notes='text search for Holter returns nothing')


# ====================================================================================================================================
def build(ctx):
    b = Builder(ctx)
    for fn in (build_b1, build_b2, build_pcq, build_cps, build_ins, build_ins2):
        fn(b)
    return b


def rows_of(b: Builder) -> list[dict]:
    out = []
    for src, q in b.items:
        r = finish(q, b.ctx)
        r['source'] = list(src)
        out.append(r)
    return out


def assign_ids(rows: list[dict]) -> list[dict]:
    """ids x1-NN .. x5-NN per bucket in build order (buckets in the order of BUCKETS); split dev3; follow_up_of keys -> parent ids."""
    rows.sort(key=lambda r: BUCKETS.index(r['bucket']))   # stable: build order within a bucket
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
            assert by_id[parent]['bucket'] == r['bucket'] and parent < r['id'], r['key_name']
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
            o = {k: r[k] for k in ('id', 'bucket', 'split', 'question', 'follow_up_of', 'answer_type', 'key', 'evidence', 'notes')}
            o['key_name'] = r['key_name']
            o['source'] = r['source']
            fh.write(json.dumps(o, ensure_ascii=False) + '\n')


def read(path=OUT):
    return [json.loads(line) for line in open(path) if line.strip()]


def summary(rows):
    print(len(rows), 'questions; split', dict(Counter(r['split'] for r in rows)))
    print('by bucket:', {b: sum(1 for r in rows if r['bucket'] == b) for b in BUCKETS})
    print('by answer_type:', dict(sorted(Counter(r['answer_type'] for r in rows).items())))
    print('follow-ups:', sum(1 for r in rows if r['follow_up_of']), ' absent:', sum(1 for r in rows if r['answer_type'] == 'absent'))
    print('sources used:', dict(sorted(Counter(s for r in rows for s in r['source']).items(), key=lambda kv: -kv[1])))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', action='store_true', help='recompute every key and compare with dev3_questions.jsonl; write nothing')
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
