-- Net payer-paid dollars on medical claims for benefit year 2028, by payer (claim record organization_id, WH-PAYER-*).
-- Net = sum of financial_deltas_cents.payer across all adjudication, reversal and re-adjudication records (reversals carry negative deltas), / 100, rounded to cents.
SELECT json_extract(payload,'$.organization_id') AS payer,
       ROUND(SUM(json_extract(payload,'$.financial_deltas_cents.payer'))/100.0, 2) AS net_payer_paid_dollars
FROM records WHERE stream='medical_claims' AND json_extract(payload,'$.benefit_year')=2028
GROUP BY 1 ORDER BY 1;
