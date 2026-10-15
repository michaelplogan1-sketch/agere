-- Diabetic patients (conditions icd10 E10/E11/E13) with NO dated numeric HbA1c result in 2029 (A1c testing care gap): labs named 'HbA1c' or 'Hemoglobin A1c', event_date 2029-01-01..2029-12-31, numeric value. Returns patient_id.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs')),
dm AS (SELECT DISTINCT patient FROM r WHERE stream='conditions' AND substr(json_extract(payload,'$.icd10'),1,3) IN ('E10','E11','E13')),
a AS (SELECT DISTINCT patient FROM r WHERE stream='labs' AND json_extract(payload,'$.name') IN ('HbA1c','Hemoglobin A1c') AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_type(payload,'$.value') IN ('integer','real'))
SELECT patient AS patient_id FROM dm WHERE patient NOT IN (SELECT patient FROM a) ORDER BY 1;
