## Verified example queries (question -> SQL over this semantic layer; each was checked against an independent reference answer). Reuse their patterns; adapt the definitions to what each new question asks.
### Q: How many patients are in the original warehouse roster?
answer_type: count
SQL:
SELECT COUNT(*) AS count FROM patient;
### Q: How many patients had reported age at least 65 at their profile capture, without advancing that age to today?
answer_type: count
note: age_reported is the explicitly requested captured age. Analytical age uses age_at(birth_date, as_of).
SQL:
SELECT COUNT(*) AS count FROM patient WHERE age_reported >= 65;
### Q: How many distinct patients have a recorded diabetes diagnosis, including diabetic complications, across ICD-10 and SNOMED? Exclude prediabetes and family history.
answer_type: count
note: Literal codes on conditions records. concept_patient.concept = 'diabetes' is the broader library phenotype (all diagnosis streams and labels); use it when the question asks for the governed diabetes definition.
SQL:
SELECT COUNT(DISTINCT patient_id) AS count FROM condition
WHERE source_table = 'conditions'
  AND (substr(icd10, 1, 3) IN ('E08','E09','E10','E11','E13')
       OR snomed IN ('44054006','127013003','90781000119102','368581000119106','157141000119108'));
### Q: Which patients have a recorded diabetes diagnosis and any observed HbA1c over 9 percent? Return the complete patient ID set; do not restrict to the latest result.
answer_type: patient_set
SQL:
WITH diabetes AS (
  SELECT DISTINCT patient_id FROM condition WHERE source_table = 'conditions'
    AND (substr(icd10, 1, 3) IN ('E08','E09','E10','E11','E13')
         OR snomed IN ('44054006','127013003','90781000119102','368581000119106','157141000119108'))
), a1c AS (
  SELECT DISTINCT patient_id FROM measurement
  WHERE source_table = 'labs' AND lower(name) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total')
    AND unit = '%' AND value > 9
)
SELECT patient_id FROM diabetes JOIN a1c USING (patient_id) ORDER BY patient_id;
### Q: Which patients have a latest source-event-dated HbA1c above 9 percent? Exclude undated results; on the same date use the latest arrival, then largest record ID.
answer_type: patient_set
note: measurement has one row per (record, concept): select DISTINCT records before ranking. available_date is the arrival date.
SQL:
WITH a1c AS (
  SELECT DISTINCT record_id, patient_id, event_date, available_date, value FROM measurement
  WHERE source_table = 'labs' AND lower(name) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total')
    AND unit = '%' AND value IS NOT NULL AND event_date IS NOT NULL AND event_date <= :as_of
), ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY event_date DESC, available_date DESC, record_id DESC) AS rn FROM a1c
)
SELECT patient_id FROM ranked WHERE rn = 1 AND value > 9 ORDER BY patient_id;
### Q: How many patients have any numeric lab named eGFR below 30 mL/min/1.73m2?
answer_type: count
SQL:
SELECT COUNT(DISTINCT patient_id) AS count FROM measurement
WHERE source_table = 'labs' AND name = 'eGFR' AND unit IN ('mL/min/1.73m2','mL/min/1.73m²') AND value < 30;
### Q: How many ADT records explicitly say admission? Count notification records, not unique stays.
answer_type: count
note: Literal record counts come from record (one row per received record); fields not modelled elsewhere are read with json_extract(payload, ...).
SQL:
SELECT COUNT(*) AS count FROM record WHERE stream = 'adt' AND json_extract(payload, '$.event') = 'admission';
### Q: Count 2028 source-event-dated encounter records by organization_id. Return a table with organization_id and encounter_count, excluding missing organizations.
answer_type: table
SQL:
SELECT organization_id, COUNT(*) AS encounter_count FROM record
WHERE stream = 'encounters' AND event_date BETWEEN '2028-01-01' AND '2028-12-31' AND organization_id IS NOT NULL
GROUP BY organization_id ORDER BY organization_id;
### Q: How many encounter records explicitly have status completed?
answer_type: count
note: Records, not visits: an encounter arrives once per status version. Use visit (one row per encounter_id) to count visits.
SQL:
SELECT COUNT(*) AS count FROM record WHERE stream = 'encounters' AND json_extract(payload, '$.status') = 'completed';
### Q: How many appointment records explicitly have status no_show?
answer_type: count
note: Appointment RECORDS; the appointment table is one row per appointment_id with outcome/ever_no_show over its versions.
SQL:
SELECT COUNT(*) AS count FROM record WHERE stream = 'appointments' AND json_extract(payload, '$.status') = 'no_show';
### Q: What is the gross sum in USD of patient_payments records marked received with source event dates in 2028? Do not subtract refunds or infer net balances.
answer_type: value
SQL:
SELECT COALESCE(SUM(json_extract(payload, '$.amount_cents')), 0) / 100.0 AS value FROM record
WHERE stream = 'patient_payments' AND event_date BETWEEN '2028-01-01' AND '2028-12-31'
  AND json_extract(payload, '$.status') = 'received' AND json_extract(payload, '$.currency') = 'USD'
  AND json_type(payload, '$.amount_cents') = 'integer';
### Q: How many patients have an observed SNOMED diabetes or diabetic-complication diagnosis but no observed ICD-10 diabetes diagnosis? Exclude prediabetes.
answer_type: count
SQL:
SELECT COUNT(DISTINCT patient_id) AS count FROM condition
WHERE source_table = 'conditions' AND snomed IN ('44054006','127013003','90781000119102','368581000119106','157141000119108')
  AND patient_id NOT IN (SELECT patient_id FROM condition WHERE source_table = 'conditions' AND substr(icd10, 1, 3) IN ('E08','E09','E10','E11','E13'));
### Q: How many distinct patients have at least two recorded numeric HbA1c results above 9 percent? Count each patient once, even if the values repeat.
answer_type: count
SQL:
SELECT COUNT(*) AS count FROM (
  SELECT patient_id FROM (SELECT DISTINCT record_id, patient_id FROM measurement
    WHERE source_table = 'labs' AND lower(name) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total') AND unit = '%' AND value > 9)
  GROUP BY patient_id HAVING COUNT(*) >= 2);
### Q: How many patients have recorded diabetes and a no_show appointment with scheduled_date in 2028?
answer_type: count
SQL:
SELECT COUNT(DISTINCT r.patient_id) AS count FROM record r
WHERE r.stream = 'appointments' AND json_extract(r.payload, '$.status') = 'no_show'
  AND json_extract(r.payload, '$.scheduled_date') BETWEEN '2028-01-01' AND '2028-12-31'
  AND r.patient_id IN (SELECT patient_id FROM condition WHERE source_table = 'conditions'
    AND (substr(icd10, 1, 3) IN ('E08','E09','E10','E11','E13')
         OR snomed IN ('44054006','127013003','90781000119102','368581000119106','157141000119108')));
### Q: Which patients have both reports_taking and reports_not_taking in reported_medication_entries for the exact same medication_name_reported, ignoring case?
answer_type: patient_set
SQL:
WITH reports AS (
  SELECT patient_id, lower(json_extract(payload, '$.medication_name_reported')) AS drug, json_extract(payload, '$.use_statement') AS use_state
  FROM record WHERE stream = 'reported_medication_entries'
)
SELECT DISTINCT a.patient_id FROM reports a JOIN reports b ON a.patient_id = b.patient_id AND a.drug = b.drug
WHERE a.use_state = 'reports_taking' AND b.use_state = 'reports_not_taking' ORDER BY a.patient_id;
### Q: How many medication order records lack any pharmacy_dispenses record linked by medication_order_record_id to that order record ID? Count order records.
answer_type: count
note: link holds every *_record_id reference (indexed on target_record_id).
SQL:
SELECT COUNT(*) AS count FROM record o WHERE o.stream = 'medication_orders'
  AND NOT EXISTS (SELECT 1 FROM link l WHERE l.target_record_id = o.record_id
                  AND l.stream = 'pharmacy_dispenses' AND l.field = 'medication_order_record_id');
### Q: For numeric percent-unit HbA1c records, count distinct patients by the recording organization. Return a table with organization_id and patient_count; exclude missing organizations.
answer_type: table
SQL:
SELECT organization_id, COUNT(DISTINCT patient_id) AS patient_count FROM measurement
WHERE source_table = 'labs' AND lower(name) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total')
  AND unit = '%' AND value IS NOT NULL AND organization_id IS NOT NULL
GROUP BY organization_id ORDER BY organization_id;
### Q: Exactly how many admissions were causally prevented by transportation assistance in this cohort? Provide the identified causal effect, not an association.
answer_type: unsupported
note: No SQL can answer this: return status and answer_type unsupported and name the missing prerequisite.
answer:
{"status":"unsupported","answer_type":"unsupported","count":null,"patient_ids":null,"value":null,"table":null,"unknown_count":0,"missing":["An identified causal design or counterfactual comparison is missing; observational associations cannot establish a causal effect."],"grounding":[]}