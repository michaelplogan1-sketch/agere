-- Share of coverage_type=medicare patients with >=1 completed primary_care encounter with event date in 2028.
WITH mc AS MATERIALIZED (
  SELECT DISTINCT patient FROM records WHERE stream='coverage' AND json_extract(payload,'$.coverage_type')='medicare'
), seen AS MATERIALIZED (
  SELECT DISTINCT patient FROM records WHERE stream='encounters' AND json_extract(payload,'$.type')='primary_care'
    AND json_extract(payload,'$.status')='completed' AND event BETWEEN '2028-01-01' AND '2028-12-31'
)
SELECT ROUND(100.0 * (SELECT COUNT(*) FROM mc WHERE patient IN (SELECT patient FROM seen)) / (SELECT COUNT(*) FROM mc), 4) AS value;
