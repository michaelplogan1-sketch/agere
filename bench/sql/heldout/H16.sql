SELECT json_extract(payload,'$.organization_id') AS organization_id,
 COUNT(DISTINCT json_extract(payload,'$.encounter_id')) AS visit_count FROM records
 WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('urgent_care','urgentcare')
 AND event BETWEEN '2029-01-01' AND :as_of AND json_extract(payload,'$.organization_id') IS NOT NULL
 AND json_extract(payload,'$.encounter_id') IS NOT NULL GROUP BY 1 ORDER BY 1;
