-- Latest numeric potassium (dated, non-cancelled; ties on latest date -> highest value) > 5.0 mmol/L AND a 'dispensed' ACE inhibitor/ARB/ARNI/MRA fill dated 2029-02-16..2029-08-15 (180 days).
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('labs','pharmacy_dispenses')),
k AS (SELECT patient, json_extract(payload,'$.event_date') d, CAST(json_extract(payload,'$.value') AS REAL) v FROM r
      WHERE stream='labs' AND coalesce(json_extract(payload,'$.name'),json_extract(payload,'$.test'))='Potassium'
        AND json_extract(payload,'$.event_date') IS NOT NULL AND coalesce(json_extract(payload,'$.result_status'),'final')<>'cancelled'
        AND typeof(json_extract(payload,'$.value')) IN ('integer','real')),
l AS (SELECT patient, max(d) d FROM k GROUP BY 1),
lv AS (SELECT k.patient, max(v) v FROM k JOIN l USING(patient,d) GROUP BY 1),
rx AS (SELECT DISTINCT patient FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed'
         AND json_extract(payload,'$.event_date') BETWEEN '2029-02-16' AND '2029-08-15'
         AND (lower(json_extract(payload,'$.medication_name')) GLOB '*lisinopril*' OR lower(json_extract(payload,'$.medication_name')) GLOB '*enalapril*'
           OR lower(json_extract(payload,'$.medication_name')) GLOB '*benazepril*' OR lower(json_extract(payload,'$.medication_name')) GLOB '*quinapril*'
           OR lower(json_extract(payload,'$.medication_name')) GLOB '*losartan*' OR lower(json_extract(payload,'$.medication_name')) GLOB '*valsartan*'
           OR lower(json_extract(payload,'$.medication_name')) GLOB '*irbesartan*' OR lower(json_extract(payload,'$.medication_name')) GLOB '*spironolactone*'))
SELECT lv.patient AS patient_id FROM lv JOIN rx USING(patient) WHERE lv.v > 5.0 ORDER BY 1;
