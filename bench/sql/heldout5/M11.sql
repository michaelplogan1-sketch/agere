-- Diabetics (E10/E11/E13, SNOMED 44054006, 'diabetes mellitus' excl. family history, T1DM/T2DM; not prediabetes/GDM/hyperglycemia) with NO urine albumin-creatinine ratio result (names 'Urine albumin/creatinine ratio', 'Microalbumin/Creatinine', 'UACR') whose event_date falls in the trailing 12 months 2028-08-16..2029-08-15. Undated UACR rows cannot satisfy a dated window.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs')),
c AS (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM c WHERE substr(i,1,3) IN ('E10','E11','E13') OR s='44054006' OR (d LIKE '%diabetes mellitus%' AND d NOT LIKE '%family history%') OR d LIKE '%t1dm%' OR d LIKE '%t2dm%'),
ua AS (SELECT DISTINCT patient FROM r WHERE stream='labs' AND json_extract(payload,'$.name') IN ('Urine albumin/creatinine ratio','Microalbumin/Creatinine','UACR')
       AND json_extract(payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15')
SELECT patient AS patient_id FROM dm WHERE patient NOT IN (SELECT patient FROM ua) ORDER BY 1;
