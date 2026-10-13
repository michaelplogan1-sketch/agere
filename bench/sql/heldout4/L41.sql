-- Distinct encounter_ids with >= 2 encounter records.
WITH e AS (SELECT json_extract(payload,'$.encounter_id') eid FROM records WHERE stream='encounters')
SELECT COUNT(*) AS count FROM (SELECT eid FROM e WHERE eid IS NOT NULL GROUP BY eid HAVING COUNT(*) > 1);
