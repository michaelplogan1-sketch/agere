-- Per payer: 2028-service claims, first-adjudication denials, rate.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('medical_claims')), mc AS (SELECT json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.status') st, json_extract(payload,'$.service_date') sd, json_extract(payload,'$.event_date') ed, json_extract(payload,'$.available_time') at, json_extract(payload,'$.organization_id') payer, json_extract(payload,'$.payer_paid_cents') paid FROM r WHERE stream='medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL),
f AS (SELECT cid, payer, st, row_number() OVER (PARTITION BY cid ORDER BY at, ed) rn FROM mc WHERE sd BETWEEN '2028-01-01' AND '2028-12-31')
SELECT payer, COUNT(*) AS claims, SUM(st='denied') AS initially_denied, round(100.0*SUM(st='denied')/COUNT(*),1) AS denial_rate_pct
FROM f WHERE rn=1 GROUP BY payer ORDER BY payer;
