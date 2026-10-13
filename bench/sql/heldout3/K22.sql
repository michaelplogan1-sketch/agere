-- Patient payments received during calendar 2028 (payment record event_date), total dollars by payment channel (cash / ACH / card).
-- All patient_payments records count (status 'received'), including the few without a currency field. amount_cents / 100, rounded to cents.
SELECT json_extract(payload,'$.payment_channel') AS payment_channel, ROUND(SUM(json_extract(payload,'$.amount_cents'))/100.0, 2) AS total_dollars
FROM records WHERE stream='patient_payments' AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31'
GROUP BY 1 ORDER BY 1;
