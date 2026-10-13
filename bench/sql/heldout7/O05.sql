-- Patients with CKD stage 3 or 4 (description 'Chronic kidney disease stage 3/4', 'CKD stage 3/3b/4', or ICD-10 N18.3*/N18.4) AND >=1 'dispensed' NSAID (ibuprofen or naproxen) at any date.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','pharmacy_dispenses')),
ckd AS (SELECT DISTINCT patient FROM r WHERE stream='conditions' AND (lower(json_extract(payload,'$.description')) LIKE 'chronic kidney disease stage 3%' OR lower(json_extract(payload,'$.description')) LIKE 'chronic kidney disease stage 4%'
        OR lower(json_extract(payload,'$.description')) LIKE 'chronic kidney disease, stage 3%' OR lower(json_extract(payload,'$.description')) LIKE 'ckd stage 3%' OR lower(json_extract(payload,'$.description')) LIKE 'ckd stage 4%'
        OR coalesce(json_extract(payload,'$.icd10'),'') LIKE 'N18.3%' OR coalesce(json_extract(payload,'$.icd10'),'')='N18.4')),
ns AS (SELECT DISTINCT patient FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed' AND (lower(json_extract(payload,'$.medication_name')) LIKE '%ibuprofen%' OR lower(json_extract(payload,'$.medication_name')) LIKE '%naproxen%'))
SELECT patient AS patient_id FROM ckd JOIN ns USING(patient) ORDER BY 1;
