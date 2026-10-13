-- Obesity AND prediabetes AND NOT diabetes, all from the condition list.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions')), c AS (SELECT patient, json_extract(payload,'$.description') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
ob AS (SELECT DISTINCT patient FROM c WHERE s IN ('162864005','408512008') OR i LIKE 'E66%' OR (lower(d) LIKE '%obes%' AND lower(d) NOT LIKE '%history of%')),
pre AS (SELECT DISTINCT patient FROM c WHERE s='714628002' OR i='R73.03' OR lower(d) LIKE '%prediabetes%'),
dm AS (SELECT DISTINCT patient FROM c WHERE (substr(i,1,3) IN ('E10','E11','E13') OR s='44054006' OR (lower(d) LIKE '%diabetes mellitus%' AND lower(d) NOT LIKE 'family history%') OR d LIKE '%T1DM%' OR d LIKE '%T2DM%'))
SELECT COUNT(*) AS count FROM ob WHERE patient IN (SELECT patient FROM pre) AND patient NOT IN (SELECT patient FROM dm);
