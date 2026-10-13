-- Mean (end_time - start_time) in days over completed inpatient encounters.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters')),
e AS (SELECT DISTINCT json_extract(payload,'$.encounter_id') eid, json_extract(payload,'$.start_time') s, json_extract(payload,'$.end_time') t FROM r
      WHERE json_extract(payload,'$.type')='inpatient' AND json_extract(payload,'$.status')='completed'
        AND json_extract(payload,'$.start_time') IS NOT NULL AND json_extract(payload,'$.end_time') IS NOT NULL)
SELECT round(AVG(julianday(t)-julianday(s)),2) AS value FROM e;
