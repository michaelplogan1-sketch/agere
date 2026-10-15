-- AMBIGUOUS (reading A): hypertensive patients (conditions I10-I16 or hypertension/HTN description, pulmonary hypertension I27 excluded) whose latest dated 2029 BP (vitals rows with blood_pressure_systolic+diastolic, latest by event_date, event_time, record_id; undated legacy rows excluded) is UNCONTROLLED at the >=140 systolic OR >=90 diastolic threshold. Reading B uses >=130/80.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','vitals')),
htn AS (SELECT DISTINCT patient FROM r WHERE stream='conditions' AND (substr(json_extract(payload,'$.icd10'),1,3) IN ('I10','I11','I12','I13','I15','I16')
   OR ((lower(json_extract(payload,'$.description')) LIKE '%hypertension%' OR json_extract(payload,'$.description') LIKE '%HTN%') AND coalesce(json_extract(payload,'$.icd10'),'') NOT LIKE 'I27%'))),
b AS (SELECT patient, json_extract(payload,'$.blood_pressure_systolic') s, json_extract(payload,'$.blood_pressure_diastolic') d,
  row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date') DESC, json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn
  FROM r WHERE stream='vitals' AND json_extract(payload,'$.blood_pressure_systolic') IS NOT NULL AND json_extract(payload,'$.blood_pressure_diastolic') IS NOT NULL
  AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31')
SELECT COUNT(*) AS count FROM b WHERE rn=1 AND (s>=140 OR d>=90) AND patient IN (SELECT patient FROM htn);
