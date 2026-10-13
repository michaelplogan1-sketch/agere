-- Men (sex_reported M) aged 40-75 (roster age_reported) with type 1/2 diabetes (N01-style definition) and NO statin (atorva/simva/rosuva/prava/lova/pitava/fluvastatin) pharmacy record with status 'dispensed' dated 2028-01-01..2029-08-15.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','pharmacy_dispenses')),
dm AS (SELECT DISTINCT patient FROM (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i FROM r WHERE stream='conditions')
       WHERE (d LIKE '%diabet%' OR d LIKE 't1dm%' OR d LIKE 't2dm%' OR i LIKE 'E10%' OR i LIKE 'E11%') AND d NOT LIKE '%prediabet%' AND d NOT LIKE '%family history%' AND d NOT LIKE 'gdm%' AND i NOT LIKE 'O24%' AND i NOT LIKE 'Z83%'),
st AS (SELECT DISTINCT patient FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed' AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2029-08-15' AND lower(json_extract(payload,'$.medication_name')) LIKE '%statin%')
SELECT COUNT(*) AS count FROM patients p JOIN dm ON dm.patient=p.id
WHERE json_extract(p.payload,'$.sex_reported')='M' AND json_extract(p.payload,'$.age_reported') BETWEEN 40 AND 75 AND p.id NOT IN (SELECT patient FROM st);
