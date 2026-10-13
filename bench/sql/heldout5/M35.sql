-- Pediatric vision screening: of roster patients under 18 by latest reported age (latest demographic_updates age, else roster age), percent with a preventive_screening record of screening_type 'distance_visual_acuity' and status 'completed' dated in 2028 ('unable_to_complete' does not count; hearing interviews do not count). Percent, 1 decimal.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('preventive_screening','demographic_updates')),
du AS (SELECT patient, json_extract(payload,'$.age_reported') a, json_extract(payload,'$.event_date') ed FROM r WHERE stream='demographic_updates'),
age AS (SELECT p.id patient, coalesce((SELECT a FROM du WHERE du.patient=p.id ORDER BY ed DESC LIMIT 1), json_extract(p.payload,'$.age_reported')) a FROM patients p),
kids AS (SELECT patient FROM age WHERE a<18),
vs AS (SELECT DISTINCT patient FROM r WHERE stream='preventive_screening' AND json_extract(payload,'$.screening_type')='distance_visual_acuity' AND json_extract(payload,'$.status')='completed' AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31')
SELECT ROUND(100.0*(SELECT COUNT(*) FROM kids WHERE patient IN (SELECT patient FROM vs))/(SELECT COUNT(*) FROM kids),1) AS value;
