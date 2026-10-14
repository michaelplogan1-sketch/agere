-- ED visits per 1,000 patients in 2029: distinct ED encounter_ids (type 'emergency'/'ED'/'ED stroke alert', earliest event_date in 2029) * 1000 / all roster patients (patients view, 1,614). Rounded to 2 dp.
WITH e AS MATERIALIZED (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM records
  WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED','ED stroke alert') GROUP BY 1)
SELECT ROUND(1000.0*(SELECT COUNT(*) FROM e WHERE d BETWEEN '2029-01-01' AND '2029-12-31')/(SELECT COUNT(*) FROM patients),2) AS value;
