-- Net payer-paid dollars on medical claims adjudicated in 2029: sum of financial_deltas_cents.payer over medical_claims rows with event_date (adjudication date) 2029-01-01..2029-12-31, so reversal rows (negative deltas) net out the original payment and the replacement adjudication counts once; divided by 100, 2 dp.
SELECT ROUND(SUM(json_extract(payload,'$.financial_deltas_cents.payer'))/100.0,2) AS value FROM records
WHERE stream='medical_claims' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31';
