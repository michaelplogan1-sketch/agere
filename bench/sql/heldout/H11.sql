SELECT COUNT(DISTINCT json_extract(payload,'$.encounter_id')) AS count FROM records
 WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED')
 AND event BETWEEN '2029-01-01' AND :as_of AND json_extract(payload,'$.encounter_id') IS NOT NULL;
