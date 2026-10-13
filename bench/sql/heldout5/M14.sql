-- Patients with 2+ distinct ED encounters (type emergency / ED / ED stroke alert; encounter_id de-duplicated across versions) dated in the trailing 12 months 2028-08-16..2029-08-15. Undated ED rows cannot be placed in the window.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
ed AS (SELECT patient, json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM r WHERE json_extract(payload,'$.type') IN ('emergency','ED','ED stroke alert') GROUP BY 1,2)
SELECT patient AS patient_id FROM ed WHERE d BETWEEN '2028-08-16' AND '2029-08-15' GROUP BY patient HAVING COUNT(*)>=2 ORDER BY 1;
