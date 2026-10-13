-- % of hospital ED encounters (>=2026-09-01) linked to a subsequent observation/inpatient stay via referring/transferred-from links or transfer_to_observation.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters','adt')),
ed AS (SELECT json_extract(payload,'$.encounter_id') eid FROM r WHERE stream='encounters' AND json_extract(payload,'$.type')='emergency'
       AND json_extract(payload,'$.organization_id') LIKE 'WH-HOSPITAL-%' GROUP BY 1 HAVING min(json_extract(payload,'$.event_date')) >= '2026-09-01'),
lk AS (SELECT json_extract(payload,'$.referring_encounter_id') eid FROM r WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('inpatient','observation')
       UNION SELECT json_extract(payload,'$.referring_encounter_id') FROM r WHERE stream='adt' AND json_extract(payload,'$.event') IN ('admission','observation_start')
       UNION SELECT json_extract(payload,'$.transferred_from_encounter_id') FROM r WHERE stream='adt'
       UNION SELECT json_extract(payload,'$.encounter_id') FROM r WHERE stream='adt' AND json_extract(payload,'$.event')='transfer_to_observation')
SELECT round(100.0*(SELECT count(*) FROM ed WHERE eid IN (SELECT eid FROM lk WHERE eid IS NOT NULL))/(SELECT count(*) FROM ed),1) AS value;
