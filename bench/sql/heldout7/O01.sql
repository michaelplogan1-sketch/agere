-- Distinct heart failure patients (condition description contains 'heart failure' or ICD-10 I50.*) with >=1 furosemide pharmacy record of status 'dispensed' dated 2029-02-15..2029-08-15. 'prepared' and 'returned_to_stock' rows are not pickups.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','pharmacy_dispenses')),
hf AS (SELECT DISTINCT patient FROM r WHERE stream='conditions' AND (lower(json_extract(payload,'$.description')) LIKE '%heart failure%' OR coalesce(json_extract(payload,'$.icd10'),'') LIKE 'I50%')),
fx AS (SELECT DISTINCT patient FROM r WHERE stream='pharmacy_dispenses' AND lower(json_extract(payload,'$.medication_name')) LIKE '%furosemide%'
       AND json_extract(payload,'$.status')='dispensed' AND json_extract(payload,'$.event_date') BETWEEN '2029-02-15' AND '2029-08-15')
SELECT COUNT(*) AS count FROM hf JOIN fx USING(patient);
