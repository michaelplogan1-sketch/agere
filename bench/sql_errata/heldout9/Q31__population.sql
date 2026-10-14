-- 2029 inpatient discharges by MS-DRG from facility claims, population-wide: medical_claims rows carrying ms_drg (non-'reversed')
-- and payer_medical_claims rows carrying ms_drg (non-'reversed', voided claim_ids excluded), discharge_date 2029-01-01..2029-12-31,
-- distinct claim_id per DRG. Grouped by MS-DRG code because payer claims carry the code without a title (titles exist only on
-- medical_claims for 13 codes). Columns ms_drg, claims.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('medical_claims','payer_medical_claims')),
pv AS MATERIALIZED (SELECT DISTINCT json_extract(payload,'$.payer_claim_id') cid FROM r WHERE stream='payer_medical_claims' AND json_extract(payload,'$.claim_status')='voided'),
d AS (SELECT json_extract(payload,'$.ms_drg') drg, json_extract(payload,'$.claim_id') cid FROM r WHERE stream='medical_claims' AND json_extract(payload,'$.ms_drg') IS NOT NULL AND json_extract(payload,'$.status')<>'reversed' AND json_extract(payload,'$.discharge_date') BETWEEN '2029-01-01' AND '2029-12-31'
  UNION SELECT json_extract(payload,'$.ms_drg'), json_extract(payload,'$.payer_claim_id') FROM r WHERE stream='payer_medical_claims' AND json_extract(payload,'$.ms_drg') IS NOT NULL AND json_extract(payload,'$.claim_status')<>'reversed'
    AND json_extract(payload,'$.discharge_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_extract(payload,'$.payer_claim_id') NOT IN (SELECT cid FROM pv))
SELECT drg AS ms_drg, COUNT(DISTINCT cid) AS claims FROM d GROUP BY drg ORDER BY drg;
