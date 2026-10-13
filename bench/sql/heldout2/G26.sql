-- Sum of payer_paid_cents on pharmacy_claims with transaction_status 'paid', in dollars.
SELECT ROUND(SUM(json_extract(payload,'$.payer_paid_cents'))/100.0, 2) AS value FROM records
WHERE stream='pharmacy_claims' AND json_extract(payload,'$.transaction_status')='paid';
