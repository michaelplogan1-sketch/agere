-- Stale appointments: distinct appointment_ids whose ONLY received status is 'booked' (no fulfilled/arrived/no_show/cancelled/rescheduled version in any order) and whose scheduled_date is before 2029-08-15. Appointments with no scheduled_date are excluded.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='appointments'),
a AS (SELECT json_extract(payload,'$.appointment_id') aid, max(json_extract(payload,'$.scheduled_date')) sd, SUM(json_extract(payload,'$.status')<>'booked') other FROM r GROUP BY 1)
SELECT COUNT(*) AS count FROM a WHERE other=0 AND sd < '2029-08-15';
