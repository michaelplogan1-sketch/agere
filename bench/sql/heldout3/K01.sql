-- Heart failure on the condition list, any coding: SNOMED 88805009, ICD-10 I50.*, or a description containing 'heart failure' or 'CHF'. Distinct patients. Condition records are undated.
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='conditions' AND (
  json_extract(payload,'$.snomed')='88805009'
  OR json_extract(payload,'$.icd10') LIKE 'I50%'
  OR lower(json_extract(payload,'$.description')) LIKE '%heart failure%'
  OR json_extract(payload,'$.description') LIKE '%CHF%');
