-- Systemic hypertension on the condition list (ICD-10 I10-I16, SNOMED 59621000, or description containing 'hypertension' or 'HTN', excluding pulmonary hypertension)
-- with NO LDL cholesterol result received under any LDL name variant ('LDL cholesterol', 'LDL'), dated or undated.
WITH c AS (SELECT patient, json_extract(payload,'$.description') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s
           FROM records WHERE stream='conditions'),
htn AS (SELECT DISTINCT patient FROM c WHERE (substr(i,1,3) BETWEEN 'I10' AND 'I16' OR s='59621000'
          OR lower(d) LIKE '%hypertension%' OR d LIKE '%HTN%') AND lower(d) NOT LIKE '%pulmonary hypertension%')
SELECT COUNT(*) AS count FROM htn WHERE patient NOT IN (
  SELECT patient FROM records WHERE stream='labs' AND json_extract(payload,'$.name') IN ('LDL cholesterol','LDL'));
