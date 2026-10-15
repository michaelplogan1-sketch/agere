WITH f AS (
  SELECT patient, event AS d, lower(json_extract(payload,'$.drug_name')) AS drug FROM (SELECT * FROM records WHERE stream='payer_pharmacy_claims' AND id NOT IN (SELECT supersedes FROM records WHERE stream='payer_pharmacy_claims' AND supersedes IS NOT NULL))
  WHERE json_extract(payload,'$.claim_status') = 'paid' AND (lower(json_extract(payload,'$.drug_name')) LIKE '%semaglutide%' OR lower(json_extract(payload,'$.drug_name')) LIKE '%ozempic%' OR lower(json_extract(payload,'$.drug_name')) LIKE '%wegovy%' OR lower(json_extract(payload,'$.drug_name')) LIKE '%rybelsus%')
  UNION
  SELECT patient, event, lower(json_extract(payload,'$.medication_name')) FROM records
  WHERE stream = 'pharmacy_dispenses' AND json_extract(payload,'$.status') = 'dispensed' AND (lower(json_extract(payload,'$.medication_name')) LIKE '%semaglutide%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%ozempic%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%wegovy%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%rybelsus%')
)
SELECT COUNT(*) AS count FROM (SELECT patient, MIN(d) AS first_fill FROM f GROUP BY patient)
WHERE first_fill BETWEEN '2027-01-01' AND '2027-12-31'
