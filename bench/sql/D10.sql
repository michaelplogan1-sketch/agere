WITH diabetes AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3) IN ('E08','E09','E10','E11','E13') OR
 json_extract(payload,'$.snomed') IN ('44054006','127013003','90781000119102','368581000119106','157141000119108'))
), egfr AS MATERIALIZED (
 SELECT patient,event,json_extract(payload,'$.value') AS value FROM records
 WHERE stream='labs' AND json_extract(payload,'$.name')='eGFR'
 AND json_extract(payload,'$.unit') IN ('mL/min/1.73m2','mL/min/1.73m²') AND json_type(payload,'$.value') IN ('integer','real')
) SELECT DISTINCT diabetes.patient AS patient_id FROM diabetes JOIN egfr USING(patient) WHERE value<60 ORDER BY patient_id;
