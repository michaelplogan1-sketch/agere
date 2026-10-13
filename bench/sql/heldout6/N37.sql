-- Lab records with a null event_date.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='labs')
SELECT count(*) AS count FROM r WHERE json_extract(payload,'$.event_date') IS NULL;
