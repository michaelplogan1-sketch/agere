-- Mean (available_date - event_date) in days for lab records dated in 2029.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='labs' AND event >= '2029-01-01')
SELECT round(avg(julianday(json_extract(payload,'$.available_date'))-julianday(json_extract(payload,'$.event_date'))),2) AS value FROM r
WHERE json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-08-15';
