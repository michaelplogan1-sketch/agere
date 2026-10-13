-- ED visits by calendar quarter of 2029. One visit = one distinct encounter_id (status updates of the same encounter are not double-counted).
-- ED types: 'emergency', 'ED', 'ED stroke alert'. Visit date = encounter event_date; undated encounters excluded. Quarters with data through the as-of date.
WITH e AS (SELECT patient, json_extract(payload,'$.encounter_id') id, MIN(json_extract(payload,'$.type')) t, MIN(json_extract(payload,'$.event_date')) d
  FROM records WHERE stream='encounters' GROUP BY patient, json_extract(payload,'$.encounter_id'))
SELECT substr(d,1,4) || '-Q' || ((CAST(substr(d,6,2) AS INTEGER)+2)/3) AS quarter, COUNT(*) AS ed_visits
FROM e WHERE t IN ('emergency','ED','ED stroke alert') AND d BETWEEN '2029-01-01' AND '2029-12-31'
GROUP BY 1 ORDER BY 1;
