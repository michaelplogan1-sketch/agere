WITH pmc_sup AS (SELECT supersedes AS id FROM records WHERE stream='payer_medical_claims' AND supersedes IS NOT NULL),
cur AS (SELECT r.* FROM records r WHERE r.stream='payer_medical_claims' AND r.id NOT IN (SELECT id FROM pmc_sup))
SELECT json_extract(payload,'$.carc') AS carc, COUNT(*) AS claims FROM cur
WHERE json_extract(payload,'$.service_from') BETWEEN '2029-01-01' AND '2029-12-31' AND json_extract(payload,'$.claim_status') IN ('denied','denied_upheld')
GROUP BY 1 ORDER BY 2 DESC LIMIT 5
