-- Patients with >=2 distinct ED encounters (emergency/ED/ED stroke alert) dated 2028-08-16..2029-08-15.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, patient, min(json_extract(payload,'$.event_date')) d, max(json_extract(payload,'$.type')) t,
             max(json_extract(payload,'$.organization_id')) org FROM r GROUP BY 1,2)
SELECT patient AS patient_id FROM e WHERE t IN ('emergency','ED','ED stroke alert') AND d BETWEEN '2028-08-16' AND '2029-08-15'
GROUP BY patient HAVING count(*) >= 2 ORDER BY 1;
