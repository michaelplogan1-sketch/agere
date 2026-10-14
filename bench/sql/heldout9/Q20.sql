-- Diabetic patients (conditions icd10 starting E10/E11/E13; prediabetes R73.x and family history Z83.3 excluded) whose most recent dated numeric HbA1c in 2029 is > 9.0%: labs named 'HbA1c' or 'Hemoglobin A1c' (unit %), event_date 2029-01-01..2029-12-31, numeric value; latest by event_date, event_time, record_id. Patients with no 2029 A1c are not counted. Distinct patients.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs')),
dm AS (SELECT DISTINCT patient FROM r WHERE stream='conditions' AND substr(json_extract(payload,'$.icd10'),1,3) IN ('E10','E11','E13')),
a AS (SELECT patient, json_extract(payload,'$.value') v, row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date') DESC, json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn
  FROM r WHERE stream='labs' AND json_extract(payload,'$.name') IN ('HbA1c','Hemoglobin A1c') AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_type(payload,'$.value') IN ('integer','real'))
SELECT COUNT(*) AS count FROM a WHERE rn=1 AND v>9 AND patient IN (SELECT patient FROM dm);
