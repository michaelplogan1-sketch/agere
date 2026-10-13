-- Asthma or COPD registry: condition list entry with ICD-10 J45.* (asthma) or J44.*/J43.* (COPD/emphysema), or description containing
-- 'asthma', 'COPD', 'chronic obstructive' or 'emphysema'. Acute bronchitis is not included.
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='conditions' AND (
  substr(json_extract(payload,'$.icd10'),1,3) IN ('J43','J44','J45')
  OR lower(json_extract(payload,'$.description')) LIKE '%asthma%'
  OR json_extract(payload,'$.description') LIKE '%COPD%'
  OR lower(json_extract(payload,'$.description')) LIKE '%chronic obstructive%'
  OR lower(json_extract(payload,'$.description')) LIKE '%emphysema%');
