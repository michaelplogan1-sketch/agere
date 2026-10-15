WITH f AS (
  SELECT patient, event AS d, lower(json_extract(payload,'$.drug_name')) AS drug FROM (SELECT * FROM records WHERE stream='payer_pharmacy_claims' AND id NOT IN (SELECT supersedes FROM records WHERE stream='payer_pharmacy_claims' AND supersedes IS NOT NULL))
  WHERE json_extract(payload,'$.claim_status') = 'paid' AND (lower(json_extract(payload,'$.drug_name')) LIKE '%empagliflozin%' OR lower(json_extract(payload,'$.drug_name')) LIKE '%jardiance%' OR lower(json_extract(payload,'$.drug_name')) LIKE '%dapagliflozin%' OR lower(json_extract(payload,'$.drug_name')) LIKE '%farxiga%' OR lower(json_extract(payload,'$.drug_name')) LIKE '%canagliflozin%' OR lower(json_extract(payload,'$.drug_name')) LIKE '%invokana%' OR lower(json_extract(payload,'$.drug_name')) LIKE '%ertugliflozin%')
  UNION
  SELECT patient, event, lower(json_extract(payload,'$.medication_name')) FROM records
  WHERE stream = 'pharmacy_dispenses' AND json_extract(payload,'$.status') = 'dispensed' AND (lower(json_extract(payload,'$.medication_name')) LIKE '%empagliflozin%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%jardiance%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%dapagliflozin%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%farxiga%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%canagliflozin%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%invokana%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%ertugliflozin%')
),
nu AS (SELECT patient, MIN(d) AS i FROM f GROUP BY patient HAVING MIN(d) BETWEEN '2027-01-01' AND '2028-12-31'),
hf AS (
  SELECT patient, COALESCE(event, json_extract(payload,'$.onset')) AS d FROM (SELECT * FROM records WHERE stream='conditions' AND id NOT IN (SELECT supersedes FROM records WHERE stream='conditions' AND supersedes IS NOT NULL)) WHERE upper(replace(json_extract(payload,'$.icd10'), '.', '')) LIKE 'I50%'
  UNION
  SELECT r.patient, r.event FROM records r, json_each(r.payload, '$.diagnoses') x
  WHERE r.stream = 'encounters' AND upper(replace(json_extract(x.value, '$.icd10'), '.', '')) LIKE 'I50%'
  UNION
  SELECT c.patient, json_extract(c.payload,'$.service_from') FROM (SELECT * FROM records WHERE stream='payer_medical_claims' AND id NOT IN (SELECT supersedes FROM records WHERE stream='payer_medical_claims' AND supersedes IS NOT NULL)) c, json_each(c.payload, '$.diagnosis_codes') x
  WHERE upper(replace(x.value, '.', '')) LIKE 'I50%'
)
SELECT COUNT(*) AS count FROM nu WHERE EXISTS (SELECT 1 FROM hf WHERE hf.patient = nu.patient AND hf.d IS NOT NULL AND hf.d <= nu.i)
