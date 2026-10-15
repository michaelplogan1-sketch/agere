-- Appointment cancellations in 2029 by channel: distinct appointment_id with a 'cancelled' status row dated (event_date) 2029-01-01..2029-12-31, grouped by cancellation_channel on that row. Columns cancellation_channel, cancellations.
SELECT json_extract(payload,'$.cancellation_channel') AS cancellation_channel, COUNT(DISTINCT json_extract(payload,'$.appointment_id')) AS cancellations FROM records
WHERE stream='appointments' AND json_extract(payload,'$.status')='cancelled' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' GROUP BY 1 ORDER BY 1;
