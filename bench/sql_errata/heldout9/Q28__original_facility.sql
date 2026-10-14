-- Original-roster reading with facility DRG claims: allowed (financial_deltas_cents.allowed; facility rows allowed_cents) over
-- medical_claims with service_date in 2029 / (12 x patients with an 'active' coverage row). 2 dp.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('medical_claims','coverage')),
m AS MATERIALIZED (SELECT json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.status') st, json_extract(payload,'$.event_date') d, json_extract(payload,'$.service_date') sd, json_extract(payload,'$.record_id') rid,
   json_extract(payload,'$.organization_id') payer,
   coalesce(json_extract(payload,'$.financial_deltas_cents.payer'), CASE WHEN json_extract(payload,'$.financial_deltas_cents') IS NULL THEN json_extract(payload,'$.payer_paid_cents') END) paid_c,
   coalesce(json_extract(payload,'$.financial_deltas_cents.allowed'), CASE WHEN json_extract(payload,'$.financial_deltas_cents') IS NULL THEN json_extract(payload,'$.allowed_cents') END) allowed_c,
   json_extract(payload,'$.billed_cents') billed_c FROM r WHERE stream='medical_claims')
SELECT ROUND((SELECT sum(allowed_c)/100.0 FROM m WHERE sd BETWEEN '2029-01-01' AND '2029-12-31') / (12.0*(SELECT COUNT(DISTINCT patient) FROM r WHERE stream='coverage' AND json_extract(payload,'$.status')='active')),2) AS value;
