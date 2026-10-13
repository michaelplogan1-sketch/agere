-- Anemia on the condition list: SNOMED 271737000, any ICD-10 D50-D64, or a description mentioning anemia/anaemia.
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='conditions' AND (
  json_extract(payload,'$.snomed')='271737000'
  OR substr(json_extract(payload,'$.icd10'),1,3) BETWEEN 'D50' AND 'D64'
  OR lower(json_extract(payload,'$.description')) LIKE '%anemi%'
  OR lower(json_extract(payload,'$.description')) LIKE '%anaemi%');
