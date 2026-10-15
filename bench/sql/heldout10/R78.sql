WITH d AS (
  SELECT DISTINCT json_extract(payload,'$.source_system') AS src, substr(arrival, 1, 10) AS day FROM records
  WHERE stream = 'labs' AND json_extract(payload,'$.source_system') LIKE 'WH-LAB-%' AND substr(arrival, 1, 10) BETWEEN '2029-01-01' AND '2029-12-31'
), g AS (
  SELECT src, julianday(day) - julianday(LAG(day) OVER (PARTITION BY src ORDER BY day)) AS gap FROM d
)
SELECT src AS source_system, CAST(MAX(gap) AS INTEGER) AS longest_gap_days FROM g GROUP BY src ORDER BY src
