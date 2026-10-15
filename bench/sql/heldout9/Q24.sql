-- Diabetic patients (conditions icd10 E10/E11/E13) with a dated numeric urine albumin-creatinine ratio in 2029: labs named 'Urine albumin/creatinine ratio' or 'Microalbumin/Creatinine' with event_date 2029-01-01..2029-12-31 and numeric value (undated legacy rows excluded). Distinct patients.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs'))
SELECT COUNT(DISTINCT patient) AS count FROM r WHERE stream='labs' AND json_extract(payload,'$.name') IN ('Urine albumin/creatinine ratio','Microalbumin/Creatinine')
  AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_type(payload,'$.value') IN ('integer','real')
  AND patient IN (SELECT patient FROM r WHERE stream='conditions' AND substr(json_extract(payload,'$.icd10'),1,3) IN ('E10','E11','E13'));
