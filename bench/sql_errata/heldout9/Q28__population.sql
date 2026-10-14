-- Medical claims, population-wide. Two payer adjudication feeds:
--  medical_claims (original roster): one row per adjudication; dollars from financial_deltas_cents (reversal rows negative); facility
--    DRG claims reported by WH-HOSPITAL-0..3 carry no financial_deltas_cents, so their payer_paid_cents / allowed_cents / billed_cents
--    are used (they are single 'paid' rows, never reversed);
--  payer_medical_claims (population): one row per adjudication_sequence; 'reversed' rows carry negative allowed_amount/paid_amount;
--    a 'voided' row (claim_frequency_code 8, amounts 0) cancels the claim and its replacement arrives as a new claim_id
--    (frequency 7, '-R' suffix).
-- Medical allowed-amount PMPM for 2029 (incurred basis), population-wide:
--  numerator = allowed dollars for services dated 2029: medical_claims allowed deltas (facility rows: allowed_cents) by service_date +
--    payer_medical_claims allowed_amount by service_from (reversal rows net; voided claim_ids excluded, replacements count);
--  denominator = member-months = distinct (patient, calendar month of 2029) where an enrollment_spans row covers any day of the month
--    (coverage_start <= month end and coverage_end null or >= month start). Pharmacy excluded. Dollars, 2 dp.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('medical_claims','payer_medical_claims','enrollment_spans')),
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
mo(ms) AS (SELECT '2029-01-01' UNION ALL SELECT date(ms,'+1 month') FROM mo WHERE ms<'2029-12-01'),
mm AS (SELECT COUNT(*) n FROM (SELECT DISTINCT e.patient, mo.ms FROM r e JOIN mo ON e.stream='enrollment_spans'
   AND json_extract(e.payload,'$.coverage_start')<=date(mo.ms,'+1 month','-1 day')
   AND (json_extract(e.payload,'$.coverage_end') IS NULL OR json_extract(e.payload,'$.coverage_end')>=mo.ms)))
SELECT ROUND(((SELECT coalesce(sum(allowed_c),0) FROM m WHERE sd BETWEEN '2029-01-01' AND '2029-12-31') + (SELECT coalesce(sum(allowed_c),0) FROM p WHERE sd BETWEEN '2029-01-01' AND '2029-12-31' AND cid NOT IN (SELECT cid FROM pv)))/100.0
  / (SELECT n FROM mm),2) AS value;
