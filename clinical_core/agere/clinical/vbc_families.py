"""Deterministic value-based-care metric families: the core claims-finance, membership, attribution, utilization and
access questions answered by code from the semantic layer (the cohort_families.py pattern: question + definition ->
typed family and parameters -> code-rendered SQL with a '-- DEFINITION:' first line -> validate).

A family claims a question only when its stated definition, after the delivery's standard boilerplate sentences are set
aside (scope, enrollment-span rules, appointment-version rules, native amount notes, the inpatient-stay rule), matches
one of the definition shapes below in full; any other clause declines (the question goes on to the next rung).

Families and conventions (each renders one read-only SELECT):
  claims_finance      paid: sum of signed paid amounts over every payer medical claim adjudication with a paid date in
                      the year (reversals / recoupments negative, voids 0); allowed: allowed amount on final-adjudication
                      paid claims (payer_claim final_status paid, counts_in_metrics = 1) with service_from in the year;
                      member_liability: patient_responsibility on claims whose latest adjudication is paid, service_from
                      in the year; pharmacy_paid: plan_paid on payer pharmacy claims with fill date in the year, paid fills
                      not later reversed plus (negative) recoupments, reversed and rejected 0. Optional product /
                      line-of-business filter or grouping. Claim allowed amount: the claim-level allowed amount, except
                      inpatient DRG claims whose delivered allowed is 0: 835 paid amount + patient responsibility.
  pmpm                allowed (as above, optional product filter) / member-months of the same product (or all primary
                      member-months), dollars per member-month
  member_months       distinct (person, month[, product]) covered on the 1st by a delivered enrollment span (current
                      versions; member_id_reissued continuations kept), through death (1st of month <= death date),
                      months up to as_of; scope all spans or primary (Medigap and COB secondary excluded)
  attribution_counts  persons alive (no death on or before the date) with an active primary span on the date that names
                      an attributed practice, per practice
  top_n_cost_members  the N persons with the highest allowed amount (final paid, service in the year); ties by patient_id
  readmissions        inpatient stays (distinct person + admission date on non-voided institutional 011x payer claims,
                      final adjudication; discharge = discharge date else service-to) admitted in the year with UB-04
                      admission type 1/2 within 0..N days after a prior stay's discharge; count, or rate = those /
                      stays discharged in the year, percent
  admissions          inpatient stays admitted in the year with the stated UB-04 admission types (3 = elective), or
                      distinct persons with >= N such stays (unit persons; any admission type when none is stated)
  appointments        appointments (one per appointment_id, latest version: semantic appointment) with a slot in the year,
                      any or a stated final status, or per slot month
  appointment_repeat  persons with >= N appointments whose final status is the stated one, through as_of
  paid_line_visits    distinct person-dates with a final paid payer claim line carrying the stated CPT/HCPCS codes,
                      service in the year
  adjudication_count  payer adjudications (or distinct payer claims) with the stated statuses [and CARC], dated in the
                      year or through as_of
  noshow_by_source    scheduling source systems with >= M fulfilled-or-no-show appointments (arrived / attended count as
                      fulfilled) with slots in the year window: top N by no-show rate (no_show / (fulfilled + no_show)),
                      percent; ties by key. The scheduling source system is the practice whose scheduling record it is
                      (appointment organization_id; the warehouse's appointment source_system is that practice, while a
                      native EHR instance such as the deep-chart panel schedules for several practices), else the
                      records' source_system
  pharmacy_tier_share paid payer pharmacy claims (claim_status paid) with fill date in the year whose formulary_tier is
                      generic, percent of those paid claims. Where the workspace carries no formulary_tier at all (native
                      NCPDP / flat pharmacy / CCLF7 do not send it) the tier is read from the RxNorm product name: a
                      generic product (SCD / GPCK) carries no [Brand] suffix (on feeds that deliver the tier, generic =
                      no bracketed brand on every row)
  hcc_persons         persons with >= 1 CMS-HCC V28 payment category from the diagnoses of final paid payer medical claims
                      with service in the year; the V28 crosswalk, age / sex edits and the heart-failure patch come from
                      risk_adjustment.py (ages as of Feb 1 of the next year, as the engine scores them); the code list is
                      the workspace's own paid-claim codes for the year that map, so the planner needs the workspace path
  deaths_by_product   persons with a delivered death record on or before the date, by the product (or line of business)
                      of the primary span (Medigap / COB secondary excluded) active the day before death, 'uninsured' when
                      none (the latest-starting span when several)
  pcp_affiliations_ended  release-wide provider-practice affiliations of PCPs (roster pcp_flag Y) on the monthly ACO provider
                      roster that ended on or before the date (or within the year): ended = the roster's term date, or,
                      for an affiliation dropped from the roster without one (a departure or a move), the first month after
                      the last roster month that lists it (only months up to the latest roster month on or before as_of)

AGERE_VBC_FAMILIES=off disables the rung. Synthetic data only; no model calls.
"""
from __future__ import annotations

import copy, os, re

from . import family_common as fc
from .family_common import q

VERSION = 'agere-vbc-families/1'
ROUTE = 'vbc_family'
FAMILIES = ('claims_finance', 'pmpm', 'member_months', 'attribution_counts', 'top_n_cost_members', 'readmissions',
            'admissions', 'appointments', 'appointment_repeat', 'paid_line_visits', 'adjudication_count',
            'noshow_by_source', 'pharmacy_tier_share', 'hcc_persons', 'deaths_by_product', 'pcp_affiliations_ended')
PRODUCTS = ('commercial', 'marketplace', 'medicaid', 'medicare_ffs', 'medicare_advantage', 'dual_ffs', 'dual_ma', 'medigap', 'cob_secondary')
LOBS = ('commercial', 'medicare', 'medicaid', 'dual', 'medigap', 'cob_secondary')
SECONDARY = ('medigap', 'cob_secondary')
MEASURES = ('paid', 'allowed', 'member_liability', 'pharmacy_paid')
ADJ_STATUSES = ('paid', 'denied', 'denied_upheld', 'voided', 'reversed', 'recouped')
APPT_STATUSES = ('fulfilled', 'no_show', 'booked', 'cancelled', 'arrived', 'rescheduled')
FULFILLED = ('fulfilled', 'arrived', 'attended')  # the delivery's rule: attended and arrived count as fulfilled
TIERS = ('generic',)  # brand / specialty cannot be read from a product name (specialty products are branded too)


def enabled():
    return os.environ.get('AGERE_VBC_FAMILIES', 'on').strip().lower() != 'off'


# ============================================================================================ SQL rendering
ALLOWED = ("CASE WHEN c.claim_type = 'institutional' AND c.drg IS NOT NULL AND coalesce(c.allowed_cents, 0) = 0 "
           "THEN coalesce(c.paid_cents, 0) + coalesce(c.patient_responsibility_cents, 0) ELSE coalesce(c.allowed_cents, 0) END")
FINAL_PAID = "c.final_status = 'paid' AND c.counts_in_metrics = 1"


def _year(col, y):
    return f"{col} BETWEEN {q(f'{y}-01-01')} AND {q(f'{y}-12-31')}"


def _filter(alias, p):
    out = []
    if p.get('products'): out.append(f"{alias}.product IN ({', '.join(q(x) for x in p['products'])})")
    if p.get('lobs'): out.append(f"{alias}.line_of_business IN ({', '.join(q(x) for x in p['lobs'])})")
    return out


def _money(expr):
    return f"round(coalesce(sum({expr}), 0) / 100.0, 2)"


def render_claims_finance(p, as_of):
    m, y, g = p['measure'], p['year'], p.get('group_by')
    if m == 'pharmacy_paid':
        expr = ("CASE WHEN x.claim_status = 'paid' AND x.reversed_by_record_id IS NULL THEN x.plan_paid_cents "
                "WHEN x.claim_status = 'recouped' THEN x.plan_paid_cents ELSE 0 END")
        return f"SELECT {_money(expr)} AS plan_paid FROM payer_pharmacy_claim x WHERE {_year('x.fill_date', y)}"
    if m == 'paid':
        frm = "payer_adjudication a JOIN payer_claim c ON c.payer_claim_id = a.payer_claim_id"
        where = [_year('a.paid_date', y)] + _filter('c', p)
        expr = "CASE WHEN a.claim_status = 'voided' THEN 0 ELSE a.paid_cents END"
    elif m == 'allowed':
        frm, where, expr = "payer_claim c", [FINAL_PAID, _year('c.service_from', y)] + _filter('c', p), ALLOWED
    else:  # member_liability
        frm, where, expr = ("payer_claim c", [FINAL_PAID, _year('c.service_from', y)] + _filter('c', p),
                            "coalesce(c.patient_responsibility_cents, 0)")
    if g:
        col = 'c.product' if g == 'product' else 'c.line_of_business'
        return (f"SELECT {col} AS key, {_money(expr)} AS value FROM {frm} WHERE {' AND '.join(where)} "
                f"GROUP BY {col} ORDER BY {col}")
    return f"SELECT {_money(expr)} AS {m} FROM {frm} WHERE {' AND '.join(where)}"


def _months_cte(y, as_of):
    return (f"mo(m) AS (SELECT {q(f'{y}-01-01')} UNION ALL SELECT date(m, '+1 month') FROM mo "
            f"WHERE m < {q(f'{y}-12-01')} AND date(m, '+1 month') <= {q(as_of)})")


def _member_months_sql(p, as_of, key=None):
    """SELECT DISTINCT patient_id, month[, key] for the span filter in p (scope primary | all, products / lobs)."""
    where = ["e.coverage_start <= mo.m", "(e.coverage_end IS NULL OR e.coverage_end >= mo.m)",
             "(d.death_date IS NULL OR d.death_date >= mo.m)"]
    if p.get('mm_scope', 'primary') == 'primary': where.append(f"e.product NOT IN ({', '.join(q(x) for x in SECONDARY)})")
    where += _filter('e', {'products': p.get('mm_products'), 'lobs': p.get('mm_lobs')})
    k = f", e.{key} AS key" if key else ''
    return (f"SELECT DISTINCT e.patient_id, mo.m{k} FROM enrollment_span e JOIN mo ON 1 "
            f"LEFT JOIN death d ON d.patient_id = e.patient_id WHERE {' AND '.join(where)}")


def render_pmpm(p, as_of):
    y = p['year']
    num = f"SELECT coalesce(sum({ALLOWED}), 0) FROM payer_claim c WHERE {' AND '.join([FINAL_PAID, _year('c.service_from', y)] + _filter('c', p))}"
    return (f"WITH RECURSIVE {_months_cte(y, as_of)}, mm AS ({_member_months_sql(p, as_of)}) "
            f"SELECT round(({num}) / 100.0 / nullif((SELECT count(*) FROM mm), 0), 2) AS pmpm")


def render_member_months(p, as_of):
    g = p.get('group_by')
    key = {'product': 'product', 'line_of_business': 'line_of_business'}.get(g)
    body = f"WITH RECURSIVE {_months_cte(p['year'], as_of)}, mm AS ({_member_months_sql(p, as_of, key)}) "
    if key: return body + "SELECT key, count(*) AS value FROM mm GROUP BY key ORDER BY key"
    return body + "SELECT count(*) AS member_months FROM mm"


def render_attribution_counts(p, as_of):
    a = as_of if p['date'] == 'as_of' else p['date']
    return (f"SELECT e.attribution_practice_id AS key, count(DISTINCT e.patient_id) AS value FROM enrollment_span e "
            f"LEFT JOIN death d ON d.patient_id = e.patient_id WHERE e.coverage_start <= {q(a)} "
            f"AND (e.coverage_end IS NULL OR e.coverage_end >= {q(a)}) AND e.product NOT IN ({', '.join(q(x) for x in SECONDARY)}) "
            f"AND e.attribution_practice_id IS NOT NULL AND e.attribution_practice_id <> '' "
            f"AND (d.death_date IS NULL OR d.death_date > {q(a)}) GROUP BY e.attribution_practice_id ORDER BY value DESC, key")


def render_top_n_cost_members(p, as_of):
    return (f"SELECT patient_id FROM (SELECT c.patient_id, sum({ALLOWED}) AS allowed FROM payer_claim c "
            f"WHERE {FINAL_PAID} AND {_year('c.service_from', p['year'])} GROUP BY c.patient_id "
            f"ORDER BY allowed DESC, c.patient_id LIMIT {int(p['n'])})")


_STAYS = ("s0 AS (SELECT c.patient_id, coalesce(c.admission_date, c.service_from) AS admit, "
          "coalesce(c.discharge_date, c.service_to) AS disch, CAST(json_extract(r.payload, '$.admission_type') AS INTEGER) AS atype "
          "FROM payer_claim c LEFT JOIN record r ON r.record_id = c.record_id WHERE c.claim_type = 'institutional' "
          "AND c.type_of_bill LIKE '011%' AND c.counts_in_metrics = 1 AND c.final_status <> 'voided'), "
          "st AS (SELECT patient_id, admit, max(disch) AS disch FROM s0 GROUP BY patient_id, admit), "
          "sty AS (SELECT DISTINCT patient_id, admit, atype FROM s0)")


def _types(types):
    return ', '.join(str(int(t)) for t in types)


def render_readmissions(p, as_of):
    y, n = p['year'], int(p['days'])
    idx = (f"SELECT DISTINCT s.patient_id, s.admit FROM st s JOIN sty t ON t.patient_id = s.patient_id AND t.admit = s.admit "
           f"AND t.atype IN ({_types(p['admission_types'])}) WHERE {_year('s.admit', y)} AND EXISTS (SELECT 1 FROM st pr "
           f"WHERE pr.patient_id = s.patient_id AND pr.admit < s.admit AND julianday(s.admit) - julianday(pr.disch) BETWEEN 0 AND {n})")
    if p['measure'] == 'rate':
        return (f"WITH {_STAYS} SELECT round(100.0 * (SELECT count(*) FROM ({idx})) / "
                f"nullif((SELECT count(*) FROM st WHERE {_year('disch', y)}), 0), 2) AS readmission_rate")
    return f"WITH {_STAYS} SELECT count(*) AS readmissions FROM ({idx})"


def render_admissions(p, as_of):
    typed = (f"JOIN sty t ON t.patient_id = s.patient_id AND t.admit = s.admit AND t.atype IN ({_types(p['admission_types'])}) "
             if p.get('admission_types') else '')
    if p.get('unit') == 'persons':
        return (f"WITH {_STAYS} SELECT count(*) AS persons FROM (SELECT s.patient_id FROM st s {typed}"
                f"WHERE {_year('s.admit', p['year'])} GROUP BY s.patient_id HAVING count(DISTINCT s.admit) >= {int(p.get('min_stays', 1))})")
    return (f"WITH {_STAYS} SELECT count(*) AS admissions FROM (SELECT DISTINCT s.patient_id, s.admit FROM st s {typed}"
            f"WHERE {_year('s.admit', p['year'])})")


def render_appointments(p, as_of):
    where = [_year('scheduled_date', p['year'])]
    if p.get('statuses'): where.append(f"final_status IN ({', '.join(q(s) for s in p['statuses'])})")
    if p.get('by') == 'month':
        return (f"SELECT substr(scheduled_date, 1, 7) AS key, count(*) AS value FROM appointment WHERE {' AND '.join(where)} "
                f"GROUP BY key ORDER BY key")
    return f"SELECT count(*) AS appointments FROM appointment WHERE {' AND '.join(where)}"


def render_appointment_repeat(p, as_of):
    return (f"SELECT patient_id FROM appointment WHERE final_status IN ({', '.join(q(s) for s in p['statuses'])}) "
            f"AND scheduled_date <= {q(as_of)} AND patient_id IS NOT NULL GROUP BY patient_id HAVING count(*) >= {int(p['min_count'])}")


def render_paid_line_visits(p, as_of):
    return (f"SELECT count(*) AS visits FROM (SELECT DISTINCT l.patient_id, coalesce(l.service_date, c.service_from) AS d "
            f"FROM payer_claim_line l JOIN payer_claim c ON c.payer_claim_id = l.payer_claim_id WHERE {FINAL_PAID} "
            f"AND l.counts_in_metrics = 1 AND {fc.codes_clause('coalesce(l.cpt, l.hcpcs)', p['codes'])}) WHERE {_year('d', p['year'])}")


def render_adjudication_count(p, as_of):
    where = [f"claim_status IN ({', '.join(q(s) for s in p['statuses'])})"]
    if p.get('carc'): where.append(f"carc = {q(p['carc'])}")
    where.append(_year('adjudication_date', p['year']) if p.get('year') else f"adjudication_date <= {q(as_of)}")
    what = 'count(DISTINCT payer_claim_id) AS claims' if p['unit'] == 'claims' else 'count(*) AS adjudications'
    return f"SELECT {what} FROM payer_adjudication WHERE {' AND '.join(where)}"


def render_noshow_by_source(p, as_of):
    fulfilled = ', '.join(q(x) for x in FULFILLED)
    sys_of = ("coalesce(nullif(a.organization_id, ''), (SELECT json_extract(r.payload, '$.source_system') FROM record r "
              "WHERE r.stream = 'appointments' AND json_extract(r.payload, '$.appointment_id') = a.appointment_id LIMIT 1))")
    return (f"WITH a AS (SELECT {sys_of} AS sys, CASE WHEN a.final_status IN ({fulfilled}) THEN 0 "
            f"WHEN a.final_status = 'no_show' THEN 1 END AS ns FROM appointment a "
            f"WHERE a.scheduled_date BETWEEN {q(str(p['year_from']) + '-01-01')} AND {q(str(p['year_to']) + '-12-31')}) "
            "SELECT sys AS key, round(100.0 * sum(ns) / count(*), 2) AS value FROM a WHERE ns IS NOT NULL AND sys IS NOT NULL "
            f"GROUP BY sys HAVING count(*) >= {int(p['min_appointments'])} ORDER BY value DESC, key LIMIT {int(p['n'])}")


def render_pharmacy_tier_share(p, as_of):
    tier = ("CASE WHEN EXISTS (SELECT 1 FROM payer_pharmacy_claim WHERE formulary_tier IS NOT NULL AND formulary_tier <> '') "
            f"THEN coalesce(x.formulary_tier, '') = {q(p['tier'])} ELSE coalesce(x.drug_name, '[') NOT LIKE '%[%' END")
    return (f"SELECT round(100.0 * sum({tier}) / nullif(count(*), 0), 2) AS pct_{p['tier']} FROM payer_pharmacy_claim x "
            f"WHERE x.claim_status = 'paid' AND {_year('x.fill_date', p['year'])}")


def _age_sql(ref):
    """Age in whole years on ref from patient.birth_date (else age_reported), never negative."""
    return (f"max(0, CASE WHEN p.birth_date IS NOT NULL THEN CAST(strftime('%Y', {q(ref)}) AS INTEGER) - "
            f"CAST(strftime('%Y', p.birth_date) AS INTEGER) - (strftime('%m-%d', {q(ref)}) < strftime('%m-%d', p.birth_date)) "
            "ELSE coalesce(p.age_reported, 0) END)")


def render_hcc_persons(p, as_of):
    y, codes = p['year'], p['v28_codes']
    ref = f'{y + 1}-02-01'
    conds = []
    if codes.get('any'): conds.append(f"x.code IN ({', '.join(q(c) for c in codes['any'])})")
    for code, sex, lo, hi in codes.get('cond') or []:
        c = [f"x.code = {q(code)}", f"x.age BETWEEN {int(lo)} AND {int(hi)}"]
        if sex: c.append(f"x.sex = {int(sex)}")
        conds.append('(' + ' AND '.join(c) + ')')
    match = ' OR '.join(conds) or '0'
    return ("WITH x AS (SELECT DISTINCT d.patient_id, upper(replace(d.icd10, '.', '')) AS code, "
            f"{_age_sql(ref)} AS age, CASE WHEN upper(coalesce(p.sex_reported, '')) LIKE 'F%' THEN 2 ELSE 1 END AS sex "
            "FROM payer_claim_diagnosis d JOIN payer_claim c ON c.payer_claim_id = d.payer_claim_id "
            f"LEFT JOIN patient p ON p.patient_id = d.patient_id WHERE {FINAL_PAID} AND {_year('c.service_from', y)}) "
            f"SELECT count(DISTINCT x.patient_id) AS persons FROM x WHERE {match}")


def render_deaths_by_product(p, as_of):
    a = as_of if p['date'] == 'as_of' else p['date']
    col = 'product' if p.get('group_by', 'product') == 'product' else 'line_of_business'
    return (f"WITH d AS (SELECT patient_id, death_date FROM death WHERE death_date IS NOT NULL AND death_date <= {q(a)}), "
            f"s AS (SELECT d.patient_id, e.{col} AS k, row_number() OVER (PARTITION BY d.patient_id "
            "ORDER BY e.coverage_start DESC, e.record_id DESC) AS rn FROM d JOIN enrollment_span e ON e.patient_id = d.patient_id "
            "AND e.coverage_start <= date(d.death_date, '-1 day') AND (e.coverage_end IS NULL OR e.coverage_end >= date(d.death_date, '-1 day')) "
            f"AND e.product NOT IN ({', '.join(q(x) for x in SECONDARY)})) "
            "SELECT coalesce(s.k, 'uninsured') AS key, count(*) AS value FROM d LEFT JOIN s ON s.patient_id = d.patient_id AND s.rn = 1 "
            "GROUP BY 1 ORDER BY 1")


def render_pcp_affiliations_ended(p, as_of):
    a = as_of if p.get('date') in (None, 'as_of') else p['date']
    lim = q(as_of[:7])
    window = _year('ended', p['year']) if p.get('year') else f"ended <= {q(a)}"
    return ("WITH r AS MATERIALIZED (SELECT x.provider_npi AS npi, x.practice_id AS practice, nullif(trim(x.term_date), '') AS term_date, "
            "coalesce(substr(d.period, 1, 7), x._drop) AS m, x.pcp_flag FROM raw_rosters_aco_provider_roster x "
            "LEFT JOIN delivery_file d ON d.relpath = x._file WHERE x.provider_npi GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' "
            "AND coalesce(x.practice_id, '') <> ''), "
            f"r2 AS (SELECT * FROM r WHERE m <= {lim}), lm AS (SELECT max(m) AS m FROM r2), "
            "aff AS (SELECT npi, practice, min(term_date) AS term_date, max(m) AS last_m FROM r2 GROUP BY npi, practice "
            "HAVING max(pcp_flag = 'Y') = 1), "
            "e AS (SELECT npi, practice, coalesce(term_date, CASE WHEN last_m < (SELECT m FROM lm) "
            "THEN date(last_m || '-01', '+1 month') END) AS ended FROM aff) "
            f"SELECT count(*) AS affiliations FROM e WHERE ended IS NOT NULL AND {window}")


RENDER = {'claims_finance': render_claims_finance, 'pmpm': render_pmpm, 'member_months': render_member_months,
          'attribution_counts': render_attribution_counts, 'top_n_cost_members': render_top_n_cost_members,
          'readmissions': render_readmissions, 'admissions': render_admissions, 'appointments': render_appointments,
          'appointment_repeat': render_appointment_repeat, 'paid_line_visits': render_paid_line_visits,
          'adjudication_count': render_adjudication_count, 'noshow_by_source': render_noshow_by_source,
          'pharmacy_tier_share': render_pharmacy_tier_share, 'hcc_persons': render_hcc_persons,
          'deaths_by_product': render_deaths_by_product, 'pcp_affiliations_ended': render_pcp_affiliations_ended}

RETURNS = {'claims_finance': lambda p: 'table' if p.get('group_by') else 'value', 'pmpm': lambda p: 'value',
           'member_months': lambda p: 'table' if p.get('group_by') else 'count', 'attribution_counts': lambda p: 'table',
           'top_n_cost_members': lambda p: 'patient_set', 'readmissions': lambda p: 'value' if p['measure'] == 'rate' else 'count',
           'admissions': lambda p: 'count', 'appointments': lambda p: 'table' if p.get('by') == 'month' else 'count',
           'appointment_repeat': lambda p: 'patient_set', 'paid_line_visits': lambda p: 'count', 'adjudication_count': lambda p: 'count',
           'noshow_by_source': lambda p: 'table', 'pharmacy_tier_share': lambda p: 'value', 'hcc_persons': lambda p: 'count',
           'deaths_by_product': lambda p: 'table', 'pcp_affiliations_ended': lambda p: 'count'}


# ---- CMS-HCC V28 code list (hcc_persons) -------------------------------------------------------------------------------
_AGES = range(0, 121)


def _v28_qualifying(code):
    """{(sex, age)} for which this ICD-10 code alone yields a V28 payment HCC (risk_adjustment crosswalk, age/sex edits,
    heart-failure patch, payment set). A code that yields one only beside another HF category adds no person: that
    partner category already qualifies the person."""
    from . import risk_adjustment as ra
    m = ra.load_model('V28')
    key = ra.icd_key(code)
    if key not in m['mapping']: return set()
    return {(sex, age) for sex in (1, 2) for age in _AGES if ra.apply_hierarchy(m, ra.ccs_for(m, [key], age, sex))[0]}


def _intervals(ages):
    out, run = [], []
    for a in sorted(ages):
        if run and a == run[-1] + 1: run.append(a)
        else:
            if run: out.append((run[0], run[-1]))
            run = [a]
    if run: out.append((run[0], run[-1]))
    return out


def v28_code_list(codes):
    """ICD-10 codes -> {'any': [codes qualifying at every age / sex], 'cond': [[code, sex (0 = both), min_age, max_age]]};
    codes with no V28 payment HCC are dropped. Ages above the last tabulated age follow it (open upper bound)."""
    full = {(sex, age) for sex in (1, 2) for age in _AGES}
    out = {'any': [], 'cond': []}
    for code in sorted({__import__('re').sub(r'[^A-Z0-9]', '', str(c or '').upper()) for c in codes} - {''}):
        ok = _v28_qualifying(code)
        if not ok: continue
        if ok == full:
            out['any'].append(code); continue
        by_sex = {sex: {a for s2, a in ok if s2 == sex} for sex in (1, 2)}
        if by_sex[1] == by_sex[2]:
            for lo, hi in _intervals(by_sex[1]): out['cond'].append([code, 0, lo, 999 if hi == _AGES[-1] else hi])
        else:
            for sex in (1, 2):
                for lo, hi in _intervals(by_sex[sex]): out['cond'].append([code, sex, lo, 999 if hi == _AGES[-1] else hi])
    return out


def _validate_v28_codes(codes, year):
    if not isinstance(year, int): raise ValueError('hcc_persons needs a year')
    if not isinstance(codes, dict) or set(codes) - {'any', 'cond'}: raise ValueError('hcc_persons needs the resolved V28 code list')
    rebuilt = v28_code_list(list(codes.get('any') or []) + [c[0] for c in codes.get('cond') or []])
    if rebuilt != {'any': sorted(codes.get('any') or []), 'cond': sorted(map(list, codes.get('cond') or []))}:
        raise ValueError('hcc_persons code list is not the V28 crosswalk for these codes')


def resolve_v28_codes(path, year):
    """The distinct diagnosis codes on the year's final paid payer medical claims in this workspace (read-only), reduced to
    the V28 code list. path: a stack / workspace directory holding semantic.sqlite3, or the sqlite file itself."""
    import sqlite3
    from pathlib import Path
    target = Path(path)
    target = target / 'semantic.sqlite3' if target.is_dir() else target
    if not target.is_file(): raise ValueError(f'no semantic layer at {target}')
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        codes = [r[0] for r in db.execute(
            f"SELECT DISTINCT d.icd10 FROM payer_claim_diagnosis d JOIN payer_claim c ON c.payer_claim_id = d.payer_claim_id "
            f"WHERE {FINAL_PAID} AND {_year('c.service_from', year)} AND d.icd10 IS NOT NULL")]
    finally:
        db.close()
    out = v28_code_list(codes)
    out['cond'] = sorted(out['cond'])
    return out


def validate(spec):
    if not isinstance(spec, dict) or spec.get('family') not in FAMILIES: raise ValueError('unknown vbc family')
    s = copy.deepcopy(spec)
    p, f = s['parameters'], s['family']
    if 'year' in p and p['year'] is not None and not (isinstance(p['year'], int) and 2000 <= p['year'] <= 2100): raise ValueError('bad year')
    for k, allowed in (('products', PRODUCTS), ('mm_products', PRODUCTS), ('lobs', LOBS), ('mm_lobs', LOBS)):
        for x in p.get(k) or []:
            if x not in allowed: raise ValueError(f'unknown {k} value {x!r}')
    if f == 'claims_finance':
        if p.get('measure') not in MEASURES: raise ValueError('bad measure')
        if p.get('group_by') not in (None, 'product', 'line_of_business'): raise ValueError('bad group_by')
        if p['measure'] == 'pharmacy_paid' and (p.get('group_by') or p.get('products') or p.get('lobs')): raise ValueError('pharmacy paid takes no product')
    elif f in ('pmpm', 'member_months'):
        if p.get('mm_scope') not in ('primary', 'all'): raise ValueError('mm_scope must be primary or all')
        if f == 'member_months' and p.get('group_by') not in (None, 'product', 'line_of_business'): raise ValueError('bad group_by')
    elif f == 'attribution_counts':
        if p.get('date') != 'as_of' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(p.get('date'))): raise ValueError('bad date')
    elif f == 'top_n_cost_members':
        if not isinstance(p.get('n'), int) or not 1 <= p['n'] <= 1000: raise ValueError('n must be 1..1000')
    elif f in ('readmissions', 'admissions'):
        types = p.get('admission_types')
        if f == 'admissions' and p.get('unit', 'stays') not in ('stays', 'persons'): raise ValueError('bad admissions unit')
        if f == 'admissions' and p.get('unit') == 'persons':
            if not isinstance(p.get('min_stays', 1), int) or not 1 <= p.get('min_stays', 1) <= 50: raise ValueError('bad min_stays')
        elif not types: raise ValueError('admission types required')
        if types is not None and not all(isinstance(t, int) and 1 <= t <= 9 for t in types): raise ValueError('bad admission types')
        if f == 'readmissions':
            if p.get('measure') not in ('count', 'rate'): raise ValueError('bad measure')
            if not isinstance(p.get('days'), int) or not 1 <= p['days'] <= 365: raise ValueError('bad days')
    elif f in ('appointments', 'appointment_repeat'):
        for x in p.get('statuses') or []:
            if x not in APPT_STATUSES: raise ValueError(f'unknown appointment status {x!r}')
        if f == 'appointment_repeat' and (not p.get('statuses') or not isinstance(p.get('min_count'), int) or not 1 <= p['min_count'] <= 100):
            raise ValueError('bad repeat parameters')
        if f == 'appointments' and p.get('by') not in (None, 'month'): raise ValueError('bad by')
    elif f == 'paid_line_visits':
        if not p.get('codes') or not all(re.fullmatch(r'[A-Z]?\d{4,5}(?:-[A-Z]?\d{4,5})?', c) for c in p['codes']): raise ValueError('bad codes')
    elif f == 'adjudication_count':
        if not p.get('statuses') or set(p['statuses']) - set(ADJ_STATUSES): raise ValueError('bad statuses')
        if p.get('carc') is not None and not re.fullmatch(r'[A-Z]{2}-\d{1,3}', p['carc']): raise ValueError('bad carc')
        if p.get('unit') not in ('adjudications', 'claims'): raise ValueError('bad unit')
    elif f == 'noshow_by_source':
        for k, lo, hi in (('year_from', 2000, 2100), ('year_to', 2000, 2100), ('min_appointments', 0, 1000000), ('n', 1, 1000)):
            if not isinstance(p.get(k), int) or isinstance(p.get(k), bool) or not lo <= p[k] <= hi: raise ValueError(f'bad {k}')
        if p['year_from'] > p['year_to']: raise ValueError('year window reversed')
    elif f == 'pharmacy_tier_share':
        if p.get('tier') not in TIERS: raise ValueError('bad tier')
    elif f == 'hcc_persons':
        _validate_v28_codes(p.get('v28_codes'), p.get('year'))
    elif f == 'deaths_by_product':
        if p.get('date') != 'as_of' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(p.get('date'))): raise ValueError('bad date')
        if p.get('group_by', 'product') not in ('product', 'line_of_business'): raise ValueError('bad group_by')
    elif f == 'pcp_affiliations_ended':
        if p.get('year') is None and p.get('date') != 'as_of' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(p.get('date'))):
            raise ValueError('bad date')
    if s.get('returns') != RETURNS[f](p): raise ValueError(f'returns must be {RETURNS[f](p)}')
    return s


def render(spec, as_of):
    s = validate(spec)
    body = RENDER[s['family']](s['parameters'], as_of)
    return f"-- DEFINITION: {fc.statement(_module(), s, as_of)}\n{body}"


# ============================================================================================ deterministic parser
# Standard sentences of the delivery's definitions; each is represented by the rendered SQL (named flags record which).
BOILERPLATE = [
    ('scope', r"scope: every patient in the \(sampled\) delivery, duplicate charts counted as their person\."),
    ('all_patients', r"^all patients\."),
    ('primary_spans', r"delivered enrollment spans \(primary coverage; medigap and cob secondary excluded; a span superseded by a "
                      r"later version of the same coverage is replaced by it, while a span superseded only by a member_id_reissued "
                      r"continuation is kept\) and delivered death records\."),
    ('appointment_rules', r"appointments: each appointment_id once, in its latest delivered version, from every delivered ehr "
                          r"scheduling record \(.*?\); slot date = slot_start, else scheduled_time/scheduled_date, else the "
                          r"appointment's date \(.*?\); attended and arrived count as fulfilled, rescheduled as cancelled\."),
    ('native_allowed', r"native allowed amounts: use the claim-level allowed amount; for inpatient drg claims whose payer flat "
                       r"medical lines show 0 allowed, the claim allowed is the 835 paid amount \(clp04\) plus patient "
                       r"responsibility \(cas pr\)\."),
    ('native_liability', r"native: patient responsibility is the payer's adjudicated member liability \(835 cas pr, flat member "
                         r"liability\); cclf payment amounts are not used\."),
    ('stay_rule', r"inpatient stay = distinct person and admission date on non-voided institutional payer claims with bill type "
                  r"011x, final adjudication\."),
    ('table_keys_product', r"table keys are the delivered product values\."),
    ('release_wide', r"^release-wide\."),
]
_P = r'(?P<prod>commercial|marketplace|medicaid|medicare_ffs|medicare_advantage|dual_ffs|dual_ma|medigap|cob_secondary|medicare|dual)'
_G = r"(?:,? by (?:the claim's )?(?P<group>product|line of business))?"
SHAPES = [
    ('claims_paid', rf"sum of paid_amount over every payer medical claim adjudication with a paid date in (?P<year>20\d\d) "
                    rf"\(reversals and recoupments are negative; voids pay 0\){_G}\."),
    ('pmpm', rf"allowed amount on final-adjudication paid (?:{_P} )?medical claims with service in (?P<year>20\d\d)(?: \(all products\))? / "
             rf"(?:(?P<y2>20\d\d) (?P<prod2>[a-z_]+) member-months \(delivered spans, through death\)|all (?P<y3>20\d\d) primary member-months)\."),
    ('claims_allowed', rf"(?:sum of )?allowed amount on final-adjudication paid (?:{_P} )?medical claims with service in (?P<year>20\d\d)"
                       rf"(?: \(all products\))?{_G}\."),
    ('member_liability', r"sum of patient_responsibility(?: \([^)]*\))? on payer medical claims whose (?:latest|final) adjudication is "
                         r"paid, service_from in (?P<year>20\d\d)(?:; cclf payment amounts are not used)?\."),
    ('pharmacy_paid', r"sum of plan_paid on payer pharmacy claims with fill date in (?P<year>20\d\d) \(paid fills minus recoupments; "
                      r"reversed and rejected pay 0\)\."),
    ('member_months', r"member-months in (?P<year>20\d\d) \(covered on the 1st, through death\)(?: by (?P<group>product|line of business))?, "
                      r"(?P<scope>all delivered spans including secondary products|primary spans only)(?:; a span version superseded by a "
                      r"later delivered version of the same coverage \(retro-termination, correction\) is replaced by it \(a span "
                      r"superseded only by a member_id_reissued continuation is kept\))?,? and each person counts once per month"
                      r"(?: per (?:product|line of business))?\."),
    ('attribution', r"persons alive and enrolled on (?P<date>as_of|\d{4}-\d{2}-\d{2}) by the attributed practice on their active "
                    r"span \(spans with no attribution are not counted\)\."),
    ('top_n', r"the (?P<n>\d+) persons with the highest allowed amount on final-adjudication paid medical claims with service in "
              r"(?P<year>20\d\d)\."),
    ('readmissions', r"inpatient stays admitted in (?P<year>20\d\d) as emergency or urgent \(ub-04 admission type 1 or 2\) within "
                     r"(?:0-)?(?P<days>\d+) days after a prior inpatient discharge of the same person(?:, divided by inpatient stays "
                     r"discharged in (?P<y2>20\d\d) \(discharge date, else service-to date\), percent)?\."),
    ('admissions', r"inpatient stays admitted in (?P<year>20\d\d) with ub-04 admission type (?P<types>\d(?:(?:, | or |,)\d)*)"
                   r"(?: \([a-z ]+\))?\."),
    ('appointments', r"appointments with a (?:slot in (?P<year>20\d\d)|(?P<y2>20\d\d) slot)(?:, any final status| whose final status "
                     r"is (?P<status>[a-z_]+)(?P<resched> \(including rescheduled\))?)\."),
    ('appointments_month', r"appointments \(any final status\) by slot month \(yyyy-mm\), (?P<year>20\d\d)\."),
    ('appointment_repeat', r"persons with >= (?P<n>\d+) appointments whose final status is (?P<status>[a-z_]+)(?:, through as_of)?\."),
    ('paid_lines', r"distinct (?:member|person)-dates with a (?:final paid claim line|payer claim line) (?P<codes>[a-z0-9 ,/-]+?)"
                   r"(?: \([^)]*\))?(?: on a claim whose latest adjudication is paid)?, service in (?P<year>20\d\d)"
                   r"(?: \(provider billing lines are not paid claims\))?\."),
    ('noshow_by_source', r"scheduling source systems with >= (?P<min>\d+) fulfilled-or-no-show appointments with "
                         r"(?P<y1>20\d\d)(?:-(?P<y2>20\d\d))? slots: top (?P<n>\d+) by no-show rate, percent\."),
    ('admission_persons', r"distinct persons with >= (?P<n>\d+) inpatient stays? admitted in (?P<year>20\d\d)"
                          r"(?: with ub-04 admission type (?P<types>\d(?:(?:, | or |,)\d)*)(?: \([a-z ]+\))?)?\."),
    ('tier_share', r"paid payer pharmacy claims (?:with fill date )?in (?P<year>20\d\d) with formulary_tier (?P<tier>[a-z_]+), "
                   r"percent of paid claims\."),
    ('hcc_persons', r"persons with >= 1 cms-hcc v28 category from (?P<year>20\d\d) paid claim diagnoses"
                    r"(?: \(service in (?P<y2>20\d\d)\))?\."),
    ('deaths_by_product', r"persons with a delivered death record through (?P<date>as_of|\d{4}-\d{2}-\d{2}), by the "
                          r"(?P<group>product|line of business) of the primary span active the day before death "
                          r"\(uninsured if none\)\."),
    ('pcp_ended', r"pcp affiliations that ended \(provider departed or moved practices\) "
                  r"(?:through (?P<date>as_of|\d{4}-\d{2}-\d{2})|in (?P<year>20\d\d))\."),
    ('adjudications', r"(?P<distinct>distinct payer claims with an? )?(?P<statuses>(?:recouped|reversed|denied|voided|paid)"
                      r"(?: or (?:recouped|reversed|denied|voided|paid))*) adjudications?(?: with carc (?P<carc>[a-z]{2}-\d{1,3}))? "
                      r"(?:dated (?P<year>20\d\d)|through as_of)\."),
]
_SHAPES = [(n, re.compile(p)) for n, p in SHAPES]


def strip_boilerplate(dl):
    flags = set()
    for name, pat in BOILERPLATE:
        new = re.sub(pat, ' ', dl)
        if new != dl: flags.add(name)
        dl = ' '.join(new.split())
    return dl.strip(), flags


def _prod_filter(word):
    if word in (None, '', 'all'): return {}
    if word in PRODUCTS: return {'products': [word]}
    if word in LOBS: return {'lobs': [word]}
    raise ValueError(f'unknown product {word!r}')


def _group(g):
    return {'product': 'product', 'line of business': 'line_of_business'}.get(g)


def parse(question, as_of):
    """question "... Definition: ..." -> {'family', 'parameters', 'returns', 'defaults', 'has_definition'} or None."""
    qtext, dtext = fc.split_definition(fc.clean(question))
    dl = dtext.lower().strip()
    if not dl: return None  # these families render a stated definition only
    body, flags = strip_boilerplate(dl)
    hits = [(n, m) for n, rx in _SHAPES for m in [rx.fullmatch(body)] if m]
    if len(hits) != 1: return None
    shape, m = hits[0]
    g = m.groupdict()
    try:
        fam, p = _params(shape, g, flags, as_of)
    except ValueError:
        return None
    if fam is None: return None
    return {'family': fam, 'parameters': p, 'returns': RETURNS[fam](p), 'defaults': [], 'has_definition': True}


def _params(shape, g, flags, as_of):
    y = int(g['year']) if g.get('year') else None
    if shape == 'claims_paid':
        return 'claims_finance', {'measure': 'paid', 'year': y, **({'group_by': _group(g['group'])} if g.get('group') else {})}
    if shape == 'claims_allowed':
        return 'claims_finance', {'measure': 'allowed', 'year': y, **_prod_filter(g.get('prod')),
                                  **({'group_by': _group(g['group'])} if g.get('group') else {})}
    if shape == 'member_liability':
        return 'claims_finance', {'measure': 'member_liability', 'year': y}
    if shape == 'pharmacy_paid':
        return 'claims_finance', {'measure': 'pharmacy_paid', 'year': y}
    if shape == 'pmpm':
        f = _prod_filter(g.get('prod'))
        if g.get('y3'):
            if int(g['y3']) != y or f: return None, None
            return 'pmpm', {'year': y, 'mm_scope': 'primary'}
        if int(g['y2']) != y or g.get('prod2') != g.get('prod') or not f: return None, None
        return 'pmpm', {'year': y, **f, 'mm_scope': 'all', **{('mm_' + k): v for k, v in f.items()}}
    if shape == 'member_months':
        scope = 'all' if g['scope'].startswith('all') else 'primary'
        return 'member_months', {'year': y, 'mm_scope': scope, **({'group_by': _group(g['group'])} if g.get('group') else {})}
    if shape == 'attribution':
        if 'primary_spans' not in flags: return None, None
        return 'attribution_counts', {'date': g['date']}
    if shape == 'top_n':
        return 'top_n_cost_members', {'year': y, 'n': int(g['n'])}
    if shape == 'readmissions':
        if 'stay_rule' not in flags: return None, None
        if g.get('y2') and int(g['y2']) != y: return None, None
        return 'readmissions', {'year': y, 'days': int(g['days']), 'admission_types': [1, 2], 'measure': 'rate' if g.get('y2') else 'count'}
    if shape == 'admissions':
        if 'stay_rule' not in flags: return None, None
        return 'admissions', {'year': y, 'admission_types': sorted({int(t) for t in re.findall(r'\d', g['types'])})}
    if shape in ('appointments', 'appointments_month', 'appointment_repeat'):
        if 'appointment_rules' not in flags: return None, None
        if shape == 'appointment_repeat':
            st = g['status']
            if st not in APPT_STATUSES: return None, None
            return 'appointment_repeat', {'statuses': [st], 'min_count': int(g['n'])}
        if shape == 'appointments_month':
            return 'appointments', {'year': y, 'by': 'month'}
        y = y or int(g['y2'])
        p = {'year': y}
        if g.get('status'):
            if g['status'] not in APPT_STATUSES: return None, None
            p['statuses'] = [g['status']] + (['rescheduled'] if g.get('resched') else [])
        return 'appointments', p
    if shape == 'paid_lines':
        codes = fc.codes_in(g['codes'].upper())
        rest = re.sub(r'[A-Z]?\d{4,5}|\bOR\b|\bAND\b|[ ,/-]', '', g['codes'].upper())
        if not codes or rest: return None, None
        return 'paid_line_visits', {'year': y, 'codes': codes}
    if shape == 'noshow_by_source':
        if 'appointment_rules' not in flags: return None, None
        y1 = int(g['y1'])
        return 'noshow_by_source', {'year_from': y1, 'year_to': int(g['y2'] or y1), 'min_appointments': int(g['min']), 'n': int(g['n'])}
    if shape == 'admission_persons':
        if 'stay_rule' not in flags: return None, None
        p = {'year': y, 'unit': 'persons', 'min_stays': int(g['n'])}
        if g.get('types'): p['admission_types'] = sorted({int(t) for t in re.findall(r'\d', g['types'])})
        return 'admissions', p
    if shape == 'tier_share':
        if g['tier'] not in TIERS: return None, None
        return 'pharmacy_tier_share', {'year': y, 'tier': g['tier']}
    if shape == 'hcc_persons':
        if g.get('y2') and int(g['y2']) != y: return None, None
        return 'hcc_persons', {'year': y}  # the V28 code list is resolved against the workspace in plan()
    if shape == 'deaths_by_product':
        if 'primary_spans' not in flags: return None, None
        return 'deaths_by_product', {'date': g['date'], 'group_by': _group(g['group'])}
    if shape == 'pcp_ended':
        return 'pcp_affiliations_ended', ({'year': y} if y else {'date': g['date']})
    if shape == 'adjudications':
        statuses = re.findall(r'recouped|reversed|denied|voided|paid', g['statuses'])
        return 'adjudication_count', {'statuses': statuses, 'carc': g['carc'].upper() if g.get('carc') else None,
                                      'year': y, 'unit': 'claims' if g.get('distinct') else 'adjudications'}
    return None, None


# ============================================================================================ runtime operation
def _module():
    import sys
    return sys.modules[__name__]


def plan(path, question, as_of, **_):
    """hcc_persons needs the workspace (its paid-claim codes reduced to the V28 code list); without a path it declines."""
    try:
        parsed = parse(question, as_of)
    except (ValueError, KeyError, TypeError):
        return None
    if parsed is not None and parsed['family'] == 'hcc_persons':
        if path is None: return None
        try:
            codes = resolve_v28_codes(path, parsed['parameters']['year'])
        except (ValueError, OSError, __import__('sqlite3').Error):
            return None
        return fc.plan(_HccModule(codes), question, as_of)
    return fc.plan(_module(), question, as_of)


class _HccModule:
    """The module seen by family_common.plan for an hcc_persons question: parse() carries the resolved code list."""
    def __init__(self, codes):
        mod = _module()
        self._codes, self.VERSION, self.ROUTE, self.FAMILIES = codes, mod.VERSION, mod.ROUTE, mod.FAMILIES
        self.validate, self.render = mod.validate, mod.render

    def parse(self, question, as_of):
        parsed = parse(question, as_of)
        if parsed is not None:
            parsed['parameters'] = {**parsed['parameters'], 'v28_codes': self._codes}
        return parsed


def reuse(path, question):
    return fc.reuse(_module(), path, question)


def record(path, question, planner_receipt, answer):
    return fc.record(_module(), path, question, planner_receipt, answer)
