WITH ed AS MATERIALIZED (
 SELECT DISTINCT patient,json_extract(payload,'$.encounter_id') AS enc FROM records
 WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED')
 AND event BETWEEN '2026-01-01' AND '2026-12-31' AND json_extract(payload,'$.encounter_id') IS NOT NULL
), cov AS MATERIALIZED (
 SELECT DISTINCT patient,json_extract(payload,'$.coverage_type') AS coverage_type FROM records WHERE stream='coverage'
) SELECT cov.coverage_type AS coverage_type,COUNT(DISTINCT ed.enc) AS ed_visit_count FROM ed JOIN cov USING(patient)
 GROUP BY 1 ORDER BY 1;
