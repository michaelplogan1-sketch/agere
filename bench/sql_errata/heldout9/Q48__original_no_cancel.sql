-- Reading B on the reference rows (scheduled_date only, i.e. original-roster appointments): no-shows / (kept + no-shows). 2 dp.
WITH a AS MATERIALIZED (SELECT json_extract(payload,'$.appointment_id') aid, json_extract(payload,'$.status') st, json_extract(payload,'$.scheduled_date') sd, json_extract(payload,'$.event_date') d, json_extract(payload,'$.record_id') rid FROM records WHERE stream='appointments'),
s AS (SELECT aid, sd FROM (SELECT aid, sd, row_number() OVER (PARTITION BY aid ORDER BY d DESC, rid DESC) rn FROM a WHERE sd IS NOT NULL) WHERE rn=1),
o AS (SELECT aid, MAX(st IN ('fulfilled','attended','arrived')) kept, MAX(st='no_show') ns FROM a GROUP BY aid)
SELECT ROUND(100.0*SUM(ns=1 AND kept=0)/SUM(kept=1 OR ns=1),2) AS value FROM o JOIN s USING(aid) WHERE s.sd BETWEEN '2029-01-01' AND '2029-12-31';
