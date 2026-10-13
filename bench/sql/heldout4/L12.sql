-- Distinct patients with depression (depress/MDD/F32/F33/F53.0) or anxiety (anxiety/GAD/panic/F41).
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions')), c AS (SELECT patient, json_extract(payload,'$.description') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions')
SELECT COUNT(DISTINCT patient) AS count FROM c
WHERE lower(d) LIKE '%depress%' OR d LIKE '%MDD%' OR substr(i,1,3) IN ('F32','F33') OR i='F53.0'
   OR lower(d) LIKE '%anxiety%' OR d LIKE '%GAD%' OR lower(d) LIKE '%panic disorder%' OR i LIKE 'F41%';
