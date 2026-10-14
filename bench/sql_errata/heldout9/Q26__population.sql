-- Diabetes (population-wide) = problem list (conditions icd10 E10/E11/E13) OR an E10/E11/E13 code in the diagnosis_codes of any
-- payer_medical_claims row (the only diagnosis source for claims-only members). Prediabetes R73.x / family history Z83.3 never qualify.
-- Pharmacy fills, population-wide:
--  pharmacy_dispenses (original roster): only status 'dispensed' rows are pickups ('prepared' / 'returned_to_stock' are not);
--    one fill per fill_id (record_id when absent); date = event_date;
--  payer_pharmacy_claims (population): a fill is an rx_claim_id with a 'paid' row; it is picked up only when no 'reversed' row
--    (transaction B2, reversal_reason return_to_stock) exists for it by the as-of date; date = event_date of the paid row.
-- Diabetics with >= 1 statin pickup dated 2029. Distinct patients.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('pharmacy_dispenses','payer_pharmacy_claims','conditions','payer_medical_claims')),
fills AS MATERIALIZED (
  SELECT DISTINCT patient, 'D:'||coalesce(json_extract(payload,'$.fill_id'),json_extract(payload,'$.record_id')) fid, lower(json_extract(payload,'$.medication_name')) m, json_extract(payload,'$.event_date') d, json_extract(payload,'$.days_supply_reported') ds
   FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed'
  UNION ALL
  SELECT patient, 'C:'||json_extract(payload,'$.rx_claim_id'), lower(json_extract(payload,'$.drug_name')), json_extract(payload,'$.event_date'), json_extract(payload,'$.days_supply') FROM r
   WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='paid'
     AND json_extract(payload,'$.rx_claim_id') NOT IN (SELECT json_extract(payload,'$.rx_claim_id') FROM r WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='reversed')),
dm AS MATERIALIZED (SELECT patient FROM r WHERE stream='conditions' AND substr(json_extract(payload,'$.icd10'),1,3) IN ('E10','E11','E13')
  UNION SELECT r.patient FROM r, json_each(r.payload,'$.diagnosis_codes') j WHERE r.stream='payer_medical_claims' AND substr(j.value,1,3) IN ('E10','E11','E13'))
SELECT COUNT(DISTINCT patient) AS count FROM fills WHERE d BETWEEN '2029-01-01' AND '2029-12-31' AND (m LIKE '%simvastatin%' OR m LIKE '%atorvastatin%' OR m LIKE '%rosuvastatin%' OR m LIKE '%pravastatin%' OR m LIKE '%lovastatin%' OR m LIKE '%pitavastatin%' OR m LIKE '%fluvastatin%') AND patient IN (SELECT patient FROM dm);
