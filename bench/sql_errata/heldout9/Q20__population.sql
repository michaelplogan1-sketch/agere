-- Diabetes (population-wide) = problem list (conditions icd10 E10/E11/E13) OR an E10/E11/E13 code in the diagnosis_codes of any
-- payer_medical_claims row (the only diagnosis source for claims-only members). Prediabetes R73.x / family history Z83.3 never qualify.
-- HbA1c lab = lower(name) in hba1c / hemoglobin a1c (covers 'HbA1c', 'Hemoglobin A1c', population 'HEMOGLOBIN A1C') or LOINC 4548-4; dated
-- (event_date) numeric results only, so undated legacy 'Hemoglobin A1c/Hemoglobin.total' rows never enter a 2029 window.
-- Diabetics whose latest dated numeric 2029 A1c (event_date, event_time, record_id) is > 9.0. Distinct patients.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs','payer_medical_claims')),
dm AS MATERIALIZED (SELECT patient FROM r WHERE stream='conditions' AND substr(json_extract(payload,'$.icd10'),1,3) IN ('E10','E11','E13')
  UNION SELECT r.patient FROM r, json_each(r.payload,'$.diagnosis_codes') j WHERE r.stream='payer_medical_claims' AND substr(j.value,1,3) IN ('E10','E11','E13')),
a AS (SELECT patient, json_extract(payload,'$.value') v, row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date') DESC, json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn
  FROM r WHERE stream='labs' AND (lower(json_extract(payload,'$.name')) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total') OR json_extract(payload,'$.loinc')='4548-4') AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_type(payload,'$.value') IN ('integer','real'))
SELECT COUNT(*) AS count FROM a WHERE rn=1 AND v>9 AND patient IN (SELECT patient FROM dm);
