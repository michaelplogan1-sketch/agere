-- Data quality: encounters in 2029 that arrived as more than one version row: distinct encounter_id (any type) with >= 2 encounters rows and earliest event_date 2029-01-01..2029-12-31.
WITH e AS MATERIALIZED (SELECT json_extract(payload,'$.encounter_id') eid, COUNT(*) n, MIN(json_extract(payload,'$.event_date')) d FROM records WHERE stream='encounters' GROUP BY 1)
SELECT COUNT(*) AS count FROM e WHERE n>=2 AND d BETWEEN '2029-01-01' AND '2029-12-31';
