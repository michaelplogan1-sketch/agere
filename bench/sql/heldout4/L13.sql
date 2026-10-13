-- Distinct ED encounter_ids per month, Jan-Jul 2029.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters')), ed AS (SELECT patient, json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM r WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED','ED stroke alert') AND json_extract(payload,'$.event_date') IS NOT NULL GROUP BY patient, eid)
SELECT substr(d,1,7) AS month, COUNT(*) AS ed_visits FROM ed WHERE d BETWEEN '2029-01-01' AND '2029-07-31' GROUP BY month ORDER BY month;
