-- Distinct telephone encounters (encounters.type='telephone', encounter_id de-duplicated across in_progress/completed versions) by calendar quarter of event_date, 2028.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
t AS (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM r WHERE json_extract(payload,'$.type')='telephone' GROUP BY 1)
SELECT '2028-Q'||((CAST(substr(d,6,2) AS INTEGER)+2)/3) AS quarter, COUNT(*) AS telephone_encounters FROM t WHERE d BETWEEN '2028-01-01' AND '2028-12-31' GROUP BY 1 ORDER BY 1;
