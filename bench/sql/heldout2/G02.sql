-- Atrial fibrillation: ICD-10 I48.*, SNOMED 49436004, or free-text AF/AFib labels.
SELECT DISTINCT patient AS patient_id FROM records
WHERE stream='conditions' AND (
  json_extract(payload,'$.icd10') LIKE 'I48%'
  OR json_extract(payload,'$.snomed')='49436004'
  OR lower(json_extract(payload,'$.description')) LIKE '%atrial fibrillation%'
  OR lower(json_extract(payload,'$.description')) LIKE '%afib%'
  OR lower(json_extract(payload,'$.description')) LIKE '%a-fib%')
ORDER BY 1;
