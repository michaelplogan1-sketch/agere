-- Hospital utilization by facility for visit dates 2028-08-16..2029-08-15: distinct encounter_ids at WH-HOSPITAL-* orgs by organization_id and type (emergency/observation/inpatient); columns hospital, ed_visits, observation_stays, inpatient_stays.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, max(json_extract(payload,'$.organization_id')) org, max(json_extract(payload,'$.type')) t, min(json_extract(payload,'$.event_date')) d
      FROM r WHERE json_extract(payload,'$.organization_id') LIKE 'WH-HOSPITAL-%' GROUP BY 1)
SELECT org AS hospital, sum(t='emergency') AS ed_visits, sum(t='observation') AS observation_stays, sum(t='inpatient') AS inpatient_stays FROM e WHERE d BETWEEN '2028-08-16' AND '2029-08-15' GROUP BY 1 ORDER BY 1;
