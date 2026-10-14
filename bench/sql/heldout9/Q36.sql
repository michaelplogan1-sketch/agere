-- Average booking lead time (days) for kept appointments scheduled in 2029: per appointment_id, booked date = earliest event_date of a 'booked' row; scheduled date = scheduled_date on the latest row carrying one (2029-01-01..2029-12-31); kept = any row status fulfilled/attended/arrived; lead = scheduled - booked. Mean, 2 dp.
WITH a AS MATERIALIZED (SELECT json_extract(payload,'$.appointment_id') aid, json_extract(payload,'$.status') st, json_extract(payload,'$.scheduled_date') sd, json_extract(payload,'$.event_date') d, json_extract(payload,'$.record_id') rid FROM records WHERE stream='appointments'),
s AS (SELECT aid, sd FROM (SELECT aid, sd, row_number() OVER (PARTITION BY aid ORDER BY d DESC, rid DESC) rn FROM a WHERE sd IS NOT NULL) WHERE rn=1),
b AS (SELECT aid, MIN(d) bd FROM a WHERE st='booked' GROUP BY aid),
k AS (SELECT DISTINCT aid FROM a WHERE st IN ('fulfilled','attended','arrived'))
SELECT ROUND(AVG(julianday(s.sd)-julianday(b.bd)),2) AS value FROM s JOIN b USING(aid) JOIN k USING(aid) WHERE s.sd BETWEEN '2029-01-01' AND '2029-12-31';
