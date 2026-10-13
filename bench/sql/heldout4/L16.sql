-- Distinct inpatient/observation encounter_ids with event_date in trailing 12 months.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters'))
SELECT COUNT(DISTINCT json_extract(payload,'$.encounter_id')) AS count FROM r
WHERE json_extract(payload,'$.type') IN ('inpatient','observation','Inpatient POD3','Inpatient HD3','L&D admission')
  AND json_extract(payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15';
