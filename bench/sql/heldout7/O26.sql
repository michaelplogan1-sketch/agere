-- Patient payments received (patient_payments, status 'received') with event_date in 2028 by payment_channel; columns payment_channel, payments (distinct record), total_usd. Uploaded cash receipts and payer cash are not patient payments.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='patient_payments')
SELECT json_extract(payload,'$.payment_channel') AS payment_channel, count(DISTINCT json_extract(payload,'$.record_id')) AS payments, round(sum(json_extract(payload,'$.amount_cents'))/100.0,2) AS total_usd
FROM r WHERE json_extract(payload,'$.status')='received' AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31' GROUP BY 1 ORDER BY 1;
