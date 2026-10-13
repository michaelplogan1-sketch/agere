-- Attributed patients with no in-person PCP encounter (primary_care/office/pediatric_office/wellness at WH-PRACTICE-*) dated 2028-08-16..2029-08-15.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, patient, min(json_extract(payload,'$.event_date')) d, max(json_extract(payload,'$.type')) t,
             max(json_extract(payload,'$.organization_id')) org FROM r GROUP BY 1,2),
att AS (SELECT patient, max(json_extract(payload,'$.organization_id')) practice FROM records WHERE stream='care_team_relationships'
        AND json_extract(payload,'$.role') IN ('primary_care','pediatric_primary_care') AND json_extract(payload,'$.status')='assigned' GROUP BY 1)
SELECT count(*) AS count FROM att WHERE patient NOT IN (SELECT patient FROM e WHERE t IN ('primary_care','office','pediatric_office','wellness')
  AND org LIKE 'WH-PRACTICE-%' AND d BETWEEN '2028-08-16' AND '2029-08-15');
