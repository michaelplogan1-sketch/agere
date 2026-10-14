-- Pharmacy claims rejected as refill too soon in 2029, population-wide: distinct claim_id of pharmacy_claim_transactions
-- 'rejected' / reject_code 'REFILL_TOO_SOON' + distinct rx_claim_id of payer_pharmacy_claims 'rejected' with NCPDP reject code '79'
-- (Refill Too Soon), event_date 2029.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('pharmacy_claim_transactions','payer_pharmacy_claims'))
SELECT (SELECT COUNT(DISTINCT json_extract(payload,'$.claim_id')) FROM r WHERE stream='pharmacy_claim_transactions' AND json_extract(payload,'$.transaction_status')='rejected' AND json_extract(payload,'$.reject_code')='REFILL_TOO_SOON' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31')
     + (SELECT COUNT(DISTINCT json_extract(payload,'$.rx_claim_id')) FROM r WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='rejected' AND json_extract(payload,'$.reject_code')='79' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31') AS count;
