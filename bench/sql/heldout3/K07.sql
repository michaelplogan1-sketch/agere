-- Patients aged 65 or older (roster age_reported) with BOTH heart failure (K01 definition) AND anemia
-- (SNOMED 271737000, ICD-10 D50-D64, or description containing 'anemia'/'anaemia') on the condition list.
WITH c AS (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s
           FROM records WHERE stream='conditions'),
hf AS (SELECT DISTINCT patient FROM c WHERE s='88805009' OR i LIKE 'I50%' OR d LIKE '%heart failure%' OR d LIKE '%chf%'),
an AS (SELECT DISTINCT patient FROM c WHERE s='271737000' OR substr(i,1,3) BETWEEN 'D50' AND 'D64' OR d LIKE '%anemi%' OR d LIKE '%anaemi%')
SELECT COUNT(*) AS count FROM patients p
WHERE json_extract(p.payload,'$.age_reported') >= 65 AND p.id IN (SELECT patient FROM hf) AND p.id IN (SELECT patient FROM an);
