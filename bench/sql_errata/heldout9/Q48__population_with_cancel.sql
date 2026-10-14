-- Appointments, population-wide: scheduled date = scheduled_date (original roster) or date of slot_start (population rows, which carry
-- slot_start / booked_at instead), from the latest row (event_date, record_id) carrying one; one outcome per appointment_id; kept =
-- any fulfilled/attended/arrived row; no-show = a no_show row and never kept.
-- Reading A: no-shows / (kept + no-shows + cancelled), %, 2 dp.
WITH a AS MATERIALIZED (SELECT json_extract(payload,'$.appointment_id') aid, json_extract(payload,'$.status') st, coalesce(json_extract(payload,'$.scheduled_date'), substr(json_extract(payload,'$.slot_start'),1,10)) sd, json_extract(payload,'$.event_date') d, json_extract(payload,'$.record_id') rid FROM records WHERE stream='appointments'),
s AS (SELECT aid, sd FROM (SELECT aid, sd, row_number() OVER (PARTITION BY aid ORDER BY d DESC, rid DESC) rn FROM a WHERE sd IS NOT NULL) WHERE rn=1),
o AS (SELECT aid, MAX(st IN ('fulfilled','attended','arrived')) kept, MAX(st='no_show') ns, MAX(st='cancelled') cx FROM a GROUP BY aid)
SELECT ROUND(100.0*SUM(ns=1 AND kept=0)/SUM(kept=1 OR ns=1 OR cx=1),2) AS value FROM o JOIN s USING(aid) WHERE s.sd BETWEEN '2029-01-01' AND '2029-12-31';
