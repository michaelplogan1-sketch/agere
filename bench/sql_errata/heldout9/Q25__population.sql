-- Diabetes (population-wide) = problem list (conditions icd10 E10/E11/E13) OR an E10/E11/E13 code in the diagnosis_codes of any
-- payer_medical_claims row (the only diagnosis source for claims-only members). Prediabetes R73.x / family history Z83.3 never qualify.
-- HbA1c lab = lower(name) in hba1c / hemoglobin a1c (covers 'HbA1c', 'Hemoglobin A1c', population 'HEMOGLOBIN A1C') or LOINC 4548-4; dated
-- (event_date) numeric results only, so undated legacy 'Hemoglobin A1c/Hemoglobin.total' rows never enter a 2029 window.
-- An A1c test in 2029 is also evidenced by a payer claim line CPT 83036 with service_date in 2029 (voided claims excluded).
-- Returns diabetics with no 2029 A1c test.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs','payer_medical_claims','payer_medical_claim_lines')),
dm AS MATERIALIZED (SELECT patient FROM r WHERE stream='conditions' AND substr(json_extract(payload,'$.icd10'),1,3) IN ('E10','E11','E13')
  UNION SELECT r.patient FROM r, json_each(r.payload,'$.diagnosis_codes') j WHERE r.stream='payer_medical_claims' AND substr(j.value,1,3) IN ('E10','E11','E13')),
a AS (SELECT patient FROM r WHERE stream='labs' AND (lower(json_extract(payload,'$.name')) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total') OR json_extract(payload,'$.loinc')='4548-4') AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_type(payload,'$.value') IN ('integer','real')
  UNION SELECT patient FROM r WHERE stream='payer_medical_claim_lines' AND json_extract(payload,'$.procedure_code')='83036' AND json_extract(payload,'$.service_date') BETWEEN '2029-01-01' AND '2029-12-31'
    AND json_extract(payload,'$.payer_claim_id') NOT IN (SELECT json_extract(payload,'$.payer_claim_id') FROM r WHERE stream='payer_medical_claims' AND json_extract(payload,'$.claim_status')='voided'))
SELECT patient AS patient_id FROM dm WHERE patient NOT IN (SELECT patient FROM a) ORDER BY 1;
