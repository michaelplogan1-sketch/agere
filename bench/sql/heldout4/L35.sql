-- % of HF patients with a dispensed carvedilol/bisoprolol/metoprolol succinate in trailing 12 months.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','pharmacy_dispenses')), c AS (SELECT patient, json_extract(payload,'$.description') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
hf AS (SELECT DISTINCT patient FROM c WHERE (s='88805009' OR i LIKE 'I50%' OR lower(d) LIKE '%heart failure%' OR d LIKE '%CHF%')),
bb AS (SELECT DISTINCT patient FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed'
       AND json_extract(payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15'
       AND (lower(json_extract(payload,'$.medication_name')) LIKE '%carvedilol%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%bisoprolol%'
            OR lower(json_extract(payload,'$.medication_name')) LIKE '%metoprolol succinate%'))
SELECT round(100.0*SUM(patient IN (SELECT patient FROM bb))/COUNT(*),1) AS value FROM hf;
