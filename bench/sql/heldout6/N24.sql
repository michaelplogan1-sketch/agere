-- Sum of issued patient refunds dated in 2028, dollars.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='patient_refunds')
SELECT round(sum(json_extract(payload,'$.amount_cents'))/100.0,2) AS value FROM r
WHERE json_extract(payload,'$.status')='issued' AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31';
