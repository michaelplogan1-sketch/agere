-- No-show % among 2028-scheduled appointments with a kept or no-show outcome.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('appointments')),
a AS (SELECT json_extract(payload,'$.appointment_id') aid, json_extract(payload,'$.status') st, json_extract(payload,'$.scheduled_date') sd FROM r),
b AS (SELECT aid FROM a WHERE st='booked' AND sd BETWEEN '2028-01-01' AND '2028-12-31'),
o AS (SELECT aid, MAX(st IN ('fulfilled','arrived')) kept, MAX(st='no_show') ns FROM a WHERE aid IN (SELECT aid FROM b) GROUP BY aid)
SELECT round(100.0*SUM(ns=1 AND kept=0)/SUM(kept=1 OR ns=1),1) AS value FROM o;
