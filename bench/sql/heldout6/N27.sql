-- Sum of payer_paid_delta_cents (paid + reversed; rejected=0) for 2028 pharmacy claim transactions, dollars.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='pharmacy_claim_transactions')
SELECT round(sum(json_extract(payload,'$.payer_paid_delta_cents'))/100.0,2) AS value FROM r
WHERE json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31';
