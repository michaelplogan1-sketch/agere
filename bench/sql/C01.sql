WITH snomed_diabetes AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions'
 AND json_extract(payload,'$.snomed') IN ('44054006','127013003','90781000119102','368581000119106','157141000119108')
), icd_diabetes AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions'
 AND substr(json_extract(payload,'$.icd10'),1,3) IN ('E08','E09','E10','E11','E13')
) SELECT COUNT(*) AS count FROM snomed_diabetes WHERE patient NOT IN (SELECT patient FROM icd_diabetes);
