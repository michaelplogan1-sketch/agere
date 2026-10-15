-- Population = every patient in the patients view (original deep-chart roster + population members).
-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Net pharmacy claim payer spend in 2029: pharmacy_claim_transactions payer_paid_delta_cents dated 2029 (reversals negative) +
-- payer_pharmacy_claims plan_paid of 'paid' rows dated 2029 + 'recouped' rows' plan_paid (negative) dated 2029 - for 'reversed' rows
-- dated 2029 the plan_paid of the reversed claim's paid row (payer reversal rows carry 0). Payer copies of dispensed fills carry no
-- plan_paid and add nothing. Dollars, 2 dp.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('pharmacy_claim_transactions','payer_pharmacy_claims')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup)),
pp AS MATERIALIZED (SELECT json_extract(payload,'$.rx_claim_id') cid, json_extract(payload,'$.claim_status') st, json_extract(payload,'$.event_date') d, coalesce(json_extract(payload,'$.plan_paid'),0) paid FROM r WHERE stream='payer_pharmacy_claims'),
orig AS MATERIALIZED (SELECT cid, sum(paid) paid FROM pp WHERE st='paid' GROUP BY cid)
SELECT ROUND(a.v/100.0 + b.v + c.v - d.v,2) AS value FROM
  (SELECT coalesce(sum(json_extract(payload,'$.payer_paid_delta_cents')),0) v FROM r WHERE stream='pharmacy_claim_transactions' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31') a,
  (SELECT coalesce(sum(paid),0) v FROM pp WHERE st='paid' AND d BETWEEN '2029-01-01' AND '2029-12-31') b,
  (SELECT coalesce(sum(paid),0) v FROM pp WHERE st='recouped' AND d BETWEEN '2029-01-01' AND '2029-12-31') c,
  (SELECT coalesce(sum(o.paid),0) v FROM pp x JOIN orig o USING(cid) WHERE x.st='reversed' AND x.d BETWEEN '2029-01-01' AND '2029-12-31') d;
