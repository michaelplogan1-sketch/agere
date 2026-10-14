-- Reading B on the reference cohort (problem-list hypertension only): latest 2029 reading >= 130 systolic OR >= 80 diastolic.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','vitals')),
htn AS MATERIALIZED (SELECT patient FROM r WHERE stream='conditions' AND (substr(json_extract(payload,'$.icd10'),1,3) IN ('I10','I11','I12','I13','I15','I16')
   OR ((lower(json_extract(payload,'$.description')) LIKE '%hypertension%' OR json_extract(payload,'$.description') LIKE '%HTN%') AND coalesce(json_extract(payload,'$.icd10'),'') NOT LIKE 'I27%'))),
b AS (SELECT patient, json_extract(payload,'$.blood_pressure_systolic') s, json_extract(payload,'$.blood_pressure_diastolic') d,
  row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date') DESC, json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn
  FROM r WHERE stream='vitals' AND json_extract(payload,'$.blood_pressure_systolic') IS NOT NULL AND json_extract(payload,'$.blood_pressure_diastolic') IS NOT NULL AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31')
SELECT COUNT(*) AS count FROM b WHERE rn=1 AND (s>=130 OR d>=80) AND patient IN (SELECT patient FROM htn);
