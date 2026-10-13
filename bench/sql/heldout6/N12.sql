-- Commercial, roster age 40-64, any dated non-cancelled numeric potassium >= 5.0 between 2028-08-16 and 2029-08-15.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream IN ('labs','coverage')),
k AS (SELECT DISTINCT patient FROM r WHERE json_extract(payload,'$.source_table')='labs' AND json_extract(payload,'$.name')='Potassium'
      AND coalesce(json_extract(payload,'$.result_status'),'')<>'cancelled' AND typeof(json_extract(payload,'$.value')) IN ('integer','real')
      AND json_extract(payload,'$.value') >= 5.0 AND json_extract(payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15'),
c AS (SELECT patient FROM r WHERE json_extract(payload,'$.source_table')='coverage' AND json_extract(payload,'$.coverage_type')='commercial')
SELECT p.id AS patient_id FROM patients p WHERE p.id IN (SELECT patient FROM k) AND p.id IN (SELECT patient FROM c)
  AND json_extract(p.payload,'$.age_reported') BETWEEN 40 AND 64 ORDER BY 1;
