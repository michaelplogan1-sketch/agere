-- Pharmacy fills, population-wide:
--  pharmacy_dispenses (original roster): only status 'dispensed' rows are pickups ('prepared' / 'returned_to_stock' are not);
--    one fill per fill_id (record_id when absent); date = event_date;
--  payer_pharmacy_claims (population): a fill is an rx_claim_id with a 'paid' row; it is picked up only when no 'reversed' row
--    (transaction B2, reversal_reason return_to_stock) exists for it by the as-of date; date = event_date of the paid row.
-- Picked-up fills dated 2029 by class (first match: statin, ace_inhibitor, arb, loop_diuretic, beta_blocker, p2y12_inhibitor,
-- metformin; lisinopril/HCTZ = ace_inhibitor, sacubitril/valsartan = arb). Columns drug_class, fills.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('pharmacy_dispenses','payer_pharmacy_claims')),
fills AS MATERIALIZED (
  SELECT DISTINCT patient, 'D:'||coalesce(json_extract(payload,'$.fill_id'),json_extract(payload,'$.record_id')) fid, lower(json_extract(payload,'$.medication_name')) m, json_extract(payload,'$.event_date') d, json_extract(payload,'$.days_supply_reported') ds
   FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed'
  UNION ALL
  SELECT patient, 'C:'||json_extract(payload,'$.rx_claim_id'), lower(json_extract(payload,'$.drug_name')), json_extract(payload,'$.event_date'), json_extract(payload,'$.days_supply') FROM r
   WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='paid'
     AND json_extract(payload,'$.rx_claim_id') NOT IN (SELECT json_extract(payload,'$.rx_claim_id') FROM r WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='reversed')),
c AS (SELECT fid, CASE
  WHEN (m LIKE '%simvastatin%' OR m LIKE '%atorvastatin%' OR m LIKE '%rosuvastatin%' OR m LIKE '%pravastatin%' OR m LIKE '%lovastatin%' OR m LIKE '%pitavastatin%' OR m LIKE '%fluvastatin%') THEN 'statin'
  WHEN m LIKE '%lisinopril%' OR m LIKE '%enalapril%' OR m LIKE '%benazepril%' OR m LIKE '%quinapril%' OR m LIKE '%ramipril%' THEN 'ace_inhibitor'
  WHEN m LIKE '%losartan%' OR m LIKE '%valsartan%' OR m LIKE '%irbesartan%' THEN 'arb'
  WHEN m LIKE '%furosemide%' OR m LIKE '%bumetanide%' OR m LIKE '%torsemide%' THEN 'loop_diuretic'
  WHEN m LIKE '%carvedilol%' OR m LIKE '%metoprolol%' OR m LIKE '%atenolol%' OR m LIKE '%bisoprolol%' OR m LIKE '%propranolol%' OR m LIKE '%labetalol%' THEN 'beta_blocker'
  WHEN m LIKE '%clopidogrel%' OR m LIKE '%prasugrel%' OR m LIKE '%ticagrelor%' THEN 'p2y12_inhibitor'
  WHEN m LIKE '%metformin%' THEN 'metformin' END cls FROM fills WHERE d BETWEEN '2029-01-01' AND '2029-12-31')
SELECT cls AS drug_class, COUNT(DISTINCT fid) AS fills FROM c WHERE cls IS NOT NULL GROUP BY cls ORDER BY cls;
