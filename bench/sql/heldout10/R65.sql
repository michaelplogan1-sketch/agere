SELECT ROUND(100.0 * SUM(json_extract(payload,'$.visit_type') = 'medicare_annual_wellness') / COUNT(*), 2) AS value
FROM records WHERE stream = 'wellness_visit_services' AND json_extract(payload,'$.status') = 'completed'
