-- ED visits (distinct ED encounter IDs, event dated 2029-01-01..2029-08-15) per 1,000 roster patients.
SELECT ROUND(1000.0 * (SELECT COUNT(DISTINCT json_extract(payload,'$.encounter_id')) FROM records
   WHERE stream='encounters' AND event BETWEEN '2029-01-01' AND '2029-08-15'
     AND (lower(json_extract(payload,'$.type'))='emergency' OR json_extract(payload,'$.type') LIKE 'ED%'))
  / (SELECT COUNT(*) FROM patients), 4) AS value;
