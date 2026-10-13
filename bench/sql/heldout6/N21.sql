-- Patients with urgent care (urgent_care/urgentcare) but no ED encounter, both within 2028-08-16..2029-08-15.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, patient, min(json_extract(payload,'$.event_date')) d, max(json_extract(payload,'$.type')) t,
             max(json_extract(payload,'$.organization_id')) org FROM r GROUP BY 1,2)
SELECT count(DISTINCT patient) AS count FROM e WHERE t IN ('urgent_care','urgentcare') AND d BETWEEN '2028-08-16' AND '2029-08-15'
  AND patient NOT IN (SELECT patient FROM e WHERE t IN ('emergency','ED','ED stroke alert') AND d BETWEEN '2028-08-16' AND '2029-08-15');
