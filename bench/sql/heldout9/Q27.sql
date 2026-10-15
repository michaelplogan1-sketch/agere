-- Inpatient admissions per 1,000 patients in 2029: distinct encounter_id of encounters type 'inpatient' (versions collapsed) whose admission date (earliest event_date) is 2029-01-01..2029-12-31, * 1000 / roster patients (1,614). 2 dp.
WITH e AS MATERIALIZED (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM records
  WHERE stream='encounters' AND json_extract(payload,'$.type')='inpatient' GROUP BY 1)
SELECT ROUND(1000.0*(SELECT COUNT(*) FROM e WHERE d BETWEEN '2029-01-01' AND '2029-12-31')/(SELECT COUNT(*) FROM patients),2) AS value;
