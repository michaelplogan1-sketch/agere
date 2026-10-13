-- Adults 18-64 (roster age_reported) whose coverage record is medicaid or uninsured, with a type 1/2 diabetes condition (incl. complications; excludes prediabetes, GDM/O24, family history Z83).
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','coverage')),
dm AS (SELECT DISTINCT patient FROM (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i FROM r WHERE stream='conditions')
       WHERE (d LIKE '%diabet%' OR d LIKE 't1dm%' OR d LIKE 't2dm%' OR i LIKE 'E10%' OR i LIKE 'E11%') AND d NOT LIKE '%prediabet%' AND d NOT LIKE '%family history%' AND d NOT LIKE 'gdm%' AND i NOT LIKE 'O24%' AND i NOT LIKE 'Z83%'),
cv AS (SELECT patient FROM r WHERE stream='coverage' AND json_extract(payload,'$.coverage_type') IN ('medicaid','uninsured'))
SELECT COUNT(*) AS count FROM patients p JOIN dm ON dm.patient=p.id JOIN cv ON cv.patient=p.id WHERE json_extract(p.payload,'$.age_reported') BETWEEN 18 AND 64;
