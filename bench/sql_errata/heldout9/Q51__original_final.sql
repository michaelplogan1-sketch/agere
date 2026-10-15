-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Original-roster reading B: medical_claims claims first adjudicated in 2029 whose first adjudication is 'denied' and that have no
-- 'paid' row by as-of / all such claims, %, 2 dp.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('medical_claims')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup)),
m AS MATERIALIZED (SELECT patient, json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.status') st, json_extract(payload,'$.event_date') d, json_extract(payload,'$.service_date') sd, json_extract(payload,'$.record_id') rid,
   json_extract(payload,'$.organization_id') payer,
   coalesce(json_extract(payload,'$.financial_deltas_cents.payer'), CASE WHEN json_extract(payload,'$.financial_deltas_cents') IS NULL THEN json_extract(payload,'$.payer_paid_cents') END) paid_c,
   coalesce(json_extract(payload,'$.financial_deltas_cents.allowed'), CASE WHEN json_extract(payload,'$.financial_deltas_cents') IS NULL THEN json_extract(payload,'$.allowed_cents') END) allowed_c,
   json_extract(payload,'$.billed_cents') billed_c FROM r WHERE stream='medical_claims'),
mf AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY cid ORDER BY d, CASE st WHEN 'reversed' THEN 1 ELSE 0 END, rid) rn FROM m WHERE cid IS NOT NULL) WHERE rn=1)
SELECT ROUND(100.0*SUM(mf.st='denied' AND np.npaid=0)/COUNT(*),2) AS value FROM mf JOIN (SELECT cid, SUM(st='paid') npaid FROM m GROUP BY cid) np USING(cid) WHERE mf.d BETWEEN '2029-01-01' AND '2029-12-31';
