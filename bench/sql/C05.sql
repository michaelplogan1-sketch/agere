WITH diabetes AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3) IN ('E08','E09','E10','E11','E13') OR
 json_extract(payload,'$.snomed') IN ('44054006','127013003','90781000119102','368581000119106','157141000119108'))
), a1c AS MATERIALIZED (
 SELECT id,patient,event,arrival,json_extract(payload,'$.value') AS value FROM records
 WHERE stream='labs' AND json_extract(payload,'$.name') IN ('HbA1c','Hemoglobin A1c','Hemoglobin A1c/Hemoglobin.total')
 AND json_extract(payload,'$.unit')='%' AND json_type(payload,'$.value') IN ('integer','real')
) SELECT DISTINCT d.patient AS patient_id FROM diabetes d JOIN a1c a ON a.patient=d.patient WHERE a.value>9 AND d.patient IN (SELECT patient FROM records WHERE stream='social_screens' AND json_type(payload,'$.food_difficulty_reported')='true') ORDER BY patient_id;
