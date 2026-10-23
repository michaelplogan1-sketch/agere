"""Bucket 4: population claims, pharmacy and purchased-SDOH questions (PMPM, high-cost claimants, denials, PDC, generics, SVI ...).

Dollar totals count every member's claims (spend is spend, including patients who later died); patient sets and rates over
patients use living patients (definitions.LIVING).
"""
from __future__ import annotations

from tools.dev_set import definitions as D
from tools.dev_set.b1_single_structured import _pdc
from tools.dev_set.keys import Q

B = 'population_claims_pharmacy_sdoh'
LIV = D.LIVING_CTE
Y = D.CALENDAR_YEAR
SPEND29 = f"SELECT * FROM payer_claim WHERE counts_in_metrics = 1 AND {D.in_year('service_from')}"
DIA = D.condition_cte('diabetes')
CKD = D.condition_cte('ckd')


def _adherent_share(ctx, drug_class, start='2029-01-01', end='2029-12-31'):
    pats = [r[0] for r in ctx.rows("SELECT DISTINCT patient_id FROM fill WHERE drug_class = ? AND fill_date >= ? AND fill_date <= ? AND patient_id IN "
                                    "(SELECT patient_id FROM patient WHERE patient_id NOT IN (SELECT patient_id FROM death) AND data_segment <> 'duplicate_chart')",
                                    (drug_class, start, end))]
    num = den = 0
    for p in pats:
        pdc, n = _pdc(ctx, p, drug_class, start, end)
        if pdc is None:
            continue
        den += 1
        num += pdc >= D.PDC_THRESHOLD
    return num, den


def build(ctx):
    out = []
    add = out.append

    add(Q(B, 'spend_by_lob_2029', "What did we pay out in medical claims in 2029 by line of business?", 'table', tolerance={'rel': 0.001, 'abs': 1.0},
          sql=f"""SELECT line_of_business, ROUND(SUM(paid_cents) / 100.0, 2) AS paid_usd FROM ({SPEND29}) GROUP BY 1 ORDER BY 2 DESC""",
          notes='payer claims counted in metrics (current, not duplicate/voided), paid amount by service date in 2029; reversals and recoupments net out'))
    add(Q(B, 'pmpm_by_lob_2029', "and what's that per member per month?", 'table', follow_up_of='spend_by_lob_2029', tolerance={'rel': 0.005, 'abs': 0.5},
          standalone='What was the medical claims paid PMPM in 2029 by line of business?',
          sql=f"""WITH s AS (SELECT line_of_business, SUM(paid_cents) / 100.0 AS paid FROM ({SPEND29}) GROUP BY 1),
                       m AS (SELECT line_of_business, COUNT(*) AS member_months FROM member_month WHERE substr(month, 1, 4) = '{Y}' GROUP BY 1)
                  SELECT s.line_of_business, ROUND(s.paid / m.member_months, 2) AS pmpm_usd FROM s JOIN m USING (line_of_business) ORDER BY 2 DESC""",
          notes='PMPM = 2029 paid / member_month rows of 2029 in the same line of business'))
    add(Q(B, 'pmpm_overall', "What's our total medical PMPM for 2029?", 'value', unit='USD', tolerance={'rel': 0.005, 'abs': 0.5},
          sql=f"""SELECT (SELECT SUM(paid_cents) / 100.0 FROM ({SPEND29})) / (SELECT COUNT(*) FROM member_month WHERE substr(month, 1, 4) = '{Y}')""",
          notes='all lines of business; paid / member months'))
    add(Q(B, 'allowed_2029', "total allowed amount on medical claims in 2029", 'value', unit='USD', tolerance={'rel': 0.001, 'abs': 1.0},
          sql=f"SELECT SUM(allowed_cents) / 100.0 FROM ({SPEND29}) WHERE final_status = 'paid'", notes='allowed amount of paid counted claims'))

    add(Q(B, 'high_cost_claimants', "Who are the high-cost claimants for 2029, meaning $50,000 or more paid?", 'patient_set',
          sql=f"""WITH {LIV} SELECT patient_id FROM ({SPEND29}) WHERE patient_id IN (SELECT patient_id FROM living)
                  GROUP BY patient_id HAVING SUM(paid_cents) >= {D.HIGH_COST_CLAIMANT_USD * 100}""",
          notes='living patients whose 2029 paid (counted claims, by service date) is at least $50,000'))
    add(Q(B, 'high_cost_share', "what share of total 2029 spend do they account for?", 'share', follow_up_of='high_cost_claimants', tolerance={'abs': 0.005},
          standalone='What share of total 2029 medical paid spend do the high-cost claimants ($50,000 or more paid in 2029) account for?',
          sql=f"""WITH {LIV}, hc AS (SELECT patient_id FROM ({SPEND29}) WHERE patient_id IN (SELECT patient_id FROM living)
                                  GROUP BY patient_id HAVING SUM(paid_cents) >= {D.HIGH_COST_CLAIMANT_USD * 100})
                  SELECT (SELECT SUM(paid_cents) FROM ({SPEND29}) WHERE patient_id IN (SELECT patient_id FROM hc)),
                         (SELECT SUM(paid_cents) FROM ({SPEND29}))""",
          notes='numerator and denominator in cents; denominator = all counted 2029 paid'))
    add(Q(B, 'high_cost_ckd', "How many of the high-cost claimants have CKD?", 'count', follow_up_of='high_cost_share',
          standalone='How many of the 2029 high-cost claimants ($50,000 or more paid) have chronic kidney disease?',
          sql=f"""WITH {LIV}, {CKD}, hc AS (SELECT patient_id FROM ({SPEND29}) WHERE patient_id IN (SELECT patient_id FROM living)
                                  GROUP BY patient_id HAVING SUM(paid_cents) >= {D.HIGH_COST_CLAIMANT_USD * 100})
                  SELECT COUNT(*) FROM hc WHERE patient_id IN (SELECT patient_id FROM has_ckd)"""))
    add(Q(B, 'top10_spenders', "Top ten patients by total paid claims in 2029, with the dollar amounts.", 'table', order_matters=True, tolerance={'abs': 1.0},
          sql=f"""WITH {LIV} SELECT patient_id, ROUND(SUM(paid_cents) / 100.0, 2) AS paid_usd FROM ({SPEND29})
                  WHERE patient_id IN (SELECT patient_id FROM living) GROUP BY patient_id ORDER BY SUM(paid_cents) DESC, patient_id LIMIT 10"""))

    add(Q(B, 'oon_share', "What percentage of our 2029 medical spend went out of network?", 'share', tolerance={'abs': 0.002},
          sql=f"""SELECT SUM(CASE WHEN {D.OUT_OF_NETWORK} THEN paid_cents END), SUM(paid_cents) FROM ({SPEND29})""",
          notes='paid dollars on out-of-network claims over all counted paid dollars'))
    add(Q(B, 'oon_by_lob', "out-of-network spend in 2029, by line of business — dollars and share of that line's spend", 'table', tolerance={'rel': 0.001, 'abs': 0.005},
          sql=f"""SELECT line_of_business, ROUND(SUM(CASE WHEN {D.OUT_OF_NETWORK} THEN paid_cents END) / 100.0, 2) AS oon_paid_usd,
                         ROUND(1.0 * SUM(CASE WHEN {D.OUT_OF_NETWORK} THEN paid_cents END) / SUM(paid_cents), 4) AS oon_share
                  FROM ({SPEND29}) GROUP BY 1 ORDER BY oon_paid_usd DESC"""))
    add(Q(B, 'denial_rate', "What was our claim denial rate in 2029?", 'share', tolerance={'abs': 0.002},
          sql=f"""SELECT SUM(final_status IN ('denied', 'denied_upheld')), COUNT(*) FROM ({SPEND29})""",
          notes='counted claims by service year 2029; denied = final status denied or denied_upheld; reversed/recouped/paid count as not denied'))
    add(Q(B, 'denial_reasons', "What are the top five denial reason codes and how many claims each?", 'table', order_matters=True,
          sql=f"""SELECT carc AS reason_code, COUNT(*) AS claims FROM ({SPEND29}) WHERE final_status IN ('denied', 'denied_upheld')
                  GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5""", notes='2029 counted denied claims, by claim adjustment reason code (CARC)'))
    add(Q(B, 'denial_by_payer', "Which payer has the highest denial rate in 2029 (payers with at least 500 claims)?", 'table',
          tolerance={'abs': 0.002},
          sql=f"""SELECT payer_id, COUNT(*) AS claims, ROUND(1.0 * SUM(final_status IN ('denied', 'denied_upheld')) / COUNT(*), 4) AS denial_rate
                  FROM ({SPEND29}) GROUP BY 1 HAVING COUNT(*) >= 500 ORDER BY denial_rate DESC, payer_id LIMIT 1"""))
    add(Q(B, 'ed_claims_avg', "What's the average paid amount on an ED professional claim in 2029?", 'value', unit='USD', tolerance={'rel': 0.005, 'abs': 0.5},
          sql=f"""SELECT ROUND(AVG(paid_cents) / 100.0, 2) FROM ({SPEND29}) WHERE place_of_service = '23' AND final_status = 'paid' AND claim_type = 'professional'""",
          notes='place of service 23 (emergency room), paid professional claims'))
    add(Q(B, 'institutional_share', "How much of our 2029 paid spend is hospital (institutional) claims?", 'share', tolerance={'abs': 0.002},
          sql=f"SELECT SUM(CASE WHEN claim_type = 'institutional' THEN paid_cents END), SUM(paid_cents) FROM ({SPEND29})"))

    n, d = _adherent_share(ctx, 'statin')
    add(Q(B, 'statin_adherence', "What percent of our statin users are adherent (PDC of 80% or better) in 2029?", 'share', answer=(n / d),
          numerator=n, denominator=d, tolerance={'abs': 0.01},
          sql="computed in b4._adherent_share: patients with >= 2 statin fills in 2029; PDC per b1._pdc (carry-forward, first fill to 2029-12-31); adherent >= 0.8",
          notes='living patients; fills from the merged fill table'))
    n, d = _adherent_share(ctx, 'ace_inhibitor')
    add(Q(B, 'acei_adherence', "How adherent are patients on ACE inhibitors in 2029 — share with PDC at least 0.8?", 'share', answer=(n / d),
          numerator=n, denominator=d, tolerance={'abs': 0.01},
          sql="computed in b4._adherent_share for class ace_inhibitor (same rules as statin_adherence)"))
    add(Q(B, 'generic_rate', "What's our generic dispensing rate on pharmacy claims in 2029?", 'share', tolerance={'abs': 0.005},
          sql=f"""SELECT SUM(formulary_tier = 'generic'), COUNT(*) FROM ({D.PHARMACY_FILLS}) WHERE {D.in_year('fill_date')} AND formulary_tier IS NOT NULL""",
          notes='counted fills in 2029 whose formulary tier is known (brand / generic / specialty); fills with no tier are excluded from the denominator'))
    add(Q(B, 'pharmacy_spend', "Total plan-paid pharmacy spend for 2029?", 'value', unit='USD', tolerance={'rel': 0.001, 'abs': 1.0},
          sql=f"SELECT SUM(plan_paid_cents) / 100.0 FROM ({D.PHARMACY_FILLS}) WHERE {D.in_year('fill_date')}"))
    add(Q(B, 'pharmacy_reject_rate', "What share of pharmacy claims were rejected in 2029, and what's the top reject code?", 'table', tolerance={'abs': 0.002},
          sql=f"""SELECT ROUND(1.0 * SUM(claim_status = 'rejected') / COUNT(*), 4) AS reject_rate,
                         (SELECT reject_code FROM payer_pharmacy_claim WHERE claim_status = 'rejected' AND {D.in_year('fill_date')}
                          GROUP BY 1 ORDER BY COUNT(*) DESC, 1 LIMIT 1) AS top_reject_code
                  FROM payer_pharmacy_claim WHERE {D.in_year('fill_date')} AND claim_status IN ('paid', 'rejected')""",
          notes='claim attempts in 2029 that were paid or rejected (reversals and recoupments are separate events)'))
    add(Q(B, 'returned_to_stock', "How many prescriptions were returned to stock in 2029?", 'count',
          sql=f"SELECT COUNT(*) FROM returned_fill WHERE reason = 'return_to_stock' AND {D.in_year('event_date')}",
          notes='returned_fill rows with reason return_to_stock (not-collected and declined-collection rows are other reasons)'))

    add(Q(B, 'food_insecure_uncontrolled', "Which of our diabetics with an A1c over 9 also live in low-food-access areas according to the purchased SDOH data?",
          'patient_set',
          sql=f"""WITH {LIV}, {DIA}, {D.SDOH_LATEST_CTE}
                  SELECT d.patient_id FROM has_diabetes d JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c'
                  JOIN sdoh_latest s ON s.patient_id = d.patient_id
                  WHERE m.value_canonical > {D.A1C_POOR_CONTROL} AND s.food_access_low = 1""",
          notes='latest matched vendor delivery per patient (definitions.SDOH_LATEST_CTE); food_access_low = 1; latest A1c > 9'))
    add(Q(B, 'food_need_uncontrolled', "Among diabetics with A1c above 9, how many screened positive for food insecurity?", 'count',
          sql=f"""WITH {LIV}, {DIA}
                  SELECT COUNT(DISTINCT d.patient_id) FROM has_diabetes d JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c'
                  WHERE m.value_canonical > {D.A1C_POOR_CONTROL} AND d.patient_id IN (SELECT patient_id FROM social_need WHERE need = 'food' AND positive = 1)""",
          notes='screened (social_need, any date) positive for food, not the vendor flag'))
    add(Q(B, 'vendor_vs_screen_food', "How many patients does the SDOH vendor flag as low food access that also screened positive for food insecurity in clinic?",
          'count',
          sql=f"""WITH {LIV}, {D.SDOH_LATEST_CTE}
                  SELECT COUNT(*) FROM sdoh_latest s WHERE s.food_access_low = 1 AND s.patient_id IN (SELECT patient_id FROM living)
                    AND s.patient_id IN (SELECT patient_id FROM social_need WHERE need = 'food' AND positive = 1)"""))
    add(Q(B, 'transport_noshow', "Which patients screened positive for a transportation barrier and also no-showed two or more times in the last year?", 'patient_set',
          sql=f"""WITH {LIV} SELECT patient_id FROM appointment WHERE final_status = 'no_show' AND {D.in_last_year('scheduled_date')}
                  AND patient_id IN (SELECT patient_id FROM living)
                  AND patient_id IN (SELECT patient_id FROM social_need WHERE need = 'transport' AND positive = 1)
                  GROUP BY patient_id HAVING COUNT(*) >= 2"""))
    add(Q(B, 'high_svi_share', "What share of our patients live in high social vulnerability areas (SVI 75 or above)?", 'share', tolerance={'abs': 0.005},
          sql=f"""WITH {LIV} SELECT SUM(svi_overall >= {D.HIGH_SVI}), COUNT(*) FROM patient_svi WHERE svi_overall IS NOT NULL
                  AND patient_id IN (SELECT patient_id FROM living)""", notes='living patients with an SVI; svi_overall is a 0-100 percentile'))
    add(Q(B, 'ed_per_1000_svi', "Compare ED visits per 1,000 patients in the last year between high-SVI (75+) and low-SVI (under 25) patients.", 'table', tolerance={'rel': 0.01, 'abs': 0.5},
          sql=f"""WITH {LIV}, g AS (SELECT patient_id, CASE WHEN svi_overall >= {D.HIGH_SVI} THEN 'high_svi' WHEN svi_overall < 25 THEN 'low_svi' END AS grp
                                   FROM patient_svi WHERE patient_id IN (SELECT patient_id FROM living)),
                       e AS (SELECT patient_id, COUNT(*) AS n FROM ({D.ED_VISITS}) WHERE {D.in_last_year('event_date')} GROUP BY 1)
                  SELECT g.grp AS group_name, COUNT(*) AS patients, ROUND(1000.0 * COALESCE(SUM(e.n), 0) / COUNT(*), 1) AS ed_visits_per_1000
                  FROM g LEFT JOIN e USING (patient_id) WHERE g.grp IS NOT NULL GROUP BY 1 ORDER BY 1""",
          notes='ED visits = utilization_event ed_visit in the last 12 months; per 1,000 living patients in the SVI group'))
    add(Q(B, 'vendor_financial_stress', "How many patients have a high financial-stress score in the purchased SDOH data?", 'count',
          sql=f"""WITH {LIV}, {D.SDOH_LATEST_CTE} SELECT COUNT(*) FROM sdoh_latest WHERE financial_stress_score >= {D.VENDOR_HIGH_RISK}
                  AND patient_id IN (SELECT patient_id FROM living)""", notes='latest matched delivery; high = score of 7 or more on the 1-10 scale'))
    add(Q(B, 'absent_mlr', "What's our medical loss ratio for the Medicare Advantage book?", 'absent',
          reason='The warehouse has claims (paid / allowed) but no premium or capitation revenue, so a loss ratio cannot be computed.',
          notes='checked: no premium, revenue, capitation, risk-adjustment payment or rebate table or column'))
    add(Q(B, 'absent_rebates', "What is our net pharmacy cost after manufacturer rebates for GLP-1s?", 'absent',
          reason='Pharmacy data holds plan-paid and patient-paid amounts per claim only; manufacturer rebates and net drug cost are not in the warehouse.',
          notes='checked payer_pharmacy_claim columns: ingredient cost, dispensing fee, plan and patient pay -- no rebate field'))
    return out
