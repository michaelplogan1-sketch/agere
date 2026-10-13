-- 2028 in-person PCP encounters (primary_care/office/pediatric_office/wellness at WH-PRACTICE-*) for patients by attributed practice, per 1,000 attributed.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, patient, min(json_extract(payload,'$.event_date')) d, max(json_extract(payload,'$.type')) t,
             max(json_extract(payload,'$.organization_id')) org FROM r GROUP BY 1,2),
att AS (SELECT patient, max(json_extract(payload,'$.organization_id')) practice FROM records WHERE stream='care_team_relationships'
        AND json_extract(payload,'$.role') IN ('primary_care','pediatric_primary_care') AND json_extract(payload,'$.status')='assigned' GROUP BY 1),
v AS (SELECT patient, count(*) n FROM e WHERE t IN ('primary_care','office','pediatric_office','wellness') AND org LIKE 'WH-PRACTICE-%' AND d BETWEEN '2028-01-01' AND '2028-12-31' GROUP BY 1)
SELECT practice, count(*) AS attributed_patients, coalesce(sum(v.n),0) AS visits, round(coalesce(sum(v.n),0)*1000.0/count(*),1) AS per_1000
FROM att LEFT JOIN v USING(patient) GROUP BY 1 ORDER BY 1;
