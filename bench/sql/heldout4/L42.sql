-- Diabetics with any social_screens food_difficulty_reported = 1.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','social_screens')), c AS (SELECT patient, json_extract(payload,'$.description') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM c WHERE (substr(i,1,3) IN ('E10','E11','E13') OR s='44054006' OR (lower(d) LIKE '%diabetes mellitus%' AND lower(d) NOT LIKE 'family history%') OR d LIKE '%T1DM%' OR d LIKE '%T2DM%'))
SELECT COUNT(DISTINCT patient) AS count FROM r WHERE stream='social_screens' AND json_extract(payload,'$.food_difficulty_reported')=1
  AND patient IN (SELECT patient FROM dm);
