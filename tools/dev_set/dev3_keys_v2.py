"""dev3 answer keys v2: the keys whose v1 value depends on a definition Mike decided on 2026-10-25.

    .venv/bin/python -m tools.dev_set.dev3_keys_v2            # (re)write tools/dev_set/dev3_keys_v2.jsonl
    .venv/bin/python -m tools.dev_set.dev3_keys_v2 --check    # recompute and compare with the file (no write)

dev3_questions.jsonl is never edited (v1 stays reproducible). Each row of dev3_keys_v2.jsonl is one changed field:
    {id, field, v1, v2, sql, rule}
`field` is a path into the question row: `key.answer`, `key.numerator`, or, for a rubric fact,
`key.answer.required_facts[id=<fact id>].<attr>` (value / claim / patterns / items). run.apply_keys_v2() overlays them at load time.

Decisions (config/agere/definition_decisions_2026-10-25.json, decision-context/3):
  R1  counts / volumes / rates / shares of EVENTS (admissions, ED visits, stays, fills, appointments, no-shows, discharges) include every
      patient, living or deceased (duplicate-chart rows are not filtered: 533 inpatient admissions in 2029, Mike's disputed example);
      counts and lists of PATIENTS stay living-only.
  R2  "hospital stay" / "admission" / "hospitalization" = utilization_event kind IN ('inpatient','observation'); "inpatient" alone = inpatient.
  R3  pharmacy spend = SUM(pharmacy_claim_event.payer_paid_delta_cents) over all transactions (reversals netted) by event_date.
  R4  medical spend / cost per claim = paid on counted payer_claim (counts_in_metrics = 1) by service date.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.dev_set import definitions as D
from tools.dev_set.b5_insight import lit, n as fact_n, rank as fact_rank, t as fact_t
from tools.dev_set.keys import connect

HERE = Path(__file__).resolve().parent
V1 = HERE / 'dev3_questions.jsonl'
OUT = HERE / 'dev3_keys_v2.jsonl'

R1 = 'R1: counts/volumes/rates/shares of events include every patient (living or deceased); patient counts stay living-only'
R2 = "R2: hospital stay / admission / hospitalization = utilization_event kind IN ('inpatient','observation'); 'inpatient' alone = inpatient"
R3 = 'R3: pharmacy spend = SUM(pharmacy_claim_event.payer_paid_delta_cents) over all transactions (reversals netted) by event_date'
R4 = 'R4: medical spend / cost per claim = paid on counted payer_claim (counts_in_metrics = 1) by service date'
HOSP = "kind IN ('inpatient', 'observation')"
LIVQ = f"patient_id IN ({D.LIVING})"
pct = lambda x: f'{100 * x:.1f}%'


def load_v1():
    out = {}
    for line in V1.read_text().splitlines():
        if '"split": "heldout"' in line:
            continue
        q = json.loads(line)
        out[q['id']] = q
    return out


class Keys:
    def __init__(self):
        self.c = connect()
        self.v1 = load_v1()
        self.rows = []

    def q(self, sql):
        return self.c.execute(sql).fetchall()

    def one(self, sql):
        r = self.c.execute(sql).fetchone()
        return r[0] if r else None

    # --- emit ---------------------------------------------------------------------------------------------------------
    def scalar(self, qid, v2, sql, rule, field='key.answer', **extra):
        """A top-level key field; rows are emitted only when the value changes."""
        node = self.v1[qid]
        for part in field.split('.'):
            node = node[part]
        if node != v2:
            self.rows.append({'id': qid, 'field': field, 'v1': node, 'v2': v2, 'sql': sql, 'rule': rule})

    def share(self, qid, num, den, sql, rule):
        self.scalar(qid, round(num / den, 6), sql, rule)
        self.scalar(qid, num, sql, rule, 'key.numerator')
        self.scalar(qid, den, sql, rule, 'key.denominator')

    def rubric(self, qid, facts, sql, rule):
        """Diff the rebuilt facts against the v1 rubric facts and emit one row per changed attribute."""
        old = {f['id']: f for f in self.v1[qid]['key']['answer']['required_facts']}
        for f in facts:
            o = old[f['id']]
            for attr, val in f.items():
                if attr in ('id', 'kind'):
                    continue
                if o.get(attr) != val:
                    self.rows.append({'id': qid, 'field': f"key.answer.required_facts[id={f['id']}].{attr}", 'v1': o.get(attr), 'v2': val,
                                      'sql': sql, 'rule': rule})

    # --- condition cohorts of every patient (events of deceased patients too) -------------------------------------------------
    def cond_patients(self, name):
        return f"(SELECT DISTINCT patient_id FROM ({D.condition_evidence(name)}))"


def build():
    k = Keys()
    one, rows = k.one, k.q

    # ---- single patient ---------------------------------------------------------------------------------------------------
    p = re.search(r"patient_id = '([^']+)'", k.v1['x1-17']['key']['sql']).group(1)
    sql = f"SELECT COUNT(*) > 0 FROM utilization_event WHERE patient_id = '{p}' AND {HOSP} AND {D.in_last_year('event_date')}"
    k.scalar('x1-17', bool(one(sql)), sql, R2)
    p = re.search(r"patient_id = '([^']+)'", k.v1['x1-18']['key']['sql']).group(1)
    sql = (f"SELECT SUM(julianday(end_date) - julianday(event_date)) FROM utilization_event WHERE patient_id = '{p}' AND {HOSP} "
           f"AND end_date IS NOT NULL")
    k.scalar('x1-18', float(one(sql)), sql, R2)
    p = re.search(r"patient_id = '([^']+)'", k.v1['x1-20']['key']['sql']).group(1)
    sql = (f"SELECT ROUND(SUM(payer_paid_delta_cents) / 100.0, 2) FROM pharmacy_claim_event WHERE patient_id = '{p}' "
           f"AND {D.in_year('event_date')}")
    k.scalar('x1-20', float(one(sql)), sql, R3)

    # ---- population: events of everyone ---------------------------------------------------------------------------------------
    sql = f"SELECT SUM(readmitted_30d), COUNT(*) FROM utilization_event WHERE kind = 'inpatient' AND {D.in_year('event_date')}"
    num, den = rows(sql)[0]
    k.share('x3-07', num, den, sql, R1)

    sql = f"""SELECT SUM(EXISTS (SELECT 1 FROM ({D.FOLLOW_UP_VISIT}) v WHERE v.patient_id = u.patient_id AND v.event_date > u.event_date
                           AND julianday(v.event_date) - julianday(u.event_date) <= 7)), COUNT(*)
          FROM utilization_event u WHERE u.kind = 'ed_visit' AND {D.in_year('u.event_date')}"""
    num, den = rows(sql)[0]
    k.share('x3-08', num, den, ' '.join(sql.split()), R1)

    sql = f"""SELECT COUNT(*) FROM utilization_event u WHERE u.{HOSP} AND u.end_date IS NOT NULL AND substr(u.end_date, 1, 4) = '2029'
           AND NOT EXISTS (SELECT 1 FROM ({D.FOLLOW_UP_VISIT}) v WHERE v.patient_id = u.patient_id AND v.event_date > u.end_date
                           AND julianday(v.event_date) - julianday(u.end_date) <= 14)"""
    k.scalar('x3-09', one(sql), ' '.join(sql.split()), R1 + '; ' + R2)

    sql = (f"SELECT ROUND(AVG(julianday(end_date) - julianday(event_date)), 3) FROM utilization_event WHERE kind = 'inpatient' "
           f"AND end_date IS NOT NULL AND {D.in_year('event_date')}")
    k.scalar('x3-23', one(sql), sql, R1)

    # ---- pharmacy spend (R3) -------------------------------------------------------------------------------------------------
    sql = f"SELECT ROUND(SUM(payer_paid_delta_cents) / 100.0, 2) FROM pharmacy_claim_event WHERE {D.in_year('event_date')}"
    spend29 = one(sql)
    k.scalar('x4-09', spend29, sql, R3)
    mm = one("SELECT COUNT(*) FROM member_month WHERE substr(month, 1, 4) = '2029'")
    sql = (f"SELECT ROUND((SELECT SUM(payer_paid_delta_cents) / 100.0 FROM pharmacy_claim_event WHERE {D.in_year('event_date')}) / "
           f"(SELECT COUNT(*) FROM member_month WHERE substr(month, 1, 4) = '2029'), 2)")
    k.scalar('x4-10', one(sql), sql, R3)

    # pharmacy name lives only on the payer_pharmacy_claim rows (record_id); transaction-feed rows carry no pharmacy name.
    sql = (f"SELECT c.pharmacy_name AS pharmacy_name, ROUND(SUM(e.payer_paid_delta_cents) / 100.0, 2) AS plan_paid_usd FROM pharmacy_claim_event e "
           f"JOIN payer_pharmacy_claim c ON c.record_id = e.record_id WHERE substr(e.event_date, 1, 4) = '2029' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5")
    top = [{'pharmacy_name': a, 'plan_paid_usd': b} for a, b in rows(sql)]
    k.scalar('x4-12', top, sql + ' -- rows without a pharmacy name (transaction feed) cannot be attributed to a pharmacy', R3)

    # Ozempic: a product is identified by its NDC. The transaction feed carries an NDC only (no drug name), and the Ozempic NDC also appears on payer
    # claims whose drug_name is the generic "Semaglutide", so rows count when the name says Ozempic or the NDC belongs to an Ozempic-named claim.
    ndcs = lambda like: f"(SELECT DISTINCT ndc FROM payer_pharmacy_claim WHERE drug_name LIKE '%{like}%' AND ndc IS NOT NULL)"
    sql = (f"SELECT ROUND(SUM(payer_paid_delta_cents) / 100.0, 2) FROM pharmacy_claim_event WHERE {D.in_year('event_date')} "
           f"AND (drug_name LIKE '%Ozempic%' OR ndc IN {ndcs('Ozempic')})")
    k.scalar('x4-13', one(sql), sql + ' -- by NDC of the Ozempic-named claims; the name-only value (payer-feed rows labelled Ozempic) is 275023.63', R3)

    # ---- insight rubrics --------------------------------------------------------------------------------------------------------
    # x5-01 / x5-02 readmissions (inpatient, everyone)
    rate = one(f"SELECT AVG(readmitted_30d) FROM utilization_event WHERE kind = 'inpatient' AND {D.in_year('event_date')}")
    cond_rates = []
    for name in ('diabetes', 'heart_failure', 'ckd', 'copd', 'hypertension'):
        r = one(f"SELECT AVG(u.readmitted_30d) FROM utilization_event u WHERE u.kind = 'inpatient' AND {D.in_year('u.event_date')} "
                f"AND u.patient_id IN {k.cond_patients(name)}")
        cond_rates.append((r, name))
    cond_rates.sort(reverse=True)
    top_c = cond_rates[0]
    words = {'diabetes': r'diabet', 'heart_failure': r'heart failure|\bHF\b|CHF', 'ckd': r'CKD|kidney', 'copd': r'COPD|chronic obstructive',
             'hypertension': r'hypertens|\bHTN\b', 'cad': r'CAD|coronary'}
    k.rubric('x5-01', [
        fact_n('overall', f'30-day readmission rate on 2029 admissions is {pct(rate)}', rate, abs_=0.01, unit='proportion', near=[r'readmi']),
        fact_n('top_cond', f'{top_c[1]} patients readmit most among the major conditions ({pct(top_c[0])})', top_c[0], abs_=0.015, unit='proportion', near=[words[top_c[1]]]),
        fact_t('top_cond_named', f'the answer names {top_c[1]} as the highest-readmission condition group', [words[top_c[1]]])],
        "2029 inpatient utilization_event readmitted_30d overall and by condition cohort (condition evidence of any patient, living or not)", R1)

    hosp = rows(f"""SELECT organization_id, COUNT(*), AVG(readmitted_30d) FROM utilization_event WHERE kind = 'inpatient' AND {D.in_year('event_date')}
                    GROUP BY 1 HAVING COUNT(*) >= 20 ORDER BY 3 DESC""")
    h1, h2 = hosp[0], hosp[1]
    k.rubric('x5-02', [
        fact_t('top_hospital', f'{h1[0]} has the highest 2029 readmission rate ({pct(h1[2])} of {h1[1]} stays)', [lit(h1[0])]),
        fact_n('top_rate', f'{h1[0]} readmission rate {pct(h1[2])}', h1[2], abs_=0.015, unit='proportion', near=[lit(h1[0])]),
        fact_n('second_rate', f'{h2[0]} is next at {pct(h2[2])}', h2[2], abs_=0.015, unit='proportion', near=[lit(h2[0])])],
        '2029 inpatient events (every patient) by organization_id with >= 20 stays: readmitted_30d rate', R1)

    # x5-03 admissions (hospital admissions = inpatient + observation)
    a28 = one(f"SELECT COUNT(*) FROM utilization_event WHERE {HOSP} AND {D.in_year('event_date', '2028')}")
    a29 = one(f"SELECT COUNT(*) FROM utilization_event WHERE {HOSP} AND {D.in_year('event_date')}")
    assert a29 < a28, 'premise direction changed: review the direction fact'
    k.rubric('x5-03', [fact_n('a28', f'Hospital admissions (inpatient + observation) in 2028 were {a28}', a28, abs_=0),
                       fact_n('a29', f'Hospital admissions (inpatient + observation) in 2029 were {a29}', a29, abs_=0)],
             f"SELECT COUNT(*) FROM utilization_event WHERE {HOSP} AND substr(event_date,1,4) = '<year>'", R2)

    # x5-04 ED concentration: visits of every patient; the count of ED users is a patient count (living only)
    ed_all = rows(f"SELECT patient_id, COUNT(*) c FROM utilization_event WHERE kind = 'ed_visit' AND {D.in_last_year('event_date')} GROUP BY 1 ORDER BY c DESC, patient_id")
    tot = sum(c for _, c in ed_all)
    run, kk = 0, 0
    for _, c in ed_all:
        run += c
        kk += 1
        if run >= tot / 2:
            break
    n5 = max(1, round(0.05 * len(ed_all)))
    top5 = sum(c for _, c in ed_all[:n5]) / tot
    users = one(f"SELECT COUNT(DISTINCT patient_id) FROM utilization_event WHERE kind = 'ed_visit' AND {D.in_last_year('event_date')} AND {LIVQ}")
    k.rubric('x5-04', [
        fact_n('users', f'{users} living patients had at least one ED visit in the last 12 months', users, abs_=0, near=[r'patients?']),
        fact_n('half', f'The most frequent {kk} ED users account for half of the {tot} visits (all patients)', kk, abs_=2, near=[r'half|50']),
        fact_n('top5', f'The top 5% of ED users ({n5} patients) account for {pct(top5)} of visits', top5, abs_=0.02, unit='proportion', near=[r'5%|five percent|top 5'])],
        'ED visits of every patient in the 12 months ending the as-of date, per patient, ordered by count (half = fewest patients covering 50% of visits; '
        'top5 = share of visits by the top 5% of ED users); users = living patients with >= 1 ED visit', R1)

    # x5-07 transport barrier vs no-shows (v1 filtered living patients)
    tr = rows(f"""SELECT t.patient_id IS NOT NULL AS barrier, AVG(a.final_status = 'no_show'), COUNT(*) FROM appointment a
                    LEFT JOIN (SELECT DISTINCT patient_id FROM social_need WHERE need = 'transport' AND positive = 1) t USING (patient_id)
                   WHERE a.final_status IN ('fulfilled', 'attended', 'arrived', 'no_show') AND {D.in_last_year('a.scheduled_date')} GROUP BY 1 ORDER BY 1""")
    r_no, r_yes = tr[0][1], tr[1][1]
    assert r_yes > r_no
    k.rubric('x5-07', [
        fact_n('barrier', f'No-show rate for patients who screened positive for a transportation barrier: {pct(r_yes)}', r_yes, abs_=0.01, unit='proportion', near=[r'transport|barrier']),
        fact_n('no_barrier', f'No-show rate for everyone else: {pct(r_no)}', r_no, abs_=0.01, unit='proportion', near=[r'everyone else|other|without|no barrier|not'])],
        'appointments scheduled in the last 12 months (fulfilled / attended / arrived / no_show) of every patient, by whether the patient ever screened positive for transport', R1)

    # x5-09 pharmacy bill by year; brand GLP-1 / SGLT2 share
    r28 = one("SELECT SUM(payer_paid_delta_cents) / 100.0 FROM pharmacy_claim_event WHERE substr(event_date, 1, 4) = '2028'")
    r29 = one("SELECT SUM(payer_paid_delta_cents) / 100.0 FROM pharmacy_claim_event WHERE substr(event_date, 1, 4) = '2029'")
    brands = {'Ozempic': 'Ozempic', 'Mounjaro': 'Mounjaro', 'Trulicity': 'Trulicity', 'Farxiga': 'Farxiga', 'Jardiance': 'Jardiance'}
    like = ' OR '.join(f"drug_name LIKE '%{b}%' OR ndc IN {ndcs(b)}" for b in brands)
    g29 = one(f"SELECT SUM(payer_paid_delta_cents) / 100.0 FROM pharmacy_claim_event WHERE substr(event_date, 1, 4) = '2029' AND ({like})")
    k.rubric('x5-09', [
        fact_n('r28', f'Net pharmacy spend (all claim transactions) in 2028 was ${r28:,.0f}', r28, rel=0.01, near=[r'2028']),
        fact_n('r29', f'Net pharmacy spend (all claim transactions) in 2029 was ${r29:,.0f}', r29, rel=0.01, near=[r'2029']),
        fact_n('share', f'Ozempic, Mounjaro, Trulicity, Farxiga and Jardiance are {pct(g29 / r29)} of 2029 net pharmacy dollars (${g29:,.0f}; by drug name or NDC)',
               g29 / r29, abs_=0.03, unit='proportion', near=[r'GLP|SGLT|Ozempic|semaglutide|brand'])],
        'pharmacy_claim_event SUM(payer_paid_delta_cents) by event year; share of 2029 from rows named Ozempic / Mounjaro / Trulicity / Farxiga / Jardiance or carrying an NDC of those named claims', R3)

    # x5-12 discharges in the window (hospital = inpatient + observation; every patient)
    lo, hi = '2030-02-26', '2030-05-13'
    base = f"FROM utilization_event u WHERE u.{HOSP} AND u.end_date IS NOT NULL AND u.end_date BETWEEN '{lo}' AND '{hi}'"
    nd = one(f"SELECT COUNT(*) {base}")
    nf = one(f"""SELECT COUNT(*) {base} AND NOT EXISTS (SELECT 1 FROM ({D.FOLLOW_UP_VISIT}) v WHERE v.patient_id = u.patient_id AND v.event_date > u.end_date
                  AND julianday(v.event_date) - julianday(u.end_date) <= 14)""")
    k.rubric('x5-12', [
        fact_n('discharges', f'{nd} hospital (inpatient or observation) discharges in the window', nd, abs_=0, near=[r'discharg']),
        fact_n('no_follow', f'{nf} of them had no outpatient visit within 14 days ({pct(nf / nd)})', nf, abs_=0, near=[r'no (outpatient )?(follow|visit)|without|never'])],
        'inpatient + observation events of every patient discharged in the window, no completed outpatient visit 1-14 days after discharge', R1 + '; ' + R2)

    # x5-13 ED visits per 1,000 patients by age band: visits of every patient; denominator = living patients in the band (a patient count)
    def band(alias):
        a = D.age_of(alias)
        return f"CASE WHEN {a} < 18 THEN 'under 18' WHEN {a} < 40 THEN '18 to 39' WHEN {a} < 65 THEN '40 to 64' ELSE '65 plus' END"
    den = dict(rows(f"SELECT {band('p')} b, COUNT(*) FROM patient p WHERE p.patient_id IN ({D.LIVING}) GROUP BY 1"))
    num = dict(rows(f"SELECT {band('p')} b, COUNT(*) FROM utilization_event u JOIN patient p USING (patient_id) WHERE u.kind = 'ed_visit' "
                    f"AND {D.in_last_year('u.event_date')} GROUP BY 1"))
    br = sorted(((num[b] / den[b] * 1000, b, num[b]) for b in den), reverse=True)
    pat = {'under 18': r'under 18|children|pediatric|kids', '18 to 39': r'18[- ]?(to|-|–)[- ]?39|young adult', '40 to 64': r'40[- ]?(to|-|–)[- ]?64|middle',
           '65 plus': r'65|older|senior|elderly'}
    k.rubric('x5-13', [
        fact_t('top_band', f'{br[0][1]} has the highest ED visits per 1,000 patients ({br[0][0]:.0f})', [pat[br[0][1]]]),
        fact_n('top_rate', f'{br[0][1]}: {br[0][0]:.0f} ED visits per 1,000 patients in the last 12 months', br[0][0], rel=0.03, near=[pat[br[0][1]]]),
        fact_n('low_rate', f'{br[-1][1]}: {br[-1][0]:.0f} per 1,000', br[-1][0], rel=0.05, near=[pat[br[-1][1]]])],
        'ED visits of every patient in the last 12 months by age band (age at as-of from birth_date) per 1,000 LIVING patients in that band', R1)

    # x5-14 average inpatient claim = paid over counted claims (R4); share v1 already follows R4
    C = D.SPEND_CLAIMS
    inst = one(f"SELECT SUM(paid_cents) FILTER (WHERE admission_date IS NOT NULL) * 1.0 / SUM(paid_cents) FROM ({C}) WHERE {D.in_year('service_from')}")
    # Mike chose "paid on counted claims" with the $12,343 average shown (paid claims): the average is over counted claims that were paid
    avg_ip = one(f"SELECT AVG(paid_cents) / 100.0 FROM ({C}) WHERE admission_date IS NOT NULL AND final_status = 'paid' AND {D.in_year('service_from')}")
    k.rubric('x5-14', [
        fact_n('share', f'Claims with an inpatient admission are {pct(inst)} of 2029 paid dollars', inst, abs_=0.02, unit='proportion', near=[r'inpatient|admission|hospital']),
        fact_n('avg', f'The average paid inpatient claim is ${avg_ip:,.0f}', avg_ip, rel=0.03, near=[r'average|avg|per claim|each'])],
        'AVG(paid_cents)/100 over counted, paid payer_claim rows (counts_in_metrics = 1, final_status = paid) with an admission_date, service_from in 2029 (Mike chose the $12,343 reading)', R4)

    # x5-18 / x5-28 rising risk: living patients (a patient count) with >= 2 ED visits and no hospital admission (inpatient or observation)
    rr_sql = (f"SELECT patient_id FROM utilization_event WHERE kind = 'ed_visit' AND {D.in_last_year('event_date')} AND {LIVQ} GROUP BY 1 HAVING COUNT(*) >= 2 "
              f"EXCEPT SELECT patient_id FROM utilization_event WHERE {HOSP} AND {D.in_last_year('event_date')}")
    rr = one(f"SELECT COUNT(*) FROM ({rr_sql})")
    rr_cost = one(f"SELECT SUM(paid_cents) / 100.0 FROM ({C}) WHERE patient_id IN ({rr_sql}) AND {D.in_last_year('service_from')}")
    k.rubric('x5-18', [
        fact_n('n', f'{rr} patients had 2+ ED visits and no hospital (inpatient or observation) admission in the last 12 months', rr, abs_=0, near=[r'patients?|rising']),
        fact_n('cost', f'They accounted for ${rr_cost:,.0f} of medical paid in the last 12 months', rr_cost, rel=0.04, near=[r'paid|cost|spend|\$'])],
        'living patients with >= 2 ED visits and no inpatient/observation event in the last 12 months; their counted paid (service_from) in the same window', R2)
    dm = one(f"SELECT COUNT(*) FROM ({rr_sql}) WHERE patient_id IN {k.cond_patients('diabetes')}")
    ht = one(f"SELECT COUNT(*) FROM ({rr_sql}) WHERE patient_id IN {k.cond_patients('hypertension')}")
    k.rubric('x5-28', [fact_n('dm', f'{dm} of the rising-risk patients have diabetes', dm, abs_=0, near=[r'diabet']),
                       fact_n('htn', f'{ht} of the rising-risk patients have hypertension', ht, abs_=0, near=[r'hypertens|\bHTN\b'])],
             'the rising-risk cohort (living, 2+ ED visits, no inpatient/observation in the last 12 months) intersected with condition evidence', R2)

    # x5-20 ED top diagnoses: visit_group rows of every patient
    ed_dx = rows(f"""SELECT principal_icd10, COUNT(*) FROM visit_group WHERE visit_class IN ('emergency', 'ed_to_inpatient', 'ed_to_observation')
                     AND principal_icd10 IS NOT NULL AND {D.in_last_year('start_date')} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3""")
    k.rubric('x5-20', [
        fact_rank('order', 'top three ED principal diagnoses in order: ' + ', '.join(f'{c} ({n})' for c, n in ed_dx), [re.escape(c) for c, _ in ed_dx]),
        fact_n('top_n', f'{ed_dx[0][0]} was the principal diagnosis on {ed_dx[0][1]} ED visits', ed_dx[0][1], abs_=0, near=[re.escape(ed_dx[0][0])])],
        'visit_group rows that began in the ED (emergency, ed_to_inpatient, ed_to_observation) in the last 12 months by principal_icd10, every patient', R1)

    # x5-21 preventable hospital stays: stays = inpatient or observation visit groups, every patient
    stay = "(has_inpatient = 1 OR has_observation = 1)"
    pref = ("(principal_icd10 LIKE 'I50%' OR principal_icd10 LIKE 'J44%' OR principal_icd10 LIKE 'J45%' OR principal_icd10 LIKE 'E11.0%' "
            "OR principal_icd10 LIKE 'E11.1%' OR principal_icd10 LIKE 'E10.1%' OR principal_icd10 LIKE 'I10%')")
    acsc = one(f"SELECT COUNT(*) FROM visit_group WHERE {stay} AND {D.in_year('start_date')} AND {pref}")
    allip = one(f"SELECT COUNT(*) FROM visit_group WHERE {stay} AND {D.in_year('start_date')}")
    k.rubric('x5-21', [
        fact_n('n', f'{acsc} hospital (inpatient or observation) visits in 2029 had a principal diagnosis in those groups', acsc, abs_=0, near=[r'stays?|admissions?|visits?|preventable']),
        fact_n('share', f'That is {pct(acsc / allip)} of the {allip} hospital visits', acsc / allip, abs_=0.01, unit='proportion', near=[r'%|percent|share'])],
        'visit_group rows with has_inpatient = 1 OR has_observation = 1 starting in 2029 (every patient); principal_icd10 in I50, J44, J45, E11.0/E11.1, E10.1, I10', R1 + '; ' + R2)

    # x5-29 conditions among 2029 hospital admissions (inpatient + observation, every patient)
    tot = one(f"SELECT COUNT(*) FROM utilization_event WHERE {HOSP} AND {D.in_year('event_date')}")
    shares = []
    for name in ('diabetes', 'hypertension', 'heart_failure', 'ckd', 'copd', 'cad'):
        c = one(f"SELECT COUNT(*) FROM utilization_event u WHERE u.{HOSP} AND {D.in_year('u.event_date')} AND u.patient_id IN {k.cond_patients(name)}")
        shares.append((c / tot, name, c))
    shares.sort(reverse=True)
    k.rubric('x5-29', [
        fact_t('top', f'{shares[0][1]} patients account for the most admissions ({shares[0][2]} of {tot}, {pct(shares[0][0])})', [words[shares[0][1]]]),
        fact_n('top_share', f'{shares[0][1]}: {pct(shares[0][0])} of 2029 admissions', shares[0][0], abs_=0.02, unit='proportion', near=[words[shares[0][1]]]),
        fact_n('second_share', f'{shares[1][1]}: {pct(shares[1][0])} of 2029 admissions', shares[1][0], abs_=0.02, unit='proportion', near=[words[shares[1][1]]])],
        '2029 inpatient + observation events of every patient; share whose patient has each condition (overlapping)', R1 + '; ' + R2)
    return k


def write(rows, path=OUT):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))


def read(path=OUT):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    rows = build().rows
    if args.check:
        same = rows == read()
        print('dev3_keys_v2.jsonl', 'matches' if same else 'DIFFERS', f'({len(rows)} fields)')
        return 0 if same else 1
    write(rows)
    print(f'wrote {len(rows)} changed fields over {len({r["id"] for r in rows})} questions -> {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
