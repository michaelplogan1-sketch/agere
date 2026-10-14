-- Appointments, population-wide: scheduled date = scheduled_date (original roster) or date of slot_start (population rows, which carry
-- slot_start / booked_at instead), from the latest row (event_date, record_id) carrying one; one outcome per appointment_id; kept =
-- any fulfilled/attended/arrived row; no-show = a no_show row and never kept.
-- Mean booking lead time (days) for kept appointments scheduled in 2029: scheduled date - earliest event_date of the appointment's
-- 'booked' row (population booked rows are dated booked_at). 2 dp.
WITH a AS MATERIALIZED (SELECT json_extract(payload,'$.appointment_id') aid, json_extract(payload,'$.status') st, coalesce(json_extract(payload,'$.scheduled_date'), substr(json_extract(payload,'$.slot_start'),1,10)) sd, json_extract(payload,'$.event_date') d, json_extract(payload,'$.record_id') rid FROM records WHERE stream='appointments'),
s AS (SELECT aid, sd FROM (SELECT aid, sd, row_number() OVER (PARTITION BY aid ORDER BY d DESC, rid DESC) rn FROM a WHERE sd IS NOT NULL) WHERE rn=1),
b AS (SELECT aid, MIN(d) bd FROM a WHERE st='booked' GROUP BY aid),
k AS (SELECT DISTINCT aid FROM a WHERE st IN ('fulfilled','attended','arrived'))
SELECT ROUND(AVG(julianday(s.sd)-julianday(b.bd)),2) AS value FROM s JOIN b USING(aid) JOIN k USING(aid) WHERE s.sd BETWEEN '2029-01-01' AND '2029-12-31';
