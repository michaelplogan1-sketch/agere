-- % of distinct 2029 encounters whose first available_date is >=3 days after earliest event_date.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='encounters' AND event >= '2029-01-01'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d, min(json_extract(payload,'$.available_date')) a FROM r GROUP BY 1)
SELECT round(100.0*sum(julianday(a)-julianday(d) >= 3)/count(*),1) AS value FROM e WHERE d BETWEEN '2029-01-01' AND '2029-08-15';
