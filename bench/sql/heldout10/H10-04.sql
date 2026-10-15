WITH codes AS (
    SELECT patient, upper(replace(json_extract(payload,'$.icd10'),'.','')) AS c FROM records WHERE stream='conditions'
    UNION SELECT r.patient, upper(replace(json_extract(d.value,'$.icd10'),'.','')) FROM records r, json_each(r.payload,'$.diagnoses') d WHERE r.stream='encounters'
    UNION SELECT r.patient, upper(replace(d.value,'.','')) FROM records r, json_each(r.payload,'$.diagnosis_codes') d WHERE r.stream IN ('payer_medical_claims','medical_claims')),
lab AS (SELECT patient, CASE
    WHEN substr(c,1,3) IN ('E10','E11') THEN 'diabetes'
    WHEN substr(c,1,3) IN ('I10','I11','I12','I13','I15','I16') THEN 'hypertension'
    WHEN substr(c,1,3)='I50' THEN 'heart_failure'
    WHEN substr(c,1,3)='J44' THEN 'copd'
    WHEN substr(c,1,3)='N18' THEN 'ckd' END AS condition FROM codes)
SELECT condition, COUNT(DISTINCT patient) AS patients FROM lab WHERE condition IS NOT NULL GROUP BY condition ORDER BY condition
