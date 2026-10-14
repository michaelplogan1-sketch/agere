-- Data quality: lab result rows with no event_date (undated legacy chart-import results) in the labs stream, all rows received through the as-of date.
SELECT COUNT(*) AS count FROM records WHERE stream='labs' AND json_extract(payload,'$.event_date') IS NULL;
