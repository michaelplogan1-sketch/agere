-- Controlling high blood pressure: denominator = patients aged 18-85 (roster age_reported) with a hypertension condition (description contains 'hypertension' excluding 'pulmonary hypertension', or starts 'HTN'/'Essential HTN', or ICD-10 I10); numerator = most recent dated vitals BP reading (blood_pressure_systolic/diastolic fields) in 2029-01-01..2029-08-15 is <140 systolic AND <90 diastolic, using the lowest systolic and lowest diastolic on that date. No 2029 reading = not controlled. Undated bp_systolic/bp_diastolic measurement rows ignored. Percent.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','vitals')),
htn AS (SELECT DISTINCT patient FROM (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i FROM r WHERE stream='conditions')
        WHERE ((d LIKE '%hypertension%' AND d NOT LIKE '%pulmonary hypertension%') OR d LIKE 'htn%' OR d LIKE 'essential htn%' OR i='I10')),
bp AS (SELECT patient, json_extract(payload,'$.event_date') d, CAST(json_extract(payload,'$.blood_pressure_systolic') AS REAL) s, CAST(json_extract(payload,'$.blood_pressure_diastolic') AS REAL) di
       FROM r WHERE stream='vitals' AND json_extract(payload,'$.blood_pressure_systolic') IS NOT NULL AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-08-15'),
l AS (SELECT patient, max(d) d FROM bp GROUP BY 1),
lv AS (SELECT bp.patient, min(s) s, min(di) di FROM bp JOIN l USING(patient,d) GROUP BY 1),
den AS (SELECT htn.patient FROM htn JOIN patients p ON p.id=htn.patient WHERE json_extract(p.payload,'$.age_reported') BETWEEN 18 AND 85)
SELECT round(100.0*sum(coalesce(lv.s<140 AND lv.di<90,0))/count(*),1) AS value FROM den LEFT JOIN lv ON lv.patient=den.patient;
