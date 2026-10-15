-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Reading B on scheduled_date-bearing (original-roster) appointments only: no-shows / (kept + no-shows). 2 dp.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('appointments')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup)),
a AS MATERIALIZED (SELECT json_extract(payload,'$.appointment_id') aid, json_extract(payload,'$.status') st, json_extract(payload,'$.scheduled_date') sd, json_extract(payload,'$.event_date') d, json_extract(payload,'$.record_id') rid FROM r),
s AS (SELECT aid, sd FROM (SELECT aid, sd, row_number() OVER (PARTITION BY aid ORDER BY d DESC, rid DESC) rn FROM a WHERE sd IS NOT NULL) WHERE rn=1),
o AS (SELECT aid, MAX(st IN ('fulfilled','attended','arrived')) kept, MAX(st='no_show') ns FROM a GROUP BY aid)
SELECT ROUND(100.0*SUM(ns=1 AND kept=0)/SUM(kept=1 OR ns=1),2) AS value FROM o JOIN s USING(aid) WHERE s.sd BETWEEN '2029-01-01' AND '2029-12-31';
