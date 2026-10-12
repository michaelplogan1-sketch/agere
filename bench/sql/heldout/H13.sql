WITH medicare AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='coverage'
  AND json_extract(payload,'$.coverage_type')='medicare' AND json_extract(payload,'$.status')='active'
), ed AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED')
  AND event BETWEEN '2029-01-01' AND :as_of
) SELECT 100.0*SUM(patient IN (SELECT patient FROM ed))/COUNT(*) AS value FROM medicare;
