-- Average arrival lag in days for dated lab results that arrived in calendar 2028: mean of (arrival date - event_date) over labs records
-- with a non-null event_date and arrival between 2028-01-01 and 2028-12-31. Rounded to 2 decimals.
SELECT ROUND(AVG(julianday(arrival) - julianday(json_extract(payload,'$.event_date'))), 2) AS value
FROM records WHERE stream='labs' AND json_extract(payload,'$.event_date') IS NOT NULL AND arrival BETWEEN '2028-01-01' AND '2028-12-31';
