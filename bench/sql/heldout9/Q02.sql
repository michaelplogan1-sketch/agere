-- Patients with 2 or more distinct ED visits (encounters type 'emergency'/'ED'/'ED stroke alert', distinct encounter_id, earliest event_date across versions) dated 2029-01-01..2029-12-31. Returns patient_id.
WITH e AS MATERIALIZED (SELECT patient, json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM records
  WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED','ED stroke alert') GROUP BY 1,2)
SELECT patient AS patient_id FROM e WHERE d BETWEEN '2029-01-01' AND '2029-12-31' GROUP BY patient HAVING COUNT(*)>=2 ORDER BY 1;
