-- 2029 YTD patient payments received minus refunds issued, dollars.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('patient_payments','patient_refunds'))
SELECT round((SUM(CASE WHEN stream='patient_payments' AND json_extract(payload,'$.status')='received' THEN json_extract(payload,'$.amount_cents') ELSE 0 END)
       - SUM(CASE WHEN stream='patient_refunds' AND json_extract(payload,'$.status')='issued' THEN json_extract(payload,'$.amount_cents') ELSE 0 END))/100.0, 2) AS value
FROM r WHERE json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-08-15';
