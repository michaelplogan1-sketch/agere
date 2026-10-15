SELECT json_extract(payload,'$.product') AS product, COUNT(DISTINCT patient) AS members
FROM (SELECT * FROM records WHERE stream='enrollment_spans' AND id NOT IN (SELECT supersedes FROM records WHERE stream='enrollment_spans' AND supersedes IS NOT NULL))
WHERE json_extract(payload,'$.coverage_start') <= '2030-01-01'
  AND (json_extract(payload,'$.coverage_end') IS NULL OR json_extract(payload,'$.coverage_end') >= '2030-01-01')
GROUP BY 1
ORDER BY 1
