-- Population = every patient in the patients view (original deep-chart roster + population members).
-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Pharmacy fills:
--  pharmacy_dispenses (original roster): only 'dispensed' rows are pickups ('prepared' / 'returned_to_stock' are not); one fill per
--    fill_id (record_id when absent); date = event_date;
--  payer_pharmacy_claims: a fill is an rx_claim_id with a 'paid' row, picked up only when no 'reversed' row (return to stock) exists for
--    it; date = event_date of the paid row. A payer claim for the same patient, date and drug name as a dispensed row is the payer's copy
--    of that fill and is not counted again.
-- Patients whose statin pickups dated 2029 sum to >= 180 days' supply (one per fill).
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('pharmacy_dispenses','payer_pharmacy_claims')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup)),
dsp AS MATERIALIZED (SELECT DISTINCT patient, 'D:'||coalesce(json_extract(payload,'$.fill_id'),json_extract(payload,'$.record_id')) fid, lower(json_extract(payload,'$.medication_name')) m, json_extract(payload,'$.event_date') d, json_extract(payload,'$.days_supply_reported') ds
   FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed'),
fills AS MATERIALIZED (SELECT * FROM dsp
  UNION ALL
  SELECT patient, 'C:'||json_extract(payload,'$.rx_claim_id'), lower(json_extract(payload,'$.drug_name')), json_extract(payload,'$.event_date'), json_extract(payload,'$.days_supply') FROM r x
   WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='paid'
     AND json_extract(payload,'$.rx_claim_id') NOT IN (SELECT json_extract(payload,'$.rx_claim_id') FROM r WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='reversed')
     AND NOT EXISTS (SELECT 1 FROM dsp WHERE dsp.patient=x.patient AND dsp.d=json_extract(x.payload,'$.event_date') AND dsp.m=lower(json_extract(x.payload,'$.drug_name'))))
SELECT patient AS patient_id FROM fills WHERE d BETWEEN '2029-01-01' AND '2029-12-31' AND (m LIKE '%simvastatin%' OR m LIKE '%atorvastatin%' OR m LIKE '%rosuvastatin%' OR m LIKE '%pravastatin%' OR m LIKE '%lovastatin%' OR m LIKE '%pitavastatin%' OR m LIKE '%fluvastatin%') GROUP BY patient HAVING SUM(ds)>=180 ORDER BY 1;
