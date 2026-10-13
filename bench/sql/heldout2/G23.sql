-- Gross received patient_payments (USD, integer cents) by payment_channel, event date 2028-07-01..2028-09-30, in dollars.
SELECT json_extract(payload,'$.payment_channel') AS payment_channel,
       ROUND(SUM(json_extract(payload,'$.amount_cents'))/100.0, 2) AS amount_usd
FROM records WHERE stream='patient_payments' AND json_extract(payload,'$.status')='received'
  AND json_extract(payload,'$.currency')='USD' AND json_type(payload,'$.amount_cents')='integer'
  AND event BETWEEN '2028-07-01' AND '2028-09-30'
GROUP BY 1 ORDER BY 1;
