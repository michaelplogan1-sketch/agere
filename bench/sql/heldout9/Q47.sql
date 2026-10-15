-- AMBIGUOUS (reading A): payer payments for 2029 = net payer-paid dollars (sum financial_deltas_cents.payer incl. negative reversal rows) over medical_claims rows whose claim service_date is 2029-01-01..2029-12-31, /100, 2 dp. Reading B anchors on the adjudication event_date instead.
SELECT ROUND(SUM(json_extract(payload,'$.financial_deltas_cents.payer'))/100.0,2) AS value FROM records
WHERE stream='medical_claims' AND json_extract(payload,'$.service_date') BETWEEN '2029-01-01' AND '2029-12-31';
