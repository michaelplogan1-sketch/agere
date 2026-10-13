-- % of hypertensives (with a dated paired BP) whose latest-day lowest SBP<140 and DBP<90.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','vitals')), c AS (SELECT patient, json_extract(payload,'$.description') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
htn AS (SELECT DISTINCT patient FROM c WHERE (s='59621000' OR substr(i,1,3) IN ('I10','I11','I12','I13','I15','I16') OR (lower(d) LIKE '%hypertension%' AND lower(d) NOT LIKE '%pulmonary hypertension%') OR d LIKE '%HTN%')),
bp AS (SELECT patient, json_extract(payload,'$.event_date') d, json_extract(payload,'$.blood_pressure_systolic') sbp, json_extract(payload,'$.blood_pressure_diastolic') dbp
       FROM r WHERE stream='vitals' AND json_type(payload,'$.blood_pressure_systolic') IN ('integer','real') AND json_type(payload,'$.blood_pressure_diastolic') IN ('integer','real')
         AND json_extract(payload,'$.event_date') IS NOT NULL AND patient IN (SELECT patient FROM htn)),
last AS (SELECT patient, max(d) d FROM bp GROUP BY patient),
lv AS (SELECT bp.patient, min(sbp) s, min(dbp) di FROM bp JOIN last l ON l.patient=bp.patient AND l.d=bp.d GROUP BY bp.patient)
SELECT round(100.0*SUM(s<140 AND di<90)/COUNT(*),1) AS value FROM lv;
