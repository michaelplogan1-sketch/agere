-- Women (sex_reported F) aged 65+ by LATEST reported age (most recent demographic_updates age_reported, else roster age_reported) who have BOTH diabetes (E10/E11/E13, SNOMED 44054006, 'diabetes mellitus' excl. family history, T1DM/T2DM; not prediabetes/GDM/hyperglycemia) AND hypertension (SNOMED 59621000, I10-I13/I15/I16, 'hypertension' excl. pulmonary, 'HTN').
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','demographic_updates')),
c AS (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM c WHERE substr(i,1,3) IN ('E10','E11','E13') OR s='44054006' OR (d LIKE '%diabetes mellitus%' AND d NOT LIKE '%family history%') OR d LIKE '%t1dm%' OR d LIKE '%t2dm%'),
htn AS (SELECT DISTINCT patient FROM c WHERE s='59621000' OR substr(i,1,3) IN ('I10','I11','I12','I13','I15','I16') OR (d LIKE '%hypertension%' AND d NOT LIKE '%pulmonary hypertension%') OR d LIKE '%htn%'),
du AS (SELECT patient, json_extract(payload,'$.age_reported') a, json_extract(payload,'$.event_date') ed FROM r WHERE stream='demographic_updates'),
age AS (SELECT p.id patient, json_extract(p.payload,'$.sex_reported') sex,
          coalesce((SELECT a FROM du WHERE du.patient=p.id ORDER BY ed DESC LIMIT 1), json_extract(p.payload,'$.age_reported')) a FROM patients p)
SELECT COUNT(*) AS count FROM age WHERE sex='F' AND a>=65 AND patient IN (SELECT patient FROM dm) AND patient IN (SELECT patient FROM htn);
