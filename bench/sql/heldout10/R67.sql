WITH f AS (
  SELECT patient, event AS d, lower(json_extract(payload,'$.drug_name')) AS drug FROM (SELECT * FROM records WHERE stream='payer_pharmacy_claims' AND id NOT IN (SELECT supersedes FROM records WHERE stream='payer_pharmacy_claims' AND supersedes IS NOT NULL))
  WHERE json_extract(payload,'$.claim_status') = 'paid' AND (lower(json_extract(payload,'$.drug_name')) LIKE '%empagliflozin%' OR lower(json_extract(payload,'$.drug_name')) LIKE '%jardiance%')
  UNION
  SELECT patient, event, lower(json_extract(payload,'$.medication_name')) FROM records
  WHERE stream = 'pharmacy_dispenses' AND json_extract(payload,'$.status') = 'dispensed' AND (lower(json_extract(payload,'$.medication_name')) LIKE '%empagliflozin%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%jardiance%')
),
idx AS (SELECT patient, MIN(d) AS i FROM f GROUP BY patient)
SELECT ROUND(100.0 * SUM(EXISTS (SELECT 1 FROM f WHERE f.patient = idx.patient AND f.d >= date(idx.i, '+365 days'))) / COUNT(*), 1) AS value
FROM idx WHERE i BETWEEN '2027-01-01' AND '2027-12-31'
