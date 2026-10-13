-- Average length of stay in days for inpatient stays (encounters.type='inpatient') DISCHARGED in 2029 (completed version end_time date 2029-01-01..2029-08-15): mean of (end_time - start_time) in fractional days, one row per encounter_id. Stays still in progress (no completed version) are excluded; observation is not inpatient.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
s AS (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.start_time')) st, max(json_extract(payload,'$.end_time')) et FROM r
      WHERE json_extract(payload,'$.type')='inpatient' AND json_extract(payload,'$.status')='completed' GROUP BY 1)
SELECT ROUND(AVG(julianday(et)-julianday(st)),2) AS value FROM s WHERE et IS NOT NULL AND substr(et,1,10) BETWEEN '2029-01-01' AND '2029-08-15';
