SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream = 'health_risk_assessments' AND CAST(json_extract(payload,'$.phq2_total') AS INTEGER) >= 3
