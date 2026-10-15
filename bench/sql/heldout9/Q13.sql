-- Net payer-paid medical dollars by payer for 2029 adjudications: sum of financial_deltas_cents.payer (reversals negative) over medical_claims rows with event_date 2029-01-01..2029-12-31, grouped by the adjudicating payer organization_id; dollars 2 dp. Columns payer, net_paid_usd.
SELECT json_extract(payload,'$.organization_id') AS payer, ROUND(SUM(json_extract(payload,'$.financial_deltas_cents.payer'))/100.0,2) AS net_paid_usd FROM records
WHERE stream='medical_claims' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' GROUP BY 1 ORDER BY 1;
