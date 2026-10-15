-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Reading B as written: statin named on an undated legacy medication_lists snapshot row (no event_date).
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('medication_lists')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup))
SELECT COUNT(DISTINCT patient) AS count FROM r WHERE json_extract(payload,'$.event_date') IS NULL AND (lower(json_extract(payload,'$.name')) LIKE '%simvastatin%' OR lower(json_extract(payload,'$.name')) LIKE '%atorvastatin%' OR lower(json_extract(payload,'$.name')) LIKE '%rosuvastatin%' OR lower(json_extract(payload,'$.name')) LIKE '%pravastatin%' OR lower(json_extract(payload,'$.name')) LIKE '%lovastatin%' OR lower(json_extract(payload,'$.name')) LIKE '%pitavastatin%' OR lower(json_extract(payload,'$.name')) LIKE '%fluvastatin%');
