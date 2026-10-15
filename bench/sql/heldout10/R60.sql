SELECT json_extract(payload,'$.cancellation_channel') AS cancellation_channel, COUNT(DISTINCT json_extract(payload,'$.appointment_id')) AS cancelled_appointments
FROM records WHERE stream = 'appointments' AND json_extract(payload,'$.status') = 'cancelled' AND json_extract(payload,'$.cancellation_channel') IS NOT NULL
GROUP BY 1 ORDER BY 1
