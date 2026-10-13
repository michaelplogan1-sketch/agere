-- Net payer payments for medical claims with service_date in 2028 by payer (claim source_system WH-PAYER-*): sum of financial_deltas_cents.payer over every adjudication record (original, reversal, replacement) so reversals net out; dollars rounded to cents. Columns payer, claims, net_payer_paid_usd.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='medical_claims')
SELECT json_extract(payload,'$.source_system') AS payer, count(DISTINCT json_extract(payload,'$.claim_id')) AS claims,
       round(sum(json_extract(payload,'$.financial_deltas_cents.payer'))/100.0,2) AS net_payer_paid_usd
FROM r WHERE json_extract(payload,'$.claim_id') IS NOT NULL AND json_extract(payload,'$.service_date') BETWEEN '2028-01-01' AND '2028-12-31' GROUP BY 1 ORDER BY 1;
