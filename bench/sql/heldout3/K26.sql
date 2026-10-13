-- Lab result records (labs stream) received with no collection/event date (event_date null). Counts records, not patients.
SELECT COUNT(*) AS count FROM records WHERE stream='labs' AND json_extract(payload,'$.event_date') IS NULL;
