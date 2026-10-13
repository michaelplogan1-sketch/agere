-- Adults (roster age>=18) with BMI>=30 from baseline height and most recent dated weight (fallback: baseline weight measurement).
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='vitals'),
ht AS (SELECT patient, max(json_extract(payload,'$.value')) h FROM r WHERE json_extract(payload,'$.measurement')='height_cm' GROUP BY 1),
bw AS (SELECT patient, max(json_extract(payload,'$.value')) w FROM r WHERE json_extract(payload,'$.measurement')='weight_kg' GROUP BY 1),
dw AS (SELECT patient, json_extract(payload,'$.event_date')||coalesce(json_extract(payload,'$.event_time'),'') k, json_extract(payload,'$.weight_kg') w FROM r
       WHERE json_extract(payload,'$.weight_kg') IS NOT NULL AND json_extract(payload,'$.event_date') IS NOT NULL),
lw AS (SELECT dw.patient, max(dw.w) w FROM dw JOIN (SELECT patient, max(k) mk FROM dw GROUP BY 1) m ON m.patient=dw.patient AND m.mk=dw.k GROUP BY 1),
b AS (SELECT p.id, coalesce(lw.w, bw.w) / ((ht.h/100.0)*(ht.h/100.0)) bmi FROM patients p JOIN ht ON ht.patient=p.id LEFT JOIN lw ON lw.patient=p.id LEFT JOIN bw ON bw.patient=p.id
      WHERE json_extract(p.payload,'$.age_reported')>=18)
SELECT COUNT(*) AS count FROM b WHERE bmi >= 30;
