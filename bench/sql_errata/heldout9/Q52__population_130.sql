-- Hypertension (population-wide) = problem list (conditions icd10 I10-I13/I15/I16 or description containing 'hypertension'/'HTN',
-- pulmonary hypertension I27.x excluded) OR an I10-I13/I15/I16 code in the diagnosis_codes of any payer_medical_claims row.
-- BP = vitals rows with both blood_pressure_systolic and blood_pressure_diastolic and an event_date in 2029; latest by event_date,
-- event_time, record_id; hypertensives without a dated 2029 reading excluded. Distinct patients.
-- Reading B: uncontrolled = latest 2029 reading systolic >= 130 OR diastolic >= 80.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','vitals','payer_medical_claims')),
htn AS MATERIALIZED (SELECT patient FROM r WHERE stream='conditions' AND (substr(json_extract(payload,'$.icd10'),1,3) IN ('I10','I11','I12','I13','I15','I16')
   OR ((lower(json_extract(payload,'$.description')) LIKE '%hypertension%' OR json_extract(payload,'$.description') LIKE '%HTN%') AND coalesce(json_extract(payload,'$.icd10'),'') NOT LIKE 'I27%'))
  UNION SELECT r.patient FROM r, json_each(r.payload,'$.diagnosis_codes') j WHERE r.stream='payer_medical_claims' AND substr(j.value,1,3) IN ('I10','I11','I12','I13','I15','I16')),
b AS (SELECT patient, json_extract(payload,'$.blood_pressure_systolic') s, json_extract(payload,'$.blood_pressure_diastolic') d,
  row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date') DESC, json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn
  FROM r WHERE stream='vitals' AND json_extract(payload,'$.blood_pressure_systolic') IS NOT NULL AND json_extract(payload,'$.blood_pressure_diastolic') IS NOT NULL AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31')
SELECT COUNT(*) AS count FROM b WHERE rn=1 AND (s>=130 OR d>=80) AND patient IN (SELECT patient FROM htn);
