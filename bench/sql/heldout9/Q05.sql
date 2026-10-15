-- Hospital observation stays in 2029: distinct encounter_id of encounters type 'observation' (all version rows collapsed), stay date = earliest event_date across versions in 2029-01-01..2029-12-31.
WITH e AS MATERIALIZED (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM records
  WHERE stream='encounters' AND json_extract(payload,'$.type')='observation' GROUP BY 1)
SELECT COUNT(*) AS count FROM e WHERE d BETWEEN '2029-01-01' AND '2029-12-31';
