WITH ppc_sup AS (SELECT supersedes AS id FROM records WHERE stream='payer_pharmacy_claims' AND supersedes IS NOT NULL)
SELECT round(sum(json_extract(payload,'$.plan_paid')), 2) AS value FROM records WHERE stream='payer_pharmacy_claims'
AND json_extract(payload,'$.claim_status')='paid' AND id NOT IN (SELECT id FROM ppc_sup) AND event BETWEEN '2029-01-01' AND '2029-12-31'
AND json_extract(payload,'$.plan_paid') IS NOT NULL
