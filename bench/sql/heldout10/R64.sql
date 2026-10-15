WITH v AS (
  SELECT patient, event AS d FROM records WHERE stream IN ('encounters', 'adt_messages', 'adt', 'medical_claims')
  UNION
  SELECT patient, json_extract(payload,'$.service_from') FROM (SELECT * FROM records WHERE stream='payer_medical_claims' AND id NOT IN (SELECT supersedes FROM records WHERE stream='payer_medical_claims' AND supersedes IS NOT NULL))
)
SELECT COUNT(*) AS count FROM (SELECT DISTINCT patient, d FROM v WHERE d BETWEEN '2030-03-01' AND '2030-03-31')
