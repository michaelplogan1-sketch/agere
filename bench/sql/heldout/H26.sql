SELECT COUNT(DISTINCT json_extract(payload,'$.appointment_id')) AS count FROM records WHERE stream='appointments'
 AND json_extract(payload,'$.status')='booked' AND json_extract(payload,'$.scheduled_date')>:as_of
 AND json_extract(payload,'$.appointment_id') IS NOT NULL;
