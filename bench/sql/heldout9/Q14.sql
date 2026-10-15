-- Net pharmacy claim payer spend in 2029: sum of payer_paid_delta_cents over pharmacy_claim_transactions with event_date 2029-01-01..2029-12-31 (paid rows positive, reversal rows negative, rejected rows zero), divided by 100, 2 dp.
SELECT ROUND(SUM(json_extract(payload,'$.payer_paid_delta_cents'))/100.0,2) AS value FROM records
WHERE stream='pharmacy_claim_transactions' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31';
