-- Inpatient admissions by half-year of 2026. Inpatient encounter types: 'inpatient', 'Inpatient POD3', 'Inpatient HD3', 'L&D admission'
-- (observation stays, SNF and ED excluded). One admission = one distinct encounter_id, dated by event_date; undated excluded.
WITH e AS (SELECT patient, json_extract(payload,'$.encounter_id') id, MIN(json_extract(payload,'$.type')) t, MIN(json_extract(payload,'$.event_date')) d
  FROM records WHERE stream='encounters' GROUP BY patient, json_extract(payload,'$.encounter_id'))
SELECT '2026-H' || CASE WHEN d < '2026-07-01' THEN '1' ELSE '2' END AS half_year, COUNT(*) AS admissions
FROM e WHERE t IN ('inpatient','Inpatient POD3','Inpatient HD3','L&D admission') AND d BETWEEN '2026-01-01' AND '2026-12-31'
GROUP BY 1 ORDER BY 1;
