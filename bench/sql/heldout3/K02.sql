-- Diabetes AND staged chronic kidney disease, both on the condition list.
-- Diabetes: ICD-10 E10/E11/E13, SNOMED 44054006, description containing 'diabetes mellitus' (excluding 'Family history of diabetes mellitus'), or 'T1DM'/'T2DM'. Prediabetes, gestational diabetes and hypoglycemia codes are not diabetes.
-- CKD: ICD-10 N18.*, description containing 'chronic kidney disease' or 'CKD', or end-stage renal disease (SNOMED 46177005 / description). 'Disorder of kidney due to diabetes mellitus' alone does not count as CKD.
WITH c AS (SELECT patient, json_extract(payload,'$.description') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s
           FROM records WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM c WHERE substr(i,1,3) IN ('E10','E11','E13') OR s='44054006'
        OR (lower(d) LIKE '%diabetes mellitus%' AND lower(d) NOT LIKE 'family history%')
        OR d LIKE '%T1DM%' OR d LIKE '%T2DM%'),
ckd AS (SELECT DISTINCT patient FROM c WHERE i LIKE 'N18%' OR lower(d) LIKE '%chronic kidney disease%' OR d LIKE '%CKD%'
        OR s='46177005' OR lower(d) LIKE '%end-stage renal disease%')
SELECT patient AS patient_id FROM dm WHERE patient IN (SELECT patient FROM ckd) ORDER BY patient;
