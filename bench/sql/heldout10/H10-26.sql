WITH htn AS (
  SELECT DISTINCT patient FROM (
    SELECT patient, upper(replace(json_extract(payload,'$.icd10'),'.','')) AS c FROM records WHERE stream='conditions'
    UNION ALL SELECT r.patient, upper(replace(json_extract(d.value,'$.icd10'),'.','')) FROM records r, json_each(r.payload,'$.diagnoses') d WHERE r.stream='encounters'
    UNION ALL SELECT r.patient, upper(replace(d.value,'.','')) FROM records r, json_each(r.payload,'$.diagnosis_codes') d WHERE r.stream IN ('payer_medical_claims','medical_claims')
  ) WHERE substr(c,1,3) IN ('I10','I11','I12','I13','I15','I16')),
bp AS (
  SELECT patient, event AS d, CAST(json_extract(payload,'$.blood_pressure_systolic') AS REAL) AS sbp, CAST(json_extract(payload,'$.blood_pressure_diastolic') AS REAL) AS dbp
  FROM records WHERE stream='vitals' AND event IS NOT NULL
    AND json_extract(payload,'$.blood_pressure_systolic') IS NOT NULL AND json_extract(payload,'$.blood_pressure_diastolic') IS NOT NULL),
y AS (SELECT * FROM bp WHERE d BETWEEN '2029-01-01' AND '2029-12-31' AND patient IN (SELECT patient FROM htn)),
last AS (SELECT y.patient, min(y.sbp) s, min(y.dbp) di FROM y JOIN (SELECT patient, max(d) d FROM y GROUP BY patient) l ON l.patient=y.patient AND l.d=y.d GROUP BY y.patient)
SELECT round(100.0 * sum(s < 140 AND di < 90) / count(*), 2) AS value FROM last
