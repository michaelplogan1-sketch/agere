SELECT COUNT(DISTINCT patient) AS count
FROM (SELECT * FROM records WHERE stream='enrollment_spans' AND id NOT IN (SELECT supersedes FROM records WHERE stream='enrollment_spans' AND supersedes IS NOT NULL))
WHERE json_extract(payload,'$.coverage_start') <= '2030-05-27'
  AND (json_extract(payload,'$.coverage_end') IS NULL OR json_extract(payload,'$.coverage_end') >= '2030-05-27')
  AND json_extract(payload,'$.attribution_practice_id') IS NOT NULL
