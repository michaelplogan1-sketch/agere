SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='medication_orders' AND (lower(json_extract(payload,'$.medication_name')) LIKE '%metformin%');
