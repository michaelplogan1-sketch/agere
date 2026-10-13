-- COPD spectrum: ICD-10 J43.*/J44.*; SNOMED 13645005, 87433001, 185086009; free text COPD/emphysema/chronic obstructive (not sleep apnea).
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='conditions' AND (
  substr(json_extract(payload,'$.icd10'),1,3) IN ('J43','J44')
  OR json_extract(payload,'$.snomed') IN ('13645005','87433001','185086009')
  OR json_extract(payload,'$.description') LIKE '%COPD%'
  OR lower(json_extract(payload,'$.description')) LIKE '%emphysema%'
  OR lower(json_extract(payload,'$.description')) LIKE '%chronic obstructive%');
