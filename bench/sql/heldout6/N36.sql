-- % of usual_primary_care discharge plans with an in-person PCP encounter 1..recommended_within_days (14) days later.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('discharge_plans','encounters')),
dp AS (SELECT patient, json_extract(payload,'$.record_id') rid, json_extract(payload,'$.event_date') dd, json_extract(payload,'$.recommended_within_days') w FROM r
       WHERE stream='discharge_plans' AND json_extract(payload,'$.followup_service')='usual_primary_care'),
e AS (SELECT patient, json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM r WHERE stream='encounters'
      AND json_extract(payload,'$.type') IN ('primary_care','office','pediatric_office','wellness') AND json_extract(payload,'$.organization_id') LIKE 'WH-PRACTICE-%' GROUP BY 1,2)
SELECT round(100.0*(SELECT count(*) FROM dp WHERE EXISTS (SELECT 1 FROM e WHERE e.patient=dp.patient AND julianday(e.d)-julianday(dp.dd) BETWEEN 1 AND dp.w))/(SELECT count(*) FROM dp),1) AS value;
