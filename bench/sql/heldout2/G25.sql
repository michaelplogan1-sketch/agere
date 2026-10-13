-- Completed immunization administrations (immunizations stream) by vaccine and administering organization.
SELECT json_extract(payload,'$.organization_id') AS practice_id, json_extract(payload,'$.vaccine') AS vaccine, COUNT(*) AS doses
FROM records WHERE stream='immunizations' AND json_extract(payload,'$.administration_status')='completed'
GROUP BY 1,2 ORDER BY 1,2;
