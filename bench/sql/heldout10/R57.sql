WITH a AS (
  SELECT json_extract(payload,'$.appointment_id') AS aid, MAX(json_extract(payload,'$.status') = 'no_show') AS ns, MIN(event) AS d
  FROM records WHERE stream = 'appointments' AND json_extract(payload,'$.status') IN ('fulfilled', 'attended', 'arrived', 'no_show')
  GROUP BY 1
)
SELECT ROUND(100.0 * SUM(ns) / COUNT(*), 2) AS value FROM a WHERE d BETWEEN '2029-01-01' AND '2029-12-31'
