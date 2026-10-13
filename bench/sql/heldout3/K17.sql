-- Patients with 2 or more ED visits (distinct encounter_id, types 'emergency','ED','ED stroke alert') dated in the 12 months ending on the as-of date
-- (event_date from 2028-08-16 through 2029-08-15 inclusive).
WITH e AS (SELECT patient, json_extract(payload,'$.encounter_id') id, MIN(json_extract(payload,'$.type')) t, MIN(json_extract(payload,'$.event_date')) d
  FROM records WHERE stream='encounters' GROUP BY patient, json_extract(payload,'$.encounter_id'))
SELECT COUNT(*) AS count FROM (SELECT patient FROM e WHERE t IN ('emergency','ED','ED stroke alert')
  AND d BETWEEN date(:as_of,'-12 months','+1 day') AND :as_of GROUP BY patient HAVING COUNT(*)>=2);
