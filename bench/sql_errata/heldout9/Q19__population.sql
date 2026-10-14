-- Prescriptions prepared but never picked up in 2029, population-wide: distinct fill_id with a pharmacy_dispenses row status
-- 'returned_to_stock' dated 2029 + distinct rx_claim_id with a payer_pharmacy_claims 'reversed' row (reversal_reason
-- return_to_stock) dated 2029.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('pharmacy_dispenses','payer_pharmacy_claims'))
SELECT (SELECT COUNT(DISTINCT json_extract(payload,'$.fill_id')) FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='returned_to_stock' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31')
     + (SELECT COUNT(DISTINCT json_extract(payload,'$.rx_claim_id')) FROM r WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='reversed' AND json_extract(payload,'$.reversal_reason')='return_to_stock' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31') AS count;
