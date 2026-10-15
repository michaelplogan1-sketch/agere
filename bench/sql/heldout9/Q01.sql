-- ED visits in calendar 2029: distinct encounter_id from encounters with type 'emergency', 'ED' or 'ED stroke alert' (any version row; in_progress/completed versions of one encounter count once), visit date = earliest event_date across versions, 2029-01-01..2029-12-31.
WITH e AS MATERIALIZED (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM records
  WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED','ED stroke alert') GROUP BY 1)
SELECT COUNT(*) AS count FROM e WHERE d BETWEEN '2029-01-01' AND '2029-12-31';
