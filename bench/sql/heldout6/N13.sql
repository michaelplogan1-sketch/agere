-- Distinct 2027 encounters at WH-SPECIALTY-*/WH-GI-* orgs per 1,000 roster patients by coverage_type (all patients in denominator).
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, patient, min(json_extract(payload,'$.event_date')) d, max(json_extract(payload,'$.type')) t,
             max(json_extract(payload,'$.organization_id')) org FROM r GROUP BY 1,2),
c AS MATERIALIZED (SELECT patient, json_extract(payload,'$.coverage_type') ct FROM records WHERE stream='coverage'),
v AS (SELECT patient, count(*) n FROM e WHERE (org LIKE 'WH-SPECIALTY-%' OR org LIKE 'WH-GI-%') AND d BETWEEN '2027-01-01' AND '2027-12-31' GROUP BY 1)
SELECT c.ct AS coverage_type, count(*) AS patients, coalesce(sum(v.n),0) AS specialist_visits, round(coalesce(sum(v.n),0)*1000.0/count(*),1) AS per_1000
FROM c LEFT JOIN v USING(patient) GROUP BY 1 ORDER BY 1;
