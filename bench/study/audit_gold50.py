"""Read-only source breakdowns for the five pending Gold-50 disputes; never score a key.

These diagnostics do not validate an answer. They expose definition sensitivities
and the evidence the independent generator audit requested. No product imports,
provider calls, generator files, or saved model SQL are used.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[2]

ALIVE = """NOT EXISTS (SELECT 1 FROM record r WHERE r.patient_id=p.patient_id
    AND r.stream='death_records' AND coalesce(json_extract(r.payload,'$.death_date'),r.event_date)<='2030-05-27')"""
PRIMARY = "coalesce(product,'') NOT IN ('medigap','cob_secondary')"
CLAIMS = """SELECT * FROM payer_claim WHERE type_of_bill GLOB '01[12]*'
    AND final_status!='voided' AND coalesce(version_status,'')!='superseded_by_replacement'"""

QUERIES = {
    'QUA-G3.claim_denominators': f"""SELECT c.payer_id,p.data_segment,c.type_of_bill,c.final_status,
        count(*) claim_rows,
        count(DISTINCT c.patient_id||'|'||c.admission_date) explicit_admission_stays,
        count(DISTINCT c.patient_id||'|'||coalesce(c.admission_date,c.service_from)) service_from_fallback_stays,
        sum(c.admission_date IS NULL) missing_admission_dates
        FROM ({CLAIMS}) c JOIN patient p USING(patient_id)
        WHERE c.principal_diagnosis LIKE 'I50%'
        AND coalesce(c.discharge_date,c.service_to) BETWEEN '2029-01-01' AND date('2030-05-27','-7 days')
        AND coalesce(c.discharge_status,'') NOT IN ('20','expired') GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""",
    'QUA-G3.raw_837_missing_admission': """SELECT _instance payer_id,
        count(DISTINCT patient_control_number) claims,
        count(DISTINCT CASE WHEN admission_date IS NULL THEN patient_control_number END) missing_admission_dates
        FROM raw_x12_837_claim WHERE facility_code IN ('11','12') AND frequency_code!='8'
        AND principal_diagnosis LIKE 'I50%'
        AND coalesce(discharge_date,statement_to) BETWEEN '2029-01-01' AND '2030-05-20'
        AND coalesce(patient_status,'') NOT IN ('20','expired') GROUP BY 1 ORDER BY 1""",
    'HOS-G2.raw_adt_matching': f"""WITH admissions AS (
        SELECT DISTINCT m.person_id, h.sending_facility,substr(h.message_datetime,1,10) admission_date
        FROM raw_hl7_message h LEFT JOIN raw_stg_person_map m
        ON m.source_key='hl7:'||h.sending_facility||':'||h.patient_id
        WHERE h.trigger_event='A01' AND h.patient_class='I' AND substr(h.message_datetime,1,4)='2029'),
        matches AS (SELECT a.*,
        EXISTS(SELECT 1 FROM ({CLAIMS}) c WHERE c.patient_id=a.person_id
               AND abs(julianday(c.admission_date)-julianday(a.admission_date))<=1) explicit_match,
        EXISTS(SELECT 1 FROM ({CLAIMS}) c WHERE c.patient_id=a.person_id
               AND abs(julianday(coalesce(c.admission_date,c.service_from))-julianday(a.admission_date))<=1) fallback_match
        FROM admissions a)
        SELECT sending_facility,count(*) admissions,sum(person_id IS NULL) unresolved_identity,
        sum(NOT explicit_match) without_explicit_match,sum(NOT fallback_match) without_service_from_fallback_match
        FROM matches GROUP BY 1 ORDER BY 1""",
    'VBC-G1.attributed_denominators': f"""WITH members AS (
        SELECT DISTINCT e.attribution_practice_id,p.patient_id,p.data_segment
        FROM enrollment_span e JOIN patient p USING(patient_id)
        WHERE {PRIMARY} AND e.coverage_start<='2030-05-27'
        AND (e.coverage_end IS NULL OR e.coverage_end>='2030-05-27')
        AND coalesce(e.attribution_practice_id,'')!='' AND {ALIVE})
        SELECT attribution_practice_id,count(DISTINCT patient_id) members FROM members
        GROUP BY 1 ORDER BY members DESC,attribution_practice_id LIMIT 5""",
    'VBC-G1.visit_source_overlap': f"""WITH members AS (
        SELECT DISTINCT e.attribution_practice_id,p.patient_id
        FROM enrollment_span e JOIN patient p USING(patient_id)
        WHERE {PRIMARY} AND e.coverage_start<='2030-05-27'
        AND (e.coverage_end IS NULL OR e.coverage_end>='2030-05-27')
        AND coalesce(e.attribution_practice_id,'')!='' AND {ALIVE}),
        top AS (SELECT attribution_practice_id FROM members GROUP BY 1 ORDER BY count(*) DESC,1 LIMIT 5),
        flags AS (SELECT m.*,EXISTS(SELECT 1 FROM payer_claim_line l JOIN payer_claim c USING(payer_claim_id)
            WHERE l.patient_id=m.patient_id AND l.cpt BETWEEN '99202' AND '99215'
            AND c.final_status!='voided' AND l.service_date BETWEEN date('2030-05-27','-365 days') AND '2030-05-27') claim_em,
            EXISTS(SELECT 1 FROM visit v WHERE v.patient_id=m.patient_id
            AND v.status IN ('completed','finished') AND v.type='office'
            AND v.event_date BETWEEN date('2030-05-27','-365 days') AND '2030-05-27') ehr_office
            FROM members m JOIN top USING(attribution_practice_id))
        SELECT attribution_practice_id,claim_em,ehr_office,count(*) members FROM flags GROUP BY 1,2,3 ORDER BY 1,2,3""",
    'VBC-G3.cohort_size': f"""WITH months AS (
        SELECT patient_id,substr(month,1,4) year,count(DISTINCT month) n FROM member_month
        WHERE {PRIMARY} AND month BETWEEN '2028-01' AND '2029-12-31' GROUP BY 1,2),
        eligible AS (SELECT patient_id FROM months WHERE n>=6 GROUP BY 1 HAVING count(DISTINCT year)=2)
        SELECT count(*) members,(count(*)+9)/10 decile_size FROM eligible""",
    'VBC-G3.allowed_and_cob': """SELECT product,substr(service_from,1,4) year,
        count(*) claims,sum(allowed_cents) modeled_allowed_cents,
        sum(allowed_cents=0) zero_allowed_claims,
        sum(allowed_cents=0 AND coalesce(drg,'')!='') zero_allowed_drg_claims
        FROM payer_claim WHERE final_status='paid' AND counts_in_metrics=1
        AND service_from BETWEEN '2028-01-01' AND '2029-12-31' GROUP BY 1,2 ORDER BY 1,2""",
    'VBC-G3.raw_835_drg_amounts': """SELECT c.payer_id,count(DISTINCT c.payer_claim_id) zero_allowed_drg_claims,
        count(DISTINCT CASE WHEN r.payer_claim_control_number IS NOT NULL THEN c.payer_claim_id END) matched_835_claims,
        count(DISTINCT CASE WHEN cast(r.paid_amount AS REAL)+coalesce(cast(r.cas_pr_total AS REAL),0)>0
              THEN c.payer_claim_id END) with_positive_835_paid_plus_pr
        FROM payer_claim c LEFT JOIN raw_x12_835_claim r ON r.payer_claim_control_number=c.payer_claim_id
        AND coalesce(r.is_reversal,0)=0
        WHERE c.final_status='paid' AND c.counts_in_metrics=1
        AND c.allowed_cents=0 AND coalesce(c.drg,'')!=''
        AND c.service_from BETWEEN '2028-01-01' AND '2029-12-31'
        GROUP BY 1 ORDER BY 1""",
    'VBC-G3.raw_835_patient_control': """SELECT c.payer_id,
        count(DISTINCT c.payer_claim_id) zero_allowed_drg_claims,
        count(DISTINCT CASE WHEN r.patient_control_number IS NOT NULL THEN c.payer_claim_id END) matched_835_claims,
        count(DISTINCT CASE WHEN cast(r.paid_amount AS REAL)+coalesce(cast(r.cas_pr_total AS REAL),0)>0
              THEN c.payer_claim_id END) with_positive_835_paid_plus_pr,
        count(DISTINCT CASE WHEN r.statement_from=c.service_from
              AND round(cast(r.total_charge AS REAL)*100)=c.charge_cents THEN c.payer_claim_id END) matching_date_and_charge,
        count(DISTINCT r._file||'|'||r.segment_index) matched_source_records
        FROM payer_claim c LEFT JOIN raw_x12_835_claim r ON r.patient_control_number=c.payer_claim_id
        AND r._file LIKE 'x12_payer/'||c.payer_id||'/%' AND coalesce(r.is_reversal,0)=0
        WHERE c.final_status='paid' AND c.counts_in_metrics=1
        AND c.allowed_cents=0 AND coalesce(c.drg,'')!=''
        AND c.service_from BETWEEN '2028-01-01' AND '2029-12-31'
        GROUP BY 1 ORDER BY 1""",
    'QUA-G1.a1c_evidence_sources': f"""WITH diagnoses AS (
        SELECT patient_id,icd10 FROM condition UNION SELECT patient_id,icd10 FROM encounter_diagnosis
        UNION SELECT patient_id,icd10 FROM payer_claim_diagnosis),
        diabetics AS (SELECT DISTINCT patient_id FROM diagnoses
        WHERE replace(icd10,'.','') LIKE 'E10%' OR replace(icd10,'.','') LIKE 'E11%'),
        eligible AS (SELECT p.patient_id,p.data_segment FROM patient p JOIN diabetics USING(patient_id)
        WHERE cast(strftime('%Y','2030-05-27') AS INT)-cast(strftime('%Y',p.birth_date) AS INT)
              -(strftime('%m-%d','2030-05-27')<strftime('%m-%d',p.birth_date)) BETWEEN 18 AND 75 AND {ALIVE}),
        labs AS (SELECT m.patient_id,m.record_id,coalesce(json_extract(r.payload,'$.source_system'),m.source_table) source
        FROM measurement m LEFT JOIN record r USING(record_id)
        WHERE (m.loinc IN ('4548-4','17856-6') OR lower(m.name) LIKE '%a1c%')
        AND m.event_date BETWEEN date('2030-05-27','-365 days') AND '2030-05-27')
        SELECT e.data_segment,coalesce(l.source,'NO_A1C') evidence_source,count(DISTINCT e.patient_id) persons
        FROM eligible e LEFT JOIN labs l USING(patient_id) GROUP BY 1,2 ORDER BY 1,2""",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT / 'artifacts/workspaces/pilot-native-v2-12/semantic.sqlite3')
    parser.add_argument('--out', type=Path, default=ROOT / 'artifacts/study/reference-audit-2026-10-19/source_breakdowns.json')
    args = parser.parse_args()
    output = {'status': 'diagnostic_only_not_reference_validation', 'created_at': datetime.now(timezone.utc).isoformat(),
              'database': str(args.database), 'notes': [
                  'No scorer or key read; no results are marked correct or incorrect.',
                  'Semantic outputs require independent raw-source reconciliation; raw_ tables are delivered parsed records.',
                  'Service-from substitution for missing admission dates is shown as a sensitivity, not an accepted rule.',
                  'Visit-source overlap is limited to the named modeled EHR types and payer E/M lines; raw provider claims still require audit.',
                  '835 diagnostic counts existence of a non-reversal remittance; it does not choose the latest adjudication or reprice claims.'],
              'breakdowns': {}}
    with sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        output['source_meta'] = dict(db.execute('SELECT * FROM meta'))
        for name, sql in QUERIES.items():
            output['breakdowns'][name] = {'sql': sql, 'rows': [dict(r) for r in db.execute(sql)]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({'output': str(args.out), 'breakdowns': len(QUERIES), 'status': output['status']}))


if __name__ == '__main__':
    main()
