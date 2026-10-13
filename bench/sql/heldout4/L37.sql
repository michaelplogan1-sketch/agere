-- Rows in labs with null event_date.
SELECT COUNT(*) AS count FROM records WHERE stream='labs' AND json_extract(payload,'$.event_date') IS NULL;
