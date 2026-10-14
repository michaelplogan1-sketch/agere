-- ED visits in 2029 by hospital: distinct encounter_id of encounters type 'emergency'/'ED'/'ED stroke alert', earliest event_date in 2029, grouped by organization_id. Columns organization_id, ed_visits.
WITH e AS MATERIALIZED (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.organization_id')) org, min(json_extract(payload,'$.event_date')) d FROM records
  WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED','ED stroke alert') GROUP BY 1)
SELECT org AS organization_id, COUNT(*) AS ed_visits FROM e WHERE d BETWEEN '2029-01-01' AND '2029-12-31' GROUP BY org ORDER BY org;
