WITH diabetes AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3) IN ('E08','E09','E10','E11','E13') OR
 json_extract(payload,'$.snomed') IN ('44054006','127013003','90781000119102','368581000119106','157141000119108'))
), a1c AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='labs'
  AND json_extract(payload,'$.name') IN ('HbA1c','Hemoglobin A1c','Hemoglobin A1c/Hemoglobin.total')
) SELECT COUNT(*) AS count FROM diabetes WHERE patient NOT IN (SELECT patient FROM a1c);
