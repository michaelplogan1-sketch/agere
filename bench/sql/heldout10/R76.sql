SELECT ROUND(100.0 * SUM(julianday(substr(arrival, 1, 10)) - julianday(json_extract(payload,'$.service_from')) > 30) / COUNT(*), 2) AS value
FROM (SELECT * FROM records WHERE stream='payer_medical_claims' AND id NOT IN (SELECT supersedes FROM records WHERE stream='payer_medical_claims' AND supersedes IS NOT NULL)) WHERE json_extract(payload,'$.service_from') IS NOT NULL
