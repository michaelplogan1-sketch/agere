-- Hypertension dx (ICD-10 I10-I16, SNOMED 59621000/38341003, free text hypertension/HTN; pulmonary hypertension excluded)
-- whose latest event-timed vitals reading with both BP values is >=140 systolic or >=90 diastolic.
WITH htn AS MATERIALIZED (
  SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
    substr(json_extract(payload,'$.icd10'),1,3) IN ('I10','I11','I12','I13','I15','I16')
    OR json_extract(payload,'$.snomed') IN ('59621000','38341003')
    OR ((lower(json_extract(payload,'$.description')) LIKE '%hypertension%' OR json_extract(payload,'$.description') LIKE '%HTN%')
        AND lower(json_extract(payload,'$.description')) NOT LIKE '%pulmonary%'))
), bp AS MATERIALIZED (
  SELECT patient, COALESCE(json_extract(payload,'$.event_time'), event) AS t, id,
         json_extract(payload,'$.blood_pressure_systolic') AS sbp, json_extract(payload,'$.blood_pressure_diastolic') AS dbp
  FROM records WHERE stream='vitals' AND event IS NOT NULL
    AND json_extract(payload,'$.blood_pressure_systolic') IS NOT NULL AND json_extract(payload,'$.blood_pressure_diastolic') IS NOT NULL
    AND patient IN (SELECT patient FROM htn)
), ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY patient ORDER BY t DESC, id DESC) AS rn FROM bp
)
SELECT COUNT(*) AS count FROM ranked WHERE rn=1 AND (sbp>=140 OR dbp>=90);
