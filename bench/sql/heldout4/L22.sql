-- Distinct patients with an ED or urgent care encounter in trailing 12 months.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters'))
SELECT COUNT(DISTINCT patient) AS count FROM r
WHERE json_extract(payload,'$.type') IN ('emergency','ED','ED stroke alert','urgent_care','urgentcare')
  AND json_extract(payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15';
