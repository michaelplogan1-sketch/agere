-- Patients with >=2 distinct ED encounters (type 'emergency', distinct encounter_id; in_progress+completed rows of one visit counted once; visit date = earliest event_date) dated 2028-08-16..2029-08-15.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, patient, min(json_extract(payload,'$.event_date')) d FROM r WHERE json_extract(payload,'$.type')='emergency' GROUP BY 1,2)
SELECT COUNT(*) AS count FROM (SELECT patient FROM e WHERE d BETWEEN '2028-08-16' AND '2029-08-15' GROUP BY 1 HAVING count(*)>=2);
