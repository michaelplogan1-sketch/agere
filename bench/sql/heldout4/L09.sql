-- CAD on problem list AND NOT heart failure.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions')), c AS (SELECT patient, json_extract(payload,'$.description') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
cad AS (SELECT DISTINCT patient FROM c WHERE (s IN ('414545008','399261000','399211009','401314000','401303003','4557003') OR substr(i,1,3) IN ('I20','I21','I22','I23','I24','I25') OR lower(d) LIKE '%coronary%' OR lower(d) LIKE '%myocardial infarction%' OR lower(d) LIKE '%ischemic heart disease%' OR d LIKE '%STEMI%' OR d LIKE '%Non-ST elevation MI%')),
hf AS (SELECT DISTINCT patient FROM c WHERE (s='88805009' OR i LIKE 'I50%' OR lower(d) LIKE '%heart failure%' OR d LIKE '%CHF%'))
SELECT COUNT(*) AS count FROM cad WHERE patient NOT IN (SELECT patient FROM hf);
