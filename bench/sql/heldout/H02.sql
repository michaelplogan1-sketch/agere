WITH ckd AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3)='N18' OR
 json_extract(payload,'$.snomed') IN ('431855005','431856006','433144002','431857002'))
), k AS MATERIALIZED (
 SELECT patient,json_extract(payload,'$.value') AS v FROM records WHERE stream='labs'
  AND json_extract(payload,'$.name')='Potassium' AND json_extract(payload,'$.unit') IN ('mmol/L','mEq/L')
  AND json_type(payload,'$.value') IN ('integer','real')
) SELECT DISTINCT ckd.patient AS patient_id FROM ckd JOIN k USING(patient) WHERE k.v>5.0 ORDER BY patient_id;
