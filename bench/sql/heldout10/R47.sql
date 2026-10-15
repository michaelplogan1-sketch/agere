WITH stays AS (
  SELECT DISTINCT patient, json_extract(payload,'$.admission_date') AS adm, json_extract(payload,'$.discharge_date') AS dis
  FROM (SELECT * FROM records WHERE stream='payer_medical_claims' AND id NOT IN (SELECT supersedes FROM records WHERE stream='payer_medical_claims' AND supersedes IS NOT NULL))
  WHERE json_extract(payload,'$.type_of_bill') = '0111' AND json_extract(payload,'$.claim_status') = 'paid'
    AND json_extract(payload,'$.admission_date') IS NOT NULL AND json_extract(payload,'$.discharge_date') IS NOT NULL
)
SELECT COUNT(*) AS count FROM stays i
WHERE i.dis BETWEEN '2029-01-01' AND '2029-12-31'
  AND EXISTS (SELECT 1 FROM stays r WHERE r.patient = i.patient AND r.adm > i.dis AND r.adm <= date(i.dis, '+30 days'))
