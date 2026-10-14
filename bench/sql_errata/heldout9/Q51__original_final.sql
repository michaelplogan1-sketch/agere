-- Original-roster reading B (final denial): medical_claims claims first adjudicated in 2029 (first row by event_date, non-reversal
-- first, record_id) whose first adjudication is 'denied' and that have no 'paid' row by as-of / all such claims, %, 2 dp.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('medical_claims')),
m AS MATERIALIZED (SELECT json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.status') st, json_extract(payload,'$.event_date') d, json_extract(payload,'$.service_date') sd, json_extract(payload,'$.record_id') rid,
   json_extract(payload,'$.organization_id') payer,
   coalesce(json_extract(payload,'$.financial_deltas_cents.payer'), CASE WHEN json_extract(payload,'$.financial_deltas_cents') IS NULL THEN json_extract(payload,'$.payer_paid_cents') END) paid_c,
   coalesce(json_extract(payload,'$.financial_deltas_cents.allowed'), CASE WHEN json_extract(payload,'$.financial_deltas_cents') IS NULL THEN json_extract(payload,'$.allowed_cents') END) allowed_c,
   json_extract(payload,'$.billed_cents') billed_c FROM r WHERE stream='medical_claims'),
mf AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY cid ORDER BY d, CASE st WHEN 'reversed' THEN 1 ELSE 0 END, rid) rn FROM m WHERE cid IS NOT NULL) WHERE rn=1)
SELECT ROUND(100.0*SUM(mf.st='denied' AND np.npaid=0)/COUNT(*),2) AS value FROM mf JOIN (SELECT cid, SUM(st='paid') npaid FROM m GROUP BY cid) np USING(cid) WHERE mf.d BETWEEN '2029-01-01' AND '2029-12-31';
