-- Pharmacy fills, population-wide:
--  pharmacy_dispenses (original roster): only status 'dispensed' rows are pickups ('prepared' / 'returned_to_stock' are not);
--    one fill per fill_id (record_id when absent); date = event_date;
--  payer_pharmacy_claims (population): a fill is an rx_claim_id with a 'paid' row; it is picked up only when no 'reversed' row
--    (transaction B2, reversal_reason return_to_stock) exists for it by the as-of date; date = event_date of the paid row.
-- Reading A: patients with a statin pickup dated in the 12 months to as-of (2029-05-28..2030-05-27).
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('pharmacy_dispenses','payer_pharmacy_claims')),
fills AS MATERIALIZED (
  SELECT DISTINCT patient, 'D:'||coalesce(json_extract(payload,'$.fill_id'),json_extract(payload,'$.record_id')) fid, lower(json_extract(payload,'$.medication_name')) m, json_extract(payload,'$.event_date') d, json_extract(payload,'$.days_supply_reported') ds
   FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed'
  UNION ALL
  SELECT patient, 'C:'||json_extract(payload,'$.rx_claim_id'), lower(json_extract(payload,'$.drug_name')), json_extract(payload,'$.event_date'), json_extract(payload,'$.days_supply') FROM r
   WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='paid'
     AND json_extract(payload,'$.rx_claim_id') NOT IN (SELECT json_extract(payload,'$.rx_claim_id') FROM r WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='reversed'))
SELECT COUNT(DISTINCT patient) AS count FROM fills WHERE d BETWEEN '2029-05-28' AND '2030-05-27' AND (m LIKE '%simvastatin%' OR m LIKE '%atorvastatin%' OR m LIKE '%rosuvastatin%' OR m LIKE '%pravastatin%' OR m LIKE '%lovastatin%' OR m LIKE '%pitavastatin%' OR m LIKE '%fluvastatin%');
