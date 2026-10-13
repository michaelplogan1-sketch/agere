-- 2028 payer_cash_allocations netted (negatives = recoupments), payer via claim_id -> medical_claims.organization_id.
WITH r AS MATERIALIZED (SELECT stream, payload FROM records WHERE stream IN ('payer_cash_allocations','medical_claims')),
p AS (SELECT json_extract(payload,'$.claim_id') cid, max(json_extract(payload,'$.organization_id')) payer FROM r WHERE stream='medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL GROUP BY 1),
a AS (SELECT json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.allocated_cents') c FROM r WHERE stream='payer_cash_allocations' AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31')
SELECT p.payer AS payer, count(*) AS allocations, round(sum(a.c)/100.0,2) AS net_cash_dollars FROM a JOIN p USING(cid) GROUP BY 1 ORDER BY 1;
