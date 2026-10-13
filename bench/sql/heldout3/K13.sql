-- ED visits per 1,000 roster patients in calendar 2026: distinct ED encounters (types 'emergency','ED','ED stroke alert') dated 2026,
-- divided by the full 1,614-person roster, times 1,000, rounded to 1 decimal.
WITH e AS (SELECT patient, json_extract(payload,'$.encounter_id') id, MIN(json_extract(payload,'$.type')) t, MIN(json_extract(payload,'$.event_date')) d
  FROM records WHERE stream='encounters' GROUP BY patient, json_extract(payload,'$.encounter_id'))
SELECT ROUND(1000.0 * (SELECT COUNT(*) FROM e WHERE t IN ('emergency','ED','ED stroke alert') AND d BETWEEN '2026-01-01' AND '2026-12-31')
       / (SELECT COUNT(*) FROM patients), 1) AS value;
