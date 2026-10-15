-- Mean inpatient length of stay in days for inpatient encounters (encounters type 'inpatient', completed version carrying both start_time and end_time; one row per encounter_id) whose discharge (date of end_time) falls 2029-01-01..2029-12-31; LOS = (end_time - start_time) in fractional days.
WITH e AS MATERIALIZED (SELECT json_extract(payload,'$.encounter_id') eid, max(json_extract(payload,'$.start_time')) s, max(json_extract(payload,'$.end_time')) en FROM records
  WHERE stream='encounters' AND json_extract(payload,'$.type')='inpatient' AND json_extract(payload,'$.status')='completed' GROUP BY 1)
SELECT ROUND(AVG(julianday(en)-julianday(s)),4) AS value FROM e WHERE en IS NOT NULL AND s IS NOT NULL AND substr(en,1,10) BETWEEN '2029-01-01' AND '2029-12-31';
