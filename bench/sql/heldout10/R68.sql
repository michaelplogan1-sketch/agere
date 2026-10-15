SELECT lower(trim(json_extract(payload,'$.drug_name'))) AS drug, COUNT(DISTINCT patient) AS patients
FROM (SELECT * FROM records WHERE stream='payer_pharmacy_claims' AND id NOT IN (SELECT supersedes FROM records WHERE stream='payer_pharmacy_claims' AND supersedes IS NOT NULL))
WHERE json_extract(payload,'$.claim_status') = 'paid' AND event BETWEEN '2029-01-01' AND '2029-12-31'
GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5
