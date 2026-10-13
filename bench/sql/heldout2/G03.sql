-- CKD stage 3 or worse (3, 3a, 3b, 4, 5, ESRD) across ICD-10, SNOMED and free text. Unstaged CKD excluded.
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='conditions' AND (
  json_extract(payload,'$.icd10') LIKE 'N18.3%' OR json_extract(payload,'$.icd10') IN ('N18.4','N18.5','N18.6')
  OR json_extract(payload,'$.snomed') IN ('433144002','431857002','433146000','46177005','700378005','700379002')
  OR lower(json_extract(payload,'$.description')) LIKE '%ckd stage 3%'
  OR lower(json_extract(payload,'$.description')) LIKE '%ckd stage 4%'
  OR lower(json_extract(payload,'$.description')) LIKE '%ckd stage 5%'
  OR lower(json_extract(payload,'$.description')) LIKE '%kidney disease stage 3%'
  OR lower(json_extract(payload,'$.description')) LIKE '%kidney disease stage 4%'
  OR lower(json_extract(payload,'$.description')) LIKE '%kidney disease stage 5%'
  OR lower(json_extract(payload,'$.description')) LIKE '%kidney disease, stage 3%'
  OR lower(json_extract(payload,'$.description')) LIKE '%kidney disease, stage 4%'
  OR lower(json_extract(payload,'$.description')) LIKE '%kidney disease, stage 5%'
  OR lower(json_extract(payload,'$.description')) LIKE '%end-stage renal%'
  OR lower(json_extract(payload,'$.description')) LIKE '%end stage renal%');
