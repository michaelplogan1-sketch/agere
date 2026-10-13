-- Roster headcount by LATEST reported age band (latest demographic_updates age_reported by event_date, else roster age_reported): 0-17, 18-44, 45-64, 65+. All 1,614 roster patients.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='demographic_updates'),
du AS (SELECT patient, json_extract(payload,'$.age_reported') a, json_extract(payload,'$.event_date') ed FROM r),
age AS (SELECT p.id patient, coalesce((SELECT a FROM du WHERE du.patient=p.id ORDER BY ed DESC LIMIT 1), json_extract(p.payload,'$.age_reported')) a FROM patients p)
SELECT CASE WHEN a<18 THEN '0-17' WHEN a<45 THEN '18-44' WHEN a<65 THEN '45-64' ELSE '65+' END AS age_band, COUNT(*) AS patients FROM age GROUP BY 1 ORDER BY 1;
