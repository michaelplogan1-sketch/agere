WITH pmc_sup AS (SELECT supersedes AS id FROM records WHERE stream='payer_medical_claims' AND supersedes IS NOT NULL),
cur AS (SELECT r.* FROM records r WHERE r.stream='payer_medical_claims' AND r.id NOT IN (SELECT id FROM pmc_sup))
SELECT round(100.0 * sum(json_extract(payload,'$.claim_status') IN ('denied','denied_upheld')) / count(*), 2) AS value FROM cur
WHERE json_extract(payload,'$.service_from') BETWEEN '2029-01-01' AND '2029-12-31' AND json_extract(payload,'$.claim_status') IN ('paid','denied','denied_upheld')
