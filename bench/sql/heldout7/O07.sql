-- Patients with a recorded allergy to lisinopril, aspirin or ibuprofen (allergies.display) who have a 'dispensed' pharmacy record of that same drug (lisinopril incl. HCTZ combos; aspirin/ASA; ibuprofen) at any date.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('allergies','pharmacy_dispenses')),
al AS (SELECT DISTINCT patient, CASE WHEN lower(json_extract(payload,'$.display'))='lisinopril' THEN 'lisinopril' WHEN lower(json_extract(payload,'$.display'))='aspirin' THEN 'aspirin' WHEN lower(json_extract(payload,'$.display'))='ibuprofen' THEN 'ibuprofen' END drug
       FROM r WHERE stream='allergies' AND lower(json_extract(payload,'$.display')) IN ('lisinopril','aspirin','ibuprofen')),
dx AS (SELECT DISTINCT patient, CASE WHEN m LIKE '%lisinopril%' THEN 'lisinopril' WHEN m LIKE '%aspirin%' OR m='asa' THEN 'aspirin' WHEN m LIKE '%ibuprofen%' THEN 'ibuprofen' END drug
       FROM (SELECT patient, lower(json_extract(payload,'$.medication_name')) m FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed'))
SELECT DISTINCT al.patient AS patient_id FROM al JOIN dx ON dx.patient=al.patient AND dx.drug=al.drug ORDER BY 1;
