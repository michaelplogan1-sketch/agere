-- Distinct inpatient vs observation encounters by year of earliest event_date, 2026-2029 (zero rows kept).
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, patient, min(json_extract(payload,'$.event_date')) d, max(json_extract(payload,'$.type')) t,
             max(json_extract(payload,'$.organization_id')) org FROM r GROUP BY 1,2),
y(year) AS (VALUES ('2026'),('2027'),('2028'),('2029'))
SELECT y.year AS year, (SELECT count(*) FROM e WHERE t='inpatient' AND substr(d,1,4)=y.year) AS inpatient_stays,
       (SELECT count(*) FROM e WHERE t='observation' AND substr(d,1,4)=y.year) AS observation_stays
FROM y ORDER BY 1;
