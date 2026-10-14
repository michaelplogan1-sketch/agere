-- Hypertensive patients (conditions icd10 I10-I16 or description containing 'hypertension'/'HTN', excluding pulmonary hypertension I27.x) whose most recent 2029 blood pressure is controlled (<140 systolic AND <90 diastolic): vitals rows carrying both blood_pressure_systolic and blood_pressure_diastolic with event_date 2029-01-01..2029-12-31 (undated legacy bp_systolic/bp_diastolic measurement rows excluded); latest by event_date, event_time, record_id. Distinct patients.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','vitals')),
htn AS (SELECT DISTINCT patient FROM r WHERE stream='conditions' AND (substr(json_extract(payload,'$.icd10'),1,3) IN ('I10','I11','I12','I13','I15','I16')
   OR ((lower(json_extract(payload,'$.description')) LIKE '%hypertension%' OR json_extract(payload,'$.description') LIKE '%HTN%') AND coalesce(json_extract(payload,'$.icd10'),'') NOT LIKE 'I27%'))),
b AS (SELECT patient, json_extract(payload,'$.blood_pressure_systolic') s, json_extract(payload,'$.blood_pressure_diastolic') d,
  row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date') DESC, json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn
  FROM r WHERE stream='vitals' AND json_extract(payload,'$.blood_pressure_systolic') IS NOT NULL AND json_extract(payload,'$.blood_pressure_diastolic') IS NOT NULL
  AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31')
SELECT COUNT(*) AS count FROM b WHERE rn=1 AND s<140 AND d<90 AND patient IN (SELECT patient FROM htn);
