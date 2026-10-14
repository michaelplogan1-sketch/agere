-- Net pharmacy claim payer spend in 2029, population-wide: pharmacy_claim_transactions payer_paid_delta_cents dated 2029 (reversals
-- negative) + payer_pharmacy_claims plan_paid of 'paid' rows dated 2029 minus, for 'reversed' rows dated 2029, the plan_paid of the
-- reversed claim's paid row (payer reversal rows carry 0, not the negative). Dollars, 2 dp.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('pharmacy_claim_transactions','payer_pharmacy_claims')),
pp AS MATERIALIZED (SELECT json_extract(payload,'$.rx_claim_id') cid, json_extract(payload,'$.claim_status') st, json_extract(payload,'$.event_date') d, json_extract(payload,'$.plan_paid') paid FROM r WHERE stream='payer_pharmacy_claims'),
orig AS MATERIALIZED (SELECT cid, sum(paid) paid FROM pp WHERE st='paid' GROUP BY cid)
SELECT ROUND(((SELECT coalesce(sum(json_extract(payload,'$.payer_paid_delta_cents')),0) FROM r WHERE stream='pharmacy_claim_transactions' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31')/100.0
  + (SELECT coalesce(sum(paid),0) FROM pp WHERE st='paid' AND d BETWEEN '2029-01-01' AND '2029-12-31')
  - (SELECT coalesce(sum(o.paid),0) FROM pp x JOIN orig o USING(cid) WHERE x.st='reversed' AND x.d BETWEEN '2029-01-01' AND '2029-12-31')),2) AS value;
