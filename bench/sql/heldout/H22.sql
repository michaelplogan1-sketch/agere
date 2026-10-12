SELECT COALESCE(SUM(json_extract(payload,'$.payer_paid_cents')),0)/100.0 AS value FROM records
 WHERE stream='pharmacy_claims' AND event BETWEEN '2026-09-01' AND '2026-09-30'
 AND json_type(payload,'$.payer_paid_cents')='integer';
