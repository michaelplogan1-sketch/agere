-- Completed primary_care encounters (distinct encounter IDs with a status=completed row), event date 2028-07-01..2028-09-30, by organization_id.
SELECT json_extract(payload,'$.organization_id') AS practice_id, COUNT(DISTINCT json_extract(payload,'$.encounter_id')) AS completed_visits
FROM records WHERE stream='encounters' AND json_extract(payload,'$.type')='primary_care'
  AND json_extract(payload,'$.status')='completed' AND event BETWEEN '2028-07-01' AND '2028-09-30'
GROUP BY 1 ORDER BY 1;
