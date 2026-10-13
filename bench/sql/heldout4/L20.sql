-- % of completed hospital ED encounters with disposition admitted/inpatient_admission.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters')),
e AS (SELECT DISTINCT json_extract(payload,'$.encounter_id') eid, json_extract(payload,'$.disposition') disp FROM r
      WHERE json_extract(payload,'$.type')='emergency' AND json_extract(payload,'$.status')='completed')
SELECT round(100.0*SUM(disp IN ('admitted','inpatient_admission'))/COUNT(*),1) AS value FROM e;
