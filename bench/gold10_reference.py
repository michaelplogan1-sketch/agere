"""Agere's independent derivation of the Gold 10 questions (docs/agere/gold10/QUESTIONS.md).

    .venv/bin/python bench/gold10_reference.py [--workspace pilot-native-v2-12] [--out docs/agere/gold10/agere_reference.json]

One readable query per question over the workspace's semantic layer. Output per question: patient count, a sha256
fingerprint of the sorted customer identifiers, and the identifiers themselves (written to the output file only; the
generator receives counts and fingerprints). A question is locked when this derivation and the generator's agree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# drug-name terms per class (published names; a name matches when it contains the term)
TERMS = {
    'hf_beta_blocker': ['carvedilol', 'metoprolol succinate', 'bisoprolol'],
    'ras': ['lisinopril', 'enalapril', 'ramipril', 'benazepril', 'captopril', 'fosinopril', 'quinapril', 'perindopril',
            'trandolapril', 'moexipril', 'losartan', 'valsartan', 'irbesartan', 'candesartan', 'olmesartan', 'telmisartan',
            'azilsartan', 'eprosartan', 'sacubitril'],
    'mra': ['spironolactone', 'eplerenone', 'finerenone'],
    'sglt2': ['dapagliflozin', 'empagliflozin', 'canagliflozin', 'ertugliflozin', 'sotagliflozin', 'bexagliflozin'],
    'statin': ['atorvastatin', 'rosuvastatin', 'simvastatin', 'pravastatin', 'lovastatin', 'fluvastatin', 'pitavastatin'],
}
PT_CODES = ('97161', '97162', '97163', '97164', '97110', '97112', '97140', '97530')
MAMMO_CODES = ('77065', '77066', '77067', 'G0202', 'G0204', 'G0206')
VISIT_TYPES = ('office', 'virtual')


def _like_any(col, terms):
    return '(' + ' OR '.join(f"lower({col}) LIKE '%{t}%'" for t in terms) + ')'


def queries(as_of):
    a = f"'{as_of}'"
    dead = f"""SELECT patient_id FROM record WHERE stream = 'death_records'
               AND json_extract(payload, '$.date_of_death') <= {a}"""
    hf = """SELECT patient_id FROM condition WHERE icd10 LIKE 'I50%'
            UNION SELECT patient_id FROM payer_claim
            WHERE replace(principal_diagnosis, '.', '') LIKE 'I50%' OR replace(diagnosis_codes, '.', '') LIKE '%I50%'"""
    diabetes = """SELECT patient_id FROM condition WHERE icd10 LIKE 'E10%' OR icd10 LIKE 'E11%'
                  UNION SELECT patient_id FROM payer_claim WHERE replace(principal_diagnosis, '.', '') GLOB 'E1[01]*'
                     OR replace(diagnosis_codes, '.', '') GLOB '*E1[01]*'"""

    def on_class(cls):
        return f"""SELECT patient_id FROM medication_list_entry WHERE list_status = 'active' AND {_like_any('name', TERMS[cls])}
                   UNION SELECT patient_id FROM fill WHERE fill_date <= {a} AND supply_end >= date({a}, '-120 day')
                     AND {_like_any("coalesce(drug_name, '') || ' ' || coalesce(rxnorm_ingredient, '')", TERMS[cls])}"""

    ed_claim = """SELECT c.patient_id, c.service_from AS d, c.principal_diagnosis AS pdx, c.diagnosis_codes AS dx
                  FROM payer_claim c WHERE c.counts_in_metrics = 1 AND (c.place_of_service = '23'
                    OR c.payer_claim_id IN (SELECT payer_claim_id FROM payer_claim_line
                                            WHERE revenue_code LIKE '045%' OR cpt BETWEEN '99281' AND '99285'))"""
    inpatient = """SELECT patient_id, admission_date AS d FROM payer_claim
                   WHERE counts_in_metrics = 1 AND substr(type_of_bill, 1, 3) IN ('011', '012') AND admission_date IS NOT NULL
                   UNION SELECT patient_id, admit_date FROM adt_visit WHERE coalesce(final_patient_class, patient_class) = 'I'"""
    procedures = "SELECT patient_id, service_date, cpt FROM payer_claim_line UNION ALL SELECT patient_id, service_date, cpt FROM claim_line"
    visits = f"SELECT patient_id, event_date AS d FROM visit WHERE type IN {VISIT_TYPES}"
    return {
        'HF-GDMT': f"""
            SELECT DISTINCT h.patient_id FROM ({hf}) h WHERE h.patient_id NOT IN ({dead})
              AND (h.patient_id NOT IN ({on_class('hf_beta_blocker')}) OR h.patient_id NOT IN ({on_class('ras')})
                   OR h.patient_id NOT IN ({on_class('mra')}) OR h.patient_id NOT IN ({on_class('sglt2')}))""",
        'LBP-PT': f"""
            SELECT DISTINCT e.patient_id FROM ({ed_claim}) e
            WHERE e.d > date({a}, '-365 day') AND e.d <= date({a}, '-60 day')
              AND (replace(e.pdx, '.', '') LIKE 'M545%' OR replace(e.dx, '.', '') LIKE '%M545%')
              AND NOT EXISTS (SELECT 1 FROM ({inpatient}) i WHERE i.patient_id = e.patient_id
                              AND i.d BETWEEN e.d AND date(e.d, '+1 day'))
              AND NOT EXISTS (SELECT 1 FROM ({procedures}) p WHERE p.patient_id = e.patient_id AND p.cpt IN {PT_CODES}
                              AND p.service_date > e.d AND p.service_date <= date(e.d, '+60 day'))""",
        'A1C-9': f"""
            WITH latest AS (SELECT patient_id, event_date, value_canonical,
                              row_number() OVER (PARTITION BY patient_id ORDER BY event_date DESC, record_id DESC) rn
                            FROM measurement WHERE concept = 'hba1c' AND value_canonical IS NOT NULL
                              AND coalesce(result_status, '') <> 'cancelled' AND coalesce(result_current, 1) = 1
                              AND event_date > date({a}, '-365 day') AND event_date <= {a})
            SELECT l.patient_id FROM latest l WHERE l.rn = 1 AND l.value_canonical > 9.0
              AND l.event_date <= date({a}, '-90 day') AND l.patient_id IN ({diabetes})
              AND NOT EXISTS (SELECT 1 FROM ({visits}) v WHERE v.patient_id = l.patient_id
                              AND v.d > l.event_date AND v.d <= date(l.event_date, '+90 day'))""",
        'ED-HIGH': f"""
            SELECT patient_id FROM (
              SELECT patient_id, d FROM ({ed_claim}) UNION SELECT patient_id, event_date FROM encounter WHERE type LIKE 'emergency%')
            WHERE d > date({a}, '-365 day') AND d <= {a}
            GROUP BY patient_id HAVING count(DISTINCT d) >= 4""",
        'TCM-GAP': f"""
            WITH dis AS (SELECT patient_id, discharge_date AS d FROM payer_claim WHERE counts_in_metrics = 1
                           AND substr(type_of_bill, 1, 3) IN ('011', '012') AND discharge_status IN ('01', '06')
                         UNION SELECT patient_id, discharge_date FROM adt_visit
                           WHERE coalesce(final_patient_class, patient_class) = 'I' AND discharge_disposition IN ('home', 'home_health'))
            SELECT DISTINCT x.patient_id FROM dis x WHERE x.d > date({a}, '-6 month') AND x.d <= date({a}, '-14 day')
              AND NOT EXISTS (SELECT 1 FROM ({visits}) v WHERE v.patient_id = x.patient_id
                              AND v.d > x.d AND v.d <= date(x.d, '+14 day'))""",
        'SPD': f"""
            SELECT p.patient_id FROM patient p WHERE p.patient_id IN ({diabetes}) AND p.patient_id NOT IN ({dead})
              AND p.birth_date IS NOT NULL
              AND CAST((julianday({a}) - julianday(p.birth_date)) / 365.25 AS INTEGER) BETWEEN 40 AND 75
              AND p.patient_id NOT IN ({on_class('statin')})""",
        'PHQ-FU': f"""
            SELECT DISTINCT m.patient_id FROM measurement m WHERE m.concept = 'phq9' AND m.value_canonical >= 10
              AND coalesce(m.result_status, '') <> 'cancelled'
              AND m.event_date > date({a}, '-365 day') AND m.event_date <= date({a}, '-30 day')
              AND NOT EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = m.patient_id
                              AND (v.type IN {VISIT_TYPES} OR v.setting LIKE '%behavioral%')
                              AND v.event_date > m.event_date AND v.event_date <= date(m.event_date, '+30 day'))""",
        'BCS': f"""
            SELECT p.patient_id FROM patient p WHERE p.sex_reported = 'F' AND p.patient_id NOT IN ({dead})
              AND CAST((julianday({a}) - julianday(p.birth_date)) / 365.25 AS INTEGER) BETWEEN 52 AND 74
              AND p.patient_id IN (SELECT patient_id FROM enrollment_span WHERE coverage_start <= {a}
                                   AND (coverage_end IS NULL OR coverage_end >= {a})
                                   AND coalesce(product, '') NOT IN ('medigap', 'cob_secondary'))
              AND p.patient_id NOT IN (SELECT patient_id FROM cancer_screening WHERE test LIKE '%mammograph%'
                                         AND event_date > date({a}, '-27 month') AND event_date <= {a}
                                       UNION SELECT patient_id FROM ({procedures}) WHERE cpt IN {MAMMO_CODES}
                                         AND service_date > date({a}, '-27 month') AND service_date <= {a})""",
        'PDC': f"""
            WITH y AS (SELECT strftime('%Y', {a}) - 1 AS yr),
                 lo AS (SELECT (SELECT yr FROM y) || '-01-01' AS s, (SELECT yr FROM y) || '-12-31' AS e),
                 f AS (SELECT patient_id, fill_date, date(fill_date, '+' || (days_supply - 1) || ' day') AS last_day FROM fill
                       WHERE days_supply > 0 AND {_like_any("coalesce(drug_name, '') || ' ' || coalesce(rxnorm_ingredient, '')", TERMS['statin'])}),
                 idx AS (SELECT patient_id, min(fill_date) AS start FROM f, lo WHERE fill_date BETWEEN lo.s AND lo.e
                         GROUP BY patient_id HAVING count(*) >= 2),
                 days AS (SELECT idx.patient_id, idx.start, count(DISTINCT c.d) AS covered FROM idx
                          JOIN (WITH RECURSIVE cal(d) AS (SELECT (SELECT s FROM lo) UNION ALL SELECT date(d, '+1 day') FROM cal
                                                         WHERE d < (SELECT e FROM lo)) SELECT d FROM cal) c ON c.d >= idx.start
                          JOIN f ON f.patient_id = idx.patient_id AND c.d BETWEEN f.fill_date AND f.last_day
                          GROUP BY idx.patient_id)
            SELECT idx.patient_id FROM idx LEFT JOIN days USING (patient_id), lo
            WHERE coalesce(days.covered, 0) * 1.0 / (julianday(lo.e) - julianday(idx.start) + 1) < 0.80""",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workspace', default='pilot-native-v2-12')
    ap.add_argument('--out', type=Path, default=Path('docs/agere/gold10/agere_reference.json'))
    a = ap.parse_args()
    from clinical_core.agere.onboarding.workspace import Workspace, native_id
    ws = Workspace(a.workspace)
    as_of = ws.summary()['as_of']
    db = sqlite3.connect(f"file:{ws.semantic_path}?mode=ro", uri=True)
    out = {'workspace': a.workspace, 'as_of': as_of, 'questions': {}}
    for qid, sql in queries(as_of).items():
        pids = sorted({r[0] for r in db.execute(sql) if r[0]})
        keys = ws.customer_ids(pids)
        ids = sorted({native_id(keys.get(p)) or p for p in pids})
        fp = hashlib.sha256('\n'.join(ids).encode()).hexdigest()[:16]
        out['questions'][qid] = {'count': len(ids), 'fingerprint': fp, 'ids': ids}
        print(f'{qid:8s} {len(ids):6d}  {fp}')
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
