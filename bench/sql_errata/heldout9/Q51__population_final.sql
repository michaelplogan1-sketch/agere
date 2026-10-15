-- Population = every patient in the patients view (original deep-chart roster + population members).
-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Medical claims:
--  medical_claims (original roster, provider remittance view): one row per adjudication; dollars = financial_deltas_cents (reversal
--    rows negative); facility DRG rows (WH-HOSPITAL-n) carry no financial_deltas_cents, so payer_paid_cents / allowed_cents /
--    billed_cents are used for them;
--  payer_medical_claims (payer feed): one row per adjudication_sequence; 'reversed' rows carry negative amounts; 'recouped' rows carry
--    the recovered payment as a negative paid_amount (allowed unchanged, so it adds 0 allowed); a 'voided' row (frequency 8, amount 0)
--    cancels the claim (its prior net payment is reversed on the void date) and a frequency-7 '-R' replacement carries the payment.
--  A payer claim that is the payer's copy of a medical_claims claim (same patient, service_from = service_date, total_charge =
--  billed amount) is dropped; medical_claims is kept for it. Other payer claims (population members, and claims for original patients
--  from providers outside our billing) count.
-- Claim = medical_claims claim_id (first row by event_date, non-reversal first, record_id) or an original payer_medical_claims
-- submission (claim_frequency_code 1 at adjudication_sequence 1; frequency-7 '-R' replacements are corrections, not new claims).
-- Reading B (final denial): first adjudication 'denied' and no 'paid' adjudication by as-of / all claims first adjudicated in 2029. 2 dp.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('medical_claims','payer_medical_claims')),
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
p0 AS MATERIALIZED (SELECT patient, json_extract(payload,'$.payer_claim_id') cid, json_extract(payload,'$.adjudication_sequence') seq, json_extract(payload,'$.claim_frequency_code') freq, json_extract(payload,'$.claim_status') st,
   json_extract(payload,'$.event_date') d, json_extract(payload,'$.service_from') sd, json_extract(payload,'$.payer_id') payer, round(json_extract(payload,'$.paid_amount')*100) paid_c,
   CASE WHEN json_extract(payload,'$.claim_status')='recouped' THEN 0 ELSE round(json_extract(payload,'$.allowed_amount')*100) END allowed_c,
   round(json_extract(payload,'$.total_charge')*100) billed_c, json_extract(payload,'$.ms_drg') drg, json_extract(payload,'$.discharge_date') dd FROM r WHERE stream='payer_medical_claims'),
mk AS MATERIALIZED (SELECT DISTINCT patient, sd, billed_c FROM m WHERE sd IS NOT NULL),
pk AS MATERIALIZED (SELECT cid, patient, min(sd) sd, max(billed_c) b FROM p0 GROUP BY cid),
pdup AS MATERIALIZED (SELECT pk.cid FROM pk JOIN mk ON mk.patient=pk.patient AND mk.sd=pk.sd AND mk.billed_c=pk.b),
p AS MATERIALIZED (SELECT * FROM p0 WHERE cid NOT IN (SELECT cid FROM pdup)),
pc AS MATERIALIZED (SELECT *, CASE WHEN st='voided' THEN -coalesce(sum(paid_c) OVER (PARTITION BY cid ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0)
   ELSE paid_c END cash_c FROM p),
pv AS MATERIALIZED (SELECT DISTINCT cid FROM p WHERE st='voided'),
mf AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY cid ORDER BY d, CASE st WHEN 'reversed' THEN 1 ELSE 0 END, rid) rn FROM m WHERE cid IS NOT NULL) WHERE rn=1),
pf AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY cid ORDER BY d) rn FROM p WHERE seq=1 AND freq='1') WHERE rn=1),
np AS MATERIALIZED (SELECT cid, SUM(st='paid') npaid FROM m WHERE cid IS NOT NULL GROUP BY cid UNION ALL SELECT cid, SUM(st='paid') FROM p GROUP BY cid),
f AS MATERIALIZED (SELECT x.cid, x.st, x.d, x.billed_c, np.npaid FROM (SELECT cid, st, d, billed_c FROM mf UNION ALL SELECT cid, st, d, billed_c FROM pf) x JOIN np USING(cid))
SELECT ROUND(100.0*SUM(st='denied' AND npaid=0)/COUNT(*),2) AS value FROM f WHERE d BETWEEN '2029-01-01' AND '2029-12-31';
