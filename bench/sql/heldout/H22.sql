SELECT COALESCE(SUM(json_extract(payload,'$.payer_paid_cents')),0)/100.0 AS value FROM records
 WHERE stream='pharmacy_claims' AND event BETWEEN '2028-01-01' AND '2028-12-31'
 AND json_type(payload,'$.payer_paid_cents')='integer';
