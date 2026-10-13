-- Distinct ED encounter IDs (type emergency / ED / ED stroke alert) with source event date in 2028, by registration coverage_type.
WITH ed AS MATERIALIZED (
  SELECT DISTINCT patient, json_extract(payload,'$.encounter_id') AS enc FROM records
  WHERE stream='encounters' AND event BETWEEN '2028-01-01' AND '2028-12-31'
    AND (lower(json_extract(payload,'$.type'))='emergency' OR json_extract(payload,'$.type') LIKE 'ED%')
), cov AS MATERIALIZED (
  SELECT patient, json_extract(payload,'$.coverage_type') AS coverage_type FROM records WHERE stream='coverage'
)
SELECT cov.coverage_type AS coverage_type, COUNT(DISTINCT ed.enc) AS ed_visits
FROM ed JOIN cov ON cov.patient=ed.patient GROUP BY 1 ORDER BY 1;
