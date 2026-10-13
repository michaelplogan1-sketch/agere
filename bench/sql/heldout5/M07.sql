-- Patients whose coverage record says Medicaid AND who have obesity on the condition list: SNOMED 162864005 (BMI 30+) or 408512008 (BMI 40+), ICD-10 E66.* except E66.3 (overweight), or a description containing 'obes' that is not a history-of entry (post-bariatric 'history of' excluded).
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','coverage')),
c AS (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
ob AS (SELECT DISTINCT patient FROM c WHERE s IN ('162864005','408512008') OR (i LIKE 'E66%' AND i<>'E66.3') OR (d LIKE '%obes%' AND d NOT LIKE '%history of%')),
mcd AS (SELECT DISTINCT patient FROM r WHERE stream='coverage' AND json_extract(payload,'$.coverage_type')='medicaid')
SELECT COUNT(*) AS count FROM ob WHERE patient IN (SELECT patient FROM mcd);
