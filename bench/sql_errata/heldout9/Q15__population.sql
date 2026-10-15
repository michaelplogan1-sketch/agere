-- Population = every patient in the patients view (original deep-chart roster + population members).
-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Pharmacy claims rejected as refill too soon in 2029: distinct claim_id of pharmacy_claim_transactions 'rejected' / REFILL_TOO_SOON +
-- distinct rx_claim_id of payer_pharmacy_claims 'rejected' with NCPDP reject code '79' (Refill Too Soon), event_date 2029.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('pharmacy_claim_transactions','payer_pharmacy_claims')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup))
SELECT a.n + b.n AS count FROM (SELECT COUNT(DISTINCT json_extract(payload,'$.claim_id')) n FROM r WHERE stream='pharmacy_claim_transactions' AND json_extract(payload,'$.transaction_status')='rejected' AND json_extract(payload,'$.reject_code')='REFILL_TOO_SOON' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31') a,
  (SELECT COUNT(DISTINCT json_extract(payload,'$.rx_claim_id')) n FROM r WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='rejected' AND json_extract(payload,'$.reject_code') IN ('79','REFILL_TOO_SOON') AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31') b;
