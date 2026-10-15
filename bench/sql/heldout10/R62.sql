SELECT COUNT(DISTINCT json_extract(payload,'$.appointment_id')) AS count FROM records
WHERE stream = 'appointments' AND lower(json_extract(payload,'$.reason')) = 'transportation'
