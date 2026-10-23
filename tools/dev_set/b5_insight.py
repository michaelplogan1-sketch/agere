"""Bucket 5: insight questions -- why / who / which / what is driving ... -- keyed by a rubric of REQUIRED facts.

Each required fact is computed by SQL here (never typed in), so the numbers re-derive when definitions change. run.py scores
an answer as the share of required facts present, and flags any forbidden claim (a contradiction). Fact kinds:

  number   value (+ tolerance {abs, rel}); optional `near` regexes the number must appear close to
  terms    patterns (regexes); mode any|all
  ranking  items that must all appear, in this order

A question whose premise is false (ED use did not rise in Q3 2029) has the true direction as a required fact.
"""
from __future__ import annotations

import re

from tools.dev_set import definitions as D
from tools.dev_set.keys import Q

B = 'insight'
LIV = D.LIVING_CTE
S29 = f"SELECT * FROM payer_claim WHERE counts_in_metrics = 1 AND {D.in_year('service_from')}"
QTRS = {'2029Q2': ('2029-04-01', '2029-06-30'), '2029Q3': ('2029-07-01', '2029-09-30'), '2029Q4': ('2029-10-01', '2029-12-31'),
        '2030Q1': ('2030-01-01', '2030-03-31')}


def n(fid, claim, value, *, abs_=None, rel=None, near=None, unit=None):
    tol = {}
    if abs_ is not None:
        tol['abs'] = abs_
    if rel is not None:
        tol['rel'] = rel
    f = {'id': fid, 'kind': 'number', 'claim': claim, 'value': round(float(value), 6), 'tolerance': tol or {'abs': 0.5}}
    if near:
        f['near'] = near
    if unit:
        f['unit'] = unit
    return f


def t(fid, claim, patterns, mode='any'):
    return {'id': fid, 'kind': 'terms', 'claim': claim, 'patterns': patterns, 'mode': mode}


def lit(x):
    """A literal id as a regex that does not match a longer id (WH-PRACTICE-2 vs WH-PRACTICE-20)."""
    return re.escape(x) + r'(?!\d)'


def rank(fid, claim, items):
    return {'id': fid, 'kind': 'ranking', 'claim': claim, 'items': items}   # items are regexes, in order


def rubric(facts, forbidden=(), variations=''):
    return {'required_facts': facts, 'forbidden': [{'id': f'x{i}', 'claim': c, 'pattern': p} for i, (c, p) in enumerate(forbidden, 1)],
            'acceptable_variations': variations, 'scoring': 'share of required facts present; any forbidden claim present is a contradiction'}


def _drug_pattern(name):
    """generic and bracketed brand name of a drug string, as one regex"""
    brand = re.findall(r'\[(.+?)\]', name)
    words = [w for w in re.findall(r'[A-Za-z]{5,}', re.sub(r'\[.+?\]', '', name)) if w.lower() not in ('tablet', 'injector', 'inhalation', 'actuat', 'dose', 'extended', 'release', 'metered')]
    return '|'.join(re.escape(x) for x in (brand + words[:1]))


def share_fact(fid, claim, num, den, near=None):
    return n(fid, claim, num / den, abs_=0.005, near=near, unit='proportion')


def ed_count(ctx, a, b):
    return ctx.one(f"SELECT COUNT(*) FROM ({D.ED_VISITS}) WHERE event_date >= ? AND event_date <= ?", (a, b))


def build(ctx):
    out = []
    add = out.append
    one = ctx.one

    # 1 -- false premise -------------------------------------------------------------------------------------------------
    q2, q3 = ed_count(ctx, *QTRS['2029Q2']), ed_count(ctx, *QTRS['2029Q3'])
    add(Q(B, 'ed_q3_2029', "Why did ED use rise in Q3 2029?", 'rubric', answer=rubric([
        n('q3', f'ED visits in Q3 2029 were {q3}', q3, abs_=0), n('q2', f'ED visits in Q2 2029 were {q2}', q2, abs_=0),
        t('direction', 'ED use fell (did not rise) from Q2 to Q3 2029: the premise is false',
          [r'\b(fell|fall|declin\w*|decreas\w*|dropp?\w*|lower)\b', r"(did not|didn't|didn’t|no) (actually )?(rise|increase)", r'premise']),
    ], variations='any wording that corrects the premise; drivers of the decline are optional'),
        sql=f"ED visits (utilization_event kind ed_visit) Q2 2029 {QTRS['2029Q2']} vs Q3 2029 {QTRS['2029Q3']}",
        notes='FALSE PREMISE: ED visits fell from Q2 to Q3 2029. The answer must say so; explaining a rise is wrong. Events count all patients.'))

    # 2 / 3 -- a real rise -----------------------------------------------------------------------------------------------
    a, b = QTRS['2029Q4'], QTRS['2030Q1']
    q4, q1 = ed_count(ctx, *a), ed_count(ctx, *b)

    def cohort_ed(cond, rng):
        return one(f"""WITH {LIV}, {D.condition_cte(cond)} SELECT COUNT(*) FROM utilization_event u
                       WHERE u.kind = 'ed_visit' AND u.patient_id IN (SELECT patient_id FROM has_{cond}) AND u.event_date >= ? AND u.event_date <= ?""", rng)

    hf4, hf1 = cohort_ed('heart_failure', a), cohort_ed('heart_failure', b)
    ad = lambda r: one(f"""SELECT COUNT(*) FROM ({D.ED_VISITS}) e JOIN patient p USING (patient_id) WHERE p.{D.AGE} BETWEEN 18 AND 64
                           AND e.event_date >= ? AND e.event_date <= ?""", r)
    ad4, ad1 = ad(a), ad(b)
    add(Q(B, 'ed_q1_2030', "Why did ED use go up in Q1 2030 compared with the quarter before?", 'rubric', answer=rubric([
        n('q4', f'ED visits in Q4 2029 were {q4}', q4, abs_=0), n('q1', f'ED visits in Q1 2030 were {q1}', q1, abs_=0),
        n('delta', f'ED visits rose by {q1 - q4} (+{(q1 - q4) / q4:.0%})', q1 - q4, abs_=0),
        n('hf', f'ED visits by heart failure patients rose from {hf4} to {hf1} (+{hf1 - hf4}), one of the largest condition-level drivers (diabetic patients rose by {cohort_ed("diabetes", b) - cohort_ed("diabetes", a)})', hf1 - hf4,
          abs_=0, near=[r'heart failure|\bHF\b|CHF']),
        n('adults', f'Adults 18-64 account for {ad1 - ad4} of the {q1 - q4} added visits', ad1 - ad4, abs_=0, near=[r'18|adult|working']),
    ], variations='drivers may be framed by condition (HF), age band (adult) or both; any one correct driver with its numbers earns that fact'),
        sql=f"ED visits per quarter ({a}, {b}); HF cohort = definitions.condition_cte('heart_failure'); age band by patient.age_latest",
        notes='ED events count all patients; cohort counts use living patients with the condition'))
    add(Q(B, 'ed_q1_2030_who', "which groups of patients drove most of that?", 'rubric', follow_up_of='ed_q1_2030', answer=rubric([
        n('hf', f'Heart failure patients: {hf1 - hf4} of the {q1 - q4} added ED visits', hf1 - hf4, abs_=0, near=[r'heart failure|\bHF\b|CHF']),
        n('dm', f'Diabetic patients: {cohort_ed("diabetes", b) - cohort_ed("diabetes", a)} added ED visits', cohort_ed('diabetes', b) - cohort_ed('diabetes', a), abs_=0, near=[r'diabet']),
        n('adults', f'Adults 18-64: {ad1 - ad4} added visits', ad1 - ad4, abs_=0, near=[r'18|adult|working']),
    ], variations='overlapping groups are fine (an HF patient can also be diabetic)'),
        sql='quarter-over-quarter ED visits by condition cohort and age band',
        standalone='Which groups of patients drove most of the rise in ED visits in Q1 2030 compared with Q4 2029?'))

    # 4 -- Medicare Advantage PMPM drivers -------------------------------------------------------------------------------
    ma = f"SELECT * FROM ({S29}) WHERE product = 'medicare_advantage'"
    ma_paid = one(f"SELECT SUM(paid_cents) / 100.0 FROM ({ma})")
    ma_mm = one(f"SELECT COUNT(*) FROM member_month WHERE substr(month, 1, 4) = '{D.CALENDAR_YEAR}' AND product = 'medicare_advantage'")
    ma_inst = one(f"SELECT SUM(paid_cents) FILTER (WHERE claim_type = 'institutional') * 1.0 / SUM(paid_cents) FROM ({ma})")
    ma_top = ctx.rows(f"SELECT COALESCE(place_of_service, 'hospital claims') pos, SUM(paid_cents) / 100.0 FROM ({ma}) GROUP BY 1 ORDER BY 2 DESC LIMIT 2")
    ma_hc = one(f"""SELECT SUM(p) * 1.0 / (SELECT SUM(paid_cents) FROM ({ma})) FROM (SELECT SUM(paid_cents) p FROM ({ma}) GROUP BY patient_id HAVING SUM(paid_cents) >= {D.HIGH_COST_CLAIMANT_USD * 100})""")
    ma_hc_n = one(f"""SELECT COUNT(*) FROM (SELECT 1 FROM ({ma}) GROUP BY patient_id HAVING SUM(paid_cents) >= {D.HIGH_COST_CLAIMANT_USD * 100})""")
    add(Q(B, 'ma_pmpm_drivers', "What is driving PMPM for Medicare Advantage?", 'rubric', answer=rubric([
        n('pmpm', f'MA medical paid PMPM in 2029 is ${ma_paid / ma_mm:,.0f}', ma_paid / ma_mm, rel=0.02, near=[r'PMPM|per member']),
        n('inst', f'Hospital (institutional) claims are {ma_inst:.0%} of MA paid spend', ma_inst, abs_=0.01, near=[r'hospital|institutional|facility|inpatient']),
        n('highcost', f'{ma_hc_n} MA members with $50,000+ paid account for {ma_hc:.0%} of MA spend', ma_hc, abs_=0.01, near=[r'high[- ]cost|claimant|\$50']),
    ], variations='percent shown as 0.xx or xx%; the hospital share may be called inpatient/facility spend'),
        sql=f"2029 counted payer claims where product = 'medicare_advantage'; PMPM = paid / MA member months; shares of paid",
        notes=f'MA = payer_claim.product medicare_advantage; MA member months 2029 = {ma_mm}; top paid categories: {ma_top}'))

    # 5 -- dual PMPM jump ------------------------------------------------------------------------------------------------
    def pmpm(lob, y):
        paid = one("SELECT SUM(paid_cents) / 100.0 FROM payer_claim WHERE counts_in_metrics = 1 AND line_of_business = ? AND substr(service_from, 1, 4) = ?", (lob, y))
        mm = one("SELECT COUNT(*) FROM member_month WHERE line_of_business = ? AND substr(month, 1, 4) = ?", (lob, y))
        return paid / mm

    def inst(lob, y):
        return one("SELECT SUM(paid_cents) / 100.0 FROM payer_claim WHERE counts_in_metrics = 1 AND line_of_business = ? AND substr(service_from, 1, 4) = ? AND claim_type = 'institutional'", (lob, y))

    d28, d29 = pmpm('dual', '2028'), pmpm('dual', '2029')
    i28, i29 = inst('dual', '2028'), inst('dual', '2029')
    add(Q(B, 'dual_pmpm_jump', "Why did PMPM for dual-eligible members jump in 2029?", 'rubric', answer=rubric([
        n('p28', f'Dual PMPM was ${d28:,.0f} in 2028', d28, rel=0.02), n('p29', f'Dual PMPM was ${d29:,.0f} in 2029 (+{d29 / d28 - 1:.0%})', d29, rel=0.02),
        n('inst', f'Institutional (hospital) paid rose from ${i28:,.0f} to ${i29:,.0f} (+${i29 - i28:,.0f}), most of the increase', i29 - i28, rel=0.03, near=[r'hospital|institutional|facility|inpatient']),
    ], variations='professional claims barely moved; a few high-cost members matter in a small population'),
        sql='counted payer claims, line_of_business dual, 2028 vs 2029, paid / dual member months; institutional paid delta'))

    # 6 -- diabetes control gap by practice ------------------------------------------------------------------------------
    prac = ctx.rows(f"""WITH {LIV}, {D.condition_cte('diabetes')}, {D.PRACTICE_CTE}
        SELECT pr.practice_id, COUNT(*) n, SUM(m.value_canonical < {D.A1C_GOAL}) ctrl, SUM(m.value_canonical > {D.A1C_POOR_CONTROL}) poor
        FROM has_diabetes d JOIN patient p USING (patient_id) JOIN practice_of pr USING (patient_id)
        JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' WHERE p.{D.AGE} >= 18 AND pr.practice_id IS NOT NULL
        GROUP BY 1 HAVING COUNT(*) >= 15 ORDER BY 1.0 * SUM(m.value_canonical < {D.A1C_GOAL}) / COUNT(*), 1""")
    overall_ctrl = sum(r[2] for r in prac) / sum(r[1] for r in prac)
    worst = prac[:3]
    add(Q(B, 'dm_gap_practices', "Which practices have the biggest diabetes control gap?", 'rubric', answer=rubric([
        rank('rank', 'Practices by share of diabetics under A1c 8, lowest first: ' + ', '.join(f'{r[0]} ({r[2] / r[1]:.0%})' for r in worst), [lit(r[0]) for r in worst]),
        n('w1', f'{worst[0][0]}: {worst[0][2] / worst[0][1]:.0%} of {worst[0][1]} diabetics are under 8', worst[0][2] / worst[0][1], abs_=0.01),
        n('overall', f'Across practices {overall_ctrl:.0%} of diabetics are under 8', overall_ctrl, abs_=0.01),
    ], variations='a gap may also be stated as the share above 8 (1 - control)'),
        sql='practice = pcp practice else attributing practice; living adult diabetics with a latest A1c; practices with >= 15 diabetics; controlled = latest A1c < 8',
        notes='the worst three practices and their control shares are computed'))
    pc = ctx.rows(f"""WITH {LIV}, {D.condition_cte('diabetes')}, {D.PRACTICE_CTE}
        SELECT pr.practice_id, COUNT(*) FROM has_diabetes d JOIN patient p USING (patient_id) JOIN practice_of pr USING (patient_id)
        JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' WHERE p.{D.AGE} >= 18 AND m.value_canonical > {D.A1C_POOR_CONTROL}
        GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3""")
    add(Q(B, 'dm_poor_practices', "and which practice has the most patients above 9?", 'rubric', follow_up_of='dm_gap_practices', answer=rubric([
        t('top', f'{pc[0][0]} has the most diabetics with latest A1c above 9', [lit(pc[0][0])]),
        n('count', f'{pc[0][0]} has {pc[0][1]} patients above 9', pc[0][1], abs_=0, near=[lit(pc[0][0])]),
    ], variations='ties are broken by practice id'),
        sql='living adult diabetics with latest A1c > 9 per practice',
        standalone='Which practice has the most diabetic patients with an A1c above 9?'))

    # 7 -- outreach: diabetes --------------------------------------------------------------------------------------------
    poor = one(f"""WITH {LIV}, {D.condition_cte('diabetes')} SELECT COUNT(*) FROM has_diabetes d JOIN patient p USING (patient_id)
                   JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' WHERE p.{D.AGE} >= 18 AND m.value_canonical > {D.A1C_POOR_CONTROL}""")
    poor_ed = one(f"""WITH {LIV}, {D.condition_cte('diabetes')} SELECT COUNT(*) FROM has_diabetes d JOIN patient p USING (patient_id)
                   JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' WHERE p.{D.AGE} >= 18 AND m.value_canonical > {D.A1C_POOR_CONTROL}
                   AND d.patient_id IN (SELECT patient_id FROM ({D.ED_VISITS}) WHERE {D.in_last_year('event_date')})""")
    nolab = one(f"""WITH {LIV}, {D.condition_cte('diabetes')} SELECT COUNT(*) FROM has_diabetes d JOIN patient p USING (patient_id) WHERE p.{D.AGE} >= 18
                    AND d.patient_id NOT IN (SELECT patient_id FROM ({D.any_lab('hba1c')}) WHERE {D.in_last_year('event_date')})""")
    add(Q(B, 'outreach_diabetes', "Who should we prioritize for diabetes outreach?", 'rubric', answer=rubric([
        n('poor', f'{poor} adult diabetics have a latest A1c above 9', poor, abs_=0, near=[r'above 9|over 9|>\s*9|uncontrolled|poor']),
        n('poor_ed', f'{poor_ed} of them also had an ED visit in the last year (highest priority)', poor_ed, abs_=0, near=[r'ED|emergency']),
        n('nolab', f'{nolab} adult diabetics have no A1c in the last 12 months', nolab, abs_=0, near=[r'no A1c|without|missing|not (been )?tested|no recent']),
    ], variations='any ordering; the answer may merge the first two as a tiered list'),
        sql='adult living diabetics: latest A1c > 9; the subset with an ED visit in the last 12 months; no A1c result in the last 12 months',
        notes='"prioritize" has no single right list: the rubric requires the three largest, most actionable groups with their counts'))

    # 8 -- outreach: HF --------------------------------------------------------------------------------------------------
    pill = {k: D.filled_class_last_year(v) for k, v in D.GDMT_PILLARS.items()}
    hf_none = one(f"""WITH {LIV}, {D.condition_cte('heart_failure')} SELECT COUNT(*) FROM has_heart_failure WHERE patient_id NOT IN ({pill['beta_blocker']})
                      AND patient_id NOT IN ({pill['raas']}) AND patient_id NOT IN ({pill['mra']}) AND patient_id NOT IN ({pill['sglt2']})""")
    hf_none_adm = one(f"""WITH {LIV}, {D.condition_cte('heart_failure')} SELECT COUNT(*) FROM has_heart_failure WHERE patient_id NOT IN ({pill['beta_blocker']})
                      AND patient_id NOT IN ({pill['raas']}) AND patient_id NOT IN ({pill['mra']}) AND patient_id NOT IN ({pill['sglt2']})
                      AND patient_id IN (SELECT patient_id FROM ({D.ADMISSIONS}) WHERE {D.in_last_year('admit_date')})""")
    hf_n = one(f"WITH {LIV}, {D.condition_cte('heart_failure')} SELECT COUNT(*) FROM has_heart_failure")
    add(Q(B, 'outreach_hf', "Which heart failure patients should the care team call first?", 'rubric', answer=rubric([
        n('none', f'{hf_none} of {hf_n} HF patients have no GDMT fill in the last year', hf_none, abs_=0, near=[r'no (GDMT|guideline)|none|not on any|no (heart failure )?(medication|therap)']),
        n('none_adm', f'{hf_none_adm} of those were also admitted in the last 12 months (call first)', hf_none_adm, abs_=0, near=[r'admit|hospital']),
    ], variations='counts may come with patient ids; tiering by recency of admission is fine'),
        sql='living HF patients with no fill in any GDMT pillar class in the last 12 months; the subset with an admission in the last 12 months'))

    # 9 -- preventive gaps -----------------------------------------------------------------------------------------------
    def rate_mammo():
        return ctx.rows(f"""WITH {LIV} SELECT SUM(p.patient_id IN (SELECT patient_id FROM ({D.MAMMOGRAM_EVIDENCE}) WHERE event_date > '2028-05-27' AND event_date <= '{D.AS_OF}')), COUNT(*)
                  FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND p.sex_reported = 'F' AND p.{D.AGE} BETWEEN 50 AND 74""")[0]

    def rate_flu():
        return ctx.rows(f"""WITH {LIV} SELECT SUM(p.patient_id IN (SELECT patient_id FROM immunization WHERE vaccine LIKE 'influenza%' AND status = 'completed'
                  AND event_date >= '2029-07-01' AND event_date <= '{D.AS_OF}')), COUNT(*) FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND p.{D.AGE} >= 65""")[0]

    def rate_crc():
        return ctx.rows(f"""WITH {LIV} SELECT SUM(p.patient_id IN (SELECT patient_id FROM cancer_screening WHERE test = 'FIT' AND {D.in_last_year('event_date')}
                      UNION SELECT patient_id FROM procedure_line WHERE cpt = '82274' AND counts_in_metrics = 1 AND {D.in_last_year('service_date')}
                      UNION SELECT patient_id FROM procedure_line WHERE cpt IN ('45378', '45380', '45385') AND counts_in_metrics = 1)), COUNT(*)
                  FROM patient p WHERE p.patient_id IN (SELECT patient_id FROM living) AND p.{D.AGE} BETWEEN 45 AND 75""")[0]
    (mn, md), (fn_, fd), (cn, cd) = rate_mammo(), rate_flu(), rate_crc()
    add(Q(B, 'preventive_gaps', "Where are our biggest preventive care gaps?", 'rubric', answer=rubric([
        share_fact('crc', f'Colorectal screening is the lowest at {cn / cd:.0%}', cn, cd, near=[r'colorectal|colon|FIT']),
        share_fact('flu', f'Flu vaccination among 65+ is {fn_ / fd:.0%}', fn_, fd, near=[r'flu|influenza']),
        share_fact('mammo', f'Mammography (women 50-74, 2 years) is {mn / md:.0%}', mn, md, near=[r'mammo|breast']),
        t('lowest', 'Colorectal cancer screening is the largest gap', [r'colorectal|colon']),
    ], variations='gap may be shown as the unscreened share (1 - rate)'),
        sql='the three screening rates defined for b3 (mammogram_rate, colorectal_rate, flu_65) compared',
        notes='cervical screening cannot be measured here (no data) and is not required'))

    # 10 -- denial driver ------------------------------------------------------------------------------------------------
    pid = one(f"""SELECT payer_id FROM ({S29}) GROUP BY 1 HAVING COUNT(*) >= 500 ORDER BY 1.0 * SUM(final_status IN ('denied', 'denied_upheld')) / COUNT(*) DESC, 1 LIMIT 1""")
    tot_rate = ctx.rows(f"SELECT SUM(final_status IN ('denied', 'denied_upheld')), COUNT(*) FROM ({S29})")[0]
    x_rate = ctx.rows(f"SELECT SUM(final_status IN ('denied', 'denied_upheld')), COUNT(*) FROM ({S29}) WHERE payer_id = ?", (pid,))[0]
    carc = ctx.rows(f"""SELECT carc, COUNT(*) FROM ({S29}) WHERE payer_id = ? AND final_status IN ('denied', 'denied_upheld') GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3""", (pid,))
    dn = x_rate[0]
    add(Q(B, 'denials_worst_payer', "Why is denial rate so high with our worst payer?", 'rubric', answer=rubric([
        t('payer', f'The worst payer is {pid}', [lit(pid)]),
        share_fact('rate', f'{pid} denies {x_rate[0] / x_rate[1]:.1%} of claims vs {tot_rate[0] / tot_rate[1]:.1%} overall', x_rate[0], x_rate[1], near=[lit(pid)]),
        n('carc1', f"Top denial reason at {pid} is {carc[0][0]} ({carc[0][1]} of {dn} denials, {carc[0][1] / dn:.0%})", carc[0][1] / dn, abs_=0.02),
        t('carc_name', f'Denial reason codes {carc[0][0]} / {carc[1][0]} lead', [carc[0][0]]),
    ], variations='reason codes may be written CO-16 or CO16'),
        sql=f'2029 counted claims by payer: denial rate; payer with highest rate (>= 500 claims) = {pid}; CARC mix of its denials'))

    # 11 -- OON concentration --------------------------------------------------------------------------------------------
    oon = ctx.rows(f"""SELECT line_of_business, 1.0 * SUM(CASE WHEN {D.OUT_OF_NETWORK} THEN paid_cents END) / SUM(paid_cents), SUM(CASE WHEN {D.OUT_OF_NETWORK} THEN paid_cents END) / 100.0
                       FROM ({S29}) GROUP BY 1 ORDER BY 3 DESC LIMIT 2""")
    oon_all = ctx.rows(f"SELECT SUM(CASE WHEN {D.OUT_OF_NETWORK} THEN paid_cents END), SUM(paid_cents) FROM ({S29})")[0]
    oon_ct = ctx.rows(f"""SELECT claim_type, 1.0 * SUM(CASE WHEN {D.OUT_OF_NETWORK} THEN paid_cents END) / SUM(paid_cents) FROM ({S29}) GROUP BY 1 ORDER BY 2 DESC""")
    add(Q(B, 'oon_concentration', "Where is our out-of-network spend concentrated?", 'rubric', answer=rubric([
        share_fact('all', f'{oon_all[0] / oon_all[1]:.1%} of 2029 paid spend is out of network', oon_all[0], oon_all[1], near=[r'out[- ]of[- ]network|OON']),
        n('top_lob_usd', f'{oon[0][0]} has the most OON dollars (${oon[0][2]:,.0f}, {oon[0][1]:.0%} of its spend)', oon[0][2], rel=0.01, near=[lit(oon[0][0])]),
        t('top_lob', f'{oon[0][0]} is the largest OON line of business', [lit(oon[0][0])]),
        t('ctype', f'OON share is higher on {oon_ct[0][0]} claims ({oon_ct[0][1]:.0%}) than {oon_ct[1][0]} ({oon_ct[1][1]:.0%})', [oon_ct[0][0]]),
    ], variations='dollar figures may be rounded to thousands'),
        sql='2029 counted claims: OON paid share overall, by line of business and by claim type'))

    # 12 -- readmissions -------------------------------------------------------------------------------------------------
    rr = ctx.rows(f"""WITH {LIV} SELECT SUM(readmitted_30d), COUNT(*) FROM stay WHERE type = 'inpatient' AND discharge_known = 1
                      AND substr(discharge_date, 1, 4) = '2029' AND patient_id IN (SELECT patient_id FROM living)""")[0]
    rr_hf = ctx.rows(f"""WITH {LIV}, {D.condition_cte('heart_failure')} SELECT SUM(readmitted_30d), COUNT(*) FROM stay WHERE type = 'inpatient' AND discharge_known = 1
                      AND substr(discharge_date, 1, 4) = '2029' AND patient_id IN (SELECT patient_id FROM has_heart_failure)""")[0]
    rr_non = ctx.rows(f"""WITH {LIV}, {D.condition_cte('heart_failure')} SELECT SUM(readmitted_30d), COUNT(*) FROM stay WHERE type = 'inpatient' AND discharge_known = 1
                      AND substr(discharge_date, 1, 4) = '2029' AND patient_id IN (SELECT patient_id FROM living) AND patient_id NOT IN (SELECT patient_id FROM has_heart_failure)""")[0]
    add(Q(B, 'readmit_drivers', "Why are our 30-day readmissions so high, and is heart failure part of it?", 'rubric', answer=rubric([
        share_fact('overall', f'30-day readmission rate for 2029 discharges is {rr[0] / rr[1]:.1%}', rr[0], rr[1], near=[r'readmi']),
        share_fact('hf', f'Heart failure patients readmit at {rr_hf[0] / rr_hf[1]:.1%} ({rr_hf[0]} of {rr_hf[1]})', rr_hf[0], rr_hf[1], near=[r'heart failure|\bHF\b|CHF']),
        share_fact('non', f'Patients without HF readmit at {rr_non[0] / rr_non[1]:.1%}', rr_non[0], rr_non[1], near=[r'without|non|other|rest']),
    ], variations='HF versus non-HF comparison may be given as a ratio; direction must be correct'),
        sql='inpatient stays with known discharge in 2029, living patients; readmitted_30d rate overall, HF cohort, non-HF'))

    # 13 -- adherence gaps -----------------------------------------------------------------------------------------------
    from tools.dev_set.b4_claims_pharmacy_sdoh import _adherent_share
    classes = ['statin', 'ace_inhibitor', 'arb', 'metformin', 'hf_evidence_based_beta_blocker', 'glp1_agonist']
    ALIAS = {'statin': r'statin', 'ace_inhibitor': r'ACE[- ]?inhibitor|ACEi?\b|ace_inhibitor', 'arb': r'\bARBs?\b|angiotensin receptor',
             'metformin': r'metformin', 'hf_evidence_based_beta_blocker': r'beta[- ]?blocker|hf_evidence_based_beta_blocker', 'glp1_agonist': r'GLP-?1|glp1_agonist'}
    adh = sorted(((c, *_adherent_share(ctx, c)) for c in classes), key=lambda r: r[1] / r[2])
    add(Q(B, 'adherence_by_class', "Which of our main drug classes have the worst adherence?", 'rubric', answer=rubric([
        rank('rank', 'Adherent share (PDC >= 0.8) lowest first: ' + ', '.join(f'{c} {a / b:.0%}' for c, a, b in adh), [ALIAS[c] for c, a, b in adh[:2]]),
        share_fact('worst', f'{adh[0][0]} is lowest at {adh[0][1] / adh[0][2]:.0%} ({adh[0][1]} of {adh[0][2]})', adh[0][1], adh[0][2], near=[ALIAS[adh[0][0]]]),
        share_fact('best', f'{adh[-1][0]} is highest at {adh[-1][1] / adh[-1][2]:.0%}', adh[-1][1], adh[-1][2], near=[ALIAS[adh[-1][0]]]),
    ], variations='class names may be written in words (ACE inhibitor, GLP-1); ranking tolerance: the two lowest must be named in order'),
        sql='b4._adherent_share per class: living patients with >= 2 fills in 2029, PDC >= 0.8; classes statin, ace_inhibitor, arb, metformin, hf_evidence_based_beta_blocker, glp1_agonist',
        notes='the ranking facts name the machine drug-class keys; run.py also accepts the human names via its class aliases'))

    # 14 -- social need and no-shows -------------------------------------------------------------------------------------
    ns = ctx.rows(f"""WITH {LIV}, a AS (SELECT patient_id, SUM(final_status = 'no_show') ns, COUNT(*) n FROM appointment
                       WHERE {D.in_last_year('scheduled_date')} AND final_status IN ('no_show', 'fulfilled', 'attended', 'arrived') GROUP BY 1)
                       SELECT need, 1.0 * SUM(CASE WHEN a.patient_id IN (SELECT patient_id FROM social_need s2 WHERE s2.need = n.need AND s2.positive = 1) THEN a.ns END) /
                              SUM(CASE WHEN a.patient_id IN (SELECT patient_id FROM social_need s2 WHERE s2.need = n.need AND s2.positive = 1) THEN a.n END) AS r_pos,
                              1.0 * SUM(CASE WHEN a.patient_id NOT IN (SELECT patient_id FROM social_need s2 WHERE s2.need = n.need AND s2.positive = 1) THEN a.ns END) /
                              SUM(CASE WHEN a.patient_id NOT IN (SELECT patient_id FROM social_need s2 WHERE s2.need = n.need AND s2.positive = 1) THEN a.n END) AS r_neg
                       FROM (SELECT DISTINCT need FROM social_need) n, a WHERE a.patient_id IN (SELECT patient_id FROM living) GROUP BY need ORDER BY r_pos - r_neg DESC""")
    add(Q(B, 'need_vs_noshow', "Which social need is most tied to missed appointments?", 'rubric', answer=rubric([
        t('top', f'{ns[0][0]} (screened positive) shows the biggest no-show gap: {ns[0][1]:.1%} vs {ns[0][2]:.1%}', [ns[0][0] if ns[0][0] != 'transport' else r'transport']),
        share_fact('pos', f'No-show rate among {ns[0][0]}-positive patients is {ns[0][1]:.1%}', ns[0][1], 1, near=[r'no[- ]show|missed']),
        share_fact('neg', f'No-show rate among the others is {ns[0][2]:.1%}', ns[0][2], 1, near=[r'no[- ]show|missed']),
    ], variations='need names: food, housing, transport(ation), utilities, safety, cost of care'),
        sql='per need: no-show share of scheduled appointments (no_show / (no_show+fulfilled+attended+arrived)) in the last year, need-positive vs not, living patients',
        notes=f'all needs ranked by gap: {[(r[0], round(r[1], 4), round(r[2], 4)) for r in ns]}'))

    # 15 -- spend concentration ------------------------------------------------------------------------------------------
    spend = [r[0] for r in ctx.rows(f"""WITH {LIV} SELECT SUM(paid_cents) p FROM ({S29}) WHERE patient_id IN (SELECT patient_id FROM living) GROUP BY patient_id ORDER BY p DESC""")]
    tot = sum(spend)
    top5 = sum(spend[:round(len(spend) * 0.05)])
    top1 = sum(spend[:round(len(spend) * 0.01)])
    add(Q(B, 'spend_concentration', "How concentrated is our medical spend — how much comes from the top few percent of patients?", 'rubric', answer=rubric([
        share_fact('top5', f'The top 5% of patients (with claims) account for {top5 / tot:.0%} of 2029 paid spend', top5, tot, near=[r'5\s?%|five percent|top 5']),
        share_fact('top1', f'The top 1% account for {top1 / tot:.0%}', top1, tot, near=[r'1\s?%|one percent|top 1']),
    ], variations='"patients" here are living patients with at least one counted 2029 claim'),
        sql='living patients with counted 2029 claims ranked by paid; top 5% and top 1% (rounded counts) share of total paid'))

    # 16 -- cost by condition --------------------------------------------------------------------------------------------
    conds = ['diabetes', 'heart_failure', 'ckd', 'copd', 'cad', 'depression']
    cc = []
    for c in conds:
        r = ctx.rows(f"""WITH {LIV}, {D.condition_cte(c)} SELECT COUNT(*), SUM(paid_cents) / 100.0 FROM ({S29}) WHERE patient_id IN (SELECT patient_id FROM has_{c})""")[0]
        pc_n = ctx.one(f"WITH {LIV}, {D.condition_cte(c)} SELECT COUNT(*) FROM has_{c}")
        cc.append((c, pc_n, r[1] or 0.0))
    per = sorted(((c, nn, p / nn) for c, nn, p in cc), key=lambda r: -r[2])
    names = {'diabetes': 'diabet', 'heart_failure': 'heart failure|HF|CHF', 'ckd': 'CKD|kidney', 'copd': 'COPD', 'cad': 'CAD|coronary', 'depression': 'depress'}
    add(Q(B, 'cost_by_condition', "Which of these conditions costs the most per patient: diabetes, heart failure, CKD, COPD, coronary disease, depression?", 'rubric', answer=rubric([
        t('top', f'{per[0][0]} is the most expensive per patient (${per[0][2]:,.0f} in 2029 paid)', [names[per[0][0]]]),
        n('top_usd', f'{per[0][0]}: ${per[0][2]:,.0f} per patient', per[0][2], rel=0.03, near=[names[per[0][0]]]),
        rank('rank', 'Per-patient cost order: ' + ', '.join(f'{c} ${v:,.0f}' for c, nn, v in per), [names[c].split('|')[0] for c, nn, v in per[:3]]),
    ], variations='total (not per-patient) spend is a different question; this one is per patient'),
        sql='2029 counted paid claims of living patients with each condition (condition_evidence, labs included) divided by the cohort size',
        notes='cohorts overlap; spend is all-cause for the cohort'))
    add(Q(B, 'cost_inpatient_share', "and how much of that for the top one is inpatient?", 'rubric', follow_up_of='cost_by_condition', answer=rubric([
        share_fact('inst', f'{per[0][0]} cohort: institutional (hospital) claims share of its 2029 paid',
                   ctx.one(f"""WITH {LIV}, {D.condition_cte(per[0][0])} SELECT SUM(CASE WHEN claim_type = 'institutional' THEN paid_cents END) FROM ({S29}) WHERE patient_id IN (SELECT patient_id FROM has_{per[0][0]})"""),
                   ctx.one(f"""WITH {LIV}, {D.condition_cte(per[0][0])} SELECT SUM(paid_cents) FROM ({S29}) WHERE patient_id IN (SELECT patient_id FROM has_{per[0][0]})"""),
                   near=[r'hospital|institutional|inpatient|facility']),
    ], variations='institutional claims stand in for inpatient/hospital spend'),
        sql='institutional paid / total paid for the cohort of the condition ranked first in cost_by_condition',
        standalone=f'For the condition with the highest per-patient cost ({per[0][0]}), what share of its 2029 paid claims is hospital (institutional)?'))

    # 17 -- pharmacy trend -----------------------------------------------------------------------------------------------
    ph = {y: ctx.one(f"SELECT SUM(plan_paid_cents) / 100.0 FROM ({D.PHARMACY_FILLS}) WHERE {D.in_year('fill_date', y)}") for y in ('2028', '2029')}
    drug = ctx.rows(f"""SELECT drug_name, SUM(CASE WHEN {D.in_year('fill_date')} THEN plan_paid_cents END) / 100.0 a, SUM(CASE WHEN {D.in_year('fill_date', '2028')} THEN plan_paid_cents END) / 100.0 b
                        FROM ({D.PHARMACY_FILLS}) GROUP BY 1 ORDER BY COALESCE(a, 0) - COALESCE(b, 0) DESC, 1 LIMIT 3""")
    add(Q(B, 'pharmacy_change', "Did pharmacy spend go up or down from 2028 to 2029, and which drugs moved it most?", 'rubric', answer=rubric([
        n('y28', f'2028 plan-paid pharmacy spend ${ph["2028"]:,.0f}', ph['2028'], rel=0.01), n('y29', f'2029 plan-paid pharmacy spend ${ph["2029"]:,.0f}', ph['2029'], rel=0.01),
        t('dir', 'Spend went down (about -3.5%)', [r'\b(down|fell|declin\w*|decreas\w*|lower|dropp?\w*)\b']),
        t('drugs', f'Largest increases: {drug[0][0]}, {drug[1][0]}', [_drug_pattern(d[0]) for d in drug[:2]]),
    ], variations='drug names may be brand or generic'),
        sql='payer_pharmacy_claim counted fills (counts_as_fill = 1) by fill year 2028 vs 2029; per-drug change',
        notes=f'top three increases by drug: {drug}'))

    # 18 -- ED use by practice -------------------------------------------------------------------------------------------
    edp = ctx.rows(f"""WITH {LIV}, {D.PRACTICE_CTE}, e AS (SELECT patient_id, COUNT(*) n FROM ({D.ED_VISITS}) WHERE {D.in_last_year('event_date')} GROUP BY 1)
                       SELECT pr.practice_id, COUNT(*) pts, ROUND(1000.0 * COALESCE(SUM(e.n), 0) / COUNT(*), 1) per1000
                       FROM living l JOIN practice_of pr USING (patient_id) LEFT JOIN e USING (patient_id) WHERE pr.practice_id IS NOT NULL
                       GROUP BY 1 HAVING COUNT(*) >= 50 ORDER BY per1000 DESC, 1 LIMIT 3""")
    add(Q(B, 'ed_by_practice', "Which practices send the most patients to the ED relative to size?", 'rubric', answer=rubric([
        rank('rank', 'Highest ED visits per 1,000 patients (last 12 months), practices with >= 50 patients: ' + ', '.join(f'{r[0]} ({r[2]})' for r in edp), [lit(r[0]) for r in edp]),
        n('top', f'{edp[0][0]}: {edp[0][2]} ED visits per 1,000 patients', edp[0][2], rel=0.03, near=[lit(edp[0][0])]),
    ], variations='rates per 100 patients are equivalent when converted'),
        sql='ED visits in the last 12 months per 1,000 living patients, by practice (pcp practice else attributing practice)'))

    # 19 -- BP control weakest ------------------------------------------------------------------------------------------
    bpw = ctx.rows(f"""WITH {LIV}, {D.condition_cte('hypertension')}, {D.PRACTICE_CTE}
        SELECT pr.practice_id, COUNT(*) n, 1.0 * SUM(s.value_canonical < {D.BP_CONTROL[0]} AND d.value_canonical < {D.BP_CONTROL[1]}) / COUNT(*) r
        FROM has_hypertension h JOIN patient p USING (patient_id) JOIN practice_of pr USING (patient_id)
        JOIN measurement_latest s ON s.patient_id = h.patient_id AND s.concept = 'systolic_bp'
        JOIN measurement_latest d ON d.patient_id = h.patient_id AND d.concept = 'diastolic_bp'
        WHERE p.{D.AGE} BETWEEN 18 AND 85 AND pr.practice_id IS NOT NULL GROUP BY 1 HAVING COUNT(*) >= 15 ORDER BY r, 1 LIMIT 3""")
    add(Q(B, 'bp_weakest', "Where is blood pressure control weakest across our practices?", 'rubric', answer=rubric([
        t('worst', f'{bpw[0][0]} has the lowest BP control ({bpw[0][2]:.0%} of {bpw[0][1]} hypertensives)', [lit(bpw[0][0])]),
        share_fact('rate', f'{bpw[0][0]} controlled share {bpw[0][2]:.0%}', bpw[0][2], 1, near=[lit(bpw[0][0])]),
        rank('rank', 'Lowest three: ' + ', '.join(r[0] for r in bpw), [lit(r[0]) for r in bpw]),
    ], variations='practices with fewer than 15 hypertensives with readings are excluded'),
        sql='living hypertensives 18-85 with latest BP, controlled <140/<90, by practice with >= 15 patients'))

    # 20 -- SVI and diabetes control ------------------------------------------------------------------------------------
    sv = ctx.rows(f"""WITH {LIV}, {D.condition_cte('diabetes')}
        SELECT CASE WHEN s.svi_overall >= {D.HIGH_SVI} THEN 'high' WHEN s.svi_overall < 25 THEN 'low' END g, COUNT(*), SUM(m.value_canonical > {D.A1C_POOR_CONTROL})
        FROM has_diabetes d JOIN patient_svi s USING (patient_id) JOIN patient p USING (patient_id)
        JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' WHERE p.{D.AGE} >= 18 GROUP BY 1""")
    sv = {r[0]: r for r in sv if r[0]}
    hi, lo = sv['high'], sv['low']
    add(Q(B, 'svi_diabetes', "Do patients in high-vulnerability areas have worse diabetes control?", 'rubric', answer=rubric([
        share_fact('high', f'High-SVI (75+) diabetics with A1c above 9: {hi[2] / hi[1]:.1%} ({hi[2]} of {hi[1]})', hi[2], hi[1], near=[r'high']),
        share_fact('low', f'Low-SVI (<25) diabetics with A1c above 9: {lo[2] / lo[1]:.1%} ({lo[2]} of {lo[1]})', lo[2], lo[1], near=[r'low']),
        t('dir', 'Direction: ' + ('high-SVI is worse' if hi[2] / hi[1] > lo[2] / lo[1] else 'high-SVI is NOT worse (no gap or reversed)'),
          [r'\b(higher|worse|more likely|greater)\b'] if hi[2] / hi[1] > lo[2] / lo[1] else [r'\b(no (clear |meaningful )?(difference|gap)|not (worse|higher)|similar|lower|better)\b']),
    ], variations='percentages with a different but defensible SVI cut-off are not accepted: use 75+ and under 25'),
        sql='living adult diabetics with latest A1c and an SVI: share with A1c > 9 in svi_overall >= 75 vs < 25'))

    # 21 -- frequent ED users --------------------------------------------------------------------------------------------
    fu = [r[0] for r in ctx.rows(f"""WITH {LIV} SELECT patient_id FROM ({D.ED_VISITS}) WHERE {D.in_last_year('event_date')} AND patient_id IN (SELECT patient_id FROM living)
                                    GROUP BY 1 HAVING COUNT(*) >= 3""")]
    marks = ','.join(f"'{p}'" for p in fu)
    fu_lob = ctx.rows(f"""WITH lob AS (SELECT patient_id, line_of_business FROM (SELECT patient_id, line_of_business, ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY coverage_start DESC) rn FROM enrollment_span) WHERE rn = 1)
                          SELECT COALESCE(line_of_business, 'unknown'), COUNT(*) FROM lob WHERE patient_id IN ({marks}) GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 2""")
    fu_visits = ctx.one(f"SELECT COUNT(*) FROM ({D.ED_VISITS}) WHERE {D.in_last_year('event_date')} AND patient_id IN ({marks})")
    tot_ed = ctx.one(f"SELECT COUNT(*) FROM ({D.ED_VISITS}) WHERE {D.in_last_year('event_date')}")
    fu_hf = ctx.one(f"WITH {LIV}, {D.condition_cte('heart_failure')} SELECT COUNT(*) FROM has_heart_failure WHERE patient_id IN ({marks})")
    add(Q(B, 'frequent_ed_profile', "Who are our frequent ED users and how much of ED volume do they drive?", 'rubric', answer=rubric([
        n('n', f'{len(fu)} patients had 3+ ED visits in the last 12 months', len(fu), abs_=0, near=[r'patients|users']),
        n('visits', f'They account for {fu_visits} of {tot_ed} ED visits ({fu_visits / tot_ed:.0%})', fu_visits / tot_ed, abs_=0.01, near=[r'visits|volume|ED']),
        n('hf', f'{fu_hf} of them have heart failure', fu_hf, abs_=0, near=[r'heart failure|\bHF\b|CHF']),
        t('lob', f'Largest line of business among them: {fu_lob[0][0]}', [fu_lob[0][0]]),
    ], variations='the visit share can be given as a fraction or a percent'),
        sql='living patients with >= 3 ED visits in the last 12 months: count, share of all ED visits, HF count, top line of business'))

    # 22 -- CKD gap ----------------------------------------------------------------------------------------------------
    ck = ctx.rows(f"""WITH {LIV}, {D.condition_cte('ckd')} SELECT COUNT(*),
        SUM(patient_id NOT IN (SELECT patient_id FROM ({D.any_lab('uacr')}) WHERE {D.in_last_year('event_date')})),
        SUM(patient_id NOT IN ({D.filled_class_last_year(['ace_inhibitor', 'arb', 'arni'])})),
        SUM(patient_id NOT IN ({D.filled_class_last_year(['sglt2_inhibitor'])})) FROM has_ckd""")[0]
    add(Q(B, 'ckd_biggest_gap', "What's the biggest gap in our CKD care?", 'rubric', answer=rubric([
        n('cohort', f'{ck[0]} CKD patients', ck[0], abs_=0, near=[r'CKD|kidney']),
        share_fact('sglt2', f'{ck[3]} of {ck[0]} ({ck[3] / ck[0]:.0%}) have no SGLT2 inhibitor fill in the last year', ck[3], ck[0], near=[r'SGLT2']),
        share_fact('uacr', f'{ck[1]} of {ck[0]} ({ck[1] / ck[0]:.0%}) have no UACR in the last year', ck[1], ck[0], near=[r'UACR|albumin']),
        share_fact('raas', f'{ck[2]} of {ck[0]} ({ck[2] / ck[0]:.0%}) have no ACEi/ARB/ARNI fill', ck[2], ck[0], near=[r'ACE|ARB|RAAS|RAS']),
        t('biggest', 'The biggest gap is ' + max([('SGLT2', ck[3]), ('UACR', ck[1]), ('ACEi/ARB', ck[2])], key=lambda x: x[1])[0],
          [{'SGLT2': r'SGLT2', 'UACR': r'UACR|albumin', 'ACEi/ARB': r'ACE|ARB'}[max([('SGLT2', ck[3]), ('UACR', ck[1]), ('ACEi/ARB', ck[2])], key=lambda x: x[1])[0]]]),
    ], variations='gaps as counts or shares'),
        sql='living CKD patients (EHR + problem list + counted claims + eGFR rule): no UACR in 12 months, no ACEi/ARB/ARNI fill, no SGLT2i fill'))

    # 23 -- admissions trend ---------------------------------------------------------------------------------------------
    adm = {y: ctx.one(f"SELECT COUNT(*) FROM ({D.ADMISSIONS}) WHERE {D.in_year('admit_date', y)}") for y in ('2028', '2029')}
    add(Q(B, 'admissions_trend', "Are hospital admissions trending up or down from 2028 to 2029?", 'rubric', answer=rubric([
        n('y28', f'{adm["2028"]} admissions in 2028', adm['2028'], abs_=0, near=[r'2028']), n('y29', f'{adm["2029"]} admissions in 2029', adm['2029'], abs_=0, near=[r'2029']),
        t('dir', 'Admissions went ' + ('down' if adm['2029'] < adm['2028'] else 'up'),
          [r'\b(down|fell|declin\w*|decreas\w*|lower|dropp?\w*|reduc\w*)\b'] if adm['2029'] < adm['2028'] else [r'\b(up|rose|increas\w*|higher|grew)\b']),
    ], variations='per-1,000 rates are fine if the counts are also given'),
        sql='utilization_event kind inpatient by admit year'))

    # 26 -- Medicaid vs commercial --------------------------------------------------------------------------------------
    LOB = ("lob AS (SELECT patient_id, line_of_business FROM (SELECT patient_id, line_of_business, "
           "ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY coverage_start DESC) rn FROM enrollment_span) WHERE rn = 1)")

    def lobq(lob):
        r = ctx.rows(f"""WITH {LIV}, {D.condition_cte('diabetes')}, {LOB}
                        SELECT SUM(m.value_canonical > {D.A1C_POOR_CONTROL}), COUNT(*) FROM has_diabetes d JOIN lob l USING (patient_id) JOIN patient p USING (patient_id)
                        JOIN measurement_latest m ON m.patient_id = d.patient_id AND m.concept = 'hba1c' WHERE p.{D.AGE} >= 18 AND l.line_of_business = ?""", (lob,))[0]
        ed = ctx.rows(f"""WITH {LIV}, {LOB}
                        SELECT (SELECT COUNT(*) FROM ({D.ED_VISITS}) e WHERE {D.in_last_year('e.event_date')} AND e.patient_id IN (SELECT patient_id FROM lob WHERE line_of_business = ?)),
                               (SELECT COUNT(*) FROM lob WHERE line_of_business = ? AND patient_id IN (SELECT patient_id FROM living))""", (lob, lob))[0]
        return r, ed
    (mc, mce), (cm, cme) = lobq('medicaid'), lobq('commercial')
    add(Q(B, 'medicaid_vs_commercial', "How does Medicaid compare with commercial on diabetes control and ED use?", 'rubric', answer=rubric([
        share_fact('md_a1c', f'Medicaid diabetics with A1c above 9: {mc[0] / mc[1]:.1%} ({mc[0]} of {mc[1]})', mc[0], mc[1], near=[r'medicaid']),
        share_fact('cm_a1c', f'Commercial diabetics with A1c above 9: {cm[0] / cm[1]:.1%} ({cm[0]} of {cm[1]})', cm[0], cm[1], near=[r'commercial']),
        n('md_ed', f'Medicaid ED visits per 1,000 patients: {1000 * mce[0] / mce[1]:.0f}', 1000 * mce[0] / mce[1], rel=0.03, near=[r'medicaid']),
        n('cm_ed', f'Commercial ED visits per 1,000 patients: {1000 * cme[0] / cme[1]:.0f}', 1000 * cme[0] / cme[1], rel=0.03, near=[r'commercial']),
    ], variations='ED rate per 100 patients accepted when converted; line of business = latest enrollment span'),
        sql='living adult diabetics with latest A1c > 9 by line of business (medicaid, commercial); ED visits last 12 months per 1,000 living patients'))

    # 27 -- discharged without follow-up (who) ---------------------------------------------------------------------------
    nf = ctx.one(f"""WITH {LIV} SELECT COUNT(DISTINCT s.patient_id) FROM stay s WHERE s.type = 'inpatient' AND s.discharge_known = 1 AND s.discharge_date >= '2030-02-27' AND s.discharge_date <= '{D.AS_OF}'
                     AND s.patient_id IN (SELECT patient_id FROM living)
                     AND NOT EXISTS (SELECT 1 FROM ({D.FOLLOW_UP_VISIT}) f WHERE f.patient_id = s.patient_id AND f.event_date > s.discharge_date AND f.event_date <= '{D.AS_OF}')""")
    nd = ctx.one(f"""WITH {LIV} SELECT COUNT(DISTINCT s.patient_id) FROM stay s WHERE s.type = 'inpatient' AND s.discharge_known = 1 AND s.discharge_date >= '2030-02-27' AND s.discharge_date <= '{D.AS_OF}'
                     AND s.patient_id IN (SELECT patient_id FROM living)""")
    add(Q(B, 'post_discharge_outreach', "Which recently discharged patients need a call right now?", 'rubric', answer=rubric([
        n('n', f'{nd} patients were discharged in the last 90 days', nd, abs_=0, near=[r'discharg']),
        n('no_fu', f'{nf} of them have had no outpatient follow-up visit since discharge', nf, abs_=0, near=[r'no (outpatient )?follow|without|not (been )?seen|no visit']),
    ], variations='last 90 days = 2030-02-27 through the as-of date; the list itself is optional'),
        sql='living patients with an inpatient discharge in the 90 days ending as-of; those with no completed outpatient visit after the discharge'))

    # 28 -- no show lead time --------------------------------------------------------------------------------------------
    ls = ctx.rows(f"""SELECT CASE WHEN lead_time_days <= 7 THEN '<=7' WHEN lead_time_days <= 30 THEN '8-30' ELSE '31+' END b, COUNT(*), 1.0 * SUM(final_status = 'no_show') / COUNT(*)
                      FROM appointment WHERE {D.in_year('scheduled_date')} AND final_status IN ('no_show', 'fulfilled', 'attended', 'arrived') AND lead_time_days IS NOT NULL GROUP BY 1""")
    ls = {r[0]: r for r in ls}
    add(Q(B, 'noshow_leadtime', "Does booking further out make people more likely to no-show?", 'rubric', answer=rubric([
        share_fact('long', f'Booked 31+ days ahead: {ls["31+"][2]:.1%} no-show ({ls["31+"][1]} appointments)', ls['31+'][2], 1, near=[r'31|30|month|far']),
        share_fact('short', f'Booked within a week: {ls["<=7"][2]:.1%} no-show', ls['<=7'][2], 1, near=[r'7|week|short|near']),
        t('yes', 'Yes, long lead times have the highest no-show rate (nearly double the 8-30 day bucket)', [r'\b(yes|higher|more likely|longer)\b']),
    ], variations='buckets: up to 7 days, 8-30 days, over 30 days; 2029 appointments that were kept or no-showed'),
        sql='appointments scheduled in 2029 with final status no_show / fulfilled / attended / arrived: no-show share by lead_time_days bucket'))

    # absent insights ----------------------------------------------------------------------------------------------------
    add(Q(B, 'absent_stars', "What's driving the decline in our Medicare Advantage star ratings?", 'absent',
          reason='The warehouse has no CMS star-rating, HEDIS-submission or CAHPS/HOS survey data, so a star-rating trend cannot be shown or explained.',
          notes='checked: no star, rating, survey or HEDIS-result table or column'))
    add(Q(B, 'absent_raf', "How much risk-adjusted revenue are we leaving on the table from uncaptured HCC diagnoses?", 'absent',
          reason='There are no HCC/RAF scores, risk-adjustment payments or premium revenue in the warehouse; diagnoses exist but the revenue side does not.',
          notes='checked: no hcc, raf, risk score, capitation or premium fields'))
    return out
