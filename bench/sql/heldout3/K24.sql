-- No-show rate for appointments scheduled (scheduled_date) in calendar 2028: distinct appointment_ids with any 'no_show' status record,
-- divided by distinct appointment_ids with any outcome record ('fulfilled', 'arrived' or 'no_show'); percentage 0-100, 1 decimal.
-- Appointments still only 'booked', or only cancelled/rescheduled, are excluded from the denominator.
WITH a AS (SELECT json_extract(payload,'$.appointment_id') id, MAX(json_extract(payload,'$.scheduled_date')) sd,
                  MAX(json_extract(payload,'$.status')='no_show') ns,
                  MAX(json_extract(payload,'$.status') IN ('fulfilled','arrived','no_show')) outcome
           FROM records WHERE stream='appointments' GROUP BY 1)
SELECT ROUND(100.0*SUM(ns)/SUM(outcome),1) AS value FROM a WHERE sd BETWEEN '2028-01-01' AND '2028-12-31' AND outcome=1;
