-- Heart failure dx (ICD-10 I50.*, SNOMED 88805009/84114007/42343007, free text heart failure/CHF/HFrEF/HFpEF)
-- with a potassium > 5.0 (mmol/L or mEq/L) whose source event date is 2029-01-01..2029-08-15.
WITH hf AS MATERIALIZED (
  SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
    json_extract(payload,'$.icd10') LIKE 'I50%'
    OR json_extract(payload,'$.snomed') IN ('88805009','84114007','42343007')
    OR lower(json_extract(payload,'$.description')) LIKE '%heart failure%'
    OR json_extract(payload,'$.description') LIKE '%CHF%'
    OR lower(json_extract(payload,'$.description')) LIKE '%hfref%'
    OR lower(json_extract(payload,'$.description')) LIKE '%hfpef%')
)
SELECT DISTINCT patient AS patient_id FROM records
WHERE stream='labs' AND patient IN (SELECT patient FROM hf)
  AND json_extract(payload,'$.name')='Potassium' AND json_extract(payload,'$.unit') IN ('mmol/L','mEq/L')
  AND json_type(payload,'$.value') IN ('integer','real') AND json_extract(payload,'$.value') > 5.0
  AND COALESCE(json_extract(payload,'$.result_status'),'') <> 'cancelled'
  AND event BETWEEN '2029-01-01' AND '2029-08-15'
ORDER BY 1;
