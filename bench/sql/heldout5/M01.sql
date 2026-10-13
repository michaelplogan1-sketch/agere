-- Distinct roster patients with heart failure on the condition list: SNOMED 88805009, any ICD-10 I50.*, or a description containing 'heart failure' or 'CHF' (systolic/diastolic/end-stage/unspecified all count). Condition rows are undated.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='conditions'),
c AS (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r)
SELECT COUNT(DISTINCT patient) AS count FROM c WHERE s='88805009' OR i LIKE 'I50%' OR d LIKE '%heart failure%' OR d LIKE '%chf%';
