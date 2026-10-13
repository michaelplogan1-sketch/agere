-- Lab feed latency: percent of lab result rows with event_date 2029-01-01..2029-08-15 whose available_date (warehouse arrival) is MORE than 3 days after event_date. Undated rows excluded. Percent, 1 decimal.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs'),
x AS (SELECT julianday(json_extract(payload,'$.available_date'))-julianday(json_extract(payload,'$.event_date')) lag FROM r WHERE json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-08-15')
SELECT ROUND(100.0*SUM(lag>3)/COUNT(*),1) AS value FROM x;
