WITH pmc_sup AS (SELECT supersedes AS id FROM records WHERE stream='payer_medical_claims' AND supersedes IS NOT NULL),
cur AS (SELECT r.* FROM records r WHERE r.stream='payer_medical_claims' AND r.id NOT IN (SELECT id FROM pmc_sup))
SELECT round(avg(julianday(json_extract(payload,'$.paid_date')) - julianday(json_extract(payload,'$.claim_received_date'))), 2) AS value FROM cur
WHERE json_extract(payload,'$.claim_status')='paid' AND json_extract(payload,'$.paid_amount') > 0
  AND json_extract(payload,'$.paid_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_extract(payload,'$.claim_received_date') IS NOT NULL
