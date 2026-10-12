WITH diabetes AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3) IN ('E08','E09','E10','E11','E13') OR
 json_extract(payload,'$.snomed') IN ('44054006','127013003','90781000119102','368581000119106','157141000119108'))
) SELECT COUNT(DISTINCT d.patient) AS count FROM diabetes d JOIN records r ON r.patient=d.patient WHERE r.stream='appointments' AND json_extract(r.payload,'$.status')='no_show' AND json_extract(r.payload,'$.scheduled_date') BETWEEN '2028-01-01' AND '2028-12-31';
