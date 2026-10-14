-- Diabetes (population-wide) = problem list (conditions icd10 E10/E11/E13) OR an E10/E11/E13 code in the diagnosis_codes of any
-- payer_medical_claims row (the only diagnosis source for claims-only members). Prediabetes R73.x / family history Z83.3 never qualify.
-- uACR lab = lower(name) in 'urine albumin/creatinine ratio', 'microalbumin/creatinine', 'microalb/creat ratio, random urine',
-- 'microalbumin/creatinine [mass ratio] in urine' or LOINC 9318-7; dated numeric 2029 results (urine albumin alone does not count;
-- claims carry 82043 urine albumin but no 82570 creatinine, so claims cannot evidence a ratio).
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs','payer_medical_claims')),
dm AS MATERIALIZED (SELECT patient FROM r WHERE stream='conditions' AND substr(json_extract(payload,'$.icd10'),1,3) IN ('E10','E11','E13')
  UNION SELECT r.patient FROM r, json_each(r.payload,'$.diagnosis_codes') j WHERE r.stream='payer_medical_claims' AND substr(j.value,1,3) IN ('E10','E11','E13'))
SELECT COUNT(DISTINCT patient) AS count FROM r WHERE stream='labs' AND (lower(json_extract(payload,'$.name')) IN ('urine albumin/creatinine ratio','microalbumin/creatinine','microalb/creat ratio, random urine','microalbumin/creatinine [mass ratio] in urine') OR json_extract(payload,'$.loinc')='9318-7') AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_type(payload,'$.value') IN ('integer','real') AND patient IN (SELECT patient FROM dm);
