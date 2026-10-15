SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream = 'care_management_enrollments' AND json_extract(payload,'$.status') = 'enrolled'
  AND CAST(json_extract(payload,'$.eligibility_risk_score') AS REAL) >= 3.0
