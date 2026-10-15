SELECT json_extract(payload,'$.visit_type') AS visit_type, COUNT(DISTINCT json_extract(payload,'$.appointment_id')) AS bookings,
       ROUND(AVG(CAST(json_extract(payload,'$.lead_time_days') AS REAL)), 1) AS avg_lead_days
FROM records WHERE stream = 'appointments' AND json_extract(payload,'$.status') = 'booked'
  AND json_extract(payload,'$.lead_time_days') IS NOT NULL AND json_extract(payload,'$.visit_type') IS NOT NULL
GROUP BY 1 ORDER BY 1
