-- CBP-style: HTN dx, roster age 18-85; latest dated BP in 2028-08-16..2029-08-15 (same-day lowest sys & dia) <140/<90; no reading = not controlled.
WITH c AS MATERIALIZED (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i FROM records WHERE stream='conditions'),
htn AS (SELECT DISTINCT c.patient FROM c JOIN patients p ON p.id=c.patient WHERE (c.d LIKE '%hypertension%' OR c.d LIKE '%htn%' OR c.i='I10')
        AND c.d NOT LIKE '%pulmonary hypertension%' AND json_extract(p.payload,'$.age_reported') BETWEEN 18 AND 85),
v AS MATERIALIZED (SELECT patient, json_extract(payload,'$.event_date') d, json_extract(payload,'$.blood_pressure_systolic') s, json_extract(payload,'$.blood_pressure_diastolic') di
      FROM records WHERE stream='vitals' AND json_extract(payload,'$.blood_pressure_systolic') IS NOT NULL AND json_extract(payload,'$.blood_pressure_diastolic') IS NOT NULL
        AND json_extract(payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15'),
l AS (SELECT v.patient, min(v.s) s, min(v.di) di FROM v JOIN (SELECT patient, max(d) md FROM v GROUP BY 1) m ON m.patient=v.patient AND m.md=v.d GROUP BY 1)
SELECT round(100.0*(SELECT count(*) FROM htn JOIN l USING(patient) WHERE l.s<140 AND l.di<90)/(SELECT count(*) FROM htn),1) AS value;
