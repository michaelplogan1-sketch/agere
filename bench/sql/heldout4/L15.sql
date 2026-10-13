-- Distinct urgent care encounter_ids by calendar quarter, 2028-Q4..2029-Q3 (partial).
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters')),
uc AS (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM r
       WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('urgent_care','urgentcare') AND json_extract(payload,'$.event_date') IS NOT NULL GROUP BY eid)
SELECT substr(d,1,4)||'-Q'||((CAST(substr(d,6,2) AS INTEGER)+2)/3) AS quarter, COUNT(*) AS uc_visits
FROM uc WHERE d BETWEEN '2028-10-01' AND '2029-08-15' GROUP BY quarter ORDER BY quarter;
