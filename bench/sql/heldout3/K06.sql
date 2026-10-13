-- Obesity on the condition list: SNOMED 162864005 (BMI 30+) or 408512008 (BMI 40+), ICD-10 E66.* except E66.3 (overweight),
-- or a description containing 'obes' -- excluding history-of entries (e.g. 'Obesity, history of, post-bariatric surgery', Z98.84) and 'Overweight'.
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='conditions' AND lower(coalesce(json_extract(payload,'$.description'),'')) NOT LIKE '%history of%' AND (
  json_extract(payload,'$.snomed') IN ('162864005','408512008')
  OR (json_extract(payload,'$.icd10') LIKE 'E66%' AND json_extract(payload,'$.icd10') <> 'E66.3')
  OR lower(json_extract(payload,'$.description')) LIKE '%obes%');
