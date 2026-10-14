-- Medical claims, population-wide. Two payer adjudication feeds:
--  medical_claims (original roster): one row per adjudication; dollars from financial_deltas_cents (reversal rows negative); facility
--    DRG claims reported by WH-HOSPITAL-0..3 carry no financial_deltas_cents, so their payer_paid_cents / allowed_cents / billed_cents
--    are used (they are single 'paid' rows, never reversed);
--  payer_medical_claims (population): one row per adjudication_sequence; 'reversed' rows carry negative allowed_amount/paid_amount;
--    a 'voided' row (claim_frequency_code 8, amounts 0) cancels the claim and its replacement arrives as a new claim_id
--    (frequency 7, '-R' suffix).
-- Claim = medical_claims claim_id (first row by event_date, non-reversal first, record_id) or an original payer_medical_claims
-- submission (claim_frequency_code 1 at adjudication_sequence 1; frequency-7 '-R' replacements are corrections, not new claims).
-- Dollar-weighted initial denial rate (%) for claims first adjudicated in 2029: 100 * billed of claims whose first adjudication
-- is 'denied' / billed of all such claims (billed_cents; payer total_charge in cents). 2 dp.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('medical_claims','payer_medical_claims')),
m AS MATERIALIZED (SELECT json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.status') st, json_extract(payload,'$.event_date') d, json_extract(payload,'$.service_date') sd, json_extract(payload,'$.record_id') rid,
   json_extract(payload,'$.organization_id') payer,
   coalesce(json_extract(payload,'$.financial_deltas_cents.payer'), CASE WHEN json_extract(payload,'$.financial_deltas_cents') IS NULL THEN json_extract(payload,'$.payer_paid_cents') END) paid_c,
   coalesce(json_extract(payload,'$.financial_deltas_cents.allowed'), CASE WHEN json_extract(payload,'$.financial_deltas_cents') IS NULL THEN json_extract(payload,'$.allowed_cents') END) allowed_c,
   json_extract(payload,'$.billed_cents') billed_c FROM r WHERE stream='medical_claims'),
p AS MATERIALIZED (SELECT json_extract(payload,'$.payer_claim_id') cid, json_extract(payload,'$.adjudication_sequence') seq, json_extract(payload,'$.claim_frequency_code') freq, json_extract(payload,'$.claim_status') st,
   json_extract(payload,'$.event_date') d, json_extract(payload,'$.service_from') sd, json_extract(payload,'$.payer_id') payer, round(json_extract(payload,'$.paid_amount')*100) paid_c, round(json_extract(payload,'$.allowed_amount')*100) allowed_c,
   round(json_extract(payload,'$.total_charge')*100) billed_c FROM r WHERE stream='payer_medical_claims'),
pc AS MATERIALIZED (SELECT *, CASE WHEN st='voided' THEN -coalesce(sum(paid_c) OVER (PARTITION BY cid ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0)
   ELSE paid_c END cash_c FROM p),
pv AS MATERIALIZED (SELECT DISTINCT cid FROM p WHERE st='voided'),
mf AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY cid ORDER BY d, CASE st WHEN 'reversed' THEN 1 ELSE 0 END, rid) rn FROM m WHERE cid IS NOT NULL) WHERE rn=1),
pf AS (SELECT * FROM p WHERE seq=1 AND freq='1'),
np AS MATERIALIZED (SELECT cid, SUM(st='paid') npaid FROM m WHERE cid IS NOT NULL GROUP BY cid UNION ALL SELECT cid, SUM(st='paid') FROM p GROUP BY cid),
f AS MATERIALIZED (SELECT x.cid, x.st, x.d, x.billed_c, np.npaid FROM (SELECT cid, st, d, billed_c FROM mf UNION ALL SELECT cid, st, d, billed_c FROM pf) x JOIN np USING(cid))
SELECT ROUND(100.0*SUM(CASE WHEN st='denied' THEN billed_c ELSE 0 END)/SUM(billed_c),2) AS value FROM f WHERE d BETWEEN '2029-01-01' AND '2029-12-31';
